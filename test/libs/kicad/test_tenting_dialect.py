# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
P0.2 S3 — tenting-family dual-shape support (BACKLOG M2, breaking point ②).

v9 serializes the tenting family as bare presence symbols
``(tenting front back)``; v10 nests them as ``(tenting (front yes) (back
yes))``. Reading accepts both shapes everywhere (setup, pad, via); writing
follows the board's own version: v9 boards keep the v9 bytes (corpus byte
fidelity guards this), v10 boards get the nested form.

No corpus switch flips here — v10 boards still stop at the net model (S4) —
so this file brings its own inline coverage per the S0 discipline.
"""

import re

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad

V9 = 20241229
V10 = 20260206


def _board(version: int, setup_tenting: str, pad_tenting: str = "") -> str:
    return f"""(kicad_pcb
\t(version {version})
\t(generator "test")
\t(generator_version "x")
\t(setup
\t\t(pad_to_mask_clearance 0)
{setup_tenting}
\t)
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(at 0 0)
\t\t(pad "1" smd rect
\t\t\t(at 0 0)
\t\t\t(size 1 1)
\t\t\t(layers "F.Cu")
{pad_tenting}
\t\t\t(uuid "00000000-0000-0000-0000-00000000000c")
\t\t)
\t)
)
"""


def test_read_v9_bare_symbols():
    pcb = kicad.loads(
        kicad.pcb.PcbFile, _board(V9, "\t\t(tenting front back)")
    ).kicad_pcb
    t = pcb.setup.tenting
    assert (t.front, t.back, t.none) == (True, True, False)


def test_read_v9_single_token_and_none():
    pcb = kicad.loads(
        kicad.pcb.PcbFile,
        _board(V9, "\t\t(tenting front)", "\t\t\t(tenting none)"),
    ).kicad_pcb
    assert (pcb.setup.tenting.front, pcb.setup.tenting.back) == (True, False)
    pad = pcb.footprints[0].pads[0]
    assert pad.tenting.none is True
    assert (pad.tenting.front, pad.tenting.back) == (False, False)


def test_read_v10_nested_shape():
    # version kept at the supported ceiling: the loads guard checks the
    # number, the parser accepts both shapes regardless of it
    pcb = kicad.loads(
        kicad.pcb.PcbFile,
        _board(
            V9,
            "\t\t(tenting\n\t\t\t(front yes)\n\t\t\t(back no)\n\t\t)",
            "\t\t\t(tenting\n\t\t\t\t(front yes)\n\t\t\t)",
        ),
    ).kicad_pcb
    assert (pcb.setup.tenting.front, pcb.setup.tenting.back) == (True, False)
    pad = pcb.footprints[0].pads[0]
    assert (pad.tenting.front, pad.tenting.back, pad.tenting.none) == (
        True,
        False,
        False,
    )


def test_absent_tenting_is_none():
    pcb = kicad.loads(kicad.pcb.PcbFile, _board(V9, "")).kicad_pcb
    assert pcb.setup.tenting is None
    assert pcb.footprints[0].pads[0].tenting is None


def test_write_v9_keeps_bare_symbols():
    raw = _board(V9, "\t\t(tenting front back)", "\t\t\t(tenting none)")
    out = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))
    assert "(tenting front back)" in out
    assert "(tenting none)" in out
    assert "(front yes)" not in out


def test_write_v10_nests_and_drops_none():
    raw = _board(V9, "\t\t(tenting front)", "\t\t\t(tenting none)")
    pcb_file = kicad.loads(kicad.pcb.PcbFile, raw)
    pcb_file.kicad_pcb.version = V10
    out = kicad.dumps(pcb_file)

    # setup: nested, both keys explicit (KiCad 10 writes false as "no")
    assert re.search(r"\(tenting\s*\(front yes\)\s*\(back no\)\s*\)", out), out
    # pad: the v9-only "none" token must not leak into v10 output
    assert "none" not in re.sub(r"hatch|not_allowed", "", out)
    # round-trip: our own v10 shape must parse back to the same flags
    reread = kicad.loads(kicad.pcb.PcbFile, out.replace(f"(version {V10})", f"(version {V9})"))
    assert (
        reread.kicad_pcb.setup.tenting.front,
        reread.kicad_pcb.setup.tenting.back,
    ) == (True, False)


def test_v9_roundtrip_byte_stable_with_tenting():
    raw = _board(V9, "\t\t(tenting front back)")
    pcb_file = kicad.loads(kicad.pcb.PcbFile, raw)
    dump = kicad.dumps(pcb_file)
    assert kicad.dumps(kicad.loads(kicad.pcb.PcbFile, dump)) == dump


@pytest.mark.parametrize("flags", [(True, True), (True, False), (False, False)])
def test_v10_write_read_roundtrip_all_flag_combos(flags: tuple[bool, bool]):
    front, back = flags
    setup = (
        f"\t\t(tenting\n\t\t\t(front {'yes' if front else 'no'})"
        f"\n\t\t\t(back {'yes' if back else 'no'})\n\t\t)"
    )
    pcb_file = kicad.loads(kicad.pcb.PcbFile, _board(V9, setup))
    pcb_file.kicad_pcb.version = V10
    out = kicad.dumps(pcb_file)
    reread = kicad.loads(
        kicad.pcb.PcbFile, out.replace(f"(version {V10})", f"(version {V9})")
    )
    t = reread.kicad_pcb.setup.tenting
    assert (t.front, t.back) == flags
