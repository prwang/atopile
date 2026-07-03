# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Metamorphic construct-fidelity tests for the GUI-authored routing constructs
(teardrops, teardrop zones, generated tuning patterns) — the sibling of
test_net_binding_corruption.py (BACKLOG P0.1 T4 conventions).

Natural corpus snapshots prove the constructs are *projected*, not that the
projection *binds to the right bits*: a semantic_view that dropped or hardcoded
a teardrop field would still match its snapshot. These tests manufacture the
evidence:

- semantic-BREAKING corruptions (flip a teardrop zone's type padvia↔track_end,
  flip a teardrops block's enabled flag, change a generated's target_length)
  → the semantic view MUST move; passing through unchanged means the oracle is
  blind to the construct and every fidelity claim built on it is vacuous;
- semantic-PRESERVING corruptions (reorder the keys inside a teardrops block —
  KiCad's parseTEARDROP_PARAMETERS is token-driven and order-insensitive)
  → the view MUST NOT move.

The corruptions are applied with the schema-independent sexp_tree helpers, not
with the engine under test. Each corruption asserts its own precondition (the
construct exists with the expected value) so a fixture regression cannot turn a
test vacuously green.
"""

from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import semantic_view_json
from faebryk.libs.test import sexp_tree
from faebryk.libs.test.fileformats import FILEFORMATS_PATH
from faebryk.libs.test.sexp_tree import Node, children, head, walk

# v10 fixtures that actually carry the constructs (see their corpus snapshots):
#   via_treatments             — vias with teardrops / padstack / treatments
#   teardrop_elongated_pad     — a pad-level teardrops override
#   two_segment_teardrop       — a (zone (attr (teardrop (type padvia)))) zone
#   tuning_generators_load_save — a real tuning_pattern generated
CONSTRUCT_BOARDS = [
    "via_treatments",
    "teardrop_elongated_pad",
    "two_segment_teardrop",
    "tuning_generators_load_save",
    "zone_arc_tuning",
]


def _raw(stem: str) -> str:
    return (FILEFORMATS_PATH / "v10" / "pcb" / f"{stem}.kicad_pcb").read_text()


def _view(text: str) -> str:
    # bind the PcbFile: it owns the zig memory; dropping it dangles .kicad_pcb
    pcb_file = kicad.loads(kicad.pcb.PcbFile, text)
    return semantic_view_json(pcb_file.kicad_pcb)


def _view_of_tree(root: Node) -> str:
    return _view(sexp_tree.dumps(root))


@pytest.fixture(params=CONSTRUCT_BOARDS)
def board(request) -> tuple[Node, str]:
    """(parsed sexp tree, baseline semantic view) of a construct-carrying
    v10 corpus board."""
    raw = _raw(request.param)
    return sexp_tree.parse(raw)[0], _view(raw)


def _teardrops_blocks(root: Node) -> list[Node]:
    return [n for n in walk(root) if head(n) == "teardrops"]


# --- harness self-check ------------------------------------------------------


def test_reformat_is_view_neutral(board):
    """The corrupter's own parse→dump reformatting must not move the view —
    otherwise every test below would be vacuous."""
    root, baseline = board
    assert _view_of_tree(root) == baseline


# --- semantic-preserving corruptions: view must be invariant ------------------


@pytest.mark.parametrize("stem", ["via_treatments", "teardrop_elongated_pad"])
def test_teardrops_key_reorder_is_view_neutral(stem: str):
    """Reversing the 9 keyed values inside every (teardrops ...) block is a pure
    reordering — KiCad's parser is token-driven and so is ours. The view must
    not move (and the bytes must actually have moved, or this proves nothing)."""
    raw = _raw(stem)
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]
    pristine = sexp_tree.dumps(root)

    blocks = _teardrops_blocks(root)
    assert blocks, f"{stem} fixture lost its teardrops blocks"
    for block in blocks:
        block[1:] = list(reversed(block[1:]))

    assert sexp_tree.dumps(root) != pristine, "reorder corruption was a no-op"
    assert _view_of_tree(root) == baseline


# --- semantic-breaking corruptions: the view MUST move ------------------------


def _assert_view_moves(root: Node, baseline: str, what: str) -> None:
    corrupted = _view_of_tree(root)
    assert corrupted != baseline, (
        f"{what} did not move the semantic view — the oracle is blind to this "
        "construct and its round-trip fidelity is unverifiable"
    )


def test_zone_teardrop_type_flip_moves_view():
    """padvia ↔ track_end is a different KiCad teardrop rebuild target — the
    view must see it."""
    raw = _raw("two_segment_teardrop")
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]

    types = [
        t
        for n in walk(root)
        if head(n) == "teardrop"
        for t in children(n, "type")
    ]
    assert types and types[0][1] == "padvia", "fixture lost its teardrop zone"
    types[0][1] = "track_end"

    _assert_view_moves(root, baseline, "flipping the teardrop zone type")


@pytest.mark.parametrize("stem", ["via_treatments", "teardrop_elongated_pad"])
def test_teardrops_enabled_flip_moves_view(stem: str):
    """(enabled yes) → (enabled no) disables the teardrop — semantics, not
    formatting. Covers both carriers: via-level and pad-level teardrops."""
    raw = _raw(stem)
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]

    enabled = [
        e
        for block in _teardrops_blocks(root)
        for e in children(block, "enabled")
        if e[1] == "yes"
    ]
    assert enabled, f"{stem} fixture has no enabled teardrops block"
    enabled[0][1] = "no"

    _assert_view_moves(root, baseline, "flipping teardrops enabled")


def _zone_pts(root: Node) -> Node:
    pts = [
        p
        for z in children(root, "zone")
        for poly in children(z, "polygon")
        for p in children(poly, "pts")
    ]
    assert pts, "fixture lost its zone polygon"
    return pts[0]


def _base_line_pts(root: Node) -> Node:
    pts = [
        p
        for g in children(root, "generated")
        for bl in children(g, "base_line")
        for p in children(bl, "pts")
    ]
    assert pts, "fixture lost its tuning base_line"
    return pts[0]


def _pop_arc(pts: Node) -> Node:
    arcs = [i for i, n in enumerate(pts[1:], start=1) if head(n) == "arc"]
    assert arcs, "pts chain lost its (arc ...) entry"
    # precondition: the arc is MID-chain (xy entries on both sides), else the
    # reorder corruption below could be a no-op
    assert arcs[0] != 1 and arcs[0] != len(pts) - 1, "fixture arc not mid-chain"
    return pts.pop(arcs[0])


def test_pts_arc_reorder_moves_view():
    """The xy/arc interleaving of a (pts ...) chain IS the outline geometry
    (KiCad rebuilds the SHAPE_LINE_CHAIN in file order) — moving the arc from
    mid-chain to the end is a DIFFERENT polygon and the view must see it.
    This is exactly the corruption the pre-fix two-list Pts encoder produced
    on every dumps()."""
    raw = _raw("zone_arc_tuning")
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]

    pts = _zone_pts(root)
    pts.append(_pop_arc(pts))

    _assert_view_moves(root, baseline, "reordering the zone outline arc")


def test_pts_arc_deletion_moves_view():
    """Deleting the arc entirely straightens the outline corner — copper
    geometry, not formatting."""
    raw = _raw("zone_arc_tuning")
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]

    _pop_arc(_zone_pts(root))

    _assert_view_moves(root, baseline, "deleting the zone outline arc")


def test_base_line_arc_mutation_moves_view():
    """A base_line arc's mid point bends the baseline the next re-tune meanders
    along; kicad-cli cannot catch its corruption (any 3 points parse), so the
    semantic view is the ONLY oracle for it."""
    raw = _raw("zone_arc_tuning")
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]

    mids = [
        m
        for arc in children(_base_line_pts(root), "arc")
        for m in children(arc, "mid")
    ]
    assert mids and mids[0][1] == "43.535534", "fixture lost its base_line arc"
    mids[0][1] = "41.5"

    _assert_view_moves(root, baseline, "mutating the base_line arc mid point")


def test_pts_arc_key_reorder_is_view_neutral():
    """Reversing the keyed (start)(mid)(end) INSIDE an arc entry is pure
    formatting — both KiCad's arc parser and ours are token-driven. The view
    must not move (control for the reorder/deletion tests above)."""
    raw = _raw("zone_arc_tuning")
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]
    pristine = sexp_tree.dumps(root)

    arcs = [a for a in walk(root) if head(a) == "arc" and children(a, "mid")]
    assert arcs, "fixture lost its pts arcs"
    for arc in arcs:
        arc[1:] = list(reversed(arc[1:]))

    assert sexp_tree.dumps(root) != pristine, "reorder corruption was a no-op"
    assert _view_of_tree(root) == baseline


def test_generated_target_length_change_moves_view():
    """target_length is the tuning goal a re-tune acts on; kicad-cli cannot
    catch its loss (KiCad's generator property map is open, defaults apply
    silently), so the semantic view is the ONLY oracle for it."""
    raw = _raw("tuning_generators_load_save")
    baseline = _view(raw)
    root = sexp_tree.parse(raw)[0]

    targets = [
        t
        for g in children(root, "generated")
        for t in children(g, "target_length")
    ]
    assert targets and targets[0][1] == "100", "fixture lost its tuning_pattern"
    targets[0][1] = "123.456"

    _assert_view_moves(root, baseline, "changing generated target_length")
