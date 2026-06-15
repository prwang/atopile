# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
layout_ir — the stable text↔geometry interface (BACKLOG §B, task B1b).

The IR is the *only* thing the text layer (layout.yaml / route plan /
diagnostics) is allowed to know about a .kicad_pcb. v10 net names drift and
uuids are random, so the text layer cannot reference either — only ato
addresses, which the IR translates. Its consumers are §C / §D / §F.

Shape (pinned by test_layout_ir_contract.py + the C3 migration contract,
test/exporters/pcb/layout/test_room_migration_contract.py):

    { "layout_ir_version": int,
      "components": { "<ato address>": {
            "ref": str, "footprint_uuid": str, "at": [x,y,r], "layer": str,
            "pads": { "<pad name>": {"uuid", "net", "at":[x,y,r], "layers":[]} } } },
      "nets":   { "<net name>": ["<addr>.<pad>", ...] },     # geometry net → pads
      "rooms":  { "<room name>": {"sheetname": str, "member_addrs": [addr]} } }

Rooms (BACKLOG §C3) are derived from each managed footprint's `sheetname` — the
KiCad-native source-correspondence channel that replaced atopile groups. A room
is the set of managed footprints sharing a sheetname; it carries NO object uuid
and NO provenance (the gen_uuid name-in-uuid hack is gone). Footprints with no
sheetname are simply not in any room. Track/via ownership is NOT in the IR: it is
the intra-room-net rule applied by layout_sync (a net all of whose pads are on
one room's footprints), so an inter-room net belongs to no room.

Net names are resolved through the same strict _NetTable as semantic_view, so a
pad's IR net is exactly the geometry-bound name (BACKLOG I3) — never
independently derived. Unrepresentable inputs are loud, never a silent half-IR
(I8): a duplicate atopile_address raises LayoutIRError, a dangling/ambiguous net
raises NetResolutionError (propagated from the net table).

The signal-address→net bridge (BACKLOG I4b, "bridge ②") is the one part that
needs the graph — net names are not recoverable as signal addresses from the pcb
alone — so it is added by `signal_nets(app)` and folded in by the build step,
where the graph is in hand.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.kicad.semantic_view import _NetTable

LAYOUT_IR_VERSION = 2

_SCHEMA_PATH = Path(__file__).with_name("layout_ir.schema.json")


class LayoutIRError(ValueError):
    """An input that cannot be faithfully represented (BACKLOG I8)."""


@lru_cache(maxsize=1)
def layout_ir_schema() -> dict[str, Any]:
    """The checked-in JSON Schema for the IR (BACKLOG B2).

    The schema is the single source of truth for the IR's shape and travels
    with the package (`layout_ir.schema.json`). Its `$id` is versioned and the
    `layout_ir_version` field gates consumers; bump both together on a breaking
    change.
    """
    return json.loads(_SCHEMA_PATH.read_text())


def _xyr(p) -> list[float]:
    return [p.x, p.y, p.r if p.r is not None else 0.0]


def signal_nets(app) -> dict[str, str]:
    """Bridge ② (BACKLOG I4b): signal ato-address → kicad net name.

    The one graph-derived part of the IR: a net's name is computed from its
    connected interfaces (faebryk/libs/net_naming.py) and is not recoverable as
    a signal address from the pcb alone. We enumerate every named net and map
    each connected interface's `get_full_name(include_uuid=False)` to that net's
    name. Truly unnamed nets (no `has_net_name`) are skipped — not referenceable.

    addr→net must be a function (an interface belongs to one net); a collision
    is a graph inconsistency and is raised loudly (I4, I8).
    """
    import faebryk.library._F as F

    bridge: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}
    for net in F.Net.bind_typegraph(app.tg).get_instances(app.g):
        name = net.get_name()
        if not name:  # unnamed → non-referenceable (I4b)
            continue
        for iface in net.get_connected_interfaces():
            addr = iface.get_full_name(include_uuid=False)
            if addr in bridge and bridge[addr] != name:
                conflicts.setdefault(addr, {bridge[addr]}).add(name)
            bridge[addr] = name
    if conflicts:
        raise LayoutIRError(
            f"signal address maps to multiple nets (addr→net not a function): "
            f"{ {k: sorted(v) for k, v in conflicts.items()} }"
        )
    return dict(sorted(bridge.items()))


def layout_ir(pcb: kicad.pcb.KicadPcb, app=None) -> dict[str, Any]:
    """Build the geometry-side IR from a parsed board (pcb-only, BACKLOG I1–I8).

    Only atopile-managed footprints (those carrying an `atopile_address`
    property) appear in `components`; manual footprints are not managed and are
    faithfully omitted (not a half-IR).

    If `app` (the built faebryk graph node) is given, the graph-derived
    `signal_nets` bridge ② is folded in (BACKLOG I4b); pcb-only callers omit it.
    """
    nets = _NetTable(pcb)  # strict: raises on duplicate/ambiguous net table

    components: dict[str, Any] = {}
    room_members: dict[str, list[str]] = {}
    for fp in pcb.footprints:
        addr = Property.try_get_property(fp.propertys, "atopile_address")
        if not addr:
            continue
        if addr in components:
            raise LayoutIRError(
                f"duplicate atopile_address {addr!r}: addr→footprint is not a "
                "function, cannot build a faithful IR"
            )
        if fp.sheetname:  # room = footprints sharing a sheetname (BACKLOG §C3)
            room_members.setdefault(fp.sheetname, []).append(addr)
        ref = Property.try_get_property(fp.propertys, "Reference") or ""
        pads: dict[str, Any] = {}
        for pad in fp.pads:
            net = nets.resolve(
                pad.net.number if pad.net is not None else 0,
                pad.net.name if pad.net is not None else None,
                f"pad {ref}.{pad.name}",
            )
            pads[pad.name] = {
                "uuid": pad.uuid,
                "net": net,
                "at": _xyr(pad.at),
                "layers": sorted(pad.layers),
            }
        components[addr] = {
            "ref": ref,
            "footprint_uuid": fp.uuid,
            "at": _xyr(fp.at),
            "layer": fp.layer,
            "pads": pads,
        }

    # reverse index: geometry net name → endpoints (no-net "" never referenceable)
    nets_map: dict[str, list[str]] = {}
    for addr, comp in components.items():
        for pad_name, pad in comp["pads"].items():
            if pad["net"] != "":
                nets_map.setdefault(pad["net"], []).append(f"{addr}.{pad_name}")
    nets_map = {k: sorted(v) for k, v in sorted(nets_map.items())}

    # rooms: managed footprints grouped by sheetname (BACKLOG §C3). No uuid, no
    # provenance — the room name IS the sheetname, member identity is the address.
    rooms: dict[str, Any] = {
        name: {"sheetname": name, "member_addrs": sorted(addrs)}
        for name, addrs in sorted(room_members.items())
    }

    ir: dict[str, Any] = {
        "layout_ir_version": LAYOUT_IR_VERSION,
        "components": components,
        "nets": nets_map,
        "rooms": rooms,
    }
    if app is not None:
        ir["signal_nets"] = signal_nets(app)
    return ir


def layout_ir_json(pcb: kicad.pcb.KicadPcb, app=None) -> str:
    """Canonical serialization — byte-stable for the build artifact (I5)."""
    return json.dumps(layout_ir(pcb, app), sort_keys=True, indent=1) + "\n"
