# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""Build-side consumer of `LayoutPlan.placements` (BACKLOG §D-Tier3).

`resolve_component_pose` (layout_plan.py) is the pure PRIORITY oracle — a TEXT
placement overrides the reuse pose, absent text falls through. This module is the
build-side half that actually moves the managed footprints: it overrides the
transformer's auto-grid spread (transformer.py:176 / :2013-2080) with the text
poses so `.ato` + `layout.yaml` are the self-contained authority for placement.

A placement names an ato ADDRESS; the managed footprint carries it as the
`atopile_address` property. A room-relative placement composes against the room
whose `module` is the LONGEST address prefix of the component. Side + rotation are
applied through the transformer's flip-aware `move_fp` (a side flip is more than a
layer swap). A placement naming no managed footprint is loud (loud-or-nothing).
"""

from faebryk.exporters.pcb.layout.layout_plan import (
    LayoutPlan,
    LayoutPlanError,
    Room,
    resolve_component_pose,
)
from faebryk.libs.kicad.fileformats import Property, kicad

_ADDR_PROP = "atopile_address"


def _footprints_by_address(pcb) -> dict[str, object]:
    out: dict[str, object] = {}
    for fp in pcb.footprints:
        addr = Property.try_get_property(fp.propertys, _ADDR_PROP)
        if addr is not None:
            out[addr] = fp
    return out


def _room_for(component: str, rooms: list[Room]) -> Room | None:
    """The room whose `module` is the longest address prefix of `component`
    (`top.r1` owns `top.r1.u1`). None ⇒ no enclosing room (absolute placements
    don't need one; room-relative ones then raise via resolve_placement)."""
    best: Room | None = None
    for room in rooms:
        m = room.module
        if component == m or component.startswith(m + "."):
            if best is None or len(m) > len(best.module):
                best = room
    return best


def apply_placements(pcb, plan: LayoutPlan) -> list[str]:
    """Move every managed footprint named by a text placement to its resolved pose.

    Text placements override the build-time auto-grid spread; rotation + side are
    applied via the transformer's flip-aware `move_fp`. Footprints with no text
    placement are left untouched (their grid/reuse pose stands). A placement naming
    an address with no managed footprint is loud. Returns the moved addresses."""
    if not plan.placements:
        return []
    # imported lazily — the transformer is a heavy module and importing it at top
    # level would couple this small consumer to the whole transform stack.
    from faebryk.exporters.pcb.kicad.transformer import PCB_Transformer

    fps = _footprints_by_address(pcb)
    moved: list[str] = []
    moved_fps: list = []
    for placement in plan.placements:
        fp = fps.get(placement.component)
        if fp is None:
            raise LayoutPlanError(
                f"placement {placement.component!r}: no managed footprint with that "
                "atopile_address on the board (typo, or it is not atopile-managed)"
            )
        room = _room_for(placement.component, plan.rooms)
        x, y = resolve_component_pose(placement, (fp.at.x, fp.at.y), room)
        layer = "B.Cu" if placement.side == "B" else "F.Cu"
        PCB_Transformer.move_fp(
            fp, kicad.pcb.Xyr(x=x, y=y, r=placement.rotation), layer
        )
        moved.append(placement.component)
        moved_fps.append(fp)

    # A moved footprint orphans its room's pulled intra-room copper: those tracks
    # are anchored to the OLD (reuse/grid) poses, so after the move they can only
    # dangle off the pads or short what they now cross (empirically: the
    # layout_reuse relic tracks — off-board copper islands that also made every
    # chain net unroutable, since the router must reach ALL of a net's copper).
    # Text placement is the pose AUTHORITY, so a placed room's copper is
    # derived-only: invalidate it and let the route stages re-lay it.
    rooms_to_clean = sorted(
        {fp.sheetname for fp in moved_fps if getattr(fp, "sheetname", None)}
    )
    if rooms_to_clean:
        from faebryk.exporters.pcb.layout.layout_sync import LayoutSync

        sync = LayoutSync(pcb)
        for room_name in rooms_to_clean:
            sync.clean_room_copper(room_name)
    return moved
