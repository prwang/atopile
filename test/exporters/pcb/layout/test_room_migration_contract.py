# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_room_migration_contract — stage C3 acceptance contract (BACKLOG §C3).

== THE PROTOCOL (revised) ==================================================

C3 retires KiCad *groups* as the room/multi-channel primitive and migrates room
identity onto each footprint's *sheet path* — KiCad's own source-correspondence
channel. After C3 **atopile creates zero groups and deletes zero groups**; the
A4 "manual group silently deleted" failure mode therefore stops existing by
construction (there is no atopile group code left to over-reach).

Room model:
  * room IDENTITY  = footprint `sheetname` (+ `sheetfile`), synthesized at build
    time = exactly the old `_get_group_name(sub_addr, fp)` value, e.g.
    "sub_chains[0]"; the full instance address, so it stays globally unique — a
    leaf name would collide. KiCad preserves `sheetname`/`sheetfile` VERBATIM
    through `pcb upgrade` even with no .kicad_sch (C3.4). It does NOT preserve
    `path`: KiCad owns the sheet-instance path and rewrites it to a fresh UUID on
    upgrade (measured), so atopile must NOT write `path` or rely on it — the room
    key is `sheetname`, which is also what `(placement (sheetname))` matches.
  * room MEMBERSHIP (footprints) = the footprints sharing a `sheetname`. This is
    exactly the address-prefix grouping the legacy group encoded (C3.1 proves the
    equivalence on the committed corpus before any code is deleted).
  * room MEMBERSHIP (tracks/vias/zones) is NOT carried by membership any more
    (today a group lists routes as members — see the 5-members-vs-3-footprints
    discrepancy in layout_reuse_top). It is derived: a piece of copper belongs to
    a room iff it sits on an *intra-room net* — a net all of whose pads are on
    that room's footprints. Inter-room nets (pads in two rooms) belong to NEITHER
    room and must survive either room's clean (C3.9 — the one genuinely new rule).
  * rule areas use `(placement (sheetname "<addr>"))` — no zig change
    (ZonePlacement.sheetname already exists, pcb.zig:828); this is a §D concern.

== THE INTERFACE DELTA (what C3 lands) ====================================

layout_ir.py / layout_ir.schema.json  (= B re-opened, BACKLOG C3.5):
    -  ir["groups"] {name: {uuid, members:[uuid], member_addrs:[addr]}}
    +  ir["rooms"]  {name: {sheetname:str, member_addrs:[addr]}}   # NO uuid /
                                                                   # NO provenance
    schema $id bumps (v1 -> v2); `groups` removed from properties, `rooms`
    required; additionalProperties:false keeps biting a stray `groups` key.

layout_sync.py:
    -  sync_groups()                 + sync_rooms()        # writes sheetname/path
                                                           # onto footprints,
                                                           # creates NO group
    -  pull_group_layout(name)       + pull_room_layout(name)
    -  _calculate_group_offset(src,g)+ _calculate_room_offset(src, room_name)
    -  _clean_group(name)            + _clean_room(name)   # deletes by intra-room
                                                           # net, not membership
    -  _is_managed_group()           (deleted — the gen_uuid name-overflow hack's
                                      last live reader; BACKLOG 遗留3)
       _get_group_name()             (kept — already address-derived; becomes the
                                      sheetname value)

transformer.py:  insert_group() retired.

== THE RATCHET (S0 discipline, mirrors test_layout_ir_contract / room_ops) ==

`_C3_LANDED` is a pure symbol probe: C3 renames pull_group_layout ->
pull_room_layout, so the new name present AND the old name absent == "landed"
(a half-rename leaves it "not landed" and keeps the ratchet red — a feature).

Two tests run GREEN NOW — they are pre-migration de-risking, not ratchets:
  * C3.1 proves the address derivation reproduces the legacy groups on the real
    corpus (so deleting the group path loses no information). It reads whichever
    era's IR shape is present, so it stays green across the cutover.
  * C3.4 is the probe formalized: it proves stock KiCad preserves synthesized
    sheetname/path + (placement (sheetname)) with no schematic and a >16-byte
    name. It tests KiCad, not atopile, so it is era-independent.

The rest are strict-xfail: red until C3 lands, flipped green by the C3 commit,
which in the SAME commit deletes the now-superseded green tests:
  - test_layout_ir_contract.py::test_I7_group_members_match_pcb         (-> C3.5a)
  - test_group_determinism.py (whole file)              (-> test_room_migration_e2e)
  - the group asserts in test_gui_edit_roundtrip.py     (-> address asserts)

NOT pinned here, on purpose (no silent cap — S5a discipline):
  * C3.2 / C3.3 (atopile-creates-no-groups; determinism-through-pull) and the
    offset-equivalence half of C3.8 need a real build (sub-address + source-pcb
    resolution that cannot be faithfully reproduced inline) — they live in
    test/end_to_end/test_room_migration_e2e.py against the layout_reuse example.
  * C3.7 (one-time fixture/example group-strip) is an implementation ACTION
    gated by C3.1 going green on the migrated fixtures, not a separate test.
"""

import copy as _copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.layout.layout_sync import LayoutSync
from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.kicad.layout_ir import layout_ir, layout_ir_schema
from faebryk.libs.test.fileformats import FILEFORMATS_PATH

# ---------------------------------------------------------------------------
# S0 ratchet guard: C3 is one atomic flag-day. Probe the layout_sync rename.
# ---------------------------------------------------------------------------
_C3_LANDED = hasattr(LayoutSync, "pull_room_layout") and not hasattr(
    LayoutSync, "pull_group_layout"
)
needs_c3 = pytest.mark.xfail(
    not _C3_LANDED,
    reason="C3 room migration (group -> sheetname/path) not landed (S0 ratchet)",
    strict=True,
)

V10_PCB_DIR = FILEFORMATS_PATH / "v10" / "pcb"
V9_PCB_DIR = FILEFORMATS_PATH / "v9" / "pcb"
_CORPUS = sorted(
    {p.stem for p in V10_PCB_DIR.glob("*.kicad_pcb")}
    | {p.stem for p in V9_PCB_DIR.glob("*.kicad_pcb")}
)


# ===========================================================================
# inline board builder (same net-synthesis rule as the B/C contracts; adds
# optional per-footprint `sheetname` — the C3 room carrier).
# ===========================================================================


def _pad(name, net, *, uuid=None, at=(0.0, 0.0, 0.0), layer="F.Cu"):
    return {"name": name, "net": net, "uuid": uuid, "at": at, "layer": layer}


def _fp(addr, pads, *, ref=None, uuid=None, at=(0.0, 0.0, 0.0), layer="F.Cu", sheetname=None):
    return {
        "addr": addr, "pads": pads, "ref": ref, "uuid": uuid, "at": at,
        "layer": layer, "sheetname": sheetname,
    }


def _seg(net, start, end, *, uuid=None, layer="F.Cu"):
    return {"net": net, "start": start, "end": end, "uuid": uuid, "layer": layer}


def _board(fps, *, groups=(), segments=(), version=20241229) -> str:
    names = sorted(
        {p["net"] for fp in fps for p in fp["pads"]} | {s["net"] for s in segments}
    )
    names = [n for n in names if n != ""]
    number = {"": 0, **{n: i + 1 for i, n in enumerate(names)}}
    auto = [0]

    def _uuid(given, tag):
        if given is not None:
            return given
        auto[0] += 1
        return f"00000000-0000-0000-0000-{tag}{auto[0]:08x}"[:36]

    lines = [
        "(kicad_pcb",
        f"\t(version {version})",
        '\t(generator "test_room_migration")',
        '\t(generator_version "10.0")',
        "\t(general (thickness 1.6))",
        '\t(layers (0 "F.Cu" signal) (2 "B.Cu" signal))',
    ]
    for n in sorted(number, key=lambda k: number[k]):
        lines.append(f'\t(net {number[n]} "{n}")')

    for fp in fps:
        fp_uuid = _uuid(fp["uuid"], "fab")
        ax, ay, ar = (list(fp["at"]) + [0.0, 0.0, 0.0])[:3]
        ref = fp["ref"] if fp["ref"] is not None else fp["addr"].rsplit(".", 1)[-1]
        lines += [
            '\t(footprint "test:FP"',
            f'\t\t(layer "{fp["layer"]}")',
            f'\t\t(uuid "{fp_uuid}")',
            f"\t\t(at {ax} {ay} {ar})",
        ]
        if fp["sheetname"] is not None:
            lines += [
                f'\t\t(sheetname "{fp["sheetname"]}")',
                f'\t\t(sheetfile "{fp["sheetname"]}.kicad_sch")',
            ]
        lines += [
            f'\t\t(property "Reference" "{ref}" (at 0 0)'
            ' (layer "F.SilkS") (effects (font (size 1 1))))',
            f'\t\t(property "atopile_address" "{fp["addr"]}" (at 0 0)'
            ' (layer "F.Fab") (effects (font (size 1 1))))',
        ]
        for p in fp["pads"]:
            pad_uuid = _uuid(p["uuid"], "fad")
            px, py, pr = (list(p["at"]) + [0.0, 0.0, 0.0])[:3]
            net_sexp = f'(net {number[p["net"]]} "{p["net"]}")' if p["net"] != "" else ""
            lines += [
                f'\t\t(pad "{p["name"]}" smd rect',
                f"\t\t\t(at {px} {py} {pr})",
                "\t\t\t(size 1 1)",
                f'\t\t\t(layers "{p["layer"]}")',
                f"\t\t\t{net_sexp}",
                f'\t\t\t(uuid "{pad_uuid}")',
                "\t\t)",
            ]
        lines.append("\t)")

    for s in segments:
        seg_uuid = _uuid(s["uuid"], "5e9")
        lines += [
            "\t(segment",
            f'\t\t(start {s["start"][0]} {s["start"][1]})',
            f'\t\t(end {s["end"][0]} {s["end"][1]})',
            "\t\t(width 0.2)",
            f'\t\t(layer "{s["layer"]}")',
            f'\t\t(net {number[s["net"]]})',
            f'\t\t(uuid "{seg_uuid}")',
            "\t)",
        ]

    for gname, guuid, members in groups:
        member_sexp = " ".join(f'"{m}"' for m in members)
        lines += [
            "\t(group",
            f'\t\t"{gname}"',
            f'\t\t(uuid "{guuid}")',
            f"\t\t(members {member_sexp})",
            "\t)",
        ]

    lines.append(")")
    return "\n".join(lines) + "\n"


def _load(text: str) -> kicad.pcb.PcbFile:
    return kicad.loads(kicad.pcb.PcbFile, text)


# ---------------------------------------------------------------------------
# era-bridging helpers so the GREEN-NOW tests survive the cutover unchanged
# ---------------------------------------------------------------------------


def _rooms_member_addrs(ir) -> dict[str, set[str]]:
    """{room name -> set(member ato addresses)}, reading whichever era's IR is
    present: native ir['rooms'] post-C3, else the address-bearing groups in
    ir['groups'] (a manual group has member_addrs == [] and self-excludes)."""
    if "rooms" in ir:
        return {name: set(r["member_addrs"]) for name, r in ir["rooms"].items()}
    return {
        name: set(g["member_addrs"])
        for name, g in ir.get("groups", {}).items()
        if g["member_addrs"]
    }


def _prefix_members(ir, room_name: str) -> set[str]:
    """Managed components whose address is room_name or under room_name + '.'."""
    return {
        a for a in ir["components"]
        if a == room_name or a.startswith(room_name + ".")
    }


# ===========================================================================
# C3.1 — MIGRATION EQUIVALENCE (GREEN NOW). The keystone safety net: the
# address-prefix derivation reproduces the legacy group membership exactly, so
# the group path can be deleted without losing information. Era-bridging, so it
# also becomes the permanent "rooms == address-prefix grouping" invariant.
# ===========================================================================


def test_C3_1_inline_room_equals_address_prefix_grouping():
    # post-migration carrier: room identity is the footprint sheetname (= the
    # address prefix), not a KiCad group.
    bf = _load(
        _board(
            [
                _fp("top.mod_a.r1", [_pad("1", "VCC")], sheetname="top.mod_a"),
                _fp("top.mod_a.r2", [_pad("1", "GND")], sheetname="top.mod_a"),
                _fp("top.mod_b.r1", [_pad("1", "GND")], sheetname="top.mod_b"),
            ]
        )
    )
    ir = layout_ir(bf.kicad_pcb)
    rooms = _rooms_member_addrs(ir)
    assert rooms == {
        "top.mod_a": {"top.mod_a.r1", "top.mod_a.r2"},
        "top.mod_b": {"top.mod_b.r1"},
    }
    # and each room is exactly the address-prefix grouping (the C3 derivation)
    for name, members in rooms.items():
        assert members == _prefix_members(ir, name), (
            f"room {name!r} membership diverges from its address prefix"
        )


@pytest.mark.parametrize("stem", _CORPUS)
def test_C3_1_corpus_groups_are_address_prefix_recoverable(stem):
    """On every committed fixture: each atopile room (a legacy GROUP whose members
    carry addresses, read straight from the pcb) is EXACTLY the managed footprints
    under its name's address prefix. This is the migration safety net — it proves
    group -> sheetname loses no information — and it reads the legacy groups
    directly (the fixtures keep them: atopile must still parse group-bearing
    boards), so it needs no fixture rewrite. A manual user group (members with no
    atopile_address) self-excludes."""
    path = (V10_PCB_DIR / f"{stem}.kicad_pcb")
    if not path.exists():
        path = V9_PCB_DIR / f"{stem}.kicad_pcb"
    pcb = _load(path.read_text()).kicad_pcb
    ir = layout_ir(pcb)
    addr_by_uuid = {c["footprint_uuid"]: a for a, c in ir["components"].items()}

    legacy: dict[str, set[str]] = {}
    for g in pcb.groups:
        if not g.name:
            continue
        addrs = {a for m in g.members if (a := addr_by_uuid.get(m)) is not None}
        if addrs:  # an address-bearing (atopile room) group
            legacy[g.name] = addrs
    if not legacy:
        pytest.skip(f"{stem}: no atopile room groups")

    for name, members in legacy.items():
        assert members == _prefix_members(ir, name), (
            f"{stem}: group {name!r} members {sorted(members)} != address-prefix "
            f"{sorted(_prefix_members(ir, name))} — group->sheetname would lose info"
        )


# ===========================================================================
# C3.4 — KiCad CROSS-VALIDATION (GREEN NOW, needs kicad-cli). The probe
# formalized: synthesized sheetname/sheetfile on footprints with NO .kicad_sch +
# a (placement (sheetname)) rule area, >16-byte room name, survive
# `kicad-cli pcb upgrade --force` VERBATIM and add no DRC violations. Measured
# corollary pinned here: KiCad does NOT preserve a synthesized `path` (it rewrites
# the sheet-instance path to a fresh UUID) — so the room key is sheetname, never
# path, and atopile must not write path.
# ===========================================================================

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
needs_kicad_cli = pytest.mark.skipif(not _HAS_KICAD_CLI, reason="requires kicad-cli")

# 31 bytes — the exact length that overflowed the legacy group-name-in-uuid hack
ROOM = "probe_room_top.power_supply_3v3"


def _drc_violation_count(pcb_path: Path, report: Path) -> int:
    """Number of DRC violations, via the JSON report written to `report` (in the
    test's tmp dir — never cwd, so the test leaves no artifacts behind). Counting
    violations makes the neutrality check real: a bare `drc` exits 0 regardless,
    so comparing return codes would be vacuous."""
    subprocess.run(
        ["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(report), str(pcb_path)],
        capture_output=True, text=True, timeout=180,
    )
    return len(json.loads(report.read_text()).get("violations", []))


@needs_kicad_cli
def test_C3_4_kicad_preserves_sheetpath_and_placement(tmp_path):
    src = V10_PCB_DIR / "test.kicad_pcb"
    bf = _load(src.read_text())
    pcb = bf.kicad_pcb
    assert pcb.footprints and pcb.zones, "fixture must have a footprint and a zone"

    # stamp a synthesized sheet identity on every footprint (no .kicad_sch
    # exists). Deliberately also set `path` to prove KiCad rewrites it (not us).
    for fp in pcb.footprints:
        fp.sheetname = ROOM
        fp.sheetfile = ROOM + ".kicad_sch"
        fp.path = "/" + ROOM
    # turn an existing zone into a sheetname-sourced placement rule area
    pcb.zones[0].placement = kicad.pcb.ZonePlacement(sheetname=ROOM, enabled=True)

    modified = tmp_path / "modified.kicad_pcb"
    modified.write_text(kicad.dumps(bf))
    # a baseline copy WITHOUT our metadata, to isolate DRC delta
    baseline = tmp_path / "baseline.kicad_pcb"
    baseline.write_text(src.read_text())

    for f in (modified, baseline):
        r = subprocess.run(
            ["kicad-cli", "pcb", "upgrade", "--force", str(f)],
            capture_output=True, text=True, timeout=180,
        )
        assert r.returncode == 0, f"upgrade failed on {f.name}:\n{r.stderr}"

    up = modified.read_text()
    assert "(version 20260206)" in up, "upgrade did not produce the v10 dialect"
    # sheetname survives verbatim on every footprint + the placement; sheetfile too
    assert up.count(f'(sheetname "{ROOM}")') >= len(pcb.footprints)
    assert up.count(f'(sheetfile "{ROOM}.kicad_sch")') >= len(pcb.footprints)
    assert "(placement" in up and f'(sheetname "{ROOM}")' in up
    # KiCad OWNS `path`: a synthesized one is rewritten to a UUID, NOT preserved.
    # This is why the room key is sheetname and atopile must not write path.
    assert f'(path "/{ROOM}")' not in up
    # our metadata introduces no new DRC violations (reports written to tmp_path)
    assert _drc_violation_count(modified, tmp_path / "m.drc.json") == (
        _drc_violation_count(baseline, tmp_path / "b.drc.json")
    ), "synthesized sheet identity / placement changed the DRC violation count"


# ===========================================================================
# C3.5a — IR: rooms are address-derived, carry NO group provenance (xfail).
# Replaces test_layout_ir_contract.py::test_I7_group_members_match_pcb (I7').
# ===========================================================================


@needs_c3
def test_C3_5a_ir_rooms_replace_groups_and_drop_provenance():
    fp_a = "aaaaaaaa-0000-0000-0000-000000000001"
    fp_b = "aaaaaaaa-0000-0000-0000-000000000002"
    bf = _load(
        _board(
            [
                _fp("top.mod.r1", [_pad("1", "VCC")], uuid=fp_a, sheetname="top.mod"),
                _fp("top.mod.r2", [_pad("1", "GND")], uuid=fp_b, sheetname="top.mod"),
            ]
        )
    )
    ir = layout_ir(bf.kicad_pcb)

    assert "groups" not in ir, "C3: the group-shaped key is gone"
    assert "rooms" in ir, "C3: rooms replace groups"
    room = ir["rooms"]["top.mod"]
    assert sorted(room["member_addrs"]) == ["top.mod.r1", "top.mod.r2"]
    assert room["sheetname"] == "top.mod"
    # no group object identity leaks in (the gen_uuid name-overflow hack is gone)
    assert "uuid" not in room and "members" not in room
    # membership is exactly the address-prefix grouping
    assert set(room["member_addrs"]) == _prefix_members(ir, "top.mod")


# ===========================================================================
# C3.5b — schema: v2 requires `rooms`, forbids `groups`, still bites (xfail).
# ===========================================================================


@needs_c3
def test_C3_5b_schema_requires_rooms_forbids_groups():
    schema = layout_ir_schema()
    assert "rooms" in schema["required"]
    assert "groups" not in schema["required"]
    assert "groups" not in schema["properties"]
    assert "rooms" in schema["properties"]
    assert "/v2." in schema["$id"], f"schema $id {schema['$id']!r} not bumped to v2"

    # additionalProperties:false must still reject a stray legacy `groups` key
    import jsonschema

    bf = _load(_board([_fp("top.r1", [_pad("1", "VCC")], sheetname="top")]))
    ir = layout_ir(bf.kicad_pcb)
    jsonschema.validate(ir, schema)  # a real C3 IR validates
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**ir, "groups": {}}, schema)


# ===========================================================================
# C3.9 — CLEAN: a room's clean deletes its intra-room-net copper ONLY; an
# inter-room net and a sibling room's copper survive (xfail). THE keystone for
# the one genuinely new rule. The correct deleted set is known BY CONSTRUCTION,
# so this needs no comparison to the (soon-deleted) old _clean_group.
# ===========================================================================


@needs_c3
def test_C3_9_clean_room_deletes_intra_room_net_only():
    a1 = "aaaa0000-0000-0000-0000-000000000001"
    a2 = "aaaa0000-0000-0000-0000-000000000002"
    b1 = "bbbb0000-0000-0000-0000-000000000001"
    b2 = "bbbb0000-0000-0000-0000-000000000002"
    seg_a = "5e900000-0000-0000-0000-0000000000a1"  # intra-room-A net
    seg_b = "5e900000-0000-0000-0000-0000000000b1"  # intra-room-B net
    seg_x = "5e900000-0000-0000-0000-0000000000cc"  # inter-room (CROSS) net
    bf = _load(
        _board(
            [
                _fp("top.a.r1", [_pad("1", "A_INT"), _pad("2", "CROSS")], uuid=a1, sheetname="top.a"),
                _fp("top.a.r2", [_pad("1", "A_INT")], uuid=a2, sheetname="top.a"),
                _fp("top.b.r1", [_pad("1", "CROSS"), _pad("2", "B_INT")], uuid=b1, sheetname="top.b"),
                _fp("top.b.r2", [_pad("1", "B_INT")], uuid=b2, sheetname="top.b"),
            ],
            segments=[
                _seg("A_INT", (0, 0), (1, 0), uuid=seg_a),
                _seg("B_INT", (5, 0), (6, 0), uuid=seg_b),
                _seg("CROSS", (2, 0), (4, 0), uuid=seg_x),
            ],
        )
    )
    pcb = bf.kicad_pcb
    sync = LayoutSync(pcb)
    sync.__keepalive = bf

    sync._clean_room("top.a")

    remaining = {s.uuid for s in pcb.segments}
    assert seg_a not in remaining, "intra-room-A copper was not cleaned"
    assert seg_x in remaining, "inter-room (CROSS) copper was wrongly deleted"
    assert seg_b in remaining, "sibling room B copper was wrongly deleted"


# ===========================================================================
# C3.10 — COPY: copy_room_layout copies ONLY the named room's intra-room copper,
# never a sibling room's nor an inter-room net's (xfail). Replaces the false
# comfort of C3.6: the current C2 fixtures are single-room, so they stay green
# while the multi-room semantics are wrong (copy_room_layout chains over ALL of
# source_pcb.segments today). A multi-room source exposes it.
# ===========================================================================


@needs_c3
def test_C3_10_copy_room_copies_only_named_room():
    from faebryk.exporters.pcb.layout.room_ops import copy_room_layout

    source = _load(
        _board(
            [
                _fp("s1.r1", [_pad("1", "S1_VCC"), _pad("2", "S1_GND")]),
                _fp("s2.r1", [_pad("1", "S2_VCC"), _pad("2", "S2_GND")]),
            ],
            segments=[
                _seg("S1_VCC", (0, 0), (1, 0)),  # room s1 intra-room
                _seg("S2_VCC", (5, 0), (6, 0)),  # room s2 intra-room (must NOT copy)
            ],
        )
    )
    target = _load(
        _board([_fp("top.s1.r1", [_pad("1", "TOP_VCC"), _pad("2", "TOP_GND")])])
    )

    rc = copy_room_layout(
        target.kicad_pcb, source.kicad_pcb,
        source_prefix="s1", target_prefix="top.s1",
        offset=kicad.pcb.Xy(x=0.0, y=0.0),
    )

    # exactly the one s1 intra-room segment was copied (not s2's)
    assert len(rc.new_objects) == 1, (
        f"copy crossed room boundaries: copied {len(rc.new_objects)} objects, "
        "expected only room s1's intra-room route"
    )
    top_vcc = next(n.number for n in target.kicad_pcb.nets if n.name == "TOP_VCC")
    assert rc.new_objects[0].net == top_vcc, "copied route not rebound to the room net"
