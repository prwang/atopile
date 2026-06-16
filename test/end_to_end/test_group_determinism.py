"""
Determinism + manual-edit preservation regressions for the build → .kicad_pcb
path. Originally the "group determinism" suite (BACKLOG §A: A1/A3 member-sort
determinism, A4 manual-group preservation); after §C3 atopile no longer creates
KiCad groups (room identity moved to footprint sheetname), so the group-specific
invariants moved:

  * fresh-build determinism through the pull path  -> test_room_migration_e2e.py
    ::test_C3_3_determinism_through_pull_without_groups
  * KiCad preserves the room across upgrade        -> test_room_migration_contract
    .py::test_C3_4 (now sheetname-based, not group-based)

What remains here are the two invariants NOT subsumed by C3 and that must never
regress:

  * incremental-add determinism + pre-existing designator preservation;
  * A4: a user's manual segment / named group / rule-area zone survives rebuilds
    (atopile must never touch a non-atopile construct — now guaranteed by
    construction since sync_rooms creates/edits no groups at all).

DETERMINISM ORACLE = SEMANTIC, NOT BYTES. uuids are opaque (random uuid4, no
metadata — BACKLOG §G), so two builds may differ byte-for-byte while being
identical in every way that matters. Asserting raw bytes (the old
`read_bytes() == baseline`) would assert the meaningless random-id part and is a
category error under the opaque-uuid principle. These tests assert
`semantic_view()` equality instead: placement + connectivity (net *names*) +
structure, with uuids and net numbers excluded. (Caveat: semantic_view still
encodes group membership as member uuids; harmless here — steady-state has no
groups and the manual test's group holds a fixed test-chosen uuid — but a fuller
oracle would key group membership by member address.)
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.kicad.semantic_view import semantic_view
from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

EXAMPLE = _repo_root() / "examples" / "layout_reuse"
TOP_PCB = Path("layout") / "top" / "top.kicad_pcb"


def _build(cwd: Path, hashseed: str) -> None:
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-v"],
        env={
            **os.environ,
            "NONINTERACTIVE": "1",
            "PYTHONHASHSEED": hashseed,
            # seeded part cache is authoritative — never re-fetch from EasyEDA
            # (download-once; keeps these tests offline & WAF-immune)
            "FBRK_PARTS_NO_REFRESH": "y",
        },
        cwd=cwd,
        stdout=print,
        stderr=print,
        timeout=300,
    )
    assert "Build successful! 🚀" in stdout + stderr


def _load_pcb(pcb_path: Path) -> "kicad.pcb.PcbFile":
    # parse from text so these tests are independent of the loads Path cache
    return kicad.loads(kicad.pcb.PcbFile, pcb_path.read_text())


def _semantic(pcb_path: Path) -> dict:
    """The uuid-independent semantic view of a built board: footprints/pads with
    placements and net *names*, copper geometry, zones, groups — never uuids,
    never net numbers. This is the ONLY legitimate determinism oracle: uuids are
    opaque (random uuid4), so two builds may differ byte-for-byte while being
    semantically identical. Asserting raw bytes would assert the meaningless part;
    asserting this view asserts what determinism actually means (same placement +
    connectivity + structure modulo opaque ids)."""
    return semantic_view(_load_pcb(pcb_path).kicad_pcb)


def _assert_rooms_tagged_no_atopile_groups(pcb_path: Path) -> None:
    """Post-§C3 successor of `_assert_members_sorted`: every managed footprint
    carries its room identity on `sheetname`, and atopile put no managed
    footprint into any KiCad group (the old member-sort determinism concern is
    gone — there are no atopile group member lists left to order)."""
    pcb = _load_pcb(pcb_path).kicad_pcb
    managed = [
        fp for fp in pcb.footprints
        if Property.try_get_property(fp.propertys, "atopile_address")
    ]
    assert managed, "expected atopile-managed footprints"
    for fp in managed:
        assert fp.sheetname, f"managed fp {fp.uuid} not tagged with a room sheetname"
    managed_uuids = {fp.uuid for fp in managed}
    for g in pcb.groups:
        assert not (set(g.members) & managed_uuids), (
            f"atopile group {g.name!r} contains managed footprints"
        )


@pytest.fixture
def example_copy(tmp_path: Path) -> Path:
    copy = tmp_path / EXAMPLE.name
    shutil.copytree(EXAMPLE, copy)
    # Seed the part cache so builds never hit the EasyEDA API (rate-limited
    # 403s would make these determinism tests flaky in CI).
    cache_fixture = _repo_root() / "test" / "common" / "resources" / "easyeda-cache"
    shutil.copytree(cache_fixture, copy / "build" / "cache" / "parts" / "easyeda")
    return copy


def test_steady_state_and_incremental_add_deterministic(
    example_copy: Path, save_tmp_path_on_failure: None
):
    """Steady-state rebuilds stay SEMANTICALLY identical (same placement +
    connectivity + structure modulo opaque uuids — NOT byte-identical, which would
    assert the meaningless random-uuid part); adding a module instance triggers the
    pull path for the new room only, and the next build is semantically stable.
    Pre-existing designators must survive the addition (keep_designators default).
    The two builds use different PYTHONHASHSEED on purpose: a hash-order-dependent
    result would diverge, and the sorted semantic view would catch it."""
    _build(example_copy, hashseed="0")
    baseline = _semantic(example_copy / TOP_PCB)

    # steady state: a rebuild is semantically identical (uuids may differ)
    _build(example_copy, hashseed="1")
    assert _semantic(example_copy / TOP_PCB) == baseline

    pcb_file_before = _load_pcb(example_copy / TOP_PCB)
    refs_before = {
        Property.try_get_property(fp.propertys, "Reference")
        for fp in pcb_file_before.kicad_pcb.footprints
    }

    # incremental: add a 4th Sub instance
    src = example_copy / "layout_reuse.ato"
    src.write_text(src.read_text().replace("new Sub[3]", "new Sub[4]"))

    _build(example_copy, hashseed="0")
    after_add = _semantic(example_copy / TOP_PCB)
    _assert_rooms_tagged_no_atopile_groups(example_copy / TOP_PCB)

    _build(example_copy, hashseed="1")
    assert _semantic(example_copy / TOP_PCB) == after_add, (
        "build after adding an instance must already be in canonical semantic order"
    )

    pcb_file_after = _load_pcb(example_copy / TOP_PCB)
    refs_after = {
        Property.try_get_property(fp.propertys, "Reference")
        for fp in pcb_file_after.kicad_pcb.footprints
    }
    assert refs_before <= refs_after, (
        f"pre-existing designators changed: {refs_before - refs_after}"
    )


MANUAL_SEG_UUID = "11111111-2222-3333-4444-555555555555"
MANUAL_GROUP_UUID = "99999999-8888-7777-6666-555555555555"
MANUAL_ZONE_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _manual_injection(net_number: int) -> str:
    """A manually routed segment, a user-named group containing it, and a
    placement rule area — the kinds of edits a human makes in KiCad."""
    return f"""\t(segment
\t\t(start 95 95)
\t\t(end 100 95)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net {net_number})
\t\t(uuid "{MANUAL_SEG_UUID}")
\t)
\t(group "manual_human_group"
\t\t(uuid "{MANUAL_GROUP_UUID}")
\t\t(locked no)
\t\t(members "{MANUAL_SEG_UUID}")
\t)
\t(zone
\t\t(net 0)
\t\t(net_name "")
\t\t(layers "F.Cu" "B.Cu")
\t\t(uuid "{MANUAL_ZONE_UUID}")
\t\t(name "manual_room")
\t\t(hatch edge 0.5)
\t\t(keepout
\t\t\t(tracks allowed)
\t\t\t(vias allowed)
\t\t\t(pads allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints allowed)
\t\t)
\t\t(placement
\t\t\t(enabled yes)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0.5)
\t\t\t(thermal_bridge_width 0.5)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 90 90) (xy 130 90) (xy 130 120) (xy 90 120)
\t\t\t)
\t\t)
\t)
"""


def test_manual_edits_preserved(example_copy: Path, save_tmp_path_on_failure: None):
    """A4: manual segments, user-named groups (incl. their member contents) and
    rule areas survive rebuilds. Post-§C3 this is guaranteed by construction —
    sync_rooms creates and edits no groups, and _clean_room only deletes a room's
    own intra-room-net copper (the manual segment is on net 0 / a non-room net),
    so a non-atopile construct is never touched."""
    _build(example_copy, hashseed="0")
    pcb_path = example_copy / TOP_PCB

    pcb_file = _load_pcb(pcb_path)
    net_number = next(
        n.number for n in pcb_file.kicad_pcb.nets if n.number != 0
    )

    txt = pcb_path.read_text()
    closing = txt.rfind(")")
    pcb_path.write_text(
        txt[:closing] + _manual_injection(net_number) + txt[closing:]
    )

    _build(example_copy, hashseed="1")

    rebuilt_file = _load_pcb(pcb_path)
    rebuilt = rebuilt_file.kicad_pcb
    assert any(s.uuid == MANUAL_SEG_UUID for s in rebuilt.segments), (
        "manual segment inside a user group was deleted by the rebuild"
    )
    manual_groups = [g for g in rebuilt.groups if g.uuid == MANUAL_GROUP_UUID]
    assert manual_groups and MANUAL_SEG_UUID in manual_groups[0].members
    zones = [z for z in rebuilt.zones if z.uuid == MANUAL_ZONE_UUID]
    assert zones and zones[0].name == "manual_room"
    assert zones[0].placement is not None and zones[0].placement.enabled

    # atopile itself created no groups — the only group is the user's manual one
    managed_uuids = {
        fp.uuid for fp in rebuilt.footprints
        if Property.try_get_property(fp.propertys, "atopile_address")
    }
    for g in rebuilt.groups:
        assert not (set(g.members) & managed_uuids), (
            f"atopile group {g.name!r} contains managed footprints"
        )

    # manual elements must not disturb steady-state semantic determinism
    # (semantic, not bytes: opaque uuids may churn without meaning). The manual
    # group's member here is the fixed test-chosen MANUAL_SEG_UUID, so the group
    # section of the view is stable across the rebuild.
    stable = _semantic(pcb_path)
    _build(example_copy, hashseed="0")
    assert _semantic(pcb_path) == stable
