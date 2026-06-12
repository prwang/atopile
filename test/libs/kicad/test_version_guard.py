# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
P0.2 S2 — readable version guard (BACKLOG M4a, fact 5).

Loading a board newer than the supported dialect must produce an actionable
error naming both versions — not an inscrutable Zig parse error pointing at
whatever construct happens to break first (historically: tenting).

The supported ceiling is kicad.PCB_MAX_SUPPORTED_VERSION; S4 bumps it to the
v10 dialect and these tests keep guarding against future formats.
"""

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.test.fileformats import FILEFORMATS_PATH


def _minimal_board(version: int) -> str:
    return (
        "(kicad_pcb\n"
        f"\t(version {version})\n"
        '\t(generator "test")\n'
        '\t(generator_version "x")\n'
        ")\n"
    )


def test_too_new_version_raises_readable_error():
    future = 99990101
    with pytest.raises(kicad.UnsupportedKicadVersion) as exc:
        kicad.loads(kicad.pcb.PcbFile, _minimal_board(future))
    msg = str(exc.value)
    assert str(future) in msg
    assert str(kicad.PCB_MAX_SUPPORTED_VERSION) in msg
    assert "upgrade" in msg, "guard must tell the user what not to do"


def test_guard_names_the_file_for_path_loads(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_minimal_board(99990101))
    with pytest.raises(kicad.UnsupportedKicadVersion) as exc:
        kicad.loads(kicad.pcb.PcbFile, board)
    assert board.name in str(exc.value)


def test_supported_version_passes_guard():
    pcb_file = kicad.loads(
        kicad.pcb.PcbFile, _minimal_board(kicad.PCB_MAX_SUPPORTED_VERSION)
    )
    assert pcb_file.kicad_pcb.version == kicad.PCB_MAX_SUPPORTED_VERSION


@pytest.mark.parametrize(
    "name", ["test", "layout_reuse_top", "interf_u_unrouted", "lvds_converter_dualclk"]
)
def test_guard_verdict_matches_dialect_support(name: str):
    """Every v10 corpus board must either load (once S4 lands) or fail with
    the readable guard error — never with a raw parse error."""
    raw = (FILEFORMATS_PATH / "v10" / "pcb" / f"{name}.kicad_pcb").read_text()
    version = 20260206
    assert f"(version {version})" in raw
    if version > kicad.PCB_MAX_SUPPORTED_VERSION:
        with pytest.raises(kicad.UnsupportedKicadVersion):
            kicad.loads(kicad.pcb.PcbFile, raw)
    else:
        kicad.loads(kicad.pcb.PcbFile, raw)
