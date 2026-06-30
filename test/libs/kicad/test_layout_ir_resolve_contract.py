# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F3 contract — the bridge② REVERSE association engine.

F-diag (`ato diagnose`) receives failures + DRC violations keyed three ways:
by NET NAME (embedded in DRC description text), by footprint/pad UUID (DRC
`items[].uuid`), and by board COORDINATE (DRC `items[].pos`). To attribute a
finding back to an ato address / room it must INVERT the forward, address-indexed
IR. `layout_ir_resolve.LayoutResolver` is that inversion:

  - footprint_uuid -> ato address
  - pad_uuid       -> (ato address, pad name)
  - net name       -> [<addr>.<pad>, ...]   (reuses ir["nets"] verbatim)
  - (x, y)         -> room name             (point-in-polygon over rule-area rings)

The honest limit pinned here (BACKLOG §F G2): TRACK/VIA/ZONE uuids are generated
at route time, AFTER the build-IR — they are NOT in the IR, so they resolve to
None. That is a CONTRACT, asserted, not a silent gap.

S0 strict-xfail: the whole module is gated on the symbol landing. An XPASS while
unlanded is a test bug.
"""

import pytest

from faebryk.libs.kicad.layout_ir import LayoutIRError  # the loud-path error (reused)

try:
    from faebryk.libs.kicad.layout_ir_resolve import (
        LayoutResolver,
        room_polygons_from_pcb,
    )

    _F3_LANDED = True
except Exception:  # noqa: BLE001 — any import failure = not landed
    _F3_LANDED = False

    class LayoutResolver:  # stub: a loud-path test must NOT pass while unlanded
        def __init__(self, *a, **k):
            raise RuntimeError("F3 layout_ir_resolve not landed")

    def room_polygons_from_pcb(*a, **k):
        raise RuntimeError("F3 layout_ir_resolve not landed")


needs_f3 = pytest.mark.xfail(
    not _F3_LANDED, reason="F3 layout_ir_resolve not landed yet", strict=True
)


def _ir() -> dict:
    """A hand-built fake IR (the consumer-facing shape from layout_ir.py:14-19).

    Two managed components in two rooms. Distinct footprint + pad uuids; a no-net
    pad (net "") is present and deliberately absent from `nets`."""
    return {
        "layout_ir_version": 2,
        "components": {
            "top.r1": {
                "ref": "R1",
                "footprint_uuid": "fp-aaaa",
                "at": [10.0, 10.0, 0.0],
                "layer": "F.Cu",
                "pads": {
                    "1": {"uuid": "pad-a1", "net": "VCC", "at": [10, 9, 0], "layers": ["F.Cu"]},
                    "2": {"uuid": "pad-a2", "net": "N1", "at": [10, 11, 0], "layers": ["F.Cu"]},
                },
            },
            "top.r2": {
                "ref": "R2",
                "footprint_uuid": "fp-bbbb",
                "at": [40.0, 40.0, 0.0],
                "layer": "F.Cu",
                "pads": {
                    "1": {"uuid": "pad-b1", "net": "N1", "at": [40, 39, 0], "layers": ["F.Cu"]},
                    "2": {"uuid": "pad-b2", "net": "", "at": [40, 41, 0], "layers": ["F.Cu"]},
                },
            },
        },
        "nets": {
            "VCC": ["top.r1.1"],
            "N1": ["top.r1.2", "top.r2.1"],
        },
        "rooms": {
            "top.r1": {"sheetname": "top.r1", "member_addrs": ["top.r1"]},
            "top.r2": {"sheetname": "top.r2", "member_addrs": ["top.r2"]},
        },
    }


# room rings (rule-area polygons): r1 small box around (10,10); r2 around (40,40)
_RINGS = {
    "top.r1": [(5.0, 5.0), (15.0, 5.0), (15.0, 15.0), (5.0, 15.0)],
    "top.r2": [(35.0, 35.0), (45.0, 35.0), (45.0, 45.0), (35.0, 45.0)],
}


# ---------------------------------------------------------------------------
# uuid reverse lookups
# ---------------------------------------------------------------------------
@needs_f3
def test_footprint_uuid_to_addr():
    r = LayoutResolver(_ir())
    assert r.footprint_addr("fp-aaaa") == "top.r1"
    assert r.footprint_addr("fp-bbbb") == "top.r2"
    # an unknown uuid (e.g. a board edge / KiCad-owned fp) is None, not a raise.
    assert r.footprint_addr("fp-nope") is None


@needs_f3
def test_pad_uuid_to_addr():
    r = LayoutResolver(_ir())
    assert r.pad_addr("pad-a2") == ("top.r1", "2")
    assert r.pad_addr("pad-b1") == ("top.r2", "1")
    # the no-net pad is still pad-resolvable (it exists in components).
    assert r.pad_addr("pad-b2") == ("top.r2", "2")
    assert r.pad_addr("pad-nope") is None


@needs_f3
def test_net_endpoints_reuses_ir_nets():
    ir = _ir()
    r = LayoutResolver(ir)
    # verbatim reuse of ir["nets"] — F3 must NOT re-derive a different mapping.
    assert r.net_endpoints("N1") == ir["nets"]["N1"] == ["top.r1.2", "top.r2.1"]
    assert r.net_endpoints("VCC") == ["top.r1.1"]
    # an unknown / no-net name resolves to no endpoints (never a raise).
    assert r.net_endpoints("UNROUTED") == []
    assert r.net_endpoints("") == []


# ---------------------------------------------------------------------------
# the G2 limit, made a contract: track/via/zone uuids are NOT resolvable
# ---------------------------------------------------------------------------
@needs_f3
def test_track_via_zone_uuid_is_unresolvable_by_design():
    """A route-time uuid (track/via/zone) is absent from the build IR. It must
    resolve to None on BOTH uuid channels — the documented G2 best-effort gap."""
    r = LayoutResolver(_ir())
    track_uuid = "track-zzzz"  # never appears in the IR
    assert r.footprint_addr(track_uuid) is None
    assert r.pad_addr(track_uuid) is None


# ---------------------------------------------------------------------------
# coord -> room (point in polygon)
# ---------------------------------------------------------------------------
@needs_f3
def test_room_at_hits_the_containing_ring():
    r = LayoutResolver(_ir(), room_polygons=_RINGS)
    assert r.room_at(10.0, 10.0) == "top.r1"
    assert r.room_at(40.0, 40.0) == "top.r2"
    # a point in neither ring resolves to None (loud-or-nothing: no nearest-room
    # guess — a DRC item off in empty board space belongs to no room).
    assert r.room_at(25.0, 25.0) is None


@needs_f3
def test_room_at_picks_most_specific_on_overlap():
    """Overlapping rings (a nested sub-room inside an outer one) resolve to the
    SMALLEST containing ring. A naive first-match would return the outer room;
    this asserts the area tie-break bites."""
    rings = {
        "outer": [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)],
        "inner": [(40.0, 40.0), (60.0, 40.0), (60.0, 60.0), (40.0, 60.0)],
    }
    r = LayoutResolver(_ir(), room_polygons=rings)
    assert r.room_at(50.0, 50.0) == "inner"  # inside both → smaller wins
    assert r.room_at(10.0, 10.0) == "outer"  # only the outer contains it


@needs_f3
def test_room_at_without_polygons_is_none():
    """No room polygons supplied (pcb-only IR, no rule areas) → coord resolves to
    None rather than raising — F-diag degrades to net-name attribution."""
    r = LayoutResolver(_ir())
    assert r.room_at(10.0, 10.0) is None


# ---------------------------------------------------------------------------
# loud-or-nothing: a corrupt (non-functional) IR is a hard error
# ---------------------------------------------------------------------------
@needs_f3
def test_duplicate_footprint_uuid_is_loud():
    ir = _ir()
    ir["components"]["top.r2"]["footprint_uuid"] = "fp-aaaa"  # collide with r1
    with pytest.raises(LayoutIRError):
        LayoutResolver(ir)


@needs_f3
def test_duplicate_pad_uuid_is_loud():
    ir = _ir()
    ir["components"]["top.r2"]["pads"]["1"]["uuid"] = "pad-a1"  # collide with r1.1
    with pytest.raises(LayoutIRError):
        LayoutResolver(ir)


# ---------------------------------------------------------------------------
# room_polygons_from_pcb — extract rule-area rings from a board's managed zones
# ---------------------------------------------------------------------------
@needs_f3
def test_room_polygons_from_pcb_extracts_managed_rings():
    """Only `rule_area_<sheetname>` zones contribute; the ring comes from
    zone.polygon.pts.xys and the room name from zone.placement.sheetname. A
    user-authored (non-prefixed) zone is ignored."""
    import types

    from faebryk.libs.kicad.fileformats import kicad

    def _zone(name, sheetname, corners, *, managed=True):
        return kicad.pcb.Zone(
            net=0,
            net_name="",
            layers=[],
            layer="F.Cu",
            uuid=kicad.gen_uuid(),
            name=name,
            hatch=kicad.pcb.Hatch(mode=kicad.pcb.E_zone_hatch_mode.EDGE, pitch=0.5),
            connect_pads=kicad.pcb.ConnectPads(mode=None, clearance=0),
            min_thickness=0.25,
            filled_areas_thickness=False,
            placement=(
                kicad.pcb.ZonePlacement(sheetname=sheetname, enabled=True)
                if managed
                else None
            ),
            polygon=kicad.pcb.Polygon(
                pts=kicad.pcb.Pts(xys=[kicad.pcb.Xy(x=x, y=y) for x, y in corners])
            ),
        )

    # the extractor is duck-typed (reads .zones + real Zone attrs); a full
    # KicadPcb needs many unrelated kwargs, so wrap real Zones in a namespace.
    pcb = types.SimpleNamespace(
        zones=[
            _zone("rule_area_top.r1", "top.r1", _RINGS["top.r1"]),
            _zone("user_keepout", None, _RINGS["top.r2"], managed=False),
        ]
    )

    rings = room_polygons_from_pcb(pcb)
    assert set(rings) == {"top.r1"}  # the user zone is excluded
    assert rings["top.r1"] == _RINGS["top.r1"]
