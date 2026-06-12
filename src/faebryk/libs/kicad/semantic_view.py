# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Name-based semantic view of a .kicad_pcb.

Reduces a parsed board to the facts the layout workflow relies on —
footprints/pads, copper geometry, zones, groups — with every net reference
resolved to its *name*. Net numbers are treated as file-local handles and never
appear in the view, which makes the view invariant across the v9 (numbered) and
v10 (name-only) dialects and across any renumbering of a v9 file.

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

SEMANTIC_VIEW_VERSION = 1


class NetResolutionError(ValueError):
    pass


def _xy(p) -> list[float]:
    return [p.x, p.y]


def _xyr(p) -> list[float]:
    return [p.x, p.y, p.r if p.r is not None else 0.0]


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
                "at": _xy(v.at),
                "size": v.size,
                "drill": v.drill,
                "layers": sorted(v.layers),
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
                "layers": sorted([z.layer] if z.layer is not None else z.layers),
                "polygon": [_xy(p) for p in z.polygon.pts.xys],
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
                "placement": (
                    {
                        "enabled": z.placement.enabled,
                        "source_type": z.placement.source_type,
                        "source": z.placement.source,
                    }
                    if z.placement is not None
                    else None
                ),
            }
            for z in pcb.zones
        ),
        key=lambda z: (
            z["name"] or "",
            z["net"],
            z["layers"],
            z["polygon"][:1],
        ),
    )

    groups = sorted(
        ({"name": g.name or "", "members": sorted(g.members)} for g in pcb.groups),
        key=lambda g: (g["name"], g["members"]),
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
    }


def semantic_view_json(pcb: kicad.pcb.KicadPcb) -> str:
    """Canonical serialization — byte-stable for snapshot comparison."""
    return json.dumps(semantic_view(pcb), sort_keys=True, indent=1) + "\n"
