# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Corpus-wide regression tests for the kicad pcb dialect support
(BACKLOG P0.1 T2/T3).

Runs every board in test/common/resources/fileformats/kicad/v*/pcb/ through
four gates of increasing strength:

1. parse           — the file loads at all
2. idempotence     — load→dump→load→dump converges (dump == dump2)
3. no data loss    — schema-independent sexp-tree diff between input and dump;
                     anything the zig schema silently skips shows up here as a
                     per-field inventory (this is the completeness auditor that
                     feeds BACKLOG P0.2 M3)
4. byte fidelity   — raw == dump, only for atopile-authored boards (KiCad's own
                     writer formats floats/field-order differently; matching it
                     byte-for-byte is a non-goal)

plus the semantic-view snapshot (the migration safety net): the name-based
view of every parseable board must match its committed snapshot byte-for-byte.
Regenerate snapshots with REGEN_SEMANTIC_SNAPSHOTS=1 after an *intentional*
semantic change — never to silence an unexplained diff.

v10 boards parse and round-trip since P0.2 S4 (net model); gates 3 and 4
stay strict-xfail on them until S5 completes the schema and re-derives the
v10 byte-fidelity fixtures from our own writer.
"""

import os
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import semantic_view_json
from faebryk.libs.test import sexp_tree
from faebryk.libs.test.fileformats import FILEFORMATS_PATH, all_pcb_fixtures

# Boards whose bytes were last written by atopile/our fixtures (not KiCad).
ATOPILE_AUTHORED_STEMS = {"test", "layout_reuse_top"}

# Real-world KiCad-authored v9 boards currently lose data through the schema
# (pintype/pinfunction/sheetfile/rev/aux_axis_origin, ...). Inventory lives in
# the test failure output; fixing them is BACKLOG P0.2 S5 scope.
KNOWN_V9_DATA_LOSS_STEMS = {"interf_u_unrouted"}

SNAPSHOT_DIR = FILEFORMATS_PATH / "snapshots"
REGEN = os.environ.get("REGEN_SEMANTIC_SNAPSHOTS") == "1"

# v10 boards parse since S4 (net model), but the schema does not yet cover
# every v10 key (covering/plugging/..., units, sheetname, ...) — S5 burns
# this down field-by-field, then regenerates the v10 byte-fidelity fixtures
# from our own writer (matching kicad-cli's formatting is a non-goal).
S5_DATA_LOSS_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="schema drops v10-only keys (P0.2 S5 scope) — run for the inventory",
)
S5_BYTE_FIDELITY_XFAIL = pytest.mark.xfail(
    strict=True,
    reason="v10 fixture bytes are kicad-cli-authored; S5 re-derives them from "
    "our writer once the schema is complete",
)


def _params(extra_marks=lambda version, path: []):
    for version, path in all_pcb_fixtures():
        marks = list(extra_marks(version, path))
        yield pytest.param(version, path, id=f"v{version}-{path.stem}", marks=marks)


def _load(path: Path) -> kicad.pcb.PcbFile:
    # parse from text so these tests are independent of the loads Path cache
    return kicad.loads(kicad.pcb.PcbFile, path.read_text())


@pytest.mark.parametrize(("version", "path"), _params())
def test_parse(version: int, path: Path):
    pcb_file = _load(path)
    pcb = pcb_file.kicad_pcb
    assert pcb.footprints or pcb.segments or pcb.zones, "board parsed but empty"


@pytest.mark.parametrize(("version", "path"), _params())
def test_dump_load_idempotent(version: int, path: Path):
    dump = kicad.dumps(_load(path))
    dump2 = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, dump))
    assert dump == dump2


@pytest.mark.parametrize(
    ("version", "path"),
    _params(
        lambda version, path: [S5_DATA_LOSS_XFAIL]
        if version >= 10 or path.stem in KNOWN_V9_DATA_LOSS_STEMS
        else []
    ),
)
def test_no_data_loss(version: int, path: Path):
    raw = path.read_text()
    dump = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))
    loss = sexp_tree.data_loss(raw, dump)
    assert not loss, (
        f"schema silently dropped data from {path.name}:\n"
        f"{sexp_tree.summarize_loss(loss)}"
    )


@pytest.mark.parametrize(
    ("version", "path"),
    [
        p
        for p in _params(
            lambda version, path: [S5_BYTE_FIDELITY_XFAIL] if version >= 10 else []
        )
        if Path(str(p.values[1])).stem in ATOPILE_AUTHORED_STEMS
    ],
)
def test_byte_fidelity(version: int, path: Path):
    raw = path.read_text()
    assert raw == kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))


def _snapshot_path(version: int, path: Path) -> Path:
    return SNAPSHOT_DIR / f"v{version}" / f"{path.stem}.semantic.json"


@pytest.mark.parametrize(("version", "path"), _params())
def test_semantic_snapshot(version: int, path: Path):
    pcb_file = _load(path)
    view = semantic_view_json(pcb_file.kicad_pcb)
    snap = _snapshot_path(version, path)
    if REGEN:
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(view)
        pytest.skip(f"regenerated {snap}")
    assert snap.exists(), (
        f"missing snapshot {snap} — run with REGEN_SEMANTIC_SNAPSHOTS=1"
    )
    assert view == snap.read_text(), (
        "semantic view drifted from committed snapshot — if intentional, "
        "regenerate with REGEN_SEMANTIC_SNAPSHOTS=1 and review the diff"
    )


def _paired_stems() -> list[str]:
    by_version: dict[int, set[str]] = {}
    for version, path in all_pcb_fixtures():
        by_version.setdefault(version, set()).add(path.stem)
    return sorted(by_version.get(9, set()) & by_version.get(10, set()))


@pytest.mark.parametrize("stem", _paired_stems())
def test_cross_dialect_equivalence(stem: str):
    """The same board expressed in v9 and v10 must have identical semantic
    views (numbers are file-local handles; names are the semantics)."""
    v9 = _load(FILEFORMATS_PATH / "v9" / "pcb" / f"{stem}.kicad_pcb")
    v10 = _load(FILEFORMATS_PATH / "v10" / "pcb" / f"{stem}.kicad_pcb")
    assert semantic_view_json(v9.kicad_pcb) == semantic_view_json(v10.kicad_pcb)
