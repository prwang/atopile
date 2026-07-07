# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F4 contract — `ato diagnose`, the diagnostics-loop CLI.

The CLI shell over the F4 builder. The testable core (`run_diagnose_for_build`)
takes explicit paths + an injectable `drc_runner`, `board_reader` and
`lengths_builder` so the aggregation/loud-path contract is unit-testable
WITHOUT kicad-cli or a real board; the Typer command bootstraps config and
supplies the real runners.

Pins: the `diagnose` command is registered; the core loads route_report.json +
the IR, runs DRC, rereads the board for room rings + via totals (G3), computes
the F2-lane net-length report from the routed board (`lengths` section), builds
`diagnostics.json` (round-trips to the return value); a pre-route baseline marks
existing DRC as not-new; missing artifacts are loud (S5a).
"""

import json
from pathlib import Path

import pytest

from atopile.errors import UserResourceException

try:
    from atopile.cli.diagnose import run_diagnose_for_build

    _F4CLI_LANDED = True
except Exception:  # noqa: BLE001
    _F4CLI_LANDED = False

    def run_diagnose_for_build(*a, **k):
        raise RuntimeError("F4 ato diagnose not landed")


needs_f4cli = pytest.mark.xfail(
    not _F4CLI_LANDED, reason="F4 ato diagnose not landed yet", strict=True
)

_IR = {
    "layout_ir_version": 2,
    "components": {
        "top.u1": {
            "ref": "U1", "footprint_uuid": "fp-u1", "at": [10, 10, 0], "layer": "F.Cu",
            "pads": {
                "3": {
                    "uuid": "pad-clk",
                    "net": "/CLK",
                    "at": [10, 9, 0],
                    "layers": ["F.Cu"],
                }
            },
        }
    },
    "nets": {"/CLK": ["top.u1.3"]},
    "rooms": {"top.u1": {"sheetname": "top.u1", "member_addrs": ["top.u1"]}},
}

_ROUTE_REPORT = {
    "stages": [
        {"stage_name": "sigs", "stage_type": "single", "successful": 0, "failed": 1,
         "total_vias": 0,
         "summary": {"successful": 0, "failed": 1, "failed_single": ["/CLK"]},
         "diag": [{"net_name": "/CLK", "reason": "no path found",
                   "blocked_by": ["/GND"], "history": []}]}
    ],
    "totals": {"successful": 0, "failed": 1, "total_vias": 0},
    "by_type": {"single": {"failed_single": ["/CLK"]}},
    "up_to": None, "final_board": "out.kicad_pcb",
}

_VIOLATION = {
    "type": "clearance", "severity": "error",
    "description": "Clearance (/CLK to /GND)",
    "items": [{"uuid": "pad-clk", "x": 10.0, "y": 10.0, "description": "pad"}],
}


_LENGTHS = {
    "nets": {"/CLK": {"track_mm": 42.0, "via_count": 2, "segment_count": 5}},
    "pairs": {},
    "classes": {},
}


def _artifacts(tmp_path: Path) -> dict:
    rr = tmp_path / "out.route_report.json"
    rr.write_text(json.dumps(_ROUTE_REPORT))
    ir = tmp_path / "out.layout_ir.json"
    ir.write_text(json.dumps(_IR))
    board = tmp_path / "out.kicad_pcb"
    board.write_text("(kicad_pcb)")
    return {
        "route_report_path": rr,
        "ir_path": ir,
        "board_path": board,
        "out_path": tmp_path / "out.diagnostics.json",
        # injectable like drc_runner/board_reader: the default builder parses
        # the real routed board, which this fake board is not.
        "lengths_builder": lambda _path: dict(_LENGTHS),
    }


def _fake_board_reader(_path):
    # room rings + reread totals (G3) — fixed, no real board parse.
    rings = {"top.u1": [(5.0, 5.0), (15.0, 5.0), (15.0, 15.0), (5.0, 15.0)]}
    return rings, {"board_vias": 7, "board_segments": 12}


def _fake_drc(violations):
    def run(_path):
        return list(violations)

    return run


# ---------------------------------------------------------------------------
@needs_f4cli
def test_diagnose_command_is_registered():
    from atopile.cli.cli import app

    names = {
        (c.name or (c.callback.__name__ if c.callback else ""))
        for c in app.registered_commands
    }
    assert "diagnose" in names


@needs_f4cli
def test_core_aggregates_and_writes_diagnostics(tmp_path):
    art = _artifacts(tmp_path)
    diag = run_diagnose_for_build(
        drc_runner=_fake_drc([_VIOLATION]),
        board_reader=_fake_board_reader,
        **art,
    )
    # round-trips to disk.
    on_disk = json.loads(art["out_path"].read_text())
    assert on_disk == diag
    # both sources aggregated + correlated.
    assert diag["summary"]["route_failures"] == 1
    assert diag["summary"]["drc_violations"] == 1
    rf = next(f for f in diag["findings"] if f["rule_id"] == "ROUTE-FAIL")
    assert rf["room"] == "top.u1" and rf["blocking_nets"] == ["/GND"]
    drc = next(f for f in diag["findings"] if f["rule_id"] == "DRC-clearance")
    assert "top.u1" in drc["components"] and drc["room"] == "top.u1"
    # G3 reread totals folded in.
    assert diag["totals"]["board_vias"] == 7
    # F2 lane: the board-side length report lands as the top-level `lengths`
    # section (computed by the injectable lengths_builder from the routed board).
    assert diag["lengths"] == _LENGTHS
    assert on_disk["lengths"]["nets"]["/CLK"]["track_mm"] == 42.0


@needs_f4cli
def test_lengths_builder_error_is_contained_as_finding(tmp_path):
    """CONTAINMENT: the lengths lane is optional metrology — a board the length
    report cannot honestly measure must not abort diagnose (pre-fix: no
    diagnostics.json at all). The error surfaces as a warning-severity
    LENGTH-REPORT-FAILED finding and `lengths` degrades to the empty shape."""
    from faebryk.libs.kicad.length_report import LengthReportError

    def boom(_path):
        raise LengthReportError("degenerate arc (collinear start/mid/end)")

    art = _artifacts(tmp_path)
    art["lengths_builder"] = boom
    diag = run_diagnose_for_build(
        drc_runner=_fake_drc([]),
        board_reader=_fake_board_reader,
        **art,
    )
    assert art["out_path"].exists()
    assert diag["lengths"] == {"nets": {}, "pairs": {}, "classes": {}}
    f = next(f for f in diag["findings"] if f["rule_id"] == "LENGTH-REPORT-FAILED")
    assert f["severity"] == "warning"
    assert "collinear" in f["description"]


@needs_f4cli
def test_baseline_board_marks_existing_drc(tmp_path):
    """A baseline board whose DRC already contains the violation → is_new False."""
    art = _artifacts(tmp_path)
    baseline = tmp_path / "baseline.kicad_pcb"
    baseline.write_text("(kicad_pcb)")
    diag = run_diagnose_for_build(
        drc_runner=_fake_drc([_VIOLATION]),
        board_reader=_fake_board_reader,
        baseline_board_path=baseline,
        **art,
    )
    drc = next(f for f in diag["findings"] if f["rule_id"] == "DRC-clearance")
    assert drc["is_new"] is False

    # no baseline → new-vs-existing is genuinely UNKNOWN (not a misleading guess);
    # is_new is absent/None. (The Typer command normally auto-supplies the
    # pre-route board — paths.layout — as baseline, so in practice it is known.)
    diag2 = run_diagnose_for_build(
        drc_runner=_fake_drc([_VIOLATION]),
        board_reader=_fake_board_reader,
        **_artifacts(tmp_path),
    )
    drc2 = next(f for f in diag2["findings"] if f["rule_id"] == "DRC-clearance")
    assert drc2.get("is_new") is None


@needs_f4cli
def test_explicit_missing_baseline_is_loud(tmp_path):
    """A user-supplied --baseline that does not exist is LOUD — not silently
    ignored (which would degrade new-vs-existing DRC without the user knowing)."""
    art = _artifacts(tmp_path)
    with pytest.raises(UserResourceException):
        run_diagnose_for_build(
            drc_runner=_fake_drc([_VIOLATION]),
            board_reader=_fake_board_reader,
            baseline_board_path=tmp_path / "does_not_exist.kicad_pcb",
            **art,
        )


@needs_f4cli
def test_missing_route_report_is_loud(tmp_path):
    art = _artifacts(tmp_path)
    art["route_report_path"].unlink()
    with pytest.raises(UserResourceException):
        run_diagnose_for_build(
            drc_runner=_fake_drc([]), board_reader=_fake_board_reader, **art
        )


@needs_f4cli
def test_missing_ir_is_loud(tmp_path):
    art = _artifacts(tmp_path)
    art["ir_path"].unlink()
    with pytest.raises(UserResourceException):
        run_diagnose_for_build(
            drc_runner=_fake_drc([]), board_reader=_fake_board_reader, **art
        )
