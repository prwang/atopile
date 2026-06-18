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
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LayoutPlanError(Exception):
    """A layout.yaml that cannot be represented or resolved (loud-or-nothing)."""


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
        return self


class RouteStage(BaseModel):
    """One router invocation: a set of nets routed in a `mode` with `config`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    nets: list[str] = Field(default_factory=list)
    mode: Literal["diff", "single"]
    config: GridRouteOverride = Field(default_factory=GridRouteOverride)

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


class LayoutPlan(BaseModel):
    """The whole layout.yaml: placement rooms + ordered route stages."""

    model_config = ConfigDict(extra="forbid")

    rooms: list[Room] = Field(default_factory=list)
    route_stages: list[RouteStage] = Field(default_factory=list)

    def resolve_nets(self, ir: dict[str, Any]) -> dict[str, list[str]]:
        """{stage name -> [kicad net name]} resolving each ato signal ADDRESS
        verbatim through the IR's bridge② (`ir["signal_nets"]`). No transformation,
        no fallback: an address absent from bridge② is loud (a bare net name or an
        address with no stable name, I4)."""
        signal_nets: dict[str, str] = ir["signal_nets"]
        resolved: dict[str, list[str]] = {}
        for stage in self.route_stages:
            names: list[str] = []
            for addr in stage.nets:
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
