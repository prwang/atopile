# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
P0.2 S0 — acceptance gates written BEFORE any migration code (BACKLOG §P0.2).

Three switches that define "done" for the v10 dialect migration:

1. M0 net-numbering synthesis contract — v10 files have no net table, so the
   in-memory numbering rule is ours to define and was pinned before the
   implementation existed: numbers are assigned by byte-wise sorted net name,
   1..n, with net 0 always "" (the implicit no-net). [flipped in S4 — and the
   S4 mutation self-check proved this is the ONLY test that catches a
   consistent encounter-order renumbering: the semantic views are name-based
   and therefore blind to it by design]

2. kicad-cli DRC oracle — a board loaded and re-written by us must be a v10
   file that KiCad 10 can read (DRC runs at all). Red until the write dialect
   flips at S7 (version bump + flag day).

3. KiCad re-save round-trip oracle — KiCad 10 re-saving our output must not
   change the semantic view when we read it back. [flipped in S4]

S6 inversion ledger (per the S0 discipline checklist, these two pinned-quirk
tests get *inverted* together with the consumer migration commits):
  - S6a: test_transformer_nets.py::test_remove_net_skips_zone_with_stale_name
         [inverted → test_remove_net_disconnects_zone_despite_stale_name]
  - S6b: test_layout_sync_nets.py::test_get_net_number_silently_maps_unknown_to_zero
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import semantic_view_json
from faebryk.libs.test.fileformats import FILEFORMATS_PATH

V9_PCB_DIR = FILEFORMATS_PATH / "v9" / "pcb"
V10_PCB_DIR = FILEFORMATS_PATH / "v10" / "pcb"

NEEDS_KICAD_CLI = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)

# ---------------------------------------------------------------------------
# 1. M0 synthesis-rule contract (flips in S4)
# ---------------------------------------------------------------------------

# Net references deliberately appear in non-alphabetical order: the rule is
# "sorted by name", NOT "first-reference order" — a first-reference-order
# implementation must fail this test.
_V10_SYNTHESIS_BOARD = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(segment
\t\t(start 0 0)
\t\t(end 1 0)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net "zz_last")
\t\t(uuid "00000000-0000-0000-0000-000000000001")
\t)
\t(segment
\t\t(start 1 0)
\t\t(end 2 0)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net "mm_mid")
\t\t(uuid "00000000-0000-0000-0000-000000000002")
\t)
\t(segment
\t\t(start 2 0)
\t\t(end 3 0)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net "aa_first")
\t\t(uuid "00000000-0000-0000-0000-000000000003")
\t)
)
"""


def _reorder_segments(text: str) -> str:
    """Same board, net references encountered in a different order."""
    blocks = re.findall(r"\t\(segment\n(?:.*\n)*?\t\)\n", text)
    assert len(blocks) == 3
    out = text
    for b in blocks:
        out = out.replace(b, "")
    closing = out.rfind(")")
    return out[:closing] + "".join(reversed(blocks)) + out[closing:]


def test_net_numbering_synthesis_rule():
    pcb_file = kicad.loads(kicad.pcb.PcbFile, _V10_SYNTHESIS_BOARD)
    pcb = pcb_file.kicad_pcb

    table = [(n.number, n.name if n.name is not None else "") for n in pcb.nets]
    assert table == [
        (0, ""),
        (1, "aa_first"),
        (2, "mm_mid"),
        (3, "zz_last"),
    ], "synthesis rule: sorted by name, numbers dense from 1, net 0 always ''"

    # numbers are handles, names are semantics: references must resolve
    by_number = dict(table)
    assert sorted(by_number[s.net] for s in pcb.segments) == [
        "aa_first",
        "mm_mid",
        "zz_last",
    ]

    # reference order must not matter (T4 pins this corpus-wide; pinned here
    # against the synthesis directly so the contract is self-contained)
    reordered = kicad.loads(kicad.pcb.PcbFile, _reorder_segments(_V10_SYNTHESIS_BOARD))
    table2 = [
        (n.number, n.name if n.name is not None else "")
        for n in reordered.kicad_pcb.nets
    ]
    assert table2 == table


# ---------------------------------------------------------------------------
# 2. DRC oracle (flips in S7 — write dialect)
# ---------------------------------------------------------------------------


@NEEDS_KICAD_CLI
@pytest.mark.xfail(strict=True, reason="P0.2 S7: write dialect is still v9")
def test_oracle_written_board_is_v10_and_drc_runs(tmp_path: Path):
    raw = (V9_PCB_DIR / "test.kicad_pcb").read_text()
    pcb_file = kicad.loads(kicad.pcb.PcbFile, raw)
    out = kicad.dumps(pcb_file)

    m = re.search(r"\(version (\d+)\)", out)
    assert m is not None
    assert int(m.group(1)) >= 20250000, (
        f"written dialect is v9 ({m.group(1)}); flag day (S7) flips this"
    )

    board = tmp_path / "board.kicad_pcb"
    board.write_text(out)
    report = tmp_path / "drc.json"
    proc = subprocess.run(
        [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "-o",
            str(report),
            str(board),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # violations are fine — what must not happen is KiCad failing to read us
    assert proc.returncode == 0, f"kicad-cli could not process our output:\n{proc.stderr}"
    json.loads(report.read_text())


# ---------------------------------------------------------------------------
# 3. KiCad re-save round-trip oracle (flips when v10 read+write are real)
# ---------------------------------------------------------------------------


@NEEDS_KICAD_CLI
def test_oracle_kicad_resave_keeps_semantics(tmp_path: Path):
    raw = (V10_PCB_DIR / "test.kicad_pcb").read_text()
    ours = kicad.loads(kicad.pcb.PcbFile, raw)
    before = semantic_view_json(ours.kicad_pcb)

    board = tmp_path / "board.kicad_pcb"
    board.write_text(kicad.dumps(ours))
    proc = subprocess.run(
        ["kicad-cli", "pcb", "upgrade", "--force", str(board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"KiCad 10 rejected our v10 output:\n{proc.stderr}"

    resaved = kicad.loads(kicad.pcb.PcbFile, board.read_text())
    assert semantic_view_json(resaved.kicad_pcb) == before, (
        "KiCad re-save changed the semantic view of our output"
    )
