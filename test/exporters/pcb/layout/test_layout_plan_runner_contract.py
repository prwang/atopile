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
   A bundle stage expands `bundle_artifact` (segmented trunk + ordered member
   offsets + breakouts) into the invocation, with member nets (and each diff
   member's partner) RESOLVED through bridge② to kicad names — NOT the ato
   addresses — and the payload (trunk/members/breakouts) carried in kwargs. The
   runner consumes `route_bundle.batch_route_bundle`, whose calling convention
   differs from single/diff (geometry-driven, not net-name-driven); the
   default invoker dispatches that shape (§E-Tier2). (Before §E-Tier2 a bundle in
   the run slice was loud not-implemented; that gate is now lifted.)

2. CONFIG EXPANDED VERBATIM: RouteStage.config (GridRouteOverride) is expanded
   into the entry's kwargs as-is, EXPLICITLY-SET keys only
   (model_dump(exclude_unset=True)) — an unset field (default None) is NOT
   forwarded, so the router's own default stands and no None leaks across. E1
   does NOT re-validate config: keys are already mode-validated at D2 parse
   (RouteStage._validate_mode_kwargs); the runner trusts that partition.

3. LAYERS FROM THE STACKUP AUTHORITY (TS-AUTH-B): every invocation's kwargs carry
   layers explicitly == stackup_layers(plan.board.stackup). The runner NEVER
   passes layers=None and so NEVER falls through to route.py:230-231
   DEFAULT_4_LAYER_STACK. A missing board.stackup (or no board at all) is loud
   (LayoutPlanError — no silent layer-count default). This is the implementation
   half of the TS-AUTH-B ratchet in test_board_section_contract.py.

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
   routed_diff_pairs/failed_diff_pairs; bundle -> routed_members/failed_members).
   run_route_stages writes route_report.json
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
"""

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import

# --- consumed model surface (already landed; import normally) ---------------
from faebryk.exporters.pcb.layout.layout_plan import (
    Board,
    BundleStage,  # noqa: F401  # named in the protocol; isinstance-tagged in runner
    GridRouteOverride,
    LayoutPlan,
    LayoutPlanError,
    RouteStage,
    Stackup,
    StackupLayer,
    load_layout_plan,
)
from faebryk.libs.util import repo_root

# ---------------------------------------------------------------------------
# S0 ratchet guard — import the FULL E1 surface; half-landing stays red.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.layout_plan_runner import (  # type: ignore
        RouteReport,  # noqa: F401
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

# ---------------------------------------------------------------------------
# fixtures — build_invocations is PURE, so input_board/workdir need not exist for
# the pure tests; only the e2e + report-writing tests use a real tmp_path.
# ---------------------------------------------------------------------------
_IN = "/e1/in.kicad_pcb"
_WD = "/e1/wd"
_TWO_LAYER = ["F.Cu", "B.Cu"]
_FOUR_LAYER = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]  # route.py default E1 must NOT eat


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


def _board2() -> Board:
    return Board(stackup=_stackup2())


def _ir(signal_nets: dict[str, str]) -> dict:
    return {"signal_nets": dict(signal_nets)}


def _plan(stages, board="2") -> LayoutPlan:
    """A plan over the given stages. board="2" ⇒ the 2-copper stackup; None ⇒ no
    board (for the loud-no-stackup test); a Board instance ⇒ used verbatim."""
    if board == "2":
        board = _board2()
    return LayoutPlan(board=board, route_stages=stages)


def _invs(plan, ir, **kw):
    kw.setdefault("input_board", _IN)
    kw.setdefault("workdir", _WD)
    return build_invocations(plan, ir, **kw)


def _three_stages():
    """3 single stages s1/s2/s3 — the --up-to slicing fixture."""
    return [RouteStage(name=f"s{i}", nets=["top.a"], mode="single") for i in (1, 2, 3)]


def _fake_invoker(summaries: dict):
    """A pure invoker: summaries[stage_name] is the canned JSON_SUMMARY dict, or
    None for the early-return (no-summary) case. Mirrors what the real subprocess
    invoker would parse, so run_route_stages aggregation is exercised router-free."""

    def invoke(inv):
        s = summaries.get(inv.stage_name)
        if s is None:
            return StageResult(
                stage_name=inv.stage_name,
                stage_type=inv.stage_type,
                successful=0,
                failed=0,
                total_vias=0,
                summary=None,
            )
        return StageResult(
            stage_name=inv.stage_name,
            stage_type=inv.stage_type,
            successful=int(s.get("successful", 0)),
            failed=int(s.get("failed", 0)),
            total_vias=int(s.get("total_vias", 0)),
            summary=s,
        )

    return invoke


# a minimal valid bundle (1 single + 1 diff) with a 2-copper board, for R2.
_BUNDLE_YAML = (
    "board:\n"
    "  stackup:\n"
    "    layers:\n"
    "      - {name: F.Cu, type: copper, thickness: 0.035}\n"
    "      - {name: d1, type: dielectric, thickness: 0.2, "
    "material: FR4, epsilon_r: 4.5}\n"
    "      - {name: B.Cu, type: copper, thickness: 0.035}\n"
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
    "      track_width: 0.1\n"
)
_BUNDLE_IR = _ir({"top.a.x": "/X", "top.d.p": "/DP", "top.d.n": "/DN"})

# a single stage THEN the same bundle — for the "bundle OUTSIDE the --up-to slice
# must not raise" nuance (the loudness is per the RUN SLICE, not the whole plan).
_SINGLE_THEN_BUNDLE_YAML = (
    "board:\n"
    "  stackup:\n"
    "    layers:\n"
    "      - {name: F.Cu, type: copper, thickness: 0.035}\n"
    "      - {name: d1, type: dielectric, thickness: 0.2, "
    "material: FR4, epsilon_r: 4.5}\n"
    "      - {name: B.Cu, type: copper, thickness: 0.035}\n"
    "route_stages:\n"
    "  - name: pre\n"
    "    mode: single\n"
    "    nets:\n"
    "      - top.s\n"
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
    "      track_width: 0.1\n"
)
_SINGLE_THEN_BUNDLE_IR = _ir(
    {"top.s": "/S", "top.a.x": "/X", "top.d.p": "/DP", "top.d.n": "/DN"}
)


# ---------------------------------------------------------------------------
# S0 gate (bundle dispatch): does build_invocations expand a BundleStage into a
# route_bundle.batch_route_bundle invocation? Before §E-Tier2 it raised
# NotImplementedError ⇒ the bundle-dispatch tests are strict-xfail and flip green
# when the runner learns the bundle shape (gate 3). The probe is PURE (no router).
# ---------------------------------------------------------------------------
def _bundle_dispatch_landed() -> bool:
    if not _E1_LANDED:
        return False
    try:
        plan = load_layout_plan(_BUNDLE_YAML)
        invs = build_invocations(plan, _BUNDLE_IR, input_board=_IN, workdir=_WD)
    except Exception:
        return False
    return any(i.stage_type == "bundle" for i in invs)


needs_bundle_dispatch = pytest.mark.xfail(
    not _bundle_dispatch_landed(),
    reason="E-Tier2 bundle dispatch (build_invocations -> batch_route_bundle) absent",
    strict=True,
)


def _route_bundle_landed() -> bool:
    """AST-probe vendor route_bundle.py for batch_route_bundle (not imported: the
    router pulls scipy + a rust ext). The router-runtime gate for the bundle e2e —
    distinct from the pure-runner _bundle_dispatch_landed probe above."""
    path = repo_root() / "vendor" / "KiCadRoutingTools" / "route_bundle.py"
    if not path.exists():
        return False
    return any(
        isinstance(n, ast.FunctionDef) and n.name == "batch_route_bundle"
        for n in ast.walk(ast.parse(path.read_text()))
    )


needs_e_tier2 = pytest.mark.xfail(
    not _route_bundle_landed(),
    reason="E-Tier2 route_bundle.batch_route_bundle not landed (router runtime)",
    strict=True,
)


# ---------------------------------------------------------------------------
# e2e router guard — same pattern as test_router_smoke_batch_route.py.
# ---------------------------------------------------------------------------
_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_BOARD = _ROUTER_ROOT / "kicad_files" / "lvds_converter_dualclk.kicad_pcb"
_SYS_PY = shutil.which("python3")

needs_router = pytest.mark.skipif(
    not _BOARD.exists() or _SYS_PY is None,
    reason=f"router submodule board or system python3 absent ({_BOARD})",
)


def _router_importable() -> bool:
    if _SYS_PY is None:
        return False
    proc = subprocess.run(
        [_SYS_PY, "-c", "import route, route_diff"],
        cwd=str(_ROUTER_ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


# ===========================================================================
# R1 — dispatch selects the entry by stage type: single -> batch_route,
# diff -> batch_route_diff_pairs (the union tag, then RouteStage.mode).
# ===========================================================================
@needs_e1
def test_dispatch_selects_entry_by_stage_type():
    plan = _plan(
        [
            RouteStage(name="s1", nets=["top.a"], mode="single"),
            RouteStage(name="s2", nets=["top.b", "top.c"], mode="diff"),
        ]
    )
    ir = _ir({"top.a": "/A", "top.b": "/B+", "top.c": "/B-"})
    invs = _invs(plan, ir)
    assert invs[0].entry == "batch_route" and invs[0].module == "route"
    assert invs[0].stage_type == "single"
    assert invs[1].entry == "batch_route_diff_pairs" and invs[1].module == "route_diff"
    assert invs[1].stage_type == "diff"


# ===========================================================================
# R2 — a bundle stage DISPATCHES to route_bundle.batch_route_bundle: the runner
# expands bundle_artifact (segmented trunk + ordered member offsets + breakouts)
# into the invocation, members RESOLVED to kicad names through bridge② (NOT ato
# addresses), layers from the stackup authority. (§E-Tier2 lifted the former
# loud-not-implemented gate.)
# ===========================================================================
@needs_bundle_dispatch
def test_bundle_dispatch_to_batch_route_bundle():
    plan = load_layout_plan(_BUNDLE_YAML)
    inv = build_invocations(plan, _BUNDLE_IR, input_board=_IN, workdir=_WD)[0]
    assert inv.stage_type == "bundle"
    assert inv.entry == "batch_route_bundle" and inv.module == "route_bundle"
    # the bundle payload rides in kwargs (segmented trunk + ordered members + 2
    # breakouts) — a geometry-driven call, not a net-name-driven one.
    assert {"trunk", "members", "breakouts"} <= set(inv.kwargs)
    assert len(inv.kwargs["breakouts"]) == 2
    # members carry RESOLVED kicad net names (bridge②), in member order — NOT the
    # ato addresses (top.a.x / top.d.p / top.d.n).
    assert [m["net"] for m in inv.kwargs["members"]] == ["/X", "/DP", "/DN"]
    # a diff member's PARTNER is resolved too (coupling survives the bridge).
    dp = next(m for m in inv.kwargs["members"] if m["net"] == "/DP")
    assert dp["kind"] == "diff" and dp["diff_partner"] == "/DN"
    # the breakout D/E seam: bundle_artifact emits `at`-keyed, ato-address order;
    # the runner must translate to the FROZEN batch_route_bundle shape — key `part`
    # with `order` RESOLVED to kicad names (the smoke-frozen T-B2 shape). Pin the
    # key, the `part` VALUE (the breakout room address — the first delivery passes
    # the room address through as the fanout locator; see BACKLOG limitation), and
    # the resolved order content (derived == member order here).
    assert [bo["part"] for bo in inv.kwargs["breakouts"]] == ["top.a", "top.d"]
    for bo in inv.kwargs["breakouts"]:
        assert "at" not in bo
        assert bo["order"] == ["/X", "/DP", "/DN"]
    # layers from the stackup authority (TS-AUTH-B), never the 4-layer default.
    assert inv.kwargs["layers"] == ["F.Cu", "B.Cu"]
    # net_names is the resolved member list (for chaining/reporting).
    assert inv.net_names == ["/X", "/DP", "/DN"]


# ===========================================================================
# R2b — bundle dispatch is per the RUN SLICE: the full plan dispatches BOTH the
# single and the bundle; --up-to before the bundle stops at the single.
# ===========================================================================
@needs_bundle_dispatch
def test_bundle_in_vs_outside_up_to_slice():
    plan = load_layout_plan(_SINGLE_THEN_BUNDLE_YAML)
    ir = _SINGLE_THEN_BUNDLE_IR
    invs = build_invocations(plan, ir, input_board=_IN, workdir=_WD)
    assert [i.stage_name for i in invs] == ["pre", "b"]
    assert invs[0].stage_type == "single" and invs[1].stage_type == "bundle"
    # --up-to the single stage stops BEFORE the bundle ⇒ just the single.
    pre = build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to="pre")
    assert [i.stage_name for i in pre] == ["pre"]


# ===========================================================================
# R2c — bundle config (BundleRouteConfig) expands verbatim, explicitly-set keys
# only: _BUNDLE_YAML sets only config.track_width ⇒ it reaches kwargs, an UNSET
# field (clearance, via_size, ...) is NOT forwarded (the router default stands),
# and no None leaks. The bundle analogue of R3.
# ===========================================================================
@needs_bundle_dispatch
def test_bundle_config_expanded_verbatim():
    plan = load_layout_plan(_BUNDLE_YAML)  # config: {track_width: 0.1}
    inv = build_invocations(plan, _BUNDLE_IR, input_board=_IN, workdir=_WD)[0]
    assert inv.kwargs["track_width"] == 0.1  # explicitly set ⇒ forwarded
    assert "clearance" not in inv.kwargs  # unset ⇒ router default stands
    assert "via_size" not in inv.kwargs and "via_drill" not in inv.kwargs
    assert None not in inv.kwargs.values()  # no None leak


# ===========================================================================
# R2d — single layer authority for a bundle too (TS-AUTH-B): a per-stage
# config.layers is a LOUD conflict — a bundle may not override the board stackup.
# The bundle analogue of R5.
# ===========================================================================
_BUNDLE_LAYERS_CONFLICT_YAML = _BUNDLE_YAML.replace(
    "    config:\n      track_width: 0.1\n",
    "    config:\n      track_width: 0.1\n      layers: [F.Cu, B.Cu]\n",
)


@needs_bundle_dispatch
def test_bundle_per_stage_layers_is_loud():
    plan = load_layout_plan(_BUNDLE_LAYERS_CONFLICT_YAML)
    with pytest.raises(LayoutPlanError):
        build_invocations(plan, _BUNDLE_IR, input_board=_IN, workdir=_WD)
    # positive control: the same bundle WITHOUT config.layers builds fine.
    ok = load_layout_plan(_BUNDLE_YAML)
    assert build_invocations(ok, _BUNDLE_IR, input_board=_IN, workdir=_WD)


# ===========================================================================
# R2e — aggregation for a bundle (router-free, via the fake invoker): the common
# scalars SUM and the per-type list keys (routed_members/failed_members) land in
# by_type['bundle'] WITHOUT leaking into single/diff. The bundle analogue of R9.
# ===========================================================================
@needs_bundle_dispatch
def test_bundle_aggregation_buckets_per_type(tmp_path):
    plan = load_layout_plan(_BUNDLE_YAML)
    canned = {
        "successful": 2,
        "failed": 1,
        "total_vias": 3,
        "total_time": 1.5,
        "total_iterations": 100,
        "routed_members": ["/X", "/DP"],
        "failed_members": ["/DN"],
    }
    report = run_route_stages(
        plan,
        _BUNDLE_IR,
        input_board=_IN,
        workdir=tmp_path,
        invoker=_fake_invoker({"b": canned}),
    )
    assert report.totals["successful"] == 2 and report.totals["failed"] == 1
    assert report.totals["total_vias"] == 3
    assert report.by_type["bundle"]["routed_members"] == ["/X", "/DP"]
    assert report.by_type["bundle"]["failed_members"] == ["/DN"]
    assert "single" not in report.by_type and "diff" not in report.by_type


# ===========================================================================
# R3 — config expanded verbatim: explicitly-set keys forwarded as-is, unset
# (default None) keys NOT forwarded, no re-validation, no None leak.
# ===========================================================================
@needs_e1
def test_config_expanded_verbatim_unset_dropped():
    stage = RouteStage(
        name="s",
        nets=["top.a"],
        mode="single",
        config=GridRouteOverride(track_width=0.3, clearance=0.15),
    )
    inv = _invs(_plan([stage]), _ir({"top.a": "/A"}))[0]
    assert inv.kwargs["track_width"] == 0.3 and inv.kwargs["clearance"] == 0.15
    # unset fields are NOT forwarded — the router default stands, no None leaks.
    assert "via_size" not in inv.kwargs and "impedance" not in inv.kwargs
    assert all(v is not None for v in inv.kwargs.values())
    # E1 adds nothing beyond the explicitly-set config keys + the stackup layers.
    assert set(inv.kwargs) == set(stage.config.model_fields_set) | {"layers"}


# ===========================================================================
# R4 — TS-AUTH-B impl side: layers == stackup_layers(board.stackup), never None
# / the 4-layer default; a plan with no board/stackup is loud.
# ===========================================================================
@needs_e1
def test_layers_sourced_from_stackup_never_default():
    plan = _plan([RouteStage(name="s", nets=["top.a"], mode="single")])
    inv = _invs(plan, _ir({"top.a": "/A"}))[0]
    assert inv.kwargs["layers"] == _TWO_LAYER  # the stackup's ordered copper set
    assert inv.kwargs["layers"] is not None and inv.kwargs["layers"] != _FOUR_LAYER
    # negative: no stackup (and no board at all) ⇒ loud, never a silent default.
    no_stk = _plan([RouteStage(name="s", nets=["top.a"], mode="single")], board=Board())
    with pytest.raises(LayoutPlanError):
        _invs(no_stk, _ir({"top.a": "/A"}))
    no_board = _plan([RouteStage(name="s", nets=["top.a"], mode="single")], board=None)
    with pytest.raises(LayoutPlanError):
        _invs(no_board, _ir({"top.a": "/A"}))


# ===========================================================================
# R5 — single-authority: a per-stage config that EXPLICITLY sets `layers` is a
# loud conflict (the board.stackup is the sole layer authority).
# ===========================================================================
@needs_e1
def test_per_stage_config_layers_is_loud_single_authority():
    bad = _plan(
        [
            RouteStage(
                name="s",
                nets=["top.a"],
                mode="single",
                config=GridRouteOverride(layers=["F.Cu", "B.Cu"]),
            )
        ]
    )
    with pytest.raises(LayoutPlanError):
        _invs(bad, _ir({"top.a": "/A"}))
    # control: the SAME stage without a per-stage layers override builds, sourcing
    # layers from the stackup authority.
    ok = _plan([RouteStage(name="s", nets=["top.a"], mode="single")])
    assert _invs(ok, _ir({"top.a": "/A"}))[0].kwargs["layers"] == _TWO_LAYER


# ===========================================================================
# R6 — per-stage User-layer constraints forwarded (guide_corridor_enabled /
# keepout_enabled), default off = absent from kwargs when unset.
# ===========================================================================
@needs_e1
def test_user_layer_constraints_forwarded_per_stage():
    on = _plan(
        [
            RouteStage(
                name="s",
                nets=["top.a"],
                mode="single",
                config=GridRouteOverride(
                    guide_corridor_enabled=True, keepout_enabled=True
                ),
            )
        ]
    )
    k = _invs(on, _ir({"top.a": "/A"}))[0].kwargs
    assert k["guide_corridor_enabled"] is True and k["keepout_enabled"] is True
    # a stage that sets neither ⇒ neither key present (default off, router decides).
    off = _plan([RouteStage(name="s", nets=["top.a"], mode="single")])
    k2 = _invs(off, _ir({"top.a": "/A"}))[0].kwargs
    assert "guide_corridor_enabled" not in k2 and "keepout_enabled" not in k2


# ===========================================================================
# R7 — board accumulates across stages (stage k output == stage k+1 input;
# output_file == workdir/<name>.kicad_pcb) and E1 adds NO keys beyond
# config.model_fields_set | {"layers"} (no locks for prior copper).
# ===========================================================================
@needs_e1
def test_board_accumulates_across_stages_output_naming_no_locks():
    stages = [RouteStage(name=f"s{i}", nets=["top.a"], mode="single") for i in range(3)]
    plan = _plan(stages)
    invs = _invs(plan, _ir({"top.a": "/A"}))
    assert invs[0].input_file == _IN  # stage 0 starts from the original board
    for k in range(2):
        assert invs[k].output_file == invs[k + 1].input_file  # chained
    for inv in invs:
        assert inv.output_file.endswith(f"{inv.stage_name}.kicad_pcb")  # per stage
    # no locks / presets: E1 forwards ONLY the config-set keys + layers, nothing else.
    for i, inv in enumerate(invs):
        cfg = plan.route_stages[i].config
        assert set(inv.kwargs) <= set(cfg.model_fields_set) | {"layers"}


# ===========================================================================
# R8 — a missing JSON_SUMMARY (summary=None) is tolerated: no raise, contributes
# 0 to every total, the stage still appears as zero-routed.
# ===========================================================================
@needs_e1
def test_missing_json_summary_tolerated(tmp_path):
    plan = _plan(
        [
            RouteStage(name="s1", nets=["top.a"], mode="single"),
            RouteStage(name="s2", nets=["top.b"], mode="single"),
        ]
    )
    ir = _ir({"top.a": "/A", "top.b": "/B"})
    summaries = {
        "s1": None,  # early return: nothing to route ⇒ no JSON_SUMMARY
        "s2": {
            "successful": 1,
            "failed": 0,
            "total_time": 0.1,
            "total_iterations": 3,
            "total_vias": 1,
            "routed_single": ["/B"],
            "failed_single": [],
        },
    }
    report = run_route_stages(
        plan, ir, input_board=_IN, workdir=tmp_path, invoker=_fake_invoker(summaries)
    )
    assert len(report.stages) == 2  # the None-summary stage is NOT dropped
    assert report.totals["successful"] == 1 and report.totals["total_vias"] == 1
    s1 = next(s for s in report.stages if s.stage_name == "s1")
    assert s1.summary is None and s1.successful == 0  # present, zero-routed
    assert report.up_to is None  # a full (non-truncated) run carries no breakpoint
    # the report_path override is honored (the surface beyond the default location).
    custom = tmp_path / "custom_report.json"
    run_route_stages(
        plan, ir, input_board=_IN, workdir=tmp_path,
        invoker=_fake_invoker(summaries), report_path=custom,
    )
    assert custom.exists()


# ===========================================================================
# R9 — aggregation: common scalar keys (incl. total_time, total_iterations)
# summed FROM the summary dict; per-type lists kept separate; route_report.json
# written and json.loads round-trips report.to_dict().
# ===========================================================================
@needs_e1
def test_aggregation_common_and_per_type_keys(tmp_path):
    import json

    plan = _plan(
        [
            RouteStage(name="se", nets=["top.a"], mode="single"),
            RouteStage(name="dp", nets=["top.b", "top.c"], mode="diff"),
        ]
    )
    ir = _ir({"top.a": "/A", "top.b": "/P", "top.c": "/N"})
    summaries = {
        "se": {
            "successful": 2,
            "failed": 1,
            "total_time": 0.5,
            "total_iterations": 10,
            "total_vias": 3,
            "routed_single": ["/A", "/A2"],
            "failed_single": ["/A3"],
        },
        "dp": {
            "successful": 1,
            "failed": 0,
            "total_time": 0.3,
            "total_iterations": 5,
            "total_vias": 2,
            "routed_diff_pairs": [["/P", "/N"]],
            "failed_diff_pairs": [],
        },
    }
    report = run_route_stages(
        plan, ir, input_board=_IN, workdir=tmp_path, invoker=_fake_invoker(summaries)
    )
    # common scalar keys summed from the summary dicts.
    assert report.totals["successful"] == 3 and report.totals["failed"] == 1
    assert report.totals["total_vias"] == 5
    assert report.totals["total_time"] == pytest.approx(0.8)
    assert report.totals["total_iterations"] == 15
    # per-type list keys kept SEPARATE by entry type.
    assert "routed_single" in report.by_type["single"]
    assert "routed_diff_pairs" not in report.by_type["single"]
    assert "routed_diff_pairs" in report.by_type["diff"]
    assert "routed_single" not in report.by_type["diff"]
    # route_report.json written and round-trips the report dict.
    rp = tmp_path / "route_report.json"
    assert rp.exists()
    assert json.loads(rp.read_text()) == report.to_dict()


# ===========================================================================
# R10 — --up-to <stage-name> slices route_stages[0..k]; report.up_to == name,
# len(report.stages) == k, report.final_board == invs[-1].output_file.
# ===========================================================================
@needs_e1
def test_up_to_by_stage_name_slices(tmp_path):
    plan = _plan(_three_stages())
    ir = _ir({"top.a": "/A"})
    invs = build_invocations(plan, ir, input_board=_IN, workdir=tmp_path, up_to="s2")
    assert [i.stage_name for i in invs] == ["s1", "s2"]  # sliced at the named stage
    report = run_route_stages(
        plan, ir, input_board=_IN, workdir=tmp_path, up_to="s2",
        invoker=_fake_invoker({}),
    )
    assert report.up_to == "s2" and len(report.stages) == 2
    assert report.final_board == invs[-1].output_file  # partial board pinned (pure)


# ===========================================================================
# R11 — --up-to <index> is 1-based, equivalent to the name form; report.up_to is
# normalized to the resolved stage NAME (never a raw int).
# ===========================================================================
@needs_e1
def test_up_to_by_1based_index_slices(tmp_path):
    plan = _plan(_three_stages())
    ir = _ir({"top.a": "/A"})
    by_name = build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to="s2")
    by_idx = build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to=2)
    names = [i.stage_name for i in by_name]
    assert [i.stage_name for i in by_idx] == names == ["s1", "s2"]
    # up_to == len ⇒ the full plan.
    full = build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to=3)
    assert len(full) == 3
    # report.up_to is normalized to the stage NAME for the index form.
    report = run_route_stages(
        plan, ir, input_board=_IN, workdir=tmp_path, up_to=2, invoker=_fake_invoker({})
    )
    assert report.up_to == "s2"


# ===========================================================================
# R12 — --up-to out-of-range index (0, len+1) or unknown name is LOUD
# (LayoutPlanError), never a silent clamp; paired controls succeed.
# ===========================================================================
@needs_e1
@pytest.mark.parametrize("up_to", [0, 4, "nope"], ids=["zero", "over", "unknown_name"])
def test_up_to_out_of_range_or_unknown_is_loud(up_to):
    plan = _plan(_three_stages())
    ir = _ir({"top.a": "/A"})
    with pytest.raises(LayoutPlanError):
        build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to=up_to)
    # controls: 1-based index 1 and the matching name both resolve.
    assert build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to=1)
    assert build_invocations(plan, ir, input_board=_IN, workdir=_WD, up_to="s1")


# ===========================================================================
# R13 — invocation.net_names == plan.resolve_nets(ir)[name], verbatim + ordered.
# ===========================================================================
@needs_e1
def test_net_names_from_resolve_nets_verbatim():
    stage = RouteStage(
        name="dp",
        nets=["top.a", "top.b"],
        mode="diff",
        config=GridRouteOverride(diff_pair_gap=0.2),
    )
    plan = _plan([stage])
    ir = _ir({"top.a": "/A", "top.b": "/B"})
    inv = _invs(plan, ir)[0]
    assert inv.net_names == plan.resolve_nets(ir)["dp"] == ["/A", "/B"]


# ===========================================================================
# R14 — purity: build_invocations runs with subprocess.run monkeypatched to
# explode (it never shells out, never imports the router).
# ===========================================================================
@needs_e1
def test_build_invocations_is_pure_no_subprocess(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("build_invocations shelled out — it must be pure")

    monkeypatch.setattr(subprocess, "run", _boom)
    plan = _plan([RouteStage(name="s", nets=["top.a"], mode="single")])
    invs = _invs(plan, _ir({"top.a": "/A"}))
    assert invs and invs[0].entry == "batch_route"


# ===========================================================================
# E15 — e2e: default_subprocess_invoker really shells to system python3 and
# parses a JSON_SUMMARY into a StageResult (tolerating the early-return None).
# ===========================================================================
@needs_e1
@needs_router
def test_e2e_subprocess_invoker_single_net(tmp_path):
    if not _router_importable():
        pytest.skip("system python3 cannot import the router (ext not built)")
    out = tmp_path / "s.kicad_pcb"
    inv = StageInvocation(
        stage_name="s",
        stage_type="single",
        entry="batch_route",
        module="route",
        input_file=str(_BOARD),
        output_file=str(out),
        net_names=["/DATA+"],
        kwargs={"layers": _TWO_LAYER, "track_width": 0.2, "clearance": 0.2},
    )
    res = default_subprocess_invoker(inv)
    # the invoker PLUMBING: a StageResult carrying the stage identity, regardless of
    # whether the single-ended route completed (early return ⇒ summary None).
    assert res.stage_name == "s" and res.stage_type == "single"
    assert res.summary is None or (
        {"successful", "failed", "total_vias"} <= res.summary.keys()
        and {"routed_single", "failed_single"} <= res.summary.keys()
    )


# ===========================================================================
# E16 — e2e: run_route_stages with the default (real) invoker over the router
# board produces route_report.json with summed totals + per-type breakdown.
# ===========================================================================
@needs_e1
@needs_router
def test_e2e_full_run_writes_route_report(tmp_path):
    if not _router_importable():
        pytest.skip("system python3 cannot import the router (ext not built)")
    plan = _plan(
        [
            RouteStage(
                name="dp",
                nets=["x.p", "x.n"],
                mode="diff",
                config=GridRouteOverride(
                    track_width=0.2, clearance=0.2, diff_pair_gap=0.25
                ),
            )
        ]
    )
    ir = _ir({"x.p": "/DATA+", "x.n": "/DATA-"})
    report = run_route_stages(plan, ir, input_board=str(_BOARD), workdir=tmp_path)
    assert (tmp_path / "route_report.json").exists()
    assert "diff" in report.by_type and "routed_diff_pairs" in report.by_type["diff"]
    assert report.final_board == str(tmp_path / "dp.kicad_pcb")
    # the routed board is actually WRITTEN (catches return_results=True, which would
    # return data instead of writing the file — route.py:775).
    assert Path(report.final_board).exists()
    # the LVDS board's one diff pair routes deterministically (smoke oracle 1/0),
    # so a routing regression turns this red instead of passing on >= 0.
    assert report.totals["successful"] >= 1


# ===========================================================================
# E17 — e2e: two stages chain (stage 1's output board feeds stage 2), and a
# zero-routed stage (nets already connected ⇒ no JSON_SUMMARY) is tolerated AND
# still chains a board through (the partial/final board always resolves).
# ===========================================================================
@needs_e1
@needs_router
def test_e2e_two_stage_chaining_and_missing_summary(tmp_path):
    if not _router_importable():
        pytest.skip("system python3 cannot import the router (ext not built)")
    plan = _plan(
        [
            RouteStage(
                name="dp",
                nets=["x.p", "x.n"],
                mode="diff",
                config=GridRouteOverride(
                    track_width=0.2, clearance=0.2, diff_pair_gap=0.25
                ),
            ),
            # stage 2 reads stage 1's routed board; the pair is now connected, so
            # single-mode routing finds nothing to do ⇒ early return, no summary.
            RouteStage(
                name="again",
                nets=["x.p"],
                mode="single",
                config=GridRouteOverride(track_width=0.2, clearance=0.2),
            ),
        ]
    )
    ir = _ir({"x.p": "/DATA+", "x.n": "/DATA-"})
    report = run_route_stages(plan, ir, input_board=str(_BOARD), workdir=tmp_path)
    assert len(report.stages) == 2
    again = next(s for s in report.stages if s.stage_name == "again")
    assert again.summary is None  # nothing to route ⇒ tolerated, zero-routed
    # the zero-routed last stage still produces a board (copied through) ⇒ the chain
    # and final_board resolve to a real file.
    assert report.final_board == str(tmp_path / "again.kicad_pcb")
    assert Path(report.final_board).exists()


# a mixed bundle (1 single + 1 diff) over the LVDS board's real nets, for the
# bucket③ e2e: a 2-copper stackup (so layers == [F.Cu, B.Cu]) + a trunk inside the
# board area. The member addresses resolve through bridge② to real board nets.
_BUNDLE_E2E_YAML = (
    "board:\n"
    "  stackup:\n"
    "    layers:\n"
    "      - {name: F.Cu, type: copper, thickness: 0.035}\n"
    "      - {name: d1, type: dielectric, thickness: 0.2, "
    "material: FR4, epsilon_r: 4.5}\n"
    "      - {name: B.Cu, type: copper, thickness: 0.035}\n"
    "route_stages:\n"
    "  - type: bundle\n"
    "    name: link\n"
    "    lanes:\n"
    "      - net: top.o\n"
    "      - diff: [top.d.p, top.d.n]\n"
    "        gap: 0.25\n"
    "        width: 0.2\n"
    "    trunk:\n"
    "      centerline:\n"
    "        - at: [150, 100]\n          spacing: 0.5\n"
    "        - at: [160, 100]\n          spacing: 0.5\n"
    "    breakouts:\n"
    "      - at: top.o\n"
    "      - at: top.d\n"
    "    config:\n"
    "      track_width: 0.2\n"
    "      clearance: 0.2\n"
)
_BUNDLE_E2E_IR = _ir({"top.o": "/OUT_A", "top.d.p": "/DATA+", "top.d.n": "/DATA-"})


# ===========================================================================
# E18 — e2e (bucket③): run_route_stages dispatches a real bundle stage through
# route_bundle.batch_route_bundle on the LVDS board, writes route_report.json with
# a per-type 'bundle' breakdown, lands all members, and writes back a board that
# parses. The end-to-end integration: plan -> bundle_artifact -> batch_route_bundle
# -> board + report.
# ===========================================================================
# needs_e_tier2 (route_bundle.py present) AND needs_bundle_dispatch (the pure
# runner probe is blind to route_bundle.py — without this an early runner-dispatch
# landing would flip E18 from a clean XFAIL to a hard subprocess error).
@needs_e_tier2
@needs_bundle_dispatch
@needs_router
def test_e2e_bundle_run_writes_report_and_board(tmp_path):
    if not _router_importable():
        pytest.skip("system python3 cannot import the router (ext not built)")
    plan = load_layout_plan(_BUNDLE_E2E_YAML)
    report = run_route_stages(
        plan, _BUNDLE_E2E_IR, input_board=str(_BOARD), workdir=tmp_path
    )
    assert (tmp_path / "route_report.json").exists()
    # the bundle stage is dispatched and aggregated under by_type['bundle'].
    assert "bundle" in report.by_type
    assert "routed_members" in report.by_type["bundle"]
    # the single + the diff pair all land along the trunk (parallel bus + fanout).
    routed = set(report.by_type["bundle"]["routed_members"])
    assert {"/OUT_A", "/DATA+", "/DATA-"} <= routed
    # NOT just the router's self-report: the scalar oracle (each member contributes)
    # and the board actually GREW new copper (a no-op/copy-through impl has the same
    # segment count and fails here).
    assert report.totals["successful"] >= 3
    before = _BOARD.read_text().count("(segment")
    after = Path(report.final_board).read_text().count("(segment")
    assert after > before, f"no new copper written (before={before}, after={after})"
    # the routed board is written and is a parseable kicad_pcb.
    assert report.final_board == str(tmp_path / "link.kicad_pcb")
    assert Path(report.final_board).exists()
    assert "(kicad_pcb" in Path(report.final_board).read_text()[:200]
