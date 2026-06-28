# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_layout_plan_runner_contract — the §E1 contract: `layout_plan_runner.py`
turns `LayoutPlan.route_stages` into router invocations and a `route_report.json`
(BACKLOG §E, "E1"). Tests-first S0 strict-xfail ratchet: every test is
strict-xfail until `layout_plan_runner.py` lands its full public surface (probe
`_E1_LANDED`, which imports ALL referenced symbols so a half-landed surface stays
uniformly red instead of erroring at collection).

== THE E1 PROTOCOL (what the runner guarantees) ===========================

The runner is split into a PURE PLANNING seam and an IMPURE EXECUTION seam so the
whole dispatch / kwargs / layers / --up-to contract is unit-testable WITHOUT the
rust router (which is not built for the venv — it runs only under system
python3):

    build_invocations(plan, ir, *, input_board, workdir, up_to=None)
        -> list[StageInvocation]            # PURE: no subprocess, no router import
    run_route_stages(plan, ir, *, input_board, workdir, up_to=None,
                     invoker=default_subprocess_invoker, report_path=None)
        -> RouteReport                      # calls invoker per stage, aggregates

`invoker: Callable[[StageInvocation], StageResult]` is the injectable seam: pure
tests pass a fake invoker that captures the StageInvocations or returns canned
StageResults (incl. the summary=None early-return case); only the default invoker
shells out to system python3.

1. DISPATCH BY STAGE TYPE (the union tag first, then RouteStage.mode):
   isinstance(stage, BundleStage) -> bundle; else RouteStage, and stage.mode in
   {"single","diff"}. single -> route.batch_route; diff ->
   route_diff.batch_route_diff_pairs; bundle -> route_bundle.batch_route_bundle.
   batch_route_bundle does NOT exist yet (E-Tier2), so a bundle stage IN THE RUN
   SLICE is LOUD not-implemented at build_invocations time (NotImplementedError
   naming the entry + E-Tier2), NEVER silently skipped (S5a).

2. CONFIG EXPANDED VERBATIM: RouteStage.config (GridRouteOverride) is expanded
   into the entry's kwargs as-is, EXPLICITLY-SET keys only
   (model_dump(exclude_unset=True)) — an unset field (default None) is NOT
   forwarded, so the router's own default stands and no None leaks across. E1
   does NOT re-validate config: keys are already mode-validated at D2 parse
   (RouteStage._validate_mode_kwargs); the runner trusts that partition.

3. LAYERS FROM THE STACKUP AUTHORITY (TS-AUTH-B): every invocation's kwargs carry
   layers explicitly == stackup_layers(plan.board.stackup). The runner NEVER
   passes layers=None and so NEVER falls through to route.py:230-231
   DEFAULT_4_LAYER_STACK. A missing/empty board.stackup is loud (stackup_layers
   raises LayoutPlanError — no silent layer-count default). This is the
   implementation half of the TS-AUTH-B ratchet in test_board_section_contract.py.

   SINGLE AUTHORITY (the must-fix): the stackup is the SOLE source of `layers`.
   GridRouteOverride.layers exists only to satisfy the D2.4 union drift-guard
   (it mirrors the router kwarg); a per-stage config that EXPLICITLY sets layers
   is a LOUD conflict (LayoutPlanError) — a stage may not override the board-level
   layer authority. (Per-stage layer *subsetting* is a deliberate non-goal here;
   see BACKLOG limitations.)

4. NET NAMES FROM bridge②: invocation.net_names == plan.resolve_nets(ir)[name],
   verbatim and in order. An address absent from ir["signal_nets"] is already
   loud in resolve_nets.

5. PER-STAGE User-layer CONSTRAINTS (default off, enabled per stage, via config):
   guide_corridor_enabled is single-only (the diff entry has no such kwarg, and
   D2 already rejects it on a diff stage), keepout_enabled is accepted by both;
   both default off, i.e. ABSENT from kwargs unless the stage's config explicitly
   enables them. E1 only forwards what was explicitly set (the single-only-vs-diff
   partition is D2-owned and not re-tested here).

6. BOARD ACCUMULATES ACROSS STAGES, NO LOCKS: stage 0's input_file is the
   original board; stage k's output_file (workdir/<stage-name>.kicad_pcb) is stage
   k+1's input_file (cross-stage chaining of the .kicad_pcb path, since pcb_data
   cannot cross the subprocess). The runner adds NO lock/freeze/preset kwargs for
   §C preset geometry or for prior-stage copper — the router treats existing
   copper as a hard obstacle for free (BACKLOG fact 16). Pinned POSITIVELY:
   set(inv.kwargs) <= set(stage.config.model_fields_set) | {"layers"} — E1 adds
   NOTHING beyond the config-set keys and the stackup layers.

7. TOLERATE MISSING JSON_SUMMARY: when a stage's nets are already connected (the
   router's own filter_already_routed empties the set) or no net resolves,
   batch_route / batch_route_diff_pairs return early (route.py:349-360 /
   route_diff.py:293-343) BEFORE the JSON_SUMMARY print, so StageResult.summary is
   None. Aggregation must treat such a stage as zero-routed (contributes 0 to
   every total) and MUST NOT assume every input net has a summary — no KeyError,
   the run continues, and the None-summary stage still appears in report.stages.

8. AGGREGATE per-type: the common scalar keys (successful, failed, total_time,
   total_iterations, total_vias) are SUMMED across stages FROM EACH STAGE'S
   summary dict (a None summary adds 0); per-type list keys are kept separate by
   entry type (single -> routed_single/failed_single; diff ->
   routed_diff_pairs/failed_diff_pairs). run_route_stages writes route_report.json
   (per-stage results, summed totals, the per-type breakdown, the up_to
   breakpoint, the final board path) and returns the RouteReport.

9. --up-to <stage-name | 1-based index>: run route_stages[0..k], stop, write the
   partial board + route_report.json. Addressed by unique stage name (uniqueness
   enforced at D2 parse) OR by 1-based index. report.up_to is normalized to the
   resolved stage NAME for either form (so the report carries str|None, never a
   raw int). An out-of-range index (<1 or >len) or an unknown name is LOUD
   (LayoutPlanError), never a silent clamp.

== INTERFACE DELTA (the surface E1 must land) =============================

NEW module faebryk.exporters.pcb.layout.layout_plan_runner exporting:
  - StageInvocation  (frozen dataclass: stage_name, stage_type, entry, module,
    input_file, output_file, net_names, kwargs)
  - StageResult      (stage_name, stage_type, successful, failed, total_vias,
    summary: dict|None)
  - RouteReport      (stages, totals, by_type, up_to, final_board; .to_dict())
  - build_invocations(plan, ir, *, input_board, workdir, up_to=None) -> list
  - run_route_stages(plan, ir, *, input_board, workdir, up_to=None,
    invoker=default_subprocess_invoker, report_path=None) -> RouteReport
  - default_subprocess_invoker(invocation) -> StageResult
CONSUMES (unchanged, pinned as oracle): LayoutPlan, BundleStage, RouteStage,
GridRouteOverride, stackup_layers, LayoutPlanError, resolve_nets; the real router
entry signatures route.batch_route / route_diff.batch_route_diff_pairs.

== THE RATCHET (S0 discipline) ===========================================

A try/except import probe sets `_E1_LANDED` by importing the FULL surface
(build_invocations, run_route_stages, StageInvocation, StageResult, RouteReport,
default_subprocess_invoker); on any Exception they bind to a `_unlanded` stub that
raises RuntimeError (a type the loud-tests' pytest.raises will NOT catch,
preventing accidental XPASS). Module-level
`needs_e1 = pytest.mark.xfail(not _E1_LANDED, ..., strict=True)` decorates every
contract test, so they are strict-xfail until the runner lands and flip green by
the marker going inactive — a half-landed surface stays red. The PURE tests flip
green in the venv (no router needed); the E2E tests additionally SKIP loudly
(never silently pass) when the router submodule board or system python3 is absent
or cannot import the router, exactly like test_router_smoke_batch_route.py.

NOTE (gate 1 of 3 — the spec): every test body below is a `NotImplementedError`
placeholder (strict-xfail green). The biting assertion bodies land in gate 2; the
runner that flips them green lands in gate 3.
"""

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import

# --- consumed model surface (already landed; import normally) ---------------
from faebryk.exporters.pcb.layout.layout_plan import (  # noqa: F401
    Board,
    BundleStage,
    GridRouteOverride,
    LayoutPlan,
    LayoutPlanError,
    RouteStage,
    Stackup,
    StackupLayer,
    stackup_layers,
)

# ---------------------------------------------------------------------------
# S0 ratchet guard — import the FULL E1 surface; half-landing stays red.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.layout_plan_runner import (  # type: ignore
        RouteReport,
        StageInvocation,
        StageResult,
        build_invocations,
        default_subprocess_invoker,
        run_route_stages,
    )

    _E1_LANDED = True
except Exception:
    _E1_LANDED = False

    def _unlanded(*_a, **_k):
        raise RuntimeError("E1 layout_plan_runner not landed (S0 ratchet)")

    StageInvocation = StageResult = RouteReport = _unlanded
    build_invocations = run_route_stages = default_subprocess_invoker = _unlanded

needs_e1 = pytest.mark.xfail(
    not _E1_LANDED,
    reason="E1 layout_plan_runner not landed (S0 ratchet)",
    strict=True,
)

_SPEC = "ratchet body lands in gate 2 (see module docstring)"


# ===========================================================================
# R1 — dispatch selects the entry by stage type: single -> batch_route,
# diff -> batch_route_diff_pairs (the union tag, then RouteStage.mode).
# ===========================================================================
@needs_e1
def test_dispatch_selects_entry_by_stage_type():
    raise NotImplementedError("R1: " + _SPEC)


# ===========================================================================
# R2 — a bundle stage in the run slice is LOUD not-implemented (E-Tier2),
# never a silent skip (S5a); paired with a single+diff positive control.
# ===========================================================================
@needs_e1
def test_bundle_dispatch_is_loud_not_implemented():
    raise NotImplementedError("R2: " + _SPEC)


# ===========================================================================
# R3 — config expanded verbatim: explicitly-set keys forwarded as-is, unset
# (default None) keys NOT forwarded, no re-validation, no None leak.
# ===========================================================================
@needs_e1
def test_config_expanded_verbatim_unset_dropped():
    raise NotImplementedError("R3: " + _SPEC)


# ===========================================================================
# R4 — TS-AUTH-B impl side: layers == stackup_layers(board.stackup), never None
# / the 4-layer default; a plan with no board.stackup is loud.
# ===========================================================================
@needs_e1
def test_layers_sourced_from_stackup_never_default():
    raise NotImplementedError("R4: " + _SPEC)


# ===========================================================================
# R5 — single-authority: a per-stage config that EXPLICITLY sets `layers` is a
# loud conflict (the board.stackup is the sole layer authority).
# ===========================================================================
@needs_e1
def test_per_stage_config_layers_is_loud_single_authority():
    raise NotImplementedError("R5: " + _SPEC)


# ===========================================================================
# R6 — per-stage User-layer constraints forwarded (guide_corridor_enabled /
# keepout_enabled), default off = absent from kwargs when unset.
# ===========================================================================
@needs_e1
def test_user_layer_constraints_forwarded_per_stage():
    raise NotImplementedError("R6: " + _SPEC)


# ===========================================================================
# R7 — board accumulates across stages (stage k output == stage k+1 input;
# output_file == workdir/<name>.kicad_pcb) and E1 adds NO keys beyond
# config.model_fields_set | {"layers"} (no locks for prior copper).
# ===========================================================================
@needs_e1
def test_board_accumulates_across_stages_output_naming_no_locks():
    raise NotImplementedError("R7: " + _SPEC)


# ===========================================================================
# R8 — a missing JSON_SUMMARY (summary=None) is tolerated: no raise, contributes
# 0 to every total, the stage still appears as zero-routed.
# ===========================================================================
@needs_e1
def test_missing_json_summary_tolerated():
    raise NotImplementedError("R8: " + _SPEC)


# ===========================================================================
# R9 — aggregation: common scalar keys (incl. total_time, total_iterations)
# summed FROM the summary dict; per-type lists kept separate; route_report.json
# written and json.loads round-trips report.to_dict().
# ===========================================================================
@needs_e1
def test_aggregation_common_and_per_type_keys():
    raise NotImplementedError("R9: " + _SPEC)


# ===========================================================================
# R10 — --up-to <stage-name> slices route_stages[0..k]; report.up_to == name,
# len(report.stages) == k, report.final_board == invs[-1].output_file.
# ===========================================================================
@needs_e1
def test_up_to_by_stage_name_slices():
    raise NotImplementedError("R10: " + _SPEC)


# ===========================================================================
# R11 — --up-to <index> is 1-based, equivalent to the name form; report.up_to is
# normalized to the resolved stage NAME (never a raw int).
# ===========================================================================
@needs_e1
def test_up_to_by_1based_index_slices():
    raise NotImplementedError("R11: " + _SPEC)


# ===========================================================================
# R12 — --up-to out-of-range index (0, len+1) or unknown name is LOUD
# (LayoutPlanError), never a silent clamp; paired controls succeed.
# ===========================================================================
@needs_e1
def test_up_to_out_of_range_or_unknown_is_loud():
    raise NotImplementedError("R12: " + _SPEC)


# ===========================================================================
# R13 — invocation.net_names == plan.resolve_nets(ir)[name], verbatim + ordered.
# ===========================================================================
@needs_e1
def test_net_names_from_resolve_nets_verbatim():
    raise NotImplementedError("R13: " + _SPEC)


# ===========================================================================
# R14 — purity: build_invocations runs with subprocess.run monkeypatched to
# explode (it never shells out, never imports the router).
# ===========================================================================
@needs_e1
def test_build_invocations_is_pure_no_subprocess():
    raise NotImplementedError("R14: " + _SPEC)


# ===========================================================================
# E15 — e2e: default_subprocess_invoker really shells to system python3 and
# parses a JSON_SUMMARY into a StageResult (tolerating the early-return None).
# ===========================================================================
@needs_e1
def test_e2e_subprocess_invoker_single_net():
    raise NotImplementedError("E15: " + _SPEC)


# ===========================================================================
# E16 — e2e: run_route_stages with the default (real) invoker over the router
# board produces route_report.json with summed totals + per-type breakdown.
# ===========================================================================
@needs_e1
def test_e2e_full_run_writes_route_report():
    raise NotImplementedError("E16: " + _SPEC)
