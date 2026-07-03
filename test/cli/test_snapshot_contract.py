# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""`ato snapshot` contract — the headless eyes of the route/diagnose loop.

Pure seams (no kicad-cli):
  * `drc_violation_marks` — one (x_mm, y_mm, label) per DRC violation, numbered
    ACROSS violations AND unconnected_items in report order — the same numbering
    `summarize_drc` prints, so a mark on the image is findable in the text.
  * `board_copper_layers` — the board's copper layer set in stack order (drives
    which layers get rendered; a 4-layer board must not render as 2).

E2E (gated on kicad-cli + rsvg-convert, the empirically-required rasterizer —
ImageMagick's builtin SVG renderer silently DROPS KiCad track segments):
  * `snapshot_board` on a real board writes an annotated PNG + a parsed
    drc.json; the PNG is content-cropped (not a blank A4 page).
"""

import json
import shutil

import pytest
from atopile.cli.snapshot import (
    board_copper_layers,
    drc_violation_marks,
    summarize_drc,
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
    png = tmp_path / "shot.png"
    assert png.exists()
    from PIL import Image

    im = Image.open(png)
    # content-cropped: a blank render would crop to ~nothing, an uncropped A4 at
    # 20 px/mm would be ~5940 px wide. The board is ~100x100 mm.
    assert 200 < im.size[0] < 5000 and 200 < im.size[1] < 5000
