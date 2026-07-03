# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Unit tests for LayoutSync's net remapping (BACKLOG P0.1 T7).

Pins the name-keyed remapping pipeline (sub-layout net name → top net name →
top net number) before the v10 migration rewires the number side of it. Until
now this logic was only covered indirectly through end-to-end builds.
"""

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.layout.layout_sync import LayoutSync
from faebryk.libs.kicad.fileformats import kicad


def _board(nets: dict[int, str], fps: list[tuple[str, list[tuple[str, int, str]]]],
           tracks: str = "") -> str:
    """Tiny v9 board: nets {number: name}, footprints as
    (atopile_address, [(pad_name, net_number, net_name)]), extra track text."""
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
            f'        (at 0 0)\n'
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
    """Callers must keep the returned PcbFile alive while using .kicad_pcb:
    the wrapper owns the zig-side memory, and a GC'd wrapper leaves sub-objects
    dangling — they silently alias the next parse (use-after-free, see the
    pyzig-ownership fact in BACKLOG)."""
    return kicad.loads(kicad.pcb.PcbFile, text)


@pytest.fixture
def sync():
    top = _load(_board({0: "", 7: "TOP_VCC", 8: "TOP_GND"}, []))
    s = LayoutSync(top.kicad_pcb)
    s.__keepalive = top
    return s


def test_get_net_number_resolves_by_name(sync):
    assert sync._get_net_number(sync.pcb, "TOP_VCC") == 7
    assert sync._get_net_number(sync.pcb, "TOP_GND") == 8
    # the empty net is a real entry (number 0), not the unknown case
    assert sync._get_net_number(sync.pcb, "") == 0


def test_get_net_number_raises_on_unknown(sync):
    """Inverted at P0.2 S6b (was test_get_net_number_silently_maps_unknown_to_
    zero). An unknown net name used to map to 0 ("no net") with no diagnostic,
    so a typo in a net map silently disconnected copper. It now raises: callers
    only pass names that must exist on the target board, so a miss is a real
    map/board desync, not a routine no-net."""
    with pytest.raises(KeyError, match="TYPO_NET"):
        sync._get_net_number(sync.pcb, "TYPO_NET")


def test_generate_net_map_maps_by_pad_topology(sync):
    """Net mapping is derived from pad-name correspondence between footprints
    at mapped addresses — names on both sides, numbers irrelevant."""
    source_file = _load(
        _board(
            {0: "", 1: "SUB_VCC", 2: "SUB_GND"},
            [("sub.r1", [("1", 1, "SUB_VCC"), ("2", 2, "SUB_GND")])],
        )
    )
    # deliberately different numbering on the target side
    target_file = _load(
        _board(
            {0: "", 8: "TOP_GND", 7: "TOP_VCC"},
            [("top.mod.r1", [("1", 7, "TOP_VCC"), ("2", 8, "TOP_GND")])],
        )
    )

    net_map = sync._generate_net_map(
        source_file.kicad_pcb, target_file.kicad_pcb, {"sub.r1": "top.mod.r1"}
    )
    assert net_map == {"SUB_VCC": "TOP_VCC", "SUB_GND": "TOP_GND"}


def test_generate_net_map_ignores_unconnected_pads(sync):
    source_file = _load(
        _board(
            {0: "", 1: "SUB_VCC"},
            [("sub.r1", [("1", 1, "SUB_VCC"), ("2", 0, "")])],
        )
    )
    target_file = _load(
        _board(
            {0: "", 7: "TOP_VCC"},
            [("top.mod.r1", [("1", 7, "TOP_VCC"), ("2", 0, "")])],
        )
    )

    net_map = sync._generate_net_map(
        source_file.kicad_pcb, target_file.kicad_pcb, {"sub.r1": "top.mod.r1"}
    )
    assert net_map == {"SUB_VCC": "TOP_VCC"}


SUB_TRACKS = """
    (segment (start 1 1) (end 2 2) (width 0.2) (layer "F.Cu") (net 1)
        (uuid "aaaaaaaa-1111-2222-3333-444444444444"))
    (segment (start 3 3) (end 4 4) (width 0.2) (layer "F.Cu") (net 2)
        (uuid "aaaaaaaa-5555-6666-7777-888888888888"))
    (zone (net 1) (net_name "SUB_VCC") (layer "F.Cu")
        (uuid "bbbbbbbb-1111-2222-3333-444444444444")
        (hatch edge 0.5)
        (polygon (pts (xy 0 0) (xy 5 0) (xy 5 5)))
    )
"""


def test_sync_routes_remaps_nets_and_moves_geometry(sync, caplog):
    sub_file = _load(
        _board({0: "", 1: "SUB_VCC", 2: "SUB_UNMAPPED"}, [], tracks=SUB_TRACKS)
    )
    sub = sub_file.kicad_pcb
    offset = kicad.pcb.Xy(x=100.0, y=50.0)

    import logging

    with caplog.at_level(logging.WARNING):
        new = sync._sync_routes(sub, sync.pcb, {"SUB_VCC": "TOP_VCC"}, offset)

    segments = [t for t in new if isinstance(t, kicad.pcb.Segment)]
    zones = [t for t in new if isinstance(t, kicad.pcb.Zone)]
    # the SUB_UNMAPPED segment is DROPPED, loudly — a track whose net cannot be
    # mapped must not be pulled as net-0 dead copper (it dangles off-board AND
    # makes its net unroutable: the router must reach all of a net's copper).
    # (The old contract disconnected it to net 0; that was the relic-track bug.)
    assert len(segments) == 1 and len(zones) == 1
    assert any("SUB_UNMAPPED" in r.message for r in caplog.records)

    mapped = segments[0]
    assert mapped.start.x == 101.0
    assert mapped.net == 7  # SUB_VCC → TOP_VCC → top number
    assert mapped.start.y == 51.0  # offset applied

    # zones carry the v9 dual key: both number and name must be remapped
    assert zones[0].net == 7
    assert zones[0].net_name == "TOP_VCC"

    # copies get fresh uuids; the source is left untouched
    assert {t.uuid for t in new}.isdisjoint(
        {"aaaaaaaa-1111-2222-3333-444444444444",
         "aaaaaaaa-5555-6666-7777-888888888888",
         "bbbbbbbb-1111-2222-3333-444444444444"}
    )
    assert sub.segments[0].net == 1
