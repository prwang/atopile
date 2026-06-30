# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F6-F8 contract — board feature authoring: pours, keepouts, silk.

These textualize the board-authoring concerns that were previously reuse-only:
  * F6 (F-fill)    — a copper POUR zone bound to a real net (bridge②).
  * F7 (F-keepout) — a non-placement KEEPOUT zone restricting copper/footprints.
  * F8 (F-silk)    — silkscreen TEXT.

All three are emitted into the .kicad_pcb by `generate_board_features`, idempotent
across rebuilds (managed pour/keepout zones carry a name prefix and are removed
before re-emit; managed silk is removed by exact (text, position, layer) match).
Empirically verified: a pour (fill+net), a keepout (restrictions), and a silk
gr_text all round-trip through `kicad-cli pcb upgrade` rc=0 (no SEGFAULT); the
keepout/pour carry NO ZonePlacement, so the source_type/source footgun is moot.

Loud-or-nothing: a pour whose net does not resolve through bridge② is a hard
error (a net-0 pour is silently GC'd by KiCad — meaningless).

S0 strict-xfail: gated on the emitter landing.
"""

import shutil

import pytest

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlanError, load_layout_plan
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.util import repo_root

try:
    from faebryk.exporters.pcb.layout.board_features import generate_board_features

    _F68_LANDED = True
except Exception:  # noqa: BLE001
    _F68_LANDED = False

    def generate_board_features(*a, **k):
        raise RuntimeError("F6-F8 board_features not landed")


needs_f68 = pytest.mark.xfail(
    not _F68_LANDED, reason="F6-F8 board_features not landed yet", strict=True
)

_BOARD = (
    repo_root() / "vendor" / "KiCadRoutingTools" / "kicad_files"
    / "lvds_converter_dualclk.kicad_pcb"
)
_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

# the board has a real /GND net; map an ato address to it for the pour.
_IR = {"signal_nets": {"top.gnd": "/GND"}}

_YAML = """\
board:
  stackup:
    layers:
      - {name: F.Cu, type: copper}
      - {name: core, type: dielectric, thickness: 1.5, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu, type: copper}
  pours:
    - net: top.gnd
      layer: F.Cu
      polygon: [[50, 50], [70, 50], [70, 70], [50, 70]]
  keepouts:
    - polygon: [[20, 20], [30, 20], [30, 30], [20, 30]]
      layers: [F.Cu, B.Cu]
  silk:
    - text: "REV A"
      at: [25, 15]
      size: 1.0
route_stages: []
"""


def _pcb():
    return kicad.loads(kicad.pcb.PcbFile, _BOARD.read_text()).kicad_pcb


@needs_f68
def test_pour_zone_is_emitted_with_real_net_and_fill():
    pcb = _pcb()
    gnd = next(n for n in pcb.nets if n.name == "/GND")
    n0 = len(pcb.zones)
    counts = generate_board_features(pcb, load_layout_plan(_YAML), _IR)
    assert counts["pours"] == 1
    pour = next(z for z in pcb.zones if z.name and z.name.startswith("fbrk_pour_"))
    assert pour.net == gnd.number  # bound to the REAL net (never net-0)
    assert pour.fill is not None and pour.fill.enable == kicad.pcb.E_zone_fill_enable.YES
    assert pour.layer == "F.Cu"
    assert len(pcb.zones) == n0 + counts["pours"] + counts["keepouts"]


@needs_f68
def test_keepout_zone_restricts_copper_and_carries_no_placement():
    pcb = _pcb()
    generate_board_features(pcb, load_layout_plan(_YAML), _IR)
    ko = next(z for z in pcb.zones if z.name and z.name.startswith("fbrk_keepout_"))
    assert ko.keepout is not None
    assert ko.keepout.tracks == kicad.pcb.E_zone_keepout.NOT_ALLOWED
    assert ko.keepout.vias == kicad.pcb.E_zone_keepout.NOT_ALLOWED
    assert ko.keepout.pads == kicad.pcb.E_zone_keepout.ALLOWED
    # NO placement (the SEGFAULT footgun is structurally avoided) and NO net.
    assert ko.placement is None
    assert ko.net == 0


@needs_f68
def test_silk_text_is_emitted():
    pcb = _pcb()
    n0 = len(pcb.gr_texts)
    counts = generate_board_features(pcb, load_layout_plan(_YAML), _IR)
    assert counts["silk"] == 1
    assert len(pcb.gr_texts) == n0 + 1
    txt = next(t for t in pcb.gr_texts if t.text == "REV A")
    assert txt.layer.layer == "F.SilkS"
    assert txt.effects.font.size.w == 1.0


@needs_f68
def test_pour_with_unresolvable_net_is_loud():
    pcb = _pcb()
    yaml = _YAML.replace("net: top.gnd", "net: top.NOPE")
    with pytest.raises(LayoutPlanError):
        generate_board_features(pcb, load_layout_plan(yaml), _IR)


@needs_f68
def test_reemit_is_idempotent():
    """Re-emitting onto an already-featured board does not accrete (managed pours/
    keepouts removed by name prefix; managed silk by exact match)."""
    pcb = _pcb()
    plan = load_layout_plan(_YAML)
    generate_board_features(pcb, plan, _IR)
    z1, t1 = len(pcb.zones), len(pcb.gr_texts)
    generate_board_features(pcb, plan, _IR)  # second emit
    assert len(pcb.zones) == z1
    assert len(pcb.gr_texts) == t1


@needs_f68
@pytest.mark.slow
@pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")
def test_features_round_trip_through_kicad_cli(tmp_path):
    """The proof the emitted geometry is valid: pour + keepout + silk survive
    kicad-cli pcb upgrade rc=0 (no SEGFAULT)."""
    import subprocess

    pf = kicad.loads(kicad.pcb.PcbFile, _BOARD.read_text())
    generate_board_features(pf.kicad_pcb, load_layout_plan(_YAML), _IR)
    out = tmp_path / "feat.kicad_pcb"
    kicad.dumps(pf, out)
    proc = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", str(out), "--force"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    text = out.read_text()
    assert "REV A" in text and "keepout" in text
