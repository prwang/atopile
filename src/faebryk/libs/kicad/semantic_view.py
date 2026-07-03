# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Name-based semantic view of a .kicad_pcb.

Reduces a parsed board to the facts the layout workflow relies on —
footprints/pads (incl. pad teardrops/padstacks), copper geometry, vias (incl.
teardrops/padstack/drill+surface treatments), zones (incl. the teardrop-zone
attr), generated tuning patterns, groups — with every net reference resolved to
its *name*. Net numbers are treated as file-local handles and never appear in
the view, which makes the view invariant across the v9 (numbered) and v10
(name-only) dialects and across any renumbering of a v9 file.

Group and generated members are expressed by member uuid — a documented
residual (a complete oracle would resolve members to addresses; see CLAUDE.md
"UUID is opaque").

Resolution is strict on purpose: a dangling net reference, a number/name
disagreement between a reference and the net table, or two nets sharing a name
raises instead of guessing. Silent misbinding is exactly the failure mode this
module exists to catch.

Used as the migration safety net (BACKLOG P0.1 T3/T4) and as the extraction
base for the layout_ir.json export (BACKLOG B1).
"""

import json
from typing import Any

from faebryk.libs.kicad.fileformats import Property, kicad

SEMANTIC_VIEW_VERSION = 3


class NetResolutionError(ValueError):
    pass


def _xy(p) -> list[float]:
    return [p.x, p.y]


def _xyr(p) -> list[float]:
    return [p.x, p.y, p.r if p.r is not None else 0.0]


def _teardrops(t) -> dict[str, Any] | None:
    """All 9 KiCad v10 teardrop parameters; None when the block is absent."""
    if t is None:
        return None
    return {
        "best_length_ratio": t.best_length_ratio,
        "max_length": t.max_length,
        "best_width_ratio": t.best_width_ratio,
        "max_width": t.max_width,
        "curved_edges": t.curved_edges,
        "filter_ratio": t.filter_ratio,
        "enabled": t.enabled,
        "allow_two_segments": t.allow_two_segments,
        "prefer_zone_connections": t.prefer_zone_connections,
    }


def _tenting(t) -> dict[str, Any] | None:
    # front/back are tri-state (yes|no|unspecified); None here = unspecified ≠ no
    if t is None:
        return None
    return {"front": t.front, "back": t.back, "none": t.none}


def _drill_span(d) -> dict[str, Any] | None:
    # backdrill / tertiary_drill: layers is a from→to span, order is semantic
    if d is None:
        return None
    return {"size": d.size, "layers": list(d.layers)}


def _post_machining(p) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"mode": p.mode, "size": p.size, "depth": p.depth, "angle": p.angle}


def _via_padstack(ps) -> dict[str, Any] | None:
    """Via padstack: mode + per-layer scalar diameter (the v10 file grammar)."""
    if ps is None:
        return None
    return {
        "mode": ps.mode,
        "layers": sorted(
            ({"name": layer.name, "size": layer.size} for layer in ps.layers),
            key=lambda entry: entry["name"],
        ),
    }


def _pad_padstack(ps) -> dict[str, Any] | None:
    """Pad padstack: mode + the per-layer shape-defining subset (full structural
    fidelity is carried by the corpus no-data-loss gate, not the view)."""
    if ps is None:
        return None
    return {
        "mode": ps.mode,
        "layers": sorted(
            (
                {
                    "name": layer.name,
                    "shape": layer.shape,
                    "size": (
                        [layer.size.w, layer.size.h]
                        if layer.size is not None
                        else None
                    ),
                    "offset": _xy(layer.offset) if layer.offset is not None else None,
                }
                for layer in ps.layers
            ),
            key=lambda entry: entry["name"],
        ),
    }


def _via_treatments(v) -> dict[str, Any] | None:
    """Drill/surface treatment keys a GUI padstack edit can set on a via.
    None when the via carries none of them (the common case)."""
    treatments = {
        "start_end_only": v.start_end_only,
        "tenting": _tenting(v.tenting),
        "capping": v.capping,
        "covering": _tenting(v.covering),
        "plugging": _tenting(v.plugging),
        "filling": v.filling,
        "backdrill": _drill_span(v.backdrill),
        "tertiary_drill": _drill_span(v.tertiary_drill),
        "front_post_machining": _post_machining(v.front_post_machining),
        "back_post_machining": _post_machining(v.back_post_machining),
    }
    if all(value is None for value in treatments.values()):
        return None
    return treatments


def _generated_xy(g) -> list[float] | None:
    return _xy(g.xy) if g is not None else None


def _zone_placement(p) -> dict[str, Any] | None:
    """Rule-area room binding. The file grammar fuses source type + value into
    a single token — (sheetname "X") | (component_class "X") — so these two
    fields ARE the binding; source_type/source model KiCad's in-memory/protobuf
    shape and are by design always None on file-parsed boards (writing them
    SIGSEGVs kicad-cli), so projecting them would carry zero information.
    An absent source token parses as an empty SHEETNAME source (KiCad's
    parser default; the 10.0.3 writer always re-emits it as (sheetname "")),
    so it projects identically to sheetname ""."""
    if p is None:
        return None
    sheetname = p.sheetname
    if sheetname is None and p.component_class is None:
        sheetname = ""
    return {
        "enabled": p.enabled,
        "sheetname": sheetname,
        "component_class": p.component_class,
    }


def _pts_chain(pts) -> list[Any]:
    """A (pts ...) chain in file order: [x, y] per xy entry and
    {"arc": [start, mid, end]} per interleaved arc entry.

    The interleaving IS the outline geometry (KiCad rebuilds the
    SHAPE_LINE_CHAIN in file order), so arcs are projected at their chain
    position — arc deletion, coordinate mutation, and xy/arc reorder must all
    move the view. The merge mirrors the writer (pcb.zig Pts.writeBodyStreamed):
    an arc goes before the (xys_before+1)-th xy; arcs without a recorded
    position sit after the last xy. A pure-xy chain projects exactly as before
    (a flat list of [x, y]) — the shape upgrade is arc-only."""
    xys = [_xy(p) for p in pts.xys]
    arcs = list(pts.arcs)
    if not arcs:
        return xys

    def _arc_entry(a) -> dict[str, Any]:
        return {"arc": [_xy(a.start), _xy(a.mid), _xy(a.end)]}

    entries: list[Any] = []
    ai = 0
    for i, xy in enumerate(xys):
        while (
            ai < len(arcs)
            and arcs[ai].xys_before is not None
            and arcs[ai].xys_before <= i
        ):
            entries.append(_arc_entry(arcs[ai]))
            ai += 1
        entries.append(xy)
    entries.extend(_arc_entry(a) for a in arcs[ai:])
    return entries


def _generated_pts(g) -> list[Any] | None:
    return _pts_chain(g.pts) if g is not None else None


class _NetTable:
    def __init__(self, pcb: kicad.pcb.KicadPcb):
        self.by_number: dict[int, str] = {}
        names_seen: dict[str, int] = {}
        for net in pcb.nets:
            name = net.name if net.name is not None else ""
            if net.number in self.by_number:
                raise NetResolutionError(
                    f"duplicate net number {net.number} in net table"
                )
            if name != "" and name in names_seen:
                raise NetResolutionError(
                    f"net name {name!r} declared for both net "
                    f"{names_seen[name]} and net {net.number}"
                )
            self.by_number[net.number] = name
            names_seen[name] = net.number

    def resolve(self, number: int, inline_name: str | None, ctx: str) -> str:
        if number == 0 and number not in self.by_number:
            # net 0 ("no net") is implicit; files need not declare it
            return ""
        if number not in self.by_number:
            raise NetResolutionError(f"dangling net reference {number} in {ctx}")
        name = self.by_number[number]
        if inline_name is not None and inline_name != name:
            raise NetResolutionError(
                f"net reference in {ctx} carries name {inline_name!r} but the "
                f"net table maps number {number} to {name!r}"
            )
        return name


def semantic_view(pcb: kicad.pcb.KicadPcb) -> dict[str, Any]:
    nets = _NetTable(pcb)

    footprints = []
    for fp in pcb.footprints:
        ref = Property.try_get_property(fp.propertys, "Reference") or ""
        pads = sorted(
            (
                {
                    "name": pad.name,
                    "net": nets.resolve(
                        pad.net.number if pad.net is not None else 0,
                        pad.net.name if pad.net is not None else None,
                        f"pad {ref}.{pad.name}",
                    ),
                    "at": _xyr(pad.at),
                    "layers": sorted(pad.layers),
                    "teardrops": _teardrops(pad.teardrops),
                    "padstack": _pad_padstack(pad.padstack),
                }
                for pad in fp.pads
            ),
            key=lambda p: (p["name"], p["at"]),
        )
        footprints.append(
            {
                "name": fp.name,
                "reference": ref,
                "at": _xyr(fp.at),
                "layer": fp.layer,
                # static (component_classes (class ...)) membership — GUI- or
                # D5-assigned; KiCad treats it as an unordered set, so sort
                "component_classes": (
                    sorted(c.name for c in fp.component_classes.classes)
                    if fp.component_classes is not None
                    else []
                ),
                "pads": pads,
            }
        )
    footprints.sort(key=lambda f: (f["reference"], f["name"], f["at"]))

    segments = sorted(
        (
            {
                "net": nets.resolve(s.net, None, "segment"),
                "start": _xy(s.start),
                "end": _xy(s.end),
                "width": s.width,
                "layer": s.layer,
            }
            for s in pcb.segments
        ),
        key=lambda s: (s["net"], s["layer"] or "", s["start"], s["end"]),
    )

    arcs = sorted(
        (
            {
                "net": nets.resolve(a.net, None, "arc"),
                "start": _xy(a.start),
                "mid": _xy(a.mid),
                "end": _xy(a.end),
                "width": a.width,
                "layer": a.layer,
            }
            for a in pcb.arcs
        ),
        key=lambda a: (a["net"], a["layer"] or "", a["start"], a["end"]),
    )

    vias = sorted(
        (
            {
                "net": nets.resolve(v.net, None, "via"),
                "type": v.type,
                "at": _xy(v.at),
                "size": v.size,
                "drill": v.drill,
                "layers": sorted(v.layers),
                "padstack": _via_padstack(v.padstack),
                "teardrops": _teardrops(v.teardrops),
                "treatments": _via_treatments(v),
            }
            for v in pcb.vias
        ),
        key=lambda v: (v["net"], v["at"]),
    )

    zones = sorted(
        (
            {
                "net": nets.resolve(
                    z.net,
                    z.net_name,
                    f"zone {z.name or z.uuid or '?'}",
                ),
                "name": z.name,
                "teardrop_type": (
                    z.attr.teardrop.type
                    if z.attr is not None and z.attr.teardrop is not None
                    else None
                ),
                "layers": sorted([z.layer] if z.layer is not None else z.layers),
                "polygon": _pts_chain(z.polygon.pts),
                "keepout": (
                    {
                        "tracks": z.keepout.tracks,
                        "vias": z.keepout.vias,
                        "pads": z.keepout.pads,
                        "copperpour": z.keepout.copperpour,
                        "footprints": z.keepout.footprints,
                    }
                    if z.keepout is not None
                    else None
                ),
                "placement": _zone_placement(z.placement),
            }
            for z in pcb.zones
        ),
        key=lambda z: (
            z["name"] or "",
            z["net"],
            z["layers"],
            # json: a polygon chain may start with an [x, y] or an
            # {"arc": ...} entry — raw comparison across those would TypeError
            json.dumps(z["polygon"][:1]),
        ),
    )

    groups = sorted(
        ({"name": g.name or "", "members": sorted(g.members)} for g in pcb.groups),
        key=lambda g: (g["name"], g["members"]),
    )

    generateds = sorted(
        (
            {
                "type": g.type,
                "name": g.name,
                "layer": g.layer,
                "locked": g.locked,
                "base_line": _generated_pts(g.base_line),
                "base_line_coupled": _generated_pts(g.base_line_coupled),
                "corner_radius_percent": g.corner_radius_percent,
                "end": _generated_xy(g.end),
                "initial_side": g.initial_side,
                "is_time_domain": g.is_time_domain,
                "last_diff_pair_gap": g.last_diff_pair_gap,
                "last_netname": g.last_netname,
                "last_status": g.last_status,
                "last_track_width": g.last_track_width,
                "last_tuning_length": g.last_tuning_length,
                "max_amplitude": g.max_amplitude,
                "min_amplitude": g.min_amplitude,
                "min_spacing": g.min_spacing,
                "origin": _generated_xy(g.origin),
                "override_custom_rules": g.override_custom_rules,
                "rounded": g.rounded,
                "single_sided": g.single_sided,
                "target_delay": g.target_delay,
                "target_delay_max": g.target_delay_max,
                "target_delay_min": g.target_delay_min,
                "target_length": g.target_length,
                "target_length_max": g.target_length_max,
                "target_length_min": g.target_length_min,
                "target_skew": g.target_skew,
                "target_skew_max": g.target_skew_max,
                "target_skew_min": g.target_skew_min,
                "tuning_mode": g.tuning_mode,
                # members are uuids — same documented residual as groups
                "members": sorted(g.members),
            }
            for g in pcb.generateds
        ),
        key=lambda g: (g["type"], g["name"], g["layer"], g["members"]),
    )

    declared = sorted(n for n in nets.by_number.values() if n != "")

    return {
        "semantic_view_version": SEMANTIC_VIEW_VERSION,
        "nets": declared,
        "footprints": footprints,
        "segments": segments,
        "arcs": arcs,
        "vias": vias,
        "zones": zones,
        "groups": groups,
        "generateds": generateds,
    }


def semantic_view_json(pcb: kicad.pcb.KicadPcb) -> str:
    """Canonical serialization — byte-stable for snapshot comparison."""
    return json.dumps(semantic_view(pcb), sort_keys=True, indent=1) + "\n"
