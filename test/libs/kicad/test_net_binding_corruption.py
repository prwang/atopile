# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Metamorphic net-binding tests (BACKLOG P0.1 T4).

Natural corpus can't catch misbinding: in every KiCad/atopile-written file the
net-table order, the numbering and the first-reference order all coincide, so
a loader that binds by *position* (or synthesizes numbers in set order) passes
every round-trip test on natural files. These tests manufacture the diversity
ourselves:

- semantic-preserving corruptions (table permutation, consistent renumbering,
  section reordering) → the semantic view MUST NOT change;
- semantic-breaking corruptions (table-only name swap, dangling reference,
  zone net/net_name disagreement, duplicate names) → resolution MUST raise or
  the view MUST change; silently passing through unchanged is the bug class
  this file exists to kill.

The corruptions are applied with the schema-independent sexp_tree helpers, not
with the engine under test.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import (
    NetResolutionError,
    semantic_view_json,
)
from faebryk.libs.test import sexp_tree
from faebryk.libs.test.fileformats import FILEFORMATS_PATH
from faebryk.libs.test.sexp_tree import Node, children, head, walk

V9_BOARDS = ["test", "layout_reuse_top", "interf_u_unrouted"]


def _view(text: str) -> str:
    # bind the PcbFile: it owns the zig memory; dropping it dangles .kicad_pcb
    pcb_file = kicad.loads(kicad.pcb.PcbFile, text)
    return semantic_view_json(pcb_file.kicad_pcb)


def _view_of_tree(root: Node) -> str:
    return _view(sexp_tree.dumps(root))


@pytest.fixture(params=V9_BOARDS)
def board(request) -> tuple[Node, str]:
    """(parsed sexp tree, baseline semantic view) of a v9 corpus board."""
    raw = (FILEFORMATS_PATH / "v9" / "pcb" / f"{request.param}.kicad_pcb").read_text()
    return sexp_tree.parse(raw)[0], _view(raw)


def _net_table(root: Node) -> list[Node]:
    entries = children(root, "net")
    assert len(entries) >= 3, "corpus board too trivial for corruption tests"
    return entries


def _replace_children(root: Node, head_name: str, replacement: list[Node]) -> None:
    """Reassign the nodes with the given head, in slot order."""
    slots = [i for i, c in enumerate(root) if isinstance(c, list) and head(c) == head_name]
    assert len(slots) == len(replacement)
    for i, node in zip(slots, replacement):
        root[i] = node


# --- harness self-check ------------------------------------------------------


def test_reformat_is_view_neutral(board):
    """The corrupter's own parse→dump reformatting must not move the view —
    otherwise every test below would be vacuous."""
    root, baseline = board
    assert _view_of_tree(root) == baseline


# --- semantic-preserving corruptions: view must be invariant ------------------


def test_net_table_permutation_is_view_neutral(board):
    root, baseline = board
    _replace_children(root, "net", list(reversed(_net_table(root))))
    assert _view_of_tree(root) == baseline


def test_consistent_renumbering_is_view_neutral(board):
    """Swap two net numbers everywhere (table + all references). Numbers are
    file-local handles; nothing semantic may move."""
    root, baseline = board
    numbers = sorted(
        int(e[1]) for e in _net_table(root) if isinstance(e[1], str) and int(e[1]) != 0
    )
    a, b = str(numbers[0]), str(numbers[-1])
    assert a != b
    for node in walk(root):
        if head(node) == "net" and len(node) >= 2 and isinstance(node[1], str):
            if node[1] == a:
                node[1] = b
            elif node[1] == b:
                node[1] = a
    assert _view_of_tree(root) == baseline


def test_section_reordering_is_view_neutral(board):
    root, baseline = board
    for section in ("footprint", "segment", "zone", "group"):
        nodes = children(root, section)
        if len(nodes) > 1:
            _replace_children(root, section, list(reversed(nodes)))
    assert _view_of_tree(root) == baseline


# --- semantic-breaking corruptions: must raise or move the view ---------------


def _assert_not_silently_equal(root: Node, baseline: str, what: str) -> None:
    try:
        corrupted = _view_of_tree(root)
    except NetResolutionError:
        return  # loud failure: good
    assert corrupted != baseline, (
        f"{what} was bound silently and identically — "
        "the loader is not resolving nets through the table"
    )


def test_table_only_name_swap_is_caught(board):
    """Swap two *names* in the net table while every reference keeps its old
    number (and, for pads, its old inline name). A position- or
    first-reference-bound loader sees nothing; a name-resolving one must."""
    root, baseline = board
    named = [e for e in _net_table(root) if len(e) >= 3 and e[2] != '""']
    e1, e2 = named[0], named[-1]
    assert e1[2] != e2[2]
    e1[2], e2[2] = e2[2], e1[2]
    _assert_not_silently_equal(root, baseline, "a table-only name swap")


def test_dangling_reference_raises(board):
    root, _ = board
    carriers = children(root, "segment") or children(root, "via") or [
        pad
        for fp in children(root, "footprint")
        for pad in children(fp, "pad")
        if children(pad, "net")
    ]
    assert carriers, "every corpus board must have at least one net reference"
    ref = children(carriers[0], "net")[0]
    ref[1] = "9999"
    del ref[2:]  # drop any inline name: the number alone must already be fatal
    with pytest.raises(NetResolutionError, match="dangling"):
        _view_of_tree(root)


def test_zone_net_name_disagreement_raises(board):
    """A zone whose (net N) and (net_name ...) disagree must not be resolved
    silently in either direction."""
    root, _ = board
    target = next(e for e in _net_table(root) if len(e) >= 3 and e[2] != '""')
    zone = sexp_tree.parse(
        f'(zone (net {target[1]}) (net_name "NOT_{target[2][1:-1]}")'
        ' (layer "F.Cu") (uuid "cccccccc-dddd-eeee-ffff-000000000000")'
        " (hatch edge 0.5)"
        " (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))))"
    )[0]
    root.append(zone)
    with pytest.raises(NetResolutionError, match="net table maps"):
        _view_of_tree(root)


def test_duplicate_net_names_raise(board):
    root, _ = board
    named = [e for e in _net_table(root) if len(e) >= 3 and e[2] != '""']
    named[-1][2] = named[0][2]
    with pytest.raises(NetResolutionError, match="declared for both"):
        _view_of_tree(root)


# --- v10: synthesized-number contracts (strict-xfail until P0.2 M1) -----------

V10_XFAIL = pytest.mark.xfail(
    strict=True, reason="v10 dialect not parseable until P0.2 migration"
)


def _v10_path(stem: str) -> Path:
    return FILEFORMATS_PATH / "v10" / "pcb" / f"{stem}.kicad_pcb"


@V10_XFAIL
@pytest.mark.parametrize("stem", ["test", "lvds_converter_dualclk"])
def test_v10_section_reordering_is_view_neutral(stem: str):
    """v10 has no net table: numbers are synthesized from references. The view
    must not depend on reference order."""
    raw = _v10_path(stem).read_text()
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]
    for section in ("footprint", "segment", "via", "zone"):
        nodes = children(root, section)
        if len(nodes) > 1:
            _replace_children(root, section, list(reversed(nodes)))
    assert _view_of_tree(root) == baseline


@V10_XFAIL
@pytest.mark.parametrize("stem", ["test", "lvds_converter_dualclk"])
def test_v10_synthesized_numbers_deterministic(stem: str):
    """Same v10 file, two processes with different hash seeds → byte-identical
    dump. Kills set/dict-iteration-order number synthesis before it ships
    (the A1 bug class, net edition)."""
    script = (
        "import sys\n"
        "import faebryk.library._F\n"
        "from faebryk.libs.kicad.fileformats import kicad\n"
        "pcb = kicad.loads(kicad.pcb.PcbFile, open(sys.argv[1]).read())\n"
        "sys.stdout.write(kicad.dumps(pcb))\n"
    )
    dumps = [
        subprocess.run(
            [sys.executable, "-c", script, str(_v10_path(stem))],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("0", "1")
    ]
    assert dumps[0] == dumps[1]
