# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
(pts ...) xy/arc interleaving fidelity.

Grammar ground truth = KiCad 10.0.3: a (pts ...) block serializes a
SHAPE_LINE_CHAIN — formatPolyPts (pcb_io_kicad_sexpr.cpp:487) writes (xy ..)
and (arc (start ..)(mid ..)(end ..)) entries interleaved in chain order, and
parseOutlinePoints (parser :389) rebuilds the chain strictly in file order.
The relative order of xy/arc entries IS the outline geometry: re-emitting all
xys first and all arcs last (what two independent multidict lists produce)
parses back as a different, typically self-intersecting polygon.

The schema carries the interleaving as PtsArc.xys_before (the number of xy
entries preceding the arc in the chain), recorded by Pts.decode and
merge-consumed by Pts.writeBodyStreamed; it is engine metadata, never a file
token. This module pins:

- exact-order round-trip of a mixed chain (textual assertion, the burn:
  pre-fix the arc was reordered to the end of the chain silently),
- idempotence of the rewrite,
- kicad.copy preserving chain order,
- Python-created arcs (no recorded position) appending after the xys,
- xys_before never leaking into the file,
- the kicad-cli 10.0.3 oracle: upgrade --force of our rewrite is identical
  to upgrade of the original (KiCad sees the same chain).

Zone outlines, gr_poly/fp_poly, and tuning-pattern base_line all share the
same Pts type; the corpus gates bite on the arc-bearing fixture
(zone_arc_tuning.kicad_pcb) and test_construct_corruption.py pins that arc
reorder/mutation/deletion move the semantic view.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad

NEEDS_KICAD_CLI = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)

# Minimal v10 board: a zone whose outline chain is (xy, arc, xy, xy) — the
# arc sits MID-chain, so any all-xys-then-all-arcs emission is detectable.
MIXED_CHAIN_BOARD = """(kicad_pcb
  (version 20260206)
  (generator "pcbnew")
  (generator_version "10.0")
  (general (thickness 1.6) (legacy_teardrops no))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
  (zone
    (net "GND")
    (layer "F.Cu")
    (uuid "3dc4bb60-0000-0000-0000-000000000001")
    (hatch edge 0.5)
    (connect_pads (clearance 0.5))
    (min_thickness 0.25)
    (filled_areas_thickness no)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon
      (pts
        (xy 100 100)
        (arc (start 110 100) (mid 112.928932 101.213203) (end 114.142136 104.142136))
        (xy 114.142136 110)
        (xy 100 110)
      )
    )
  )
)
"""


def _pts_token_order(text: str, anchor: str = "(polygon") -> list[str]:
    """xy/arc token names of the first pts block after anchor, in file order."""
    start = text.index(anchor)
    m = re.search(r"\(pts\b", text[start:])
    assert m is not None
    body_start = start + m.start()
    # scan to the matching close paren of (pts
    depth = 0
    for i in range(body_start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                body = text[body_start : i + 1]
                break
    else:  # pragma: no cover
        raise AssertionError("unbalanced pts block")
    return re.findall(r"\((xy|arc)[\s(]", body)


def test_mixed_chain_roundtrips_in_exact_file_order():
    pcb = kicad.loads(kicad.pcb.PcbFile, MIXED_CHAIN_BOARD)
    pts = pcb.kicad_pcb.zones[0].polygon.pts
    # decode recorded the chain position: 1 xy precedes the arc
    assert [a.xys_before for a in pts.arcs] == [1]
    out = kicad.dumps(pcb)
    assert _pts_token_order(out) == ["xy", "arc", "xy", "xy"]
    # the arc body itself is intact
    assert "(start 110 100)" in out
    # engine metadata never becomes a file token
    assert "xys_before" not in out


def test_mixed_chain_rewrite_is_idempotent():
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, MIXED_CHAIN_BOARD))
    out2 = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, out))
    assert out == out2


def test_copy_preserves_chain_order():
    pcb = kicad.loads(kicad.pcb.PcbFile, MIXED_CHAIN_BOARD)
    zone_copy = kicad.copy(pcb.kicad_pcb.zones[0])
    assert [a.xys_before for a in zone_copy.polygon.pts.arcs] == [1]
    pcb.kicad_pcb.zones = [zone_copy]
    assert _pts_token_order(kicad.dumps(pcb)) == ["xy", "arc", "xy", "xy"]


def test_python_created_arc_appends_after_xys():
    pcb = kicad.loads(kicad.pcb.PcbFile, MIXED_CHAIN_BOARD)
    pts = pcb.kicad_pcb.zones[0].polygon.pts
    pts.arcs = list(pts.arcs) + [
        kicad.pcb.PtsArc(
            start=kicad.pcb.Xy(x=100, y=110),
            mid=kicad.pcb.Xy(x=99, y=105),
            end=kicad.pcb.Xy(x=100, y=100),
        )
    ]
    out = kicad.dumps(pcb)
    assert _pts_token_order(out) == ["xy", "arc", "xy", "xy", "arc"]
    # ...and the appended arc gets its position recorded on reload
    pts2 = kicad.loads(kicad.pcb.PcbFile, out).kicad_pcb.zones[0].polygon.pts
    assert [a.xys_before for a in pts2.arcs] == [1, 3]


def test_pure_xy_chain_serializes_as_before():
    """An arc-free chain must be byte-identical to the historical two-list
    emission (no behavioural change for the 99% case)."""
    pcb = kicad.loads(kicad.pcb.PcbFile, MIXED_CHAIN_BOARD)
    pts = pcb.kicad_pcb.zones[0].polygon.pts
    pts.arcs = []
    out = kicad.dumps(pcb)
    assert _pts_token_order(out) == ["xy", "xy", "xy"]
    flat = re.sub(r"\s+", " ", out[out.index("(pts") :])
    assert flat.startswith("(pts (xy 100 100) (xy 114.142136 110) (xy 100 110) )")


def test_generated_base_line_arc_roundtrips_in_order():
    """base_line/base_line_coupled wrap the same Pts type: a tuning pattern
    whose baseline has a mid-chain arc must round-trip in order too."""
    board = MIXED_CHAIN_BOARD.replace(
        "  (zone",
        """  (generated
    (uuid "7a37a54e-0000-0000-0000-00000000aaaa")
    (type tuning_pattern)
    (name "Tuning Pattern")
    (layer "F.Cu")
    (base_line
      (pts
        (xy 10 10)
        (arc (start 20 10) (mid 22.928932 11.213203) (end 24.142136 14.142136))
        (xy 24.142136 20)
      )
    )
    (corner_radius_percent 80)
    (end (xy 24.142136 20))
    (initial_side "default")
    (last_diff_pair_gap 0.1)
    (last_netname "GND")
    (last_status "unset")
    (last_track_width 0.2)
    (last_tuning_length 0)
    (max_amplitude 1)
    (min_amplitude 0.1)
    (min_spacing 0.6)
    (origin (xy 10 10))
    (override_custom_rules no)
    (rounded yes)
    (single_sided no)
    (target_length 50)
    (target_length_max 50.1)
    (target_length_min 49.9)
    (tuning_mode "single")
    (members "3dc4bb60-0000-0000-0000-00000000bbbb")
  )
  (segment (start 10 10) (end 24.142136 20) (width 0.2) (layer "F.Cu") (net "GND") (uuid "3dc4bb60-0000-0000-0000-00000000bbbb"))
  (zone""",
    )
    pcb = kicad.loads(kicad.pcb.PcbFile, board)
    gen = pcb.kicad_pcb.generateds[0]
    assert [a.xys_before for a in gen.base_line.pts.arcs] == [1]
    out = kicad.dumps(pcb)
    assert _pts_token_order(out, anchor="(base_line") == ["xy", "arc", "xy"]
    out2 = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, out))
    assert out == out2


@NEEDS_KICAD_CLI
def test_kicad_cli_sees_identical_chain(tmp_path: Path):
    """KiCad-as-oracle: upgrade --force of our rewrite must produce the same
    outline chain as upgrade of the original (pre-fix the rewrite's upgraded
    polygon had the arc moved to the end — a different polygon)."""

    def upgraded_pts(text: str, name: str) -> list[str]:
        p = tmp_path / name
        p.write_text(text)
        subprocess.run(
            ["kicad-cli", "pcb", "upgrade", "--force", str(p)],
            check=True,
            capture_output=True,
        )
        upgraded = p.read_text()
        start = upgraded.index("(polygon")
        end = upgraded.index("(filled_polygon") if "(filled_polygon" in upgraded else len(upgraded)
        return re.sub(r"\s+", " ", upgraded[start:end]).strip()

    original = upgraded_pts(MIXED_CHAIN_BOARD, "orig.kicad_pcb")
    rewrite = upgraded_pts(
        kicad.dumps(kicad.loads(kicad.pcb.PcbFile, MIXED_CHAIN_BOARD)),
        "rewrite.kicad_pcb",
    )
    assert original == rewrite
