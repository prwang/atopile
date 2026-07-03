# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""D5 end-to-end proof (unit-level, synthetic board — no example rebuild).

One layout.yaml room with `source: component_class` flows through the SAME
chain `generate_layout_plan` runs (build_steps.py): load_layout_plan →
generate_rule_areas (board side: the `(component_class "X")` placement token)
AND generate_component_classes → C_kicad_project_file.dumps (project side: the
class declaration with its SHEET_NAME membership assignment). The two outputs
agree on the ONE room identity (class name == room.module == the C3-stamped
sheetname) — that agreement is the D5 contract, so it is asserted here across
BOTH files, not per-module.

The kicad-cli oracle for this pair (upgrade rc=0, token verbatim, DRC-neutral)
is test_rule_area_contract.py::test_kicad_ingests_component_class_rule_area.
"""

import json

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.layout_ir import layout_ir

try:
    from faebryk.exporters.pcb.layout.board_rules import generate_component_classes
    from faebryk.exporters.pcb.layout.layout_plan import load_layout_plan
    from faebryk.exporters.pcb.layout.rule_area import generate_rule_areas

    _D5_LANDED = True
except Exception:  # noqa: BLE001
    _D5_LANDED = False

    def _unlanded(*_a, **_k):
        raise RuntimeError("D5 not landed (S0 ratchet)")

    generate_component_classes = load_layout_plan = generate_rule_areas = _unlanded

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

    # board side: one zone per room, each with ITS room's source token
    zones = generate_rule_areas(pcb, plan, ir)
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
