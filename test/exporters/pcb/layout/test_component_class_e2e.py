# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""D5 end-to-end proof (unit-level, synthetic board — no example rebuild).

One layout.yaml room with `source: component_class` flows through the SAME
chain `generate_layout_plan` runs (build_steps.py): load_layout_plan →
generate_rule_areas (board side: the `(component_class "X")` placement token)
→ generate_component_class_membership (board side: the static per-footprint
`(component_classes (class "X"))` token — the membership channel kicad-cli
honors headlessly) AND generate_component_classes →
C_kicad_project_file.dumps (project side: the GUI-mirror SHEET_NAME
assignment). All three outputs agree on the ONE room identity (class name ==
room.module == the C3-stamped sheetname) — that agreement is the D5 contract,
so it is asserted here across the files, not per-module.

The kicad-cli oracles are in test_rule_area_contract.py:
test_kicad_ingests_component_class_rule_area (upgrade rc=0, tokens verbatim,
DRC-neutral) and test_kicad_drc_enforces_component_class_headlessly (a
hasComponentClass-conditioned DRC rule BITES through the static token, and
does NOT through the .kicad_pro assignment alone).
"""

import json

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.layout_ir import layout_ir

try:
    from faebryk.exporters.pcb.layout.board_rules import (
        generate_component_class_membership,
        generate_component_classes,
    )
    from faebryk.exporters.pcb.layout.layout_plan import load_layout_plan
    from faebryk.exporters.pcb.layout.rule_area import generate_rule_areas

    _D5_LANDED = True
except Exception:  # noqa: BLE001
    _D5_LANDED = False

    def _unlanded(*_a, **_k):
        raise RuntimeError("D5 not landed (S0 ratchet)")

    generate_component_classes = load_layout_plan = generate_rule_areas = _unlanded
    generate_component_class_membership = _unlanded

needs_d5 = pytest.mark.xfail(
    not _D5_LANDED, reason="D5 component-class plumbing not landed", strict=True
)

_YAML = """\
rooms:
  - module: top.analog
    source: component_class
    origin: [0.0, 0.0]
    size: [20.0, 20.0]
  - module: top.digital
    origin: [30.0, 0.0]
    size: [10.0, 10.0]
route_stages: []
"""

# minimal board: one managed footprint per room, sheetname pre-stamped (C3).
_BOARD = """\
(kicad_pcb
\t(version 20241229)
\t(generator "test_component_class_e2e")
\t(generator_version "10.0")
\t(general (thickness 1.6))
\t(layers (0 "F.Cu" signal) (2 "B.Cu" signal))
\t(net 0 "")
\t(net 1 "N1")
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000fab1")
\t\t(at 5 5 0)
\t\t(sheetname "top.analog")
\t\t(sheetfile "top.analog.kicad_sch")
\t\t(property "Reference" "U1" (at 0 0) (layer "F.SilkS") (effects (font (size 1 1))))
\t\t(property "atopile_address" "top.analog.u1" (at 0 0) (layer "F.Fab") (effects (font (size 1 1))))
\t\t(pad "1" smd rect (at 0 0 0) (size 1 1) (layers "F.Cu") (net 1 "N1") (uuid "00000000-0000-0000-0000-00000000fad1"))
\t)
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000fab2")
\t\t(at 35 5 0)
\t\t(sheetname "top.digital")
\t\t(sheetfile "top.digital.kicad_sch")
\t\t(property "Reference" "U2" (at 0 0) (layer "F.SilkS") (effects (font (size 1 1))))
\t\t(property "atopile_address" "top.digital.u2" (at 0 0) (layer "F.Fab") (effects (font (size 1 1))))
\t\t(pad "1" smd rect (at 0 0 0) (size 1 1) (layers "F.Cu") (net 1 "N1") (uuid "00000000-0000-0000-0000-00000000fad2"))
\t)
)
"""


@needs_d5
def test_component_class_room_flows_yaml_to_board_and_project(tmp_path):
    plan = load_layout_plan(_YAML)
    bf = kicad.loads(kicad.pcb.PcbFile, _BOARD)
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    # board side: one zone per room, each with ITS room's source token, plus
    # the static membership stamp on the class room's footprint (build_steps
    # runs the same pair back to back).
    zones = generate_rule_areas(pcb, plan, ir)
    assert generate_component_class_membership(pcb, plan) == 1
    board_text = kicad.dumps(bf)
    assert '(component_class "top.analog")' in board_text
    assert '(sheetname "top.digital")' in board_text
    # exactly-one-source per zone (footprints legitimately carry their C3
    # `(sheetname ...)` in the text, so this is scoped to the placements)
    by_room = {z.name.removeprefix("rule_area_"): z.placement for z in zones}
    assert by_room["top.analog"].component_class == "top.analog"
    assert by_room["top.analog"].sheetname is None
    assert by_room["top.digital"].sheetname == "top.digital"
    assert by_room["top.digital"].component_class is None
    assert '(component_class "top.digital")' not in board_text

    # the static membership token lands ONLY on the class room's footprint —
    # the channel kicad-cli resolves headlessly (the .kicad_pro assignment
    # below is the GUI mirror).
    by_addr = {
        next(p.value for p in fp.propertys if p.name == "atopile_address"): fp
        for fp in pcb.footprints
    }
    analog_cc = by_addr["top.analog.u1"].component_classes
    assert analog_cc is not None
    assert [c.name for c in analog_cc.classes] == ["top.analog"]
    assert by_addr["top.digital.u2"].component_classes is None
    assert '(class "top.analog")' in board_text

    # project side: only the class room is declared; write like build_steps does
    project = generate_component_classes(plan)
    assert project is not None
    proj_path = tmp_path / "board.kicad_pro"
    project.dumps(proj_path)
    settings = json.loads(proj_path.read_text())["component_class_settings"]
    assert settings["meta"]["version"] == 0
    assert [a["component_class"] for a in settings["assignments"]] == ["top.analog"]
    assert settings["assignments"][0]["conditions"]["SHEET_NAME"] == {
        "primary": "top.analog"
    }

    # SIGSEGV tripwire holds on the D5 path too (CLAUDE.md hazard)
    assert "source_type" not in board_text


def _fp(pcb, sheetname):
    return next(fp for fp in pcb.footprints if fp.sheetname == sheetname)


def _class_names(fp) -> list[str]:
    cc = fp.component_classes
    return [c.name for c in cc.classes] if cc is not None else []


def _cc_plan(*modules, source="component_class"):
    from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, Room

    return LayoutPlan(
        rooms=[Room(module=m, source=source) for m in modules], route_stages=[]
    )


@needs_d5
def test_membership_stamp_is_idempotent_and_gcs_on_revert():
    """Re-stamping a converged board changes nothing (idempotent rebuild); a
    room reverted to `source: sheetname` gets its stamp REMOVED, leaving no
    (component_classes ...) block behind."""
    bf = kicad.loads(kicad.pcb.PcbFile, _BOARD)
    pcb = bf.kicad_pcb
    assert generate_component_class_membership(pcb, _cc_plan("top.analog")) == 1
    assert generate_component_class_membership(pcb, _cc_plan("top.analog")) == 0
    assert _class_names(_fp(pcb, "top.analog")) == ["top.analog"]

    # revert: same module, sheetname-sourced room -> the stamp is ours, GC it
    assert (
        generate_component_class_membership(
            pcb, _cc_plan("top.analog", source="sheetname")
        )
        == 1
    )
    assert _fp(pcb, "top.analog").component_classes is None
    assert "(component_classes" not in kicad.dumps(bf)


@needs_d5
def test_membership_stamp_preserves_user_static_classes():
    """Union merge: a user's GUI-assigned static class (a name outside
    atopile's room-address namespace) survives stamping AND the later GC —
    ours is added next to it, never clobbering."""
    pcb = kicad.loads(kicad.pcb.PcbFile, _BOARD).kicad_pcb
    fp = _fp(pcb, "top.analog")
    fp.component_classes = kicad.pcb.FootprintComponentClasses(
        classes=[kicad.pcb.FootprintComponentClass(name="PWR")]
    )
    generate_component_class_membership(pcb, _cc_plan("top.analog"))
    assert _class_names(fp) == ["PWR", "top.analog"]
    # idempotent with the user class present
    assert generate_component_class_membership(pcb, _cc_plan("top.analog")) == 0
    assert _class_names(fp) == ["PWR", "top.analog"]
    # revert removes only ours
    generate_component_class_membership(pcb, _cc_plan("top.analog", source="sheetname"))
    assert _class_names(fp) == ["PWR"]


@needs_d5
def test_membership_stamp_gcs_ghost_of_restamped_room():
    """A footprint whose sheetname was re-stamped under a DIFFERENT room level
    (same atopile_address) does not keep the old room's class as a ghost: any
    class that is a dotted ancestor of the footprint's own address is
    atopile-owned and re-derived every build."""
    pcb = kicad.loads(kicad.pcb.PcbFile, _BOARD).kicad_pcb
    fp = _fp(pcb, "top.analog")
    generate_component_class_membership(pcb, _cc_plan("top.analog"))
    assert _class_names(fp) == ["top.analog"]
    # sync_rooms re-stamps the room one level up (address unchanged)
    fp.sheetname = "top"
    generate_component_class_membership(pcb, _cc_plan("top"))
    assert _class_names(fp) == ["top"]  # no ghost "top.analog"


@needs_d5
def test_membership_stamp_unknown_room_is_loud():
    """S5a: a component_class room matching no footprint sheetname raises
    (before any mutation) — stamping that classifies nothing is a silent
    half-product."""
    from faebryk.exporters.pcb.layout.layout_plan import LayoutPlanError

    pcb = kicad.loads(kicad.pcb.PcbFile, _BOARD).kicad_pcb
    with pytest.raises(LayoutPlanError, match="top.nope"):
        generate_component_class_membership(pcb, _cc_plan("top.nope"))
    assert all(fp.component_classes is None for fp in pcb.footprints)
