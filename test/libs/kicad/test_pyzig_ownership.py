# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
P0.2 S1 — pyzig ownership + loads-cache joint fix (BACKLOG M3b, fact 4b).

Two interlocking landmines, fixed in the same commit and pinned here:

1. pyzig use-after-free: sub-object wrappers (e.g. ``loads(...).kicad_pcb``)
   used to hold a raw pointer into the PcbFile wrapper's allocation with no
   reference to it — once the file wrapper was GC'd, the next parse reused the
   memory and the sub-object silently read another board's data. Sub-objects
   now hold an ownership chain (child -> parent -> root).

2. ``kicad.loads`` Path cache had no invalidation: re-reading a rewritten file
   returned the stale earlier parse. The cache is now fingerprinted by
   (mtime_ns, size), and ``kicad.dumps(obj, path)`` keeps it coherent.

These had to land together: the permanent cache was the only thing
accidentally shielding production's ``loads(path).kicad_pcb`` call sites from
the use-after-free; adding invalidation alone would have armed it.
"""

import gc
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad


def _board_text(net_name: str, ref: str = "R1") -> str:
    return f"""(kicad_pcb
\t(version 20241229)
\t(generator "test")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(net 0 "")
\t(net 1 "{net_name}")
\t(footprint "test:FP"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-0000-0000-00000000000a")
\t\t(at 1 2)
\t\t(property "Reference" "{ref}"
\t\t\t(at 0 0 0)
\t\t\t(layer "F.SilkS")
\t\t\t(uuid "00000000-0000-0000-0000-00000000000b")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at 0 0)
\t\t\t(size 1 1)
\t\t\t(layers "F.Cu")
\t\t\t(net 1 "{net_name}")
\t\t\t(uuid "00000000-0000-0000-0000-00000000000c")
\t\t)
\t)
)
"""


def _churn_allocator(n: int = 8) -> None:
    """Force allocator reuse: parse several boards and drop them."""
    for i in range(n):
        kicad.loads(kicad.pcb.PcbFile, _board_text(f"CHURN_{i}"))
    gc.collect()


def test_child_survives_dropped_file_wrapper():
    """The original minimal repro of fact 4b, inverted: dropping the PcbFile
    wrapper while holding .kicad_pcb must NOT let the next parse alias it."""
    pcb = kicad.loads(kicad.pcb.PcbFile, _board_text("AAA")).kicad_pcb
    gc.collect()
    _churn_allocator()

    assert [n.name for n in pcb.nets] == ["", "AAA"]


def test_grandchild_survives_dropped_intermediates():
    """Deep chain: list element wrapper keeps the root alive even after the
    file wrapper, the board wrapper and the list wrapper are all dropped."""
    fp = kicad.loads(kicad.pcb.PcbFile, _board_text("BBB", ref="R7")).kicad_pcb.footprints[0]
    gc.collect()
    _churn_allocator()

    assert fp.name == "test:FP"
    assert fp.pads[0].net.name == "BBB"


def test_loads_path_returns_same_object_while_unchanged(tmp_path: Path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_board_text("CCC"))

    first = kicad.loads(kicad.pcb.PcbFile, board)
    second = kicad.loads(kicad.pcb.PcbFile, board)
    assert first is second, "documented shared-object semantics broke"


def test_loads_path_reparses_after_rewrite(tmp_path: Path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_board_text("OLD_NET"))

    stale = kicad.loads(kicad.pcb.PcbFile, board)
    stale_pcb = stale.kicad_pcb
    assert stale_pcb.nets[1].name == "OLD_NET"

    board.write_text(_board_text("NEW_NET_LONGER"))
    fresh = kicad.loads(kicad.pcb.PcbFile, board)

    assert fresh is not stale
    assert fresh.kicad_pcb.nets[1].name == "NEW_NET_LONGER"

    # the evicted parse must stay valid even after the wrapper is dropped
    # (this is exactly why the cache fix and the ownership fix are one commit)
    del stale, fresh
    gc.collect()
    _churn_allocator()
    assert stale_pcb.nets[1].name == "OLD_NET"


def test_dumps_keeps_cache_coherent(tmp_path: Path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_board_text("DDD"))

    obj = kicad.loads(kicad.pcb.PcbFile, board)
    obj.kicad_pcb.nets[1].name = "RENAMED"
    kicad.dumps(obj, board)

    # load after dump: same object (not a stale reparse, not a cache miss)
    assert kicad.loads(kicad.pcb.PcbFile, board) is obj
    # and the bytes on disk match
    assert '(net 1 "RENAMED")' in board.read_text()


def test_loads_path_type_assert_still_holds(tmp_path: Path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(_board_text("EEE"))
    kicad.loads(kicad.pcb.PcbFile, board)
    with pytest.raises(AssertionError):
        kicad.loads(kicad.footprint.FootprintFile, board)  # type: ignore[arg-type]
