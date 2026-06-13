# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
P0.2 S5a — schema-unknown keys must never be dropped silently (BACKLOG §P0.2).

The Zig decoder ignores any keyed list whose key matches no schema field; on
the next dumps() that data is gone. These tests pin the loudness contract:
loads() records such keys, warns by default, and raises with
strict_unknown=True. "Zero unknown keys over the corpus" is asserted by
test_fileformats_corpus.py::test_no_data_loss (the S5b completion criterion).
"""

import logging

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad

_BOARD_WITH_UNKNOWNS = """(kicad_pcb
\t(version 20241229)
\t(generator "pcbnew")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(zig_unknown_toplevel 42)
\t(segment
\t\t(start 0 0)
\t\t(end 1 0)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net 0)
\t\t(zig_unknown_in_segment "x")
\t\t(uuid "00000000-0000-0000-0000-000000000001")
\t)
)
"""

_CLEAN_BOARD = _BOARD_WITH_UNKNOWNS.replace(
    "\t(zig_unknown_toplevel 42)\n", ""
).replace("\t\t(zig_unknown_in_segment \"x\")\n", "")


def test_unknown_keys_are_recorded_and_warned(caplog):
    with caplog.at_level(logging.WARNING):
        kicad.loads(kicad.pcb.PcbFile, _BOARD_WITH_UNKNOWNS)

    assert any("KicadPcb:zig_unknown_toplevel" in k for k in kicad.last_unknown_keys)
    assert any(
        "Segment:zig_unknown_in_segment" in k for k in kicad.last_unknown_keys
    )
    assert any("WILL BE DROPPED" in r.message for r in caplog.records)


def test_strict_mode_raises():
    with pytest.raises(kicad.UnknownSexpKeys, match="zig_unknown_toplevel"):
        kicad.loads(kicad.pcb.PcbFile, _BOARD_WITH_UNKNOWNS, strict_unknown=True)


def test_clean_board_reports_nothing(caplog):
    with caplog.at_level(logging.WARNING):
        kicad.loads(kicad.pcb.PcbFile, _CLEAN_BOARD, strict_unknown=True)
    assert kicad.last_unknown_keys == []
    assert not [r for r in caplog.records if "WILL BE DROPPED" in r.message]


def test_recording_resets_between_loads():
    """A dirty parse must not leak its findings into the next clean parse."""
    with pytest.raises(kicad.UnknownSexpKeys):
        kicad.loads(kicad.pcb.PcbFile, _BOARD_WITH_UNKNOWNS, strict_unknown=True)
    kicad.loads(kicad.pcb.PcbFile, _CLEAN_BOARD)
    assert kicad.last_unknown_keys == []
