"""
Regression tests for byte-level determinism of group members in .kicad_pcb.

Root cause (fixed in layout_sync.py:pull_group_layout): newly pulled element
UUIDs were appended to the already-sorted member list in Python set-iteration
order, which varies per process with PYTHONHASHSEED. The next build's
sync_groups re-sorted the full list, so the build *after* a pull differed from
the pull build in member order only.

These tests force different hash seeds across consecutive builds so any
unsorted set-iteration path shows up as a byte diff.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

EXAMPLE = _repo_root() / "examples" / "layout_reuse"
TOP_PCB = Path("layout") / "top" / "top.kicad_pcb"


def _build(cwd: Path, hashseed: str) -> None:
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-v"],
        env={
            **os.environ,
            "NONINTERACTIVE": "1",
            "PYTHONHASHSEED": hashseed,
        },
        cwd=cwd,
        stdout=print,
        stderr=print,
        timeout=300,
    )
    assert "Build successful! 🚀" in stdout + stderr


def _load_pcb(pcb_path: Path) -> "kicad.pcb.PcbFile":
    # parse from text so these tests are independent of the loads Path cache
    return kicad.loads(kicad.pcb.PcbFile, pcb_path.read_text())


def _assert_members_sorted(pcb_path: Path) -> None:
    pcb_file = _load_pcb(pcb_path)
    pcb = pcb_file.kicad_pcb
    for group in pcb.groups:
        assert list(group.members) == sorted(group.members), (
            f"group {group.name!r} members not sorted"
        )


@pytest.fixture
def example_copy(tmp_path: Path) -> Path:
    copy = tmp_path / EXAMPLE.name
    shutil.copytree(EXAMPLE, copy)
    # Seed the part cache so builds never hit the EasyEDA API (rate-limited
    # 403s would make these determinism tests flaky in CI).
    cache_fixture = _repo_root() / "test" / "common" / "resources" / "easyeda-cache"
    shutil.copytree(cache_fixture, copy / "build" / "cache" / "parts" / "easyeda")
    return copy


def test_fresh_build_deterministic(
    example_copy: Path, save_tmp_path_on_failure: None
):
    """First build runs pull_group_layout (top layout absent in the example);
    second build re-sorts via sync_groups. Both must serialize identically."""
    assert not (example_copy / TOP_PCB).exists(), (
        "fixture must start without a top layout to exercise the pull path"
    )

    _build(example_copy, hashseed="0")
    first = (example_copy / TOP_PCB).read_bytes()
    _assert_members_sorted(example_copy / TOP_PCB)

    _build(example_copy, hashseed="1")
    second = (example_copy / TOP_PCB).read_bytes()

    assert first == second, "fresh build then rebuild must be byte-identical"


def test_steady_state_and_incremental_add_deterministic(
    example_copy: Path, save_tmp_path_on_failure: None
):
    """Steady-state rebuilds stay identical; adding a module instance triggers
    the pull path for the new group only, and the next build must not move
    bytes. Pre-existing designators must survive the addition
    (keep_designators default)."""
    _build(example_copy, hashseed="0")
    baseline = (example_copy / TOP_PCB).read_bytes()

    # steady state
    _build(example_copy, hashseed="1")
    assert (example_copy / TOP_PCB).read_bytes() == baseline

    pcb_file_before = _load_pcb(example_copy / TOP_PCB)
    refs_before = {
        Property.try_get_property(fp.propertys, "Reference")
        for fp in pcb_file_before.kicad_pcb.footprints
    }

    # incremental: add a 4th Sub instance
    src = example_copy / "layout_reuse.ato"
    src.write_text(src.read_text().replace("new Sub[3]", "new Sub[4]"))

    _build(example_copy, hashseed="0")
    after_add = (example_copy / TOP_PCB).read_bytes()
    _assert_members_sorted(example_copy / TOP_PCB)

    _build(example_copy, hashseed="1")
    assert (example_copy / TOP_PCB).read_bytes() == after_add, (
        "build after adding an instance must already be in canonical order"
    )

    pcb_file_after = _load_pcb(example_copy / TOP_PCB)
    refs_after = {
        Property.try_get_property(fp.propertys, "Reference")
        for fp in pcb_file_after.kicad_pcb.footprints
    }
    assert refs_before <= refs_after, (
        f"pre-existing designators changed: {refs_before - refs_after}"
    )


MANUAL_SEG_UUID = "11111111-2222-3333-4444-555555555555"
MANUAL_GROUP_UUID = "99999999-8888-7777-6666-555555555555"
MANUAL_ZONE_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _manual_injection(net_number: int) -> str:
    """A manually routed segment, a user-named group containing it, and a
    placement rule area — the kinds of edits a human makes in KiCad."""
    return f"""\t(segment
\t\t(start 95 95)
\t\t(end 100 95)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net {net_number})
\t\t(uuid "{MANUAL_SEG_UUID}")
\t)
\t(group "manual_human_group"
\t\t(uuid "{MANUAL_GROUP_UUID}")
\t\t(locked no)
\t\t(members "{MANUAL_SEG_UUID}")
\t)
\t(zone
\t\t(net 0)
\t\t(net_name "")
\t\t(layers "F.Cu" "B.Cu")
\t\t(uuid "{MANUAL_ZONE_UUID}")
\t\t(name "manual_room")
\t\t(hatch edge 0.5)
\t\t(keepout
\t\t\t(tracks allowed)
\t\t\t(vias allowed)
\t\t\t(pads allowed)
\t\t\t(copperpour not_allowed)
\t\t\t(footprints allowed)
\t\t)
\t\t(placement
\t\t\t(enabled yes)
\t\t)
\t\t(fill
\t\t\t(thermal_gap 0.5)
\t\t\t(thermal_bridge_width 0.5)
\t\t)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 90 90) (xy 130 90) (xy 130 120) (xy 90 120)
\t\t\t)
\t\t)
\t)
"""


def test_manual_edits_preserved(example_copy: Path, save_tmp_path_on_failure: None):
    """Manual segments, user-named groups (incl. their member contents) and
    rule areas must survive rebuilds. Regression for the over-broad group
    cleanup that deleted the contents of every non-atopile named group."""
    _build(example_copy, hashseed="0")
    pcb_path = example_copy / TOP_PCB

    pcb_file = _load_pcb(pcb_path)
    net_number = next(
        n.number for n in pcb_file.kicad_pcb.nets if n.number != 0
    )

    txt = pcb_path.read_text()
    closing = txt.rfind(")")
    pcb_path.write_text(
        txt[:closing] + _manual_injection(net_number) + txt[closing:]
    )

    _build(example_copy, hashseed="1")

    rebuilt_file = _load_pcb(pcb_path)
    rebuilt = rebuilt_file.kicad_pcb
    assert any(s.uuid == MANUAL_SEG_UUID for s in rebuilt.segments), (
        "manual segment inside a user group was deleted by the rebuild"
    )
    manual_groups = [g for g in rebuilt.groups if g.uuid == MANUAL_GROUP_UUID]
    assert manual_groups and MANUAL_SEG_UUID in manual_groups[0].members
    zones = [z for z in rebuilt.zones if z.uuid == MANUAL_ZONE_UUID]
    assert zones and zones[0].name == "manual_room"
    assert zones[0].placement is not None and zones[0].placement.enabled

    # manual elements must not disturb steady-state byte determinism
    stable = pcb_path.read_bytes()
    _build(example_copy, hashseed="0")
    assert pcb_path.read_bytes() == stable


def _groups_from_text(pcb_path: Path) -> dict[str, set[str]]:
    """Text-level group extraction. The file written by `kicad-cli pcb
    upgrade` uses the current KiCad 10 format (e.g. nested `(tenting ...)`),
    which the Zig schema (pinned to version 20241229) cannot parse — so the
    upgraded side of the round trip must be compared at text level. Corollary:
    never run `pcb upgrade` on boards atopile still manages."""
    import re

    txt = pcb_path.read_text()
    out: dict[str, set[str]] = {}
    for m in re.finditer(r'\(group "([^"]*)"', txt):
        depth = 0
        for j in range(m.start(), len(txt)):
            if txt[j] == "(":
                depth += 1
            elif txt[j] == ")":
                depth -= 1
                if depth == 0:
                    break
        block = txt[m.start() : j + 1]
        members = re.search(r"\(members((?:\s+\"[0-9a-f-]+\")+)\s*\)", block)
        out[m.group(1)] = (
            set(re.findall(r'"([0-9a-f-]+)"', members.group(1))) if members else set()
        )
    return out


@pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="requires kicad-cli"
)
def test_kicad_upgrade_roundtrip_preserves_groups(
    example_copy: Path, save_tmp_path_on_failure: None
):
    """`kicad-cli pcb upgrade` must preserve group membership (order may be
    KiCad's own; membership sets are what we rely on)."""
    _build(example_copy, hashseed="0")

    before = _groups_from_text(example_copy / TOP_PCB)
    run_live(
        ["kicad-cli", "pcb", "upgrade", str(example_copy / TOP_PCB)],
        stdout=print,
        stderr=print,
        timeout=120,
    )
    after = _groups_from_text(example_copy / TOP_PCB)

    assert before == after
