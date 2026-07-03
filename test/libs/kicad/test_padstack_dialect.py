# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
BACKLOG §G — padstack + teardrop fidelity (GUI advanced routing constructs).

Grammar ground truth = KiCad 10.0.3 (pcb_io_kicad_sexpr[_parser].cpp):

- via padstack: ``(padstack (mode front_inner_back|custom) (layer "<name>"
  (size <d>)))`` — mode is a KEYED sub-list, the layer name is POSITIONAL,
  and the only legal per-layer subkey is a single-scalar size. Before this
  landed, loading that exact v10 shape raised
  ``ValueError: MissingField ... field 'y'`` (the schema modeled KiCad's
  in-memory per-layer PADSTACK — Xy size + thermal_* — instead of the file
  syntax; same category error as ZonePlacement.source_type, see CLAUDE.md).
  The first test pins that burn.
- pad padstack: per-layer shape/size/offset/... overrides (parsePadstack).
- pad teardrops: same 9-key block as vias, written between primitives and
  tenting.
- via-level v10 keys: blind/buried/micro type tokens, start_end_only,
  backdrill/tertiary_drill, front/back_post_machining,
  capping/filling (opt-bools) and covering/plugging (nested front/back).
- tri-state opt-bools: KiCad's FormatOptBool writes yes|no|none; "none"
  (unspecified/inherit) reads as None and is re-emitted as none.

Corpus fixtures exercising the real grammar end-to-end (parse, idempotence,
no-data-loss, semantic snapshot — auto-discovered by
test_fileformats_corpus.py): padstacks_complex, teardrop_elongated_pad,
two_segment_teardrop, via_treatments.
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

NEEDS_KICAD_CLI = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)

# The new-construct fixtures promoted from KiCad's own QA boards (see
# v10/README.md). Every one must round-trip through our engine into a file
# kicad-cli 10.0.3 can still read.
NEW_CONSTRUCT_STEMS = [
    "padstacks_complex",
    "teardrop_elongated_pad",
    "two_segment_teardrop",
    "via_treatments",
]


def _board(body: str) -> str:
    return (
        '(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n'
        '\t(generator_version "10.0")\n\t(general (thickness 1.6))\n'
        f"{body})\n"
    )


# The exact real-grammar via padstack that used to hard-fail with
# "MissingField ... field 'y'" — reproduced 2026-07-03 before the fix.
_VIA_WITH_PADSTACK = _board(
    '\t(via\n\t\t(at 5 5)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
    '\t\t(layers "F.Cu" "B.Cu")\n'
    '\t\t(padstack (mode front_inner_back) (layer "Inner" (size 0.5)))\n'
    '\t\t(net "GND")\n'
    '\t\t(uuid "00000000-0000-0000-0000-0000000000aa")\n\t)\n'
)


def test_real_v10_via_padstack_reads():
    """The board-blocking burn: a GUI-saved per-layer via size must load."""
    pcb = kicad.loads(kicad.pcb.PcbFile, _VIA_WITH_PADSTACK).kicad_pcb
    assert not kicad.last_unknown_keys
    ps = pcb.vias[0].padstack
    assert ps is not None
    assert ps.mode == "front_inner_back"
    assert [(la.name, la.size) for la in ps.layers] == [("Inner", 0.5)]


def test_via_padstack_write_is_kicad_legal():
    """Output grammar: keyed (mode ...), positional layer name, single-value
    size. The pre-fix schema wrote a positional mode token, which KiCad's
    parser rejects."""
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _VIA_WITH_PADSTACK))
    flat = re.sub(r"\s+", " ", out)
    assert "(padstack (mode front_inner_back)" in flat
    m = re.search(r'\(layer "Inner" \(size ([^)]*)\)', flat)
    assert m is not None, out
    assert len(m.group(1).split()) == 1, "via padstack layer size must be scalar"


def test_via_padstack_roundtrip_no_loss():
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _VIA_WITH_PADSTACK))
    assert not sexp_tree.data_loss(_VIA_WITH_PADSTACK, out)
    reread = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb
    ps = reread.vias[0].padstack
    assert [(la.name, la.size) for la in ps.layers] == [("Inner", 0.5)]


def test_absent_constructs_are_none():
    plain = _board(
        '\t(via\n\t\t(at 1 1)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
        '\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "GND")\n'
        '\t\t(uuid "00000000-0000-0000-0000-0000000000ab")\n\t)\n'
    )
    via = kicad.loads(kicad.pcb.PcbFile, plain).kicad_pcb.vias[0]
    assert via.padstack is None
    assert via.teardrops is None
    assert via.type is None
    assert via.backdrill is None
    assert via.tertiary_drill is None
    assert via.front_post_machining is None
    assert via.back_post_machining is None
    assert via.start_end_only is None
    assert via.capping is None
    assert via.covering is None
    assert via.plugging is None
    assert via.filling is None
    assert via.zone_layer_connections is None


_PAD_TEARDROPS = (
    "\t\t\t(teardrops\n"
    "\t\t\t\t(best_length_ratio 0.45)\n"
    "\t\t\t\t(max_length 2)\n"
    "\t\t\t\t(best_width_ratio 0.95)\n"
    "\t\t\t\t(max_width 3)\n"
    "\t\t\t\t(curved_edges no)\n"
    "\t\t\t\t(filter_ratio 0.8)\n"
    "\t\t\t\t(enabled yes)\n"
    "\t\t\t\t(allow_two_segments no)\n"
    "\t\t\t\t(prefer_zone_connections no)\n"
    "\t\t\t)\n"
)

_FP_WITH_PAD_CONSTRUCTS = _board(
    '\t(footprint "test:FP"\n\t\t(layer "F.Cu")\n\t\t(at 0 0)\n'
    '\t\t(pad "1" thru_hole rect\n\t\t\t(at 0 0)\n\t\t\t(size 1 1)\n'
    '\t\t\t(drill 0.5)\n\t\t\t(layers "*.Cu")\n'
    f"{_PAD_TEARDROPS}"
    '\t\t\t(uuid "00000000-0000-0000-0000-0000000000ac")\n'
    "\t\t\t(padstack\n\t\t\t\t(mode custom)\n"
    '\t\t\t\t(layer "In1.Cu"\n\t\t\t\t\t(shape rect)\n'
    "\t\t\t\t\t(size 1.016 1.016)\n\t\t\t\t\t(roundrect_rratio 0.25)\n"
    "\t\t\t\t\t(chamfer_ratio 0.25)\n"
    "\t\t\t\t\t(chamfer top_left bottom_right)\n\t\t\t\t)\n"
    '\t\t\t\t(layer "B.Cu"\n\t\t\t\t\t(shape oval)\n'
    "\t\t\t\t\t(size 1.524 1.2)\n\t\t\t\t\t(offset 0.1 -0.1)\n"
    "\t\t\t\t\t(thermal_gap 0.5)\n\t\t\t\t\t(zone_connect 2)\n\t\t\t\t)\n"
    "\t\t\t)\n\t\t)\n\t)\n"
)


def test_pad_teardrops_and_padstack_roundtrip():
    pcb = kicad.loads(kicad.pcb.PcbFile, _FP_WITH_PAD_CONSTRUCTS).kicad_pcb
    assert not kicad.last_unknown_keys
    pad = pcb.footprints[0].pads[0]

    td = pad.teardrops
    assert td is not None
    assert (td.best_length_ratio, td.max_length) == (0.45, 2.0)
    assert (td.enabled, td.allow_two_segments, td.prefer_zone_connections) == (
        True,
        False,
        False,
    )

    ps = pad.padstack
    assert ps is not None
    assert ps.mode == "custom"
    by_name = {la.name: la for la in ps.layers}
    assert set(by_name) == {"In1.Cu", "B.Cu"}
    assert by_name["In1.Cu"].shape == "rect"
    assert by_name["In1.Cu"].roundrect_rratio == 0.25
    assert list(by_name["In1.Cu"].chamfer) == ["top_left", "bottom_right"]
    assert (by_name["B.Cu"].size.w, by_name["B.Cu"].size.h) == (1.524, 1.2)
    assert (by_name["B.Cu"].offset.x, by_name["B.Cu"].offset.y) == (0.1, -0.1)
    assert by_name["B.Cu"].thermal_gap == 0.5
    assert by_name["B.Cu"].zone_connect == "FULL"

    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _FP_WITH_PAD_CONSTRUCTS))
    assert not sexp_tree.data_loss(_FP_WITH_PAD_CONSTRUCTS, out)
    # dumps -> loads equivalence
    pad2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.footprints[0].pads[0]
    assert pad2.teardrops.best_length_ratio == 0.45
    assert {la.name for la in pad2.padstack.layers} == {"In1.Cu", "B.Cu"}
    # write order on pads: teardrops before tenting/uuid, padstack last
    flat = re.sub(r"\s+", " ", out)
    assert flat.index("(teardrops") < flat.index('(uuid "00000000-0000-0000-0000-0000000000ac"')
    assert flat.index('(uuid "00000000-0000-0000-0000-0000000000ac"') < flat.index("(padstack")


_VIA_TREATMENTS = _board(
    "\t(via blind\n\t\t(at 6 5)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n"
    "\t\t(backdrill (size 0.9) (layers \"B.Cu\" \"In1.Cu\"))\n"
    "\t\t(tertiary_drill (size 1) (layers \"F.Cu\" \"In1.Cu\"))\n"
    "\t\t(front_post_machining counterbore (size 1.2) (depth 0.3))\n"
    "\t\t(back_post_machining countersink (size 1.1) (angle 90))\n"
    '\t\t(layers "F.Cu" "In1.Cu")\n'
    "\t\t(start_end_only yes)\n"
    "\t\t(zone_layer_connections)\n"
    "\t\t(tenting (front yes) (back none))\n"
    "\t\t(capping yes)\n"
    "\t\t(covering (front no) (back no))\n"
    "\t\t(plugging (front yes) (back none))\n"
    "\t\t(filling no)\n"
    '\t\t(net "GND")\n'
    '\t\t(uuid "00000000-0000-0000-0000-0000000000ad")\n\t)\n'
)


def test_via_treatments_roundtrip():
    """The v10 via-level keys the 10.0.3 formatter writes: type token,
    backdrill/tertiary_drill, post machining, start_end_only, the IPC-4761
    protection family with tri-state front/back, and the present-but-empty
    (zone_layer_connections)."""
    pcb = kicad.loads(kicad.pcb.PcbFile, _VIA_TREATMENTS).kicad_pcb
    assert not kicad.last_unknown_keys
    via = pcb.vias[0]
    assert via.type == "blind"
    assert via.backdrill.size == 0.9
    assert list(via.backdrill.layers) == ["B.Cu", "In1.Cu"]
    assert via.tertiary_drill.size == 1.0
    assert via.front_post_machining.mode == "counterbore"
    assert via.front_post_machining.depth == 0.3
    assert via.back_post_machining.mode == "countersink"
    assert via.back_post_machining.angle == 90.0
    assert via.start_end_only is True
    # present-but-empty zone_layer_connections: NOT None (absent), but empty
    assert via.zone_layer_connections is not None
    assert list(via.zone_layer_connections) == []
    # tri-state: none == unspecified == None; distinct from no == False
    assert (via.tenting.front, via.tenting.back) == (True, None)
    assert (via.covering.front, via.covering.back) == (False, False)
    assert (via.plugging.front, via.plugging.back) == (True, None)
    assert (via.capping, via.filling) == (True, False)

    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _VIA_TREATMENTS))
    assert not sexp_tree.data_loss(_VIA_TREATMENTS, out)
    flat = re.sub(r"\s+", " ", out)
    assert "(via blind" in flat
    assert "(zone_layer_connections)" in flat
    assert re.search(r"\(tenting \(front yes\) \(back none\) ?\)", flat), flat
    assert re.search(r"\(plugging \(front yes\) \(back none\) ?\)", flat), flat

    # dumps -> loads equivalence
    via2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.vias[0]
    assert via2.type == "blind"
    assert (via2.tenting.front, via2.tenting.back) == (True, None)
    assert via2.zone_layer_connections is not None
    assert list(via2.zone_layer_connections) == []


def test_absent_zone_layer_connections_stays_absent():
    """Absent vs present-but-empty must not be conflated: an absent clause
    leaves KiCad's defaults, an empty one forces no-zone-connection."""
    plain = _board(
        '\t(via\n\t\t(at 1 1)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
        '\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "GND")\n'
        '\t\t(uuid "00000000-0000-0000-0000-0000000000ae")\n\t)\n'
    )
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, plain))
    assert "zone_layer_connections" not in out


def test_unknown_padstack_subkey_is_loud():
    """S5a backstop stays in force for future grammar: a subkey we do not
    model inside a padstack is reported, never silently dropped."""
    board = _VIA_WITH_PADSTACK.replace(
        '(layer "Inner" (size 0.5))',
        '(layer "Inner" (size 0.5) (v11_only_subkey 1))',
    )
    kicad.loads(kicad.pcb.PcbFile, board)
    assert any("v11_only_subkey" in k for k in kicad.last_unknown_keys), (
        kicad.last_unknown_keys
    )


def test_decode_error_names_the_failing_struct():
    """Loud errors must name the real struct/field: the padstack burn used to
    be misattributed as 'kicad.pcb.Font field y' (stale ErrorContext path)."""
    bad = _board(
        "\t(via\n\t\t(at 5)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n"
        '\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "GND")\n'
        '\t\t(uuid "00000000-0000-0000-0000-0000000000af")\n\t)\n'
    )
    with pytest.raises(ValueError) as exc:
        kicad.loads(kicad.pcb.PcbFile, bad)
    assert "Xy" in str(exc.value), str(exc.value)
    assert "Font" not in str(exc.value)


def test_teardrop_write_order_matches_kicad():
    """Teardrop keys are emitted in KiCad 10.0.3's order so KiCad-authored
    fixtures round-trip structurally (formatTeardropParameters)."""
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, _FP_WITH_PAD_CONSTRUCTS))
    m = re.search(r"\(teardrops(.*?)\n\t\t\t\)", out, re.S)
    assert m is not None, out
    keys = re.findall(r"\(([a-z_]+) ", m.group(1))
    assert keys == [
        "best_length_ratio",
        "max_length",
        "best_width_ratio",
        "max_width",
        "curved_edges",
        "filter_ratio",
        "enabled",
        "allow_two_segments",
        "prefer_zone_connections",
    ]


@pytest.mark.parametrize("stem", NEW_CONSTRUCT_STEMS)
@NEEDS_KICAD_CLI
def test_fixture_rewrite_is_kicad_readable(stem: str, tmp_path: Path):
    """Every promoted new-construct fixture must survive a loads()->dumps()
    rewrite into a file kicad-cli 10.0.3 can still DRC (rc==0 == KiCad can
    read us; violations are fine)."""
    raw = (V10_PCB_DIR / f"{stem}.kicad_pcb").read_text()
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))
    board = tmp_path / "board.kicad_pcb"
    board.write_text(out)
    report = tmp_path / "drc.json"
    proc = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(report), str(board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"kicad-cli could not read our rewrite of {stem}:\n{proc.stderr}"
    )
    json.loads(report.read_text())
