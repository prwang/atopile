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
    route_diff.batch_route_diff_pairs; bundle -> route_bundle.batch_route_bundle,
    expanding bundle_artifact into a geometry-driven payload with member nets
    resolved through bridge② — §E-Tier2), expands `RouteStage.config` verbatim
    into the entry's kwargs
    (explicitly-set keys only — the router default stands otherwise; the ONE
    non-verbatim key is `length_match_groups`, whose entries resolve through
    `_resolve_length_match_groups`: bridge② address or the exact board net
    name of one of the stage's own nets, membership-checked — anything else,
    including a net another stage routes, is loud — F3), sources
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

# verbose per-stage detail (the bundle `members` table carries full polylines) — kept
# in each stage's own summary, but NOT bucketed into by_type (it would duplicate the
# geometry and accumulate across multi-stage runs, bloating route_report.json).
_DETAIL_KEYS = ("members",)


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
    the router returned early (no summary printed) — a zero-routed stage. `diag` is
    the parsed JSON_DIAG `failed_nets` list (§F / F2: per-failed-net cause —
    net_name / reason / blocked_by / history), or None when the stage emitted no
    diag line (a bundle stage, or a router build predating F2)."""

    stage_name: str
    stage_type: str
    successful: int
    failed: int
    total_vias: int
    summary: dict | None
    diag: list | None = None


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
                    "diag": s.diag,
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


def _resolve_length_match_groups(
    groups: list[list[str]],
    ir: dict[str, Any],
    stage_name: str,
    *,
    stage_nets: list[str],
    all_stage_nets: dict[str, list[str]],
) -> list[list[str]]:
    """Resolve each `length_match_groups` entry to a kicad net name (bridge②)
    and require it to be a net THIS STAGE ROUTES.

    An entry is an ATO SIGNAL ADDRESS (`ir["signal_nets"]`, the same channel as
    `resolve_nets` / the board_rules net_classes precedent) or, failing that,
    the EXACT board net name of one of the stage's own nets (kept verbatim).
    Anything else is LOUD, naming the entry and the stage (S5a).

    STAGE MEMBERSHIP is part of the contract, not a nicety: the router's
    matching universe is exclusively the current invocation's routed_results
    (vendor routing_common.run_length_matching) — prior-stage / reuse-board
    copper is a hard OBSTACLE, never a matching member, and a group whose
    entries fall below 2 routed nets is silently skipped router-side. A
    resolvable-but-not-routed entry therefore used to make the whole group a
    silent no-op (the authored matching intent evaporated with route_report
    reporting success). `stage_nets` = this stage's resolved nets;
    `all_stage_nets` = every stage's, for the who-routes-it hint."""
    signal_nets: dict[str, str] = ir["signal_nets"]
    board_nets = ir.get("nets") or {}
    stage_net_set = set(stage_nets)
    resolved: list[list[str]] = []
    for group in groups:
        names: list[str] = []
        for entry in group:
            if entry in signal_nets:
                name = signal_nets[entry]
            elif entry in board_nets:
                name = entry
            else:
                raise LayoutPlanError(
                    f"stage {stage_name!r}: length_match_groups entry {entry!r} "
                    "is neither a resolvable ato signal address (bridge② "
                    "signal_nets) nor an existing board net name — the router "
                    "would silently match nothing"
                )
            if name not in stage_net_set:
                routed_by = sorted(
                    other
                    for other, nets in all_stage_nets.items()
                    if other != stage_name and name in nets
                )
                hint = (
                    f" (net {name!r} is routed by stage {routed_by[0]!r}: the "
                    "router only matches nets routed in the SAME stage — route "
                    "the whole group in one stage)"
                    if routed_by
                    else " (no stage routes it: prior/reuse copper is an "
                    "obstacle for the router, never a matching member)"
                )
                raise LayoutPlanError(
                    f"stage {stage_name!r}: length_match_groups entry {entry!r} "
                    f"resolves to net {name!r}, which is not routed by this "
                    f"stage — the group would silently match nothing{hint}"
                )
            names.append(name)
        resolved.append(names)
    return resolved


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
    board/stackup or a per-stage `config.layers` is loud. A bundle stage dispatches
    to route_bundle.batch_route_bundle via `_bundle_invocation` (§E-Tier2)."""
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
        input_file = str(input_board) if prev_output is None else prev_output
        output_file = str(Path(workdir) / f"{stage.name}.kicad_pcb")

        if isinstance(stage, BundleStage):
            invocations.append(
                _bundle_invocation(
                    stage, ir, layers, input_file, output_file,
                    all_stage_nets=resolved,
                )
            )
            prev_output = output_file
            continue

        if stage.mode == "single":
            entry, module, stage_type = "batch_route", "route", "single"
        else:
            entry, module, stage_type = (
                "batch_route_diff_pairs",
                "route_diff",
                "diff",
            )

        # config expanded verbatim — explicitly-set keys only (the router default
        # stands for the rest; D2 already mode-validated these keys). TWO
        # non-verbatim keys: length_match_groups entries are ato addresses/net
        # names and resolve through bridge② here (the only seam with the ir),
        # and a diff stage defaults fix_polarity to FALSE — the router's own
        # default (True) rewrites target pad nets to avoid a physical P/N
        # twist, silently making the board implement a different netlist than
        # bridge② (the .ato netlist is authoritative under atopile; a swap is
        # an explicit opt-in, and diagnose surfaces it either way).
        kwargs = stage.config.model_dump(exclude_unset=True)
        if stage_type == "diff":
            kwargs.setdefault("fix_polarity", False)
        if "layers" in kwargs:
            raise LayoutPlanError(
                f"stage {stage.name!r}: per-stage config.layers is not allowed — "
                "the board.stackup is the single layer authority (TS-AUTH-B)"
            )
        kwargs["layers"] = list(layers)
        if kwargs.get("length_match_groups") is not None:
            kwargs["length_match_groups"] = _resolve_length_match_groups(
                kwargs["length_match_groups"],
                ir,
                stage.name,
                stage_nets=resolved[stage.name],
                all_stage_nets=resolved,
            )

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


def _bundle_invocation(
    stage: "BundleStage",
    ir: dict[str, Any],
    layers,
    input_file: str,
    output_file: str,
    *,
    all_stage_nets: dict[str, list[str]] | None = None,
) -> StageInvocation:
    """A bundle stage -> a route_bundle.batch_route_bundle invocation. Expands
    bundle_artifact (segmented trunk + ordered member offset table + breakouts) and
    RESOLVES the ato member addresses (and each diff member's partner) to kicad net
    names through bridge②; the breakouts are translated from the artifact's
    {at(room), ato-order} to the frozen batch_route_bundle {part, kicad-order} shape.
    The geometry-driven payload (trunk/members/breakouts) rides in kwargs alongside
    the BundleRouteConfig knobs and the stackup layers (TS-AUTH-B)."""
    # local import: bundle_geometry pulls the model; keep build_invocations' top
    # imports lean and avoid a cycle.
    from faebryk.exporters.pcb.layout.bundle_geometry import bundle_artifact

    art = bundle_artifact(stage, ir)
    addr2net = dict(zip([m["net"] for m in art["members"]], art["resolved_nets"]))
    members = [
        {
            **m,
            "net": addr2net[m["net"]],
            "diff_partner": (
                addr2net[m["diff_partner"]] if m.get("diff_partner") else None
            ),
        }
        for m in art["members"]
    ]
    breakouts = [
        {"part": bo["at"], "order": [addr2net[a] for a in bo["order"]]}
        for bo in art["breakouts"]
    ]

    kwargs = stage.config.model_dump(exclude_unset=True)
    if "layers" in kwargs:
        raise LayoutPlanError(
            f"bundle {stage.name!r}: per-stage config.layers is not allowed — "
            "the board.stackup is the single layer authority (TS-AUTH-B)"
        )
    kwargs["layers"] = list(layers)
    if kwargs.get("length_match_groups") is not None:
        # the SAME resolution path as single/diff stages (one semantics),
        # membership-checked against the bundle's own member nets
        kwargs["length_match_groups"] = _resolve_length_match_groups(
            kwargs["length_match_groups"],
            ir,
            stage.name,
            stage_nets=list(art["resolved_nets"]),
            all_stage_nets=all_stage_nets or {},
        )
    kwargs["trunk"] = art["trunk"]
    kwargs["members"] = members
    kwargs["breakouts"] = breakouts

    return StageInvocation(
        stage_name=stage.name,
        stage_type="bundle",
        entry="batch_route_bundle",
        module="route_bundle",
        input_file=input_file,
        output_file=output_file,
        net_names=list(art["resolved_nets"]),
        kwargs=kwargs,
    )


def _system_python3() -> str | None:
    """The SYSTEM python3 to run the router under — NEVER the atopile venv's.

    The rust router ext (and its apt numpy/scipy) is built for the system python3,
    not the venv (CLAUDE.md). `shutil.which("python3")` is WRONG here: when `ato`
    runs with the venv on PATH, `which` returns `.venv/bin/python3`, which has no
    rust ext → the router subprocess dies rc=1. (Tests masked this by running
    without the venv on PATH.) So we skip any interpreter living under this venv's
    prefix and fall back to the conventional system locations."""
    import sys

    venv = Path(sys.prefix)
    candidates: list[str] = []
    found = shutil.which("python3")
    if found:
        candidates.append(found)
    candidates += ["/usr/bin/python3", "/usr/local/bin/python3"]
    for cand in candidates:
        path = Path(cand)
        if not path.exists():
            continue
        try:  # an interpreter whose literal path is inside the venv → skip it
            path.relative_to(venv)
            continue
        except ValueError:
            pass
        return str(path)
    return None


def default_subprocess_invoker(inv: StageInvocation) -> StageResult:
    """IMPURE: run one invocation under SYSTEM python3 (the rust ext is not built
    for the venv) and parse its JSON_SUMMARY back into a StageResult. Mirrors the
    shell-out pattern in test_router_smoke_batch_route.py."""
    sys_py = _system_python3()
    if sys_py is None:
        raise LayoutPlanError(
            "route runner: no SYSTEM python3 to run the router (the rust ext is "
            "not built for the atopile venv — need a non-venv python3 with the "
            "router deps)"
        )

    # the router subprocess runs with cwd=_ROUTER_ROOT (the vendor dir), so any
    # RELATIVE board path (e.g. config.build.paths.layout = "layout/top/top.kicad_pcb",
    # relative to the project) would resolve against the wrong directory and the
    # router would FileNotFoundError. Resolve to absolute (against the PARENT cwd =
    # the project, where the artifacts live) so input/output paths are unambiguous
    # across the cwd boundary — and so the board the router WRITES is the same one
    # the parent reads back for chaining. (The pure StageInvocation keeps the
    # as-given paths; absolutizing is the invoker's job — it owns the cwd switch.)
    input_file = str(Path(inv.input_file).resolve())
    output_file = str(Path(inv.output_file).resolve())

    # the entry call differs by stage type: single/diff are net-name-driven
    # (input, output, NETS, ...); a bundle is geometry-driven — trunk/members/
    # breakouts ride in KW and input/output are keyword args (route_bundle.py).
    if inv.stage_type == "bundle":
        call = (
            "    " + inv.module + "." + inv.entry + "("
            + "input_file=" + repr(input_file)
            + ", output_file=" + repr(output_file)
            + ", return_results=False, verbose=False, **KW)\n"
        )
    else:
        # return_results=False so the router WRITES output_file (return_results=True
        # returns data INSTEAD of writing — route.py:775); JSON_SUMMARY prints either
        # way (route.py:772, before that branch), so we still scrape it from stdout.
        call = (
            "    " + inv.module + "." + inv.entry + "("
            + repr(input_file) + ", " + repr(output_file)
            + ", NETS, return_results=False, verbose=False, **KW)\n"
        )

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
        + call
        + "finally:\n"
        "    sys.stdout = _o\n"
        "_out = _buf.getvalue()\n"
        "_m = re.search(r'JSON_SUMMARY: (\\{.*\\})', _out)\n"
        "_summary = json.loads(_m.group(1)) if _m else None\n"
        # §F / F2: a SEPARATE JSON_DIAG line carries the per-failed-net cause. It is
        # confined to its own line (`.` excludes newlines), so the greedy match
        # cannot bleed into the JSON_SUMMARY line above.
        "_d = re.search(r'JSON_DIAG: (\\{.*\\})', _out)\n"
        "_diag = json.loads(_d.group(1)).get('failed_nets') if _d else None\n"
        "print('JSON_OUT' + json.dumps({'summary': _summary, 'diag': _diag}))\n"
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
    parsed = json.loads(line[len("JSON_OUT"):])
    summary = parsed["summary"]
    diag = parsed.get("diag")

    # a zero-routed stage (nothing to route / all already connected) returns BEFORE
    # write_routed_output (route.py:349-360) — no board is written. Copy the input
    # through so the cross-stage chain (and final_board) still resolves to a board.
    out = Path(output_file)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_file, output_file)

    if summary is None:
        return StageResult(inv.stage_name, inv.stage_type, 0, 0, 0, None, diag=diag)
    return StageResult(
        inv.stage_name,
        inv.stage_type,
        int(summary.get("successful", 0)),
        int(summary.get("failed", 0)),
        int(summary.get("total_vias", 0)),
        summary,
        diag=diag,
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
            if key in _COMMON_KEYS or key in _DETAIL_KEYS:
                continue
            if isinstance(value, list):
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
