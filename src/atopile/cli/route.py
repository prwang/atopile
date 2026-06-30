# pylint: disable=logging-fstring-interpolation
"""`ato route` — run the §E1 route runner over a built board (BACKLOG §F, F1).

The thin shell of the diagnostics closed loop: build → **route** → diagnose →
SKILL-edits-plan → rebuild. It re-parses the build's `layout.yaml`, loads the
build's `<output_base>.layout_ir.json` artifact, and drives `run_route_stages`
over the stamped `.kicad_pcb`, writing a `<output_base>.route_report.json`.

It is a SHELL: all logic that can be tested without a 5-minute build or the rust
router lives in `run_route_for_build`, which takes EXPLICIT paths + an injectable
invoker. The Typer `route` command only bootstraps config and derives those paths
from `config.build.paths` (mirroring `view.py`). Missing artifacts are loud
(UserResourceException) — you cannot route a build that was never built (S5a).
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Callable

import typer

from atopile.errors import UserResourceException

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def run_route_for_build(
    *,
    layout_config: Path,
    ir_path: Path,
    board_path: Path,
    workdir: Path,
    report_path: Path,
    up_to: str | None = None,
    invoker: Callable | None = None,
):
    """Route one build from its on-disk artifacts; return the RouteReport.

    Loud-or-nothing: the layout.yaml, the `.layout_ir.json` artifact, and the
    stamped board must all exist — a missing one means the build never ran (or
    had no layout_config), which is a user error, not a silent no-op."""
    from faebryk.exporters.pcb.layout.layout_plan import load_layout_plan
    from faebryk.exporters.pcb.layout.layout_plan_runner import (
        default_subprocess_invoker,
        run_route_stages,
    )

    if layout_config is None or not Path(layout_config).exists():
        raise UserResourceException(
            f"no layout.yaml to route ({layout_config}) — this build has no "
            "layout_config, or it was never built. Add a `layout_config:` to "
            "ato.yaml and run `ato build` first."
        )
    if not Path(ir_path).exists():
        raise UserResourceException(
            f"missing layout IR artifact {ir_path} — run `ato build` first "
            "(the router resolves nets through this bridge②)."
        )
    if not Path(board_path).exists():
        raise UserResourceException(
            f"missing board {board_path} — run `ato build` first."
        )

    plan = load_layout_plan(Path(layout_config))
    ir = json.loads(Path(ir_path).read_text())
    if invoker is None:
        invoker = default_subprocess_invoker

    return run_route_stages(
        plan,
        ir,
        input_board=Path(board_path),
        workdir=Path(workdir),
        up_to=up_to,
        invoker=invoker,
        report_path=Path(report_path),
    )


def _print_summary(report) -> None:
    """A terse human summary of a RouteReport (the CLI's stdout)."""
    log.info(
        f"routed {report.totals['successful']} net(s), "
        f"{report.totals['failed']} failed, "
        f"{report.totals['total_vias']} via(s) "
        f"({'partial: ' + report.up_to if report.up_to else 'full run'})"
    )
    for stage in report.stages:
        log.info(
            f"  stage {stage.stage_name!r} ({stage.stage_type}): "
            f"{stage.successful} ok / {stage.failed} failed"
        )
    log.info(f"final board: {report.final_board}")
    log.info(f"report: written for `ato diagnose` to consume")


def route(
    entry: Annotated[str | None, typer.Argument()] = None,
    build: Annotated[list[str], typer.Option("--build", "-b", envvar="ATO_BUILD")] = [],
    up_to: Annotated[
        str | None,
        typer.Option(
            "--up-to",
            help="Route only up to this stage (1-based index or stage name); "
            "writes the partial board + report (a resumable checkpoint).",
        ),
    ] = None,
):
    """Route a built board through its layout.yaml route_stages.

    Consumes the `ato build` artifacts (the stamped .kicad_pcb + layout_ir.json)
    and produces a routed board + a `route_report.json` for `ato diagnose`.
    """
    from atopile.config import config

    config.apply_options(entry=entry, selected_builds=build if build else ())
    build_name = list(config.selected_builds)[0]
    log.info(f"Routing {build_name}...")

    with config.select_build(build_name):
        paths = config.build.paths
        output_base = paths.output_base
        report = run_route_for_build(
            layout_config=paths.layout_config,
            ir_path=output_base.with_suffix(".layout_ir.json"),
            board_path=paths.layout,
            workdir=output_base.parent / f"{output_base.name}.route",
            report_path=output_base.with_suffix(".route_report.json"),
            up_to=up_to,
        )
    _print_summary(report)
