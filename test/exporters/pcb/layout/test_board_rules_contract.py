# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F5 contract — F-drc-rules: board net-class authoring → .kicad_pro.

F5 textualizes the board-level DRC intent (clearance, track-width class, via
class, diff-pair geometry) so KiCad's DRC judges DESIGN INTENT instead of
silently eating its defaults. The authored `board.net_classes` are emitted into
the project file's `net_settings` (classes + per-net assignment patterns), each
net resolved ato-address → kicad-net-name through bridge② (like everywhere else).

Empirically verified separately: kicad-cli `pcb drc` HONORS these classes (a 5 mm
clearance class turns 0 clearance violations into 36), rc=0 (no SEGFAULT). This
file pins the pure emitter: the classes + assignments are correct, unresolvable
nets are loud, and an existing project is merged (not clobbered).

D5 lives in the same module: `generate_component_classes` authors
`component_class_settings` assignments (one SHEET_NAME condition per
`source: component_class` room, class name == room.module) into the SAME
project file, merge-preserving. The D5 tests here pin the emitter + merge; the
kicad-cli ingest of board+project together is pinned in
test_rule_area_contract.py::test_kicad_ingests_component_class_rule_area.

S0 strict-xfail: gated on the emitter landing.
"""

import shutil

import pytest

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlanError, load_layout_plan

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

try:
    from faebryk.exporters.pcb.layout.board_rules import generate_project_rules

    _F5_LANDED = True
except Exception:  # noqa: BLE001
    _F5_LANDED = False

    def generate_project_rules(*a, **k):
        raise RuntimeError("F5 board_rules not landed")


needs_f5 = pytest.mark.xfail(
    not _F5_LANDED, reason="F5 board_rules not landed yet", strict=True
)

try:
    from faebryk.exporters.pcb.layout.board_rules import generate_component_classes

    _D5_LANDED = True
except Exception:  # noqa: BLE001
    _D5_LANDED = False

    def generate_component_classes(*a, **k):
        raise RuntimeError("D5 generate_component_classes not landed")


needs_d5 = pytest.mark.xfail(
    not _D5_LANDED, reason="D5 component-class authoring not landed yet", strict=True
)

_YAML = """\
board:
  stackup:
    layers:
      - {name: F.Cu, type: copper}
      - {name: core, type: dielectric, thickness: 1.5, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu, type: copper}
  net_classes:
    - name: HV
      clearance: 0.5
      track_width: 0.4
      via_diameter: 0.8
      via_drill: 0.4
      nets:
        - top.pwr
        - top.gnd
route_stages: []
"""

_IR = {
    "layout_ir_version": 2, "components": {}, "nets": {}, "rooms": {},
    "signal_nets": {"top.pwr": "/PWR", "top.gnd": "/GND", "top.sig": "/SIG"},
}


@needs_f5
def test_net_classes_emit_into_project():
    plan = load_layout_plan(_YAML)
    proj = generate_project_rules(plan, _IR)
    classes = {c.name: c for c in proj.net_settings.classes}
    assert "Default" in classes  # KiCad requires a Default class
    assert "HV" in classes
    hv = classes["HV"]
    assert hv.clearance == 0.5 and hv.track_width == 0.4
    assert hv.via_diameter == 0.8 and hv.via_drill == 0.4
    # nets assigned by netclass_pattern, resolved to KICAD net names (bridge②),
    # not the ato addresses.
    pats = {(p.netclass, p.pattern) for p in proj.net_settings.netclass_patterns}
    assert ("HV", "/PWR") in pats and ("HV", "/GND") in pats
    assert all(p.pattern not in ("top.pwr", "top.gnd")
               for p in proj.net_settings.netclass_patterns)


@needs_f5
def test_unresolvable_net_in_class_is_loud():
    yaml = _YAML.replace("- top.gnd", "- top.NOPE")
    plan = load_layout_plan(yaml)
    with pytest.raises(LayoutPlanError):
        generate_project_rules(plan, _IR)


@needs_f5
def test_no_net_classes_returns_none():
    """A plan with no authored net classes writes NO project file (None) — KiCad
    defaults stand, and that is the explicit, non-erroring outcome."""
    plan = load_layout_plan(
        "board:\n  stackup:\n    layers:\n"
        "      - {name: F.Cu, type: copper}\n"
        "      - {name: core, type: dielectric, thickness: 1.5, material: FR4, epsilon_r: 4.5}\n"
        "      - {name: B.Cu, type: copper}\nroute_stages: []\n"
    )
    assert generate_project_rules(plan, _IR) is None


@needs_f5
def test_existing_project_is_merged_not_clobbered():
    """Emitting onto an existing project preserves its other settings (only
    net_settings is authored)."""
    from faebryk.libs.kicad.other_fileformats import C_kicad_project_file

    base = C_kicad_project_file()
    base.pcbnew.page_layout_descr_file = "my_frame.kicad_wks"  # a pre-existing value
    plan = load_layout_plan(_YAML)
    proj = generate_project_rules(plan, _IR, base_project=base)
    assert proj.pcbnew.page_layout_descr_file == "my_frame.kicad_wks"
    assert any(c.name == "HV" for c in proj.net_settings.classes)


def test_duplicate_class_name_is_loud_at_parse():
    yaml = _YAML.replace(
        "    - name: HV",
        "    - name: HV\n      nets: [top.sig]\n    - name: HV",
    )
    with pytest.raises(Exception):
        load_layout_plan(yaml)


def test_net_in_two_classes_is_loud_at_parse():
    yaml = _YAML.replace(
        "route_stages: []",
        "    - name: LV\n      nets:\n        - top.pwr\nroute_stages: []",
    )
    with pytest.raises(Exception):
        load_layout_plan(yaml)


@needs_f5
@pytest.mark.slow
@pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")
def test_kicad_cli_drc_honors_emitter_output(tmp_path):
    """The proof F-drc-rules WORKS end-to-end through the EMITTER UNDER TEST:
    `generate_project_rules` builds a project authoring a 5 mm clearance class
    over every board net; written next to the board, `kicad-cli pcb drc` flags
    clearance violations it otherwise would not (rc=0, no SEGFAULT). This pins the
    real chain (emitter → .kicad_pro → kicad-cli) — a schema/emit regression in
    generate_project_rules WILL break it (unlike a hand-built project would)."""
    import json
    import subprocess
    from pathlib import Path

    from faebryk.exporters.pcb.layout.layout_plan import (
        Board, LayoutPlan, NetClass,
    )
    from faebryk.libs.kicad.fileformats import kicad
    from faebryk.libs.util import repo_root

    src = (
        repo_root() / "vendor" / "KiCadRoutingTools" / "kicad_files"
        / "lvds_converter_dualclk.kicad_pcb"
    )
    if not src.exists():
        pytest.skip("router board fixture absent")
    board = tmp_path / "b.kicad_pcb"
    board.write_text(src.read_text())

    # map every real board net to a synthetic ato address, assign them all to a
    # WIDE 5 mm clearance class — and emit the project THROUGH generate_project_rules.
    pcb = kicad.loads(kicad.pcb.PcbFile, board.read_text()).kicad_pcb
    net_names = sorted({n.name for n in pcb.nets if n.name})
    ir = {"signal_nets": {f"top.n{i}": name for i, name in enumerate(net_names)}}
    plan = LayoutPlan(
        board=Board(
            net_classes=[
                NetClass(name="WIDE", clearance=5.0, nets=list(ir["signal_nets"]))
            ]
        )
    )
    proj = generate_project_rules(plan, ir)
    assert proj is not None

    def _clearance_count(write_project: bool) -> int:
        if write_project:
            proj.dumps(tmp_path / "b.kicad_pro")
        elif (tmp_path / "b.kicad_pro").exists():
            (tmp_path / "b.kicad_pro").unlink()
        out = tmp_path / "drc.json"
        subprocess.run(
            ["kicad-cli", "pcb", "drc", str(board), "--format", "json",
             "-o", str(out), "--severity-all"],
            check=True, capture_output=True, text=True,
        )
        viols = json.loads(out.read_text()).get("violations", [])
        return sum(1 for v in viols if v.get("type") == "clearance")

    with_rules = _clearance_count(True)
    without_rules = _clearance_count(False)
    assert with_rules > without_rules, (with_rules, without_rules)


# ===========================================================================
# D5 — component-class declarations → .kicad_pro component_class_settings.
# generate_component_classes authors ONE SHEET_NAME assignment per
# `source: component_class` room (class name == room.module == the C3-stamped
# sheetname); merge-preserving at both levels (other project sections AND
# foreign assignments survive). JSON shape SSOT: KiCad
# common/project/component_class_settings.cpp.
# ===========================================================================
def _cc_plan(rooms):
    from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, Room

    return LayoutPlan(
        rooms=[Room(**r) for r in rooms], route_stages=[]
    )


@needs_d5
def test_component_class_rooms_emit_assignments():
    """Only component_class-sourced rooms are declared; the assignment shape is
    exactly KiCad's: conditions_operator ALL + one SHEET_NAME condition whose
    primary is the room module."""
    import json

    plan = _cc_plan(
        [
            {"module": "top.r1", "source": "component_class"},
            {"module": "top.r2"},  # sheetname room: NOT declared as a class
        ]
    )
    proj = generate_component_classes(plan)
    assert proj is not None
    settings = proj.component_class_settings
    assert settings.meta.version == 0
    assert settings.sheet_component_classes.enabled is False
    assert [a.component_class for a in settings.assignments] == ["top.r1"]
    a = settings.assignments[0]
    assert a.conditions_operator == "ALL"
    assert set(a.conditions) == {"SHEET_NAME"}
    assert a.conditions["SHEET_NAME"].primary == "top.r1"

    # serialized shape: KiCad's loader does contains("secondary") then
    # get<string>() — an absent secondary must be OMITTED, never null.
    dumped = json.loads(proj.dumps())["component_class_settings"]
    cond = dumped["assignments"][0]["conditions"]["SHEET_NAME"]
    assert cond == {"primary": "top.r1"}


@needs_d5
def test_no_component_class_rooms_writes_nothing():
    """A plan whose rooms are all sheetname-sourced authors NO project file
    (None) — the explicit, non-erroring outcome (mirrors F5's no-classes pin)."""
    assert generate_component_classes(_cc_plan([{"module": "top.r1"}])) is None
    assert generate_component_classes(_cc_plan([])) is None


@needs_d5
def test_component_classes_merge_not_clobber():
    """Emitting onto an existing project preserves (a) every other project
    section, (b) foreign (user-authored) class assignments in their original
    position; a STALE atopile-owned assignment for the same class is replaced,
    not duplicated (idempotent re-emit)."""
    from faebryk.libs.kicad.other_fileformats import C_kicad_project_file

    _CC = C_kicad_project_file.C_component_class_settings
    base = C_kicad_project_file()
    base.pcbnew.page_layout_descr_file = "my_frame.kicad_wks"
    base.component_class_settings.assignments = [
        _CC.C_assignment(
            component_class="USER_CLASS",
            conditions_operator="ANY",
            conditions={"REFERENCE": _CC.C_condition(primary="R1,R2")},
        ),
        _CC.C_assignment(  # stale artifact of a previous emit — must be replaced
            component_class="top.r1",
            conditions_operator="ALL",
            conditions={"SHEET_NAME": _CC.C_condition(primary="top.STALE")},
        ),
    ]

    plan = _cc_plan([{"module": "top.r1", "source": "component_class"}])
    proj = generate_component_classes(plan, base_project=base)
    assert proj is not None
    assert proj.pcbnew.page_layout_descr_file == "my_frame.kicad_wks"
    assignments = proj.component_class_settings.assignments
    assert [a.component_class for a in assignments] == ["USER_CLASS", "top.r1"]
    # the user assignment is untouched, the owned one is refreshed
    assert assignments[0].conditions["REFERENCE"].primary == "R1,R2"
    assert assignments[1].conditions["SHEET_NAME"].primary == "top.r1"


@needs_d5
def test_stale_owned_assignment_gc_on_room_rename():
    """A renamed component_class room leaves NO ghost assignment: atopile's own
    emissions are recognized by their structural fingerprint (ALL + single
    SHEET_NAME whose primary == class name, no secondary) and dropped on
    re-emit, while a user assignment that does NOT match the fingerprint
    survives — even one for a class name atopile no longer owns."""
    from faebryk.libs.kicad.other_fileformats import C_kicad_project_file

    _CC = C_kicad_project_file.C_component_class_settings
    p1 = generate_component_classes(
        _cc_plan(
            [
                {"module": "top.a", "source": "component_class"},
                {"module": "top.b", "source": "component_class"},
            ]
        )
    )
    assert p1 is not None
    # the exact round trip build_steps performs with the on-disk .kicad_pro
    base = C_kicad_project_file.loads(p1.dumps())
    # a user assignment for a stale-looking name but with a FOREIGN shape
    # (ANY + REFERENCE) — not our fingerprint, must survive the GC.
    base.component_class_settings.assignments.append(
        _CC.C_assignment(
            component_class="top.a",
            conditions_operator="ANY",
            conditions={"REFERENCE": _CC.C_condition(primary="R1")},
        )
    )
    p2 = generate_component_classes(
        _cc_plan([{"module": "top.renamed", "source": "component_class"}]),
        base_project=base,
    )
    assert p2 is not None
    assignments = p2.component_class_settings.assignments
    assert [a.component_class for a in assignments] == ["top.a", "top.renamed"]
    # the survivor is the user's REFERENCE one, not our stale fingerprint
    assert set(assignments[0].conditions) == {"REFERENCE"}


@needs_d5
def test_zero_cc_rooms_prunes_stale_assignments():
    """A plan whose rooms all reverted to `source: sheetname` still cleans the
    project: atopile-fingerprint assignments from a previous build are pruned
    (the function returns the cleaned project so the caller writes it), user
    assignments survive, and a base with nothing stale still returns None."""
    from faebryk.libs.kicad.other_fileformats import C_kicad_project_file

    _CC = C_kicad_project_file.C_component_class_settings
    p1 = generate_component_classes(
        _cc_plan([{"module": "top.a", "source": "component_class"}])
    )
    assert p1 is not None
    base = C_kicad_project_file.loads(p1.dumps())
    base.component_class_settings.assignments.append(
        _CC.C_assignment(
            component_class="USER_CLASS",
            conditions_operator="ANY",
            conditions={"REFERENCE": _CC.C_condition(primary="R1,R2")},
        )
    )
    # same module, but the room is now sheetname-sourced -> prune, keep user
    pruned = generate_component_classes(
        _cc_plan([{"module": "top.a"}]), base_project=base
    )
    assert pruned is not None
    assert [
        a.component_class for a in pruned.component_class_settings.assignments
    ] == ["USER_CLASS"]

    # nothing stale, nothing owned -> the honest None (no gratuitous write)
    clean = C_kicad_project_file()
    clean.component_class_settings.assignments = [
        _CC.C_assignment(
            component_class="USER_CLASS",
            conditions_operator="ANY",
            conditions={"REFERENCE": _CC.C_condition(primary="R1,R2")},
        )
    ]
    assert generate_component_classes(_cc_plan([]), base_project=clean) is None
    assert (
        clean.component_class_settings.assignments[0].component_class
        == "USER_CLASS"
    )


@needs_d5
def test_component_classes_compose_with_net_class_rules():
    """The build-step chain (F5 then D5 onto ONE project object) yields a single
    project carrying BOTH sections — net classes and class assignments."""
    plan = load_layout_plan(
        _YAML.replace(
            "route_stages: []",
            "rooms:\n  - module: top.r1\n    source: component_class\n"
            "route_stages: []",
        )
    )
    proj = generate_project_rules(plan, _IR)
    assert proj is not None
    proj = generate_component_classes(plan, base_project=proj)
    assert proj is not None
    assert any(c.name == "HV" for c in proj.net_settings.classes)
    assert [
        a.component_class for a in proj.component_class_settings.assignments
    ] == ["top.r1"]
