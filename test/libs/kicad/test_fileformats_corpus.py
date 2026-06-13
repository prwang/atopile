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

Since P0.2 S7 (flag day, Option B) atopile writes the v10 dialect for every
board — v9 is read-only and is *upgraded on write*. So gates 3 and 4 are v10-only:
a v9 board re-emitted as v10 differs structurally (no net table, name-only refs),
which is a deliberate dialect change, not data loss. The v9 read path is instead
guarded by:
  - test_v9_upgrade_preserves_semantics (load v9 → write v10 → reload → the
    semantic view is unchanged: the upgrade loses nothing that matters);
  - test_cross_dialect_equivalence (v9 and v10 of the same board agree);
  - the semantic-view snapshots (read fidelity for every dialect).
Any new v10 schema gap surfaces as a gate-3 failure here and a loud warning at
load time (test_unknown_key_loudness.py).
"""

import os
import re
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import semantic_view_json
from faebryk.libs.test import sexp_tree
from faebryk.libs.test.fileformats import FILEFORMATS_PATH, all_pcb_fixtures

# Boards whose bytes were last written by atopile/our writer (not KiCad). The
# v10 fixtures were re-derived from our writer at S5b; both dialects of these
# stems are byte-stable round-trips.
ATOPILE_AUTHORED_STEMS = {"test", "layout_reuse_top"}

SNAPSHOT_DIR = FILEFORMATS_PATH / "snapshots"
REGEN = os.environ.get("REGEN_SEMANTIC_SNAPSHOTS") == "1"


def _params(extra_marks=lambda version, path: []):
    for version, path in all_pcb_fixtures():
        marks = list(extra_marks(version, path))
        yield pytest.param(version, path, id=f"v{version}-{path.stem}", marks=marks)


def _v10_params():
    """Only the v10 fixtures. The write dialect is v10 (S7), so byte fidelity and
    structural no-data-loss are only meaningful when input and output share it."""
    for version, path in all_pcb_fixtures():
        if version == 10:
            yield pytest.param(version, path, id=f"v{version}-{path.stem}")


def _v9_params():
    for version, path in all_pcb_fixtures():
        if version == 9:
            yield pytest.param(version, path, id=f"v{version}-{path.stem}")


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


@pytest.mark.parametrize(("version", "path"), _v10_params())
def test_no_data_loss(version: int, path: Path):
    """A v10 board re-emitted as v10 drops no schema field (P0.2 S5b complete).
    The remaining round-trip gap, if any, is float/quote formatting, which
    data_loss() normalizes away — a real drop shows as a per-field entry.
    v10-only: a v9 board is upgraded on write (S7), so raw-vs-dump structural
    diff there is dialect change, not loss (see test_v9_upgrade_preserves_
    semantics)."""
    raw = path.read_text()
    dump = kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))
    loss = sexp_tree.data_loss(raw, dump)
    assert not loss, (
        f"schema silently dropped data from {path.name}:\n"
        f"{sexp_tree.summarize_loss(loss)}"
    )


@pytest.mark.parametrize(
    ("version", "path"),
    [p for p in _v10_params() if Path(str(p.values[1])).stem in ATOPILE_AUTHORED_STEMS],
)
def test_byte_fidelity(version: int, path: Path):
    """Atopile-authored v10 boards round-trip byte-for-byte. The v10 fixtures
    were re-derived from our own writer (kicad-cli's formatting is a non-goal);
    KiCad reads them losslessly (test_v10_acceptance oracles). v10-only: the
    write dialect is v10, so v9 input never reproduces its own bytes (S7)."""
    raw = path.read_text()
    assert raw == kicad.dumps(kicad.loads(kicad.pcb.PcbFile, raw))


@pytest.mark.parametrize(("version", "path"), _v9_params())
def test_v9_upgrade_preserves_semantics(version: int, path: Path):
    """The v9 read path's safety net under S7 upgrade-on-write: reading a v9
    board, writing it (as v10), and reading it back must leave the name-based
    semantic view unchanged. Structural bytes change (dialect upgrade); meaning
    does not."""
    original = _load(path)
    before = semantic_view_json(original.kicad_pcb)

    upgraded_text = kicad.dumps(original)
    reloaded = kicad.loads(kicad.pcb.PcbFile, upgraded_text)

    assert int(re.search(r"\(version (\d+)\)", upgraded_text).group(1)) >= 20250000
    assert semantic_view_json(reloaded.kicad_pcb) == before


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
