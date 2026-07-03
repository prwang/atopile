# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
bundle_geometry — the cross-section SSOT for a D-Tier2 bundle (BACKLOG §D-Tier2).

A BundleStage (layout_plan) carries the validated *intent* (ordered lanes +
segmented trunk + breakouts). This module turns that intent into the GEOMETRY the
router materializes: the per-segment cross-section (`cross_section_offsets`) and
the `<t>.layout_plan.json` fragment E1 reads (`bundle_artifact`). It is the single
source of every member offset — the artifact is not a second computation, it calls
the same function (pinned by T-A5). The contract is in
`test/exporters/pcb/layout/test_bundle_contract.py`.

CROSS-SECTION CONVENTION (pinned — makes the oracle hand-computable):
lanes are packed in declared order along the axis perpendicular to the centerline.
`spacing` is the EDGE-TO-EDGE gap between adjacent lane SLOTS (so a lane's WIDTH
shifts every downstream member's offset). A single lane's slot width = its width
(the bundle default); a diff lane's slot width = 2*width + gap — the true copper
envelope of two tracks with an EDGE-TO-EDGE gap between them. The whole packing is
then centered about offset 0 (subtract the slot-envelope midpoint), so the
cross-section is symmetric about the centerline. Within a diff slot, P sits at
slot_center - (gap + width)/2, N at slot_center + (gap + width)/2, so the
CENTER-TO-CENTER distance is gap + width and the copper edge gap is exactly `gap`.
(Once got burned: gap was applied center-to-center, so gap == width produced two
tracks touching edge-to-edge — a hard short on the board.)
"""

from dataclasses import dataclass
from typing import Any

from faebryk.exporters.pcb.layout.layout_plan import (
    BundleStage,
    DiffLane,
    LayoutPlanError,
    Trunk,
)

# fallback when a bundle config gives no track_width (a single lane's slot width)
_DEFAULT_TRACK_WIDTH = 0.1


@dataclass
class LaneOffset:
    """One routed member's slot in the cross-section: its signed perpendicular
    offset from the centerline (0 == centerline), its width, and its L1 role."""

    net: str
    offset: float  # signed, perpendicular to the centerline
    width: float
    kind: str  # "single" | "diff"
    diff_partner: str | None = None  # the paired net, for a diff member
    polarity: str | None = None  # "P" | "N" | None


def cross_section_offsets(
    lanes: list, spacing: float, *, default_width: float
) -> list[LaneOffset]:
    """The per-segment cross-section: members in lane order (diff P before N), each
    with its signed offset under the pinned edge-packed-then-centered convention.

    `spacing` is the inter-lane edge gap for THIS segment (the trunk varies it per
    vertex); `default_width` is the bundle default a single lane (and a diff lane
    with no explicit width) takes."""
    # slot widths: single = its width; diff = 2*width + gap (the pair envelope:
    # two tracks plus the edge-to-edge gap between them)
    slots: list[tuple[Any, float, float]] = []  # (lane, slot_width, track_width)
    for lane in lanes:
        if isinstance(lane, DiffLane):
            if lane.gap is None:
                # normally filled from rules.diff_pair_gap by LayoutPlan._apply_rules;
                # a direct caller with an unfilled lane must not get silent geometry.
                raise LayoutPlanError(
                    f"diff lane {lane.diff}: gap is unset (no explicit gap and no "
                    "rules.diff_pair_gap was applied)"
                )
            w = lane.width if lane.width is not None else default_width
            slots.append((lane, 2 * w + lane.gap, w))
        else:
            slots.append((lane, default_width, default_width))

    # pack left-to-right; `spacing` is added BETWEEN slots only (not after last)
    cursor = 0.0
    centers: list[float] = []
    for i, (_lane, slot_w, _w) in enumerate(slots):
        centers.append(cursor + slot_w / 2)
        cursor += slot_w
        if i != len(slots) - 1:
            cursor += spacing
    midpoint = cursor / 2  # left edge is 0, right edge is `cursor` ⇒ center on mid

    out: list[LaneOffset] = []
    for (lane, _slot_w, w), center in zip(slots, centers):
        c = center - midpoint
        if isinstance(lane, DiffLane):
            p, n = lane.diff
            # center-to-center = gap + width ⇒ copper EDGE gap is exactly lane.gap
            half_pitch = (lane.gap + w) / 2
            out.append(
                LaneOffset(p, c - half_pitch, w, "diff", diff_partner=n, polarity="P")
            )
            out.append(
                LaneOffset(n, c + half_pitch, w, "diff", diff_partner=p, polarity="N")
            )
        else:
            out.append(LaneOffset(lane.net, c, w, "single"))
    return out


def _trunk_segments(trunk: Trunk) -> list[dict]:
    """One entry per centerline segment: equal-spacing endpoints = a rigid segment
    (fixed offsets); unequal = a transition (the cross-section morphs between the
    two endpoint profiles). This is the locus E auto-routes."""
    segs: list[dict] = []
    for a, b in zip(trunk.centerline, trunk.centerline[1:]):
        if a.spacing == b.spacing:
            segs.append({"kind": "rigid", "spacing": a.spacing})
        else:
            segs.append(
                {"kind": "transition", "spacing_a": a.spacing, "spacing_b": b.spacing}
            )
    return segs


def bundle_artifact(bundle: BundleStage, ir: dict[str, Any]) -> dict:
    """The `<t>.layout_plan.json` fragment E1 reads: the segmented trunk, the
    COMPUTED ordered member table (offsets from `cross_section_offsets` — not a
    second source), the breakout order, and the resolved nets (bridge②, in member
    order). JSON-serializable. Missing member resolution is loud (S5a)."""
    cfg = bundle.config
    default_width = (
        cfg.track_width if cfg.track_width is not None else _DEFAULT_TRACK_WIDTH
    )
    # the nominal member offsets use the entry (first vertex) spacing; per-segment
    # variation is carried by `trunk.segments` (E recomputes from each spacing).
    entry_spacing = bundle.trunk.centerline[0].spacing
    offsets = cross_section_offsets(
        bundle.lanes, entry_spacing, default_width=default_width
    )
    members = [
        {
            "net": o.net,
            "offset": o.offset,
            "width": o.width,
            "kind": o.kind,
            "diff_partner": o.diff_partner,
            "polarity": o.polarity,
        }
        for o in offsets
    ]
    member_nets = [o.net for o in offsets]

    trunk = {
        "centerline": [
            {"at": [float(v.at[0]), float(v.at[1])], "spacing": v.spacing}
            for v in bundle.trunk.centerline
        ],
        "segments": _trunk_segments(bundle.trunk),
    }

    signal_nets: dict[str, str] = ir["signal_nets"]
    resolved: list[str] = []
    for m in member_nets:
        if m not in signal_nets:
            raise LayoutPlanError(
                f"bundle {bundle.name!r}: member {m!r} is not a resolvable ato "
                "signal address (not in bridge② signal_nets)"
            )
        resolved.append(signal_nets[m])

    breakouts = [
        {"at": bo.at, "order": list(bo.order) if bo.order is not None else member_nets}
        for bo in bundle.breakouts
    ]

    return {
        "type": "bundle",
        "name": bundle.name,
        "trunk": trunk,
        "members": members,
        "breakouts": breakouts,
        "resolved_nets": resolved,
    }
