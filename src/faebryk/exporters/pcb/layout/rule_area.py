# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
rule_area — turn a resolved LayoutPlan into KiCad placement rule areas (§D, D3).

For each room in the plan this emits one placement rule area onto the board: a
keepout zone whose `placement.sheetname` is the room's ato address — the §C3
source channel KiCad preserves verbatim (no `group`, no custom S-expression
token; `ZonePlacement.sheetname` is a native field). C3 has already stamped that
same sheetname onto the room's footprints, so the rule area and its members agree.

Room boundary (`_room_boundary`) — three geometry forms:
  * explicit `polygon` (D-Tier3; ≥ 3 pts, simple) ⇒ that polygon verbatim;
  * explicit `origin`/`size` (D2) ⇒ the axis-aligned rectangle
    [origin, origin + size];
  * both omitted ⇒ the rectangle is DERIVED as the bbox of the room's member pad
    board positions, taken from the IR via the SAME pose composition the rest of
    atopile uses (`room_ops.pad_board_xy` → `Geometry.abs_pos`), never a
    re-invented convention.
`room.rotation` rotates the boundary CCW about its first vertex; `room.layers`
restricts the zone to those layers (else every signal layer). Both were
parsed-but-silently-dropped before D-Tier3 and are now wired (no "declared yet
silently ignored": either it takes effect or the field is removed).

The generated zone is a keepout rule area with everything ALLOWED: it carries no
copper (so KiCad never GCs it as net-0 copper, BACKLOG fact 2) and restricts
nothing (so it adds zero DRC violations, D3.5). Its sole effect is the placement
grouping via sheetname.

Contract pinned by test/exporters/pcb/layout/test_rule_area_contract.py
(sheetname / bbox / derived-bbox) and test_placement_contract.py (TR1-TR4:
polygon, rotation, per-room layers).
"""

import math
from typing import Any

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, Room
from faebryk.exporters.pcb.layout.room_ops import RoomOpsError, pad_board_xy
from faebryk.libs.kicad.fileformats import kicad

PCB = kicad.pcb.KicadPcb
Zone = kicad.pcb.Zone

# every generated rule area is named with this prefix, so a rebuild can identify
# and replace exactly the ones it owns (re-emit idempotency, D4) without touching
# user-authored zones.
_MANAGED_PREFIX = "rule_area_"


class RuleAreaError(Exception):
    """A room that cannot be turned into a rule area (loud-or-nothing, S5a)."""


def _copper_layers(pcb: PCB) -> list[str]:
    return [layer.name for layer in pcb.layers if layer.type == "signal"]


def _room_bbox(room: Room, ir: dict[str, Any]) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) for a room. Explicit origin/size ⇒ the exact
    rectangle; otherwise the bbox of the room's member pad board positions.

    The caller (`generate_rule_areas`) has already verified room.module is a real
    board room, so the derived path can index ir["rooms"] directly."""
    if room.origin is not None and room.size is not None:
        ox, oy = room.origin
        w, h = room.size
        return (ox, oy, ox + w, oy + h)

    # derived bbox: bound the room's member pads (the §D "derived bounding box" case)
    member_addrs = ir["rooms"][room.module]["member_addrs"]
    xs: list[float] = []
    ys: list[float] = []
    for addr in member_addrs:
        comp = ir["components"][addr]
        for pad_name in comp["pads"]:
            try:
                x, y, _rot = pad_board_xy(ir, addr, pad_name)
            except RoomOpsError as e:  # pragma: no cover - IR self-consistent
                raise RuleAreaError(str(e)) from e
            xs.append(x)
            ys.append(y)
    if not xs:
        raise RuleAreaError(
            f"room {room.module!r} has member footprints but no pads — "
            "cannot derive a bbox (give an explicit origin/size)"
        )
    return (min(xs), min(ys), max(xs), max(ys))


def _rotate(
    points: list[tuple[float, float]], degrees: float
) -> list[tuple[float, float]]:
    """Rotate a boundary by `degrees` CCW about its FIRST vertex (the pinned
    convention; for an origin/size room the first vertex is the origin)."""
    if degrees == 0.0:
        return points
    th = math.radians(degrees)
    c, s = math.cos(th), math.sin(th)
    px, py = points[0]
    return [
        (px + (x - px) * c - (y - py) * s, py + (x - px) * s + (y - py) * c)
        for x, y in points
    ]


def _room_boundary(room: Room, ir: dict[str, Any]) -> list[tuple[float, float]]:
    """The room's boundary points (CCW-rotated by room.rotation about the first
    vertex): an explicit polygon, an explicit rectangle, or the derived bbox."""
    if room.polygon is not None:
        points = [tuple(p) for p in room.polygon]
    else:
        minx, miny, maxx, maxy = _room_bbox(room, ir)
        points = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    return _rotate(points, room.rotation)


def _make_placement_rule_area(
    sheetname: str, corners: list[tuple[float, float]], layers: list[str]
) -> Zone:
    return Zone(
        net=0,
        net_name="",
        layers=layers if len(layers) > 1 else [],
        layer=layers[0] if len(layers) == 1 else None,
        uuid=kicad.gen_uuid(),
        name=f"rule_area_{sheetname}",
        hatch=kicad.pcb.Hatch(mode=kicad.pcb.E_zone_hatch_mode.EDGE, pitch=0.5),
        connect_pads=kicad.pcb.ConnectPads(mode=None, clearance=0),
        min_thickness=0.25,
        filled_areas_thickness=False,
        # a placement grouping area: restrict nothing (zero DRC delta), carry no
        # copper (never GC'd as net-0 copper).
        keepout=kicad.pcb.ZoneKeepout(
            tracks=kicad.pcb.E_zone_keepout.ALLOWED,
            vias=kicad.pcb.E_zone_keepout.ALLOWED,
            pads=kicad.pcb.E_zone_keepout.ALLOWED,
            copperpour=kicad.pcb.E_zone_keepout.ALLOWED,
            footprints=kicad.pcb.E_zone_keepout.ALLOWED,
        ),
        # placement carries ONLY enabled + sheetname. Do NOT set
        # ZonePlacement.source_type / source: those schema fields have no valid
        # KiCad-10 grammar and emitting them SEGFAULTS kicad-cli's loader
        # (verified by isolation 2026-06-16; see CLAUDE.md fileformats hazard).
        # This is the canonical KiCad-authored enabled-placement form.
        placement=kicad.pcb.ZonePlacement(
            sheetname=sheetname,
            enabled=True,
        ),
        # fill is optional — KiCad synthesizes a default (fill ...) block on load
        # either way — but we emit the canonical one so a freshly-generated board
        # round-trips through `kicad-cli upgrade` with no spurious diff. It does
        # NOT enable a pour (no `(fill yes ...)`); the keepout already carries no
        # copper, so it is never GC'd as net-0 copper regardless.
        fill=kicad.pcb.ZoneFill(
            mode=None,
            thermal_gap=0.5,
            thermal_bridge_width=0.5,
            island_removal_mode=0,
        ),
        polygon=kicad.pcb.Polygon(
            pts=kicad.pcb.Pts(xys=[kicad.pcb.Xy(x=x, y=y) for x, y in corners])
        ),
    )


def generate_rule_areas(
    pcb: PCB, plan: LayoutPlan, ir: dict[str, Any]
) -> list[Zone]:
    """Emit one placement rule area per plan room onto `pcb`; return the inserted
    zones. Each room gets an INDEPENDENT zone sourced by its sheetname — no
    cross-room contamination (D3.4).

    Idempotent: any previously-generated rule area (name prefix `rule_area_`) is
    removed first, so re-emitting onto an already-stamped board does not duplicate
    or accrete zones (D4 re-emit). User-authored zones are untouched."""
    # S5a (loud, never silent): every plan room must name a REAL board room (= a footprint
    # sheetname in the IR). A rule area whose sheetname matches no footprint would
    # group NOTHING — a silent half-output — so reject it loudly, in BOTH geometry
    # modes (explicit origin/size must not be a loophole around this check).
    rooms_ir = ir.get("rooms", {})
    for room in plan.rooms:
        if room.module not in rooms_ir:
            raise RuleAreaError(
                f"room {room.module!r} matches no footprint sheetname on the board "
                f"(known rooms: {sorted(rooms_ir)}) — a typo or stale reference; a "
                "rule area for it would group nothing"
            )
    kicad.filter(
        pcb,
        "zones",
        pcb.zones,
        lambda z: not (z.name is not None and z.name.startswith(_MANAGED_PREFIX)),
    )
    default_layers = _copper_layers(pcb)
    inserted: list[Zone] = []
    for room in plan.rooms:
        # an explicit room.layers takes effect (no longer silently ignored, S5a);
        # otherwise the zone spans every signal layer.
        layers = list(room.layers) if room.layers else default_layers
        zone = _make_placement_rule_area(
            room.module, _room_boundary(room, ir), layers
        )
        inserted.append(kicad.insert(pcb, "zones", pcb.zones, zone))
    return inserted
