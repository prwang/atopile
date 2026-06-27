# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_placement_contract — D-Tier3 self-contained PLACEMENT + ROOM GEOMETRY
(BACKLOG §D-Tier3, 桶① part 1: TP1-TP6 + TR1-TR4 = 16 cases).

== WHY ====================================================================

In the generative / agent-text-first workflow `.ato` (circuit) + `layout.yaml`
(layout) must be SELF-CONTAINED — the single authority. Today component poses
have no text channel: managed footprints are auto-clustered on a grid at build
time (the transformer's per-parent cluster spread), per-instance rotation/side
are read back only from a board, and
`Room.rotation/layers/anchor` were PARSED but silently dropped by D3
(`_make_placement_rule_area` fed only a bbox + all signal layers). This file pins
the fix: a `placements` text channel keyed by ato address (room-relative, with a
board-absolute escape hatch) and `Room.polygon` + rotation/layers now wired into
the rule area (`rule_area._room_boundary`) — "declared but silently ignored" is
not allowed; either it takes effect or the field is removed.

== THE RATCHET (S0 discipline) ============================================

`_DT3_LANDED` imports the full D-Tier3 symbol set AND checks the new model fields
(`Room.polygon`, `LayoutPlan.placements`/`board`). A HALF landing (renamed one
half, or a symbol without its field) is treated as not-landed → sentinels that
raise → every test here is a clean strict-xfail. Negative cases are paired with a
positive control: before landing the control also raises (sentinel), so the test
xfails cleanly instead of XPASSing for the wrong reason (e.g. extra='forbid'
rejecting everything). Flip green by landing the feature, never by editing here.
"""

import pytest
from pydantic import ValidationError

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.layout_ir import layout_ir

# ---------------------------------------------------------------------------
# S0 ratchet guard — import the FULL D-Tier3 surface; half-landing stays red.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.layout_plan import (  # type: ignore
        Board,
        BoardOutline,
        LayoutPlan,
        LayoutPlanError,
        Placement,
        Room,
        Stackup,
        StackupLayer,
        load_layout_plan,  # noqa: F401
        outline_bounds,  # noqa: F401
        resolve_component_pose,
        resolve_placement,
        stackup_layers,  # noqa: F401
    )
    from faebryk.exporters.pcb.layout.rule_area import (  # type: ignore
        RuleAreaError,  # noqa: F401
        generate_rule_areas,
    )

    if not (
        "polygon" in Room.model_fields
        and "placements" in LayoutPlan.model_fields
        and "board" in LayoutPlan.model_fields
    ):
        raise ImportError("D-Tier3 model fields not present (half-landed)")
    _DT3_LANDED = True
except Exception:
    _DT3_LANDED = False

    class LayoutPlanError(Exception):  # placeholder so raises-tuples stay well-formed
        ...

    class RuleAreaError(Exception): ...

    def _unlanded(*_a, **_k):
        raise RuntimeError("D-Tier3 not landed (S0 ratchet)")

    Placement = Board = BoardOutline = Stackup = StackupLayer = _unlanded
    Room = LayoutPlan = generate_rule_areas = resolve_placement = _unlanded
    resolve_component_pose = _unlanded

needs_dt3 = pytest.mark.xfail(
    not _DT3_LANDED,
    reason="D-Tier3 placement/room-geometry not landed (S0 ratchet)",
    strict=True,
)


# ===========================================================================
# inline board builder (mirrors test_rule_area_contract): managed footprints
# carrying sheetname (= room) so `generate_rule_areas` accepts the room.
# ===========================================================================
def _board(footprints) -> str:
    """footprints: list of (addr, sheetname, [(pad, net, x, y)], (fx, fy))."""
    nets = sorted({p[1] for fp in footprints for p in fp[2]} - {""})
    number = {"": 0, **{n: i + 1 for i, n in enumerate(nets)}}
    lines = [
        "(kicad_pcb",
        "\t(version 20241229)",
        '\t(generator "test_placement")',
        '\t(generator_version "10.0")',
        "\t(general (thickness 1.6))",
        '\t(layers (0 "F.Cu" signal) (2 "B.Cu" signal))',
    ]
    for n in sorted(number, key=lambda k: number[k]):
        lines.append(f'\t(net {number[n]} "{n}")')
    u = [0]

    def uuid(tag):
        u[0] += 1
        return f"00000000-0000-0000-0000-{tag}{u[0]:08x}"[:36]

    for addr, sheetname, pads, (fx, fy) in footprints:
        ref = addr.rsplit(".", 1)[-1]
        lines += [
            '\t(footprint "test:FP"',
            '\t\t(layer "F.Cu")',
            f'\t\t(uuid "{uuid("fab")}")',
            f"\t\t(at {fx} {fy} 0)",
            f'\t\t(sheetname "{sheetname}")',
            f'\t\t(sheetfile "{sheetname}.kicad_sch")',
            f'\t\t(property "Reference" "{ref}" (at 0 0)'
            ' (layer "F.SilkS") (effects (font (size 1 1))))',
            f'\t\t(property "atopile_address" "{addr}" (at 0 0)'
            ' (layer "F.Fab") (effects (font (size 1 1))))',
        ]
        for pad_name, net, px, py in pads:
            net_sexp = f'(net {number[net]} "{net}")' if net != "" else ""
            lines += [
                f'\t\t(pad "{pad_name}" smd rect',
                f"\t\t\t(at {px} {py} 0)",
                "\t\t\t(size 1 1)",
                '\t\t\t(layers "F.Cu")',
                f"\t\t\t{net_sexp}",
                f'\t\t\t(uuid "{uuid("fad")}")',
                "\t\t)",
            ]
        lines.append("\t)")
    lines.append(")")
    return "\n".join(lines) + "\n"


def _load(text: str) -> kicad.pcb.PcbFile:
    return kicad.loads(kicad.pcb.PcbFile, text)


def _zone_for(pcb, sheetname):
    for z in pcb.zones:
        if z.placement is not None and z.placement.sheetname == sheetname:
            return z
    raise AssertionError(f"no placement zone for {sheetname!r}")


def _poly(zone) -> list[tuple[float, float]]:
    return [(p.x, p.y) for p in zone.polygon.pts.xys]


# a single-room board so every TR test has a real sheetname to attach to.
_FPS = [("top.r1.u1", "top.r1", [("1", "N1", 1.0, 1.0)], (0.0, 0.0))]


def _legal_placement():
    """The positive control reused by every negative case: pre-landing this is a
    sentinel call that raises ⇒ the negative test xfails cleanly, not XPASS."""
    return Placement(component="top.r1.u1", at=(1.0, 2.0))


# ===========================================================================
# TP1 — placements positive, field-by-field (not "parses ⇒ green").
# ===========================================================================
@needs_dt3
def test_placement_fields_round_trip():
    p = Placement(component="top.r1.u1", at=(3.5, -4.0), rotation=90.0, side="B")
    assert p.component == "top.r1.u1"
    assert tuple(p.at) == (3.5, -4.0)
    assert p.rotation == 90.0
    assert p.side == "B"
    # defaults: rotation 0, side front
    d = Placement(component="top.r1.u1", at=(0.0, 0.0))
    assert d.rotation == 0.0 and d.side == "F"


# ===========================================================================
# TP2 — placements negative (loud-or-nothing), each paired w/ positive control.
# ===========================================================================
@needs_dt3
@pytest.mark.parametrize(
    "kwargs, locator",
    [
        ({"component": "a", "at": (1.0, 2.0), "bogus": 1}, "bogus"),  # extra=forbid
        ({"component": "a", "at": (1.0, 2.0), "side": "X"}, "side"),  # side ∉ {F,B}
        ({"at": (1.0, 2.0)}, "component"),  # missing component
        ({"component": "a"}, "at"),  # missing at
        ({"component": "a", "at": (1.0, 2.0, 3.0)}, "at"),  # at not a 2-tuple
    ],
    ids=["extra_key", "bad_side", "no_component", "no_at", "at_not_pair"],
)
def test_placement_invalid_is_loud(kwargs, locator):
    assert _legal_placement().component == "top.r1.u1"  # control: must not raise
    with pytest.raises(ValidationError) as ei:
        Placement(**kwargs)
    # the error must point at the OFFENDING field (structured loc, not a loose
    # substring of the whole message — "at" is a substring of "validation").
    locs = {str(p) for err in ei.value.errors() for p in err["loc"]}
    assert locator in locs


# ===========================================================================
# TP3 — room-relative → board-absolute pure oracle (answer by construction).
# ===========================================================================
@needs_dt3
def test_resolve_placement_composes_room_origin():
    room = Room(module="top.r1", origin=(10.0, 20.0), size=(5.0, 5.0))
    p = Placement(component="top.r1.u1", at=(3.0, 4.0))
    assert resolve_placement(p, room) == pytest.approx((13.0, 24.0))  # hand-computed


# ===========================================================================
# TP4 — mutation self-check (anti-blindness): move the room, members must move.
# ===========================================================================
@needs_dt3
def test_resolve_placement_follows_room_move():
    p = Placement(component="top.r1.u1", at=(3.0, 4.0))
    before = resolve_placement(
        p, Room(module="top.r1", origin=(10.0, 20.0), size=(5.0, 5.0))
    )
    after = resolve_placement(
        p, Room(module="top.r1", origin=(100.0, 200.0), size=(5.0, 5.0))
    )
    assert before != after  # if equal, the oracle is blind to room origin
    assert after == pytest.approx((103.0, 204.0))


# ===========================================================================
# TP5 — board-absolute escape hatch: an absolute placement lands verbatim,
# NOT composed through the room.
# ===========================================================================
@needs_dt3
def test_absolute_placement_bypasses_room():
    p = Placement(component="top.r1.u1", at=(50.0, 60.0), absolute=True)
    other = Room(module="top.r1", origin=(10.0, 20.0), size=(5.0, 5.0))
    assert resolve_placement(p, other) == pytest.approx((50.0, 60.0))  # room ignored


# ===========================================================================
# TP6 — placements vs reuse priority, via the REAL pure consumer
# `resolve_component_pose`: a text placement OVERRIDES reuse; absent text FALLS
# THROUGH to reuse; neither is loud. Pins the priority that text authority takes
# over the auto-grid spread (transformer.py:176 / :2013-2080) and over a pose
# read back from a reuse board. Inverting the priority would fail this test.
# ===========================================================================
@needs_dt3
def test_text_placement_overrides_reuse_else_falls_back():
    room = Room(module="top.r1", origin=(10.0, 20.0), size=(5.0, 5.0))
    text = Placement(component="top.r1.u1", at=(3.0, 4.0))

    # text present ⇒ text pose wins (composed (13,24), NOT the reuse (99,99))
    assert resolve_component_pose(text, (99.0, 99.0), room) == pytest.approx(
        (13.0, 24.0)
    )
    # no text ⇒ the reuse pose stands
    assert resolve_component_pose(None, (7.0, 7.0), room) == pytest.approx((7.0, 7.0))
    # neither ⇒ loud (no silent (0,0))
    with pytest.raises(LayoutPlanError):
        resolve_component_pose(None, None, room)


# ===========================================================================
# TR1 — Room.polygon positive + rule area: a non-rectangular polygon (≥3 pts)
# produces a zone with THAT polygon, not the bbox 4-corners.
# (extends rule_area._room_boundary / _make_placement_rule_area.)
# ===========================================================================
_L_SHAPE = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 8.0), (0.0, 8.0)]


@needs_dt3
def test_room_polygon_becomes_zone_polygon():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    generate_rule_areas(
        pcb, LayoutPlan(rooms=[Room(module="top.r1", polygon=_L_SHAPE)]), layout_ir(pcb)
    )
    pts = _poly(_zone_for(pcb, "top.r1"))
    assert pts == [pytest.approx(p) for p in _L_SHAPE]  # the L, not 4 bbox corners
    assert len(pts) == 6  # a bbox would be 4 — proves it is not bbox'd


# ===========================================================================
# TR2 — room geometry negative (loud), paired with a positive control.
# ===========================================================================
_BOWTIE = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]  # self-intersecting


@needs_dt3
@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"polygon": [(0.0, 0.0), (1.0, 1.0)]}, "3 points"),  # < 3 points
        ({"polygon": _BOWTIE}, "self-intersecting"),  # self-intersecting
        (
            {"origin": (0.0, 0.0), "size": (5.0, 5.0), "polygon": _L_SHAPE},
            "mutually exclusive",
        ),  # both geometries
    ],
    ids=["too_few_points", "self_intersecting", "polygon_and_rect"],
)
def test_room_geometry_invalid_is_loud(kwargs, msg):
    assert Room(module="top.r1", polygon=_L_SHAPE).polygon is not None  # control
    with pytest.raises(ValidationError) as ei:
        Room(module="top.r1", **kwargs)
    assert msg in str(ei.value)  # rejected for the RIGHT reason, not incidentally


# ===========================================================================
# TR3 — Room.rotation is WIRED (no longer silently ignored by D3):
# a rotated room produces a ROTATED polygon. Convention pinned here: CCW about the
# first boundary point. origin/size ⇒ corners [origin, origin+size], first = origin.
# ===========================================================================
@needs_dt3
def test_room_rotation_rotates_the_zone_polygon():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    generate_rule_areas(
        pcb,
        LayoutPlan(
            rooms=[
                Room(module="top.r1", origin=(0.0, 0.0), size=(4.0, 2.0), rotation=90.0)
            ]
        ),
        layout_ir(pcb),
    )
    pts = _poly(_zone_for(pcb, "top.r1"))

    # oracle: corners [(0,0),(4,0),(4,2),(0,2)] rotated 90° CCW about (0,0). The
    # closed form for 90° CCW about the origin is (x,y)->(-y,x) — hand-computed
    # below (NOT recomputed via the same cos/sin the impl uses), so a transposed
    # rotation matrix in the implementation would diverge from these and fail.
    corners = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)]
    oracle = [(0.0, 0.0), (0.0, 4.0), (-2.0, 4.0), (-2.0, 0.0)]
    assert pts == [pytest.approx(p) for p in oracle]
    # and it is NOT the un-rotated axis-aligned rectangle (rotation took effect)
    assert pts != [pytest.approx(p) for p in corners]


# ===========================================================================
# TR4 — Room.layers is WIRED (no longer silently ignored): an explicit layer set
# restricts the zone to those layers, not the all-signal-layers default
# (rule_area._copper_layers). Either it takes effect or the field must be removed.
# ===========================================================================
@needs_dt3
def test_room_layers_restricts_the_zone():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    # default (no layers): the zone spans both signal layers of the board
    generate_rule_areas(
        pcb,
        LayoutPlan(rooms=[Room(module="top.r1", origin=(0.0, 0.0), size=(5.0, 5.0))]),
        ir,
    )
    default_zone = _zone_for(pcb, "top.r1")
    default_layers = set(
        default_zone.layers or ([default_zone.layer] if default_zone.layer else [])
    )
    assert default_layers == {"F.Cu", "B.Cu"}

    # explicit single layer: restricted to exactly that layer
    bf2 = _load(_board(_FPS))
    pcb2 = bf2.kicad_pcb
    generate_rule_areas(
        pcb2,
        LayoutPlan(
            rooms=[
                Room(
                    module="top.r1", origin=(0.0, 0.0), size=(5.0, 5.0), layers=["F.Cu"]
                )
            ]
        ),
        layout_ir(pcb2),
    )
    z = _zone_for(pcb2, "top.r1")
    realized = set(z.layers or ([z.layer] if z.layer else []))
    assert realized == {"F.Cu"}  # honored, not the {F.Cu, B.Cu} default
