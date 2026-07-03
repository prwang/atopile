# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Lifecycle contract for managed rewrites over GUI-authored constructs
(BACKLOG §G follow-up to the tuning-pattern schema landing).

Pins that LayoutSync keeps generateds (KiCad tuning patterns) and teardrop
zones coherent through the clean/re-pull cycle:

- `_clean_room` deletes a generated together with its intra-room member
  tracks, LOUDLY (dangling member uuids are unsaveable by KiCad; silence is
  the only wrong option). Generateds whose members don't intersect the
  deleted copper survive untouched.
- `_clean_room` deletes intra-room teardrop zones like any intra-room-net
  zone — intentional: teardrops are derived copper of the deleted tracks and
  the re-pull restores the source board's teardrop zones with attr intact.
- `_sync_routes` pulls a generated iff ALL its member tracks were pulled,
  remapping member uuids to the pulled copies and translating the tuning
  geometry (origin/end/base_line) by the room offset. Partially pulled or
  empty-member generateds are dropped LOUDLY (half a tuning pattern is
  meaningless — its properties describe the complete member set).
- `_sync_routes` net honesty for zones: a zone carrying a real source net
  with no top-board mapping is dropped LOUDLY (the old net-0 fallthrough
  silently produced dead copper KiCad GCs on save). Net-0 passthrough
  remains ONLY for zones genuinely net-0 in the source (keepouts).
- A teardrop zone whose net maps IS pulled, with attr.teardrop intact.
"""

import logging
import shutil
import subprocess

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.layout.layout_sync import LayoutSync
from faebryk.libs.kicad.fileformats import kicad

needs_kicad_cli = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)

SEG_A = "aaaaaaaa-0001-4000-8000-000000000001"
SEG_B = "aaaaaaaa-0002-4000-8000-000000000002"
SEG_C = "aaaaaaaa-0003-4000-8000-000000000003"
GEN_FULL = "eeeeeeee-0001-4000-8000-000000000001"
GEN_PARTIAL = "eeeeeeee-0002-4000-8000-000000000002"
GEN_EMPTY = "eeeeeeee-0003-4000-8000-000000000003"
GEN_DISJOINT = "eeeeeeee-0004-4000-8000-000000000004"


def _board(
    nets: dict[int, str],
    fps: list[tuple[str, list[tuple[str, int, str]]]],
    tracks: str = "",
) -> str:
    """Tiny v9 board (same shape as test_layout_sync_nets._board): nets
    {number: name}, footprints as (atopile_address,
    [(pad_name, net_number, net_name)]), extra track/zone/generated text."""
    net_decls = "\n".join(f'    (net {n} "{name}")' for n, name in nets.items())
    fp_blocks = []
    for addr, pads in fps:
        pad_blocks = "\n".join(
            f'        (pad "{p}" smd rect (at {i} 0) (size 1 1) (layers "F.Cu")'
            f' (net {num} "{name}"))'
            for i, (p, num, name) in enumerate(pads)
        )
        fp_blocks.append(
            f'    (footprint "test:FP"\n'
            f'        (layer "F.Cu")\n'
            f"        (at 0 0)\n"
            f'        (property "atopile_address" "{addr}" (at 0 0) (layer "F.Fab")'
            f" (effects (font (size 1 1))))\n"
            f"{pad_blocks}\n"
            f"    )"
        )
    return (
        "(kicad_pcb\n"
        '    (version 20241229) (generator "test_atopile")'
        ' (generator_version "latest")\n'
        '    (layers (0 "F.Cu" signal) (2 "B.Cu" signal))\n'
        f"{net_decls}\n" + "\n".join(fp_blocks) + "\n" + tracks + "\n)"
    )


def _load(text: str) -> kicad.pcb.PcbFile:
    """Callers must keep the returned PcbFile alive while using .kicad_pcb
    (pyzig ownership, see BACKLOG)."""
    return kicad.loads(kicad.pcb.PcbFile, text)


def _segment(uuid: str, net: int, x: float) -> str:
    return (
        f"    (segment (start {x} 1) (end {x + 1} 2) (width 0.2)"
        f' (layer "F.Cu") (net {net}) (uuid "{uuid}"))'
    )


def _zone(uuid: str, net: int, net_name: str, attr: str = "") -> str:
    return (
        f'    (zone (net {net}) (net_name "{net_name}") (layer "F.Cu")\n'
        f'        (uuid "{uuid}")\n'
        f"{attr}"
        "        (hatch edge 0.5)\n"
        "        (polygon (pts (xy 0 0) (xy 5 0) (xy 5 5)))\n"
        "    )"
    )


TEARDROP_ATTR = "        (attr (teardrop (type padvia)))\n"


def _generated(
    uuid: str,
    name: str,
    members: list[str],
    base_line: str = "(pts (xy 1 1) (xy 3 3))",
) -> str:
    member_text = " ".join(f'"{m}"' for m in members)
    return (
        f'    (generated (uuid "{uuid}") (type tuning_pattern)'
        f' (name "{name}") (layer "F.Cu")\n'
        f"        (base_line {base_line})\n"
        "        (end (xy 3 3)) (origin (xy 1 1))\n"
        '        (initial_side "right") (last_netname "SUB_VCC")\n'
        "        (max_amplitude 1) (min_amplitude 0.1) (min_spacing 0.6)\n"
        "        (rounded yes) (single_sided no) (target_length 20)\n"
        f'        (tuning_mode "single")\n'
        f"        (members {member_text})\n"
        "    )"
    )


@pytest.fixture
def sync():
    top = _load(_board({0: "", 7: "TOP_VCC", 8: "TOP_GND"}, []))
    s = LayoutSync(top.kicad_pcb)
    s.__keepalive = top
    return s


# ---------------------------------------------------------------------------
# _sync_routes: generated pull (all-members) / loud drop (partial, empty)
# ---------------------------------------------------------------------------


PULL_TRACKS = "\n".join(
    [
        _segment(SEG_A, 1, 1),
        _segment(SEG_B, 1, 3),
        _segment(SEG_C, 2, 5),  # SUB_UNMAPPED -> dropped by the net contract
        _generated(GEN_FULL, "Full", [SEG_A, SEG_B]),
        _generated(GEN_PARTIAL, "Partial", [SEG_B, SEG_C]),
        _generated(GEN_EMPTY, "Empty", []),
    ]
)


@pytest.fixture
def pulled(sync, caplog):
    sub_file = _load(
        _board({0: "", 1: "SUB_VCC", 2: "SUB_UNMAPPED"}, [], tracks=PULL_TRACKS)
    )
    offset = kicad.pcb.Xy(x=100.0, y=50.0)
    with caplog.at_level(logging.WARNING):
        new = sync._sync_routes(
            sub_file.kicad_pcb, sync.pcb, {"SUB_VCC": "TOP_VCC"}, offset
        )
    return new, caplog, sub_file


def test_pull_generated_with_all_members_pulled_and_remapped(pulled):
    new, _, sub_file = pulled
    segments = [t for t in new if isinstance(t, kicad.pcb.Segment)]
    generateds = [t for t in new if isinstance(t, kicad.pcb.Generated)]

    # only the fully-pulled generated survives
    assert [g.name for g in generateds] == ["Full"]
    gen = generateds[0]

    # members remapped to the PULLED copies' fresh uuids, order preserved
    assert len(segments) == 2
    assert list(gen.members) == [segments[0].uuid, segments[1].uuid]
    assert set(gen.members).isdisjoint({SEG_A, SEG_B, SEG_C})

    # fresh identity; source untouched
    assert gen.uuid != GEN_FULL
    assert list(sub_file.kicad_pcb.generateds[0].members) == [SEG_A, SEG_B]

    # tuning geometry moved with the room offset
    assert (gen.origin.xy.x, gen.origin.xy.y) == (101.0, 51.0)
    assert (gen.end.xy.x, gen.end.xy.y) == (103.0, 53.0)
    assert [(p.x, p.y) for p in gen.base_line.pts.xys] == [
        (101.0, 51.0),
        (103.0, 53.0),
    ]
    # source geometry untouched
    src_gen = sub_file.kicad_pcb.generateds[0]
    assert (src_gen.origin.xy.x, src_gen.origin.xy.y) == (1.0, 1.0)

    # tuning properties preserved; net status cache remapped like the members
    assert gen.target_length == 20.0
    assert gen.tuning_mode == "single"
    assert gen.last_netname == "TOP_VCC"


def test_pull_drops_partial_and_empty_generateds_loudly(pulled):
    new, caplog, _ = pulled
    generateds = [t for t in new if isinstance(t, kicad.pcb.Generated)]
    assert {g.name for g in generateds} == {"Full"}

    # warnings were emitted during fixture setup
    records = caplog.get_records("setup")
    partial_warnings = [r.message for r in records if "'Partial'" in r.message]
    empty_warnings = [r.message for r in records if "'Empty'" in r.message]
    assert len(partial_warnings) == 1 and "1/2" in partial_warnings[0]
    assert len(empty_warnings) == 1 and "0/0" in empty_warnings[0]
    assert all(
        "dropping generated tuning_pattern" in m
        for m in partial_warnings + empty_warnings
    )


# ---------------------------------------------------------------------------
# _sync_routes: zone net honesty (task 3)
# ---------------------------------------------------------------------------


ZONE_TRACKS = "\n".join(
    [
        _zone("bbbbbbbb-0001-4000-8000-000000000001", 1, "SUB_VCC", TEARDROP_ATTR),
        _zone("bbbbbbbb-0002-4000-8000-000000000002", 2, "SUB_UNMAPPED", TEARDROP_ATTR),
        _zone("bbbbbbbb-0003-4000-8000-000000000003", 0, ""),  # keepout-style
    ]
)


@pytest.fixture
def pulled_zones(sync, caplog):
    sub_file = _load(
        _board({0: "", 1: "SUB_VCC", 2: "SUB_UNMAPPED"}, [], tracks=ZONE_TRACKS)
    )
    offset = kicad.pcb.Xy(x=100.0, y=50.0)
    with caplog.at_level(logging.WARNING):
        new = sync._sync_routes(
            sub_file.kicad_pcb, sync.pcb, {"SUB_VCC": "TOP_VCC"}, offset
        )
    return new, caplog, sub_file


def test_pull_teardrop_zone_with_mapped_net_keeps_attr(pulled_zones):
    new, _, _ = pulled_zones
    zones = [t for t in new if isinstance(t, kicad.pcb.Zone)]
    mapped = [z for z in zones if z.net == 7]
    assert len(mapped) == 1
    td = mapped[0]
    assert td.net_name == "TOP_VCC"
    assert td.attr is not None and td.attr.teardrop is not None
    assert td.attr.teardrop.type == "padvia"


def test_pull_drops_real_net_zone_without_mapping_loudly(pulled_zones):
    """A real-but-unmappable-net zone must NOT degrade to net-0 dead copper
    (KiCad GCs net-0 fill on save — silent loss); it takes the loud drop
    branch like segments do."""
    new, caplog, _ = pulled_zones
    zones = [t for t in new if isinstance(t, kicad.pcb.Zone)]
    assert all(z.net_name != "SUB_UNMAPPED" for z in zones)
    # no pulled zone silently became net 0 while claiming the unmapped net
    assert len(zones) == 2  # mapped teardrop + genuine net-0
    assert any(
        "Zone" in r.message and "SUB_UNMAPPED" in r.message
        for r in caplog.get_records("setup")
    )


def test_pull_keeps_genuinely_net0_zone(pulled_zones):
    new, caplog, _ = pulled_zones
    zones = [t for t in new if isinstance(t, kicad.pcb.Zone)]
    net0 = [z for z in zones if z.net == 0]
    assert len(net0) == 1
    # passthrough is silent: net-0 zones (keepouts) are legitimate as-is, so
    # the ONLY zone drop-warning is the unmapped real-net one
    zone_warnings = [
        r for r in caplog.get_records("setup") if "dropping Zone" in r.message
    ]
    assert len(zone_warnings) == 1 and "SUB_UNMAPPED" in zone_warnings[0].message
    # geometry still offset like any pulled object
    assert (net0[0].polygon.pts.xys[0].x, net0[0].polygon.pts.xys[0].y) == (
        100.0,
        50.0,
    )


# ---------------------------------------------------------------------------
# arc-bearing pulls: pts arcs must translate WITH the xys (no shear)
# ---------------------------------------------------------------------------

GEN_ARC = "eeeeeeee-0005-4000-8000-000000000005"
ZONE_ARC_UUID = "bbbbbbbb-0009-4000-8000-000000000009"

# base_line/outline chains with a MID-chain (arc ...) entry — the KiCad file
# shape of a rounded-corner SHAPE_LINE_CHAIN (pcb.zig Pts/PtsArc). Pre-fix,
# _move_generated and move_object translated only pts.xys, tearing the arc
# off its chain by the full room offset.
ARC_BASE_LINE = (
    "(pts (xy 1 1) (arc (start 1 1) (mid 2 0.6) (end 3 1)) (xy 3 3))"
)

ARC_PULL_TRACKS = "\n".join(
    [
        _segment(SEG_A, 1, 1),
        _segment(SEG_B, 1, 3),
        _generated(GEN_ARC, "ArcTuned", [SEG_A, SEG_B], base_line=ARC_BASE_LINE),
        f'    (zone (net 1) (net_name "SUB_VCC") (layer "F.Cu")\n'
        f'        (uuid "{ZONE_ARC_UUID}")\n'
        "        (hatch edge 0.5)\n"
        "        (polygon (pts (xy 0 0) (arc (start 5 0) (mid 6 2.5) (end 5 5))"
        " (xy 0 5)))\n"
        "    )",
    ]
)


@pytest.fixture
def pulled_arcs(sync, caplog):
    sub_file = _load(
        _board({0: "", 1: "SUB_VCC"}, [], tracks=ARC_PULL_TRACKS)
    )
    offset = kicad.pcb.Xy(x=100.0, y=50.0)
    with caplog.at_level(logging.WARNING):
        new = sync._sync_routes(
            sub_file.kicad_pcb, sync.pcb, {"SUB_VCC": "TOP_VCC"}, offset
        )
    return new, sub_file


def test_pulled_baseline_arc_moves_with_xys(pulled_arcs):
    """Pulling a room with an arc-bearing tuning baseline translates the arc
    start/mid/end by the same offset as the xy points — a sheared baseline
    would make the next GUI re-tune regenerate copper at the source-room
    location."""
    new, sub_file = pulled_arcs
    (gen,) = [t for t in new if isinstance(t, kicad.pcb.Generated)]

    assert [(p.x, p.y) for p in gen.base_line.pts.xys] == [
        (101.0, 51.0),
        (103.0, 53.0),
    ]
    (arc,) = gen.base_line.pts.arcs
    assert (arc.start.x, arc.start.y) == (101.0, 51.0)
    assert (arc.mid.x, arc.mid.y) == (102.0, 50.6)
    assert (arc.end.x, arc.end.y) == (103.0, 51.0)
    # chain position survives the pull: the arc is still after the first xy
    assert arc.xys_before == 1

    # source untouched
    (src_arc,) = sub_file.kicad_pcb.generateds[0].base_line.pts.arcs
    assert (src_arc.start.x, src_arc.start.y) == (1.0, 1.0)


def test_pulled_zone_outline_arc_moves_with_xys(pulled_arcs):
    """Same contract for zone outlines through PCB_Transformer.move_object:
    translating only the xy corners leaves the arc at source coordinates — a
    self-intersecting outline spanning the room offset, written silently."""
    new, _ = pulled_arcs
    (zone,) = [t for t in new if isinstance(t, kicad.pcb.Zone)]

    assert [(p.x, p.y) for p in zone.polygon.pts.xys] == [
        (100.0, 50.0),
        (100.0, 55.0),
    ]
    (arc,) = zone.polygon.pts.arcs
    assert (arc.start.x, arc.start.y) == (105.0, 50.0)
    assert (arc.mid.x, arc.mid.y) == (106.0, 52.5)
    assert (arc.end.x, arc.end.y) == (105.0, 55.0)
    assert arc.xys_before == 1


# ---------------------------------------------------------------------------
# _clean_room: generated + teardrop-zone lifecycle (tasks 1 and 4)
# ---------------------------------------------------------------------------


CLEAN_TRACKS = "\n".join(
    [
        _segment(SEG_A, 1, 1),
        _segment(SEG_B, 1, 3),
        _zone("bbbbbbbb-0004-4000-8000-000000000004", 1, "R_NET", TEARDROP_ATTR),
        _generated(GEN_FULL, "Doomed", [SEG_A, SEG_B]),
        _generated(GEN_DISJOINT, "Survivor", ["ffffffff-0001-4000-8000-000000000001"]),
    ]
)


@pytest.fixture
def cleaned(caplog):
    board = _load(
        _board(
            {0: "", 1: "R_NET"},
            [("top.mod.r1", [("1", 1, "R_NET"), ("2", 1, "R_NET")])],
            tracks=CLEAN_TRACKS,
        )
    )
    pcb = board.kicad_pcb
    for fp in pcb.footprints:
        fp.sheetname = "top.mod"
    s = LayoutSync(pcb)
    with caplog.at_level(logging.WARNING):
        s._clean_room("top.mod")
    return pcb, caplog, board


def test_clean_room_deletes_generated_with_member_tracks_and_warns(cleaned):
    pcb, caplog, _ = cleaned
    # member segments gone (pre-existing contract) AND the generated gone with
    # them — a surviving generated would carry dangling member uuids, which
    # KiCad refuses to save.
    assert len(pcb.segments) == 0
    assert [g.name for g in pcb.generateds] == ["Survivor"]
    warnings = [
        r.message
        for r in caplog.get_records("setup")
        if "deleting generated tuning_pattern 'Doomed'" in r.message
    ]
    assert len(warnings) == 1
    assert "top.mod" in warnings[0]  # names the room


def test_clean_room_keeps_disjoint_generated_silently(cleaned):
    """Only generateds whose members INTERSECT the deleted copper are removed;
    an unrelated generated is not collateral and produces no warning."""
    pcb, caplog, _ = cleaned
    assert [g.name for g in pcb.generateds] == ["Survivor"]
    assert not any("'Survivor'" in r.message for r in caplog.get_records("setup"))


def test_clean_room_deletes_intra_room_teardrop_zone(cleaned):
    """Intentional (documented in the _clean_room docstring): teardrop zones
    on intra-room nets are derived copper of the deleted tracks and are
    deleted with them; the re-pull restores the source board's teardrop zones
    with attr.teardrop intact (pinned by
    test_pull_teardrop_zone_with_mapped_net_keeps_attr)."""
    pcb, _, _ = cleaned
    assert len(pcb.zones) == 0


# ---------------------------------------------------------------------------
# pull_room_layout end-to-end + kicad-cli oracle
# ---------------------------------------------------------------------------


class _StubbedSync(LayoutSync):
    """LayoutSync with the sub-board loader stubbed (no project config)."""

    def __init__(self, pcb, sub_pcb):
        self._stub_sub_pcb = sub_pcb
        super().__init__(pcb)

    def _get_pcb(self, pcb_address: str):
        return self._stub_sub_pcb


def _fp_with_subaddress(
    addr: str,
    sub_addr: str,
    pads: list[tuple[str, int, str]],
    at: str = "0 0",
    uuid: str | None = None,
) -> str:
    pad_blocks = "\n".join(
        f'        (pad "{p}" smd rect (at {i} 0) (size 1 1) (layers "F.Cu")'
        f' (net {num} "{name}"))'
        for i, (p, num, name) in enumerate(pads)
    )
    uuid_block = f'        (uuid "{uuid}")\n' if uuid else ""
    return (
        f'    (footprint "test:FP"\n'
        f'        (layer "F.Cu")\n'
        f"{uuid_block}"
        f"        (at {at})\n"
        f'        (property "atopile_address" "{addr}" (at 0 0) (layer "F.Fab")'
        f" (effects (font (size 1 1))))\n"
        f'        (property "atopile_subaddresses" "[{sub_addr}]" (at 0 0)'
        f' (layer "F.Fab") (effects (font (size 1 1))))\n'
        f"{pad_blocks}\n"
        f"    )"
    )


@pytest.fixture
def room_pull():
    """Drive the REAL pull_room_layout path (clean → sync → insert), so the
    Generated insertion branch is exercised, not a test replica of it."""
    top_text = (
        "(kicad_pcb\n"
        '    (version 20241229) (generator "test_atopile")'
        ' (generator_version "latest")\n'
        '    (layers (0 "F.Cu" signal) (2 "B.Cu" signal))\n'
        '    (net 0 "")\n    (net 7 "TOP_VCC")\n'
        + _fp_with_subaddress(
            "top.mod.r1",
            "sub/sub.kicad_pcb:mod.r1",
            [("1", 7, "TOP_VCC"), ("2", 7, "TOP_VCC")],
            at="10 10",
        )
        + "\n)"
    )
    sub_tracks = "\n".join(
        [
            _segment(SEG_A, 1, 1),
            _segment(SEG_B, 1, 3),
            _zone("bbbbbbbb-0005-4000-8000-000000000005", 1, "SUB_VCC", TEARDROP_ATTR),
            _generated(GEN_FULL, "Full", [SEG_A, SEG_B]),
        ]
    )
    top_file = _load(top_text)
    sub_file = _load(
        _board(
            {0: "", 1: "SUB_VCC"},
            [("mod.r1", [("1", 1, "SUB_VCC"), ("2", 1, "SUB_VCC")])],
            tracks=sub_tracks,
        )
    )
    s = _StubbedSync(top_file.kicad_pcb, sub_file.kicad_pcb)
    s.sync_rooms()
    s.pull_room_layout("top")
    return top_file, sub_file, s


def test_pull_room_layout_inserts_generated_into_board(room_pull):
    top_file, _, _ = room_pull
    pcb = top_file.kicad_pcb

    assert len(pcb.segments) == 2
    assert [g.name for g in pcb.generateds] == ["Full"]
    gen = pcb.generateds[0]
    assert list(gen.members) == [seg.uuid for seg in pcb.segments]
    # room offset (10, 10) applied to the tuning geometry
    assert (gen.origin.xy.x, gen.origin.xy.y) == (11.0, 11.0)

    # the teardrop zone came along, net-mapped, attr intact
    assert len(pcb.zones) == 1
    z = pcb.zones[0]
    assert z.net == 7 and z.attr.teardrop.type == "padvia"


ROOM_FP = "ffffffff-0001-4000-8000-000000000001"
OUTSIDE_FP = "ffffffff-0002-4000-8000-000000000002"


@pytest.fixture
def inter_room_pull():
    """room_pull, but TOP_VCC also reaches a pad OUTSIDE the room (a room
    interface net): _clean_room's intra = inside - outside cannot reclaim it,
    so only insert-dedup keeps repeated pulls idempotent."""
    outside_fp = (
        '    (footprint "test:CONN"\n'
        '        (layer "F.Cu")\n'
        f'        (uuid "{OUTSIDE_FP}")\n'
        "        (at 50 50)\n"
        '        (property "atopile_address" "other.conn1" (at 0 0)'
        ' (layer "F.Fab") (effects (font (size 1 1))))\n'
        '        (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")'
        ' (net 7 "TOP_VCC"))\n'
        "    )"
    )
    top_text = (
        "(kicad_pcb\n"
        '    (version 20241229) (generator "test_atopile")'
        ' (generator_version "latest")\n'
        '    (layers (0 "F.Cu" signal) (2 "B.Cu" signal))\n'
        '    (net 0 "")\n    (net 7 "TOP_VCC")\n'
        + _fp_with_subaddress(
            "top.mod.r1",
            "sub/sub.kicad_pcb:mod.r1",
            [("1", 7, "TOP_VCC"), ("2", 7, "TOP_VCC")],
            at="10 10",
            uuid=ROOM_FP,
        )
        + "\n"
        + outside_fp
        + "\n)"
    )
    sub_tracks = "\n".join(
        [
            _segment(SEG_A, 1, 1),
            _segment(SEG_B, 1, 3),
            _zone("bbbbbbbb-0005-4000-8000-000000000005", 1, "SUB_VCC", TEARDROP_ATTR),
            _generated(GEN_FULL, "Full", [SEG_A, SEG_B]),
        ]
    )
    top_file = _load(top_text)
    sub_file = _load(
        _board(
            {0: "", 1: "SUB_VCC"},
            [("mod.r1", [("1", 1, "SUB_VCC"), ("2", 1, "SUB_VCC")])],
            tracks=sub_tracks,
        )
    )
    s = _StubbedSync(top_file.kicad_pcb, sub_file.kicad_pcb)
    s.sync_rooms()
    return top_file, sub_file, s


def test_repeated_pull_of_inter_room_net_is_idempotent(inter_room_pull):
    """Pull x3 must converge to exactly ONE copy of the inter-room net's
    copper and tuning pattern. _clean_room skips inter-room nets by design
    (deleting them would eat user copper outside the room), so without
    insert-dedup each pull stacked another 2 segments + teardrop zone +
    generated, silently."""
    top_file, _, s = inter_room_pull
    pcb = top_file.kicad_pcb

    for _ in range(3):
        s.pull_room_layout("top")

    assert len(pcb.segments) == 2
    assert len(pcb.zones) == 1
    assert [g.name for g in pcb.generateds] == ["Full"]
    # the re-pulled generated's members converged onto the EXISTING pulled
    # tracks (dedup remaps source uuids onto the first pull's copies)
    assert list(pcb.generateds[0].members) == [seg.uuid for seg in pcb.segments]
    assert pcb.zones[0].net == 7 and pcb.zones[0].attr.teardrop.type == "padvia"


def test_repeated_pull_intra_room_still_replaces(room_pull):
    """Control: an intra-room net keeps the clean -> re-pull lifecycle (fresh
    copies each pull, counts stable) — dedup must not freeze the normal
    replace path."""
    top_file, _, s = room_pull
    pcb = top_file.kicad_pcb
    first_uuids = {seg.uuid for seg in pcb.segments}

    s.pull_room_layout("top")

    assert len(pcb.segments) == 2
    assert len(pcb.zones) == 1
    assert [g.name for g in pcb.generateds] == ["Full"]
    # clean deleted the first pull's copies; the re-pull inserted fresh ones
    assert {seg.uuid for seg in pcb.segments}.isdisjoint(first_uuids)
    assert list(pcb.generateds[0].members) == [seg.uuid for seg in pcb.segments]


@needs_kicad_cli
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_kicad_ingests_pulled_generated_and_teardrop(room_pull, tmp_path):
    """KiCad-can-read-us oracle: the pulled board (generated tuning pattern +
    teardrop zone) survives `kicad-cli pcb upgrade --force` (rc==0) with the
    generated block and its members intact (KiCad drops ghost tuning patterns
    whose members do not resolve), and `kicad-cli pcb drc` can load it."""
    top_file, _, _ = room_pull
    pcb = top_file.kicad_pcb
    board = tmp_path / "pulled.kicad_pcb"
    board.write_text(kicad.dumps(top_file))

    r = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", "--force", str(board)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"upgrade failed:\n{r.stderr}"

    up = board.read_text()
    assert "(generated" in up and "tuning_pattern" in up
    # members survived KiCad's ghost-pattern GC == they resolved to real tracks
    for member in pcb.generateds[0].members:
        assert f'"{member}"' in up
    assert "(teardrop" in up

    r = subprocess.run(
        [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "-o",
            str(tmp_path / "drc.json"),
            str(board),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"drc failed to load the board:\n{r.stderr}"
