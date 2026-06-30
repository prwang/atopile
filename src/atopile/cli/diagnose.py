# pylint: disable=logging-fstring-interpolation
"""`ato diagnose` — aggregate a route run + DRC into diagnostics.json (§F / F4).

The closing command of the diagnostics loop: build → route → **diagnose**. It
reads the build's `route_report.json` (route failures + F2 blocking cause) and
the `.layout_ir.json` (bridge②), runs `kicad-cli pcb drc` on the routed board,
rereads the board for the true via/geometry totals (G3) and the room rings, and
emits an ato-address-indexed `diagnostics.json` the PCB-layout SKILL consumes.

A SHELL: all aggregation/correlation/schema lives in the pure
`diagnostics.build_diagnostics` (F4); this module only loads artifacts and runs
the real DRC + board reread. Those two impure steps are injectable
(`drc_runner` / `board_reader`) so the loud-path + aggregation contract is
unit-testable without kicad-cli or a real board. Missing artifacts are loud
(UserResourceException) — you diagnose a ROUTED build, not a missing one.
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Callable

import typer

from atopile.errors import UserResourceException

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def _default_drc_runner(board_path: Path) -> list:
    """Run kicad-cli DRC and adapt the typed report into the builder's dict shape
    (the builder is pure and never touches the typed C_ model)."""
    from faebryk.libs.kicad.drc import run_drc

    report = run_drc(Path(board_path))
    out: list = []
    for violation in list(report.violations) + list(report.unconnected_items):
        out.append(
            {
                "type": str(violation.type),
                "severity": str(violation.severity),
                "description": violation.description,
                "items": [
                    {
                        "uuid": str(item.uuid),
                        "x": item.pos.x,
                        "y": item.pos.y,
                        "description": item.description,
                    }
                    for item in violation.items
                ],
            }
        )
    return out


def _default_board_reader(board_path: Path) -> tuple[dict, dict]:
    """Reread the routed board for the room rings (coord→room, F3) and the TRUE
    via/segment totals (G3: total_vias in the summary is only newly-added vias)."""
    from faebryk.libs.kicad.fileformats import kicad
    from faebryk.libs.kicad.layout_ir_resolve import room_polygons_from_pcb

    pcb = kicad.loads(kicad.pcb.PcbFile, Path(board_path).read_text()).kicad_pcb
    room_polygons = room_polygons_from_pcb(pcb)
    board_totals = {
        "board_vias": len(pcb.vias),
        "board_segments": len(pcb.segments),
    }
    return room_polygons, board_totals


def run_diagnose_for_build(
    *,
    route_report_path: Path,
    ir_path: Path,
    out_path: Path,
    board_path: Path | None = None,
    baseline_board_path: Path | None = None,
    drc_runner: Callable | None = None,
    board_reader: Callable | None = None,
) -> dict:
    """Build diagnostics.json for one build from its artifacts; return the dict.

    The board diagnosed defaults to the route_report's `final_board` (the ROUTED
    board) — `board_path` overrides it. Loud-or-nothing: the route_report, the IR,
    and the routed board must all exist — a missing one means the build was never
    routed (run `ato route`)."""
    from faebryk.exporters.pcb.layout.diagnostics import (
        build_diagnostics,
        drc_violation_key,
    )

    if not Path(route_report_path).exists():
        raise UserResourceException(
            f"missing {route_report_path} — run `ato route` first (diagnose "
            "aggregates the route report)."
        )
    if not Path(ir_path).exists():
        raise UserResourceException(
            f"missing layout IR artifact {ir_path} — run `ato build` first."
        )

    route_report = json.loads(Path(route_report_path).read_text())
    ir = json.loads(Path(ir_path).read_text())

    # the routed board is the report's authoritative final_board (override-able).
    if board_path is None:
        board_path = Path(route_report.get("final_board", ""))
    if not str(board_path) or not Path(board_path).exists():
        raise UserResourceException(
            f"missing routed board {board_path} — run `ato route` first."
        )
    if drc_runner is None:
        drc_runner = _default_drc_runner
    if board_reader is None:
        board_reader = _default_board_reader

    room_polygons, board_totals = board_reader(Path(board_path))
    violations = drc_runner(Path(board_path))

    baseline_keys = None
    if baseline_board_path is not None and Path(baseline_board_path).exists():
        baseline_keys = {drc_violation_key(v) for v in drc_runner(Path(baseline_board_path))}

    diag = build_diagnostics(
        route_report=route_report,
        drc_violations=violations,
        ir=ir,
        room_polygons=room_polygons,
        baseline_drc_keys=baseline_keys,
        board_totals=board_totals,
    )
    Path(out_path).write_text(json.dumps(diag, indent=2, sort_keys=True) + "\n")
    return diag


def _print_summary(diag: dict, out_path: Path) -> None:
    s = diag["summary"]
    log.info(
        f"{s['total_findings']} finding(s): {s['route_failures']} route "
        f"failure(s), {s['drc_violations']} DRC ({s.get('new_drc', 0)} new)"
    )
    for f in diag["findings"]:
        where = f.get("room") or (f.get("components") or ["?"])[0]
        log.info(f"  [{f['severity']}] {f['rule_id']} @ {where}: {f['summary']}")
    log.info(f"diagnostics written to {out_path}")


def diagnose(
    entry: Annotated[str | None, typer.Argument()] = None,
    build: Annotated[list[str], typer.Option("--build", "-b", envvar="ATO_BUILD")] = [],
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            help="A pre-route board to diff DRC against (marks pre-existing "
            "violations as not-new, so only routing-introduced DRC is flagged).",
        ),
    ] = None,
):
    """Aggregate the route report + KiCad DRC into a structured diagnostics.json.

    Correlates every failure/violation back to its ato address + room, for the
    PCB-layout SKILL to act on. Run after `ato route`.
    """
    from atopile.config import config

    config.apply_options(entry=entry, selected_builds=build if build else ())
    build_name = list(config.selected_builds)[0]
    log.info(f"Diagnosing {build_name}...")

    with config.select_build(build_name):
        paths = config.build.paths
        output_base = paths.output_base
        out_path = output_base.with_suffix(".diagnostics.json")
        # the board diagnosed = the report's routed final_board (board_path=None);
        # the pre-route board (paths.layout, which `ato route` does NOT overwrite —
        # it writes into a workdir) is the automatic DRC baseline, so only
        # routing-introduced violations are flagged `is_new`. --baseline overrides.
        diag = run_diagnose_for_build(
            route_report_path=output_base.with_suffix(".route_report.json"),
            ir_path=output_base.with_suffix(".layout_ir.json"),
            out_path=out_path,
            baseline_board_path=baseline or paths.layout,
        )
    _print_summary(diag, out_path)
