# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""DRC-refill contract — connectivity is judged on REFILLED zones.

The build authors pours as `(fill yes)` zones WITHOUT stored fill polygons
(board_features.py), and the router round-trips them verbatim — so every
board reaching the snapshot/diagnose DRC path carries unfilled zones. Without
`--refill-zones`, kicad-cli judges connectivity on the stored (empty) fill:
a TH pad whose only connection is the plane reports `unconnected_items` —
a FALSE unconnected that would gate a perfectly good board.

Contract (both in-repo DRC runners):
  * `atopile.cli.snapshot.run_drc` and `faebryk.libs.kicad.drc.run_drc`
    (the `ato diagnose` / library path) report ZERO unconnected items on a
    board whose two GND pads are joined only by an unfilled `(fill yes)` zone.
  * The fixture BITES: the same board run through raw kicad-cli WITHOUT
    `--refill-zones` reports >= 1 unconnected item (guards against a fixture
    that is vacuously connected — CLAUDE.md "tests must really bite").
  * `snapshot_board`'s PNG shows the FILLED zone (the eyes must show the
    copper DRC judged) while the input board file itself is NOT mutated
    (snapshot stays read-only on its input; raw mode may point at a user's
    hand-authored board).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

needs_kicad_cli = pytest.mark.skipif(not _HAS_KICAD_CLI, reason="kicad-cli absent")


def _pad_fp(ref: str, x: float) -> str:
    return (
        f'\t(footprint "test:{ref}"\n\t\t(layer "F.Cu")\n\t\t(at {x} 100)\n'
        f'\t\t(property "Reference" "{ref}" (at 0 -2 0) (layer "F.SilkS")\n'
        "\t\t\t(effects (font (size 1 1) (thickness 0.15))))\n"
        '\t\t(property "Value" "PAD" (at 0 2 0) (layer "F.Fab")\n'
        "\t\t\t(effects (font (size 1 1) (thickness 0.15))))\n"
        '\t\t(pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) '
        '(layers "*.Cu") (net 1 "GND"))\n\t)'
    )


def _unfilled_zone_board(tmp_path: Path) -> Path:
    """Two TH GND pads 10mm apart; their ONLY join is a B.Cu GND zone authored
    `(fill yes)` but carrying no stored fill polygons — exactly the state a
    build-authored pour is in when DRC sees it. Connectivity answer fixed by
    construction: refilled = connected, stored = unconnected."""
    board = tmp_path / "unfilled_zone.kicad_pcb"
    board.write_text(
        "(kicad_pcb\n"
        "\t(version 20241229)\n"
        '\t(generator "pcbnew")\n'
        '\t(generator_version "9.0")\n'
        "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n"
        '\t(paper "A4")\n'
        "\t(layers\n"
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(2 "B.Cu" signal)\n'
        '\t\t(25 "Edge.Cuts" user)\n'
        "\t)\n"
        "\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n"
        '\t(net 0 "")\n'
        '\t(net 1 "GND")\n'
        f"{_pad_fp('P1', 100)}\n"
        f"{_pad_fp('P2', 110)}\n"
        "\t(zone\n"
        "\t\t(net 1)\n"
        '\t\t(net_name "GND")\n'
        '\t\t(layer "B.Cu")\n'
        '\t\t(name "gndpour")\n'
        "\t\t(hatch edge 0.5)\n"
        "\t\t(connect_pads (clearance 0.2))\n"
        "\t\t(min_thickness 0.25)\n"
        "\t\t(filled_areas_thickness no)\n"
        "\t\t(fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))\n"
        "\t\t(polygon\n"
        "\t\t\t(pts (xy 95 95) (xy 115 95) (xy 115 105) (xy 95 105))\n"
        "\t\t)\n"
        "\t)\n"
        "\t(gr_rect (start 94 94) (end 116 106)\n"
        '\t\t(stroke (width 0.1) (type default)) (layer "Edge.Cuts"))\n'
        ")\n"
    )
    return board


@needs_kicad_cli
@pytest.mark.slow
def test_fixture_bites_without_refill(tmp_path):
    """Raw kicad-cli WITHOUT --refill-zones reports the pads unconnected —
    proves the fixture actually exercises the refill path (a fixture that is
    connected either way would make the contract tests below vacuous)."""
    board = _unfilled_zone_board(tmp_path)
    out = tmp_path / "raw.json"
    subprocess.run(
        [
            "kicad-cli", "pcb", "drc", str(board),
            "--format", "json", "--severity-all", "-o", str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(out.read_text())
    assert len(report["unconnected_items"]) >= 1, (
        "fixture does not bite: zone-only connectivity was judged connected "
        "even without refill"
    )


@needs_kicad_cli
@pytest.mark.slow
def test_snapshot_run_drc_judges_refilled_zones(tmp_path):
    from atopile.cli.snapshot import run_drc

    board = _unfilled_zone_board(tmp_path)
    before = board.read_bytes()
    drc = run_drc(board, tmp_path / "drc.json")
    assert drc["unconnected_items"] == [], (
        "snapshot DRC judged the STORED (empty) zone fill: pads joined only "
        f"by the pour report unconnected: {drc['unconnected_items']}"
    )
    # judging refilled state must not mutate the judged board
    assert board.read_bytes() == before, "run_drc mutated its input board"


@needs_kicad_cli
@pytest.mark.slow
def test_faebryk_run_drc_judges_refilled_zones(tmp_path):
    """The `ato diagnose` / library DRC path (faebryk.libs.kicad.drc)."""
    from faebryk.libs.kicad.drc import run_drc

    board = _unfilled_zone_board(tmp_path)
    before = board.read_bytes()
    report = run_drc(board)
    assert report.unconnected_items == [], (
        "diagnose DRC judged the STORED (empty) zone fill: "
        f"{[v.description for v in report.unconnected_items]}"
    )
    assert board.read_bytes() == before, "run_drc mutated its input board"


@needs_kicad_cli
@pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="rsvg-convert absent"
)
@pytest.mark.slow
def test_snapshot_png_shows_zone_fill_without_mutating_board(tmp_path):
    """The eyes must show the copper DRC judged: `snapshot_board` renders the
    REFILLED zone (a large solid copper area), while the input board file is
    byte-identical afterwards (raw mode may point at a user's own board).
    Oracle fixed by construction: the 20x10mm zone dominates the 22x12mm
    board, so a filled render has a much higher non-background pixel fraction
    than the raw (hatch-outline-only) render of the same board."""
    from PIL import Image

    from atopile.cli.snapshot import render_board_png, snapshot_board

    board = _unfilled_zone_board(tmp_path)
    before = board.read_bytes()

    raw_png = render_board_png(board, tmp_path / "raw.png")
    snapshot_board(board, tmp_path / "shot")
    assert board.read_bytes() == before, "snapshot mutated its input board"

    def nonbg_fraction(path: Path) -> float:
        im = Image.open(path).convert("RGB")
        bg = im.getpixel((0, 0))
        colors = im.getcolors(maxcolors=1 << 24) or []
        total = im.size[0] * im.size[1]
        return sum(n for n, c in colors if c != bg) / total

    raw_frac = nonbg_fraction(raw_png)
    filled_frac = nonbg_fraction(tmp_path / "shot.png")
    assert filled_frac > 2 * raw_frac, (
        f"snapshot PNG does not show the zone fill: filled fraction "
        f"{filled_frac:.3f} vs raw (unfilled) {raw_frac:.3f}"
    )


def _four_layer_plane_board(tmp_path: Path) -> Path:
    """4-layer board: an In1.Cu GND zone ((fill yes), unfilled on disk) spanning
    the board, one fat F.Cu track and one fat B.Cu track crossing it, two TH
    GND pads. Render answer fixed by construction: after refill the plane is a
    large solid area; the F.Cu and B.Cu tracks must remain VISIBLE on top of
    it in the composite (the eyes exist to show signals, not to paint planes
    over them — pre-fix, layer paint order hid every track under the fill)."""
    board = tmp_path / "plane4.kicad_pcb"
    board.write_text(
        "(kicad_pcb\n"
        "\t(version 20241229)\n"
        '\t(generator "pcbnew")\n'
        '\t(generator_version "9.0")\n'
        "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n"
        '\t(paper "A4")\n'
        "\t(layers\n"
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(1 "In1.Cu" signal)\n'
        '\t\t(2 "In2.Cu" signal)\n'
        '\t\t(3 "B.Cu" signal)\n'
        '\t\t(25 "Edge.Cuts" user)\n'
        "\t)\n"
        "\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n"
        '\t(net 0 "")\n'
        '\t(net 1 "GND")\n'
        f"{_pad_fp('P1', 100)}\n"
        f"{_pad_fp('P2', 110)}\n"
        '\t(segment (start 97 102) (end 113 102) (width 0.6) (layer "F.Cu") (net 1))\n'
        '\t(segment (start 97 103.5) (end 113 103.5) (width 0.6) (layer "B.Cu") (net 1))\n'
        "\t(zone\n"
        "\t\t(net 1)\n"
        '\t\t(net_name "GND")\n'
        '\t\t(layer "In1.Cu")\n'
        '\t\t(name "gndpour")\n'
        "\t\t(hatch edge 0.5)\n"
        "\t\t(connect_pads (clearance 0.2))\n"
        "\t\t(min_thickness 0.25)\n"
        "\t\t(filled_areas_thickness no)\n"
        "\t\t(fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))\n"
        "\t\t(polygon\n"
        "\t\t\t(pts (xy 95 95) (xy 115 95) (xy 115 105) (xy 95 105))\n"
        "\t\t)\n"
        "\t)\n"
        "\t(gr_rect (start 94 94) (end 116 106)\n"
        '\t\t(stroke (width 0.1) (type default)) (layer "Edge.Cuts"))\n'
        ")\n"
    )
    return board


@needs_kicad_cli
@pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="rsvg-convert absent"
)
@pytest.mark.slow
def test_render_keeps_signal_layers_visible_over_plane_fill(tmp_path):
    """Compositing contract: inner-layer (plane) copper renders BENEATH the
    outer signal layers — an F.Cu track and a B.Cu track crossing a filled
    In1.Cu plane stay visible in the default render, and the plane itself is
    visible where no signal covers it. Sampled at construction-fixed points;
    the F.Cu sample must match a control render of F.Cu+Edge.Cuts alone
    (same crop anchor), proving the track is drawn on TOP, undimmed."""
    from PIL import Image

    from atopile.cli.snapshot import _refilled_render_source, render_board_png

    board = _four_layer_plane_board(tmp_path)
    ppmm = 20.0

    def sample(png: Path, x_mm: float, y_mm: float):
        im = Image.open(png).convert("RGB")
        # crop_margin_mm=0: crop anchor == content bbox corner == the edge
        # rect at (94, 94) (stroke 0.05 halo)
        px = (x_mm - 93.95) * ppmm, (y_mm - 93.95) * ppmm
        return im.getpixel((int(px[0]), int(px[1])))

    with _refilled_render_source(board) as src:
        full = render_board_png(
            src, tmp_path / "full.png", ppmm=ppmm, crop_margin_mm=0.0
        )
        control = render_board_png(
            src,
            tmp_path / "fcu.png",
            layers=["F.Cu", "Edge.Cuts"],
            ppmm=ppmm,
            crop_margin_mm=0.0,
        )

    bg = sample(full, 94.5, 94.4)  # inside edge, outside zone: background
    plane = sample(full, 105, 96.5)  # plane only
    f_track = sample(full, 105, 102)  # F.Cu track over the plane
    b_track = sample(full, 105, 103.5)  # B.Cu track over the plane

    assert plane != bg, "plane fill invisible in the composite"
    assert f_track == sample(control, 105, 102), (
        f"F.Cu track not on top / dimmed: composite {f_track} vs control "
        f"{sample(control, 105, 102)}"
    )
    assert b_track != plane and b_track != bg, (
        f"B.Cu track hidden under the plane fill: {b_track} vs plane {plane}"
    )
