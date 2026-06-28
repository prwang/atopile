# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_corridor_contract — Tier0 corridor-as-data (BACKLOG Tier0).

A single-ended route stage may carry a `corridor: [[x,y],...]` polyline. The
schema half (this stage's `corridor` field + validation) and the build half
(`corridor.draw_corridors` painting it onto User.1 and flipping
`guide_corridor_enabled`) are pinned here. Single-mode only — the differential
router entry has no guide_corridor_* kwarg (§D2 mode exclusivity).
"""

import pytest
from pydantic import ValidationError

from faebryk.exporters.pcb.layout.corridor import GUIDE_LAYER, draw_corridors
from faebryk.exporters.pcb.layout.layout_plan import load_layout_plan
from faebryk.libs.kicad.fileformats import kicad

_LOUD = (ValidationError, ValueError)


def _plan(corridor_yaml: str, mode: str = "single"):
    return load_layout_plan(
        "rooms: []\nroute_stages:\n"
        f"  - name: bus\n    mode: {mode}\n    nets: [top.a.x]\n"
        f"    config: {{}}\n{corridor_yaml}"
    )


def _empty_board():
    text = (
        "(kicad_pcb\n"
        "\t(version 20241229)\n"
        '\t(generator "test_corridor")\n'
        '\t(generator_version "10.0")\n'
        "\t(general (thickness 1.6))\n"
        '\t(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (50 "User.1" user))\n'
        ")\n"
    )
    return kicad.loads(kicad.pcb.PcbFile, text).kicad_pcb


def _guide_lines(pcb):
    return [
        ln
        for ln in pcb.gr_lines
        if getattr(ln, "layer", None) == GUIDE_LAYER
        or GUIDE_LAYER in (getattr(ln, "layers", None) or [])
    ]


# ===========================================================================
# C0.1 — corridor positive (field preserved verbatim, in order).
# ===========================================================================
def test_corridor_parses_in_order():
    plan = _plan("    corridor: [[0, 0], [10, 0], [10, 10]]")
    (stage,) = plan.route_stages
    assert [tuple(p) for p in stage.corridor] == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]


# ===========================================================================
# C0.2 — corridor negative (loud), paired with a positive control.
# ===========================================================================
def test_corridor_on_diff_is_loud():
    assert _plan("    corridor: [[0, 0], [1, 1]]").route_stages  # single control: ok
    with pytest.raises(_LOUD) as ei:
        _plan("    corridor: [[0, 0], [1, 1]]", mode="diff")
    assert "single" in str(ei.value)


def test_corridor_too_few_points_is_loud():
    assert _plan("    corridor: [[0, 0], [1, 1]]").route_stages  # 2 pts control: ok
    with pytest.raises(_LOUD) as ei:
        _plan("    corridor: [[0, 0]]")
    assert "2 points" in str(ei.value)


# ===========================================================================
# C0.3 — build: draw_corridors paints the polyline onto User.1 (N-1 segments,
# coords verbatim) and flips guide_corridor_enabled so the router reads it.
# ===========================================================================
def test_draw_corridors_paints_user1_and_enables_guide():
    pcb = _empty_board()
    plan = _plan("    corridor: [[0, 0], [10, 0], [10, 10]]")
    drawn = draw_corridors(pcb, plan)

    # a 3-point polyline ⇒ 2 segments, all on the User.1 guide layer
    assert len(drawn) == 2
    guides = _guide_lines(pcb)
    assert len(guides) == 2
    segs = {((ln.start.x, ln.start.y), (ln.end.x, ln.end.y)) for ln in guides}
    assert segs == {((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (10.0, 10.0))}

    # the stage now asks the router to read the corridor we drew
    (stage,) = plan.route_stages
    assert stage.config.guide_corridor_enabled is True
    assert stage.config.guide_corridor_layer == GUIDE_LAYER


# ===========================================================================
# C0.4 — re-emit is idempotent: a second pass replaces, never accretes.
# ===========================================================================
def test_draw_corridors_is_idempotent():
    pcb = _empty_board()
    plan = _plan("    corridor: [[0, 0], [10, 0], [10, 10]]")
    draw_corridors(pcb, plan)
    draw_corridors(pcb, plan)
    assert len(_guide_lines(pcb)) == 2  # not 4


# ===========================================================================
# C0.5 — a stage with no corridor draws nothing and leaves the guide flag unset.
# ===========================================================================
def test_no_corridor_is_a_noop():
    pcb = _empty_board()
    plan = _plan("")  # no corridor line
    assert draw_corridors(pcb, plan) == []
    assert _guide_lines(pcb) == []
    (stage,) = plan.route_stages
    assert stage.config.guide_corridor_enabled is None
