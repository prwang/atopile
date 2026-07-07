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
