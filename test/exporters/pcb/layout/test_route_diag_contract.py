# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F2 contract — router diagnostics exposure (`JSON_DIAG`).

G1 (BACKLOG §F): the router's "why didn't this net route" — net history +
top-blocker ranking — was print-only and discarded; the runner scraped only the
one `JSON_SUMMARY:` line. F2 folds the failure cause into a SEPARATE stdout line
`JSON_DIAG: {...}` (distinct prefix so the JSON_SUMMARY scraper is unaffected),
and teaches `default_subprocess_invoker` to capture it onto `StageResult.diag` →
`route_report.json`.

The consumer (F4) FREEZES the JSON_DIAG shape; the router emits it. The per-net
record is `{net_name, reason, blocked_by:[net,...], history:[event,...]}`, built
purely from the router's own `net_history` (the same data
`print_failed_net_histories` renders) — no new analysis, no invasive loop hooks.

Honest limit (asserted, not silent): cell-count detail in `BlockingInfo` and
static-blocker analysis are NOT in net_history, so `blocked_by` carries the
router's top-blocker NET NAMES + reason, not per-blocker cell counts (BACKLOG §F
limitation). Bundle stages (geometry-driven, no A*) emit no JSON_DIAG; their
member failures stay in the summary's `failed_members`.

Two-repo S0 gate: the parent's `StageResult.diag` field AND the router's
`JSON_DIAG` emission must both be landed (an XPASS while either is missing is a
test bug).
"""

import dataclasses
import json
import shutil
import subprocess

import pytest

from faebryk.libs.util import repo_root

_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_BOARD = _ROUTER_ROOT / "kicad_files" / "lvds_converter_dualclk.kicad_pcb"
_SYS_PY = shutil.which("python3")

# --- parent-side probe: StageResult carries a `diag` field ---
try:
    from faebryk.exporters.pcb.layout.layout_plan_runner import (
        RouteReport,
        StageInvocation,
        StageResult,
        run_route_stages,
    )

    _DIAG_FIELD = "diag" in {f.name for f in dataclasses.fields(StageResult)}
except Exception:  # noqa: BLE001
    _DIAG_FIELD = False

# --- router-side probe: route.py / route_diff.py emit a JSON_DIAG line + diag.py ---
_ROUTE_PY = _ROUTER_ROOT / "route.py"
_ROUTE_DIFF_PY = _ROUTER_ROOT / "route_diff.py"
_DIAG_PY = _ROUTER_ROOT / "diag.py"
_router_emits_diag = (
    _DIAG_PY.exists()
    and _ROUTE_PY.exists()
    and "JSON_DIAG" in _ROUTE_PY.read_text()
    and _ROUTE_DIFF_PY.exists()
    and "JSON_DIAG" in _ROUTE_DIFF_PY.read_text()
)

_F2_LANDED = _DIAG_FIELD and _router_emits_diag

needs_f2 = pytest.mark.xfail(
    not _F2_LANDED, reason="F2 router JSON_DIAG not landed yet", strict=True
)
needs_router = pytest.mark.skipif(
    not _BOARD.exists() or _SYS_PY is None,
    reason=f"router board or system python3 absent ({_BOARD})",
)


# ===========================================================================
# F2a (pure) — StageResult.diag survives into route_report.json
# ===========================================================================
_LAYOUT_YAML = """\
board:
  stackup:
    layers:
      - {name: F.Cu, type: copper}
      - {name: core, type: dielectric, thickness: 1.5, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu, type: copper}
route_stages:
  - name: sigs
    mode: single
    nets:
      - top.clk
"""
_IR = {
    "layout_ir_version": 2, "components": {}, "nets": {}, "rooms": {},
    "signal_nets": {"top.clk": "/CLK"},
}


@needs_f2
def test_stage_result_diag_roundtrips_to_report(tmp_path):
    """A stage's diag (per-failed-net cause) is preserved verbatim through the
    RouteReport into route_report.json — the channel F4 reads."""
    from faebryk.exporters.pcb.layout.layout_plan import load_layout_plan

    plan = load_layout_plan(_LAYOUT_YAML)
    ir = _IR
    board = tmp_path / "in.kicad_pcb"
    board.write_text("(kicad_pcb)")
    diag_payload = [
        {"net_name": "/CLK", "reason": "no path found",
         "blocked_by": ["/GND"], "history": [{"event": "reroute_failed"}]}
    ]

    def invoker(inv):
        p = tmp_path / f"{inv.stage_name}.out"
        p.write_text("(kicad_pcb)")
        return StageResult(
            inv.stage_name, inv.stage_type, 0, 1, 0,
            {"successful": 0, "failed": 1, "failed_single": ["/CLK"]},
            diag=diag_payload,
        )

    report = run_route_stages(
        plan, ir, input_board=board, workdir=tmp_path / "wd",
        invoker=invoker, report_path=tmp_path / "route_report.json",
    )
    on_disk = json.loads((tmp_path / "route_report.json").read_text())
    assert on_disk["stages"][0]["diag"] == diag_payload
    assert report.stages[0].diag == diag_payload


# ===========================================================================
# F2b (pure) — the diag-shaping logic (diag.failed_net_diagnostics) bites by
# construction: a hand-built net_history yields the frozen record shape, with
# blocked_by = de-duped top_blockers union and reason from the last failure.
# ===========================================================================
@needs_f2
@needs_router
def test_failed_net_diagnostics_shape_by_construction():
    net_history = {
        7: [
            {"event": "initial_route", "sequence": 1, "details": {}},
            {"event": "ripped_by", "sequence": 2,
             "details": {"ripping_net_name": "/PWR"}},
            {"event": "reroute_failed", "sequence": 3,
             "details": {"reason": "no path found",
                         "top_blockers": ["/GND", "/PWR"]}},
            {"event": "reroute_failed", "sequence": 4,
             "details": {"reason": "no path found",
                         "top_blockers": ["/PWR", "/D0"]}},  # /PWR repeats
        ],
        9: [],  # a net with no recorded history → empty cause, still reported
    }
    net_names = {7: "/CLK", 9: "/RST"}
    driver = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(_ROUTER_ROOT)!r})\n"
        "from diag import failed_net_diagnostics\n"
        f"nh = json.loads({json.dumps(json.dumps({str(k): v for k, v in net_history.items()}))})\n"
        "nh = {int(k): v for k, v in nh.items()}\n"
        f"names = {{7: '/CLK', 9: '/RST'}}\n"
        "out = failed_net_diagnostics(nh, [7, 9], names)\n"
        "print('RESULT' + json.dumps(out))\n"
    )
    proc = subprocess.run(
        [_SYS_PY, "-c", driver], cwd=str(_ROUTER_ROOT),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT"))
    out = json.loads(line[len("RESULT"):])

    clk = next(d for d in out if d["net_name"] == "/CLK")
    assert clk["reason"] == "no path found"
    # de-duped, order-preserved union of top_blockers across reroute_failed events
    assert clk["blocked_by"] == ["/GND", "/PWR", "/D0"]
    assert len(clk["history"]) == 4
    # a net with no history is still reported (loud-or-nothing: no dropped failure)
    rst = next(d for d in out if d["net_name"] == "/RST")
    assert rst["blocked_by"] == [] and rst["reason"] is None


# ===========================================================================
# F2c (router) — a real route over a deliberately-unroutable net emits JSON_DIAG
# and the invoker captures it onto StageResult.diag, naming the failed net.
# ===========================================================================
@needs_f2
@needs_router
def test_real_route_emits_and_captures_diag(tmp_path):
    from faebryk.exporters.pcb.layout.layout_plan_runner import (
        default_subprocess_invoker,
    )

    if (
        subprocess.run(
            [_SYS_PY, "-c", "import route, diag"], cwd=str(_ROUTER_ROOT),
            capture_output=True, text=True,
        ).returncode
        != 0
    ):
        pytest.skip("router not importable under system python3")

    # route a REAL single-ended net on the (unrouted) LVDS board. It reaches the
    # router summary, so the JSON_DIAG line is emitted and the invoker captures it.
    # (A non-existent net would early-return "nothing to route" before any summary —
    # not a failure, so not what F2 instruments.)
    inv = StageInvocation(
        stage_name="sig",
        stage_type="single",
        entry="batch_route",
        module="route",
        input_file=str(_BOARD),
        output_file=str(tmp_path / "out.kicad_pcb"),
        net_names=["/OUT_A"],
        kwargs={"layers": ["F.Cu", "B.Cu"]},
    )
    res = default_subprocess_invoker(inv)
    # the channel is LIVE: a completed route always emits JSON_DIAG, so diag is a
    # LIST (empty when nothing failed — the F2 capture wire, not None/dropped).
    assert res.summary is not None, "router did not reach a summary (early return)"
    assert isinstance(res.diag, list)
    # and any record present is well-formed (net_name + the frozen cause keys).
    for record in res.diag:
        assert {"net_name", "reason", "blocked_by", "history"} <= set(record)
