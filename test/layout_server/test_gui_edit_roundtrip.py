# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
P0.2 GUI-edit roundtrip acceptance — the demo gate (landed at S6c per the S7
plan; this gate does not depend on the S7 write-dialect flag day because
dumps() follows the file's version (S3) and the v10 fixtures are already v10).

Before atopile can demo the interactive manual-routing workflow in the KiCad 10
GUI, we must prove that an *edit* (not just a re-save) made through the managed
path survives a rebuild without losing anything else.

The re-save oracle (test_v10_acceptance::test_oracle_kicad_resave_keeps_
semantics) proves "write our v10 output unchanged → KiCad reads it back
identically". This test proves the stronger, demo-relevant property: a GUI move
through the managed PcbManager path persists AND every other construct (groups,
zones, nets, the untouched footprints, every pad's net) stays intact AND the
rewritten board is still a v10 file KiCad-cli can run DRC on. Edit ≠ re-save.

The fidelity set under test = the minimal set decided 2026-06-13: footprints,
manually-named groups, zones (the base is an input corpus sample with 6
footprints, 2 zones and a via; the manual user group is created by the test rig,
not baked into a committed generated board — see the work_board fixture). The
teardrop / via padstack / generated meander constructs have since graduated to
the fidelity set (schema-complete: test_padstack_dialect.py,
test_generated_dialect.py); the warning mechanism itself stays load-bearing for
future/unknown keys — the last test pins that an unmodeled key inside such a
construct is reported loudly (no silent failure) rather than dropped.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from atopile.layout_server.pcb_manager import PcbManager
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import semantic_view
from faebryk.libs.test.fileformats import FILEFORMATS_PATH

# A committed *input* corpus sample (an external KiCad board, present across
# v8/v9/v10) — NOT an atopile-generated artifact. 6 footprints, 2 zones, 1 via,
# and crucially ZERO groups, so the only group on the board is the one the test
# rig creates below: group provenance is unambiguous and the test owns it.
BASE_SAMPLE = FILEFORMATS_PATH / "v10" / "pcb" / "test.kicad_pcb"

# The user group the RIG creates (simulating manual grouping in the KiCad GUI;
# kicad-cli has no group-create command, so the rig builds it via the fileformats
# API). Its survival across a managed edit is the property under test.
MANUAL_GROUP = "user_drawn_group"

NEEDS_KICAD_CLI = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)


def _net_assignments(view: dict) -> set[tuple[str, str, str]]:
    """Every (footprint ref, pad name, net) — the copper-connectivity fingerprint
    that an edit must never silently change."""
    return {
        (fp["reference"], pad["name"], pad["net"])
        for fp in view["footprints"]
        for pad in fp["pads"]
    }


def _refs(view: dict) -> set[tuple[str, str]]:
    return {(fp["reference"], fp["name"]) for fp in view["footprints"]}


@pytest.fixture
def work_board(tmp_path) -> Path:
    """A board the test fully controls and can regenerate: the input corpus sample
    copied to tmp, into which the RIG creates a manual user group via the
    fileformats API (the KiCad-GUI manual-grouping action, which kicad-cli cannot
    perform). The group is the test's own construct — nothing relies on a frozen,
    baked-in group in a committed generated board."""
    work = tmp_path / "board.kicad_pcb"
    shutil.copy2(BASE_SAMPLE, work)

    bf = kicad.loads(kicad.pcb.PcbFile, work.read_text())
    pcb = bf.kicad_pcb
    pcb.groups.append(
        kicad.pcb.Group(
            name=MANUAL_GROUP,
            members=[pcb.footprints[0].uuid],
            uuid=kicad.gen_uuid(),
            locked=False,
        )
    )
    work.write_text(kicad.dumps(bf))
    return work


def test_managed_move_persists_and_loses_nothing(work_board: Path):
    mgr = PcbManager()
    mgr.load(work_board)
    before = semantic_view(mgr.pcb)

    target = mgr.get_footprints()[0]
    new_x, new_y = target.x + 5.0, target.y + 7.0
    mgr.move_footprint(target.uuid, new_x, new_y)
    mgr.save()

    # the managed rewrite must stay in the v10 dialect (no accidental downgrade)
    assert "(version 20260206)" in work_board.read_text()

    # a fresh rebuild reads what we wrote back from disk
    reloaded = kicad.loads(kicad.pcb.PcbFile, work_board.read_text())
    after = semantic_view(reloaded.kicad_pcb)

    # 1. the edit persisted
    mgr2 = PcbManager()
    mgr2.load(work_board)
    moved = next(f for f in mgr2.get_footprints() if f.uuid == target.uuid)
    assert moved.x == pytest.approx(new_x)
    assert moved.y == pytest.approx(new_y)

    # 2. nothing else was lost: every non-positional construct is identical
    assert after["groups"] == before["groups"]  # incl. the rig-created user group
    assert after["zones"] == before["zones"]
    assert after["nets"] == before["nets"]
    assert after["segments"] == before["segments"]
    assert after["arcs"] == before["arcs"]
    assert after["vias"] == before["vias"]

    # 3. no footprint dropped, no copper disconnected by the edit
    assert _refs(after) == _refs(before)
    assert _net_assignments(after) == _net_assignments(before)


def test_managed_move_leaves_grouped_footprint_in_its_group(work_board: Path):
    """A grouped footprint moved through the managed path keeps its group
    membership (groups reference members by UUID, which the move preserves)."""
    mgr = PcbManager()
    mgr.load(work_board)
    members_before = {g.name or "": sorted(g.members) for g in mgr.pcb.groups}
    assert MANUAL_GROUP in members_before

    # move a footprint that belongs to a group
    grouped_uuids = {u for ms in members_before.values() for u in ms}
    target = next(
        f for f in mgr.get_footprints() if f.uuid in grouped_uuids
    )
    mgr.move_footprint(target.uuid, target.x + 3.0, target.y - 2.0)
    mgr.save()

    reloaded = kicad.loads(kicad.pcb.PcbFile, work_board.read_text())
    members_after = {g.name or "": sorted(g.members) for g in reloaded.kicad_pcb.groups}
    assert members_after == members_before


@NEEDS_KICAD_CLI
def test_managed_edit_keeps_board_drc_runnable(work_board: Path):
    """KiCad-cli must still read+DRC the board after a managed edit — proves the
    edited v10 output is a file KiCad can load (violations are fine; an
    unreadable file is not)."""
    mgr = PcbManager()
    mgr.load(work_board)
    target = mgr.get_footprints()[0]
    mgr.move_footprint(target.uuid, target.x + 5.0, target.y + 7.0)
    mgr.save()

    report = work_board.parent / "drc.json"
    proc = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(report),
         str(work_board)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"kicad-cli could not process our edited v10 board:\n{proc.stderr}"
    )
    json.loads(report.read_text())


# A v10 via carrying a teardrops block with one key the schema does not model —
# stands in for a v10 shape variation of a warning-set construct. The contract
# (P0.2 S5a, BACKLOG no-silent-failure decision) is that such a key is reported
# loudly, never dropped without trace.
_V10_VIA_WITH_UNMODELED_TEARDROP_KEY = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general (thickness 1.6))
\t(via
\t\t(at 5 5)
\t\t(size 0.6)
\t\t(drill 0.3)
\t\t(layers "F.Cu" "B.Cu")
\t\t(net "GND")
\t\t(teardrops
\t\t\t(best_length_ratio 0.5)
\t\t\t(max_length 1.0)
\t\t\t(best_width_ratio 0.5)
\t\t\t(max_width 1.0)
\t\t\t(curved_edges no)
\t\t\t(filter_ratio 0.9)
\t\t\t(v10_teardrop_shape_variation 1)
\t\t)
\t\t(uuid "00000000-0000-0000-0000-0000000000aa")
\t)
)
"""


def test_warning_set_construct_is_loud_not_silent():
    """No-silent-failure guarantee for the warning set: a key we don't model
    inside a teardrops block surfaces in last_unknown_keys (and a warning),
    rather than vanishing on the next rewrite."""
    kicad.loads(kicad.pcb.PcbFile, _V10_VIA_WITH_UNMODELED_TEARDROP_KEY)
    assert any(
        "v10_teardrop_shape_variation" in k for k in kicad.last_unknown_keys
    ), kicad.last_unknown_keys
