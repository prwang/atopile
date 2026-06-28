# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
corridor — Tier0 corridor-as-data (BACKLOG Tier0): materialize a route stage's
text `corridor` polyline onto the board as a guide, with ZERO router changes.

A single-ended `RouteStage` may carry `corridor: [[x,y],...]` (BACKLOG Tier0).
`draw_corridors` draws that polyline as `gr_line` segments on the dedicated User.1
guide layer and flips the stage's `guide_corridor_enabled`, so the router (E1)
steers the stage's nets along it via its NATIVE `guide_corridor_enabled` reader
(§C boundary fact 4) — no router fork. It is a SOFT hint (the route may be pushed
off the corridor to avoid obstacles), not a hard checkpoint.

Single-mode only: the differential router entry has no `guide_corridor_*` kwarg
(§D2 mode exclusivity); the schema already rejects a corridor on a diff stage, so
this step only ever sees single stages.

Contract pinned by test/exporters/pcb/layout/test_corridor_contract.py.
"""

from typing import Any

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, RouteStage
from faebryk.libs.kicad.fileformats import kicad

PCB = kicad.pcb.KicadPcb

# the dedicated guide-corridor layer (same as room_ops.GUIDE_LAYER, §C fact 9).
GUIDE_LAYER = "User.1"


def _on_guide_layer(line: Any) -> bool:
    return getattr(line, "layer", None) == GUIDE_LAYER or GUIDE_LAYER in (
        getattr(line, "layers", None) or []
    )


def _corridor_line(start: tuple[float, float], end: tuple[float, float]):
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


def draw_corridors(pcb: PCB, plan: LayoutPlan) -> list:
    """Draw each single stage's `corridor` polyline onto User.1 and flip that
    stage's `guide_corridor_enabled`; return the inserted lines.

    Idempotent: every gr_line on the dedicated User.1 guide layer is removed first,
    so re-emitting onto an already-stamped board never accretes corridor guides.
    (User.1 is the guide-only layer; no build step emits other geometry there.)"""
    kicad.filter(
        pcb, "gr_lines", pcb.gr_lines, lambda line: not _on_guide_layer(line)
    )
    drawn: list = []
    for stage in plan.route_stages:
        if not isinstance(stage, RouteStage) or not stage.corridor:
            continue
        pts = [tuple(p) for p in stage.corridor]
        for a, b in zip(pts, pts[1:]):
            drawn.append(
                kicad.insert(pcb, "gr_lines", pcb.gr_lines, _corridor_line(a, b))
            )
        # let the router read the corridor we just drew (native guide reader).
        stage.config.guide_corridor_enabled = True
        stage.config.guide_corridor_layer = GUIDE_LAYER
    return drawn
