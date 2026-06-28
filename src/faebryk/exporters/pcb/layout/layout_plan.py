# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
layout_plan — the layout.yaml intent model (BACKLOG §D, task D2).

`layout.yaml` is the layout-INTENT source, a peer of the .ato circuit source and
the .kicad_pcb geometry source. This module parses & validates it into a typed
`LayoutPlan`, the single object every downstream stage consumes: D3 (rule areas)
and E1 (the router dispatch). The contract is pinned by
`test/exporters/pcb/layout/test_layout_plan_contract.py`.

== FORM: declarative placement + ordered routing pipeline (the architecture) ==

`layout.yaml` is DATA, not a script — deliberately. It has two halves with
DIFFERENT semantics:

  * `rooms` is DECLARATIVE — a set of placement regions keyed by ato address.
    Order is irrelevant; reordering rooms changes nothing.

  * `route_stages` is an ORDERED PIPELINE — stages execute in list order over
    accumulating board state. Routing is inherently stateful: an earlier stage
    lays copper the later ones must route around, so the list ORDER IS PART OF
    THE INTENT. Reordering stages is a semantic change, not a cosmetic one.
    `route_stages` is a `list` (ordered, never a set) precisely to carry this,
    and the order is preserved verbatim through parse + resolve (pinned by
    `test_route_stage_order_is_preserved`).

This is "imperative in effect, declarative in form" — the same shape as a
Dockerfile, an Ansible playbook, or a sequence of SQL migrations: an ordered run
of effectful steps expressed as reviewable data. We deliberately do NOT make the
source of truth a scripting dialect (e.g. Tcl). The data form is what buys the
project's load-bearing invariants: deterministic regeneration, semantic
diff/review, parse-time loud validation (S5a), and safe machine editing by the
PCB-layout SKILL. The IMPERATIVE / feedback half of the workflow — "this route
failed, the space is gone, now what?" — lives OUTSIDE the file, in the
build → structured-diagnostics → SKILL-edits-plan → rebuild loop: the agent is
the interpreter, `layout.yaml` is the program-as-data it rewrites. In-file
control flow (branch/loop on runtime results) would only be warranted if a
routing decision had to depend on a value unknowable at authoring time; PCB
intent does not — a board is manufactured, hence static. (Assembly variants —
Altium-style DNP / BOM swaps, rarely route changes — are out of scope here and
orthogonal: they belong to BOM/part selection, not layout intent, and can be
layered on later without touching this model.)

== THE LOAD-BEARING FACTS =================================================

The contract is pinned by the D2 test module — read its docstring for the full
protocol; the load-bearing facts:

  * Everything is addressed by **ato address**, never net name or designator —
    v10 has no net table and designators are unstable. `resolve_nets` is the only
    address→kicad-net step, and it is *verbatim* delegation to the IR's bridge②
    (`ir["signal_nets"]`), never a reinvented lookup. NB authoring: instance
    addresses carry indices (`sub_chains[0]...`); the `[` makes a YAML FLOW
    sequence (`nets: [a[0], b[0]]`) unparseable, so list nets in BLOCK form
    (`nets:` / `  - a[0]`) — a flow item would need quoting.

  * Loud-or-nothing (S5a): unknown keys (every level), `mode` outside
    {diff, single}, partial/degenerate room geometry, and unresolvable nets are
    all hard errors — never silently swallowed or coerced.

  * `GridRouteOverride` exposes a curated subset of the *router entry kwargs*
    (the names `batch_route` / `batch_route_diff_pairs` accept), NOT the
    `GridRouteConfig` dataclass fields — the entries take flat kwargs and
    translate some names internally. The drift guard test pins every override
    field to the union of those two signatures.

  * The two router entries take DIFFERENT kwargs: `guide_corridor_*` etc. are
    single-only, `diff_pair_*`/`fix_polarity`/`gnd_via_*` are diff-only. A stage's
    `config` key must be valid for the entry its `mode` dispatches to — a
    wrong-mode key is rejected AT PARSE TIME (mode + config are both known then),
    never passed to a kwarg expansion that would TypeError in E1.
"""

from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    model_validator,
)


class LayoutPlanError(Exception):
    """A layout.yaml that cannot be represented or resolved (loud-or-nothing)."""


# ---------------------------------------------------------------------------
# shared 2D-polygon helpers (used by Room.polygon and board.outline). A boundary
# is a list of (x, y) vertices; "self-intersecting" = any two NON-adjacent edges
# cross. Kept dependency-free (no shapely): O(n²) over a handful of points.
# ---------------------------------------------------------------------------
def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """True if open segment ab properly crosses open segment cd."""

    def orient(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    # strict straddle on both sides ⇒ a proper crossing (shared endpoints of
    # adjacent edges, handled by the caller's index skipping, are not a crossing).
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _polygon_self_intersects(points: list[tuple[float, float]]) -> bool:
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # skip adjacent edges (they legitimately share a vertex) and the
            # wrap-around adjacency between the last and first edge.
            if j == i or j == (i + 1) % n or i == (j + 1) % n:
                continue
            if _segments_intersect(*edges[i], *edges[j]):
                return True
    return False


def _validate_polygon(points: list[tuple[float, float]], where: str) -> None:
    """Loud-or-nothing: a polygon must have ≥ 3 points and be simple (S5a)."""
    if len(points) < 3:
        raise ValueError(f"{where}: polygon needs ≥ 3 points, got {len(points)}")
    if _polygon_self_intersects([tuple(p) for p in points]):
        raise ValueError(f"{where}: polygon is self-intersecting (must be simple)")


# ---------------------------------------------------------------------------
# GridRouteOverride — a curated subset of the router ENTRY kwargs.
#
# Field names are the kwargs `batch_route` / `batch_route_diff_pairs` accept (the
# router builds a GridRouteConfig internally, translating some names). The drift
# guard test (D2.4) pins every field below to the union of those two signatures.
#
# Mode partition (D2.5): the two entries accept different kwargs. The sets below
# are the curated mode-exclusive knobs; everything else on the model is shared
# (accepted by both entries). Pinned against the live router by D2.5's premise
# guard — `guide_corridor_enabled` must stay single-only, `diff_pair_gap`
# diff-only, or that test goes red.
# ---------------------------------------------------------------------------
_SINGLE_ONLY_KWARGS = frozenset(
    {
        "guide_corridor_enabled",
        "guide_corridor_layer",
        "guide_corridor_spacing",
        "power_nets",
        "power_nets_widths",
    }
)
_DIFF_ONLY_KWARGS = frozenset(
    {
        "diff_pair_gap",
        "diff_pair_intra_match",
        "fix_polarity",
        "gnd_via_enabled",
    }
)


class GridRouteOverride(BaseModel):
    """Per-stage router knobs. extra='forbid' ⇒ an unknown/typo'd key is loud."""

    model_config = ConfigDict(extra="forbid")

    # --- shared (accepted by both batch_route and batch_route_diff_pairs) ---
    track_width: float | None = None
    clearance: float | None = None
    via_size: float | None = None
    via_drill: float | None = None
    impedance: float | None = None
    keepout_enabled: bool | None = None
    keepout_layer: str | None = None
    length_match_groups: list[str] | None = None
    length_match_tolerance: float | None = None
    layers: list[str] | None = None

    # --- single-only (batch_route) ---
    guide_corridor_enabled: bool | None = None
    guide_corridor_layer: str | None = None
    guide_corridor_spacing: float | None = None
    power_nets: list[str] | None = None
    power_nets_widths: dict[str, float] | None = None

    # --- diff-only (batch_route_diff_pairs) ---
    diff_pair_gap: float | None = None
    diff_pair_intra_match: bool | None = None
    fix_polarity: bool | None = None
    gnd_via_enabled: bool | None = None


class Room(BaseModel):
    """A placement region == one footprint sheetname (= ato address, §C3)."""

    model_config = ConfigDict(extra="forbid")

    module: str
    origin: tuple[float, float] | None = None
    size: tuple[float, float] | None = None
    polygon: list[tuple[float, float]] | None = None
    rotation: float = 0.0
    layers: list[str] = Field(default_factory=list)
    anchor: str | None = None

    @model_validator(mode="after")
    def _validate_geometry(self) -> "Room":
        # origin/size are all-or-nothing: both ⇒ explicit rectangle, neither ⇒
        # derived bbox (D3). Exactly one is ambiguous and must be loud.
        if (self.origin is None) != (self.size is None):
            raise ValueError(
                f"room {self.module!r}: origin and size must be given together "
                "(both = explicit rectangle, neither = derived bbox); exactly one "
                "is ambiguous"
            )
        if self.size is not None:
            w, h = self.size
            if w <= 0 or h <= 0:
                raise ValueError(
                    f"room {self.module!r}: size must be positive, got {self.size!r}"
                )
        # polygon is the THIRD geometry form, mutually exclusive with origin/size:
        # a room is an explicit rectangle XOR an explicit polygon XOR a derived
        # bbox — never two at once (S5a: multiple geometries are ambiguous).
        if self.polygon is not None:
            if self.origin is not None:
                raise ValueError(
                    f"room {self.module!r}: polygon and origin/size are mutually "
                    "exclusive geometries"
                )
            _validate_polygon(self.polygon, f"room {self.module!r}")
        return self


class Placement(BaseModel):
    """A per-component pose, keyed by ato ADDRESS (never designator, §C). This is
    the TEXT authority for a footprint's `(at x y rot)` + side in the derived
    .kicad_pcb, replacing the build-time auto-grid spread (transformer.py:176 →
    :2013-2080). `at` is ROOM-RELATIVE by default (composed through the room
    origin by `resolve_placement`); set `absolute` to land board-absolute coords
    verbatim (the escape hatch)."""

    model_config = ConfigDict(extra="forbid")

    component: str  # ato address
    at: tuple[float, float]
    rotation: float = 0.0
    side: Literal["F", "B"] = "F"
    absolute: bool = False  # at is board-absolute (skip room composition)


def resolve_placement(
    placement: Placement, room: "Room | None"
) -> tuple[float, float]:
    """The board-ABSOLUTE (x, y) for a placement (the pure room-relative oracle).

    An `absolute` placement lands its `at` verbatim (the room is ignored). A
    room-relative placement is composed through the room ORIGIN — and a
    room-relative placement with no room origin to compose against is loud (S5a:
    no silent (0,0) base)."""
    if placement.absolute:
        return placement.at
    if room is None or room.origin is None:
        raise LayoutPlanError(
            f"placement {placement.component!r}: room-relative coordinates need a "
            "room with an origin to compose against (or mark it absolute)"
        )
    ox, oy = room.origin
    x, y = placement.at
    return (ox + x, oy + y)


def resolve_component_pose(
    placement: "Placement | None",
    reuse_pose: tuple[float, float] | None,
    room: "Room | None" = None,
) -> tuple[float, float]:
    """The final board-absolute (x, y) for a component, pinning the
    placements-vs-reuse PRIORITY: a TEXT `placement` OVERRIDES the reuse pose
    (text is authoritative over both the build-time auto-grid spread and a pose
    read back from a reuse board); with no text placement the reuse pose FALLS
    THROUGH; with neither it is loud (no silent (0,0))."""
    if placement is not None:
        return resolve_placement(placement, room)
    if reuse_pose is not None:
        return reuse_pose
    raise LayoutPlanError(
        "component has neither a text placement nor a reuse pose to fall back on"
    )


class RouteStage(BaseModel):
    """One router invocation: a set of nets routed in a `mode` with `config`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    nets: list[str] = Field(default_factory=list)
    mode: Literal["diff", "single"]
    config: GridRouteOverride = Field(default_factory=GridRouteOverride)
    # Tier0 corridor-as-data (BACKLOG Tier0): a guide polyline the build draws onto
    # User.1 + flips `guide_corridor_enabled`, steering this stage's nets along it
    # (zero router change — reuses the router's native guide reader). Single-mode
    # only: the diff entry has no guide_corridor_* kwarg (§D2 mode exclusivity).
    corridor: list[tuple[float, float]] | None = None

    @model_validator(mode="after")
    def _validate_corridor(self) -> "RouteStage":
        if self.corridor is None:
            return self
        if self.mode != "single":
            raise ValueError(
                f"stage {self.name!r}: corridor is only valid for mode 'single' "
                "(the differential router entry has no guide_corridor_* knob)"
            )
        if len(self.corridor) < 2:
            raise ValueError(
                f"stage {self.name!r}: corridor needs >= 2 points to be a "
                f"polyline, got {len(self.corridor)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_mode_kwargs(self) -> "RouteStage":
        # a config key must be valid for the entry this stage's mode dispatches to
        # (the two entries take different kwargs). Check only EXPLICITLY-set keys —
        # defaults are never forwarded. Loud at parse, not a TypeError in E1.
        forbidden = (
            _DIFF_ONLY_KWARGS if self.mode == "single" else _SINGLE_ONLY_KWARGS
        )
        bad = set(self.config.model_fields_set) & forbidden
        if bad:
            entry = (
                "batch_route" if self.mode == "single" else "batch_route_diff_pairs"
            )
            raise ValueError(
                f"stage {self.name!r} (mode {self.mode!r} → {entry}): config keys "
                f"{sorted(bad)} are not accepted by that router entry "
                "(wrong-mode knob)"
            )
        return self


# ---------------------------------------------------------------------------
# BundleStage — the D-Tier2 BUS abstraction (BACKLOG §D-Tier2).
#
# A bundle is the unit the flat `route_stages` cannot express: ordered `lanes`
# (single OR diff) + a segmented `trunk` + exactly 2 `breakouts`. Its constraints
# are LEVELLED, never flattened — a diff lane stays a coupled pair (L1) inside the
# bundle (L2 only adds order + inter-lane spacing). The geometry SSOT (the
# cross-section the router materializes) lives in the sibling `bundle_geometry`
# module; this model only carries the validated intent. The contract is pinned by
# `test/exporters/pcb/layout/test_bundle_contract.py`.
# ---------------------------------------------------------------------------


class SingleLane(BaseModel):
    """One single-ended member of a bundle (= an ato signal address). Its width is
    the bundle default — a single lane carries no per-lane width."""

    model_config = ConfigDict(extra="forbid")

    net: str


class DiffLane(BaseModel):
    """One differential member of a bundle: a (P, N) pair routed as the intrinsic
    L1 coupling (REUSES route_diff downstream), never two parallel singles."""

    model_config = ConfigDict(extra="forbid")

    diff: tuple[str, str]  # (P addr, N addr) — EXACTLY 2 (tuple arity is loud)
    gap: float = Field(gt=0)  # intra-pair edge gap, > 0
    width: float | None = Field(default=None, gt=0)  # else bundle default
    impedance: float | None = Field(default=None, gt=0)


class TrunkVertex(BaseModel):
    """One centerline vertex: a board point + the inter-lane edge spacing there."""

    model_config = ConfigDict(extra="forbid")

    at: tuple[float, float]
    spacing: float = Field(gt=0)  # inter-lane edge gap, > 0


class SpacingOverride(BaseModel):
    """Override the inter-lane gap AFTER a named member (a non-uniform profile)."""

    model_config = ConfigDict(extra="forbid")

    after: str  # names an existing bundle member
    gap: float = Field(gt=0)


class Trunk(BaseModel):
    """The segmented centerline: >= 2 vertices. Adjacent vertices with equal
    spacing = a rigid segment; unequal = a transition (the neck-down)."""

    model_config = ConfigDict(extra="forbid")

    centerline: list[TrunkVertex] = Field(min_length=2)
    spacing_overrides: list[SpacingOverride] = Field(default_factory=list)


class Breakout(BaseModel):
    """A fanout region at one end of the bundle (a real room address). `order`, if
    given, is an explicit member permutation; else it is derived from lane order."""

    model_config = ConfigDict(extra="forbid")

    at: str  # a real room address (sheetname)
    order: list[str] | None = None  # a PERMUTATION of the members


class RipUpBudget(BaseModel):
    """Per-stage INTRA-stage rip-up budget — maps to the router's
    max_rip_up_count / ripped_route_avoidance_cost / _radius knobs. CROSS-stage
    prior copper is a free, un-rippable obstacle (BACKLOG 关键事实 16) and is NOT
    expressed here; bundle priority comes from STAGE ORDER."""

    model_config = ConfigDict(extra="forbid")

    max_rip_up_count: int | None = Field(default=None, ge=0)
    ripped_route_avoidance_cost: float | None = None
    ripped_route_avoidance_radius: float | None = None


class BundleRouteConfig(BaseModel):
    """Bundle-level router defaults. NOT GridRouteOverride: a bundle dispatches to
    `batch_route_bundle`, whose kwargs differ. Every field here must be a real
    `batch_route_bundle` kwarg (T-B1 AST drift guard). extra='forbid' ⇒ loud."""

    model_config = ConfigDict(extra="forbid")

    track_width: float | None = None
    clearance: float | None = None
    via_size: float | None = None
    via_drill: float | None = None
    impedance: float | None = None
    layers: list[str] | None = None


class BundleStage(BaseModel):
    """A bus: ordered lanes (single|diff) + a segmented trunk + exactly 2
    breakouts. The discriminated third stage type alongside RouteStage."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["bundle"]  # the union discriminator
    name: str
    lanes: list[SingleLane | DiffLane] = Field(min_length=1)
    trunk: Trunk
    breakouts: tuple[Breakout, Breakout]  # EXACTLY 2 (tuple arity is loud)
    config: BundleRouteConfig = Field(default_factory=BundleRouteConfig)
    rip_up: RipUpBudget | None = None

    def members(self) -> list[str]:
        """The flattened member net addresses in lane order, diff P before N — the
        bundle-global invariant order every downstream consumer reads."""
        out: list[str] = []
        for lane in self.lanes:
            if isinstance(lane, DiffLane):
                out.extend(lane.diff)
            else:
                out.append(lane.net)
        return out

    @model_validator(mode="after")
    def _validate_bundle(self) -> "BundleStage":
        members = self.members()
        member_set = set(members)
        # a spacing override must name a real member (no dangling profile point)
        for ov in self.trunk.spacing_overrides:
            if ov.after not in member_set:
                raise ValueError(
                    f"bundle {self.name!r}: spacing_overrides.after {ov.after!r} is "
                    "not a bundle member"
                )
        # an explicit breakout order must be a permutation of the members (and so
        # both ends necessarily cover the same set — self-consistent)
        for bo in self.breakouts:
            if bo.order is not None and sorted(bo.order) != sorted(members):
                raise ValueError(
                    f"bundle {self.name!r}: breakout {bo.at!r} order is not a "
                    f"permutation of the bundle members {members}"
                )
        return self


# ---------------------------------------------------------------------------
# board section — the board-LEVEL facts a pure-text end-to-end board needs
# (BACKLOG §D-Tier3). `outline` and `stackup` are peers of rooms/route_stages;
# every fact downstream consumes either has a schema here or is loud when absent.
# ---------------------------------------------------------------------------
class BoardOutline(BaseModel):
    """The board edge: an axis-aligned rectangle (`origin`/`size`) OR an explicit
    `polygon` (≥ 3 points, simple). No outline at all is a silent disaster
    downstream (obstacle_map.py:393-395 routes unbounded), so this schema exists
    to make the boundary authoritative."""

    model_config = ConfigDict(extra="forbid")

    origin: tuple[float, float] | None = None
    size: tuple[float, float] | None = None
    polygon: list[tuple[float, float]] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "BoardOutline":
        if (self.origin is None) != (self.size is None):
            raise ValueError(
                "board.outline: origin and size must be given together"
            )
        if self.polygon is not None:
            if self.origin is not None:
                raise ValueError(
                    "board.outline: polygon and origin/size are mutually exclusive"
                )
            _validate_polygon(self.polygon, "board.outline")
        if self.origin is None and self.polygon is None:
            raise ValueError(
                "board.outline: give either origin/size or a polygon"
            )
        if self.size is not None:
            w, h = self.size
            if w <= 0 or h <= 0:
                raise ValueError(
                    f"board.outline: size must be positive, got {self.size!r}"
                )
        return self


def outline_bounds(
    outline: BoardOutline | None,
) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) for an outline. A MISSING outline is loud — never
    a silently invented unbounded board (the obstacle_map.py:393-395 sign-off)."""
    if outline is None:
        raise LayoutPlanError(
            "board.outline is required: no silent unbounded board (copper would "
            "spill off-board, the fab boundary is unknowable)"
        )
    if outline.origin is not None and outline.size is not None:
        ox, oy = outline.origin
        w, h = outline.size
        return (ox, oy, ox + w, oy + h)
    pts = [tuple(p) for p in outline.polygon]  # type: ignore[union-attr]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


class StackupLayer(BaseModel):
    """One physical layer: a copper layer (name + optional thickness) or a
    dielectric (thickness + material + Er, all required — a complete stackup, not
    just a count)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["copper", "dielectric"]
    thickness: float | None = None
    material: str | None = None
    epsilon_r: float | None = None  # Er

    @model_validator(mode="after")
    def _validate(self) -> "StackupLayer":
        if self.thickness is not None and self.thickness <= 0:
            raise ValueError(
                f"stackup layer {self.name!r}: thickness must be > 0, got "
                f"{self.thickness!r}"
            )
        if self.type == "dielectric":
            # a dielectric without thickness/material/Er is an incomplete stackup
            # (= degenerate "just a count"); controlled impedance needs all three.
            missing = [
                k
                for k, v in (
                    ("thickness", self.thickness),
                    ("material", self.material),
                    ("epsilon_r", self.epsilon_r),
                )
                if v is None
            ]
            if missing:
                raise ValueError(
                    f"dielectric layer {self.name!r}: incomplete stackup — missing "
                    f"{missing} (a complete stackup carries thickness/material/Er, "
                    "never just a layer count)"
                )
        return self


class Stackup(BaseModel):
    """The ordered physical stack (top → bottom). The copper subset is the SINGLE
    authority for layer count (`stackup_layers`). Loud: ≥ 2 copper, unique names,
    a dielectric between every adjacent copper pair (complete, not a bare count)."""

    model_config = ConfigDict(extra="forbid")

    layers: list[StackupLayer] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate(self) -> "Stackup":
        names = [layer.name for layer in self.layers]
        if len(names) != len(set(names)):
            raise ValueError("stackup: layer names must be unique")
        coppers = [layer for layer in self.layers if layer.type == "copper"]
        if len(coppers) < 2:
            raise ValueError(
                f"stackup: need ≥ 2 copper layers, got {len(coppers)}"
            )
        # complete, not a count: two copper layers must be separated by ≥ 1
        # dielectric — adjacent coppers mean the dielectric was omitted.
        for a, b in zip(self.layers, self.layers[1:]):
            if a.type == "copper" and b.type == "copper":
                raise ValueError(
                    f"stackup: copper layers {a.name!r} and {b.name!r} are "
                    "adjacent — a dielectric (thickness/material/Er) must separate "
                    "them (incomplete stackup = degenerate layer count)"
                )
        return self


def stackup_layers(stackup: Stackup | None) -> list[str]:
    """The ordered copper layer names — the SINGLE authority for layer count (it
    feeds both the generated board's layer table and the router's `layers`). A
    MISSING stackup is loud: no silent layer-count default (S5a)."""
    if stackup is None or not stackup.layers:
        raise LayoutPlanError(
            "board.stackup is required for the layer count: no silent default "
            "(controlled impedance depends on the real stackup)"
        )
    return [layer.name for layer in stackup.layers if layer.type == "copper"]


class Board(BaseModel):
    """The board-level section: outline + complete stackup (both optional in the
    model — their REQUIRED-ness is enforced where consumed: outline by
    `outline_bounds`, stackup by `stackup_layers` and the impedance check)."""

    model_config = ConfigDict(extra="forbid")

    outline: BoardOutline | None = None
    stackup: Stackup | None = None


def _stage_requests_impedance(stage: Any) -> bool:
    """True if a stage asks for controlled impedance anywhere (stage config or, for
    a bundle, any diff lane). Used to enforce the stackup hard-dependency."""
    if stage.config.impedance is not None:
        return True
    if isinstance(stage, BundleStage):
        return any(
            isinstance(lane, DiffLane) and lane.impedance is not None
            for lane in stage.lanes
        )
    return False


def _stage_kind(v: Any) -> str:
    """Pick the route_stages union member: a `type: bundle` mapping (or a
    BundleStage instance) is the bundle stage; everything else is a RouteStage
    (which carries no `type` key — existing plans parse unchanged)."""
    if isinstance(v, BundleStage):
        return "bundle"
    if isinstance(v, RouteStage):
        return "route"
    if isinstance(v, dict):
        return "bundle" if v.get("type") == "bundle" else "route"
    return "route"


_Stage = Annotated[
    Union[
        Annotated[RouteStage, Tag("route")],
        Annotated[BundleStage, Tag("bundle")],
    ],
    Discriminator(_stage_kind),
]


class LayoutPlan(BaseModel):
    """The whole layout.yaml: placement rooms + ordered route stages."""

    model_config = ConfigDict(extra="forbid")

    rooms: list[Room] = Field(default_factory=list)
    placements: list[Placement] = Field(default_factory=list)
    board: Board | None = None
    route_stages: list[_Stage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_impedance_needs_stackup(self) -> "LayoutPlan":
        # impedance is a HARD dependency on a real stackup: a stage requesting
        # impedance with no board.stackup would silently fall back to a fixed
        # width (route.py:256-258) = impedance out of control. Reject at parse.
        has_stackup = self.board is not None and self.board.stackup is not None
        if has_stackup:
            return self
        for stage in self.route_stages:
            if _stage_requests_impedance(stage):
                raise LayoutPlanError(
                    f"stage {stage.name!r} requests controlled impedance but the "
                    "plan has no board.stackup — impedance needs a complete stackup "
                    "(else the router silently falls back to a fixed width)"
                )
        return self

    @model_validator(mode="after")
    def _validate_unique_stage_names(self) -> "LayoutPlan":
        # stage names are the addressing key: resolve_nets keys its result by name
        # (a duplicate would SILENTLY clobber the earlier stage's resolved nets),
        # and the `--up-to <name>` incremental breakpoint addresses by name. Reject
        # duplicates loudly at parse rather than let one stage shadow another.
        seen: set[str] = set()
        dups: list[str] = []
        for stage in self.route_stages:
            if stage.name in seen and stage.name not in dups:
                dups.append(stage.name)
            seen.add(stage.name)
        if dups:
            raise LayoutPlanError(
                f"route_stages have duplicate name(s) {sorted(dups)} — stage names "
                "must be unique (they key resolve_nets and the --up-to breakpoint)"
            )
        return self

    def resolve_nets(self, ir: dict[str, Any]) -> dict[str, list[str]]:
        """{stage name -> [kicad net name]} resolving each ato signal ADDRESS
        verbatim through the IR's bridge② (`ir["signal_nets"]`). No transformation,
        no fallback: an address absent from bridge② is loud (a bare net name or an
        address with no stable name, I4)."""
        signal_nets: dict[str, str] = ir["signal_nets"]
        resolved: dict[str, list[str]] = {}
        for stage in self.route_stages:
            # a bundle flattens its lanes (diff P before N); a RouteStage uses its
            # explicit net list. Both resolve verbatim through bridge②, in order.
            addrs = (
                stage.members() if isinstance(stage, BundleStage) else stage.nets
            )
            names: list[str] = []
            for addr in addrs:
                if addr not in signal_nets:
                    raise LayoutPlanError(
                        f"stage {stage.name!r}: net address {addr!r} is not a "
                        "resolvable ato signal address (not in bridge② signal_nets) "
                        "— give an ato signal address, not a bare net name"
                    )
                names.append(signal_nets[addr])
            resolved[stage.name] = names
        return resolved


def load_layout_plan(source: str | Path) -> LayoutPlan:
    """Parse & validate a layout.yaml into a `LayoutPlan`.

    `source` is either a `Path` (read from disk) or a `str` of YAML text. All
    validation is loud (LayoutPlanError or pydantic ValidationError)."""
    text = source.read_text() if isinstance(source, Path) else source
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        hint = ""
        # the most common authoring trap: ato addresses carry `[N]` indices, and a
        # YAML FLOW sequence `nets: [a[0]]` treats `[` as a nested sequence start.
        if isinstance(e, yaml.parser.ParserError) and "flow sequence" in str(e):
            hint = (
                "\nhint: ato addresses contain '[' (instance indices); list nets as "
                "a BLOCK sequence (`nets:` then `  - a[0]`), not flow `[a[0], ...]`."
            )
        raise LayoutPlanError(f"layout.yaml is not valid YAML: {e}{hint}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise LayoutPlanError(
            f"layout.yaml top level must be a mapping, got {type(data).__name__}"
        )
    return LayoutPlan.model_validate(data)
