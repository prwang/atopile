# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_rule_area_contract — stage D3 acceptance contract (BACKLOG §D, task D3).

== THE PROTOCOL ===========================================================

D3 turns a resolved `LayoutPlan` (D2) into KiCad placement rule areas on the
board. For each room it emits one keepout/placement `Zone` whose
`placement.sheetname` is the room's ato address — the §C3 source channel KiCad
preserves verbatim (no `group`, no zig change: `ZonePlacement.sheetname` already
exists, pcb.zig:828). C3 has already stamped that same sheetname onto the room's
footprints, so the rule area and its members agree.

    generate_rule_areas(pcb, plan, ir) -> list[Zone]
        # inserts (via kicad.insert, reusing the transformer insert_zone pattern)
        # one placement zone per room; returns the inserted zones.

Room polygon:
  * explicit `origin`/`size` (D2) ⇒ the axis-aligned rectangle [origin,
    origin+size];
  * both omitted ⇒ the rectangle is DERIVED as the bbox of the room's member pad
    positions (from the IR) — the D-spec "派生包围盒" case.

Constraints (BACKLOG facts 3/9): no custom S-expression tokens (sheetname is a
native field); copper-layer geometry must carry a real net — a placement rule
area is a keepout, it carries no copper. Multi-room boards get one independent
zone per room (D-spec "多 room" pin), no cross-contamination.

== THE RATCHET (S0 discipline) ============================================

`_D3_LANDED` probes the rule_area module AND layout_plan (D3 builds real D2
plans). strict-xfail until both land; the symbols bind to a sentinel that raises
so the bodies fail cleanly now and flip green automatically on landing. D3.5 is
additionally gated on kicad-cli (skipif), like C3.4 — it tests that KiCad itself
ingests the generated rule area.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.layout_ir import layout_ir

# ---------------------------------------------------------------------------
# S0 ratchet guard: probe the not-yet-existing rule_area + layout_plan symbols.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.layout_plan import (  # type: ignore
        LayoutPlan,
        Room,
        RouteStage,  # noqa: F401
    )
    from faebryk.exporters.pcb.layout.rule_area import (  # type: ignore
        generate_rule_areas,
    )

    _D3_LANDED = True
except Exception:
    _D3_LANDED = False

    def _unlanded(*_a, **_k):
        raise RuntimeError("D3 rule_area not landed (S0 ratchet)")

    LayoutPlan = Room = generate_rule_areas = _unlanded

needs_d3 = pytest.mark.xfail(
    not _D3_LANDED,
    reason="D3 rule-area generator not landed (S0 ratchet)",
    strict=True,
)

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
needs_kicad_cli = pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")


# ===========================================================================
# inline board builder: managed footprints carrying sheetname (= room) + pads at
# known global positions (so derived bboxes are answerable by construction).
# ===========================================================================
def _board(footprints) -> str:
    """footprints: list of (addr, sheetname, [(pad_name, net, x, y)], fp_xy)."""
    nets = sorted({p[1] for fp in footprints for p in fp[2]} - {""})
    number = {"": 0, **{n: i + 1 for i, n in enumerate(nets)}}
    lines = [
        "(kicad_pcb",
        "\t(version 20241229)",
        '\t(generator "test_rule_area")',
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


def _placement_zones(pcb) -> dict[str, object]:
    """{sheetname -> zone} for every placement-bearing zone on the board."""
    return {
        z.placement.sheetname: z
        for z in pcb.zones
        if z.placement is not None and z.placement.sheetname is not None
    }


def _bbox(zone) -> tuple[float, float, float, float]:
    xs = [p.x for p in zone.polygon.pts.xys]
    ys = [p.y for p in zone.polygon.pts.xys]
    return (min(xs), min(ys), max(xs), max(ys))


# a two-room board: r1 has two pads (a real bbox), r2 has one footprint. Every
# footprint sits at the origin so a pad's GLOBAL position == its declared
# (footprint-local) `at` — the derived bbox is then answerable by construction
# (the IR carries footprint `at` + pad-local `at`; a correct generator composes
# them, so origin-placed footprints make the global pad coords unambiguous).
_FPS = [
    ("top.r1.u1", "top.r1", [("1", "N1", 5.0, 5.0), ("2", "N2", 9.0, 7.0)], (0.0, 0.0)),
    ("top.r1.u2", "top.r1", [("1", "N1", 11.0, 8.0)], (0.0, 0.0)),
    ("top.r2.u3", "top.r2", [("1", "N3", 30.0, 30.0)], (0.0, 0.0)),
]


def _plan_with(rooms) -> "LayoutPlan":
    return LayoutPlan(rooms=rooms, route_stages=[])


# ===========================================================================
# D3.1 — placement sheetname round-trips through dumps/loads VERBATIM.
# ===========================================================================
@needs_d3
def test_placement_sheetname_round_trips():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    plan = _plan_with(
        [Room(module="top.r1", origin=(0.0, 0.0), size=(20.0, 20.0))]
    )
    generate_rule_areas(pcb, plan, ir)

    # in-memory: exactly one placement zone, sourced from the sheetname, enabled
    zones = _placement_zones(pcb)
    assert set(zones) == {"top.r1"}
    assert zones["top.r1"].placement.enabled is True

    # survives a dumps→loads cycle AND appears verbatim in the text (no custom token)
    text = kicad.dumps(bf)
    assert "(placement" in text and '(sheetname "top.r1")' in text
    reloaded = _load(text).kicad_pcb
    assert set(_placement_zones(reloaded)) == {"top.r1"}


# ===========================================================================
# D3.2 — explicit origin/size ⇒ the exact rectangle bbox.
# ===========================================================================
@needs_d3
def test_explicit_origin_size_polygon():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    plan = _plan_with([Room(module="top.r1", origin=(2.0, 3.0), size=(10.0, 6.0))])
    generate_rule_areas(pcb, plan, ir)

    z = _placement_zones(pcb)["top.r1"]
    assert _bbox(z) == pytest.approx((2.0, 3.0, 12.0, 9.0))


# ===========================================================================
# D3.3 — derived bbox: size omitted ⇒ polygon bounds the room's member pads.
# (Answer fixed by construction: r1's pads span x∈[5,11], y∈[5,8].)
# ===========================================================================
@needs_d3
def test_derived_bbox_bounds_member_pads():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    plan = _plan_with([Room(module="top.r1")])  # no origin/size → derive
    generate_rule_areas(pcb, plan, ir)

    minx, miny, maxx, maxy = _bbox(_placement_zones(pcb)["top.r1"])
    # bbox must contain every member pad of r1 and be tight to their extent
    assert minx <= 5.0 and miny <= 5.0
    assert maxx >= 11.0 and maxy >= 8.0
    assert (minx, miny, maxx, maxy) == pytest.approx((5.0, 5.0, 11.0, 8.0))


# ===========================================================================
# D3.4 — multi-room: N rooms ⇒ N independent zones, one per sheetname, no
# cross-contamination (mirrors C3.10's anti-"single-room false-green" pin).
# ===========================================================================
@needs_d3
def test_multi_room_one_zone_each():
    bf = _load(_board(_FPS))
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    plan = _plan_with(
        [
            Room(module="top.r1", origin=(0.0, 0.0), size=(20.0, 20.0)),
            Room(module="top.r2", origin=(25.0, 25.0), size=(10.0, 10.0)),
        ]
    )
    generate_rule_areas(pcb, plan, ir)

    zones = _placement_zones(pcb)
    assert set(zones) == {"top.r1", "top.r2"}
    # each zone's geometry belongs to its OWN room, not the other's
    assert _bbox(zones["top.r1"]) == pytest.approx((0.0, 0.0, 20.0, 20.0))
    assert _bbox(zones["top.r2"]) == pytest.approx((25.0, 25.0, 35.0, 35.0))


# ===========================================================================
# D3.5 — KiCad cross-validation (slow, needs kicad-cli): the generated rule area
# survives `kicad-cli pcb upgrade --force` VERBATIM and adds no DRC violations.
# Reuses the C3.4 approach (>16-byte room name, JSON DRC report in tmp_path).
# ===========================================================================
def _drc_violation_count(pcb_path: Path, report: Path) -> int:
    subprocess.run(
        [
            "kicad-cli", "pcb", "drc", "--format", "json",
            "-o", str(report), str(pcb_path),
        ],
        capture_output=True, text=True, timeout=180,
    )
    return len(json.loads(report.read_text()).get("violations", []))


@needs_d3
@needs_kicad_cli
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_kicad_ingests_generated_rule_area(tmp_path):
    room = "rule_area_top.power_supply_3v3"  # >16 bytes, like C3.4
    fps = [(f"{room}.u1", room, [("1", "N1", 5.0, 5.0)], (5.0, 5.0))]
    bf = _load(_board(fps))
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    generate_rule_areas(
        pcb, _plan_with([Room(module=room, origin=(0.0, 0.0), size=(20.0, 20.0))]), ir
    )

    modified = tmp_path / "modified.kicad_pcb"
    modified.write_text(kicad.dumps(bf))
    baseline = tmp_path / "baseline.kicad_pcb"
    baseline.write_text(_board(fps))  # same board, no rule area

    for f in (modified, baseline):
        r = subprocess.run(
            ["kicad-cli", "pcb", "upgrade", "--force", str(f)],
            capture_output=True, text=True, timeout=180,
        )
        assert r.returncode == 0, f"upgrade failed on {f.name}:\n{r.stderr}"

    up = modified.read_text()
    assert "(version 20260206)" in up
    assert "(placement" in up and f'(sheetname "{room}")' in up
    assert _drc_violation_count(modified, tmp_path / "m.json") == (
        _drc_violation_count(baseline, tmp_path / "b.json")
    ), "the generated rule area changed the DRC violation count"
