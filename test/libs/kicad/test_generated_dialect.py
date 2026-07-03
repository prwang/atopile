# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
BACKLOG §G — (generated ...) tuning-pattern fidelity + component-class fields.

Grammar ground truth = KiCad 10.0.3:

- generated (PCB_GENERATOR, parser parseGENERATOR :7023, writer :2507-2602):
  envelope uuid/type/name/layer/locked/members + an open key->value property
  map. HARD parser requirement: uuid is the FIRST subkey. The writer emits
  the property map in ALPHABETICAL key order (STRING_ANY_MAP is sorted) with
  exactly 5 value shapes — f64, yes/no, quoted string, (key (xy x y)),
  (key (pts ...)) — then the sorted quoted member uuids LAST. 'type' is a raw
  unquoted symbol. Property set = pcb_tuning_pattern.cpp GetProperties
  :1691-1732 (28 keys incl. base_line/base_line_coupled/origin/end).
  tuning_mode in {single, diff_pair, diff_pair_skew}.
- zone placement component_class source: (placement (enabled ...)
  (component_class "X")) — the file grammar fuses source type+value into one
  token (parser :8120-8169); verified kicad-cli-safe, unlike the SIGSEGVing
  source_type/source fields (CLAUDE.md hazard, test_rule_area_contract.py).
- footprint static classes: (component_classes (class "X") ...)
  (parser :5597-5620, writer :1248-1259).

Corpus fixtures (auto-discovered by test_fileformats_corpus.py):
tuning_generators_load_save (real KiCad QA board, single mode),
tuning_diffpair_synth (diff_pair + base_line_coupled). The diff_pair_skew
variant is authored here and validated through the kicad-cli oracle.

Future/unknown generator property keys stay on the loud S5a unknown-key sink
(the correct residual for an open map); this file pins that too. Lifecycle
(ghost patterns, member deletion) is layout_sync policy, NOT this layer's:
the fidelity layer round-trips a generated verbatim even with empty members.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.test import sexp_tree
from faebryk.libs.test.fileformats import FILEFORMATS_PATH

V10_PCB_DIR = FILEFORMATS_PATH / "v10" / "pcb"
SINGLE_FIXTURE = V10_PCB_DIR / "tuning_generators_load_save.kicad_pcb"
DIFFPAIR_FIXTURE = V10_PCB_DIR / "tuning_diffpair_synth.kicad_pcb"

NEEDS_KICAD_CLI = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)

# The full v10 tuning_pattern property key set (pcb_tuning_pattern.cpp
# GetProperties + origin from pcb_generator.cpp). update_order is
# #ifdef-gated out of release KiCad and deliberately unmodeled.
TUNING_PROPERTY_KEYS = [
    "base_line",
    "base_line_coupled",
    "corner_radius_percent",
    "end",
    "initial_side",
    "is_time_domain",
    "last_diff_pair_gap",
    "last_netname",
    "last_status",
    "last_track_width",
    "last_tuning_length",
    "max_amplitude",
    "min_amplitude",
    "min_spacing",
    "origin",
    "override_custom_rules",
    "rounded",
    "single_sided",
    "target_delay",
    "target_delay_max",
    "target_delay_min",
    "target_length",
    "target_length_max",
    "target_length_min",
    "target_skew",
    "target_skew_max",
    "target_skew_min",
    "tuning_mode",
]


def _skew_board() -> str:
    """The third tuning_mode: authored from the validated diff_pair fixture
    (KiCad's skew pattern carries the identical key set; only the mode enum
    string differs — tuningToString: single|diff_pair|diff_pair_skew)."""
    return DIFFPAIR_FIXTURE.read_text().replace(
        '(tuning_mode "diff_pair")', '(tuning_mode "diff_pair_skew")'
    )


def _generated_block(text: str) -> str:
    m = re.search(r"^\t\(generated\n(.*?)^\t\)$", text, re.S | re.M)
    assert m is not None, "no (generated ...) block found"
    return m.group(0)


def _top_level_keys(block: str) -> list[str]:
    """Immediate subkeys of the generated block, in emission order."""
    return re.findall(r"^\t\t\(([a-z_]+)[ \n]", block, re.M)


# ---------------------------------------------------------------------------
# round-trip: all three tuning modes
# ---------------------------------------------------------------------------


def test_single_mode_fixture_loads_schema_complete():
    """The real KiCad QA serpentine: strict_unknown load must produce ZERO
    Generated:* unknown keys (schema completeness — before this landed every
    tuning property key hit the S5a sink)."""
    pcb = kicad.loads(
        kicad.pcb.PcbFile, SINGLE_FIXTURE.read_text(), strict_unknown=True
    ).kicad_pcb
    assert not [k for k in kicad.last_unknown_keys if k.startswith("Generated")]
    g = pcb.generateds[0]
    assert g.type == "tuning_pattern"
    assert g.tuning_mode == "single"
    assert g.name == "Tuning Pattern"
    assert g.layer == "F.Cu"
    # value shapes: f64, bool, quoted string, (xy), (pts)
    assert g.target_length == 100.0
    assert g.target_skew_min == -0.1
    assert g.is_time_domain is False
    assert g.rounded is True
    assert g.initial_side == "left"
    assert g.last_status == "too_short"
    assert g.last_netname == ""
    assert (g.origin.xy.x, g.origin.xy.y) == (115.571487, 50.14)
    assert (g.end.xy.x, g.end.xy.y) == (123.13735, 50.14)
    assert [(p.x, p.y) for p in g.base_line.pts.xys] == [
        (115.571487, 50.14),
        (123.13735, 50.14),
    ]
    assert g.base_line_coupled is None  # single mode has no coupled line
    assert len(g.members) == 47
    assert all(re.fullmatch(r"[0-9a-f-]{36}", m) for m in g.members)


def test_diffpair_fixture_loads_schema_complete():
    pcb = kicad.loads(
        kicad.pcb.PcbFile, DIFFPAIR_FIXTURE.read_text(), strict_unknown=True
    ).kicad_pcb
    g = pcb.generateds[0]
    assert g.tuning_mode == "diff_pair"
    assert g.last_diff_pair_gap == 0.18
    assert [(p.x, p.y) for p in g.base_line_coupled.pts.xys] == [
        (115.571487, 50.64),
        (123.13735, 50.64),
    ]


def test_skew_mode_roundtrips():
    board = _skew_board()
    pcb = kicad.loads(kicad.pcb.PcbFile, board, strict_unknown=True).kicad_pcb
    assert pcb.generateds[0].tuning_mode == "diff_pair_skew"
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, board))
    assert not sexp_tree.data_loss(board, out)
    assert '(tuning_mode "diff_pair_skew")' in out


@pytest.mark.parametrize(
    "fixture", [SINGLE_FIXTURE, DIFFPAIR_FIXTURE], ids=lambda p: p.stem
)
def test_rewrite_loses_nothing(fixture: Path):
    """loads()->dumps() drops no sexp node from the whole board (gate 3 of
    the corpus, pinned here per-construct so a schema regression names the
    tuning fixture directly)."""
    raw = fixture.read_text()
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))
    loss = sexp_tree.data_loss(raw, out)
    assert not loss, sexp_tree.summarize_loss(loss)
    # dumps -> loads equivalence on every modeled property
    g1 = kicad.loads(kicad.pcb.PcbFile, raw).kicad_pcb.generateds[0]
    g2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.generateds[0]
    for key in TUNING_PROPERTY_KEYS:
        v1, v2 = getattr(g1, key), getattr(g2, key)
        if key in ("origin", "end"):
            v1 = None if v1 is None else (v1.xy.x, v1.xy.y)
            v2 = None if v2 is None else (v2.xy.x, v2.xy.y)
        elif key.startswith("base_line"):
            v1 = None if v1 is None else [(p.x, p.y) for p in v1.pts.xys]
            v2 = None if v2 is None else [(p.x, p.y) for p in v2.pts.xys]
        assert v1 == v2, key
    assert list(g1.members) == list(g2.members)


# ---------------------------------------------------------------------------
# emission shape: uuid first, type unquoted, properties alphabetical,
# members quoted last
# ---------------------------------------------------------------------------


def test_emission_shape():
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, DIFFPAIR_FIXTURE.read_text()))
    block = _generated_block(out)
    keys = _top_level_keys(block)
    # KiCad's parser hard-requires uuid as the FIRST subkey (parseGENERATOR)
    assert keys[0] == "uuid"
    # envelope order matches the 10.0.3 writer
    assert keys[1:4] == ["type", "name", "layer"]
    # members come last, quoted
    assert keys[-1] == "members"
    assert re.search(r'\(members "[0-9a-f-]{36}"', block)
    # 'type' is a raw unquoted symbol
    assert "(type tuning_pattern)" in block
    # the property map is emitted in alphabetical key order (KiCad writes a
    # sorted map; our field declaration order must keep matching it)
    props = keys[4:-1]
    assert props == sorted(props)
    assert props == TUNING_PROPERTY_KEYS  # diff_pair fixture carries all 28


def test_empty_members_generated_is_kept_verbatim():
    """The fidelity layer must NOT adopt KiCad's ghost-dropping lifecycle
    rule (that policy belongs to layout_sync): a generated with no members
    round-trips verbatim, loudly-nothing dropped."""
    raw = DIFFPAIR_FIXTURE.read_text()
    block = _generated_block(raw)
    gutted = raw.replace(
        block, re.sub(r"\(members[^)]*\)", "", block, flags=re.S), 1
    )
    pcb = kicad.loads(kicad.pcb.PcbFile, gutted).kicad_pcb
    assert list(pcb.generateds[0].members) == []
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, gutted))
    g2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.generateds[0]
    assert g2.tuning_mode == "diff_pair"
    assert g2.target_length == 100.0


def test_future_generator_property_stays_loud():
    """S5a backstop: the generated property map is open-ended in KiCad; a key
    we do not model must be reported, never silently dropped."""
    raw = SINGLE_FIXTURE.read_text().replace(
        '(tuning_mode "single")',
        '(tuning_mode "single")\n\t\t(v11_only_generator_prop 42)',
    )
    kicad.loads(kicad.pcb.PcbFile, raw)
    assert any("v11_only_generator_prop" in k for k in kicad.last_unknown_keys), (
        kicad.last_unknown_keys
    )


# ---------------------------------------------------------------------------
# kicad-cli oracles
# ---------------------------------------------------------------------------


def _drc_ok(board: Path, tmp_path: Path) -> None:
    report = tmp_path / f"{board.stem}.drc.json"
    proc = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(report), str(board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"kicad-cli cannot read {board.name}:\n{proc.stderr}"
    json.loads(report.read_text())


@pytest.mark.parametrize(
    "fixture", [SINGLE_FIXTURE, DIFFPAIR_FIXTURE], ids=lambda p: p.stem
)
@NEEDS_KICAD_CLI
def test_fixture_rewrite_is_kicad_readable(fixture: Path, tmp_path: Path):
    """DRC rc==0 both as-committed and after our loads()->dumps() rewrite."""
    _drc_ok(fixture, tmp_path)
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, fixture.read_text()))
    rewritten = tmp_path / "rewrite.kicad_pcb"
    rewritten.write_text(out)
    _drc_ok(rewritten, tmp_path)


@NEEDS_KICAD_CLI
def test_skew_variant_is_kicad_legal(tmp_path: Path):
    """The authored diff_pair_skew board is real-KiCad-valid: kicad-cli
    round-trips it preserving the mode, and can DRC our rewrite of it."""
    board = tmp_path / "skew.kicad_pcb"
    board.write_text(_skew_board())
    proc = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", "--force", str(board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert '(tuning_mode "diff_pair_skew")' in board.read_text()
    rewritten = tmp_path / "skew_rewrite.kicad_pcb"
    rewritten.write_text(kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _skew_board())))
    _drc_ok(rewritten, tmp_path)


@NEEDS_KICAD_CLI
def test_property_order_matches_kicad_resave(tmp_path: Path):
    """Byte-shape sanity (NOT byte equality — uuid-opaque rule): the generated
    subkey sequence our writer emits equals the sequence KiCad 10.0.3 itself
    writes when it resaves the same board."""
    board = tmp_path / "resave.kicad_pcb"
    shutil.copyfile(DIFFPAIR_FIXTURE, board)
    proc = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", "--force", str(board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    theirs = _top_level_keys(_generated_block(board.read_text()))
    ours = _top_level_keys(
        _generated_block(
            kicad.dumps(kicad.loads(kicad.pcb.PcbFile, DIFFPAIR_FIXTURE.read_text()))
        )
    )
    assert ours == theirs


# ---------------------------------------------------------------------------
# component-class fidelity fields (zig only; python plumbing is D5's job)
# ---------------------------------------------------------------------------


def _board(body: str) -> str:
    return (
        '(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n'
        '\t(generator_version "10.0")\n\t(general (thickness 1.6))\n'
        f"{body})\n"
    )


_COMPONENT_CLASS_BOARD = _board(
    '\t(footprint "test:FP"\n\t\t(layer "F.Cu")\n'
    '\t\t(uuid "00000000-0000-0000-0000-000000000001")\n\t\t(at 10 10)\n'
    '\t\t(component_classes (class "PSU") (class "RF"))\n'
    '\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n\t)\n'
    '\t(zone\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(name "room")\n'
    "\t\t(hatch edge 0.5)\n"
    "\t\t(keepout (tracks allowed) (vias allowed) (pads allowed) "
    "(copperpour allowed) (footprints allowed))\n"
    '\t\t(placement (enabled yes) (component_class "PSU"))\n'
    "\t\t(polygon (pts (xy 0 0) (xy 20 0) (xy 20 20) (xy 0 20)))\n\t)\n"
)


def test_zone_placement_component_class_roundtrips():
    pcb = kicad.loads(
        kicad.pcb.PcbFile, _COMPONENT_CLASS_BOARD, strict_unknown=True
    ).kicad_pcb
    pl = pcb.zones[0].placement
    assert pl.enabled is True
    assert pl.component_class == "PSU"
    assert pl.sheetname is None
    # the SIGSEGV-hazard in-memory fields must never materialize from a file
    assert pl.source_type is None and pl.source is None

    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _COMPONENT_CLASS_BOARD))
    assert not sexp_tree.data_loss(_COMPONENT_CLASS_BOARD, out)
    flat = re.sub(r"\s+", " ", out)
    assert '(component_class "PSU")' in flat
    # regression guard (kicad-cli SIGSEGV): never write source_type/source
    assert "source_type" not in out
    assert not re.search(r"\(source[ )]", out)
    pl2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.zones[0].placement
    assert pl2.component_class == "PSU"


def test_footprint_component_classes_roundtrip():
    pcb = kicad.loads(
        kicad.pcb.PcbFile, _COMPONENT_CLASS_BOARD, strict_unknown=True
    ).kicad_pcb
    cc = pcb.footprints[0].component_classes
    assert cc is not None
    assert [c.name for c in cc.classes] == ["PSU", "RF"]

    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _COMPONENT_CLASS_BOARD))
    flat = re.sub(r"\s+", " ", out)
    assert re.search(r'\(component_classes \(class "PSU"\) \(class "RF"\) ?\)', flat)
    cc2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.footprints[0].component_classes
    assert [c.name for c in cc2.classes] == ["PSU", "RF"]


def test_absent_component_classes_stay_absent():
    plain = _board(
        '\t(footprint "test:FP"\n\t\t(layer "F.Cu")\n'
        '\t\t(uuid "00000000-0000-0000-0000-000000000002")\n\t\t(at 10 10)\n'
        '\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n\t)\n'
    )
    pcb = kicad.loads(kicad.pcb.PcbFile, plain).kicad_pcb
    assert pcb.footprints[0].component_classes is None
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, plain))
    assert "component_classes" not in out


@NEEDS_KICAD_CLI
def test_component_class_rewrite_is_kicad_readable(tmp_path: Path):
    """kicad-cli oracle for both component-class channels: DRC reads our
    rewrite (rc==0) and a KiCad resave preserves both constructs — the exact
    verification that cleared component_class of the source_type SIGSEGV."""
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _COMPONENT_CLASS_BOARD))
    board = tmp_path / "cc.kicad_pcb"
    board.write_text(out)
    _drc_ok(board, tmp_path)
    proc = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", "--force", str(board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    resaved = board.read_text()
    assert '(component_class "PSU")' in resaved
    assert re.search(
        r'\(component_classes\s+\(class "PSU"\)\s+\(class "RF"\)\s*\)', resaved
    )
