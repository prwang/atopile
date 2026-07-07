# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""length_report contract — board-side per-net routed-length metrology (F2 lane).

An agent tuning routed lengths was blind: no atopile artifact carried ANY
per-net copper length. `faebryk.libs.kicad.length_report` closes that gap with
pure functions over a loaded board:

  build_length_report(pcb, project=None) -> {"nets", "pairs", "classes"}
    nets:    {net_name: {track_mm, via_count, segment_count}}  (every NAMED net)
    pairs:   {base: {p_net, n_net, p_track_mm, n_track_mm, skew_mm}} — detected
             by the SAME suffix conventions the router uses, ALL FIVE, in the
             vendor's own check order (DDR `_t_X`/`_c_X` and `_t`/`_c` first,
             then `_P`/`_N`, bare `P`/`N` after a digit/underscore, `+`/`-`);
             a net pairs only WITHIN one convention (vendor
             net_queries.extract_diff_pair_base semantics, reimplemented —
             vendor code is not importable here).
    classes: when a .kicad_pro project is supplied — per netclass
             {longest, shortest, spread_mm} over its netclass_patterns,
             matched with KiCad's OWN pattern semantics (exact name, anchored
             wildcard, anchored regex — union; stale patterns skipped).

What this pins (answers fixed by construction — a golden synthetic board whose
copper lengths are hand-computed):
  * arc length = circumcircle radius × subtended angle through start/mid/end
    (quarter circle + major-arc direction, hand-computed); collinear arc LOUD;
  * a straight 50 mm segment reports track_mm == 50.0 EXACTLY (KiCad ground
    truth: length of (start 100 100)-(end 150 100) is 50 mm by definition);
  * per-net track_mm / via_count / segment_count on the golden board;
  * all five pair suffix conventions pair up; an unpaired net appears in
    `nets` and NOT in `pairs` (no crash, no warn-spam); conventions never
    cross-pair (CLK_N does not pair with CLK+/CLK-); DDR channel-suffixed
    pairs stay channel-local (DQS0_t_A never pairs with DQS0_c_B);
  * a track referencing a net number absent from the net table is LOUD;
  * netclass aggregation matches patterns like KiCad (exact / wildcard /
    regex, all anchored); a stale pattern is skipped, not fatal (raising here
    used to brick snapshot/diagnose on GUI-touched boards); no project →
    classes == {}.

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


def test_extract_pair_suffix_ddr_tc_conventions():
    """The DDR true/complement conventions the vendor oracle checks FIRST
    (net_queries.extract_diff_pair_base): `X_t`/`X_c` and `X_t_A`/`X_c_A`
    (channel-suffixed; the base keeps the suffix as `X_X_A` so pairing stays
    channel-local). Without them, DDR pairs the router routes and intra-matches
    got no skew metrology (silent divergence from the claimed router parity)."""
    assert extract_pair_suffix("CK_t") == ("CK", True, "_t")
    assert extract_pair_suffix("CK_c") == ("CK", False, "_t")
    assert extract_pair_suffix("DQS0_t_A") == ("DQS0_X_A", True, "_t")
    assert extract_pair_suffix("DQS0_c_A") == ("DQS0_X_A", False, "_t")
    # vendor ordering: the mid `_t_`/`_c_` pattern wins over a trailing
    # convention (e.g. `X_t_N` is the _t style with base X_X_N, not a _P half)
    assert extract_pair_suffix("X_t_N") == ("X_X_N", True, "_t")


def test_ddr_tc_pairs_up_and_stays_channel_local():
    """CK_t/CK_c and DQS0_t_A/DQS0_c_A pair with correct skew; DQS0_c_B (other
    channel) does not cross-pair; _t never pairs with a _P/+ style net."""
    nets = ("CK_t", "CK_c", "DQS0_t_A", "DQS0_c_A", "DQS0_c_B")
    copper = [
        _segment(100, 100, 130, 100, 1),  # CK_t: 30.0 mm
        _segment(100, 101, 128, 101, 2),  # CK_c: 28.0 mm
        _segment(100, 110, 120, 110, 3),  # DQS0_t_A: 20.0 mm
        _segment(100, 111, 119, 111, 4),  # DQS0_c_A: 19.0 mm
        _segment(100, 120, 105, 120, 5),  # DQS0_c_B: 5.0 mm (unpaired)
    ]
    pcb = kicad.loads(kicad.pcb.PcbFile, _board_text(copper, nets)).kicad_pcb
    report = build_length_report(pcb)
    pairs = report["pairs"]
    assert set(pairs) == {"CK", "DQS0_X_A"}
    ck = pairs["CK"]
    assert (ck["p_net"], ck["n_net"]) == ("CK_t", "CK_c")
    assert ck["skew_mm"] == pytest.approx(2.0, abs=1e-6)
    dqs = pairs["DQS0_X_A"]
    assert (dqs["p_net"], dqs["n_net"]) == ("DQS0_t_A", "DQS0_c_A")
    assert dqs["skew_mm"] == pytest.approx(1.0, abs=1e-6)
    assert "DQS0_c_B" in report["nets"]


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


# KiCad's netclass_patterns are PATTERN MATCHERS (net_settings.cpp builds each
# entry as an anchored wildcard + anchored regex combined matcher), not exact
# names — the exact-name shape is merely what the F5 emitter authors. A raise
# on any non-exact/stale pattern bricked `ato snapshot` / `ato diagnose` on
# GUI-touched and reuse boards (a stale pattern is KiCad-normal after a net
# rename). The classes section now matches with KiCad's semantics.
def test_netclass_wildcard_pattern_expands_like_kicad():
    project = _project([("fast", "A_*")])
    report = build_length_report(_golden_pcb(), project=project)
    fast = report["classes"]["fast"]
    assert fast["longest"]["net"] == "A_N"
    assert fast["shortest"] == {"net": "A_P", "track_mm": 50.0}
    assert fast["spread_mm"] == pytest.approx(0.8, abs=1e-6)


def test_netclass_regex_pattern_expands_like_kicad():
    # anchored regex (EDA_PATTERN_MATCH_REGEX_ANCHORED): matches whole names
    project = _project([("fast", "LVDS0(P|N)")])
    report = build_length_report(_golden_pcb(), project=project)
    fast = report["classes"]["fast"]
    assert fast["longest"]["net"] == "LVDS0P"
    assert fast["shortest"]["net"] == "LVDS0N"
    # anchoring: the regex must NOT have swallowed A_P etc.
    assert fast["spread_mm"] == pytest.approx(1.0, abs=1e-6)


def test_netclass_stale_pattern_is_skipped_not_fatal():
    """A pattern matching zero nets is KiCad-normal (stale after a net
    rename) — skipped; other patterns of the class still aggregate.
    (Migrated invariant: this used to raise, which killed the whole
    snapshot/diagnose loop over a legal KiCad construct.)"""
    project = _project([("fast", "GHOST"), ("fast", "A_P"), ("fast", "A_N")])
    report = build_length_report(_golden_pcb(), project=project)
    fast = report["classes"]["fast"]
    assert fast["spread_mm"] == pytest.approx(0.8, abs=1e-6)
    # a class whose EVERY pattern is stale has nothing to aggregate → omitted
    project = _project([("fast", "GHOST")])
    report = build_length_report(_golden_pcb(), project=project)
    assert report["classes"] == {}


def test_netclass_invalid_regex_pattern_falls_back_to_wildcard_only():
    """A pattern that is not a valid regex (KiCad tolerates this: the regex
    matcher simply fails to compile and never matches) must not crash the
    report; it can still match as a wildcard/exact name."""
    project = _project([("fast", "A_P("), ("fast", "A_N")])
    report = build_length_report(_golden_pcb(), project=project)
    # "A_P(" matches nothing (not a net name, invalid regex, no wildcard hit)
    fast = report["classes"]["fast"]
    assert fast["longest"]["net"] == "A_N"
    assert fast["shortest"]["net"] == "A_N"


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
