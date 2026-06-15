# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
layout_ir export — acceptance contract (BACKLOG §B / task B1a).

S0 discipline: this file is written BEFORE the implementation (B1b) and every
test is a strict-xfail ratchet. Until `faebryk.libs.kicad.layout_ir` exists the
tests xfail; B1b flips them green by *removing* the xfail (it goes inactive once
the module imports). A test that XPASSes while the module is still absent is a
bug in the test, not progress.

WHY a contract at all (the user's gate): layout_ir is the *only* stable
interface between the text layer (layout.yaml / route plan / diagnostics) and
the geometry layer (.kicad_pcb). v10 net names drift and uuids are random, so
the text layer can reference neither — only ato addresses, which the IR
translates. Its consumers are §C / §D / §F. If the IR is wrong, every one of
them backtracks. So the spec is pinned here, reverse-engineered from what those
consumers actually read (BACKLOG §B consumer→requirement matrix), not invented.

The eight invariants (BACKLOG §B I1–I8):
  I1  every managed fp keyed by ato address == the pcb `atopile_address` property
  I2  addr↔footprint_uuid / pad_uuid is a bijection; uuids are the pcb's own
  I3  IR pad net == the geometry-bound name (== semantic_view), not re-derived
  I4  addr→net is a function; same-geometry-net pads share a name;
      unnamed/no-net pads are not referenceable
       - I4a (pcb-only, here): partition consistency + no-net exclusion
       - I4b (graph-derived, deferred): signal-address→net bridge, loud unnamed
  I5  layout_ir.json is byte-stable and reference-order-invariant (INCREMENTAL
      stability only — uuids are random, so NOT clean-checkout reproducible)
  I6  reuse-subtree addresses are a true hierarchy: a `<sub>`→`<top.inner>`
      prefix remap is total and collision-free (bijection)
  I7  group member uuid set is faithful to the pcb groups
  I8  the unrepresentable (duplicate address / unresolvable net) is LOUD —
      never a silent half-IR

Coverage techniques (BACKLOG §B, proven by P0.1 T4/T8 to catch bind-by-position
bugs that natural-corpus snapshots miss):
  1. contract unit tests on hand-built inline fixtures (I1–I8)
  2. cross-check vs semantic_view over the real v10 corpus (independent reader)
  3. consumer-oracle: reconstruct LayoutSync._generate_net_map from the IR alone
     and assert == the live, e2e-battle-tested function (the strongest pin)
  4. corrupter integration: the T4 net-binding mutations — IR moves iff the
     semantics move
  5. determinism: PYTHONHASHSEED sweep + reference reordering → byte-identical
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.layout.layout_sync import LayoutSync
from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.kicad.semantic_view import NetResolutionError, semantic_view
from faebryk.libs.test import sexp_tree
from faebryk.libs.test.fileformats import FILEFORMATS_PATH
from faebryk.libs.test.sexp_tree import Node, children, head, walk

# ---------------------------------------------------------------------------
# S0 ratchet: guarded import. The contract is written against the API B1b must
# provide; while it is absent every test below xfails (strict).
# ---------------------------------------------------------------------------
try:
    from faebryk.libs.kicad.layout_ir import (  # type: ignore
        LAYOUT_IR_VERSION,
        LayoutIRError,
        layout_ir,
        layout_ir_json,
        layout_ir_schema,
    )

    _IR_AVAILABLE = True
except ImportError:
    _IR_AVAILABLE = False
    LAYOUT_IR_VERSION = None  # type: ignore

    class LayoutIRError(Exception):  # type: ignore
        """Placeholder so `pytest.raises(LayoutIRError)` collects pre-B1b."""

    layout_ir = None  # type: ignore
    layout_ir_json = None  # type: ignore
    layout_ir_schema = None  # type: ignore

needs_ir = pytest.mark.xfail(
    not _IR_AVAILABLE,
    reason="B1b: faebryk.libs.kicad.layout_ir not implemented yet (S0 ratchet)",
    strict=True,
)

V10_PCB_DIR = FILEFORMATS_PATH / "v10" / "pcb"
V9_PCB_DIR = FILEFORMATS_PATH / "v9" / "pcb"
_V10_BOARDS = sorted(p.stem for p in V10_PCB_DIR.glob("*.kicad_pcb"))


# ===========================================================================
# inline-board builder (mirrors test_layout_sync_nets / test_v10_acceptance;
# net table synthesized from pad net names so callers only state semantics)
# ===========================================================================


def _pad(name, net, *, uuid=None, at=(0.0, 0.0, 0.0), layer="F.Cu"):
    return {"name": name, "net": net, "uuid": uuid, "at": at, "layer": layer}


def _fp(addr, pads, *, ref=None, uuid=None, at=(0.0, 0.0, 0.0), layer="F.Cu"):
    return {
        "addr": addr,
        "pads": pads,
        "ref": ref,
        "uuid": uuid,
        "at": at,
        "layer": layer,
    }


def _board(
    fps, *, groups=(), segments=(), version=20241229, omit_net_table=False
) -> str:
    """A tiny self-consistent board.

    `fps`: list from _fp(); each pad from _pad() carries a net *name* ("" = no
    net). The numbered net table is synthesized here by byte-sorted name (the
    v10 synthesis rule, also valid for v9), so a test never hand-maintains
    numbers. `groups`: (name, uuid, [member_uuid,...]). `segments`: (net_name,
    (x0,y0), (x1,y1)).
    """
    names = sorted({p["net"] for fp in fps for p in fp["pads"]} | {s[0] for s in segments})
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
        '\t(generator "test_layout_ir")',
        '\t(generator_version "10.0")',
        '\t(general (thickness 1.6))',
        '\t(layers (0 "F.Cu" signal) (2 "B.Cu" signal))',
    ]
    if not omit_net_table:
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
            net_sexp = (
                f'(net {number[p["net"]]} "{p["net"]}")' if p["net"] != "" else ""
            )
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

    for net_name, (x0, y0), (x1, y1) in segments:
        lines += [
            "\t(segment",
            f"\t\t(start {x0} {y0})",
            f"\t\t(end {x1} {y1})",
            "\t\t(width 0.2)",
            '\t\t(layer "F.Cu")',
            f'\t\t(net {number[net_name]})',
            f'\t\t(uuid "{_uuid(None, "5e9")}")',
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
    # keep the PcbFile alive while using .kicad_pcb (zig ownership, BACKLOG)
    return kicad.loads(kicad.pcb.PcbFile, text)


def _addr_props(pcb) -> set[str]:
    """The atopile_address property set, read independently of the IR."""
    out = set()
    for fp in pcb.footprints:
        addr = Property.try_get_property(fp.propertys, "atopile_address")
        if addr:
            out.add(addr)
    return out


# ===========================================================================
# I1 — addressing: IR keys == the pcb atopile_address property set
# ===========================================================================


@needs_ir
def test_I1_components_keyed_by_atopile_address():
    bf = _load(
        _board(
            [
                _fp("top.r1", [_pad("1", "VCC"), _pad("2", "GND")]),
                _fp("top.sub.r2", [_pad("1", "GND"), _pad("2", "SIG")]),
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    # IR keys == the property set read by an independent reader (== authored)
    assert set(ir["components"]) == _addr_props(pcb) == {"top.r1", "top.sub.r2"}


@needs_ir
def test_I1_ref_is_carried_per_component():
    bf = _load(_board([_fp("top.r1", [_pad("1", "VCC")], ref="R1")]))
    ir = layout_ir(bf.kicad_pcb)
    assert ir["components"]["top.r1"]["ref"] == "R1"


# ===========================================================================
# I2 — uuids: addr↔footprint_uuid / pad_uuid bijection, uuids are the pcb's own
# ===========================================================================


@needs_ir
def test_I2_uuids_are_the_pcb_uuids_and_reverse_maps():
    bf = _load(
        _board(
            [
                _fp(
                    "top.r1",
                    [
                        _pad("1", "VCC", uuid="11111111-0000-0000-0000-000000000001"),
                        _pad("2", "GND", uuid="11111111-0000-0000-0000-000000000002"),
                    ],
                    uuid="aaaaaaaa-0000-0000-0000-000000000001",
                ),
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    fp = next(f for f in pcb.footprints)
    comp = ir["components"]["top.r1"]
    assert comp["footprint_uuid"] == fp.uuid

    pad_uuids_pcb = {p.name: p.uuid for p in fp.pads}
    for pad_name, pad in comp["pads"].items():
        assert pad["uuid"] == pad_uuids_pcb[pad_name]

    # fp uuid → addr is invertible (bijection across the managed set)
    by_uuid = {c["footprint_uuid"]: addr for addr, c in ir["components"].items()}
    assert by_uuid[fp.uuid] == "top.r1"
    assert len(by_uuid) == len(ir["components"])  # no two fps share a uuid


# ===========================================================================
# I3 — pad net == geometry-bound name (== semantic_view), NOT re-derived
# ===========================================================================


@needs_ir
def test_I3_pad_net_matches_semantic_view():
    bf = _load(
        _board(
            [
                _fp("top.r1", [_pad("1", "VCC"), _pad("2", "GND")]),
                _fp("top.r2", [_pad("1", "GND"), _pad("2", "")]),  # pad 2 = no net
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    # independent second reader: the IR must agree with semantic_view pad-for-pad
    sv = semantic_view(pcb)
    sv_by_ref = {f["reference"]: f for f in sv["footprints"]}
    for addr, comp in ir["components"].items():
        sv_pads = {p["name"]: p["net"] for p in sv_by_ref[comp["ref"]]["pads"]}
        for pad_name, pad in comp["pads"].items():
            assert pad["net"] == sv_pads[pad_name], (
                f"{addr}.{pad_name}: IR net {pad['net']!r} != "
                f"semantic_view {sv_pads[pad_name]!r}"
            )


# ===========================================================================
# I-geo — geometry faithfulness (the hole B1b left: I1/I2/I3/I5 pin address,
# uuid, net and byte-stability, but NOTHING asserted that component.at / pad.at
# / layer / layers equal the pcb's. C1 computes board_xy = transform(fp.at) ∘
# pad.at on exactly these fields, so a transposed/dropped coordinate would
# silently corrupt every forced via. We pin it two independent ways:
#   (a) inline, unique refs: IR == semantic_view pad-for-pad (a SECOND _xyr
#       implementation) AND == the authored values;
#   (b) corpus, identity-precise: match IR→pcb by the very uuids I2 pins and
#       assert at/layer/layers equal — catches a pad-A-coords-onto-pad-B swap
#       that a multiset check would miss.
# ===========================================================================


@needs_ir
def test_Igeo_component_and_pad_geometry_match_pcb():
    bf = _load(
        _board(
            [
                _fp(
                    "top.r1",
                    [
                        _pad("1", "VCC", at=(0.5, -1.5, 90.0), layer="F.Cu"),
                        _pad("2", "GND", at=(0.5, 1.5, 90.0), layer="F.Cu"),
                    ],
                    ref="R1",
                    at=(10.0, 20.0, 90.0),
                    layer="F.Cu",
                ),
                _fp(
                    "top.r2",
                    [_pad("1", "GND", at=(-0.3, 0.0, 0.0), layer="B.Cu")],
                    ref="R2",
                    at=(30.0, 40.0, 180.0),
                    layer="B.Cu",
                ),
            ]
        )
    )
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)

    # independent second reader (semantic_view has its own _xyr)
    sv = semantic_view(pcb)
    sv_by_ref = {f["reference"]: f for f in sv["footprints"]}

    # authored values, to also catch a "both readers wrong the same way" case
    authored = {
        "top.r1": {
            "at": [10.0, 20.0, 90.0],
            "layer": "F.Cu",
            "pads": {"1": ([0.5, -1.5, 90.0], ["F.Cu"]), "2": ([0.5, 1.5, 90.0], ["F.Cu"])},
        },
        "top.r2": {
            "at": [30.0, 40.0, 180.0],
            "layer": "B.Cu",
            "pads": {"1": ([-0.3, 0.0, 0.0], ["B.Cu"])},
        },
    }

    for addr, comp in ir["components"].items():
        sv_fp = sv_by_ref[comp["ref"]]
        exp = authored[addr]
        assert comp["at"] == sv_fp["at"] == exp["at"]
        assert comp["layer"] == sv_fp["layer"] == exp["layer"]
        sv_pads = {p["name"]: p for p in sv_fp["pads"]}
        for pad_name, pad in comp["pads"].items():
            exp_at, exp_layers = exp["pads"][pad_name]
            assert pad["at"] == sv_pads[pad_name]["at"] == exp_at, (
                f"{addr}.{pad_name}: IR at {pad['at']} != "
                f"semantic_view {sv_pads[pad_name]['at']} / authored {exp_at}"
            )
            assert pad["layers"] == sv_pads[pad_name]["layers"] == exp_layers


@needs_ir
@pytest.mark.parametrize("stem", _V10_BOARDS)
def test_Igeo_corpus_geometry_is_identity_matched_to_pcb(stem):
    """On the real v10 corpus, match every IR component/pad to the pcb by the
    uuid I2 pins and assert geometry equals — identity-precise, so a coordinate
    transposed onto the wrong pad cannot hide."""
    bf = _load((V10_PCB_DIR / f"{stem}.kicad_pcb").read_text())
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    if not ir["components"]:
        pytest.skip(f"{stem}: no atopile-managed components")

    fp_by_uuid = {fp.uuid: fp for fp in pcb.footprints}
    for comp in ir["components"].values():
        fp = fp_by_uuid[comp["footprint_uuid"]]
        assert comp["at"] == [fp.at.x, fp.at.y, fp.at.r if fp.at.r is not None else 0.0]
        assert comp["layer"] == fp.layer
        pad_by_uuid = {p.uuid: p for p in fp.pads}
        for pad in comp["pads"].values():
            p = pad_by_uuid[pad["uuid"]]
            assert pad["at"] == [p.at.x, p.at.y, p.at.r if p.at.r is not None else 0.0]
            assert pad["layers"] == sorted(p.layers)


# ===========================================================================
# I4a — net partition (pcb-only half of I4); I4b (bridge②) is deferred below
# ===========================================================================


@needs_ir
def test_I4a_same_net_pads_share_name_and_appear_under_that_net():
    bf = _load(
        _board(
            [
                _fp("top.r1", [_pad("1", "VCC"), _pad("2", "MID")]),
                _fp("top.r2", [_pad("1", "MID"), _pad("2", "GND")]),
            ]
        )
    )
    ir = layout_ir(bf.kicad_pcb)

    # nets is the inverse of the per-pad net field: consistent both directions
    rebuilt: dict[str, set[str]] = {}
    for addr, comp in ir["components"].items():
        for pad_name, pad in comp["pads"].items():
            if pad["net"] != "":
                rebuilt.setdefault(pad["net"], set()).add(f"{addr}.{pad_name}")
    assert {k: sorted(v) for k, v in rebuilt.items()} == {
        k: sorted(v) for k, v in ir["nets"].items()
    }
    # the shared net actually unites the two endpoints (partition, not per-pad)
    assert set(ir["nets"]["MID"]) == {"top.r1.2", "top.r2.1"}


@needs_ir
def test_I4a_no_net_pads_are_not_referenceable():
    bf = _load(_board([_fp("top.r1", [_pad("1", "VCC"), _pad("2", "")])]))
    ir = layout_ir(bf.kicad_pcb)
    # the empty net is never a referenceable entry
    assert "" not in ir["nets"]
    assert all(name != "" for name in ir["nets"])
    # but the pad itself is still represented (I3): faithful, just net=""
    assert ir["components"]["top.r1"]["pads"]["2"]["net"] == ""


# ===========================================================================
# I5 — determinism: byte-stable + reference-order-invariant (INCREMENTAL only)
# ===========================================================================


@needs_ir
def test_I5_layout_ir_json_is_idempotent():
    bf = _load(
        _board(
            [
                _fp("top.r1", [_pad("1", "VCC"), _pad("2", "GND")]),
                _fp("top.r2", [_pad("1", "GND"), _pad("2", "VCC")]),
            ]
        )
    )
    pcb = bf.kicad_pcb
    assert layout_ir_json(pcb) == layout_ir_json(pcb)


@needs_ir
def test_I5_layout_ir_json_is_reference_order_invariant():
    text = _board(
        [
            _fp("top.r1", [_pad("1", "VCC"), _pad("2", "GND")]),
            _fp("top.r2", [_pad("1", "GND"), _pad("2", "VCC")]),
        ]
    )
    baseline = layout_ir_json(_load(text).kicad_pcb)

    # reverse footprint + net declaration order in the source text
    root = sexp_tree.parse(text)[0]
    for section in ("footprint", "net"):
        nodes = children(root, section)
        if len(nodes) > 1:
            slots = [
                i for i, c in enumerate(root)
                if isinstance(c, list) and head(c) == section
            ]
            for i, node in zip(slots, list(reversed(nodes))):
                root[i] = node
    reordered = layout_ir_json(_load(sexp_tree.dumps(root)).kicad_pcb)
    assert reordered == baseline


@needs_ir
@pytest.mark.parametrize("stem", ["test", "layout_reuse_top"])
def test_I5_synthesis_is_hash_seed_independent(stem):
    """Two processes, different PYTHONHASHSEED → byte-identical layout_ir.json.
    Kills set/dict-iteration-order leakage in the IR (A1 bug class, IR edition).
    Mirrors test_net_binding_corruption::test_v10_synthesized_numbers_*."""
    path = V10_PCB_DIR / f"{stem}.kicad_pcb"
    if not path.exists():
        pytest.skip(f"{path} not in corpus")
    script = (
        "import sys\n"
        "import faebryk.library._F\n"
        "from faebryk.libs.kicad.fileformats import kicad\n"
        "from faebryk.libs.kicad.layout_ir import layout_ir_json\n"
        "pcb = kicad.loads(kicad.pcb.PcbFile, open(sys.argv[1]).read()).kicad_pcb\n"
        "sys.stdout.write(layout_ir_json(pcb))\n"
    )
    dumps = [
        subprocess.run(
            [sys.executable, "-c", script, str(path)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("0", "1")
    ]
    assert dumps[0] == dumps[1]


# ===========================================================================
# I6 — reuse subtree: prefix remap <sub> → <top.inner> is a total bijection
# ===========================================================================


@needs_ir
def test_I6_address_prefix_remap_is_total_bijection():
    sub_ir = layout_ir(
        _load(
            _board(
                [
                    _fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "SUB_MID")]),
                    _fp("sub.r2", [_pad("1", "SUB_MID"), _pad("2", "SUB_GND")]),
                ]
            )
        ).kicad_pcb
    )
    top_ir = layout_ir(
        _load(
            _board(
                [
                    _fp("top.dup.r1", [_pad("1", "T_VCC"), _pad("2", "T_MID")]),
                    _fp("top.dup.r2", [_pad("1", "T_MID"), _pad("2", "T_GND")]),
                ]
            )
        ).kicad_pcb
    )

    def remap(addr, old="sub", new="top.dup"):
        assert addr == old or addr.startswith(old + ".")  # true hierarchy
        return new + addr[len(old):]

    mapped = {remap(a) for a in sub_ir["components"]}
    assert mapped == set(top_ir["components"])  # total + onto
    assert len(mapped) == len(sub_ir["components"])  # injective (collision-free)


# ===========================================================================
# I7 — superseded by C3 (rooms replaced groups). The room derivation contract
# now lives in test_room_migration_contract.py::test_C3_5a_* (rooms are
# address/sheetname-derived, no group provenance).
# ===========================================================================


# ===========================================================================
# I8 — the unrepresentable is LOUD (no silent half-IR)
# ===========================================================================


@needs_ir
def test_I8_duplicate_address_raises():
    text = _board(
        [
            _fp("top.r1", [_pad("1", "VCC")], uuid="aaaaaaaa-0000-0000-0000-000000000001"),
            _fp("top.r1", [_pad("1", "GND")], uuid="aaaaaaaa-0000-0000-0000-000000000002"),
        ]
    )
    with pytest.raises(LayoutIRError, match="top.r1"):
        layout_ir(_load(text).kicad_pcb)


@needs_ir
def test_I8_dangling_net_raises():
    # hand-craft a pad referencing a net number with no table entry
    text = _board([_fp("top.r1", [_pad("1", "VCC")])])
    root = sexp_tree.parse(text)[0]
    pad = children(children(root, "footprint")[0], "pad")[0]
    netref = children(pad, "net")[0]
    netref[1] = "9999"
    del netref[2:]  # drop inline name: the bare dangling number must be fatal
    with pytest.raises(NetResolutionError, match="dangling"):
        layout_ir(_load(sexp_tree.dumps(root)).kicad_pcb)


# ===========================================================================
# Technique 2 — cross-check vs semantic_view over the real v10 corpus
# ===========================================================================

@needs_ir
@pytest.mark.parametrize("stem", _V10_BOARDS)
def test_corpus_ir_pad_nets_agree_with_semantic_view(stem):
    bf = _load((V10_PCB_DIR / f"{stem}.kicad_pcb").read_text())
    pcb = bf.kicad_pcb
    ir = layout_ir(pcb)
    sv = semantic_view(pcb)

    # group semantic_view footprints by atopile_address-equivalent (reference is
    # not unique across reuse, so compare via the IR's own ref → addr is keyed
    # by address; we check every IR pad net is *present* as a real net fact)
    sv_nets = set(sv["nets"]) | {""}
    for addr, comp in ir["components"].items():
        for pad_name, pad in comp["pads"].items():
            assert pad["net"] in sv_nets, (
                f"{stem}:{addr}.{pad_name} net {pad['net']!r} is not a "
                "semantic_view net (IR invented a name)"
            )


# ===========================================================================
# Technique 3 — consumer-oracle: reconstruct _generate_net_map from the IR and
# assert it equals the live, e2e-battle-tested LayoutSync._generate_net_map.
# This is the strongest pin: it proves the IR carries exactly what C2 needs.
# ===========================================================================


def _net_map_from_ir(src_ir, tgt_ir, addr_map):
    """Reconstruct LayoutSync._generate_net_map(source, target, addr_map) using
    ONLY the IR. Mirrors layout_sync.py:171-243 (pad-name correspondence at
    mapped addresses; falsy net names skipped; most-frequent mapping wins).
    The size tie-break for duplicate pad names is dead-code on the inline corpus
    (pad names are unique per fp) and out of scope here."""
    net_map: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    for src_addr, tgt_addr in addr_map.items():
        src = src_ir["components"].get(src_addr)
        tgt = tgt_ir["components"].get(tgt_addr)
        if not src or not tgt:
            continue
        for pad_name, src_pad in src["pads"].items():
            tgt_pad = tgt["pads"].get(pad_name)
            if tgt_pad is None:
                continue
            sn, tn = src_pad["net"], tgt_pad["net"]
            if sn and tn:  # both non-empty == src_pad.net.name and tgt_pad.net.name
                counts.setdefault(sn, {})
                counts[sn][tn] = counts[sn].get(tn, 0) + 1
                if sn not in net_map or counts[sn][tn] > max(counts[sn].values()):
                    net_map[sn] = tn
    return net_map


@needs_ir
def test_oracle_net_map_reconstructed_from_ir_equals_live():
    source = _load(
        _board(
            [_fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "SUB_GND")])]
        )
    )
    # deliberately different net names on the target side
    target = _load(
        _board(
            [_fp("top.mod.r1", [_pad("1", "TOP_VCC"), _pad("2", "TOP_GND")])]
        )
    )
    addr_map = {"sub.r1": "top.mod.r1"}

    sync = LayoutSync(target.kicad_pcb)
    sync.__keepalive = target
    live = sync._generate_net_map(source.kicad_pcb, target.kicad_pcb, addr_map)

    from_ir = _net_map_from_ir(
        layout_ir(source.kicad_pcb), layout_ir(target.kicad_pcb), addr_map
    )
    assert from_ir == live == {"SUB_VCC": "TOP_VCC", "SUB_GND": "TOP_GND"}


@needs_ir
def test_oracle_net_map_ignores_unconnected_pads_like_live():
    source = _load(
        _board([_fp("sub.r1", [_pad("1", "SUB_VCC"), _pad("2", "")])])
    )
    target = _load(
        _board([_fp("top.mod.r1", [_pad("1", "TOP_VCC"), _pad("2", "")])])
    )
    addr_map = {"sub.r1": "top.mod.r1"}

    sync = LayoutSync(target.kicad_pcb)
    sync.__keepalive = target
    live = sync._generate_net_map(source.kicad_pcb, target.kicad_pcb, addr_map)

    from_ir = _net_map_from_ir(
        layout_ir(source.kicad_pcb), layout_ir(target.kicad_pcb), addr_map
    )
    assert from_ir == live == {"SUB_VCC": "TOP_VCC"}


# ===========================================================================
# Technique 4 — corrupter integration (T4 net-binding mutations): the IR moves
# iff the semantics move. Reuses the v9 corpus + sexp_tree like
# test_net_binding_corruption, but asserts on layout_ir_json, not the view.
# ===========================================================================

_CORRUPTER_BOARDS = ["test", "layout_reuse_top", "interf_u_unrouted"]


def _ir_json_of_tree(root: Node) -> str:
    return layout_ir_json(_load(sexp_tree.dumps(root)).kicad_pcb)


def _require_managed_nets(root: Node) -> None:
    """A net-sensitivity test is only meaningful on a board whose IR actually
    references nets — i.e. has atopile-managed footprints. Raw fixtures with no
    `atopile_address` (e.g. interf_u_unrouted) yield an empty IR that is
    trivially net-insensitive; skip rather than pass vacuously."""
    ir = layout_ir(_load(sexp_tree.dumps(root)).kicad_pcb)
    if not ir["nets"]:
        pytest.skip("board has no atopile-managed nets — IR net-sensitivity n/a")


def _net_table(root: Node) -> list[Node]:
    entries = children(root, "net")
    assert len(entries) >= 3, "corpus board too trivial for corruption tests"
    return entries


def _replace_children(root: Node, head_name: str, replacement: list[Node]) -> None:
    slots = [
        i for i, c in enumerate(root) if isinstance(c, list) and head(c) == head_name
    ]
    assert len(slots) == len(replacement)
    for i, node in zip(slots, replacement):
        root[i] = node


@needs_ir
@pytest.mark.parametrize("stem", _CORRUPTER_BOARDS)
def test_corrupter_consistent_renumbering_is_ir_neutral(stem):
    """Swap two net numbers everywhere (table + all references). Numbers are
    file-local handles — the IR keys off names, so nothing may move."""
    raw = (V9_PCB_DIR / f"{stem}.kicad_pcb").read_text()
    root = sexp_tree.parse(raw)[0]
    _require_managed_nets(root)
    baseline = _ir_json_of_tree(root)

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
    assert _ir_json_of_tree(root) == baseline


@needs_ir
@pytest.mark.parametrize("stem", _CORRUPTER_BOARDS)
def test_corrupter_table_only_name_swap_moves_the_ir(stem):
    """Swap two *names* in the net table while every reference keeps its old
    number. A position-/first-reference-bound IR would not notice; a
    name-resolving one (via semantic_view) must move or raise."""
    raw = (V9_PCB_DIR / f"{stem}.kicad_pcb").read_text()
    root = sexp_tree.parse(raw)[0]
    _require_managed_nets(root)
    baseline = _ir_json_of_tree(root)

    named = [e for e in _net_table(root) if len(e) >= 3 and e[2] != '""']
    e1, e2 = named[0], named[-1]
    assert e1[2] != e2[2]
    e1[2], e2[2] = e2[2], e1[2]
    try:
        corrupted = _ir_json_of_tree(root)
    except NetResolutionError:
        return  # loud failure is acceptable
    assert corrupted != baseline, (
        "a table-only name swap was bound silently — the IR is not resolving "
        "nets through the table"
    )


# ===========================================================================
# I4b — bridge② (signal-address → net): the ONE graph-derived part of B. Net
# names are computed from connected interfaces (net_naming.py) and are not
# recoverable from the pcb alone, so its *behaviour* (function-ness, geometry
# consistency, unnamed-not-referenceable) is proven on a real build in
# test/end_to_end/test_layout_ir_build.py. Here we pin only the API contract,
# which is checkable without a graph.
# (C2 does NOT need this bridge: _generate_net_map keys off addresses + pad-name
# + pad-net-name, all pinned above. bridge② is a D2/F-diag need.)
# ===========================================================================


@needs_ir
def test_I4b_signal_nets_is_opt_in_and_graph_only():
    from faebryk.libs.kicad.layout_ir import signal_nets

    assert callable(signal_nets)  # bridge② is exposed
    # pcb-only callers never see bridge②: it requires the app graph
    bf = _load(_board([_fp("top.r1", [_pad("1", "VCC")])]))
    assert "signal_nets" not in layout_ir(bf.kicad_pcb)


# ===========================================================================
# B2 — the IR shape is frozen as a checked-in JSON Schema, and the version
# field is real. The schema lets every downstream consumer (§C/§D/§F, and any
# out-of-process reader of the .layout_ir.json artifact) validate without
# importing atopile. We pin: the schema is itself valid; every IR this contract
# produces validates against it; the version field is present and consistent;
# and the schema is strict enough to REJECT a malformed IR (else it pins
# nothing).
# ===========================================================================


@needs_ir
def test_B2_schema_is_a_valid_jsonschema_and_versioned():
    import jsonschema

    schema = layout_ir_schema()
    # the meta-schema accepts it (draft 2020-12)
    jsonschema.Draft202012Validator.check_schema(schema)
    # version is real and the schema agrees on the floor
    assert LAYOUT_IR_VERSION >= 1
    assert schema["properties"]["layout_ir_version"]["minimum"] <= LAYOUT_IR_VERSION
    assert f"/v{LAYOUT_IR_VERSION}." in schema["$id"], (
        f"schema $id {schema['$id']!r} not pinned to version {LAYOUT_IR_VERSION}"
    )


@needs_ir
def test_B2_pcb_only_and_signal_bearing_ir_validate():
    import jsonschema

    schema = layout_ir_schema()
    bf = _load(
        _board(
            [
                _fp("top.r1", [_pad("1", "VCC"), _pad("2", "GND")]),
                _fp("top.r2", [_pad("1", "GND"), _pad("2", "")]),
            ],
            groups=[],
        )
    )
    ir = layout_ir(bf.kicad_pcb)
    assert ir["layout_ir_version"] == LAYOUT_IR_VERSION
    jsonschema.validate(ir, schema)  # pcb-only (no signal_nets) is valid

    # an IR carrying bridge② (folded in by the build step) is also valid
    with_bridge = {**ir, "signal_nets": {"top.r1.power.hv": "VCC"}}
    jsonschema.validate(with_bridge, schema)


@needs_ir
@pytest.mark.parametrize("stem", _V10_BOARDS)
def test_B2_corpus_ir_validates_against_schema(stem):
    import jsonschema

    bf = _load((V10_PCB_DIR / f"{stem}.kicad_pcb").read_text())
    ir = layout_ir(bf.kicad_pcb)
    jsonschema.validate(ir, layout_ir_schema())


@needs_ir
def test_B2_schema_rejects_a_malformed_ir():
    """A schema that accepts everything pins nothing — prove it bites."""
    import jsonschema

    schema = layout_ir_schema()
    bf = _load(_board([_fp("top.r1", [_pad("1", "VCC")])]))
    ir = layout_ir(bf.kicad_pcb)

    # missing the required version field
    bad_missing = {k: v for k, v in ir.items() if k != "layout_ir_version"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_missing, schema)

    # a 2-element `at` (a dropped rotation) — the I-geo field must be [x,y,r]
    import copy

    bad_at = copy.deepcopy(ir)
    bad_at["components"]["top.r1"]["at"] = [1.0, 2.0]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_at, schema)

    # an unknown top-level key is rejected (additionalProperties: false)
    bad_extra = {**ir, "surprise": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_extra, schema)
