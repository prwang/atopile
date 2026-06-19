# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
room_ops — forced-via insertion and room (sub-layout) copy on top of layout_ir.

This is the §C layer of the text-first layout workflow (BACKLOG §C). It consumes
*only* the layout_ir (BACKLOG §B) — never raw net numbers or uuids — and produces
geometry that §E (the router) ingests. Two operations:

  * `insert_forced_via` — pin a net to cross between two copper layers at a chosen
    board coordinate, splitting it into pad→via (in_layer) and via→pad (out_layer).
    The via and both segments carry the *real* net (never net-0: KiCad garbage-
    collects net-0 copper, BACKLOG 事实 9). Routing *guides* are confined to the
    User corridor layers (User.1 = guide corridor, User.2 = keepout); they are
    never copper.

  * `copy_room_layout` — replicate a sub-layout (a "room": footprints + intra-room
    routes) onto a prefixed instance on the target board, remapping ato addresses
    by prefix and nets by pad correspondence. The net remap is identical to the
    live, e2e-tested LayoutSync._generate_net_map (the B→C consumer-oracle): see
    `room_net_map`.

The address/net maps are pure functions of the IR (`address_prefix_map`,
`room_net_map`), so a caller — and the §E router-feeding step — can compute and
inspect exactly what will move before any board mutation happens.
"""

from dataclasses import dataclass, field
from typing import Any

from faebryk.exporters.pcb.kicad.transformer import PCB_Transformer
from faebryk.libs.geometry.basic import Geometry
from faebryk.libs.kicad.fileformats import kicad

PCB = kicad.pcb.KicadPcb

# Router corridor layers (BACKLOG 事实 9): guide geometry only.
GUIDE_LAYER = "User.1"
GUIDE_LAYERS = {"User.1", "User.2"}


class RoomOpsError(ValueError):
    """An operation that cannot be performed faithfully against the IR/board."""


@dataclass
class ForcedVia:
    """The geometry a forced via materializes: the via, the two copper segments
    that split the net across it, and the User-layer routing guides."""

    via: Any  # kicad.pcb.Via
    segments: list  # [kicad.pcb.Segment, ...] one per spanned copper layer
    guides: list  # [kicad.pcb.Line, ...] on User.* only


@dataclass
class RoomCopy:
    """The maps used and the objects created by a room copy, so a caller (and §E)
    can see exactly what moved."""

    addr_map: dict[str, str]
    net_map: dict[str, str]
    new_objects: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# pure IR functions (no board mutation)
# ---------------------------------------------------------------------------


def pad_board_xy(ir: dict[str, Any], addr: str, pad: str) -> list[float]:
    """Absolute board position [x, y, rot] of a pad, via the SAME pose
    composition the rest of atopile uses (Geometry.abs_pos) — a footprint's
    pad lands where KiCad would put it, not by a re-invented convention.

    Geometry.abs_pos returns numpy scalars; we clean to plain floats so the
    result is JSON-stable (BACKLOG §C C-impl memo)."""
    try:
        comp = ir["components"][addr]
    except KeyError:
        raise RoomOpsError(f"address {addr!r} not in IR components")
    try:
        p = comp["pads"][pad]
    except KeyError:
        raise RoomOpsError(f"pad {pad!r} not on component {addr!r}")
    x, y, rot, _layer = Geometry.abs_pos(tuple(comp["at"]), tuple(p["at"]))
    return [float(x), float(y), float(rot)]


def address_prefix_map(
    src_ir: dict[str, Any],
    tgt_ir: dict[str, Any],
    *,
    source_prefix: str,
    target_prefix: str,
) -> dict[str, str]:
    """The I6 prefix bijection: every source component `<source_prefix>.<rest>`
    maps to `<target_prefix>.<rest>` that exists on the target. Total over the
    matching source components, collision-free onto the target."""
    src = src_ir["components"]
    tgt = set(tgt_ir["components"])
    out: dict[str, str] = {}
    for addr in src:
        if addr == source_prefix:
            rest = ""
        elif addr.startswith(source_prefix + "."):
            rest = addr[len(source_prefix) :]  # keeps leading "."
        else:
            continue
        mapped = target_prefix + rest
        if mapped in tgt:
            out[addr] = mapped
    return out


def room_net_map(
    src_ir: dict[str, Any], tgt_ir: dict[str, Any], addr_map: dict[str, str]
) -> dict[str, str]:
    """Map source net names → target net names by pad correspondence.

    This MUST equal the live LayoutSync._generate_net_map (the B→C consumer-
    oracle): for each address pair, pads are matched by name and each source
    pad's net votes for the colocated target pad's net; the most-voted target
    wins. Empty ("" / no-net) pads do not vote — mirroring the live truthiness
    check on `pad.net.name`."""
    net_map: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    src_c = src_ir["components"]
    tgt_c = tgt_ir["components"]
    for s_addr, t_addr in addr_map.items():
        if s_addr not in src_c or t_addr not in tgt_c:
            continue
        s_pads = src_c[s_addr]["pads"]
        t_pads = tgt_c[t_addr]["pads"]
        for pname, spad in s_pads.items():
            tpad = t_pads.get(pname)
            if tpad is None:
                continue
            s_net = spad["net"]
            t_net = tpad["net"]
            if not s_net or not t_net:  # "" never votes (live truthiness)
                continue
            counts.setdefault(s_net, {})
            counts[s_net][t_net] = counts[s_net].get(t_net, 0) + 1
            if s_net not in net_map or counts[s_net][t_net] > max(
                counts[s_net].values()
            ):
                net_map[s_net] = t_net
    return net_map


# ---------------------------------------------------------------------------
# board mutation
# ---------------------------------------------------------------------------


def _net_number(pcb: PCB, name: str) -> int:
    """Resolve a net name to its file-local number on `pcb`.

    Iterating `pcb.nets` matches the table _NetTable resolves against (and the
    v10 synthesized table), so the number we stamp round-trips back to `name`."""
    for net in pcb.nets:
        if net.name == name:
            return net.number
    raise RoomOpsError(
        f"net {name!r} not found on board; available: "
        f"{sorted(n.name for n in pcb.nets)}"
    )


def _guide_line(start: tuple[float, float], end: tuple[float, float]):
    return kicad.pcb.Line(
        start=kicad.pcb.Xy(x=start[0], y=start[1]),
        end=kicad.pcb.Xy(x=end[0], y=end[1]),
        solder_mask_margin=None,
        stroke=kicad.pcb.Stroke(width=0.05, type="solid"),
        fill=None,
        layer=GUIDE_LAYER,
        layers=[GUIDE_LAYER],
        locked=False,
        uuid=kicad.gen_uuid(),
    )


def insert_forced_via(
    pcb: PCB,
    ir: dict[str, Any],
    *,
    net: str,
    pads: tuple[tuple[str, str], tuple[str, str]],
    via_at: tuple[float, float],
    in_layer: str,
    out_layer: str,
    via_size: float = 0.6,
    via_drill: float = 0.3,
    track_width: float = 0.2,
    grid_step: float = 0.1,
) -> ForcedVia:
    """Insert a forced via on `net` at `via_at`, splitting it into
    pad→via (`in_layer`) and via→pad (`out_layer`). The via and both segments
    carry the real net number (never 0). Routing guides go on User.1 only.

    Returns the created geometry; everything is also inserted into `pcb`.
    """
    if len(pads) != 2:
        raise RoomOpsError("a forced via splits a net between exactly two pads")
    number = _net_number(pcb, net)

    # snap the via to the routing grid so the corridor lands on grid centres
    def _snap(v: float) -> float:
        return round(v / grid_step) * grid_step

    vx, vy = _snap(via_at[0]), _snap(via_at[1])

    p0 = pad_board_xy(ir, pads[0][0], pads[0][1])
    p1 = pad_board_xy(ir, pads[1][0], pads[1][1])

    seg_in = kicad.pcb.Segment(
        start=kicad.pcb.Xy(x=p0[0], y=p0[1]),
        end=kicad.pcb.Xy(x=vx, y=vy),
        width=track_width,
        layer=in_layer,
        net=number,
        uuid=kicad.gen_uuid(),
    )
    seg_out = kicad.pcb.Segment(
        start=kicad.pcb.Xy(x=vx, y=vy),
        end=kicad.pcb.Xy(x=p1[0], y=p1[1]),
        width=track_width,
        layer=out_layer,
        net=number,
        uuid=kicad.gen_uuid(),
    )
    via = kicad.pcb.Via(
        at=kicad.pcb.Xy(x=vx, y=vy),
        size=via_size,
        drill=via_drill,
        layers=[in_layer, out_layer],
        net=number,
        remove_unused_layers=False,
        keep_end_layers=False,
        zone_layer_connections=[],
        padstack=None,
        teardrops=None,
        tenting=None,
        free=None,
        locked=None,
        uuid=kicad.gen_uuid(),
    )
    guides = [
        _guide_line((p0[0], p0[1]), (vx, vy)),
        _guide_line((vx, vy), (p1[0], p1[1])),
    ]

    kicad.insert(pcb, "vias", pcb.vias, via)
    kicad.insert(pcb, "segments", pcb.segments, seg_in)
    kicad.insert(pcb, "segments", pcb.segments, seg_out)
    for g in guides:
        kicad.insert(pcb, "gr_lines", pcb.gr_lines, g)

    return ForcedVia(via=via, segments=[seg_in, seg_out], guides=guides)


def copy_room_layout(
    target_pcb: PCB,
    source_pcb: PCB,
    *,
    source_prefix: str,
    target_prefix: str,
    offset: kicad.pcb.Xy,
) -> RoomCopy:
    """Copy a room's intra-room routes from `source_pcb` onto `target_pcb`,
    remapping ato addresses by prefix and nets by pad correspondence.

    The address and net maps are exactly `address_prefix_map` / `room_net_map`
    (== the live LayoutSync net map). ONLY the named room's intra-room copper is
    copied: a track is copied iff its source net maps (i.e. has pad
    correspondence inside this room). A sibling room's nets and inter-room nets do
    not map, so their copper is left behind — the copy never crosses a room
    boundary (BACKLOG §C3, pinned by C3.10). Copied tracks/vias/zones get fresh
    uuids, the offset applied, and their net rebound to the target's number.
    Returns the maps and the new objects.
    """
    from itertools import chain

    from faebryk.libs.kicad.layout_ir import layout_ir

    src_ir = layout_ir(source_pcb)
    tgt_ir = layout_ir(target_pcb)
    addr_map = address_prefix_map(
        src_ir, tgt_ir, source_prefix=source_prefix, target_prefix=target_prefix
    )
    net_map = room_net_map(src_ir, tgt_ir, addr_map)

    new_objects: list = []
    for track in chain(
        source_pcb.segments, source_pcb.arcs, source_pcb.zones, source_pcb.vias
    ):
        src_net = next((n for n in source_pcb.nets if n.number == track.net), None)
        if src_net is None or src_net.name not in net_map:
            continue  # not this room's intra-room copper — do not cross the boundary
        tgt_name = net_map[src_net.name]
        new_track = kicad.copy(track)
        new_track.uuid = kicad.gen_uuid()
        new_track.net = _net_number(target_pcb, tgt_name)
        if isinstance(new_track, kicad.pcb.Zone):
            new_track.net_name = tgt_name
        PCB_Transformer.move_object(new_track, offset)
        container, container_name = PCB_Transformer.get_pcb_container(
            new_track, target_pcb
        )
        inserted = kicad.insert(target_pcb, container_name, container, new_track)
        new_objects.append(inserted)

    return RoomCopy(addr_map=addr_map, net_map=net_map, new_objects=new_objects)
