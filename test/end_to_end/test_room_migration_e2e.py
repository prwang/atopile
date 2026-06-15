"""
test_room_migration_e2e — stage C3 acceptance, the build-level half (BACKLOG §C3).

The protocol and the inline/contract ratchet live in
test/exporters/pcb/layout/test_room_migration_contract.py. These two tests pin
the parts that require a REAL build (sub-address + source-pcb resolution that
cannot be faithfully reproduced inline), against the layout_reuse example —
exactly the harness test_group_determinism.py uses (which the C3 commit deletes,
its determinism invariant subsumed by C3.3 below).

  * C3.2  atopile creates ZERO groups and writes the room onto footprint
          sheetname (the A4 "manual group deleted" failure mode stops existing
          because there is no atopile group code left).
  * C3.3  determinism THROUGH THE PULL PATH: the byte-stability that used to come
          from sorting group.members (the A1/A3 fix, layout_sync.py:495-500) now
          rides on route/zone insertion order, since there are no members to
          sort. So this MUST exercise the pull path (top layout absent), or it
          covers sync but not pull.

S0 ratchet: `_C3_LANDED` is the same symbol probe as the contract file; until C3
lands both tests strict-xfail (the build still emits groups).
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from faebryk.exporters.pcb.layout.layout_sync import LayoutSync
from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

EXAMPLE = _repo_root() / "examples" / "layout_reuse"
TOP_PCB = Path("layout") / "top" / "top.kicad_pcb"

_C3_LANDED = hasattr(LayoutSync, "pull_room_layout") and not hasattr(
    LayoutSync, "pull_group_layout"
)
needs_c3 = pytest.mark.xfail(
    not _C3_LANDED,
    reason="C3 room migration (group -> sheetname/path) not landed (S0 ratchet)",
    strict=True,
)


def _build(cwd: Path, hashseed: str) -> None:
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-v"],
        env={**os.environ, "NONINTERACTIVE": "1", "PYTHONHASHSEED": hashseed},
        cwd=cwd,
        stdout=print,
        stderr=print,
        timeout=300,
    )
    assert "Build successful! 🚀" in stdout + stderr


def _load_pcb(pcb_path: Path) -> "kicad.pcb.PcbFile":
    return kicad.loads(kicad.pcb.PcbFile, pcb_path.read_text())


@pytest.fixture
def example_copy(tmp_path: Path) -> Path:
    copy = tmp_path / EXAMPLE.name
    shutil.copytree(EXAMPLE, copy)
    cache_fixture = _repo_root() / "test" / "common" / "resources" / "easyeda-cache"
    shutil.copytree(cache_fixture, copy / "build" / "cache" / "parts" / "easyeda")
    return copy


def _managed(pcb) -> list:
    return [
        fp for fp in pcb.footprints
        if Property.try_get_property(fp.propertys, "atopile_address")
    ]


@needs_c3
def test_C3_2_atopile_creates_no_groups_and_writes_sheetname(
    example_copy: Path, save_tmp_path_on_failure: None
):
    _build(example_copy, hashseed="0")
    pcb = _load_pcb(example_copy / TOP_PCB).kicad_pcb

    managed = _managed(pcb)
    assert managed, "fixture should have atopile-managed footprints"

    # the room identity is carried on the footprint, not in a group
    for fp in managed:
        assert fp.sheetname, f"managed fp {fp.uuid} carries no room sheetname"

    # atopile put ZERO managed footprints into ANY KiCad group
    managed_uuids = {fp.uuid for fp in managed}
    for g in pcb.groups:
        assert not (set(g.members) & managed_uuids), (
            f"atopile group {g.name!r} still contains managed footprints"
        )


@needs_c3
def test_C3_3_determinism_through_pull_without_groups(
    example_copy: Path, save_tmp_path_on_failure: None
):
    assert not (example_copy / TOP_PCB).exists(), (
        "fixture must start without a top layout to exercise the pull path"
    )

    _build(example_copy, hashseed="0")
    first = (example_copy / TOP_PCB).read_bytes()

    pcb = _load_pcb(example_copy / TOP_PCB).kicad_pcb
    managed_uuids = {fp.uuid for fp in _managed(pcb)}
    for g in pcb.groups:
        assert not (set(g.members) & managed_uuids), (
            "pull path created an atopile group"
        )

    # byte-stable across hash seeds — determinism now rides on insertion order,
    # not group.members sorting (which no longer exists)
    _build(example_copy, hashseed="1")
    second = (example_copy / TOP_PCB).read_bytes()
    assert first == second, "fresh build then rebuild must be byte-identical"
