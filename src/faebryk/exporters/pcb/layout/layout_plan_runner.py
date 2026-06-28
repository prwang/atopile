# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""Build-side §E1 route runner: turn `LayoutPlan.route_stages` into router
invocations and a `route_report.json` (BACKLOG §E, "E1").

The runner is split into a PURE PLANNING seam and an IMPURE EXECUTION seam so the
whole dispatch / kwargs / layers / --up-to contract is unit-testable WITHOUT the
rust router (which is not built for the atopile venv — it runs only under the
SYSTEM python3, like the §C router-oracle):

  - `build_invocations` (PURE): route_stages -> a list of `StageInvocation`. It
    selects the entry by stage type (single -> route.batch_route, diff ->
    route_diff.batch_route_diff_pairs; a bundle is LOUD not-implemented, BACKLOG
    §E-Tier2), expands `RouteStage.config` verbatim into the entry's kwargs
    (explicitly-set keys only — the router default stands otherwise), sources
    `layers` SOLELY from `stackup_layers(board.stackup)` (TS-AUTH-B: never the
    route.py 4-layer default; a per-stage `config.layers` is a loud conflict),
    resolves net names through bridge② (`resolve_nets`), and chains each stage's
    output board into the next stage's input (cross-stage prior copper is a hard
    obstacle for free — BACKLOG fact 16 — so the runner adds NO locks).
  - `default_subprocess_invoker` (IMPURE): shells a single `StageInvocation` out
    to system python3, calls the entry with `return_results=True`, and parses the
    `JSON_SUMMARY:` line back into a `StageResult` (None when the router returned
    early before printing a summary — already-connected nets, nothing to route).
  - `run_route_stages`: drives the invoker per stage, tolerates a missing summary
    (zero-routed, never a KeyError), aggregates the common scalar keys (summed
    from each stage's summary dict) and the per-type list keys (kept separate),
    writes `route_report.json`, and returns the `RouteReport`.

`--up-to <stage-name | 1-based index>` truncates the run to route_stages[0..k]
and still writes the partial board + report (each stage = a resumable checkpoint;
BACKLOG §E1). The contract is pinned by test_layout_plan_runner_contract.py.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from faebryk.exporters.pcb.layout.layout_plan import (
    BundleStage,
    LayoutPlan,
    LayoutPlanError,
    stackup_layers,
)
from faebryk.libs.util import repo_root

_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"

# the JSON_SUMMARY scalar keys both router entries emit (route.py:744-771 /
# route_diff.py:679-692). Summed across stages; per-type list keys are kept apart.
_COMMON_KEYS = ("successful", "failed", "total_time", "total_iterations", "total_vias")


@dataclass(frozen=True)
class StageInvocation:
    """A fully-resolved single router call (PURE planning output). `entry`/`module`
    name the system-python3 callable; `kwargs` is config (verbatim) + the stackup
    `layers`; `input_file`/`output_file` chain across stages."""

    stage_name: str
    stage_type: str  # "single" | "diff" | "bundle"
    entry: str  # "batch_route" | "batch_route_diff_pairs" | "batch_route_bundle"
    module: str  # "route" | "route_diff" | "route_bundle"
    input_file: str
    output_file: str
    net_names: list[str]
    kwargs: dict[str, Any]


@dataclass
class StageResult:
    """One stage's outcome. `summary` is the parsed JSON_SUMMARY dict, or None when
    the router returned early (no summary printed) — a zero-routed stage."""

    stage_name: str
    stage_type: str
    successful: int
    failed: int
    total_vias: int
    summary: dict | None


@dataclass
class RouteReport:
    """The run product written to route_report.json. `totals` sums the common
    scalar keys; `by_type` keeps the per-type list keys separate; `up_to` is the
    resolved breakpoint stage NAME (or None for a full run)."""

    stages: list[StageResult]
    totals: dict
    by_type: dict
    up_to: str | None
    final_board: str

    def to_dict(self) -> dict:
        return {
            "stages": [
                {
                    "stage_name": s.stage_name,
                    "stage_type": s.stage_type,
                    "successful": s.successful,
                    "failed": s.failed,
                    "total_vias": s.total_vias,
                    "summary": s.summary,
                }
                for s in self.stages
            ],
            "totals": self.totals,
            "by_type": self.by_type,
            "up_to": self.up_to,
            "final_board": self.final_board,
        }


def _slice_stages(route_stages: list, up_to: str | int | None) -> list:
    """route_stages[0..k] per --up-to (1-based index OR unique stage name). An
    out-of-range index or an unknown name is LOUD, never a silent clamp (S5a)."""
    n = len(route_stages)
    if up_to is None:
        return list(route_stages)
    # a CLI (§F `ato route --up-to`) hands a string; an all-digit one is an index.
    if isinstance(up_to, str) and up_to.isdigit():
        up_to = int(up_to)
    # bool is an int subclass — reject it explicitly (True/False is not an index).
    if isinstance(up_to, bool):
        raise LayoutPlanError(
            f"--up-to must be a stage name or 1-based index, got {up_to!r}"
        )
    if isinstance(up_to, int):
        if up_to < 1 or up_to > n:
            raise LayoutPlanError(
                f"--up-to index {up_to} out of range (1..{n} for {n} stage(s))"
            )
        return list(route_stages[:up_to])
    names = [st.name for st in route_stages]
    if up_to not in names:
        raise LayoutPlanError(
            f"--up-to stage name {up_to!r} not found (stages: {names})"
        )
    return list(route_stages[: names.index(up_to) + 1])


def build_invocations(
    plan: LayoutPlan,
    ir: dict[str, Any],
    *,
    input_board: str | Path,
    workdir: str | Path,
    up_to: str | int | None = None,
) -> list[StageInvocation]:
    """PURE: route_stages -> [StageInvocation]. No subprocess, no router import.

    layers come SOLELY from the board.stackup authority (TS-AUTH-B); a missing
    board/stackup or a per-stage `config.layers` is loud. A bundle stage in the
    run slice is loud not-implemented (§E-Tier2)."""
    # the single layer authority — computed once, never the route.py 4-layer default.
    if plan.board is None or plan.board.stackup is None:
        raise LayoutPlanError(
            "route runner: board.stackup is required — it is the single layer "
            "authority (TS-AUTH-B); no silent layer default"
        )
    layers = stackup_layers(plan.board.stackup)

    sliced = _slice_stages(plan.route_stages, up_to)
    resolved = plan.resolve_nets(ir)  # {stage name -> [kicad net name]} (bridge②)

    invocations: list[StageInvocation] = []
    prev_output: str | None = None
    for stage in sliced:
        if isinstance(stage, BundleStage):
            raise NotImplementedError(
                f"stage {stage.name!r}: bundle routing dispatches to "
                "route_bundle.batch_route_bundle, which is not implemented yet "
                "(BACKLOG §E-Tier2)"
            )
        if stage.mode == "single":
            entry, module, stage_type = "batch_route", "route", "single"
        else:
            entry, module, stage_type = (
                "batch_route_diff_pairs",
                "route_diff",
                "diff",
            )

        # config expanded verbatim — explicitly-set keys only (the router default
        # stands for the rest; D2 already mode-validated these keys).
        kwargs = stage.config.model_dump(exclude_unset=True)
        if "layers" in kwargs:
            raise LayoutPlanError(
                f"stage {stage.name!r}: per-stage config.layers is not allowed — "
                "the board.stackup is the single layer authority (TS-AUTH-B)"
            )
        kwargs["layers"] = list(layers)

        input_file = str(input_board) if prev_output is None else prev_output
        output_file = str(Path(workdir) / f"{stage.name}.kicad_pcb")
        invocations.append(
            StageInvocation(
                stage_name=stage.name,
                stage_type=stage_type,
                entry=entry,
                module=module,
                input_file=input_file,
                output_file=output_file,
                net_names=list(resolved[stage.name]),
                kwargs=kwargs,
            )
        )
        prev_output = output_file
    return invocations


def default_subprocess_invoker(inv: StageInvocation) -> StageResult:
    """IMPURE: run one invocation under SYSTEM python3 (the rust ext is not built
    for the venv) and parse its JSON_SUMMARY back into a StageResult. Mirrors the
    shell-out pattern in test_router_smoke_batch_route.py."""
    sys_py = shutil.which("python3")
    if sys_py is None:
        raise LayoutPlanError("route runner: no system python3 to run the router")

    driver = (
        "import sys, io, json, re\n"
        "sys.path.insert(0, " + repr(str(_ROUTER_ROOT)) + ")\n"
        "import " + inv.module + "\n"
        "NETS = " + repr(list(inv.net_names)) + "\n"
        "KW = json.loads(" + repr(json.dumps(inv.kwargs)) + ")\n"
        "_buf = io.StringIO()\n"
        "_o = sys.stdout\n"
        "sys.stdout = _buf\n"
        "try:\n"
        # return_results=False so the router WRITES output_file (return_results=True
        # returns data INSTEAD of writing — route.py:775); JSON_SUMMARY prints either
        # way (route.py:772, before that branch), so we still scrape it from stdout.
        "    " + inv.module + "." + inv.entry + "("
        + repr(inv.input_file) + ", " + repr(inv.output_file)
        + ", NETS, return_results=False, verbose=False, **KW)\n"
        "finally:\n"
        "    sys.stdout = _o\n"
        "_m = re.search(r'JSON_SUMMARY: (\\{.*\\})', _buf.getvalue())\n"
        "_summary = json.loads(_m.group(1)) if _m else None\n"
        "print('JSON_OUT' + json.dumps({'summary': _summary}))\n"
    )
    try:
        proc = subprocess.run(
            [sys_py, "-c", driver],
            cwd=str(_ROUTER_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as e:
        raise LayoutPlanError(
            f"route runner: stage {inv.stage_name!r} router subprocess timed out"
        ) from e
    if proc.returncode != 0:
        raise LayoutPlanError(
            f"route runner: stage {inv.stage_name!r} router subprocess failed "
            f"(rc={proc.returncode})\n{proc.stderr[-2000:]}"
        )
    line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("JSON_OUT")), None
    )
    if line is None:
        raise LayoutPlanError(
            f"route runner: stage {inv.stage_name!r} produced no JSON_OUT line\n"
            f"stdout tail:\n{proc.stdout[-2000:]}"
        )
    summary = json.loads(line[len("JSON_OUT"):])["summary"]

    # a zero-routed stage (nothing to route / all already connected) returns BEFORE
    # write_routed_output (route.py:349-360) — no board is written. Copy the input
    # through so the cross-stage chain (and final_board) still resolves to a board.
    out = Path(inv.output_file)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inv.input_file, inv.output_file)

    if summary is None:
        return StageResult(inv.stage_name, inv.stage_type, 0, 0, 0, None)
    return StageResult(
        inv.stage_name,
        inv.stage_type,
        int(summary.get("successful", 0)),
        int(summary.get("failed", 0)),
        int(summary.get("total_vias", 0)),
        summary,
    )


def run_route_stages(
    plan: LayoutPlan,
    ir: dict[str, Any],
    *,
    input_board: str | Path,
    workdir: str | Path,
    up_to: str | int | None = None,
    invoker: Callable[[StageInvocation], StageResult] = default_subprocess_invoker,
    report_path: str | Path | None = None,
) -> RouteReport:
    """Drive each stage through `invoker`, aggregate, write route_report.json, and
    return the RouteReport. Tolerates a missing summary (zero-routed). Works for
    full runs and --up-to partial runs (which also write a partial board+report)."""
    invocations = build_invocations(
        plan, ir, input_board=input_board, workdir=workdir, up_to=up_to
    )
    # the breakpoint is the LAST stage actually run, normalized to its NAME.
    up_to_name = None if up_to is None else invocations[-1].stage_name

    # the router writes output boards via a plain open() (no mkdir) — ensure workdir
    # exists before the first stage writes into it.
    Path(workdir).mkdir(parents=True, exist_ok=True)
    results = [invoker(inv) for inv in invocations]

    totals = {k: 0 for k in _COMMON_KEYS}
    by_type: dict[str, dict] = {}
    for res in results:
        if res.summary is None:
            continue  # zero-routed: contributes nothing, but the stage stays listed
        for k in _COMMON_KEYS:
            totals[k] += res.summary.get(k, 0)
        bucket = by_type.setdefault(res.stage_type, {})
        for key, value in res.summary.items():
            if key not in _COMMON_KEYS and isinstance(value, list):
                bucket.setdefault(key, []).extend(value)

    final_board = invocations[-1].output_file if invocations else str(input_board)
    report = RouteReport(
        stages=results,
        totals=totals,
        by_type=by_type,
        up_to=up_to_name,
        final_board=final_board,
    )

    out = (
        Path(report_path)
        if report_path is not None
        else Path(workdir) / "route_report.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2))
    return report
