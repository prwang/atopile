# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
room_ops — acceptance contract (BACKLOG §C: forced via / room copy).

S0 discipline (same as B1a): written BEFORE the implementation, every test is a
strict-xfail ratchet. Until `faebryk.exporters.pcb.layout.room_ops` exists the
tests xfail; the C implementation flips them green by *removing* the xfail (it
goes inactive once the module imports). A test that XPASSes while the module is
absent is a bug in the test.

WHY a contract (the user's gate, mirrored from B): §C only consumes B's IR (pad
xy/net + address prefix remap) and feeds §E (the router). If C's output shape is
wrong, §E back-patches C. So the C→E boundary is pinned HERE, reverse-engineered
from how the router actually consumes a board — exactly as B→C was pinned with
the `_generate_net_map` oracle. The router is the C→E consumer-oracle, just as
`_generate_net_map` was the B→C one.

Two tiers (BACKLOG §C "下游契约 + 自测计划"):
  Tier-1 — structural, no router, validated with layout_ir / semantic_view /
           the live LayoutSync._generate_net_map oracle.
  Tier-2 — router-oracle: C's output is handed to the real router
           (KiCadRoutingTools) and must be honored. The router runs in the
           system python3 (its grid_router.so is not built for this venv), so
           Tier-2 shells out — the same invocation the E3 thin slice
           (tests/test_router_smoke_batch_route.py) already proved green.

What stays in §E (NOT a back-patch risk): `blocked_before_forced_via` blocking
analysis is diagnostic quality, not C correctness — it back-patches E/F's own
diagnostics, never C.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.layout.layout_sync import LayoutSync
from faebryk.libs.geometry.basic import Geometry
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.layout_ir import layout_ir
from faebryk.libs.kicad.semantic_view import _NetTable
from faebryk.libs.util import find, repo_root

# ---------------------------------------------------------------------------
# S0 ratchet: guarded import of the API the C implementation must provide.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.room_ops import (  # type: ignore
        ForcedVia,
        RoomCopy,
        address_prefix_map,
        copy_room_layout,
        insert_forced_via,
        pad_board_xy,
        room_net_map,
    )

    _ROOM_OPS_AVAILABLE = True
except ImportError:
    _ROOM_OPS_AVAILABLE = False

    class ForcedVia:  # type: ignore
        ...

    class RoomCopy:  # type: ignore
        ...

    pad_board_xy = insert_forced_via = None  # type: ignore
    address_prefix_map = room_net_map = copy_room_layout = None  # type: ignore

needs_room_ops = pytest.mark.xfail(
    not _ROOM_OPS_AVAILABLE,
    reason="§C: faebryk.exporters.pcb.layout.room_ops not implemented (S0 ratchet)",
    strict=True,
)

# Tier-2: the router lives in the sibling KiCadRoutingTools checkout and runs
# under system python3 (its .so is not built for this venv).
ROUTER_DIR = repo_root().parent / "KiCadRoutingTools"
_ROUTER_AVAILABLE = (ROUTER_DIR / "route_diff.py").exists()
needs_router = pytest.mark.skipif(
    not _ROUTER_AVAILABLE, reason=f"KiCadRoutingTools not present at {ROUTER_DIR}"
)

GRID = 0.1  # acceptance grid for "landing within one grid"


# ===========================================================================
# inline board builder (same synthesis rule as test_layout_ir_contract:
# numbered net table derived from pad net names, so tests state semantics only)
# ===========================================================================


def _pad(name, net, *, uuid=None, at=(0.0, 0.0, 0.0), layer="F.Cu"):
    return {"name": name, "net": net, "uuid": uuid, "at": at, "layer": layer}


def _fp(addr, pads, *, ref=None, uuid=None, at=(0.0, 0.0, 0.0), layer="F.Cu"):
    return {"addr": addr, "pads": pads, "ref": ref, "uuid": uuid, "at": at, "layer": layer}


def _board(fps, *, version=20241229) -> str:
    names = sorted({p["net"] for fp in fps for p in fp["pads"]} - {""})
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
        '\t(generator "test_room_ops")',
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

    lines.append(")")
    return "\n".join(lines) + "\n"


def _load(text: str) -> kicad.pcb.PcbFile:
    return kicad.loads(kicad.pcb.PcbFile, text)


def _net_name(pcb, number: int) -> str:
    return _NetTable(pcb).resolve(number, None, "router-honored object")


# ===========================================================================
# Tier-1 / C1 — the forced-via transform and split topology (no router)
# ===========================================================================


@needs_room_ops
def test_C1_pad_board_xy_matches_geometry_abs_pos():
    """board_xy = transform(component.at) ∘ pad.at must be the SAME composition
    the rest of atopile uses (Geometry.abs_pos), not a re-invented convention —
    so a placed footprint's pad lands where KiCad would put it."""
    bf = _load(
        _board(
            [
                _fp(
                    "top.u1",
                    [_pad("1", "SIG", at=(1.0, 2.0, 0.0))],
                    at=(10.0, 20.0, 90.0),
                )
            ]
        )
    )
    ir = layout_ir(bf.kicad_pcb)
    comp = ir["components"]["top.u1"]
    pad = comp["pads"]["1"]

    expected = Geometry.abs_pos(tuple(comp["at"]), tuple(pad["at"]))  # (x,y,rot,layer)
    got = pad_board_xy(ir, "top.u1", "1")
    assert got[0] == pytest.approx(expected[0])
    assert got[1] == pytest.approx(expected[1])


@needs_room_ops
def test_C1_forced_via_on_real_net_and_split_topology():
    """A forced via splits a net into pad→via@in_layer and via@out_layer→pad.
    The via and both segments live on COPPER with the REAL net (never net-0,
    KiCad would garbage-collect net-0 copper, BACKLOG 事实 9); guide geometry
    lives ONLY on User.1/User.2 (the router corridor layers)."""
    bf = _load(
        _board(
            [
                _fp("top.u1", [_pad("1", "SIG")], at=(0.0, 0.0, 0.0)),
                _fp("top.u2", [_pad("1", "SIG")], at=(10.0, 0.0, 0.0)),
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    fv = insert_forced_via(
        pcb,
        ir,
        net="SIG",
        pads=(("top.u1", "1"), ("top.u2", "1")),
        via_at=(5.0, 0.0),
        in_layer="F.Cu",
        out_layer="B.Cu",
    )
    assert isinstance(fv, ForcedVia)

    # via on copper, spanning the two layers, carrying the real net
    assert sorted(fv.via.layers) == sorted(["F.Cu", "B.Cu"])
    assert fv.via.net != 0
    assert _net_name(pcb, fv.via.net) == "SIG"

    # exactly two segments: one per layer, both on the real net, never net-0
    assert len(fv.segments) == 2
    layers = sorted(s.layer for s in fv.segments)
    assert layers == ["B.Cu", "F.Cu"]
    for s in fv.segments:
        assert s.net != 0
        assert _net_name(pcb, s.net) == "SIG"

    # guide geometry is confined to the User corridor layers, never copper
    guide_layers = {g.layer for g in fv.guides}
    assert guide_layers <= {"User.1", "User.2"}
    assert not (guide_layers & {"F.Cu", "B.Cu"})


@needs_room_ops
def test_C1_forced_via_landing_within_one_grid():
    """Pure-B acceptance (BACKLOG §C): the via and the segment endpoints land on
    the requested board coordinates within one routing grid."""
    bf = _load(
        _board(
            [
                _fp("top.u1", [_pad("1", "SIG")], at=(0.0, 0.0, 0.0)),
                _fp("top.u2", [_pad("1", "SIG")], at=(8.0, 6.0, 0.0)),
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    via_at = (4.0, 3.0)

    fv = insert_forced_via(
        pcb, ir,
        net="SIG",
        pads=(("top.u1", "1"), ("top.u2", "1")),
        via_at=via_at,
        in_layer="F.Cu",
        out_layer="B.Cu",
        grid_step=GRID,
    )

    assert abs(fv.via.at.x - via_at[0]) <= GRID
    assert abs(fv.via.at.y - via_at[1]) <= GRID

    # each pad's board xy is an endpoint of exactly one segment (within a grid)
    p0 = pad_board_xy(ir, "top.u1", "1")
    p1 = pad_board_xy(ir, "top.u2", "1")
    seg_pts = [(s.start.x, s.start.y) for s in fv.segments] + [
        (s.end.x, s.end.y) for s in fv.segments
    ]
    for px, py in (p0[:2], p1[:2]):
        assert any(abs(x - px) <= GRID and abs(y - py) <= GRID for x, y in seg_pts), (
            f"no segment endpoint near pad ({px},{py}): {seg_pts}"
        )


# ===========================================================================
# Tier-1 / C2 — address prefix remap + net map (the B→C oracle, re-pinned)
# ===========================================================================


@needs_room_ops
def test_C2_address_prefix_map_is_bijection():
    """C2's address remap is the I6 prefix bijection <sub> → <top.dup>."""
    src_ir = layout_ir(
        _load(
            _board(
                [
                    _fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "SUB_MID")]),
                    _fp("sub.r2", [_pad("1", "SUB_MID"), _pad("2", "SUB_GND")]),
                ]
            )
        ).kicad_pcb
    )
    tgt_ir = layout_ir(
        _load(
            _board(
                [
                    _fp("top.dup.r1", [_pad("1", "T_VCC"), _pad("2", "T_MID")]),
                    _fp("top.dup.r2", [_pad("1", "T_MID"), _pad("2", "T_GND")]),
                ]
            )
        ).kicad_pcb
    )
    addr_map = address_prefix_map(
        src_ir, tgt_ir, source_prefix="sub", target_prefix="top.dup"
    )
    assert addr_map == {"sub.r1": "top.dup.r1", "sub.r2": "top.dup.r2"}
    # total over the source, collision-free onto the target
    assert set(addr_map) == set(src_ir["components"])
    assert set(addr_map.values()) == set(tgt_ir["components"])
    assert len(set(addr_map.values())) == len(addr_map)


@needs_room_ops
def test_C2_net_map_equals_live():
    """The strongest C pin (mirrors B's consumer-oracle): C2's net_map must
    equal the live, e2e-battle-tested LayoutSync._generate_net_map. C2 rewrites
    the route-copy path, so it MUST hold this equivalence or it silently
    misbinds nets on the copied room (the BUG-2 class)."""
    source = _load(_board([_fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "SUB_GND")])]))
    target = _load(
        _board([_fp("top.mod.r1", [_pad("1", "TOP_VCC"), _pad("2", "TOP_GND")])])
    )
    addr_map = {"sub.r1": "top.mod.r1"}

    sync = LayoutSync(target.kicad_pcb)
    sync.__keepalive = target
    live = sync._generate_net_map(source.kicad_pcb, target.kicad_pcb, addr_map)

    from_c = room_net_map(
        layout_ir(source.kicad_pcb), layout_ir(target.kicad_pcb), addr_map
    )
    assert from_c == live == {"SUB_VCC": "TOP_VCC", "SUB_GND": "TOP_GND"}


@needs_room_ops
def test_C2_copy_room_layout_reports_maps_and_objects():
    """copy_room_layout returns the maps it used and the copied geometry, so a
    caller (and §E) can see exactly what moved — net_map equals the live one."""
    source = _load(_board([_fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "SUB_GND")])]))
    target = _load(
        _board([_fp("top.mod.r1", [_pad("1", "TOP_VCC"), _pad("2", "TOP_GND")])])
    )

    rc = copy_room_layout(
        target.kicad_pcb,
        source.kicad_pcb,
        source_prefix="sub",
        target_prefix="top.mod",
        offset=kicad.pcb.Xy(x=0.0, y=0.0),
    )
    assert isinstance(rc, RoomCopy)
    assert rc.addr_map == {"sub.r1": "top.mod.r1"}
    assert rc.net_map == {"SUB_VCC": "TOP_VCC", "SUB_GND": "TOP_GND"}
    assert isinstance(rc.new_objects, list)  # copied segments/vias/zones


# ===========================================================================
# Tier-2 — router-oracle: C's output is honored by the real router.
# Shells out to system python3 (the E3-proven invocation). These are armed by
# the SAME ratchet (needs_room_ops); once C lands they run for real and the
# router (not KiCad) is the judge. needs_router skips if the checkout is absent.
# ===========================================================================


def _net_num(pcb, name: str) -> int:
    return find(pcb.nets, lambda n: n.name == name).number


def _add_segment(pcb, net_number: int, start, end, *, layer="F.Cu", width=0.2):
    """Inject an intra-room copper segment (the corpus _board builder makes only
    footprints; a room-copy test needs a route to actually copy)."""
    seg = kicad.pcb.Segment(
        start=kicad.pcb.Xy(x=start[0], y=start[1]),
        end=kicad.pcb.Xy(x=end[0], y=end[1]),
        width=width,
        layer=layer,
        net=net_number,
        uuid=kicad.gen_uuid(mark="FBRK"),
    )
    kicad.insert(pcb, "segments", pcb.segments, seg)
    return seg


def _route(pcb_path: Path, nets: list[str], out_path: Path) -> dict:
    """Route single-ended `nets` via system python3; return its JSON_SUMMARY.

    Single-ended nets use route.py — route_diff.py routes ONLY differential
    pairs (nets needing _P/_N, P/N, or +/- suffixes) and prints no JSON_SUMMARY
    for a non-pair net (verified; BACKLOG §C "Tier-2 现实检验"). The diff-pair
    harness is exercised separately by the E3 thin slice."""
    import json
    import re

    cmd = [
        "python3",
        "route.py",
        str(pcb_path),
        str(out_path),
        "--nets",
        *nets,
        "--layers",
        "F.Cu",
        "B.Cu",
    ]
    r = subprocess.run(cmd, cwd=ROUTER_DIR, capture_output=True, text=True, timeout=300)
    m = re.search(r"JSON_SUMMARY: (\{.*\})", r.stdout + r.stderr)
    assert m, f"router produced no JSON_SUMMARY:\n{(r.stdout + r.stderr)[-2000:]}"
    return json.loads(m.group(1))


@needs_room_ops
@needs_router
def test_C1_router_honors_forced_via(tmp_path):
    """C's forced via must be well-formed enough that the router ingests the
    board without error AND preserves the via (a hard constraint, not ripped up).

    The router is a from-scratch router (it only routes UN-connected nets), so
    "honor" is tested the way it actually manifests: route a *different*,
    genuinely-unconnected net (SIG2) on the same board and assert it routes
    cleanly (failed == 0 — the board with the forced via was accepted), then
    confirm the forced via still sits at its requested coordinate in the OUTPUT
    board (survival is read from the board, not the summary's router-added via
    count). See BACKLOG §C "Tier-2 现实检验"."""
    bf = _load(
        _board(
            [
                _fp("top.u1", [_pad("1", "SIG")], at=(0.0, 0.0, 0.0)),
                _fp("top.u2", [_pad("1", "SIG")], at=(10.0, 0.0, 0.0)),
                # a second, genuinely-unconnected net so the router has real work
                _fp("top.u3", [_pad("1", "SIG2")], at=(0.0, 5.0, 0.0)),
                _fp("top.u4", [_pad("1", "SIG2")], at=(10.0, 5.0, 0.0)),
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    fv = insert_forced_via(
        pcb, ir,
        net="SIG",
        pads=(("top.u1", "1"), ("top.u2", "1")),
        via_at=(5.0, 0.0),
        in_layer="F.Cu",
        out_layer="B.Cu",
    )
    in_pcb = tmp_path / "forced.kicad_pcb"
    in_pcb.write_text(kicad.dumps(bf))
    out_pcb = tmp_path / "routed.kicad_pcb"
    summary = _route(in_pcb, ["SIG2"], out_pcb)
    assert summary["failed"] == 0  # board with the forced via was accepted+routed

    # the forced via survived in the output board (honored, not ripped up)
    routed = _load(out_pcb.read_text()).kicad_pcb
    vx, vy = fv.via.at.x, fv.via.at.y
    assert any(
        abs(v.at.x - vx) <= GRID and abs(v.at.y - vy) <= GRID for v in routed.vias
    ), f"forced via at ({vx},{vy}) not preserved: {[(v.at.x, v.at.y) for v in routed.vias]}"


@needs_room_ops
@needs_router
def test_C2_router_keeps_copied_room_connected(tmp_path):
    """After C2 copies a room (fp + intra-room routes + remapped nets), the
    copied geometry must be well-formed: the router ingests the board and routes
    a remaining net cleanly, and the copied intra-room route survives.

    Source carries an intra-room SUB_VCC segment (the _board builder makes only
    footprints), so the copy has something to replicate; the target footprints
    are placed apart and a separate NETX gives the router real work."""
    source = _load(_board([_fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "SUB_GND")])]))
    _add_segment(source.kicad_pcb, _net_num(source.kicad_pcb, "SUB_VCC"), (0.0, 0.0), (1.0, 0.0))

    target = _load(
        _board(
            [
                _fp("top.mod.r1", [_pad("1", "TOP_VCC"), _pad("2", "TOP_GND")], at=(0.0, 0.0, 0.0)),
                # a separate, genuinely-unconnected net for the router to route
                _fp("top.x1", [_pad("1", "NETX")], at=(0.0, 8.0, 0.0)),
                _fp("top.x2", [_pad("1", "NETX")], at=(10.0, 8.0, 0.0)),
            ]
        )
    )
    rc = copy_room_layout(
        target.kicad_pcb,
        source.kicad_pcb,
        source_prefix="sub",
        target_prefix="top.mod",
        offset=kicad.pcb.Xy(x=0.0, y=0.0),
    )
    assert rc.new_objects, "the SUB_VCC intra-room route was not copied"

    in_pcb = tmp_path / "copied.kicad_pcb"
    in_pcb.write_text(kicad.dumps(target))
    out_pcb = tmp_path / "routed.kicad_pcb"
    # routing the remaining net must complete cleanly on the copied board
    summary = _route(in_pcb, ["NETX"], out_pcb)
    assert summary["failed"] == 0

    # the copied intra-room TOP_VCC route survived the routing pass
    routed = _load(out_pcb.read_text()).kicad_pcb
    top_vcc = _net_num(routed, "TOP_VCC")
    assert any(s.net == top_vcc for s in routed.segments), (
        "copied TOP_VCC intra-room segment did not survive routing"
    )
