# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F end-to-end — the diagnostics CLOSED LOOP over a real build.

The contract units pin the pieces (F1 route CLI, F2 JSON_DIAG, F3 reverse
resolve, F4 diagnostics builder). This file proves the only thing that exists
solely when wired through a real build: `ato build` → `ato route` → `ato
diagnose` produces a `diagnostics.json` whose findings are correlated to real ato
addresses/rooms on a real routed board.

Built against examples/layout_reuse (ships parts locally → offline build), routes
one real net through the §E1 runner under system python3, and diagnoses the
result through kicad-cli DRC. The route may even FAIL — that is a valid loop
outcome (a ROUTE-FAIL finding); what matters is the loop closes with structured,
attributed diagnostics.

Markers: @slow @not_in_ci + skipif(no kicad-cli / no system python3). The build is
~5 min; the S0 ratchet keeps it from running until the §F CLIs land.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

EXAMPLE = _repo_root() / "examples" / "layout_reuse"
IR_JSON = Path("build") / "builds" / "top" / "top.layout_ir.json"
ROUTE_REPORT = Path("build") / "builds" / "top" / "top.route_report.json"
DIAGNOSTICS = Path("build") / "builds" / "top" / "top.diagnostics.json"

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
_HAS_SYS_PY = shutil.which("python3") is not None

# the §F CLIs must be importable (the S0 landing gate for this e2e).
try:
    import atopile.cli.route  # noqa: F401
    import atopile.cli.diagnose  # noqa: F401

    _F_CLIS_LANDED = True
except Exception:  # noqa: BLE001
    _F_CLIS_LANDED = False

needs_f = pytest.mark.xfail(
    not _F_CLIS_LANDED, reason="§F route/diagnose CLIs not landed", strict=True
)


def _ato(cwd: Path, *args: str, timeout: int = 300) -> str:
    bindir = os.path.dirname(sys.executable)
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", *args],
        env={
            **os.environ,
            "NONINTERACTIVE": "1",
            "FBRK_PARTS_NO_REFRESH": "y",
            "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
        },
        cwd=cwd, stdout=print, stderr=print, timeout=timeout,
    )
    return stdout + stderr


def _routable_net_addrs(ir: dict) -> list[str]:
    """Up to two signal addresses on DISTINCT, routable (>= 2 endpoint) nets — one
    address per net (single-mode routes the whole net). Falls back to any one
    address. (Two addresses on the SAME net would hand the router a duplicate net,
    a degenerate stage.)"""
    signal_nets = ir.get("signal_nets", {})
    nets = ir.get("nets", {})
    seen: set[str] = set()
    out: list[str] = []
    for addr in sorted(signal_nets):
        name = signal_nets[addr]
        if name in seen:
            continue
        if len(nets.get(name, [])) >= 2:  # >= 2 endpoints → a real net to route
            seen.add(name)
            out.append(addr)
        if len(out) >= 2:
            break
    return out or sorted(signal_nets)[:1]


@pytest.fixture(scope="module")
def diagnosed(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("diagnose_loop")
    dst = work / "layout_reuse"
    shutil.copytree(EXAMPLE, dst)
    if not (_F_CLIS_LANDED and _HAS_KICAD_CLI and _HAS_SYS_PY):
        return dst

    # 1) discovery build → read the IR for real net addresses + a real room.
    out = _ato(dst, "build", "-v")
    assert "Build successful! 🚀" in out, out[-2000:]
    ir = json.loads((dst / IR_JSON).read_text())
    addrs = _routable_net_addrs(ir)
    assert len(addrs) >= 1, "no signal addresses discovered in IR"

    # 2) write a layout.yaml: a 2-copper stackup (TS-AUTH-B) + one single stage.
    nets_block = "\n".join(f"      - {a}" for a in addrs)
    (dst / "layout.yaml").write_text(
        "rules:\n"
        "  clearance: 0.1\n"
        "  track_width: 0.15\n"
        "board:\n"
        "  stackup:\n"
        "    layers:\n"
        "      - {name: F.Cu, type: copper}\n"
        "      - {name: core, type: dielectric, thickness: 1.5, material: FR4, epsilon_r: 4.5}\n"
        "      - {name: B.Cu, type: copper}\n"
        "route_stages:\n"
        "  - name: sigs\n"
        "    mode: single\n"
        "    nets:\n"
        f"{nets_block}\n"
    )
    ato = (dst / "ato.yaml").read_text()
    patched = ato.replace(
        "    entry: layout_reuse.ato:Top",
        "    entry: layout_reuse.ato:Top\n    layout_config: ./layout.yaml",
    )
    assert patched != ato
    (dst / "ato.yaml").write_text(patched)

    # 3) rebuild (stamps rule areas + emits artifacts), route, diagnose.
    out = _ato(dst, "build", "-v")
    assert "Build successful! 🚀" in out, out[-2000:]
    _ato(dst, "route", timeout=600)
    _ato(dst, "diagnose", timeout=300)
    return dst


@needs_f
@pytest.mark.not_in_ci
@pytest.mark.slow
@pytest.mark.skipif(
    not (_HAS_KICAD_CLI and _HAS_SYS_PY),
    reason="requires kicad-cli + system python3 (router)",
)
def test_route_report_is_produced(diagnosed):
    report = json.loads((diagnosed / ROUTE_REPORT).read_text())
    assert "stages" in report and report["stages"]
    assert report["stages"][0]["stage_name"] == "sigs"


@needs_f
@pytest.mark.not_in_ci
@pytest.mark.slow
@pytest.mark.skipif(
    not (_HAS_KICAD_CLI and _HAS_SYS_PY),
    reason="requires kicad-cli + system python3 (router)",
)
def test_diagnostics_json_is_well_formed_and_attributed(diagnosed):
    diag = json.loads((diagnosed / DIAGNOSTICS).read_text())
    assert {"findings", "summary", "totals"} <= set(diag)
    assert isinstance(diag["findings"], list)
    # every finding carries the §F schema fields; any route-failure names its net.
    for f in diag["findings"]:
        assert {"rule_id", "severity", "ato_path", "room"} <= set(f)
    rf = [f for f in diag["findings"] if f["rule_id"] == "ROUTE-FAIL"]
    for f in rf:
        assert f["nets"], "a route failure finding must name its net"
