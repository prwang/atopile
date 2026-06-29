# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_bundle_contract — stage D-Tier2 acceptance contract (BACKLOG §D-Tier2).

bundle 总线传输（mixed single/diff）+ the `batch_route_bundle` D/E boundary. This
is the tests-first S0 ratchet: written BEFORE the implementation, every test is a
strict-xfail that flips green by *removing/deactivating* its gate when the feature
lands. A test that XPASSes while the feature is absent is a bug in the test (S0).

== THE ABSTRACTION (the why) ==============================================

The current `route_stages` minimum unit is "one net routed end-to-end" — it
cannot express a BUS: two fanouts plus a parallel, same-shape trunk in between
(the core hand-routing pattern; a DDR byte lane is single+diff MIXED — N DQ
single-ended + clock/strobe diff pairs, each with its own width/impedance/intra
gap). A bundle is:

    bundle = ordered `lanes` (each single OR diff, each carrying its own width)
           + a segmented `trunk` (centerline vertices, each with a `spacing`)
           + exactly 2 `breakouts` (the fanout regions at each end).

LEVELLED constraints, never flattened: L0 single net = track; L1 diff lane = the
intrinsic 2-net coupling (intra gap / skew / polarity) = REUSES `route_diff`; L2
bundle = ordered lanes + spacing profile + breakout. All in-scope constraints
hold simultaneously at every point ⇒ a diff lane inside a bundle NEVER degrades
into two independent parallel singles; it stays a coupled pair end to end. The
bundle layer only adds order + inter-lane spacing; it may not dissolve L1.

trunk = a sequence of RIGID / TRANSITION segments. `lanes` (members / ORDER /
per-diff-lane intra gap·width) is a bundle-global invariant; only `spacing` varies
along the centerline, per VERTEX. Adjacent vertices with equal spacing ⇒ a rigid
segment (offsets fixed, no search); unequal ⇒ a transition segment (order fixed,
spacing morphs — the neck-down at a corner). Transitions are the ONLY auto-route
locus in the trunk, and there both endpoints' cross-sections are pinned (profile
A / profile B); E only searches the bounded morph between them.

== THE PINNED INTERFACE (this contract freezes the names) =================

`layout_plan` gains a discriminated third stage type alongside RouteStage
(mode: single|diff):

    BundleStage
      type:      Literal["bundle"]          # the union discriminator
      name:      str
      lanes:     list[SingleLane | DiffLane] # ORDERED, bundle-global invariant
      trunk:     Trunk
      breakouts: tuple[Breakout, Breakout]   # EXACTLY 2
      config:    BundleRouteConfig           # bundle defaults (NOT GridRouteOverride)
      rip_up:    RipUpBudget | None          # per-stage INTRA-stage rip-up budget

    SingleLane   net: str                          # one ato signal address
    DiffLane     diff: tuple[str, str]             # (P addr, N addr) — EXACTLY 2
                 gap: float                        # intra-pair, > 0
                 width: float | None = None        # else bundle default
                 impedance: float | None = None

    Trunk        centerline: list[TrunkVertex]     # >= 2 vertices
                 spacing_overrides: list[SpacingOverride] = []
    TrunkVertex  at: tuple[float, float]
                 spacing: float                    # > 0, inter-lane edge gap
    SpacingOverride after: str  gap: float         # `after` names an existing lane
    Breakout     at: str                           # a real room address (sheetname)
                 order: list[str] | None = None    # a PERMUTATION of the members

`rip_up` (per-stage, INTRA-stage rip-up budget only) maps to the router knobs
max_rip_up_count / ripped_route_avoidance_cost / _radius. CROSS-stage prior
copper is a hard, un-rippable obstacle the router gives for free — it is NOT
expressed here (BACKLOG 关键事实 16); bundle priority comes from STAGE ORDER.

The geometry SSOT lives in a sibling module `bundle_geometry`:

    LaneOffset                 # one routed member's cross-section slot
      net:          str
      offset:       float      # signed, perpendicular to centerline; 0 == centerline
      width:        float
      kind:         "single" | "diff"
      diff_partner: str | None # the paired net, for a diff member
      polarity:     "P" | "N" | None

    cross_section_offsets(lanes, spacing, *, default_width) -> list[LaneOffset]
        # the per-segment cross-section, members in lane order (diff P before N).

    bundle_artifact(bundle, ir) -> dict
        # the <t>.layout_plan.json fragment E1 reads: segmented trunk + computed
        # offsets + ordered member table + breakout order + resolved nets.

CROSS-SECTION CONVENTION (pinned — makes the oracle hand-computable):
lanes are packed in declared order along the axis perpendicular to the centerline;
`spacing` is the EDGE-TO-EDGE gap between adjacent lane slots (so a lane's WIDTH
shifts every downstream member's offset — required by T-A3's mutation self-check).
A single lane's slot width = its width; a diff lane's slot width = gap + width.
The whole packing is then centered about offset 0 (subtract the slot-envelope
midpoint), so the cross-section is symmetric about the centerline. Within a diff
slot, P sits at slot_center - gap/2, N at slot_center + gap/2.

== THE D/E BOUNDARY (bucket②, consumer-oracle, strict-xfail until E) =======

`batch_route_bundle` does NOT exist in the router yet, so D freezes its contract
and E implements to the same frozen shape (no "build E first, then retrofit the
schema"). Pinned in router file `route_bundle.py`:

    batch_route_bundle(
        trunk,            # segmented centerline + per-segment member offsets
        members,          # ordered table: net / offset / width / kind / (P|N pair)
        breakouts,        # 2x (part + order)
        track_width, clearance, via_size, via_drill, ...,  # shared geometry
        return_results=True,
    ) -> {..., "members": [{"net": ..., "routed": bool, "blocked": ...}, ...]}

result schema is `batch_route`-isomorphic, reported PER MEMBER (routed/blocked).
T-B1 AST-pins BundleRouteConfig.model_fields ⊆ that entry's kwargs (same method
as D2's GridRouteOverride drift guard); T-B2 calls it and pins the result shape.
T-B3..T-B6 (added with §E-Tier2) pin the BEHAVIOR: the per-member parallel offset
bus, the offset→track mutation self-check, the constant diff intra-pair gap (L1
never dissolves), and the transition neck-down morph — read off the
`members[*].polyline` the router emits. Bucket③ (real router on a real BOARD with
breakout fanout) lands in the §E1 runner e2e (test_layout_plan_runner_contract).

== THE RATCHET ============================================================

`_DT2_LANDED` = all bundle symbols import (model + geometry). Half-landed (a
renamed-only-halfway module) stays red. Bucket① is gated on it. Bucket② is gated
additionally on `_E_TIER2_LANDED` (router `route_bundle.batch_route_bundle`
present, AST-probed); if the router repo is wholly absent the bucket② bodies skip
LOUDLY (never silently pass). Negative (loud-or-nothing) tests are PAIRED with a
positive control parse of the base bundle, so before landing they fail on the
control (clean XFAIL) and cannot XPASS for the wrong reason (D2 rejecting every
`type: bundle` plan as an unknown stage).
"""

import ast
import json
import shutil
import subprocess

import pytest
from pydantic import ValidationError

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.util import repo_root

# ---------------------------------------------------------------------------
# S0 ratchet guard (D side): probe the not-yet-existing bundle symbols.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.bundle_geometry import (  # type: ignore
        LaneOffset,  # noqa: F401
        bundle_artifact,
        cross_section_offsets,
    )
    from faebryk.exporters.pcb.layout.layout_plan import (  # type: ignore
        Breakout,
        BundleRouteConfig,
        BundleStage,
        DiffLane,
        LayoutPlan,
        LayoutPlanError,
        SingleLane,
        SpacingOverride,
        Trunk,
        TrunkVertex,
        load_layout_plan,
    )

    _DT2_LANDED = True
except Exception:  # module / symbol not present yet
    _DT2_LANDED = False

    class LayoutPlanError(Exception):  # placeholder so raises-tuples stay well-formed
        ...

    def _unlanded(*_a, **_k):
        # a distinct error type pytest.raises(_LOUD) will NOT catch, so an unlanded
        # D-Tier2 produces a clean XFAIL (never an accidental XPASS).
        raise RuntimeError("D-Tier2 bundle not landed (S0 ratchet)")

    LayoutPlan = SingleLane = DiffLane = Trunk = TrunkVertex = _unlanded
    Breakout = SpacingOverride = BundleStage = BundleRouteConfig = _unlanded
    load_layout_plan = cross_section_offsets = bundle_artifact = _unlanded

needs_dt2 = pytest.mark.xfail(
    not _DT2_LANDED,
    reason="D-Tier2 bundle model/geometry not landed (S0 ratchet)",
    strict=True,
)

# loud errors the validation paths may raise (named D error OR pydantic's own)
_LOUD = (LayoutPlanError, ValidationError)


# ---------------------------------------------------------------------------
# S0 ratchet guard (D/E boundary): AST-probe the router's batch_route_bundle.
# ---------------------------------------------------------------------------
_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_ROUTER_PRESENT = (_ROUTER_ROOT / "route.py").exists()  # repo cloned at all?


def _batch_route_bundle_kwargs() -> set[str] | None:
    """The REAL kwargs `batch_route_bundle` accepts, AST-parsed from the router
    file `route_bundle.py` (not imported: the router pulls in scipy + a rust ext).
    None if the router repo, the file, or the function is absent — i.e. E-Tier2 has
    not landed."""
    path = _ROUTER_ROOT / "route_bundle.py"
    if not path.exists():
        return None
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == "batch_route_bundle":
            a = node.args
            return {p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)}
    return None


_E_TIER2_LANDED = _batch_route_bundle_kwargs() is not None

needs_e_tier2 = pytest.mark.xfail(
    not (_DT2_LANDED and _E_TIER2_LANDED),
    reason="E-Tier2 batch_route_bundle (route_bundle.py) not landed (S0 ratchet)",
    strict=True,
)


# ===========================================================================
# fixtures: the shared mixed-DDR bundle (8 DQ single + 2 clock/strobe diff,
# with a corner neck-down: rigid–transition–rigid trunk) + a minimal base.
# ===========================================================================
def _ddr_yaml() -> str:
    """8 single DQ + 2 diff (dqs, ck), interleaved; 4 centerline vertices with
    spacings [0.5, 0.5, 0.3, 0.3] = rigid → transition → rigid."""
    lanes = []
    order_idx = 0
    member_order: list[str] = []  # flattened net order, diff P before N
    # dq0..dq3, dqs(diff), dq4..dq7, ck(diff)
    for i in range(4):
        lanes.append(f"      - net: top.ddr.dq[{i}]")
        member_order.append(f"top.ddr.dq[{i}]")
    lanes.append("      - diff: [top.ddr.dqs_p, top.ddr.dqs_n]")
    lanes.append("        gap: 0.15")
    lanes.append("        width: 0.12")
    lanes.append("        impedance: 90")
    member_order += ["top.ddr.dqs_p", "top.ddr.dqs_n"]
    for i in range(4, 8):
        lanes.append(f"      - net: top.ddr.dq[{i}]")
        member_order.append(f"top.ddr.dq[{i}]")
    lanes.append("      - diff: [top.ddr.ck_p, top.ddr.ck_n]")
    lanes.append("        gap: 0.18")
    lanes.append("        width: 0.12")
    member_order += ["top.ddr.ck_p", "top.ddr.ck_n"]
    order_idx = 0  # noqa: F841 (kept for readability of the construction above)

    # breakout B carries an explicit order = a real permutation of the members
    bo_order = list(reversed(member_order))
    bo_block = "".join(f"          - {n}\n" for n in bo_order)

    yaml = (
        # the dqs/ck lanes carry controlled impedance ⇒ a complete stackup is a
        # hard dependency (TS5): declare one so the plan parses.
        "board:\n"
        "  stackup:\n"
        "    layers:\n"
        "      - {name: F.Cu, type: copper, thickness: 0.035}\n"
        "      - {name: d1, type: dielectric, thickness: 0.2, material: FR4,"
        " epsilon_r: 4.5}\n"
        "      - {name: B.Cu, type: copper, thickness: 0.035}\n"
        "route_stages:\n"
        "  - type: bundle\n"
        "    name: ddr_byte0\n"
        "    lanes:\n" + "\n".join(lanes) + "\n"
        "    trunk:\n"
        "      centerline:\n"
        "        - at: [0, 0]\n          spacing: 0.5\n"
        "        - at: [10, 0]\n          spacing: 0.5\n"
        "        - at: [12, 2]\n          spacing: 0.3\n"
        "        - at: [12, 12]\n          spacing: 0.3\n"
        "    breakouts:\n"
        "      - at: top.ddr\n"
        "      - at: top.mcu\n"
        "        order:\n" + bo_block +
        "    config:\n"
        "      track_width: 0.1\n"
        "      clearance: 0.1\n"
    )
    return yaml


# member order the DDR fixture flattens to (used by several tests as the oracle)
_DDR_MEMBERS = (
    [f"top.ddr.dq[{i}]" for i in range(4)]
    + ["top.ddr.dqs_p", "top.ddr.dqs_n"]
    + [f"top.ddr.dq[{i}]" for i in range(4, 8)]
    + ["top.ddr.ck_p", "top.ddr.ck_n"]
)

# a minimal valid bundle: 1 single + 1 diff, rigid trunk, 2 breakouts.
_BASE_BUNDLE = (
    "route_stages:\n"
    "  - type: bundle\n"
    "    name: b\n"
    "    lanes:\n"
    "      - net: top.a.x\n"
    "      - diff: [top.d.p, top.d.n]\n"
    "        gap: 0.15\n"
    "        width: 0.12\n"
    "    trunk:\n"
    "      centerline:\n"
    "        - at: [0, 0]\n          spacing: 0.5\n"
    "        - at: [10, 0]\n          spacing: 0.5\n"
    "    breakouts:\n"
    "      - at: top.a\n"
    "      - at: top.d\n"
    "    config:\n"
    # track_width != bundle_geometry._DEFAULT_TRACK_WIDTH (0.1) so T-A5 proves the
    # config→geometry seam: an impl that ignores config.track_width gives different
    # single-lane offsets and FAILS (no blind default-collision pass).
    "      track_width: 0.2\n"
)
_BASE_MEMBERS = ["top.a.x", "top.d.p", "top.d.n"]


def _plan(yaml_text: str) -> "LayoutPlan":
    return load_layout_plan(yaml_text)


def _ir(signal_nets: dict[str, str]) -> dict:
    return {
        "layout_ir_version": 2,
        "components": {},
        "nets": {},
        "rooms": {},
        "signal_nets": signal_nets,
    }


# ===========================================================================
# T-A1 — schema 正向: the mixed DDR fixture parses into typed objects, with
# lane order/types, segment spacings and breakout order asserted FIELD BY FIELD
# (not "parsed without error"). BACKLOG D-Tier2 桶① T-A1.
# ===========================================================================
@needs_dt2
def test_ddr_fixture_parses_field_by_field():
    plan = _plan(_ddr_yaml())
    assert isinstance(plan, LayoutPlan)
    (stage,) = plan.route_stages
    assert isinstance(stage, BundleStage)
    assert stage.type == "bundle"
    assert stage.name == "ddr_byte0"

    # lanes: order + type discrimination are the bundle-global invariant
    assert len(stage.lanes) == 10  # 8 single + 2 diff
    kinds = [
        "diff" if isinstance(la, DiffLane) else "single" for la in stage.lanes
    ]
    assert kinds == ["single"] * 4 + ["diff"] + ["single"] * 4 + ["diff"]
    dqs = stage.lanes[4]
    assert isinstance(dqs, DiffLane)
    assert tuple(dqs.diff) == ("top.ddr.dqs_p", "top.ddr.dqs_n")
    assert dqs.gap == 0.15 and dqs.width == 0.12 and dqs.impedance == 90
    assert isinstance(stage.lanes[0], SingleLane)
    assert stage.lanes[0].net == "top.ddr.dq[0]"

    # trunk: segmented centerline, per-vertex spacing verbatim (rigid/transition)
    assert isinstance(stage.trunk, Trunk)
    assert all(isinstance(v, TrunkVertex) for v in stage.trunk.centerline)
    assert [v.spacing for v in stage.trunk.centerline] == [0.5, 0.5, 0.3, 0.3]
    assert [tuple(v.at) for v in stage.trunk.centerline] == [
        (0, 0), (10, 0), (12, 2), (12, 12)
    ]

    # breakouts: exactly 2; A derives order (None), B carries an explicit perm
    assert len(stage.breakouts) == 2
    a, b = stage.breakouts
    assert isinstance(a, Breakout) and a.at == "top.ddr" and a.order is None
    assert b.at == "top.mcu"
    assert b.order == list(reversed(_DDR_MEMBERS))


# ===========================================================================
# T-A2 — schema 负向 (loud-or-nothing, S5a): each malformed bundle raises, and
# the base bundle (the positive control) parses — PAIRED so it cannot XPASS for
# the wrong reason. BACKLOG D-Tier2 桶① T-A2.
# ===========================================================================
def _mutual(*lines: str) -> str:
    return _BASE_BUNDLE + "".join(lines)


_BAD_BUNDLES = {
    # diff lane with the wrong arity (not exactly 2 nets)
    "diff_one_net": _BASE_BUNDLE.replace("[top.d.p, top.d.n]", "[top.d.p]"),
    "diff_three_net": _BASE_BUNDLE.replace(
        "[top.d.p, top.d.n]", "[top.d.p, top.d.n, top.d.x]"
    ),
    # non-positive geometry
    "gap_zero": _BASE_BUNDLE.replace("gap: 0.15", "gap: 0"),
    "width_neg": _BASE_BUNDLE.replace("width: 0.12", "width: -0.1"),
    "spacing_zero": _BASE_BUNDLE.replace("spacing: 0.5", "spacing: 0", 1),
    # centerline with a single vertex (< 2)
    "centerline_one_vertex": (
        "route_stages:\n  - type: bundle\n    name: b\n    lanes:\n"
        "      - net: top.a.x\n    trunk:\n      centerline:\n"
        "        - at: [0, 0]\n          spacing: 0.5\n"
        "    breakouts:\n      - at: top.a\n      - at: top.a\n"
        "    config:\n      track_width: 0.1\n"
    ),
    # spacing_overrides.after names a lane that is not a member (dangling)
    "spacing_override_dangling": _mutual(
        "    trunk_spacing_overrides_placeholder: 0\n"
    ),  # replaced below to attach under trunk correctly
    # explicit breakout order is not a permutation of the members (missing one)
    "order_not_permutation": _BASE_BUNDLE.replace(
        "      - at: top.d\n",
        "      - at: top.d\n        order:\n          - top.a.x\n          - top.d.p\n",
    ),
    # the two breakouts' orders cover different member sets (not self-consistent)
    "order_inconsistent_ends": _BASE_BUNDLE.replace(
        "      - at: top.a\n      - at: top.d\n",
        "      - at: top.a\n        order:\n          - top.a.x\n          - top.d.p\n"
        "          - top.d.n\n      - at: top.d\n        order:\n"
        "          - top.a.x\n          - top.d.p\n",
    ),
    # unknown key at the bundle level (extra='forbid')
    "unknown_key": _mutual("    bogus_key: 1\n"),
    # breakouts must be EXACTLY 2 — one is too few
    "one_breakout": _BASE_BUNDLE.replace("      - at: top.d\n", ""),
}

# attach the dangling spacing_override under trunk (needs to live inside trunk)
_BAD_BUNDLES["spacing_override_dangling"] = (
    "route_stages:\n  - type: bundle\n    name: b\n    lanes:\n"
    "      - net: top.a.x\n      - diff: [top.d.p, top.d.n]\n        gap: 0.15\n"
    "        width: 0.12\n    trunk:\n      centerline:\n"
    "        - at: [0, 0]\n          spacing: 0.5\n"
    "        - at: [10, 0]\n          spacing: 0.5\n"
    "      spacing_overrides:\n"
    "        - after: top.NOT_A_LANE\n          gap: 0.3\n"
    "    breakouts:\n      - at: top.a\n      - at: top.d\n"
    "    config:\n      track_width: 0.1\n"
)


@needs_dt2
@pytest.mark.parametrize("name", sorted(_BAD_BUNDLES))
def test_malformed_bundle_is_loud(name):
    # positive control: the base bundle parses (so a failure below is the DEFECT
    # being rejected, not bundles being unsupported). Pre-landing this raises ⇒
    # clean XFAIL; it can never XPASS.
    assert isinstance(_plan(_BASE_BUNDLE), LayoutPlan)
    with pytest.raises(_LOUD):
        _plan(_BAD_BUNDLES[name])


# ===========================================================================
# T-A3 — `cross_section_offsets` (geometry SSOT, the most critical D unit):
#   ① constructed oracle — hand-computed mixed cross-section == output;
#   ② mutation self-check — perturb width/gap/spacing ⇒ a downstream offset MUST
#      change (an offset that does not move == a blind test == a bug);
#   ③ centering invariant — the slot envelope is symmetric about the centerline;
#   ④ transition boundary — two spacings give two distinct, each-centered profiles.
# BACKLOG D-Tier2 桶① T-A3.
# ===========================================================================
def _tiny_lanes():
    """[single A, diff(P,N gap0.2 width0.15), single B]; default_width 0.1.

    Edge-packed, centered (see CROSS-SECTION CONVENTION):
        A      offset -0.725  width 0.10
        diff P offset -0.100  width 0.15
        diff N offset +0.100  width 0.15
        B      offset +0.725  width 0.10
    """
    return [
        SingleLane(net="n.a"),
        DiffLane(diff=("n.p", "n.n"), gap=0.2, width=0.15),
        SingleLane(net="n.b"),
    ]


def _by_net(offsets):
    return {o.net: o for o in offsets}


@needs_dt2
def test_cross_section_oracle():
    offs = cross_section_offsets(_tiny_lanes(), 0.5, default_width=0.1)
    # members in lane order, diff P before N
    assert [o.net for o in offs] == ["n.a", "n.p", "n.n", "n.b"]
    o = _by_net(offs)
    assert o["n.a"].offset == pytest.approx(-0.725)
    assert o["n.p"].offset == pytest.approx(-0.1)
    assert o["n.n"].offset == pytest.approx(0.1)
    assert o["n.b"].offset == pytest.approx(0.725)
    assert o["n.a"].width == pytest.approx(0.1)  # single → default_width
    assert o["n.p"].width == pytest.approx(0.15)  # diff → lane width
    # kind / polarity / partner
    assert o["n.a"].kind == "single" and o["n.a"].polarity is None
    assert o["n.p"].kind == "diff" and o["n.p"].polarity == "P"
    assert o["n.n"].polarity == "N"
    assert o["n.p"].diff_partner == "n.n" and o["n.n"].diff_partner == "n.p"


@needs_dt2
@pytest.mark.parametrize(
    "mutate,probe",
    [
        # wider spacing pushes the outer singles further out
        (lambda lanes: cross_section_offsets(lanes, 0.8, default_width=0.1), "n.a"),
        # wider single-lane default width shifts downstream members
        (lambda lanes: cross_section_offsets(lanes, 0.5, default_width=0.3), "n.b"),
    ],
    ids=["spacing", "default_width"],
)
def test_cross_section_mutation_propagates(mutate, probe):
    base = _by_net(cross_section_offsets(_tiny_lanes(), 0.5, default_width=0.1))
    mutated = _by_net(mutate(_tiny_lanes()))
    assert mutated[probe].offset != pytest.approx(base[probe].offset), (
        "an offset that does not move under a geometry change == a blind test"
    )


@needs_dt2
def test_cross_section_diff_gap_mutation_propagates():
    base = _by_net(cross_section_offsets(_tiny_lanes(), 0.5, default_width=0.1))
    wide = [
        SingleLane(net="n.a"),
        DiffLane(diff=("n.p", "n.n"), gap=0.4, width=0.15),  # gap 0.2 → 0.4
        SingleLane(net="n.b"),
    ]
    mut = _by_net(cross_section_offsets(wide, 0.5, default_width=0.1))
    # intra-pair offsets widen AND the outer singles move (slot grew)
    assert mut["n.p"].offset != pytest.approx(base["n.p"].offset)
    assert mut["n.a"].offset != pytest.approx(base["n.a"].offset)


@needs_dt2
def test_cross_section_is_centered():
    offs = cross_section_offsets(_tiny_lanes(), 0.5, default_width=0.1)
    left_edge = min(o.offset - o.width / 2 for o in offs)
    right_edge = max(o.offset + o.width / 2 for o in offs)
    # the slot envelope straddles the centerline symmetrically (offset 0)
    assert left_edge == pytest.approx(-right_edge)


@needs_dt2
def test_transition_boundary_profiles_differ_and_each_centered():
    """A transition segment is pinned by the cross-sections of its two endpoints
    (the differing spacings on either side). Both must be valid centered profiles
    and must differ (else nothing morphs)."""
    prof_a = cross_section_offsets(_tiny_lanes(), 0.5, default_width=0.1)
    prof_b = cross_section_offsets(_tiny_lanes(), 0.3, default_width=0.1)
    a, b = _by_net(prof_a), _by_net(prof_b)
    assert a["n.a"].offset != pytest.approx(b["n.a"].offset)  # genuinely morphs
    for prof in (prof_a, prof_b):
        le = min(o.offset - o.width / 2 for o in prof)
        re = max(o.offset + o.width / 2 for o in prof)
        assert le == pytest.approx(-re)  # each boundary is itself centered


# ===========================================================================
# T-A4 — resolve_nets (consumer-oracle): bundle members resolve IN ORDER through
# bridge② (ir["signal_nets"]); a diff lane's P AND N both resolve; a missing
# address is loud. Verbatim delegation, no transform. BACKLOG D-Tier2 桶① T-A4.
# ===========================================================================
@needs_dt2
def test_resolve_nets_flattens_bundle_in_order():
    bridge = {
        **{f"top.ddr.dq[{i}]": f"DQ{i}" for i in range(8)},
        "top.ddr.dqs_p": "DQS_P",
        "top.ddr.dqs_n": "DQS_N",
        "top.ddr.ck_p": "CK_P",
        "top.ddr.ck_n": "CK_N",
    }
    plan = _plan(_ddr_yaml())
    resolved = plan.resolve_nets(_ir(bridge))
    assert resolved == {"ddr_byte0": [bridge[m] for m in _DDR_MEMBERS]}


@needs_dt2
def test_resolve_nets_missing_bundle_member_is_loud():
    bridge = {"top.a.x": "X", "top.d.p": "DP"}  # top.d.n absent
    plan = _plan(_BASE_BUNDLE)
    with pytest.raises(_LOUD):
        plan.resolve_nets(_ir(bridge))


# ===========================================================================
# T-A5 — artifact schema: D's <t>.layout_plan.json fragment = the FILE interface
# E1 reads. Must carry the segmented trunk, the COMPUTED offsets (ordered member
# table), the breakout order, and resolved nets. BACKLOG D-Tier2 桶① T-A5.
# ===========================================================================
@needs_dt2
def test_bundle_artifact_carries_segmented_geometry():
    bridge = {"top.a.x": "X", "top.d.p": "DP", "top.d.n": "DN"}
    plan = _plan(_BASE_BUNDLE)
    (stage,) = plan.route_stages
    art = bundle_artifact(stage, _ir(bridge))

    assert art["type"] == "bundle" and art["name"] == "b"

    # segmented trunk: vertices with per-segment spacing survive into the artifact
    spacings = [v["spacing"] for v in art["trunk"]["centerline"]]
    assert spacings == [0.5, 0.5]

    # ordered member table with COMPUTED offsets (the injected geometry, D-t2.2b)
    members = art["members"]
    assert [m["net"] for m in members] == _BASE_MEMBERS  # lane order, P before N
    assert all({"net", "offset", "width", "kind"} <= set(m) for m in members)
    # the offsets must equal the SSOT function (artifact is not a second source);
    # default_width == config.track_width (0.2, != the 0.1 hardcoded default) so
    # this verifies the config→geometry seam, not a default collision.
    oracle = cross_section_offsets(stage.lanes, 0.5, default_width=0.2)
    assert [pytest.approx(m["offset"]) for m in members] == [o.offset for o in oracle]

    # breakouts: the D-side artifact keys them by room address `at` with `order` in
    # ato addresses (member order when derived) — the runner later resolves these to
    # the frozen `part`+kicad-name shape batch_route_bundle wants (R2). Pin both the
    # key and the order CONTENT here so a wrong artifact cannot pass on length alone.
    assert len(art["breakouts"]) == 2
    a_bo, d_bo = art["breakouts"]
    assert a_bo["at"] == "top.a" and a_bo["order"] == _BASE_MEMBERS  # derived order
    assert d_bo["at"] == "top.d" and d_bo["order"] == _BASE_MEMBERS
    # breakout order + resolved nets (the address→net bridge②, in member order)
    assert art["resolved_nets"] == [bridge[m] for m in _BASE_MEMBERS]

    # the fragment is JSON-serializable (it is written to <t>.layout_plan.json)
    json.dumps(art)


# ===========================================================================
# T-B1 — D/E contract: BundleRouteConfig fields ⊆ batch_route_bundle kwargs.
# AST oracle (same method as D2's GridRouteOverride drift guard). strict-xfail
# until E-Tier2 creates route_bundle.batch_route_bundle. BACKLOG D-Tier2 桶② T-B1.
# ===========================================================================
@needs_e_tier2
def test_bundle_config_fields_are_real_router_kwargs():
    if not _ROUTER_PRESENT:
        pytest.skip(
            f"KiCadRoutingTools not present at {_ROUTER_ROOT}; cannot AST-probe "
            "batch_route_bundle"
        )
    kwargs = _batch_route_bundle_kwargs()
    assert kwargs is not None, "route_bundle.batch_route_bundle not defined (E-Tier2)"
    fields = set(BundleRouteConfig.model_fields)
    assert fields, "BundleRouteConfig exposes no router knobs"
    extra = fields - kwargs
    assert not extra, (
        "BundleRouteConfig has keys batch_route_bundle does not accept as a kwarg "
        f"(drift — E1 expands these into the entry, would TypeError): {sorted(extra)}"
    )


# ===========================================================================
# T-B2 — D/E contract: calling batch_route_bundle with a contract-shaped input
# yields a per-member routed/blocked result (batch_route-isomorphic). The router
# runs under the system interpreter (its rust ext is not built for this venv), so
# this shells out — the same pattern as the §C router-oracle. strict-xfail until
# E-Tier2. BACKLOG D-Tier2 桶② T-B2.
# ===========================================================================
@needs_e_tier2
def test_batch_route_bundle_result_shape():
    if not _ROUTER_PRESENT:
        pytest.skip(f"KiCadRoutingTools not present at {_ROUTER_ROOT}")
    kwargs = _batch_route_bundle_kwargs()
    assert kwargs is not None, "route_bundle.batch_route_bundle not defined (E-Tier2)"
    # the contract input shape E must accept (segmented trunk + ordered member
    # table + 2 breakouts). Pinned here so E builds to it; the call + per-member
    # result assertion run once E-Tier2 lands (this body executes only then).
    import subprocess
    import sys

    driver = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(_ROUTER_ROOT)!r})\n"
        "from route_bundle import batch_route_bundle\n"
        "trunk = {'centerline': [{'at': [0,0], 'spacing': 0.5},\n"
        "                        {'at': [10,0], 'spacing': 0.5}],\n"
        "         'segments': [{'kind': 'rigid', 'spacing': 0.5}]}\n"
        "members = [{'net':'X','offset':-0.5,'width':0.1,'kind':'single'},\n"
        "           {'net':'DP','offset':-0.1,'width':0.12,'kind':'diff',"
        "'diff_partner':'DN','polarity':'P'},\n"
        "           {'net':'DN','offset': 0.1,'width':0.12,'kind':'diff',"
        "'diff_partner':'DP','polarity':'N'}]\n"
        "breakouts = [{'part':'U1','order':['X','DP','DN']},\n"
        "             {'part':'U2','order':['X','DP','DN']}]\n"
        "res = batch_route_bundle(trunk, members, breakouts,\n"
        "                         track_width=0.1, clearance=0.1,\n"
        "                         return_results=True)\n"
        "print('JSON_RESULT' + json.dumps({'keys': sorted(res),\n"
        "      'members': [{'net': m.get('net'), 'routed': m.get('routed')}\n"
        "                  for m in res.get('members', [])]}))\n"
    )
    proc = subprocess.run(
        [sys.executable.replace("/.venv/bin/python", "/usr/bin/python3")
         if "/.venv/" in sys.executable else "python3", "-c", driver],
        capture_output=True, text=True, cwd=str(_ROUTER_ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("JSON_RESULT"))
    res = json.loads(line[len("JSON_RESULT"):])
    # result is batch_route-isomorphic, reported PER MEMBER
    nets = {m["net"] for m in res["members"]}
    assert {"X", "DP", "DN"} <= nets
    assert all("routed" in m for m in res["members"])


# ===========================================================================
# T-B3..T-B6 — E-Tier2 BEHAVIORAL contract (桶②, beyond the T-B2 shape-pin):
# `batch_route_bundle` turns the frozen (segmented trunk + ordered member offsets
# + breakouts) into a PARALLEL BUS — one offset track per member along the
# centerline — that ① is routed per member, ② sits at each member's signed offset
# (and MOVES when the offset changes — no blind test), ③ keeps a diff lane's P/N a
# CONSTANT intra-pair gap apart end to end (L1 coupling never dissolves into two
# independent singles), and ④ morphs the cross-section at a transition (neck-down)
# while preserving lane ORDER. These exercise the REAL router under system
# python3; the trunk geometry needs NO board, so they run without a fixture PCB.
# strict-xfail until E-Tier2 lands route_bundle.batch_route_bundle. The geometry
# the assertions read is the `members[*].polyline` the router emits in its
# JSON_SUMMARY (the member's trunk track, in board mm).
# ===========================================================================
_SYS_PY = shutil.which("python3")

needs_sys_py = pytest.mark.skipif(
    _SYS_PY is None, reason="no system python3 to run the router"
)

# trunks along +X (y == 0) so a member's signed offset maps directly to track y.
_RIGID_TRUNK = {
    "centerline": [{"at": [0, 0], "spacing": 0.5}, {"at": [10, 0], "spacing": 0.5}],
    "segments": [{"kind": "rigid", "spacing": 0.5}],
}
# rigid (0.5) then a neck-down transition to 0.5 -> 0.25 in the last segment.
_TRANSITION_TRUNK = {
    "centerline": [
        {"at": [0, 0], "spacing": 0.5},
        {"at": [10, 0], "spacing": 0.5},
        {"at": [20, 0], "spacing": 0.25},
    ],
    "segments": [
        {"kind": "rigid", "spacing": 0.5},
        {"kind": "transition", "spacing_a": 0.5, "spacing_b": 0.25},
    ],
}
_BREAKOUTS_GEO = [{"part": "A", "order": None}, {"part": "B", "order": None}]


def _members_from_lanes(lanes, spacing=0.5, default_width=0.1) -> list[dict]:
    """The router's `members` input table, DERIVED from the geometry SSOT
    (`cross_section_offsets`) so the behavioral tests stay pinned to the same
    offsets T-A3 froze (the bus is not a second source of truth)."""
    return [
        {
            "net": o.net,
            "offset": o.offset,
            "width": o.width,
            "kind": o.kind,
            "diff_partner": o.diff_partner,
            "polarity": o.polarity,
        }
        for o in cross_section_offsets(lanes, spacing, default_width=default_width)
    ]


def _run_bundle_geometry(trunk, members, breakouts, **kw) -> dict:
    """Shell out to system python3 and call `batch_route_bundle` in GEOMETRY-ONLY
    mode (no board) — the rust ext is not built for the venv, same pattern as the
    §C router-oracle. Returns the JSON_SUMMARY dict; each `members[*]` carries a
    `polyline` (the member's trunk track, [[x, y], ...] in board mm)."""
    payload = json.dumps([trunk, members, breakouts, kw])
    driver = (
        "import sys, json, io, re\n"
        f"sys.path.insert(0, {str(_ROUTER_ROOT)!r})\n"
        "from contextlib import redirect_stdout\n"
        "from route_bundle import batch_route_bundle\n"
        f"trunk, members, breakouts, kw = json.loads({payload!r})\n"
        "buf = io.StringIO()\n"
        "with redirect_stdout(buf):\n"
        "    batch_route_bundle(trunk, members, breakouts,\n"
        "                       return_results=True, verbose=False, **kw)\n"
        "m = re.search(r'JSON_SUMMARY: (\\{.*\\})', buf.getvalue())\n"
        "print('JSON_RESULT' + (m.group(1) if m else 'null'))\n"
    )
    proc = subprocess.run(
        [_SYS_PY, "-c", driver],
        cwd=str(_ROUTER_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    line = next(
        ln for ln in proc.stdout.splitlines() if ln.startswith("JSON_RESULT")
    )
    res = json.loads(line[len("JSON_RESULT"):])
    assert res is not None, "batch_route_bundle printed no JSON_SUMMARY"
    return res


@needs_e_tier2
@needs_sys_py
def test_bundle_trunk_is_parallel_offset_bus():
    """① every member routed, in order; ② each member's track is the line y == its
    signed offset (perpendicular distance == |offset|), CONSTANT along a rigid
    trunk, spanning the centerline. = the parallel bus."""
    members = _members_from_lanes(_tiny_lanes())  # single A, diff(P,N), single B
    res = _run_bundle_geometry(
        _RIGID_TRUNK, members, _BREAKOUTS_GEO, track_width=0.1, clearance=0.1
    )
    out = {m["net"]: m for m in res["members"]}
    assert [m["net"] for m in res["members"]] == [m["net"] for m in members]
    assert all(m["routed"] for m in res["members"])
    assert res["successful"] == len(members) and res["failed"] == 0
    for m in members:
        poly = out[m["net"]]["polyline"]
        assert len(poly) >= 2
        ys = [p[1] for p in poly]
        assert all(abs(y - m["offset"]) < 1e-6 for y in ys), (
            f"{m['net']} not at its offset {m['offset']}: ys={ys}"
        )
        xs = [p[0] for p in poly]
        assert min(xs) <= 1e-6 and max(xs) >= 10 - 1e-6  # spans the trunk


@needs_e_tier2
@needs_sys_py
def test_bundle_track_offset_mutation_propagates():
    """A member's offset change MUST move its track (an offset that does not move
    the geometry == a blind test == a bug)."""
    members = _members_from_lanes(_tiny_lanes())
    base = {
        m["net"]: m
        for m in _run_bundle_geometry(
            _RIGID_TRUNK, members, _BREAKOUTS_GEO, track_width=0.1, clearance=0.1
        )["members"]
    }
    bumped = [dict(m) for m in members]
    bumped[0]["offset"] = members[0]["offset"] - 1.0  # shift member A by 1 mm
    moved = {
        m["net"]: m
        for m in _run_bundle_geometry(
            _RIGID_TRUNK, bumped, _BREAKOUTS_GEO, track_width=0.1, clearance=0.1
        )["members"]
    }
    net0 = members[0]["net"]
    assert moved[net0]["polyline"][0][1] != base[net0]["polyline"][0][1]


@needs_e_tier2
@needs_sys_py
def test_bundle_diff_lane_stays_coupled():
    """L1 is never flattened: a diff lane's P and N stay a CONSTANT intra-pair gap
    apart at EVERY point of the trunk — never two independent parallel singles."""
    members = _members_from_lanes(_tiny_lanes())  # middle diff(n.p, n.n) gap 0.2
    res = _run_bundle_geometry(
        _RIGID_TRUNK, members, _BREAKOUTS_GEO, track_width=0.1, clearance=0.1
    )
    out = {m["net"]: m for m in res["members"]}
    p, n = out["n.p"]["polyline"], out["n.n"]["polyline"]
    assert len(p) == len(n) and len(p) >= 2
    for (xp, yp), (xn, yn) in zip(p, n):
        assert abs(xp - xn) < 1e-6  # same x stations
        assert abs(abs(yp - yn) - 0.2) < 1e-6  # constant intra-pair gap


@needs_e_tier2
@needs_sys_py
def test_bundle_transition_morphs_cross_section():
    """A transition (neck-down 0.5 -> 0.25) RE-PACKS the cross-section per vertex:
    only the INTER-lane spacing changes, the INTRA-pair gap is bundle-global. So
    the morph must equal the geometry SSOT at each vertex's spacing — NOT a naive
    proportional offset scale (which would collapse the diff gap and dissolve L1).
    Pinned here: ① the SSOT-magnitude at both ends, ② lane order/side preserved,
    ③ the diff pair stays a CONSTANT 0.2 apart at EVERY station incl. the far end."""
    members = _members_from_lanes(_tiny_lanes())
    res = _run_bundle_geometry(
        _TRANSITION_TRUNK, members, _BREAKOUTS_GEO, track_width=0.1, clearance=0.1
    )
    out = {m["net"]: m for m in res["members"]}
    # the SSOT profiles at the near (spacing 0.5) and far (spacing 0.25) vertices.
    near_oracle = {o.net: o.offset for o in cross_section_offsets(
        _tiny_lanes(), 0.5, default_width=0.1)}
    far_oracle = {o.net: o.offset for o in cross_section_offsets(
        _tiny_lanes(), 0.25, default_width=0.1)}
    for net in ("n.a", "n.b"):  # the two outer singles
        poly = out[net]["polyline"]
        near, far = poly[0][1], poly[-1][1]
        # ① morph equals the re-packed SSOT at each end (not a proportional scale).
        assert near == pytest.approx(near_oracle[net])
        assert far == pytest.approx(far_oracle[net])
        # ② genuinely necked down, side/sign (lane order) preserved.
        assert abs(far) < abs(near) - 1e-9
        assert (near > 0) == (far > 0)
    # ③ L1 holds THROUGH the transition: intra-pair gap is constant 0.2 everywhere,
    # including the far (necked-down) end — re-packing must not scale the pair gap.
    p, n = out["n.p"]["polyline"], out["n.n"]["polyline"]
    assert len(p) == len(n) and len(p) >= 3  # v0, v1, v2 (spans the transition)
    for (xp, yp), (xn, yn) in zip(p, n):
        assert abs(xp - xn) < 1e-6
        assert abs(abs(yp - yn) - 0.2) < 1e-6
