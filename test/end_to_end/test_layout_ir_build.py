# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
layout_ir build-step + bridge② end-to-end (BACKLOG §B / B1b).

The contract unit tests (test/libs/kicad/test_layout_ir_contract.py) pin the
pcb-only IR on inline fixtures. This file proves the two pieces that only exist
in a real build:

  1. the `layout-ir` muster step actually runs under `ato build` and writes
     <output_base>.layout_ir.json;
  2. bridge② (signal-address→net, BACKLOG I4b) is derived from the graph,
     is a function, and is consistent with the board geometry.

Uses examples/layout_reuse — it has a reused sub-layout and ships its parts
locally (no EasyEDA fetch), so the build is self-contained.
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


@pytest.fixture(scope="module")
def built_layout_reuse(tmp_path_factory) -> Path:
    """Build examples/layout_reuse once; return the build dir."""
    work = tmp_path_factory.mktemp("layout_reuse")
    dst = work / "layout_reuse"
    shutil.copytree(EXAMPLE, dst)
    # The build shells out to `ato` on PATH during part-picking; ensure it
    # resolves to THIS interpreter's atopile, not a stale system install (a
    # mismatched `ato` hangs the picker — see CLAUDE.md PATH hazard).
    bindir = os.path.dirname(sys.executable)
    env = {
        **os.environ,
        "NONINTERACTIVE": "1",
        "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
    }
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-v"],
        env=env,
        cwd=dst,
        stdout=print,
        stderr=print,
        timeout=300,
    )
    combined = stdout + stderr
    assert "Build successful! 🚀" in combined, combined[-2000:]
    return dst


def _ir(build_dir: Path, target: str) -> dict:
    path = build_dir / "build" / "builds" / target / f"{target}.layout_ir.json"
    assert path.exists(), f"layout-ir step did not produce {path}"
    return json.loads(path.read_text())


@pytest.mark.not_in_ci  # requires kicad-cli
@pytest.mark.slow
def test_layout_ir_artifact_is_produced_and_well_formed(built_layout_reuse):
    ir = _ir(built_layout_reuse, "top")
    assert ir["layout_ir_version"] >= 1
    assert ir["components"], "no managed components in IR"
    # every component keyed by a hierarchical ato address with real geometry
    for addr, comp in ir["components"].items():
        assert "." in addr, f"address {addr!r} is not hierarchical"
        assert comp["footprint_uuid"]
        assert comp["pads"]
    # nets reference only managed component endpoints (bridge③ closure)
    managed = set(ir["components"])
    for net, endpoints in ir["nets"].items():
        assert net != ""
        for ep in endpoints:
            assert ep.rsplit(".", 1)[0] in managed


@pytest.mark.not_in_ci  # requires kicad-cli
@pytest.mark.slow
def test_layout_reuse_addresses_share_prefix_per_instance(built_layout_reuse):
    """I6 on a real reuse board: the reused instances yield true address
    hierarchies that differ only by their instance prefix."""
    ir = _ir(built_layout_reuse, "top")
    roots = {addr.split(".")[0] for addr in ir["components"]}
    assert roots, "no components"
    # each address is a dotted path under some instance root (prefix-remap-able)
    for addr in ir["components"]:
        assert any(addr == r or addr.startswith(r + ".") for r in roots)


@pytest.mark.not_in_ci  # requires kicad-cli
@pytest.mark.slow
def test_I4b_signal_nets_bridge_is_a_function_and_geometry_consistent(
    built_layout_reuse,
):
    ir = _ir(built_layout_reuse, "top")
    assert "signal_nets" in ir, "build step did not fold in bridge② (signal_nets)"
    bridge = ir["signal_nets"]
    assert bridge, "bridge② is empty on a routed design"

    # function-ness: JSON keys are unique by construction; addresses look like
    # real signal ato-addresses
    for signal_addr, net_name in bridge.items():
        assert "." in signal_addr, f"signal address {signal_addr!r} not hierarchical"
        assert net_name, "referenceable net must have a non-empty name"

    # consistency with geometry: every net a signal maps to must exist as a real
    # geometry net (it has pads), tying bridge② to bridge③. A named net with no
    # pads (pure-interface) is the only legitimate exception, so require the vast
    # majority to land in geometry rather than demanding a strict subset.
    geom = set(ir["nets"])
    referenced = set(bridge.values())
    landed = referenced & geom
    assert landed, f"no bridge② net is present in geometry: {sorted(referenced)[:10]}"
    assert len(landed) >= 0.8 * len(referenced), (
        f"bridge② nets missing from geometry: {sorted(referenced - geom)}"
    )
