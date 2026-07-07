# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""F1 contract — matched-length DRC rules: layout.yaml → .kicad_dru.

kicad-cli 10.0.3 headless DRC fires these custom constraints (empirically
pinned by the live tests below):

  * `(constraint length (min/max ..))`      → "length_out_of_range"
  * `(constraint skew (max ..))`            → "skew_out_of_range" — the matched
    group is ALL items matching the rule condition, bucketed per net, each net
    judged against the LONGEST net of the group (i.e. inter-pair/group match);
  * `(constraint skew (max ..) (within_diff_pairs))` → restricts comparison to
    within each _P/_N pair (intra-pair skew).

Authoring surface (this lane):
  * `rules.intra_pair_skew_max` → ONE board-wide rule conditioned on
    `A.inDiffPair('*')` (matches every suffix-convention pair);
  * per net class: `skew_max` / `intra_pair_skew_max` / `length_min` /
    `length_max` → rules conditioned on `A.hasNetclass('<name>')` (the class
    itself reaches KiCad through the F5 `.kicad_pro` net_settings — the same
    build already writes both files next to each other).

Coherence (parse-time loud): values > 0; a class setting any matched-length
field with an EMPTY nets list is rejected (a constraint that can never bite is
a lie); a class name containing a quote cannot be embedded in the dru condition
string and is rejected; an inverted length window (length_min > length_max) can
never be satisfied and is rejected.

SKEW-RULE EXCLUSIVITY (empirically pinned on kicad-cli 10.0.3): KiCad has ONE
skew constraint type — `(within_diff_pairs)` is an option on it, not a second
type — and DRC keeps only the LAST matching skew rule per item, so two skew
rules over the same nets silently disable each other. Therefore:
  * a class setting BOTH `skew_max` and `intra_pair_skew_max` is rejected at
    parse (the intra rule, emitted second, would shadow the group rule);
  * `rules.intra_pair_skew_max` combined with a class `skew_max` whose nets
    resolve to suffix-convention diff-pair members is rejected at emit
    (`generate_project_rules`, the seam with bridge② — the class rule, emitted
    after the board rule, would shadow the intra-pair budget).
The board-side lengths report (F2) still measures the non-DRC-checkable number.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from faebryk.exporters.pcb.layout.board_rules import (
    generate_dru_rules,
    generate_project_rules,
)
from faebryk.exporters.pcb.layout.layout_plan import (
    Board,
    DesignRules,
    LayoutPlan,
    LayoutPlanError,
    NetClass,
    load_layout_plan,
)
from faebryk.libs.util import repo_root

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

_ML_FIELDS = ("skew_max", "intra_pair_skew_max", "length_min", "length_max")

# the violation types this lane authors rules for — the control board asserts
# ZERO of exactly these (other DRC noise on a bare synthetic board is fine).
_MATCHED_LENGTH_TYPES = {
    "skew_out_of_range",
    "length_out_of_range",
    "diff_pair_uncoupled_length_too_long",
}


# ---------------------------------------------------------------------------
# schema: parsing (mm floats + mil/mm strings), junk keys, coherence
# ---------------------------------------------------------------------------
def test_rules_intra_pair_skew_parses_mm_and_mil():
    r = DesignRules.model_validate(
        {"clearance": 0.1, "track_width": 0.1, "intra_pair_skew_max": "20mil"}
    )
    assert r.intra_pair_skew_max == pytest.approx(0.508)
    r = DesignRules.model_validate(
        {"clearance": 0.1, "track_width": 0.1, "intra_pair_skew_max": 0.5}
    )
    assert r.intra_pair_skew_max == pytest.approx(0.5)
    # optional: omitted stays None (no silent default budget)
    r = DesignRules.model_validate({"clearance": 0.1, "track_width": 0.1})
    assert r.intra_pair_skew_max is None


def test_rules_intra_pair_skew_nonpositive_is_loud():
    with pytest.raises(ValidationError):
        DesignRules.model_validate(
            {"clearance": 0.1, "track_width": 0.1, "intra_pair_skew_max": 0}
        )


def test_netclass_matched_length_fields_parse_mm_and_mil():
    # skew_max and intra_pair_skew_max are mutually exclusive on one class
    # (skew-rule exclusivity, module docstring) — parse them on two classes.
    nc = NetClass.model_validate(
        {
            "name": "fast",
            "skew_max": "20mil",
            "length_min": "40mm",
            "length_max": 60,
            "nets": ["top.a_p", "top.a_n"],
        }
    )
    assert nc.skew_max == pytest.approx(0.508)
    assert nc.length_min == pytest.approx(40.0)
    assert nc.length_max == pytest.approx(60.0)
    nc2 = NetClass.model_validate(
        {"name": "serdes", "intra_pair_skew_max": 0.25, "nets": ["top.a_p"]}
    )
    assert nc2.intra_pair_skew_max == pytest.approx(0.25)


def test_netclass_junk_key_is_loud():
    with pytest.raises(ValidationError):
        NetClass.model_validate(
            {"name": "fast", "skewmax": 1.0, "nets": ["top.a"]}
        )


@pytest.mark.parametrize("field", _ML_FIELDS)
def test_netclass_matched_length_nonpositive_is_loud(field):
    with pytest.raises(ValidationError):
        NetClass.model_validate({"name": "fast", field: 0, "nets": ["top.a"]})
    with pytest.raises(ValidationError):
        NetClass.model_validate({"name": "fast", field: -1.0, "nets": ["top.a"]})


@pytest.mark.parametrize("field", _ML_FIELDS)
def test_netclass_matched_length_with_empty_nets_is_loud(field):
    """A matched-length constraint over a class assigning no nets can never
    bite — that is a lie, rejected at parse."""
    with pytest.raises(ValidationError):
        NetClass.model_validate({"name": "fast", field: 1.0})
    with pytest.raises(ValidationError):
        NetClass.model_validate({"name": "fast", field: 1.0, "nets": []})


def test_netclass_inverted_length_window_is_loud():
    """length_min > length_max is a window no routed net can ever satisfy —
    every class net would be length_out_of_range forever (the tune loop could
    never converge). Same 'can never pass DRC' precedent as DesignRules'
    diff_pair_gap < clearance."""
    with pytest.raises(ValidationError, match="inverted"):
        NetClass.model_validate(
            {"name": "fast", "length_min": 60.0, "length_max": 40.0,
             "nets": ["top.a"]}
        )
    # equal bounds are a (tight but satisfiable) window — not rejected
    nc = NetClass.model_validate(
        {"name": "fast", "length_min": 50.0, "length_max": 50.0,
         "nets": ["top.a"]}
    )
    assert nc.length_min == nc.length_max == pytest.approx(50.0)


def test_netclass_skew_and_intra_skew_together_is_loud():
    """Skew-rule exclusivity (module docstring): both fields on one class emit
    two SKEW_CONSTRAINT rules with the identical condition; KiCad keeps only
    the LAST one, so `skew_max` would silently never be enforced (empirically
    pinned: group spread 19x over budget reported ZERO violations)."""
    with pytest.raises(ValidationError, match="skew_max"):
        NetClass.model_validate(
            {
                "name": "fast",
                "skew_max": 0.3,
                "intra_pair_skew_max": 2.0,
                "nets": ["top.a_p", "top.a_n"],
            }
        )


def test_rules_intra_skew_plus_class_skew_over_pair_nets_is_loud():
    """The board-wide intra-pair rule is emitted BEFORE class rules, so a class
    `skew_max` over diff-pair nets shadows it completely (last match wins):
    the P-vs-N budget would never be judged for any net in the class
    (empirically pinned: 2.5x intra breach reported ZERO violations while the
    class rule was present). Rejected at the bridge② seam, naming both rules."""
    plan = LayoutPlan(
        rules=DesignRules(
            clearance=0.127, track_width=0.15, intra_pair_skew_max=0.2
        ),
        board=Board(
            net_classes=[
                NetClass(name="sata", skew_max=1.0, nets=list(_IR["signal_nets"]))
            ]
        ),
    )
    with pytest.raises(LayoutPlanError) as ei:
        generate_project_rules(plan, _IR)
    msg = str(ei.value)
    assert "intra_pair_skew_max" in msg and "sata" in msg


def test_rules_intra_skew_plus_class_skew_over_nonpair_nets_is_fine():
    """The conflict exists only for nets the board-wide `A.inDiffPair('*')`
    rule also matches — a skew_max class over NON-pair nets coexists."""
    ir = {"signal_nets": {"top.d0": "D0", "top.d1": "D1"}}
    plan = LayoutPlan(
        rules=DesignRules(
            clearance=0.127, track_width=0.15, intra_pair_skew_max=0.2
        ),
        board=Board(
            net_classes=[
                NetClass(name="bus", skew_max=1.0, nets=["top.d0", "top.d1"])
            ]
        ),
    )
    project = generate_project_rules(plan, ir)
    assert project is not None


def test_class_intra_skew_plus_rules_intra_skew_is_a_scoped_override():
    """SAME-semantics rules: a class `intra_pair_skew_max` after the board-wide
    one gives the class nets the class budget and everyone else the board
    budget — a coherent scoped override, NOT shadowing; stays accepted."""
    plan = LayoutPlan(
        rules=DesignRules(
            clearance=0.127, track_width=0.15, intra_pair_skew_max=0.2
        ),
        board=Board(
            net_classes=[
                NetClass(
                    name="sata",
                    intra_pair_skew_max=0.1,
                    nets=list(_IR["signal_nets"]),
                )
            ]
        ),
    )
    project = generate_project_rules(plan, _IR)
    assert project is not None
    dru = generate_dru_rules(plan)
    assert '(rule "board-diffpair-intra-skew"' in dru
    assert '(rule "class-sata-intra-skew"' in dru


def test_netclass_quoted_name_with_matched_length_is_loud():
    """The class name is embedded in the dru condition string
    `A.hasNetclass('<name>')` — a quote cannot be escaped there."""
    for bad in ("fa'st", 'fa"st'):
        with pytest.raises(ValidationError):
            NetClass.model_validate(
                {"name": bad, "skew_max": 1.0, "nets": ["top.a"]}
            )
    # scope pin: without a matched-length field the name never reaches a dru
    # condition string — it stays representable (JSON escapes it in .kicad_pro).
    assert NetClass.model_validate({"name": "fa'st"}).name == "fa'st"


# ---------------------------------------------------------------------------
# golden emission: the full .kicad_dru text for a plan exercising every field
# ---------------------------------------------------------------------------
# NB the golden plan respects skew-rule exclusivity (module docstring): the
# class combining a group budget with an intra budget was a shadowing lie and
# is now rejected at parse — the group `skew_max` lives on its own class over
# NON-pair nets, the intra budget on the pair class.
_GOLDEN_YAML = """\
rules:
  clearance: 0.15
  track_width: 0.2
  component_spacing: 0.5
  uncoupled_max_length: 5
  intra_pair_skew_max: 20mil
board:
  net_classes:
    - name: fast
      intra_pair_skew_max: 0.25
      length_min: 40
      length_max: 60
      nets:
        - top.a_p
        - top.a_n
    - name: bus
      skew_max: 1.5
      nets:
        - top.d0
        - top.d1
    - name: longonly
      length_max: 55
      nets:
        - top.b_p
"""

_GOLDEN_DRU = """\
(version 1)
(rule "board-min-clearance"
  (constraint clearance (min 0.15mm)))
(rule "board-component-spacing"
  (constraint courtyard_clearance (min 0.5mm)))
(rule "board-diffpair-uncoupled-max"
  (constraint diff_pair_uncoupled (max 5.0mm)))
(rule "board-diffpair-intra-skew"
  (condition "A.inDiffPair('*')")
  (constraint skew (max 0.508mm) (within_diff_pairs)))
(rule "class-fast-intra-skew"
  (condition "A.hasNetclass('fast')")
  (constraint skew (max 0.25mm) (within_diff_pairs)))
(rule "class-fast-length"
  (condition "A.hasNetclass('fast')")
  (constraint length (min 40.0mm) (max 60.0mm)))
(rule "class-bus-skew"
  (condition "A.hasNetclass('bus')")
  (constraint skew (max 1.5mm)))
(rule "class-longonly-length"
  (condition "A.hasNetclass('longonly')")
  (constraint length (max 55.0mm)))
"""


def test_golden_dru_emission():
    plan = load_layout_plan(_GOLDEN_YAML)
    assert generate_dru_rules(plan) == _GOLDEN_DRU


def test_class_rules_emit_without_rules_header():
    """A plan with NO `rules:` header but a class carrying matched-length fields
    still writes a dru — the class rules must not silently vanish behind the
    old 'no rules header → None' gate."""
    plan = LayoutPlan(
        board=Board(
            net_classes=[NetClass(name="fast", skew_max=1.0, nets=["top.a_p"])]
        )
    )
    dru = generate_dru_rules(plan)
    assert dru is not None
    assert '(rule "class-fast-skew"' in dru
    assert "A.hasNetclass('fast')" in dru
    # and the honest None survives: no header, no matched-length fields
    plain = LayoutPlan(
        board=Board(net_classes=[NetClass(name="slow", clearance=0.3)])
    )
    assert generate_dru_rules(plain) is None


# ---------------------------------------------------------------------------
# LIVE kicad-cli bite tests — the proof the emitted rules actually fire under
# `kicad-cli pcb drc` (pattern:
# test_rule_area_contract.py::test_kicad_drc_enforces_component_class_headlessly).
#
# Synthetic board: the fixture header (layers + setup), a net table with two
# suffix-convention pairs, and four straight F.Cu segments with lengths fixed
# by construction:  A_P 50mm / A_N 50.8mm (intra skew 0.8mm),
# B_P = B_N = 45mm (inter-pair skew vs longest A_N = 5.8mm). Pair members sit
# 0.4mm apart (centerline) so the pairs couple.
# ---------------------------------------------------------------------------
_FIXTURE = (
    repo_root()
    / "test/common/resources/fileformats/kicad/v10/pcb/test.kicad_pcb"
)

_NETS = ("A_P", "A_N", "B_P", "B_N")
_IR = {"signal_nets": {f"top.{n.lower()}": n for n in _NETS}}


def _segment(x1: float, y1: float, x2: float, y2: float, net: int) -> str:
    return (
        f"\t(segment\n\t\t(start {x1} {y1})\n\t\t(end {x2} {y2})\n"
        f'\t\t(width 0.2)\n\t\t(layer "F.Cu")\n\t\t(net {net})\n\t)'
    )


def _synthetic_board_text() -> str:
    header = "".join(_FIXTURE.read_text().splitlines(keepends=True)[:169])
    nets = ['\t(net 0 "")'] + [
        f'\t(net {i} "{name}")' for i, name in enumerate(_NETS, start=1)
    ]
    segments = [
        _segment(100, 100, 150, 100, 1),  # A_P: 50mm
        _segment(100, 100.4, 150.8, 100.4, 2),  # A_N: 50.8mm
        _segment(100, 110, 145, 110, 3),  # B_P: 45mm
        _segment(100, 110.4, 145, 110.4, 4),  # B_N: 45mm
    ]
    return header + "\n".join(nets + segments) + "\n\t(embedded_fonts no)\n)\n"


def _run_drc(tmp_path: Path, subdir: str, plan: LayoutPlan) -> list[dict]:
    """Write the synthetic board + the plan's emitted .kicad_pro/.kicad_dru
    NEXT TO it (the exact build_steps layout kicad-cli auto-loads), run a
    headless DRC, return the violations."""
    d = tmp_path / subdir
    d.mkdir()
    board = d / "probe.kicad_pcb"
    board.write_text(_synthetic_board_text())
    project = generate_project_rules(plan, _IR)
    assert project is not None
    project.dumps(d / "probe.kicad_pro")
    dru = generate_dru_rules(plan)
    assert dru is not None
    (d / "probe.kicad_dru").write_text(dru)
    report = d / "drc.json"
    r = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(report),
         "--severity-all", str(board)],
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"drc failed:\n{r.stderr}"
    return json.loads(report.read_text()).get("violations", [])


def _of_type(violations: list[dict], vtype: str) -> list[dict]:
    return [v for v in violations if v.get("type") == vtype]


def _mentions(violation: dict, *net_names: str) -> bool:
    blob = json.dumps(violation)
    return any(n in blob for n in net_names)


needs_kicad = pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")


@needs_kicad
@pytest.mark.slow
@pytest.mark.regression
def test_live_intra_pair_skew_fires_on_mismatched_pair(tmp_path):
    """`rules.intra_pair_skew_max` 0.5mm: pair A skews 0.8mm → exactly the
    intra-pair `skew_out_of_range` fires, mentioning the A pair; the matched
    pair B is clean."""
    plan = LayoutPlan(
        rules=DesignRules(clearance=0.1, track_width=0.2, intra_pair_skew_max=0.5)
    )
    violations = _run_drc(tmp_path, "intra", plan)
    skews = _of_type(violations, "skew_out_of_range")
    assert skews, "intra-pair skew rule did not fire — (within_diff_pairs) broken?"
    assert all(_mentions(v, "A_P", "A_N") for v in skews), skews
    assert not any(_mentions(v, "B_P", "B_N") for v in skews), skews


@needs_kicad
@pytest.mark.slow
@pytest.mark.regression
def test_live_class_skew_and_length_fire(tmp_path):
    """Class-scoped rules through the REAL chain (netclass in .kicad_pro +
    `A.hasNetclass` condition in .kicad_dru): `skew_max` 1mm — B nets sit
    5.8mm under the group's longest (A_N) → inter-pair skew_out_of_range on B
    (A_P at 0.8mm passes); `length_min` 48mm → length_out_of_range on the
    45mm B nets only."""
    plan = LayoutPlan(
        board=Board(
            net_classes=[
                NetClass(
                    name="fast",
                    skew_max=1.0,
                    length_min=48.0,
                    nets=list(_IR["signal_nets"]),
                )
            ]
        )
    )
    violations = _run_drc(tmp_path, "cls", plan)

    skews = _of_type(violations, "skew_out_of_range")
    assert skews, "class skew rule did not fire — hasNetclass scoping broken?"
    assert all(_mentions(v, "B_P", "B_N") for v in skews), skews
    assert not any(_mentions(v, "A_P") and not _mentions(v, "B_P", "B_N")
                   for v in skews), skews

    lengths = _of_type(violations, "length_out_of_range")
    assert lengths, "class length_min rule did not fire"
    assert all(_mentions(v, "B_P", "B_N") for v in lengths), lengths
    assert not any(_mentions(v, "A_P", "A_N") for v in lengths), lengths


@needs_kicad
@pytest.mark.slow
@pytest.mark.regression
def test_live_control_within_tolerance_is_clean(tmp_path):
    """The control: the SAME board with every budget sized to the geometry
    (intra 2mm > 0.8mm, class skew 10mm > 5.8mm, length window 40–60mm) yields
    ZERO matched-length violations — the rules fire on breach, not on sight.
    Two plans, because skew-rule exclusivity (module docstring) makes
    `rules.intra_pair_skew_max` + a class `skew_max` over the same pair nets
    unrepresentable (the combination silently disabled the intra budget)."""
    plan_intra = LayoutPlan(
        rules=DesignRules(clearance=0.1, track_width=0.2, intra_pair_skew_max=2.0),
    )
    violations = _run_drc(tmp_path, "control_intra", plan_intra)
    matched = [v for v in violations if v.get("type") in _MATCHED_LENGTH_TYPES]
    assert matched == [], matched

    plan_class = LayoutPlan(
        board=Board(
            net_classes=[
                NetClass(
                    name="fast",
                    skew_max=10.0,
                    length_min=40.0,
                    length_max=60.0,
                    nets=list(_IR["signal_nets"]),
                )
            ]
        ),
    )
    violations = _run_drc(tmp_path, "control_class", plan_class)
    matched = [v for v in violations if v.get("type") in _MATCHED_LENGTH_TYPES]
    assert matched == [], matched
