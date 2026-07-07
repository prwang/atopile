# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""length_report contract — board-side per-net routed-length metrology (F2 lane).

An agent tuning routed lengths was blind: no atopile artifact carried ANY
per-net copper length. `faebryk.libs.kicad.length_report` closes that gap with
pure functions over a loaded board:

  build_length_report(pcb, project=None) -> {"nets", "pairs", "classes"}
    nets:    {net_name: {track_mm, via_count, segment_count}}  (every NAMED net)
    pairs:   {base: {p_net, n_net, p_track_mm, n_track_mm, skew_mm}} — detected
             by the SAME suffix conventions the router uses (`_P`/`_N`, bare
             `P`/`N` after a digit/underscore, `+`/`-`); a net pairs only
             WITHIN one convention (vendor net_queries.extract_diff_pair_base
             semantics, reimplemented — vendor code is not importable here).
    classes: when a .kicad_pro project is supplied — per netclass
             {longest, shortest, spread_mm} over its netclass_patterns
             (exact net names, the F5-authored shape).

What this pins (answers fixed by construction — a golden synthetic board whose
copper lengths are hand-computed):
  * arc length = circumcircle radius × subtended angle through start/mid/end
    (quarter circle + major-arc direction, hand-computed); collinear arc LOUD;
  * a straight 50 mm segment reports track_mm == 50.0 EXACTLY (KiCad ground
    truth: length of (start 100 100)-(end 150 100) is 50 mm by definition);
  * per-net track_mm / via_count / segment_count on the golden board;
  * all three pair suffix conventions pair up; an unpaired net appears in
    `nets` and NOT in `pairs` (no crash, no warn-spam); conventions never
    cross-pair (CLK_N does not pair with CLK+/CLK-);
  * a track referencing a net number absent from the net table is LOUD;
  * netclass aggregation uses exact pattern names; a pattern naming a net not
    on the board is LOUD; no project → classes == {}.

HONESTY (pinned by the module docstring, restated here): track_mm is routed
track centerline length ONLY — KiCad DRC length rules additionally count via
Z-length and pad-to-die; DRC remains the authority, this report is iteration
guidance. There is deliberately no fabricated "total" field.
"""

import math

import pytest

from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.length_report import (
    LengthReportError,
    arc_length_mm,
    build_length_report,
    extract_pair_suffix,
    length_report_for_board,
)
from faebryk.libs.kicad.other_fileformats import C_kicad_project_file
from faebryk.libs.util import repo_root

# ---------------------------------------------------------------------------
# golden synthetic board — the standard recipe (test_matched_length_rules
# pattern): fixture header (layers + setup) + net table + copper + close.
# ---------------------------------------------------------------------------
_FIXTURE = (
    repo_root() / "test/common/resources/fileformats/kicad/v10/pcb/test.kicad_pcb"
)

_NETS = ("A_P", "A_N", "LVDS0P", "LVDS0N", "CLK+", "CLK-", "CLK_N", "LONELY")

# CLK+ carries a quarter circle (center (100,120), r=10) plus a straight 10 mm
# tail — its expected length is 10·π/2 + 10, hand-computed, NOT read back from
# the implementation.
_QUARTER = 10.0 * math.pi / 2.0
_CLK_P_MM = _QUARTER + 10.0


def _segment(x1, y1, x2, y2, net: int) -> str:
    return (
        f"\t(segment\n\t\t(start {x1} {y1})\n\t\t(end {x2} {y2})\n"
        f'\t\t(width 0.2)\n\t\t(layer "F.Cu")\n\t\t(net {net})\n\t)'
    )


def _arc(x1, y1, xm, ym, x2, y2, net: int) -> str:
    return (
        f"\t(arc\n\t\t(start {x1} {y1})\n\t\t(mid {xm} {ym})\n"
        f"\t\t(end {x2} {y2})\n"
        f'\t\t(width 0.2)\n\t\t(layer "F.Cu")\n\t\t(net {net})\n\t)'
    )


def _via(x, y, net: int) -> str:
    return (
        f"\t(via\n\t\t(at {x} {y})\n\t\t(size 0.6)\n\t\t(drill 0.3)\n"
        f'\t\t(layers "F.Cu" "B.Cu")\n\t\t(net {net})\n\t)'
    )


def _board_text(copper: list[str], nets: tuple[str, ...] = _NETS) -> str:
    header = "".join(_FIXTURE.read_text().splitlines(keepends=True)[:169])
    net_table = ['\t(net 0 "")'] + [
        f'\t(net {i} "{name}")' for i, name in enumerate(nets, start=1)
    ]
    return header + "\n".join(net_table + copper) + "\n\t(embedded_fonts no)\n)\n"


def _golden_copper() -> list[str]:
    # net numbers = 1-based index into _NETS
    return [
        _segment(100, 100, 150, 100, 1),  # A_P: 50.0 mm
        _via(150, 100, 1),  # A_P: 1 via
        _segment(100, 101, 150.8, 101, 2),  # A_N: 50.8 mm
        _segment(100, 110, 130, 110, 3),  # LVDS0P: 30.0 mm
        _segment(100, 111, 129, 111, 4),  # LVDS0N: 29.0 mm
        # CLK+: quarter circle, center (100,120), r=10, start→mid→end CCW-in-file
        _arc(110, 120, 107.0710678, 127.0710678, 100, 130, 5),
        _segment(100, 130, 100, 140, 5),  # CLK+ tail: 10.0 mm
        _segment(120, 120, 120, 145, 6),  # CLK-: 25.0 mm
        _segment(100, 148, 105, 148, 7),  # CLK_N: 5.0 mm (unpaired _P-style)
        _segment(100, 150, 110, 150, 8),  # LONELY: 10.0 mm (no pair suffix)
    ]


def _golden_pcb():
    return kicad.loads(kicad.pcb.PcbFile, _board_text(_golden_copper())).kicad_pcb


# ---------------------------------------------------------------------------
# arc math — hand-computed, exact inputs
# ---------------------------------------------------------------------------
def test_arc_length_quarter_circle_hand_computed():
    """Quarter circle, radius 10, center origin: length = 10·π/2 = 15.70796…"""
    h = 10.0 / math.sqrt(2.0)
    assert arc_length_mm((10, 0), (h, h), (0, 10)) == pytest.approx(
        _QUARTER, rel=1e-12
    )


def test_arc_length_major_arc_goes_through_mid():
    """Same endpoints, mid on the FAR side: the ¾ circle (3·π/2·r), not the
    short way — the mid point decides the direction, not the chord."""
    assert arc_length_mm((10, 0), (0, -10), (0, 10)) == pytest.approx(
        15.0 * math.pi, rel=1e-12
    )


def test_collinear_arc_is_loud():
    with pytest.raises(LengthReportError, match="collinear"):
        arc_length_mm((0, 0), (5, 0), (10, 0))


# ---------------------------------------------------------------------------
# per-net stats on the golden board
# ---------------------------------------------------------------------------
def test_straight_segment_matches_kicad_ground_truth_exactly():
    """(start 100 100)-(end 150 100) is 50 mm by definition — EXACT equality,
    no tolerance: any scale/unit slip must fail this."""
    report = build_length_report(_golden_pcb())
    assert report["nets"]["A_P"]["track_mm"] == 50.0


def test_golden_board_per_net_stats():
    report = build_length_report(_golden_pcb())
    nets = report["nets"]
    assert set(nets) == set(_NETS)  # every NAMED net; net 0 "" excluded
    assert nets["A_P"] == {"track_mm": 50.0, "via_count": 1, "segment_count": 1}
    assert nets["A_N"]["track_mm"] == pytest.approx(50.8, abs=1e-6)
    assert nets["A_N"]["via_count"] == 0
    assert nets["LVDS0P"]["track_mm"] == 30.0
    assert nets["LVDS0N"]["track_mm"] == 29.0
    # arc + straight tail summed; file coords carry 1e-7 rounding → abs=1e-5
    assert nets["CLK+"]["track_mm"] == pytest.approx(_CLK_P_MM, abs=1e-5)
    assert nets["CLK+"]["segment_count"] == 2  # 1 arc + 1 segment
    assert nets["CLK-"]["track_mm"] == 25.0
    assert nets["LONELY"] == {"track_mm": 10.0, "via_count": 0, "segment_count": 1}


def test_dangling_net_reference_is_loud():
    """Copper referencing a net number absent from the table must raise — a
    silent zero-length net would misguide the tuning loop."""
    text = _board_text([_segment(0, 0, 10, 0, 99)])
    pcb = kicad.loads(kicad.pcb.PcbFile, text).kicad_pcb
    with pytest.raises(LengthReportError, match="99"):
        build_length_report(pcb)


# ---------------------------------------------------------------------------
# differential pairs — all three conventions, no cross-convention pairing
# ---------------------------------------------------------------------------
def test_extract_pair_suffix_conventions():
    assert extract_pair_suffix("A_P") == ("A", True, "_P")
    assert extract_pair_suffix("A_N") == ("A", False, "_P")
    assert extract_pair_suffix("LVDS0P") == ("LVDS0", True, "P")
    assert extract_pair_suffix("LVDS0N") == ("LVDS0", False, "P")
    assert extract_pair_suffix("CLK+") == ("CLK", True, "+")
    assert extract_pair_suffix("CLK-") == ("CLK", False, "+")
    # bare P/N only after a digit or underscore (vendor semantics): a name
    # merely ending in P is not a pair half
    assert extract_pair_suffix("STOP") is None
    assert extract_pair_suffix("LONELY") is None


def test_pairs_all_three_conventions_and_unpaired_net():
    report = build_length_report(_golden_pcb())
    pairs = report["pairs"]
    assert set(pairs) == {"A", "LVDS0", "CLK"}
    a = pairs["A"]
    assert (a["p_net"], a["n_net"]) == ("A_P", "A_N")
    assert a["p_track_mm"] == 50.0
    assert a["n_track_mm"] == pytest.approx(50.8, abs=1e-6)
    assert a["skew_mm"] == pytest.approx(0.8, abs=1e-6)
    lv = pairs["LVDS0"]
    assert (lv["p_net"], lv["n_net"]) == ("LVDS0P", "LVDS0N")
    assert lv["skew_mm"] == pytest.approx(1.0, abs=1e-6)
    clk = pairs["CLK"]
    assert (clk["p_net"], clk["n_net"]) == ("CLK+", "CLK-")
    assert clk["skew_mm"] == pytest.approx(_CLK_P_MM - 25.0, abs=1e-5)
    # CLK_N (style _P, no CLK_P partner) must NOT cross-pair with CLK+/CLK-;
    # it and LONELY still appear in nets — unpaired is not an error.
    assert "CLK_N" in report["nets"] and "LONELY" in report["nets"]
    assert clk["n_net"] != "CLK_N"


# ---------------------------------------------------------------------------
# netclass aggregation (project supplied)
# ---------------------------------------------------------------------------
def _project(patterns: list[tuple[str, str]]) -> C_kicad_project_file:
    ns = C_kicad_project_file.C_net_settings
    project = C_kicad_project_file()
    project.net_settings.classes = [
        ns.C_classes(name="Default"),
        ns.C_classes(name="fast"),
    ]
    project.net_settings.netclass_patterns = [
        ns.C_netclass_pattern(netclass=c, pattern=p) for c, p in patterns
    ]
    return project


def test_netclass_aggregation_longest_shortest_spread():
    project = _project([("fast", "A_P"), ("fast", "A_N"), ("fast", "LVDS0P")])
    report = build_length_report(_golden_pcb(), project=project)
    assert set(report["classes"]) == {"fast"}  # Default has no patterns → absent
    fast = report["classes"]["fast"]
    assert fast["longest"]["net"] == "A_N"
    assert fast["longest"]["track_mm"] == pytest.approx(50.8, abs=1e-6)
    assert fast["shortest"] == {"net": "LVDS0P", "track_mm": 30.0}
    assert fast["spread_mm"] == pytest.approx(20.8, abs=1e-6)


def test_netclass_pattern_not_on_board_is_loud():
    project = _project([("fast", "GHOST")])
    with pytest.raises(LengthReportError, match="GHOST"):
        build_length_report(_golden_pcb(), project=project)


def test_no_project_means_empty_classes():
    report = build_length_report(_golden_pcb())
    assert report["classes"] == {}


# ---------------------------------------------------------------------------
# file-level entrypoint (the shape snapshot/diagnose consume)
# ---------------------------------------------------------------------------
def test_length_report_for_board_picks_up_adjacent_project(tmp_path):
    board = tmp_path / "golden.kicad_pcb"
    board.write_text(_board_text(_golden_copper()))
    # no adjacent .kicad_pro → classes {}
    report = length_report_for_board(board)
    assert set(report) == {"nets", "pairs", "classes"}
    assert report["classes"] == {}
    assert report["nets"]["A_P"]["track_mm"] == 50.0
    # with the project staged next to the board (the snapshot staging layout)
    _project([("fast", "A_P"), ("fast", "LVDS0P")]).dumps(
        tmp_path / "golden.kicad_pro"
    )
    report = length_report_for_board(board)
    assert report["classes"]["fast"]["longest"]["net"] == "A_P"
