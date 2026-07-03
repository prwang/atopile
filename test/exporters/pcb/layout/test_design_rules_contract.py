# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""The `rules:` header contract (DesignRules).

The router cannot start with no rules: a plan with route_stages and no `rules:`
is loud AT PARSE. The header plays two roles, both pinned here:

  * DEFAULTS — a stage/lane that omits geometry inherits it (track_width,
    clearance, diff_pair_width/gap). The fill must survive
    `model_dump(exclude_unset=True)` — that is how layout_plan_runner forwards
    config to the router, so a fill that only wrote the attribute (without
    marking the field set) would SILENTLY never reach the router.
  * MINIMUMS — explicit values below the board minimums (clearance floor,
    intra-pair gap vs clearance, trunk spacing vs inter_pair_clearance) are a
    short circuit by construction and rejected at parse.

Lengths accept mm numbers and "mil"/"mm" strings ("5mil" == 0.127mm exactly).
DRC authority: rules → the .kicad_pro Default net class + the .kicad_dru custom
rules (courtyard component spacing, diff-pair max uncoupled length, clearance
floor) so kicad-cli DRC judges the same numbers the router started from.
"""

import pytest
from faebryk.exporters.pcb.layout.layout_plan import (
    DesignRules,
    LayoutPlanError,
    load_layout_plan,
)
from pydantic import ValidationError

_LOUD = (LayoutPlanError, ValidationError)

_RULES_HEADER = """
rules:
  clearance: 5mil
  track_width: 0.15
  diff_pair_width: 0.2
  diff_pair_gap: 0.15
  inter_pair_clearance: 20mil
  component_spacing: 20mil
  uncoupled_max_length: 50mil
"""

_STAGES_NO_CONFIG = """
route_stages:
  - name: sigs
    mode: single
    nets:
      - a.x
  - name: pairs
    mode: diff
    nets:
      - a.p
      - a.n
"""

_BUNDLE_NO_GAPS = """
board:
  outline:
    origin: [0, 0]
    size: [10, 10]
  stackup:
    layers:
      - {name: F.Cu, type: copper}
      - {name: d1, type: dielectric, thickness: 0.2, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu, type: copper}
route_stages:
  - type: bundle
    name: bus
    lanes:
      - diff: [a.p, a.n]
    trunk:
      centerline:
        - at: [0, 0]
          spacing: 0.6
        - at: [5, 0]
          spacing: 0.6
    breakouts:
      - at: left
      - at: right
"""


# ---------------------------------------------------------------------------
# length parsing: mm numbers and mil/mm strings; anything else is loud
# ---------------------------------------------------------------------------
def test_rule_lengths_parse_mil_and_mm():
    r = DesignRules.model_validate(
        {"clearance": "5mil", "track_width": "0.15mm", "component_spacing": 0.508}
    )
    assert r.clearance == pytest.approx(0.127)  # 5 * 0.0254 exactly
    assert r.track_width == pytest.approx(0.15)
    assert r.component_spacing == pytest.approx(0.508)


@pytest.mark.parametrize("bad", ["5 furlong", "mil", "", True, [0.1], {"mm": 1}])
def test_rule_length_garbage_is_loud(bad):
    with pytest.raises(_LOUD):
        DesignRules.model_validate({"clearance": bad, "track_width": 0.1})


def test_rules_reject_unknown_key():
    with pytest.raises(_LOUD):
        DesignRules.model_validate(
            {"clearance": 0.1, "track_width": 0.1, "trackwidth": 0.2}
        )


def test_rules_diff_gap_below_clearance_is_loud():
    # P/N are different nets: an intra-pair gap below clearance can never pass DRC
    with pytest.raises(_LOUD):
        DesignRules.model_validate(
            {"clearance": 0.2, "track_width": 0.1, "diff_pair_gap": 0.1}
        )


# ---------------------------------------------------------------------------
# the gate: route_stages with no rules header cannot parse
# ---------------------------------------------------------------------------
def test_route_stages_without_rules_is_loud():
    with pytest.raises(_LOUD, match="rules"):
        load_layout_plan(_STAGES_NO_CONFIG)


def test_plan_without_stages_needs_no_rules():
    plan = load_layout_plan("rooms:\n  - module: a\n")
    assert plan.rules is None


# ---------------------------------------------------------------------------
# defaults flow — and they must survive exclude_unset (the forwarding path)
# ---------------------------------------------------------------------------
def test_rules_fill_single_and_diff_stage_config():
    plan = load_layout_plan(_RULES_HEADER + _STAGES_NO_CONFIG)
    single, diff = plan.route_stages
    assert single.config.track_width == pytest.approx(0.15)
    assert single.config.clearance == pytest.approx(0.127)
    assert diff.config.track_width == pytest.approx(0.2)  # diff_pair_width
    assert diff.config.diff_pair_gap == pytest.approx(0.15)
    # the runner forwards with exclude_unset=True: the fill MUST be marked set
    fwd = single.config.model_dump(exclude_unset=True)
    assert fwd["track_width"] == pytest.approx(0.15)
    assert fwd["clearance"] == pytest.approx(0.127)
    fwd = diff.config.model_dump(exclude_unset=True)
    assert fwd["diff_pair_gap"] == pytest.approx(0.15)


def test_rules_fill_bundle_lane_gap_and_width():
    plan = load_layout_plan(_RULES_HEADER + _BUNDLE_NO_GAPS)
    (bundle,) = plan.route_stages
    (lane,) = bundle.lanes
    assert lane.gap == pytest.approx(0.15)
    assert lane.width == pytest.approx(0.2)
    assert bundle.config.track_width == pytest.approx(0.15)


def test_explicit_stage_values_beat_rules():
    text = (
        _RULES_HEADER
        + """
route_stages:
  - name: fat
    mode: single
    nets:
      - a.x
    config:
      track_width: 0.5
"""
    )
    plan = load_layout_plan(text)
    assert plan.route_stages[0].config.track_width == pytest.approx(0.5)


def test_bundle_lane_without_gap_and_no_rule_gap_is_loud():
    header = "rules:\n  clearance: 0.1\n  track_width: 0.15\n"
    with pytest.raises(_LOUD, match="diff_pair_gap"):
        load_layout_plan(header + _BUNDLE_NO_GAPS)


# ---------------------------------------------------------------------------
# minimums: below-rule explicit values are a parse error, not a DRC surprise
# ---------------------------------------------------------------------------
def test_stage_clearance_below_rule_is_loud():
    text = (
        _RULES_HEADER
        + """
route_stages:
  - name: tight
    mode: single
    nets:
      - a.x
    config:
      clearance: 0.05
"""
    )
    with pytest.raises(_LOUD, match="below"):
        load_layout_plan(text)


def test_bundle_lane_gap_below_clearance_is_loud():
    text = _RULES_HEADER + _BUNDLE_NO_GAPS.replace(
        "- diff: [a.p, a.n]", "- diff: [a.p, a.n]\n        gap: 0.05"
    )
    with pytest.raises(_LOUD, match="short circuit"):
        load_layout_plan(text)


def test_trunk_spacing_below_inter_pair_clearance_is_loud():
    text = _RULES_HEADER + _BUNDLE_NO_GAPS.replace("spacing: 0.6", "spacing: 0.2")
    with pytest.raises(_LOUD, match="inter_pair_clearance"):
        load_layout_plan(text)


# ---------------------------------------------------------------------------
# DRC authority: .kicad_pro Default class + .kicad_dru custom rules
# ---------------------------------------------------------------------------
def test_rules_author_default_net_class():
    from faebryk.exporters.pcb.layout.board_rules import generate_project_rules

    plan = load_layout_plan(_RULES_HEADER + _STAGES_NO_CONFIG)
    project = generate_project_rules(plan, {"signal_nets": {}})
    assert project is not None
    default = next(
        c for c in project.net_settings.classes if c.name == "Default"
    )
    assert default.clearance == pytest.approx(0.127)
    assert default.track_width == pytest.approx(0.15)
    assert default.diff_pair_width == pytest.approx(0.2)
    assert default.diff_pair_gap == pytest.approx(0.15)


def test_dru_rules_content():
    from faebryk.exporters.pcb.layout.board_rules import generate_dru_rules

    plan = load_layout_plan(_RULES_HEADER + _STAGES_NO_CONFIG)
    dru = generate_dru_rules(plan)
    assert dru is not None
    assert "(version 1)" in dru
    assert "(constraint clearance (min 0.127mm))" in dru
    assert "(constraint courtyard_clearance (min 0.508mm))" in dru
    assert "(constraint diff_pair_uncoupled (max 1.27mm))" in dru


def test_dru_rules_none_without_rules():
    from faebryk.exporters.pcb.layout.board_rules import generate_dru_rules

    plan = load_layout_plan("rooms:\n  - module: a\n")
    assert generate_dru_rules(plan) is None
