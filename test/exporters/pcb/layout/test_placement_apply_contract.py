# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_placement_apply_contract — build-side placement consumption (BACKLOG §D-Tier3).

The pure priority oracle `resolve_component_pose` (text > reuse) is pinned by
test_placement_contract. THIS file pins the build-side half: `apply_placements`
moves the managed footprints (matched by `atopile_address`) to their resolved
poses, overriding the auto-grid spread — room-relative composes through the room
origin, `absolute` lands verbatim, rotation + side are applied, and a placement
naming no managed footprint is loud.
"""

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.layout.layout_plan import (
    LayoutPlan,
    LayoutPlanError,
    Placement,
    Room,
)
from faebryk.exporters.pcb.layout.placement import apply_placements
from faebryk.libs.kicad.fileformats import kicad


def _board(footprints) -> kicad.pcb.PcbFile:
    """footprints: list of (addr, sheetname, (fx, fy)). One pad each so they are
    well-formed; managed via the atopile_address property."""
    lines = [
        "(kicad_pcb",
        "\t(version 20241229)",
        '\t(generator "test_placement_apply")',
        '\t(generator_version "10.0")',
        "\t(general (thickness 1.6))",
        '\t(layers (0 "F.Cu" signal) (31 "B.Cu" signal))',
        '\t(net 0 "")',
    ]
    u = [0]

    def uuid():
        u[0] += 1
        return f"00000000-0000-0000-0000-{u[0]:012x}"

    for addr, sheetname, (fx, fy) in footprints:
        ref = addr.rsplit(".", 1)[-1]
        lines += [
            '\t(footprint "test:FP"',
            '\t\t(layer "F.Cu")',
            f'\t\t(uuid "{uuid()}")',
            f"\t\t(at {fx} {fy} 0)",
            f'\t\t(sheetname "{sheetname}")',
            f'\t\t(property "Reference" "{ref}" (at 0 0)'
            ' (layer "F.SilkS") (effects (font (size 1 1))))',
            f'\t\t(property "atopile_address" "{addr}" (at 0 0)'
            ' (layer "F.Fab") (effects (font (size 1 1))))',
            '\t\t(pad "1" smd rect (at 0.5 0 0) (size 1 1) (layers "F.Cu")'
            f' (uuid "{uuid()}"))',
            "\t)",
        ]
    lines.append(")")
    return kicad.loads(kicad.pcb.PcbFile, "\n".join(lines) + "\n")


def _fp(pcb, addr):
    from faebryk.libs.kicad.fileformats import Property

    for fp in pcb.footprints:
        if Property.try_get_property(fp.propertys, "atopile_address") == addr:
            return fp
    raise AssertionError(f"no footprint for {addr!r}")


_FPS = [("top.r1.u1", "top.r1", (0.0, 0.0))]


# ===========================================================================
# PA1 — room-relative placement composes through the room origin + rotation,
# overriding the footprint's pre-existing (grid) pose. Returns moved addresses.
# ===========================================================================
def test_room_relative_placement_moves_and_composes():
    pcb = _board(_FPS).kicad_pcb
    plan = LayoutPlan(
        rooms=[Room(module="top.r1", origin=(10.0, 20.0), size=(5.0, 5.0))],
        placements=[Placement(component="top.r1.u1", at=(3.0, 4.0), rotation=90.0)],
    )
    assert apply_placements(pcb, plan) == ["top.r1.u1"]
    fp = _fp(pcb, "top.r1.u1")
    assert (fp.at.x, fp.at.y) == pytest.approx((13.0, 24.0))  # origin + at
    assert (fp.at.r or 0) % 360 == pytest.approx(90.0)


# ===========================================================================
# PA2 — absolute placement lands verbatim (room ignored even if present).
# ===========================================================================
def test_absolute_placement_lands_verbatim():
    pcb = _board(_FPS).kicad_pcb
    plan = LayoutPlan(
        rooms=[Room(module="top.r1", origin=(10.0, 20.0), size=(5.0, 5.0))],
        placements=[Placement(component="top.r1.u1", at=(50.0, 60.0), absolute=True)],
    )
    apply_placements(pcb, plan)
    fp = _fp(pcb, "top.r1.u1")
    assert (fp.at.x, fp.at.y) == pytest.approx((50.0, 60.0))  # NOT 60,80


# ===========================================================================
# PA3 — side="B" flips the footprint onto the back copper (more than a pose).
# ===========================================================================
def test_back_side_placement_flips_layer():
    pcb = _board(_FPS).kicad_pcb
    assert _fp(pcb, "top.r1.u1").layer == "F.Cu"  # control: starts on front
    plan = LayoutPlan(
        placements=[Placement(component="top.r1.u1", at=(1.0, 2.0), absolute=True,
                              side="B")],
    )
    apply_placements(pcb, plan)
    assert _fp(pcb, "top.r1.u1").layer == "B.Cu"


# ===========================================================================
# PA4 — a placement naming no managed footprint is loud (loud-or-nothing).
# ===========================================================================
def test_placement_for_unknown_address_is_loud():
    pcb = _board(_FPS).kicad_pcb
    plan = LayoutPlan(
        placements=[Placement(component="top.ghost", at=(0.0, 0.0), absolute=True)],
    )
    with pytest.raises(LayoutPlanError) as ei:
        apply_placements(pcb, plan)
    assert "top.ghost" in str(ei.value)


# ===========================================================================
# PA5 — a room-relative placement with no enclosing room origin is loud
# (no silent (0,0) base — same discipline as resolve_placement).
# ===========================================================================
def test_room_relative_without_room_is_loud():
    pcb = _board(_FPS).kicad_pcb
    plan = LayoutPlan(placements=[Placement(component="top.r1.u1", at=(3.0, 4.0))])
    with pytest.raises(LayoutPlanError):
        apply_placements(pcb, plan)


# ===========================================================================
# PA6 — no placements is a noop, and a footprint with no placement is untouched.
# ===========================================================================
def test_no_placements_is_a_noop_and_unplaced_untouched():
    pcb = _board(
        [("top.r1.u1", "top.r1", (7.0, 8.0)), ("top.r1.u2", "top.r1", (1.0, 2.0))]
    ).kicad_pcb
    # one placement, the other footprint must keep its pose
    plan = LayoutPlan(
        rooms=[Room(module="top.r1", origin=(0.0, 0.0), size=(5.0, 5.0))],
        placements=[Placement(component="top.r1.u1", at=(3.0, 4.0))],
    )
    apply_placements(pcb, plan)
    assert (_fp(pcb, "top.r1.u1").at.x, _fp(pcb, "top.r1.u1").at.y) == pytest.approx(
        (3.0, 4.0)
    )
    assert (_fp(pcb, "top.r1.u2").at.x, _fp(pcb, "top.r1.u2").at.y) == pytest.approx(
        (1.0, 2.0)
    )  # untouched

    # and a plan with no placements at all returns [] (pure noop)
    pcb2 = _board(_FPS).kicad_pcb
    assert apply_placements(pcb2, LayoutPlan()) == []


# ===========================================================================
# PA-clean — a text placement INVALIDATES the placed room's intra-room copper:
# pulled/manual tracks are anchored to the OLD poses, so after the move they can
# only dangle or short (the layout_reuse relic-track bug: off-board copper
# islands that also made every chain net unroutable). Inter-room copper and
# other rooms' copper survive (the _clean_room semantics, pinned here from the
# placement side).
# ===========================================================================
def _board_with_copper() -> kicad.pcb.PcbFile:
    text = """(kicad_pcb
\t(version 20241229)
\t(generator "test_placement_apply")
\t(generator_version "10.0")
\t(general (thickness 1.6))
\t(layers (0 "F.Cu" signal) (31 "B.Cu" signal))
\t(net 0 "")
\t(net 1 "A_INTRA")
\t(net 2 "AB_INTER")
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000000a")
\t\t(at 1 1 0)
\t\t(sheetname "top.a")
\t\t(property "Reference" "r1" (at 0 0) (layer "F.SilkS") (effects (font (size 1 1))))
\t\t(property "atopile_address" "top.a.r1" (at 0 0) (layer "F.Fab") (effects (font (size 1 1))))
\t\t(pad "1" smd rect (at 0.5 0 0) (size 1 1) (layers "F.Cu") (net 1 "A_INTRA") (uuid "00000000-0000-0000-0000-00000000001a"))
\t)
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000000b")
\t\t(at 3 1 0)
\t\t(sheetname "top.a")
\t\t(property "Reference" "r2" (at 0 0) (layer "F.SilkS") (effects (font (size 1 1))))
\t\t(property "atopile_address" "top.a.r2" (at 0 0) (layer "F.Fab") (effects (font (size 1 1))))
\t\t(pad "1" smd rect (at -0.5 0 0) (size 1 1) (layers "F.Cu") (net 1 "A_INTRA") (uuid "00000000-0000-0000-0000-00000000001b"))
\t\t(pad "2" smd rect (at 0.5 0 0) (size 1 1) (layers "F.Cu") (net 2 "AB_INTER") (uuid "00000000-0000-0000-0000-00000000001c"))
\t)
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000000c")
\t\t(at 6 1 0)
\t\t(sheetname "top.b")
\t\t(property "Reference" "r3" (at 0 0) (layer "F.SilkS") (effects (font (size 1 1))))
\t\t(property "atopile_address" "top.b.r3" (at 0 0) (layer "F.Fab") (effects (font (size 1 1))))
\t\t(pad "1" smd rect (at -0.5 0 0) (size 1 1) (layers "F.Cu") (net 2 "AB_INTER") (uuid "00000000-0000-0000-0000-00000000001d"))
\t)
\t(segment (start 1.5 1) (end 2.5 1) (width 0.2) (layer "F.Cu") (net 1) (uuid "00000000-0000-0000-0000-0000000000e1"))
\t(segment (start 3.5 1) (end 5.5 1) (width 0.2) (layer "F.Cu") (net 2) (uuid "00000000-0000-0000-0000-0000000000e2"))
)
"""
    return kicad.loads(kicad.pcb.PcbFile, text)


def test_placement_invalidates_placed_rooms_intra_copper():
    pcb = _board_with_copper().kicad_pcb
    assert len(pcb.segments) == 2
    plan = LayoutPlan(
        rooms=[Room(module="top.a", origin=(10.0, 10.0), size=(8.0, 4.0))],
        placements=[Placement(component="top.a.r1", at=(1.0, 1.0))],
    )
    moved = apply_placements(pcb, plan)
    assert moved == ["top.a.r1"]
    # the intra-room net's copper is gone; the inter-room net's copper survives
    remaining_nets = {s.net for s in pcb.segments}
    intra = next(n.number for n in pcb.nets if n.name == "A_INTRA")
    inter = next(n.number for n in pcb.nets if n.name == "AB_INTER")
    assert intra not in remaining_nets
    assert inter in remaining_nets


def test_placement_without_room_copper_is_noop_clean():
    pcb = _board(_FPS).kicad_pcb  # no segments at all
    plan = LayoutPlan(
        rooms=[Room(module="top.r1", origin=(5.0, 5.0), size=(4.0, 4.0))],
        placements=[Placement(component="top.r1.u1", at=(1.0, 1.0))],
    )
    apply_placements(pcb, plan)  # must not raise
    assert len(pcb.segments) == 0
