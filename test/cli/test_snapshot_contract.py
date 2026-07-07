# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""`ato snapshot` contract — the headless eyes of the route/diagnose loop.

Pure seams (no kicad-cli):
  * `drc_violation_marks` — one (x_mm, y_mm, label) per DRC violation, numbered
    ACROSS violations AND unconnected_items in report order — the same numbering
    `summarize_drc` prints, so a mark on the image is findable in the text.
  * `board_copper_layers` — the board's copper layer set in stack order (drives
    which layers get rendered; a 4-layer board must not render as 2).
  * `write_board_lengths` / `lengths_table_lines` — the F2-lane net-length
    artifact: `<out_base>.lengths.json` (per-net track_mm / via_count /
    segment_count + pair skews + netclass spread) plus the compact per-pair
    log table — the numbers an agent tuning lengths reads INSIDE the snapshot
    loop, not in a separate tool.

E2E (gated on kicad-cli + rsvg-convert, the empirically-required rasterizer —
ImageMagick's builtin SVG renderer silently DROPS KiCad track segments):
  * `snapshot_board` on a real board writes an annotated PNG + a parsed
    drc.json + a lengths.json; the PNG is content-cropped (not a blank A4 page).
"""

import json
import shutil

import pytest

from atopile.cli.snapshot import (
    board_copper_layers,
    drc_violation_marks,
    lengths_table_lines,
    summarize_drc,
    write_board_lengths,
)
from faebryk.libs.util import repo_root

_BOARD = (
    repo_root() / "vendor" / "KiCadRoutingTools" / "kicad_files"
    / "lvds_converter_dualclk.kicad_pcb"
)

_DRC = {
    "violations": [
        {
            "type": "shorting_items",
            "severity": "error",
            "description": "Items shorting two nets (nets A and B)",
            "items": [{"pos": {"x": 12.5, "y": 34.0}}, {"pos": {"x": 13.0, "y": 34.0}}],
        },
        {
            "type": "clearance",
            "severity": "error",
            "description": "Clearance violation",
            "items": [{"pos": {"x": 1.0, "y": 2.0}}],
        },
    ],
    "unconnected_items": [
        {
            "type": "unconnected_items",
            "severity": "error",
            "description": "Missing connection between items",
            "items": [{"pos": {"x": 5.0, "y": 6.0}}],
        }
    ],
}


def test_marks_are_numbered_across_violations_and_unconnected():
    marks = drc_violation_marks(_DRC)
    assert marks == [
        (12.5, 34.0, "1"),  # first item's position, one mark per violation
        (1.0, 2.0, "2"),
        (5.0, 6.0, "3"),  # unconnected continues the SAME numbering
    ]
    # the printed summary carries the same numbers (mark ↔ text findability)
    lines = summarize_drc(_DRC)
    assert [ln.split("]")[0].strip(" [") for ln in lines] == ["1", "2", "3"]
    assert "shorting_items" in lines[0] and "(12.5, 34.0)mm" in lines[0]


def test_marks_skip_itemless_violation_but_keep_numbering():
    drc = {"violations": [{"type": "x", "severity": "error", "items": []}] + _DRC[
        "violations"
    ], "unconnected_items": []}
    marks = drc_violation_marks(drc)
    # violation 1 has no position (no mark), 2 and 3 keep their real numbers
    assert [m[2] for m in marks] == ["2", "3"]


# ---------------------------------------------------------------------------
# F2 lane: the lengths artifact (no kicad-cli — pure parse + compute + write)
# ---------------------------------------------------------------------------
def _synthetic_board(tmp_path):
    """Two straight F.Cu segments forming a _P/_N pair with a 0.8 mm skew —
    lengths fixed by construction (the golden-board recipe)."""
    fixture = (
        repo_root() / "test/common/resources/fileformats/kicad/v10/pcb/test.kicad_pcb"
    )
    header = "".join(fixture.read_text().splitlines(keepends=True)[:169])
    nets = ['\t(net 0 "")', '\t(net 1 "A_P")', '\t(net 2 "A_N")']
    segs = [
        '\t(segment\n\t\t(start 100 100)\n\t\t(end 150 100)\n\t\t(width 0.2)\n'
        '\t\t(layer "F.Cu")\n\t\t(net 1)\n\t)',
        '\t(segment\n\t\t(start 100 101)\n\t\t(end 150.8 101)\n\t\t(width 0.2)\n'
        '\t\t(layer "F.Cu")\n\t\t(net 2)\n\t)',
    ]
    board = tmp_path / "golden.kicad_pcb"
    board.write_text(header + "\n".join(nets + segs) + "\n\t(embedded_fonts no)\n)\n")
    return board


def test_write_board_lengths_writes_artifact_and_pair_table(tmp_path):
    board = _synthetic_board(tmp_path)
    out_base = tmp_path / "shot"
    report = write_board_lengths(board, out_base)
    on_disk = json.loads((tmp_path / "shot.lengths.json").read_text())
    assert on_disk == report
    assert report["nets"]["A_P"]["track_mm"] == 50.0
    assert report["pairs"]["A"]["skew_mm"] == pytest.approx(0.8, abs=1e-6)
    # the compact per-pair table an agent reads in the snapshot log:
    lines = lengths_table_lines(report)
    assert len(lines) == 1
    assert "A_P" in lines[0] and "A_N" in lines[0]
    assert "50.000" in lines[0] and "50.800" in lines[0] and "0.800" in lines[0]


@pytest.mark.skipif(not _BOARD.exists(), reason="router fixture board absent")
def test_board_copper_layers_reads_stack_order():
    layers = board_copper_layers(_BOARD)
    assert layers[0] == "F.Cu" and layers[-1] == "B.Cu"
    assert len(layers) >= 2


@pytest.mark.skipif(
    not _BOARD.exists()
    or shutil.which("kicad-cli") is None
    or shutil.which("rsvg-convert") is None,
    reason="kicad-cli / rsvg-convert / fixture board absent",
)
@pytest.mark.slow
def test_snapshot_board_writes_annotated_png_and_drc_json(tmp_path):
    from atopile.cli.snapshot import snapshot_board

    board = tmp_path / _BOARD.name
    shutil.copyfile(_BOARD, board)
    out_base = tmp_path / "shot"
    drc = snapshot_board(board, out_base)
    assert (tmp_path / "shot.drc.json").exists()
    assert json.loads((tmp_path / "shot.drc.json").read_text()) == drc
    # the F2-lane lengths artifact rides along with every snapshot
    lengths = json.loads((tmp_path / "shot.lengths.json").read_text())
    assert set(lengths) == {"nets", "pairs", "classes"}
    assert len(lengths["nets"]) > 0
    png = tmp_path / "shot.png"
    assert png.exists()
    from PIL import Image

    im = Image.open(png)
    # content-cropped: a blank render would crop to ~nothing, an uncropped A4 at
    # 20 px/mm would be ~5940 px wide. The board is ~100x100 mm.
    assert 200 < im.size[0] < 5000 and 200 < im.size[1] < 5000


@pytest.mark.skipif(
    shutil.which("kicad-cli") is None or shutil.which("rsvg-convert") is None,
    reason="kicad-cli / rsvg-convert absent",
)
@pytest.mark.slow
def test_snapshot_survives_length_report_error(tmp_path, caplog):
    """CONTAINMENT: the lengths lane is an optional metrology rider — a board
    the length report cannot honestly measure (here: a degenerate collinear
    track arc) must NOT kill the snapshot's primary deliverables. The PNG and
    drc.json still ship; the lengths.json is absent and a WARNING says why.
    (Pre-fix, one LengthReportError aborted the loop's eyes entirely.)"""
    import logging

    from atopile.cli.snapshot import snapshot_board

    board = _synthetic_board(tmp_path)
    text = board.read_text()
    collinear_arc = (
        "\t(arc\n\t\t(start 100 105)\n\t\t(mid 105 105)\n\t\t(end 110 105)\n"
        '\t\t(width 0.2)\n\t\t(layer "F.Cu")\n\t\t(net 1)\n\t)'
    )
    board.write_text(
        text.replace(
            "\t(embedded_fonts no)", collinear_arc + "\n\t(embedded_fonts no)"
        )
    )
    out_base = tmp_path / "shot"
    with caplog.at_level(logging.WARNING):
        drc = snapshot_board(board, out_base)
    assert (tmp_path / "shot.drc.json").exists()
    assert (tmp_path / "shot.png").exists()
    assert isinstance(drc, dict)
    assert not (tmp_path / "shot.lengths.json").exists()
    assert any("length report" in r.message for r in caplog.records)


def test_board_copper_layers_ignores_stackup_mentions(tmp_path):
    """A 2-layer board whose (stackup ...) section names inner layers must NOT
    report them: the layer TABLE is the authority. (Live bug: the whole-file
    text scan saw In1.Cu in the stackup of a 2-layer fixture, and kicad-cli
    silently exports nothing for a nonexistent layer — blank render group.)"""
    board = tmp_path / "two_layer_with_stackup.kicad_pcb"
    board.write_text(
        "(kicad_pcb\n"
        "\t(version 20241229)\n"
        '\t(generator "pcbnew")\n'
        "\t(layers\n"
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(2 "B.Cu" signal)\n'
        '\t\t(25 "Edge.Cuts" user)\n'
        "\t)\n"
        "\t(setup\n"
        "\t\t(stackup\n"
        '\t\t\t(layer "In1.Cu" (type "copper"))\n'
        '\t\t\t(layer "In2.Cu" (type "copper"))\n'
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )
    assert board_copper_layers(board) == ["F.Cu", "B.Cu"]
