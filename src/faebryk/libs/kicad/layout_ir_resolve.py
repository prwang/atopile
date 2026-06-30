# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""layout_ir_resolve — bridge② REVERSE association engine (BACKLOG §F, F3).

F-diag (`ato diagnose`) gets failures and DRC violations keyed by NET NAME (the
name is embedded in the DRC description text), by footprint/pad UUID (the DRC
`items[].uuid`), and by board COORDINATE (the DRC `items[].pos`). To attribute a
finding back to an ato address / room it must INVERT the forward, address-indexed
IR (`layout_ir.py`). `LayoutResolver` is that inversion:

  - footprint_uuid -> ato address            (inverts components[addr].footprint_uuid)
  - pad_uuid       -> (ato address, pad)      (inverts components[addr].pads[pad].uuid)
  - net name       -> [<addr>.<pad>, ...]     (REUSES ir["nets"] verbatim — already reverse)
  - (x, y)         -> room name               (point-in-polygon over rule-area rings)

WHY reverse, and the HONEST LIMITS (BACKLOG §F G2 — these are pinned by the
contract test, not silent gaps):

  * uuid resolution covers ONLY footprints and pads — those are the only uuids the
    build-time IR carries. TRACK / VIA / ZONE uuids are generated at ROUTE time,
    AFTER the IR is built, so they never appear in the IR and resolve to None here.
    F-diag falls back to net-name + coordinate hit-testing for those.
  * coord->room needs the room POLYGONS, which live on the board as managed zones
    (`rule_area_<sheetname>`, see rule_area.py), not in the IR (the IR has no
    spatial extents). `room_polygons_from_pcb` extracts them; `room_at` is the pure
    point-in-polygon consumer (unit-tested with hand-fed rings).

AMBIGUITY, not loudness, on duplicate uuids: a REUSED layout (examples/layout_reuse)
legitimately repeats the SAME footprint/pad uuid across every reused instance —
the reuse copies the source geometry verbatim, uuids and all. So a duplicate uuid
is NOT a corrupt IR; it is an EXPECTED case where uuid→addr is not a function.
Such a uuid is marked ambiguous and resolves to None (the honest G2 best-effort:
fall back to net-name + coordinate correlation), never a crash. Every other
lookup miss (unknown uuid / net / a point in no room) likewise returns None / [].

Pure: the only I/O is the optional `room_polygons_from_pcb` board read; the
resolver itself is venv-importable and graph-free.
"""

from typing import Any

_MANAGED_ZONE_PREFIX = "rule_area_"  # mirrors rule_area.py:_MANAGED_PREFIX

# sentinel for a uuid seen on more than one address (reuse) → unresolvable.
_AMBIGUOUS = object()


def _ring_area(ring: list[tuple[float, float]]) -> float:
    """Twice the absolute signed area of a closed polygon (shoelace). Used only as
    a deterministic "most-specific room wins" tie-break — magnitude, not sign."""
    n = len(ring)
    acc = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        acc += x1 * y2 - x2 * y1
    return abs(acc)


def _point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon for a simple (possibly rotated) ring. Points on
    the boundary are treated as inside-enough for attribution; the exact edge case
    does not matter for DRC-item room assignment (items sit well inside a room)."""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        # does the horizontal ray at y cross edge (i, j)?
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


class LayoutResolver:
    """Reverse lookups from a layout IR (+ optional room rings). See module doc."""

    def __init__(
        self,
        ir: dict[str, Any],
        room_polygons: dict[str, list[tuple[float, float]]] | None = None,
    ) -> None:
        self._fp_addr: dict[str, str] = {}
        self._pad_addr: dict[str, tuple[str, str]] = {}
        self._nets: dict[str, list[str]] = dict(ir.get("nets", {}))
        # rings normalized to lists of (x, y) float tuples (so callers may pass any
        # sequence-of-pairs); kept with their room name for the most-specific tie-break.
        self._rings: dict[str, list[tuple[float, float]]] = {
            name: [(float(px), float(py)) for px, py in ring]
            for name, ring in (room_polygons or {}).items()
        }

        for addr, comp in ir.get("components", {}).items():
            fp_uuid = comp.get("footprint_uuid")
            if fp_uuid is not None:
                # a uuid already seen (reuse) is now AMBIGUOUS → unresolvable, not
                # a crash: uuid→addr ceases to be a function but the IR is valid.
                self._fp_addr[fp_uuid] = (
                    _AMBIGUOUS if fp_uuid in self._fp_addr else addr
                )
            for pad_name, pad in comp.get("pads", {}).items():
                pad_uuid = pad.get("uuid")
                if pad_uuid is None:
                    continue
                self._pad_addr[pad_uuid] = (
                    _AMBIGUOUS
                    if pad_uuid in self._pad_addr
                    else (addr, pad_name)
                )

    # --- uuid channels (footprint/pad only — track/via/zone are route-time, G2;
    #     a reuse-duplicated uuid is ambiguous → None) ---
    def footprint_addr(self, uuid: str) -> str | None:
        v = self._fp_addr.get(uuid)
        return None if v is _AMBIGUOUS else v

    def pad_addr(self, uuid: str) -> tuple[str, str] | None:
        v = self._pad_addr.get(uuid)
        return None if v is _AMBIGUOUS else v

    # --- net channel (verbatim reuse of ir["nets"]) -----------------------------
    def net_endpoints(self, net_name: str) -> list[str]:
        return list(self._nets.get(net_name, []))

    # --- coordinate channel (point-in-ring, most-specific wins) -----------------
    def room_at(self, x: float, y: float) -> str | None:
        """The room whose ring contains (x, y); on overlap the SMALLEST-area ring
        wins (a nested sub-room beats its enclosing room), tie-broken by name for
        determinism. No containing ring → None (no nearest-room guessing)."""
        hits = [
            (name, ring)
            for name, ring in self._rings.items()
            if _point_in_ring(x, y, ring)
        ]
        if not hits:
            return None
        hits.sort(key=lambda nr: (_ring_area(nr[1]), nr[0]))
        return hits[0][0]


def room_polygons_from_pcb(pcb) -> dict[str, list[tuple[float, float]]]:
    """Extract room rings from a board's MANAGED placement zones.

    A managed rule area is a zone named `rule_area_<sheetname>` (rule_area.py);
    its ring is `zone.polygon.pts.xys` and its room name is
    `zone.placement.sheetname`. User-authored zones (no prefix / no placement) are
    ignored. The returned dict feeds `LayoutResolver(ir, room_polygons=...)`."""
    rings: dict[str, list[tuple[float, float]]] = {}
    for zone in pcb.zones:
        name = getattr(zone, "name", None)
        if not name or not name.startswith(_MANAGED_ZONE_PREFIX):
            continue
        placement = getattr(zone, "placement", None)
        sheetname = getattr(placement, "sheetname", None) if placement else None
        if not sheetname:
            continue
        polygon = getattr(zone, "polygon", None)
        pts = getattr(getattr(polygon, "pts", None), "xys", None)
        if not pts:
            continue
        rings[sheetname] = [(float(p.x), float(p.y)) for p in pts]
    return rings
