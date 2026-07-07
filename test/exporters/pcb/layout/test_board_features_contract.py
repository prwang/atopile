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
    """bridge② miss: the ato address is not in signal_nets at all."""
    pcb = _pcb()
    yaml = _YAML.replace("net: top.gnd", "net: top.NOPE")
    with pytest.raises(LayoutPlanError):
        generate_board_features(pcb, load_layout_plan(yaml), _IR)


@needs_f68
def test_pour_net_resolved_but_absent_from_board_is_loud():
    """The load-bearing F6 invariant: a pour MUST bind a REAL board net (a net-0
    pour is silently GC'd by KiCad). Here the ato address RESOLVES through bridge②
    but to a net name that is NOT on the board — `_net_number` must be loud, not
    fall through to net-0. (Mutating the raise to `return 0` re-introduces the
    GC footgun and must fail this test.)"""
    pcb = _pcb()
    ir = {"signal_nets": {"top.gnd": "/NET_NOT_ON_THIS_BOARD"}}
    with pytest.raises(LayoutPlanError):
        generate_board_features(pcb, load_layout_plan(_YAML), ir)


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


# ===========================================================================
# board.outline → Edge.Cuts (the outline AUTHORITY):
#   * a declared rect/polygon outline is drawn as a closed gr_line loop on
#     Edge.Cuts (was: validated but NEVER drawn — DRC "malformed outline");
#   * re-emit replaces (never accretes) existing Edge.Cuts lines;
#   * no declared outline ⇒ the board's own edges are untouched (reuse case).
# ===========================================================================
_OUTLINE_YAML = """\
board:
  outline:
    origin: [0, 0]
    size: [60, 70]
  stackup:
    layers:
      - {name: F.Cu, type: copper}
      - {name: core, type: dielectric, thickness: 1.5, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu, type: copper}
route_stages: []
"""


def _edge_lines(pcb):
    return [ln for ln in pcb.gr_lines if getattr(ln, "layer", None) == "Edge.Cuts"]


@needs_f68
def test_outline_is_drawn_as_closed_edge_cuts_loop():
    pcb = _pcb()
    plan = load_layout_plan(_OUTLINE_YAML)
    counts = generate_board_features(pcb, plan, _IR)
    assert counts["outline_edges"] == 4
    edges = _edge_lines(pcb)
    assert len(edges) == 4
    # closed rectangle over exactly the declared bounds
    pts = {(ln.start.x, ln.start.y) for ln in edges} | {
        (ln.end.x, ln.end.y) for ln in edges
    }
    assert pts == {(0.0, 0.0), (60.0, 0.0), (60.0, 70.0), (0.0, 70.0)}
    # each corner appears exactly once as a start and once as an end (a loop)
    starts = sorted((ln.start.x, ln.start.y) for ln in edges)
    ends = sorted((ln.end.x, ln.end.y) for ln in edges)
    assert starts == ends


@needs_f68
def test_outline_reemit_replaces_never_accretes():
    pcb = _pcb()
    plan = load_layout_plan(_OUTLINE_YAML)
    generate_board_features(pcb, plan, _IR)
    generate_board_features(pcb, plan, _IR)
    assert len(_edge_lines(pcb)) == 4


@needs_f68
def test_no_declared_outline_leaves_existing_edges_untouched():
    pcb = _pcb()
    before = len(_edge_lines(pcb))
    generate_board_features(pcb, load_layout_plan(_YAML), _IR)  # no outline key
    assert len(_edge_lines(pcb)) == before


# ===========================================================================
# board.stackup → setup.stackup (the PHYSICAL stackup AUTHORITY):
#   * the declared copper/dielectric stack replaces the board's electrical
#     stackup core (was: only the layer TABLE was derived from board.stackup —
#     the physical `(stackup ...)` section stayed the KiCad 2-layer default, so
#     the router's impedance mode computed geometry against a 1.51 mm core and
#     laid ~0.76 mm-wide "100Ω" pairs that shorted P to N; found live on F4);
#   * cosmetic outer entries (silk/paste/mask) and copper_finish are preserved
#     (declared stackups carry no cosmetic facts); a copper_finish is always
#     present after stamping — the router's stackup parser anchors on it;
#   * dielectrics are canonically renamed "dielectric N" (KiCad's own naming),
#     typed prepreg/core from the declared material;
#   * no declared stackup ⇒ the board's own section is untouched (reuse case);
#   * re-emit is idempotent (replace, never accrete).
# ===========================================================================
_STACKUP_YAML = """\
board:
  stackup:
    layers:
      - {name: F.Cu, type: copper, thickness: 0.017}
      - {name: p1, type: dielectric, thickness: 0.176, material: 7628 prepreg, epsilon_r: 4.6}
      - {name: In1.Cu, type: copper, thickness: 0.017}
      - {name: core, type: dielectric, thickness: 1.1, material: FR4 core, epsilon_r: 4.6}
      - {name: In2.Cu, type: copper, thickness: 0.017}
      - {name: p2, type: dielectric, thickness: 0.176, material: 7628 prepreg, epsilon_r: 4.6}
      - {name: B.Cu, type: copper, thickness: 0.017}
route_stages: []
"""


def _stackup_entries(pcb) -> list[tuple]:
    st = pcb.setup.stackup
    assert st is not None
    return [
        (
            layer.name,
            layer.type,
            layer.thickness.thickness if layer.thickness is not None else None,
            layer.material,
            layer.epsilon_r,
        )
        for layer in st.layers
    ]


@needs_f68
def test_stackup_is_stamped_into_setup_physical_section():
    pcb = _pcb()
    counts = generate_board_features(pcb, load_layout_plan(_STACKUP_YAML), _IR)
    assert counts["stackup_layers"] == 7
    entries = _stackup_entries(pcb)
    electrical = [e for e in entries if e[1] in ("copper", "prepreg", "core")]
    assert electrical == [
        ("F.Cu", "copper", 0.017, None, None),
        ("dielectric 1", "prepreg", 0.176, "7628 prepreg", 4.6),
        ("In1.Cu", "copper", 0.017, None, None),
        ("dielectric 2", "core", 1.1, "FR4 core", 4.6),
        ("In2.Cu", "copper", 0.017, None, None),
        ("dielectric 3", "prepreg", 0.176, "7628 prepreg", 4.6),
        ("B.Cu", "copper", 0.017, None, None),
    ]
    # cosmetic outer entries survive around the electrical core
    names = [e[0] for e in entries]
    assert names.index("F.Cu") > 0, names  # something (silk/mask) above top copper
    assert names.index("B.Cu") < len(names) - 1, names
    # the router's stackup parser anchors on copper_finish — must be present
    assert pcb.setup.stackup.copper_finish is not None


@needs_f68
def test_stamp_preserves_board_level_stackup_flags():
    """The KiCad stackup section carries board-level FAB flags beyond layers:
    dielectric_constraints / edge_connector / castellated_pads / edge_plating
    (pcb.pyi Stackup). A declared layout.yaml stackup says nothing about them,
    so stamping must pass them through like copper_finish — silently dropping
    a `(castellated_pads yes)` on the reuse flow loses fab intent (S5a)."""
    pcb = _pcb()
    st = pcb.setup.stackup
    assert st is not None, "fixture board must carry a stackup section"
    st.dielectric_constraints = True
    st.edge_connector = "bevelled"
    st.castellated_pads = True
    st.edge_plating = True
    generate_board_features(pcb, load_layout_plan(_STACKUP_YAML), _IR)
    st2 = pcb.setup.stackup
    assert st2.dielectric_constraints is True
    assert st2.edge_connector == "bevelled"
    assert st2.castellated_pads is True
    assert st2.edge_plating is True


@needs_f68
def test_stackup_reemit_is_idempotent():
    pcb = _pcb()
    plan = load_layout_plan(_STACKUP_YAML)
    generate_board_features(pcb, plan, _IR)
    first = _stackup_entries(pcb)
    generate_board_features(pcb, plan, _IR)
    assert _stackup_entries(pcb) == first


@needs_f68
def test_no_declared_stackup_leaves_physical_section_untouched():
    pcb = _pcb()
    before = _stackup_entries(pcb) if pcb.setup.stackup is not None else None
    yaml = "board:\n  silk:\n    - {text: X, at: [1, 1]}\nroute_stages: []\n"
    generate_board_features(pcb, load_layout_plan(yaml), _IR)
    after = _stackup_entries(pcb) if pcb.setup.stackup is not None else None
    assert after == before


@needs_f68
@pytest.mark.slow
@pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")
def test_stamped_stackup_round_trips_and_router_reads_it(tmp_path):
    """The consumer oracle, both directions: the stamped physical stackup
    survives `kicad-cli pcb upgrade` rc=0 AND the vendored router's own parser
    reads back the declared dielectric facts — the exact numbers its impedance
    mode uses. (This is the contract whose absence produced the 0.76 mm 'wide
    100Ω pair' short: the router silently computed against the default core.)"""
    import json
    import subprocess

    from faebryk.exporters.pcb.layout.layout_plan_runner import _system_python3

    pf = kicad.loads(kicad.pcb.PcbFile, _BOARD.read_text())
    generate_board_features(pf.kicad_pcb, load_layout_plan(_STACKUP_YAML), _IR)
    out = tmp_path / "stack.kicad_pcb"
    kicad.dumps(pf, out)

    proc = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", str(out), "--force"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr

    router_root = repo_root() / "vendor" / "KiCadRoutingTools"
    probe = subprocess.run(
        [
            str(_system_python3()), "-c",
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "import json\n"
            "from kicad_parser import parse_kicad_pcb\n"
            "pcb = parse_kicad_pcb(sys.argv[2])\n"
            "print('JSON_OUT:' + json.dumps([\n"
            "    [layer.name, layer.layer_type, layer.thickness, layer.epsilon_r]\n"
            "    for layer in pcb.board_info.stackup\n"
            "    if layer.layer_type in ('copper', 'prepreg', 'core')\n"
            "]))",
            str(router_root), str(out),
        ],
        capture_output=True, text=True, cwd=str(router_root), timeout=120,
    )
    if probe.returncode != 0:
        pytest.skip(f"system python3 cannot run the router parser: {probe.stderr[-300:]}")
    line = next(
        line for line in probe.stdout.splitlines() if line.startswith("JSON_OUT:")
    )
    got = json.loads(line.removeprefix("JSON_OUT:"))
    assert ["dielectric 1", "prepreg", 0.176, 4.6] in got, got
    assert ["dielectric 2", "core", 1.1, 4.6] in got, got
    assert [g for g in got if g[1] == "copper"] == [
        ["F.Cu", "copper", 0.017, 0.0],
        ["In1.Cu", "copper", 0.017, 0.0],
        ["In2.Cu", "copper", 0.017, 0.0],
        ["B.Cu", "copper", 0.017, 0.0],
    ], got
