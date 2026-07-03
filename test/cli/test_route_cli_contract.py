# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F1 contract — `ato route`, the thin routing CLI shell.

F1 wires the §E1 route runner (`run_route_stages`) to a build's on-disk
artifacts. It is a SHELL: the testable core (`run_route_for_build`) takes
explicit paths + an injectable invoker so the dispatch/loud-path contract is
unit-testable WITHOUT a 5-minute `ato build` or the rust router; the Typer
command only bootstraps config and derives those paths.

What this pins:
  - the `route` command is REGISTERED on the CLI app (it is reachable as `ato route`);
  - the core re-parses layout.yaml + loads the `.layout_ir.json` artifact, drives
    the runner, and writes a `route_report.json` (round-trips to the return value);
  - missing artifacts (no layout_config / no IR / no board) are LOUD, never a
    silent no-op (S5a) — you cannot "route" a build that was never built.

S0 strict-xfail: gated on the core landing.
"""

import json
from pathlib import Path

import pytest

from atopile.errors import UserResourceException  # the loud missing-artifact error

try:
    from atopile.cli.route import run_route_for_build

    _F1_LANDED = True
except Exception:  # noqa: BLE001
    _F1_LANDED = False

    def run_route_for_build(*a, **k):
        raise RuntimeError("F1 ato route not landed")


needs_f1 = pytest.mark.xfail(
    not _F1_LANDED, reason="F1 ato route not landed yet", strict=True
)

# a minimal two-copper stackup so build_invocations' TS-AUTH-B layer authority is
# satisfied without a board section being optional-away.
_LAYOUT_YAML = """\
rules:
  clearance: 0.1
  track_width: 0.15
  diff_pair_width: 0.15
  diff_pair_gap: 0.15
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
      - top.a
      - top.b
"""

_IR = {
    "layout_ir_version": 2,
    "components": {},
    "nets": {},
    "rooms": {},
    "signal_nets": {"top.a": "NET_A", "top.b": "NET_B"},
}


def _artifacts(tmp_path: Path) -> dict:
    """Lay down the on-disk artifacts a built project would have."""
    layout_config = tmp_path / "layout.yaml"
    layout_config.write_text(_LAYOUT_YAML)
    ir_path = tmp_path / "out.layout_ir.json"
    ir_path.write_text(json.dumps(_IR))
    board = tmp_path / "out.kicad_pcb"
    board.write_text("(kicad_pcb)")  # the fake invoker never parses it
    return {
        "layout_config": layout_config,
        "ir_path": ir_path,
        "board_path": board,
        "workdir": tmp_path / "route",
        "report_path": tmp_path / "out.route_report.json",
    }


def _fake_invoker(captured: list):
    """A pure invoker: records each invocation, returns a fixed StageResult so the
    core can run with no router. Mirrors test_layout_plan_runner_contract."""
    from faebryk.exporters.pcb.layout.layout_plan_runner import StageResult

    def invoke(inv):
        captured.append(inv)
        Path(inv.output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(inv.output_file).write_text("(kicad_pcb)")
        return StageResult(
            inv.stage_name, inv.stage_type, len(inv.net_names), 0, 0,
            {"successful": len(inv.net_names), "failed": 0, "total_vias": 0},
        )

    return invoke


# ---------------------------------------------------------------------------
@needs_f1
def test_route_command_is_registered_on_cli():
    """`ato route` is reachable — the command is added to the Typer app."""
    from atopile.cli.cli import app

    names = {
        (c.name or (c.callback.__name__ if c.callback else ""))
        for c in app.registered_commands
    }
    assert "route" in names


@needs_f1
def test_core_drives_runner_and_writes_report(tmp_path):
    art = _artifacts(tmp_path)
    captured: list = []
    report = run_route_for_build(invoker=_fake_invoker(captured), **art)

    # it parsed layout.yaml → one stage dispatched, nets resolved via bridge②.
    assert [inv.stage_name for inv in captured] == ["sigs"]
    assert captured[0].net_names == ["NET_A", "NET_B"]
    assert captured[0].entry == "batch_route"
    # report round-trips to disk.
    assert art["report_path"].exists()
    on_disk = json.loads(art["report_path"].read_text())
    assert on_disk == report.to_dict()
    assert report.totals["successful"] == 2


@needs_f1
def test_up_to_is_threaded_through(tmp_path):
    """--up-to reaches the runner (an unknown stage name is loud, proving the
    value is actually consumed, not dropped)."""
    from faebryk.exporters.pcb.layout.layout_plan import LayoutPlanError

    art = _artifacts(tmp_path)
    with pytest.raises(LayoutPlanError):
        run_route_for_build(invoker=_fake_invoker([]), up_to="no_such_stage", **art)


@needs_f1
def test_missing_ir_artifact_is_loud(tmp_path):
    art = _artifacts(tmp_path)
    art["ir_path"].unlink()  # built board but IR artifact absent
    with pytest.raises(UserResourceException):
        run_route_for_build(invoker=_fake_invoker([]), **art)


@needs_f1
def test_missing_layout_config_is_loud(tmp_path):
    art = _artifacts(tmp_path)
    art["layout_config"].unlink()
    with pytest.raises(UserResourceException):
        run_route_for_build(invoker=_fake_invoker([]), **art)


@needs_f1
def test_missing_board_is_loud(tmp_path):
    art = _artifacts(tmp_path)
    art["board_path"].unlink()
    with pytest.raises(UserResourceException):
        run_route_for_build(invoker=_fake_invoker([]), **art)
