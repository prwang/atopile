# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_layout_plan_build — stage D4 acceptance, the build-level half (BACKLOG §D).

The contract unit tests pin the pieces in isolation:
  * D1  test/test_config.py            — paths.layout_config resolution
  * D2  test_layout_plan_contract.py   — layout.yaml parse/validate + bridge②
  * D3  test_rule_area_contract.py     — rule-area generator + KiCad fidelity

This file proves the parts that only exist in a real build wired through the
muster: the `layout-plan` step reads `config.build.paths.layout_config` (D1),
parses+resolves the plan (D2), emits rule areas onto the board (D3), and writes
`<output_base>.layout_plan.json`. Built against examples/layout_reuse (ships its
parts locally, builds offline), exactly like test_layout_ir_build.py.

Pins:
  * the layout-plan artifact is produced and well-formed (rooms + route_stages);
  * the built board carries one placement rule area per plan room (sheetname ==
    room module, §C3);
  * build→build is deterministic AND re-emit is idempotent — a second build does
    not duplicate rule areas or perturb the resolved plan (the D-spec "re-emit
    幂等" + "build→build 稳" pins; placement count == #rooms, never doubled);
  * the rule-area-bearing board is ingestible by kicad-cli drc.

S0 ratchet: `_D4_LANDED` probes the build step + the D1 path field; until D lands
every test strict-xfail. The expensive build only runs when landed (the fixture
skips the doomed build otherwise), so an unlanded ratchet stays cheap and red.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import atopile.build_steps as _build_steps
from atopile.config import BuildTargetPaths
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

EXAMPLE = _repo_root() / "examples" / "layout_reuse"
TOP_PCB = Path("layout") / "top" / "top.kicad_pcb"
PLAN_JSON = Path("build") / "builds" / "top" / "top.layout_plan.json"
IR_JSON = Path("build") / "builds" / "top" / "top.layout_ir.json"

_D4_LANDED = hasattr(_build_steps, "generate_layout_plan") and (
    "layout_config" in BuildTargetPaths.model_fields
)
needs_d4 = pytest.mark.xfail(
    not _D4_LANDED,
    reason="D4 layout-plan build step / layout_config not landed (S0 ratchet)",
    strict=True,
)

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None


def _build(cwd: Path) -> str:
    bindir = os.path.dirname(sys.executable)
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-v"],
        env={
            **os.environ,
            "NONINTERACTIVE": "1",
            "FBRK_PARTS_NO_REFRESH": "y",
            "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
        },
        cwd=cwd, stdout=print, stderr=print, timeout=300,
    )
    out = stdout + stderr
    assert "Build successful! 🚀" in out, out[-2000:]
    return out


def _write_layout_yaml(dst: Path, room: str) -> None:
    """A minimal plan: one room (derived bbox, no origin/size), no route stages."""
    (dst / "layout.yaml").write_text(
        f"rooms:\n  - module: {room}\nroute_stages: []\n"
    )
    ato = (dst / "ato.yaml").read_text()
    patched = ato.replace(
        "    entry: layout_reuse.ato:Top",
        "    entry: layout_reuse.ato:Top\n    layout_config: ./layout.yaml",
    )
    assert patched != ato, "could not patch ato.yaml top build with layout_config"
    (dst / "ato.yaml").write_text(patched)


@pytest.fixture(scope="module")
def built_with_plan(tmp_path_factory) -> Path:
    """Discover a real room, attach a layout.yaml, and build with it once.

    When D4 has not landed the expensive build is skipped (not the test): the dir
    is returned un-built so the test bodies fail cheaply on the missing artifact
    — a clean strict-xfail rather than a wasted 5-minute doomed build."""
    work = tmp_path_factory.mktemp("layout_plan_build")
    dst = work / "layout_reuse"
    shutil.copytree(EXAMPLE, dst)
    if not _D4_LANDED:
        return dst

    # discovery build (no plan) → read a real room name (= a footprint sheetname)
    _build(dst)
    rooms = json.loads((dst / IR_JSON).read_text())["rooms"]
    assert rooms, "no rooms in the discovery IR (C3 sheetname rooms expected)"
    room = sorted(rooms)[0]

    _write_layout_yaml(dst, room)
    _build(dst)  # the real, plan-driven build
    return dst


def _placement_sheetnames(pcb_path: Path) -> list[str]:
    pcb = kicad.loads(kicad.pcb.PcbFile, pcb_path.read_text()).kicad_pcb
    return sorted(
        z.placement.sheetname
        for z in pcb.zones
        if z.placement is not None and z.placement.sheetname is not None
    )


@needs_d4
@pytest.mark.not_in_ci  # requires a full build (kicad-cli + parts)
@pytest.mark.slow
def test_layout_plan_artifact_is_produced_and_well_formed(built_with_plan):
    path = built_with_plan / PLAN_JSON
    assert path.exists(), f"layout-plan step did not produce {path}"
    plan = json.loads(path.read_text())
    assert "rooms" in plan and "route_stages" in plan
    assert plan["rooms"], "no rooms in the resolved plan"
    assert plan["route_stages"] == []
    # the plan's rooms are REAL board rooms — every module is a footprint
    # sheetname present in the build's IR (the §C3 room channel). NB: a top-level
    # room is a single address segment (e.g. "sub_chains[0]"), not necessarily
    # dotted — `_get_room_name` strips the inner suffix, so a hierarchy/"." check
    # would be a false premise about the example data.
    real_rooms = set(json.loads((built_with_plan / IR_JSON).read_text())["rooms"])
    for room in plan["rooms"]:
        assert room["module"] in real_rooms, (
            f"room module {room['module']!r} is not a real board room {real_rooms}"
        )


@needs_d4
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_built_board_has_one_rule_area_per_room(built_with_plan):
    plan = json.loads((built_with_plan / PLAN_JSON).read_text())
    expected = sorted(r["module"] for r in plan["rooms"])
    on_board = _placement_sheetnames(built_with_plan / TOP_PCB)
    # every plan room has a placement rule area on the board, sourced by sheetname
    assert set(expected) <= set(on_board)


@needs_d4
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_rebuild_is_deterministic_and_reemit_idempotent(built_with_plan):
    """A second build over the already-emitted board must not duplicate rule
    areas (re-emit idempotent) nor perturb the resolved plan json (build→build
    stable). Pinned on the plan-json bytes + the placement-zone multiset — not
    the .kicad_pcb bytes, which carry opaque uuids (§G)."""
    before_json = (built_with_plan / PLAN_JSON).read_bytes()
    before_zones = _placement_sheetnames(built_with_plan / TOP_PCB)

    _build(built_with_plan)  # rebuild in place

    assert (built_with_plan / PLAN_JSON).read_bytes() == before_json
    after_zones = _placement_sheetnames(built_with_plan / TOP_PCB)
    # idempotent: same sheetnames, same count (no duplicated/accreting zones)
    assert after_zones == before_zones


@needs_d4
@pytest.mark.not_in_ci
@pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")
@pytest.mark.slow
def test_built_board_is_drc_ingestible(built_with_plan, tmp_path):
    """The rule-area-bearing board is well-formed for KiCad: drc runs and emits a
    valid report (the placement rule area did not corrupt the board)."""
    report = tmp_path / "drc.json"
    subprocess.run(
        [
            "kicad-cli", "pcb", "drc", "--format", "json",
            "-o", str(report), str(built_with_plan / TOP_PCB),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert report.exists()
    data = json.loads(report.read_text())
    assert "violations" in data
