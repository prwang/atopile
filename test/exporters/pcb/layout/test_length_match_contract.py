# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_length_match_contract — the length tuner is reachable from layout.yaml (F3).

The vendored router has always had a real meander generator
(`length_matching.py`: trombone meanders, group matching to the LONGEST member,
intra-pair P/N matching), but layout.yaml could not reach it: the override
declared `length_match_groups: list[str]` while both router entries take
`List[List[str]]` (shape mismatch, silently unusable), entries were never
resolved from ato addresses, `meander_amplitude` was not exposed, the intra-pair
skew target was not numerically authorable, and the bundle path had no matching
at all. This module pins the repaired contract:

  SCHEMA (venv-pure)
   * `length_match_groups` is canonically `list[list[str]]`; a flat `list[str]`
     is auto-wrapped into ONE group (documented authoring shorthand); junk
     shapes are loud; `meander_amplitude` must be > 0; a matching knob without
     its consumer (tolerance/amplitude with no groups, an intra tolerance with
     intra matching off) is a dead knob and loud (S5a).
   * BundleRouteConfig carries the same three knobs with the same semantics.
   * `diff_pair_intra_match_tolerance` is diff-only (mode partition, D2.5).

  RESOLUTION (venv-pure, through build_invocations — the ONLY seam where the
  bridge② ir is available; precedent = board_rules net_classes)
   * each group entry: an ato signal address resolves through
     `ir["signal_nets"]`; else an exact existing board net name (`ir["nets"]`)
     is kept verbatim; else LOUD, naming the entry AND the stage.
   * the bundle invocation resolves through the SAME code path.

  E2E (system python3 + the real router + the F2 length_report — the proof)
   * two single nets of deliberately different routed length in one group +
     tolerance ⇒ post-route track_mm spread <= tolerance AND the shorter net's
     segment_count inflated (a meander physically happened).
   * a diff pair with `diff_pair_intra_match` + a numeric
     `diff_pair_intra_match_tolerance` ⇒ pair skew shrinks below it (a
     synthetic crossed-polarity 2-pad fixture: skew fixed by construction AND
     the router's length metric == the board's track_mm).
   * a MULTIPOINT pair ⇒ the intra meanders physically reach the written board
     (pins the `_sync_matched_results_into_write_list` repair: leg merging
     breaks dict identity between routed_results and the write list, which
     silently dropped meanders before F3).
   * a bundle with the same knobs ⇒ member track_mm spread <= tolerance.

Board: the router submodule's 2-layer LVDS board (same as
test_router_smoke_batch_route.py), routed via the REAL layout_plan_runner
subprocess invoker. Skips loudly (never silently passes) when the router or
system python3 is absent — the same guard as the smoke tests.
"""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from faebryk.exporters.pcb.layout.layout_plan import (
    Board,
    BundleRouteConfig,
    BundleStage,
    DesignRules,
    GridRouteOverride,
    LayoutPlan,
    LayoutPlanError,
    RouteStage,
    Stackup,
    StackupLayer,
    load_layout_plan,
)
from faebryk.exporters.pcb.layout.layout_plan_runner import (
    _system_python3,
    build_invocations,
    run_route_stages,
)
from faebryk.libs.kicad.length_report import length_report_for_board
from faebryk.libs.util import repo_root

_LOUD = (LayoutPlanError, ValidationError)

_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_BOARD = _ROUTER_ROOT / "kicad_files" / "lvds_converter_dualclk.kicad_pcb"
# NEVER bare shutil.which("python3") — see _system_python3's docstring.
_SYS_PY = _system_python3()

needs_router_board = pytest.mark.skipif(
    not _BOARD.exists() or _SYS_PY is None,
    reason=f"router submodule board or system python3 absent ({_BOARD})",
)


# ---------------------------------------------------------------------------
# shared plan scaffolding (mirrors test_layout_plan_runner_contract.py)
# ---------------------------------------------------------------------------
def _stackup2() -> Stackup:
    return Stackup(
        layers=[
            StackupLayer(name="F.Cu", type="copper", thickness=0.035),
            StackupLayer(
                name="d1",
                type="dielectric",
                thickness=1.5,
                material="FR4",
                epsilon_r=4.5,
            ),
            StackupLayer(name="B.Cu", type="copper", thickness=0.035),
        ]
    )


def _rules() -> DesignRules:
    return DesignRules(
        clearance=0.2, track_width=0.2, diff_pair_width=0.2, diff_pair_gap=0.25
    )


def _plan(stages) -> LayoutPlan:
    return LayoutPlan(
        rules=_rules(), board=Board(stackup=_stackup2()), route_stages=stages
    )


# ===========================================================================
# 1 — SCHEMA: canonical nested shape + flat-wrap + junk-forbid + coherence
# ===========================================================================
def test_nested_groups_parse_verbatim():
    ov = GridRouteOverride(
        length_match_groups=[["top.a", "top.b"], ["top.c", "top.d"]],
        length_match_tolerance=0.3,
    )
    assert ov.length_match_groups == [["top.a", "top.b"], ["top.c", "top.d"]]


def test_flat_group_auto_wraps_into_one_group():
    """The documented flat shorthand: `[a, b]` == one group `[[a, b]]`."""
    ov = GridRouteOverride(length_match_groups=["top.a", "top.b"])
    assert ov.length_match_groups == [["top.a", "top.b"]]


def test_bundle_flat_group_auto_wraps_too():
    cfg = BundleRouteConfig(length_match_groups=["top.a", "top.b"])
    assert cfg.length_match_groups == [["top.a", "top.b"]]


@pytest.mark.parametrize(
    "junk",
    [
        5,  # not a list at all
        [5],  # a non-string entry
        [[1, 2]],  # a group of non-strings
        ["top.a", ["top.b"]],  # mixed flat/nested — ambiguous, never coerced
        [["top.a"], "top.b"],  # mixed nested/flat — same
    ],
    ids=["scalar", "int_entry", "int_group", "mixed_flat_first", "mixed_nested_first"],
)
def test_junk_groups_are_loud(junk):
    with pytest.raises(ValidationError):
        GridRouteOverride(length_match_groups=junk, length_match_tolerance=0.3)


@pytest.mark.parametrize("bad", [-1.0, 0.0])
def test_non_positive_meander_amplitude_is_loud(bad):
    with pytest.raises(ValidationError):
        GridRouteOverride(
            length_match_groups=[["a", "b"]], meander_amplitude=bad
        )
    with pytest.raises(ValidationError):
        BundleRouteConfig(length_match_groups=[["a", "b"]], meander_amplitude=bad)


def test_bundle_config_parses_new_keys():
    cfg = BundleRouteConfig(
        length_match_groups=[["top.a", "top.b"]],
        length_match_tolerance=0.4,
        meander_amplitude=1.2,
    )
    assert cfg.length_match_groups == [["top.a", "top.b"]]
    assert cfg.length_match_tolerance == 0.4
    assert cfg.meander_amplitude == 1.2


# a matching knob without its consumer is a DEAD knob — loud, never silently
# ignored (S5a). One case per orphaned knob, plus the positive control.
@pytest.mark.parametrize(
    "kwargs",
    [
        {"length_match_tolerance": 0.3},  # tolerance with no groups
        {"meander_amplitude": 1.0},  # amplitude with no groups / no intra match
        # intra tolerance with intra matching off (diff-only knob, but the
        # coherence gate is on the model itself)
        {"diff_pair_intra_match_tolerance": 0.1},
        {"diff_pair_intra_match_tolerance": 0.1, "diff_pair_intra_match": False},
    ],
    ids=[
        "tolerance_without_groups",
        "amplitude_without_consumer",
        "intra_tolerance_without_intra",
        "intra_tolerance_with_intra_off",
    ],
)
def test_orphaned_matching_knob_is_loud(kwargs):
    with pytest.raises(ValidationError):
        GridRouteOverride(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"length_match_tolerance": 0.3},
        {"meander_amplitude": 1.0},
    ],
    ids=["tolerance_without_groups", "amplitude_without_groups"],
)
def test_bundle_orphaned_matching_knob_is_loud(kwargs):
    with pytest.raises(ValidationError):
        BundleRouteConfig(**kwargs)


def test_amplitude_with_intra_match_only_is_coherent():
    """meander_amplitude's OTHER consumer: intra-pair matching (no groups)."""
    ov = GridRouteOverride(
        diff_pair_intra_match=True,
        diff_pair_intra_match_tolerance=0.1,
        meander_amplitude=1.0,
    )
    assert ov.meander_amplitude == 1.0


def test_intra_match_tolerance_is_diff_only():
    """Mode partition (D2.5): the single entry has no intra-match kwarg."""
    yaml_diff = (
        "rules:\n  clearance: 0.1\n  track_width: 0.15\n"
        "  diff_pair_width: 0.15\n  diff_pair_gap: 0.15\n"
        "rooms: []\nroute_stages:\n  - name: s\n    mode: diff\n    nets: []\n"
        "    config:\n      diff_pair_intra_match: true\n"
        "      diff_pair_intra_match_tolerance: 0.1\n"
    )
    plan = load_layout_plan(yaml_diff)
    assert plan.route_stages[0].config.diff_pair_intra_match_tolerance == 0.1

    yaml_single = yaml_diff.replace("mode: diff", "mode: single")
    with pytest.raises(_LOUD):
        load_layout_plan(yaml_single)


def test_meander_amplitude_accepted_in_both_modes():
    for mode in ("single", "diff"):
        yaml_text = (
            "rules:\n  clearance: 0.1\n  track_width: 0.15\n"
            "  diff_pair_width: 0.15\n  diff_pair_gap: 0.15\n"
            f"rooms: []\nroute_stages:\n  - name: s\n    mode: {mode}\n"
            "    nets: []\n    config:\n"
            "      length_match_groups:\n"
            "        - [top.a, top.b]\n"
            "      length_match_tolerance: 0.3\n"
            "      meander_amplitude: 1.5\n"
        )
        plan = load_layout_plan(yaml_text)
        assert plan.route_stages[0].config.meander_amplitude == 1.5


# ===========================================================================
# 2 — RESOLUTION: group entries resolve in the runner through bridge② —
# ato signal address → mapped net name; exact board net name → kept; else loud
# naming the entry and the stage (precedent: board_rules net_classes).
# ===========================================================================
_RESOLVE_IR = {
    "signal_nets": {"top.a": "N_A", "top.b": "N_B"},
    "nets": {"N_A": [], "N_B": [], "/RAW": []},
}


def _single_stage_with_groups(groups) -> LayoutPlan:
    return _plan(
        [
            RouteStage(
                name="s",
                mode="single",
                nets=["top.a", "top.b"],
                config=GridRouteOverride(
                    length_match_groups=groups, length_match_tolerance=0.3
                ),
            )
        ]
    )


def test_groups_resolve_address_and_keep_board_net():
    plan = _single_stage_with_groups([["top.a", "/RAW"]])
    (inv,) = build_invocations(
        plan, _RESOLVE_IR, input_board="in.kicad_pcb", workdir="wd"
    )
    assert inv.kwargs["length_match_groups"] == [["N_A", "/RAW"]]


def test_flat_group_resolves_after_wrap():
    plan = _single_stage_with_groups(["top.a", "top.b"])
    (inv,) = build_invocations(
        plan, _RESOLVE_IR, input_board="in.kicad_pcb", workdir="wd"
    )
    assert inv.kwargs["length_match_groups"] == [["N_A", "N_B"]]


def test_garbage_entry_is_loud_naming_entry_and_stage():
    plan = _single_stage_with_groups([["top.a", "bogus_entry"]])
    with pytest.raises(LayoutPlanError) as ei:
        build_invocations(plan, _RESOLVE_IR, input_board="in.kicad_pcb", workdir="wd")
    msg = str(ei.value)
    assert "bogus_entry" in msg and "'s'" in msg


def test_bundle_groups_resolve_through_same_path():
    stage = BundleStage.model_validate(
        {
            "type": "bundle",
            "name": "b",
            "lanes": [{"net": "top.a"}, {"net": "top.b"}],
            "trunk": {
                "centerline": [
                    {"at": [0, 0], "spacing": 0.5},
                    {"at": [10, 0], "spacing": 0.5},
                ]
            },
            "breakouts": [{"at": "top"}, {"at": "top"}],
            "config": {
                "track_width": 0.2,
                "length_match_groups": [["top.a", "top.b"]],
                "length_match_tolerance": 0.3,
            },
        }
    )
    plan = _plan([stage])
    (inv,) = build_invocations(
        plan, _RESOLVE_IR, input_board="in.kicad_pcb", workdir="wd"
    )
    assert inv.entry == "batch_route_bundle"
    assert inv.kwargs["length_match_groups"] == [["N_A", "N_B"]]


def test_bundle_garbage_entry_is_loud():
    stage = BundleStage.model_validate(
        {
            "type": "bundle",
            "name": "b",
            "lanes": [{"net": "top.a"}, {"net": "top.b"}],
            "trunk": {
                "centerline": [
                    {"at": [0, 0], "spacing": 0.5},
                    {"at": [10, 0], "spacing": 0.5},
                ]
            },
            "breakouts": [{"at": "top"}, {"at": "top"}],
            "config": {
                "length_match_groups": [["nonsense"]],
                "length_match_tolerance": 0.3,
            },
        }
    )
    plan = _plan([stage])
    with pytest.raises(LayoutPlanError) as ei:
        build_invocations(plan, _RESOLVE_IR, input_board="in.kicad_pcb", workdir="wd")
    msg = str(ei.value)
    assert "nonsense" in msg and "'b'" in msg


# ===========================================================================
# 3 — VENDOR GUARD: bundle length matching is board-mode only — the meander
# needs a parsed board (pcb_data) for net ids and clearance; geometry-only
# callers asking for it get a LOUD error, never a silent no-op. route_bundle's
# geometry-only path is pure python, so this imports it in-venv.
# ===========================================================================
def test_bundle_geometry_only_length_matching_is_loud():
    import sys

    sys.path.insert(0, str(_ROUTER_ROOT))
    try:
        import route_bundle
    finally:
        sys.path.remove(str(_ROUTER_ROOT))
    trunk = {
        "centerline": [
            {"at": [0, 0], "spacing": 0.5},
            {"at": [10, 0], "spacing": 0.5},
        ]
    }
    members = [
        {"net": "A", "offset": -0.35, "width": 0.2, "kind": "single"},
        {"net": "B", "offset": 0.35, "width": 0.2, "kind": "single"},
    ]
    breakouts = [{"part": "x", "order": ["A", "B"]}, {"part": "y", "order": ["A", "B"]}]
    with pytest.raises(ValueError, match="length_match"):
        route_bundle.batch_route_bundle(
            trunk, members, breakouts, length_match_groups=[["A", "B"]]
        )


# ===========================================================================
# 4 — E2E (the proof): the REAL runner subprocess → the REAL router → the F2
# length_report. Board: the submodule LVDS board. /OUT_A and /OUT_B are 2-pad
# nets whose natural routed lengths differ by ~4.3mm (>> tolerance), so the
# matched run MUST meander; /CLK+//CLK- is a routable pair with ~1.4mm natural
# skew (>> the intra tolerance).
# ===========================================================================
_TOL = 0.5  # mm — group tolerance for the single/bundle e2e
_INTRA_TOL = 0.15  # mm — intra-pair skew target for the diff e2e


def _run_stage(tmp_path: Path, stage: RouteStage, ir: dict) -> dict:
    """One real router stage via run_route_stages; returns the length report."""
    report = run_route_stages(
        _plan([stage]), ir, input_board=_BOARD, workdir=tmp_path
    )
    assert report.totals["failed"] == 0, report.to_dict()
    return length_report_for_board(Path(report.final_board))


@needs_router_board
def test_e2e_single_group_matches_within_tolerance(tmp_path):
    ir = {
        "signal_nets": {"top.out_a": "/OUT_A", "top.out_b": "/OUT_B"},
        "nets": {},
    }
    # baseline (no matching): pins the premise AND the shorter net's segments
    base = _run_stage(
        tmp_path / "base",
        RouteStage(name="base", mode="single", nets=["top.out_a", "top.out_b"]),
        ir,
    )
    la, lb = base["nets"]["/OUT_A"]["track_mm"], base["nets"]["/OUT_B"]["track_mm"]
    assert abs(la - lb) > 2 * _TOL, (
        f"premise broken: natural lengths {la:.2f}/{lb:.2f} no longer differ — "
        "pick two nets with different manhattan lengths"
    )
    shorter = "/OUT_A" if la < lb else "/OUT_B"
    base_segments = base["nets"][shorter]["segment_count"]

    # matched: group authored as ATO ADDRESSES (resolution is part of the e2e)
    matched = _run_stage(
        tmp_path / "matched",
        RouteStage(
            name="matched",
            mode="single",
            nets=["top.out_a", "top.out_b"],
            config=GridRouteOverride(
                length_match_groups=[["top.out_a", "top.out_b"]],
                length_match_tolerance=_TOL,
                meander_amplitude=1.0,
            ),
        ),
        ir,
    )
    ma = matched["nets"]["/OUT_A"]["track_mm"]
    mb = matched["nets"]["/OUT_B"]["track_mm"]
    assert abs(ma - mb) <= _TOL, (
        f"post-route spread {abs(ma - mb):.3f}mm exceeds tolerance {_TOL}mm "
        f"(/OUT_A={ma:.3f}, /OUT_B={mb:.3f})"
    )
    # a meander PHYSICALLY happened on the shorter net (segment inflation)
    assert matched["nets"][shorter]["segment_count"] > base_segments, (
        f"no meander: {shorter} segment_count "
        f"{matched['nets'][shorter]['segment_count']} <= baseline {base_segments}"
    )


# A minimal synthetic diff-pair board whose intra skew is FIXED BY CONSTRUCTION
# (CLAUDE.md test discipline: the adversarial fixture's answer must be built in,
# not discovered): the target pads have CROSSED polarity and the stage routes
# with fix_polarity=False, so one net must physically uncross around the other —
# a genuine ~1.5mm copper skew (empirically 1.488mm), far above _INTRA_TOL. A
# 2-pad pair on an empty board also keeps the router's internal length metric
# identical to the board's track_mm (no multipoint legs, no stubs, no swap
# accounting), so the authored tolerance is judged on the same number the F2
# length_report measures.
_SYNTH_DIFF_BOARD = """(kicad_pcb
\t(version 20241229)
\t(generator "pcbnew")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(25 "Edge.Cuts" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t)
\t(net 0 "")
\t(net 1 "/D_P")
\t(net 2 "/D_N")
\t(footprint "test:pair_src"
\t\t(layer "F.Cu")
\t\t(at 100 100)
\t\t(property "Reference" "U1" (at 0 -2 0) (layer "F.SilkS")
\t\t\t(effects (font (size 1 1) (thickness 0.15))))
\t\t(property "Value" "SRC" (at 0 2 0) (layer "F.Fab")
\t\t\t(effects (font (size 1 1) (thickness 0.15))))
\t\t(pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net 1 "/D_P"))
\t\t(pad "2" smd rect (at 0 0.65) (size 0.5 0.5) (layers "F.Cu") (net 2 "/D_N"))
\t)
\t(footprint "test:pair_dst"
\t\t(layer "F.Cu")
\t\t(at 120 110)
\t\t(property "Reference" "U2" (at 0 -2 0) (layer "F.SilkS")
\t\t\t(effects (font (size 1 1) (thickness 0.15))))
\t\t(property "Value" "DST" (at 0 2 0) (layer "F.Fab")
\t\t\t(effects (font (size 1 1) (thickness 0.15))))
\t\t(pad "1" smd rect (at 0 0.65) (size 0.5 0.5) (layers "F.Cu") (net 1 "/D_P"))
\t\t(pad "2" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net 2 "/D_N"))
\t)
\t(gr_rect (start 90 90) (end 130 120)
\t\t(stroke (width 0.1) (type default)) (layer "Edge.Cuts"))
)
"""


@needs_router_board
def test_e2e_diff_intra_match_tolerance_shrinks_skew(tmp_path):
    board = tmp_path / "synth_pair.kicad_pcb"
    board.write_text(_SYNTH_DIFF_BOARD)
    ir = {
        "signal_nets": {"top.d.p": "/D_P", "top.d.n": "/D_N"},
        "nets": {},
    }

    def stage(name: str, config: GridRouteOverride | None) -> RouteStage:
        kw = {} if config is None else {"config": config}
        return RouteStage(name=name, mode="diff", nets=["top.d.p", "top.d.n"], **kw)

    def run(tmp: Path, st: RouteStage) -> dict:
        report = run_route_stages(
            _plan([st]), ir, input_board=board, workdir=tmp
        )
        assert report.totals["failed"] == 0, report.to_dict()
        return length_report_for_board(Path(report.final_board))

    base = run(
        tmp_path / "base",
        stage("base", GridRouteOverride(fix_polarity=False)),
    )
    skew0 = base["pairs"]["/D"]["skew_mm"]
    assert skew0 > _INTRA_TOL, (
        f"premise broken: the crossed-polarity fixture's natural skew "
        f"{skew0:.3f}mm is already within {_INTRA_TOL}mm — the intra-match "
        "assertion would prove nothing"
    )

    matched = run(
        tmp_path / "matched",
        stage(
            "matched",
            GridRouteOverride(
                fix_polarity=False,
                diff_pair_intra_match=True,
                diff_pair_intra_match_tolerance=_INTRA_TOL,
            ),
        ),
    )
    skew1 = matched["pairs"]["/D"]["skew_mm"]
    assert skew1 <= _INTRA_TOL, (
        f"intra-pair skew {skew1:.3f}mm exceeds the authored tolerance "
        f"{_INTRA_TOL}mm (was {skew0:.3f}mm unmatched)"
    )


@needs_router_board
def test_e2e_multipoint_intra_meander_reaches_board(tmp_path):
    """The write-path sync seam: on a MULTIPOINT pair the router merges leg
    results into a new dict, so routed_results and the write-path `results`
    list hold DIFFERENT objects — before the `_sync_matched_results_into_write_
    list` repair the intra meanders were applied to the merged dict and
    silently missing from the written board. The LVDS /DATA pair is multipoint
    (3 terminals), so this asserts the meandered copper physically lands on the
    board: total pair copper must GROW vs the unmatched run by roughly the
    router-reported skew (~1.5mm). (The skew<=tolerance NUMBER is asserted on
    the 2-pad fixture above — a multipoint pair's router-metric includes
    polarity-swap stub accounting the board-side track_mm cannot see.)"""
    ir = {
        "signal_nets": {"top.data.p": "/DATA+", "top.data.n": "/DATA-"},
        "nets": {},
    }

    def run(tmp: Path, config: GridRouteOverride | None) -> float:
        kw = {} if config is None else {"config": config}
        st = RouteStage(name="s", mode="diff", nets=["top.data.p", "top.data.n"], **kw)
        report = run_route_stages(_plan([st]), ir, input_board=_BOARD, workdir=tmp)
        assert report.totals["failed"] == 0, report.to_dict()
        rep = length_report_for_board(Path(report.final_board))
        pair = rep["pairs"]["/DATA"]
        return pair["p_track_mm"] + pair["n_track_mm"]

    base_total = run(tmp_path / "base", None)
    matched_total = run(
        tmp_path / "matched",
        GridRouteOverride(
            diff_pair_intra_match=True,
            diff_pair_intra_match_tolerance=0.1,
        ),
    )
    assert matched_total > base_total + 0.5, (
        f"intra meanders did not reach the written board: pair copper "
        f"{base_total:.3f}mm -> {matched_total:.3f}mm (expected ~+1.5mm)"
    )


@needs_router_board
def test_e2e_bundle_group_matches_within_tolerance(tmp_path):
    """Bundle board mode: two single lanes fanned out to real pads, matched to
    within tolerance. Driven through system python3 (board mode pulls the kicad
    parser + scipy, which live under the system interpreter only)."""
    out = tmp_path / "bundle_out.kicad_pcb"
    # the trunk sits DELIBERATELY far from both nets' pads (top-left of the
    # board; the pads are around x=103..117, y=66..68) so the two members'
    # direct-connect fanout distances differ by ~8mm BY CONSTRUCTION — without
    # matching the member spread would dwarf _TOL, so the assertion below can
    # only pass if run_length_matching actually meandered the shorter member.
    trunk = {
        "centerline": [
            {"at": [85.0, 55.0], "spacing": 0.5},
            {"at": [95.0, 55.0], "spacing": 0.5},
        ]
    }
    members = [
        {"net": "/OUT_B", "offset": -0.35, "width": 0.2, "kind": "single"},
        {"net": "/OUT_A", "offset": 0.35, "width": 0.2, "kind": "single"},
    ]
    breakouts = [
        {"part": "w", "order": ["/OUT_B", "/OUT_A"]},
        {"part": "e", "order": ["/OUT_B", "/OUT_A"]},
    ]
    driver = (
        f"import sys; sys.path.insert(0, {str(_ROUTER_ROOT)!r})\n"
        "import io, json\n"
        "import route_bundle\n"
        "buf = io.StringIO(); _o = sys.stdout; sys.stdout = buf\n"
        "try:\n"
        "    route_bundle.batch_route_bundle(\n"
        f"        {trunk!r}, {members!r}, {breakouts!r},\n"
        f"        input_file={str(_BOARD)!r}, output_file={str(out)!r},\n"
        "        layers=['F.Cu', 'B.Cu'], track_width=0.2, clearance=0.2,\n"
        "        length_match_groups=[['/OUT_A', '/OUT_B']],\n"
        f"        length_match_tolerance={_TOL}, meander_amplitude=1.0,\n"
        "    )\n"
        "finally:\n"
        "    sys.stdout = _o\n"
        "print('DRIVER_OK')\n"
    )
    proc = subprocess.run(
        [_SYS_PY, "-c", driver],
        cwd=str(_ROUTER_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0 and (
        "ModuleNotFoundError" in proc.stderr or "ImportError" in proc.stderr
    ):
        pytest.skip(
            "system python3 cannot import the router board deps: "
            + proc.stderr.strip()[-300:]
        )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "DRIVER_OK" in proc.stdout

    rep = length_report_for_board(out)
    ma = rep["nets"]["/OUT_A"]["track_mm"]
    mb = rep["nets"]["/OUT_B"]["track_mm"]
    assert ma > 0 and mb > 0, rep["nets"]
    assert abs(ma - mb) <= _TOL, (
        f"bundle member spread {abs(ma - mb):.3f}mm exceeds tolerance {_TOL}mm "
        f"(/OUT_A={ma:.3f}, /OUT_B={mb:.3f})"
    )
    # the meandered (shorter) member carries more than the deterministic
    # trunk(1) + fanout(2) segments it would have without matching
    assert max(
        rep["nets"]["/OUT_A"]["segment_count"],
        rep["nets"]["/OUT_B"]["segment_count"],
    ) > 3, rep["nets"]
