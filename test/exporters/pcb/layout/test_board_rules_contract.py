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
def test_kicad_cli_drc_honors_emitted_net_class(tmp_path):
    """The proof that F-drc-rules WORKS: a project authoring a huge Default
    clearance, written next to a board, makes `kicad-cli pcb drc` flag clearance
    violations it otherwise would not (rc=0, no SEGFAULT). Pins the empirically-
    verified round-trip so a schema/emit regression cannot silently break it."""
    import json
    import subprocess
    from pathlib import Path

    from faebryk.libs.kicad.other_fileformats import C_kicad_project_file
    from faebryk.libs.util import repo_root

    src = (
        repo_root() / "vendor" / "KiCadRoutingTools" / "kicad_files"
        / "lvds_converter_dualclk.kicad_pcb"
    )
    if not src.exists():
        pytest.skip("router board fixture absent")
    board = tmp_path / "b.kicad_pcb"
    board.write_text(src.read_text())

    def _clearance_count(proj: C_kicad_project_file | None) -> int:
        if proj is not None:
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

    # a 5 mm Default clearance is absurdly tight → many clearance violations.
    proj = C_kicad_project_file()
    proj.net_settings.classes = [
        C_kicad_project_file.C_net_settings.C_classes(name="Default", clearance=5.0)
    ]
    with_rules = _clearance_count(proj)
    without_rules = _clearance_count(None)
    assert with_rules > without_rules, (with_rules, without_rules)
