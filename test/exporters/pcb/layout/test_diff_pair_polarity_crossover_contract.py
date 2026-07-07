# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_diff_pair_polarity_crossover_contract — an INVERTED diff pair must route
PHYSICALLY under the runner's fix_polarity=False default (netlist authority).

Contract (the .ato netlist is authoritative; pinned against the real router
through the real layout_plan_runner subprocess seam):

 * A diff stage with fix_polarity UNSET (runner injects False) routes a pair
   whose target connector has inverted P/N geometric order — it used to fail
   outright or, with fix_polarity=true, "solve" it by rewriting the target
   pad nets (ROUTE-POLARITY-SWAPPED). Now the router resolves the inversion
   the way interactive tools do: both nets transition layers through a
   STAGGERED via pair at which the P/N lateral assignment swaps.
 * Netlist honored end to end: the summary reports zero polarity swaps, the
   output board's pad->net map equals the input's, and kicad-cli DRC reports
   ZERO unconnected items (each track chain lands on the pad with the SAME
   net) and no copper violations.
 * The crossover physically exists: each net of the inverted pair owns >= 1
   via, the two nets' vias are staggered (>= via_size + clearance apart), and
   the P/N lateral order near the source is the opposite of the order near
   the target.
 * Negative control: a straight-polarity pair in the SAME stage routes with
   ZERO vias — no crossover is invented where none is needed.
 * diff_pair_gap resumes outside the crossover window (sampled P-N center
   distance stays near track_width + diff_pair_gap).

Both connectors are through-hole with short fanout stubs, so the escape
directions are real and the opposite-side connector-flip resolution is
unavailable by construction — the crossover is the only legal mechanism and
the test answer is fixed, not discovered (CLAUDE.md test discipline).

Runs the router under the SYSTEM interpreter via run_route_stages (the real
invoker); skips loudly when the router submodule, system python3, or
kicad-cli is absent — the same guard as the other router e2e tests.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from faebryk.exporters.pcb.layout.layout_plan import (
    Board,
    DesignRules,
    GridRouteOverride,
    LayoutPlan,
    RouteStage,
    Stackup,
    StackupLayer,
)
from faebryk.exporters.pcb.layout.layout_plan_runner import (
    _system_python3,
    run_route_stages,
)
from faebryk.libs.util import repo_root

_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_SYS_PY = _system_python3()
_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

needs_router = pytest.mark.skipif(
    not (_ROUTER_ROOT / "route_diff.py").exists() or _SYS_PY is None,
    reason="router submodule or system python3 absent",
)

# geometry constants (KiCad default DRC floor: clearance 0.2, width 0.2)
_W = 0.2
_CLR = 0.2
_GAP = 0.25
_VIA = 0.6
_DRILL = 0.3
_SRC_X, _TGT_X = 100.0, 130.0
_STUB = 1.5
_A_TOP, _A_BOT = 100.0, 101.4  # inverted pair rows
_B_TOP, _B_BOT = 108.0, 109.4  # control pair rows


def _th_pad(num: str, y: float, net_id: int, net: str) -> str:
    return (
        f'\t\t(pad "{num}" thru_hole circle (at 0 {y}) (size 1.0 1.0) '
        f'(drill 0.6) (layers "*.Cu") (net {net_id} "{net}"))'
    )


def _stub(x1: float, y: float, x2: float, net_id: int) -> str:
    return (
        f"\t(segment (start {x1} {y}) (end {x2} {y}) "
        f'(width {_W}) (layer "F.Cu") (net {net_id}))'
    )


def _synth_board() -> str:
    """Two TH connectors 30mm apart; pair A's target has inverted P/N order."""
    j1_pads = "\n".join(
        [
            _th_pad("1", _A_TOP - 104.0, 1, "/A_P"),
            _th_pad("2", _A_BOT - 104.0, 2, "/A_N"),
            _th_pad("3", _B_TOP - 104.0, 3, "/B_P"),
            _th_pad("4", _B_BOT - 104.0, 4, "/B_N"),
        ]
    )
    j2_pads = "\n".join(
        [
            _th_pad("1", _A_TOP - 104.0, 2, "/A_N"),  # INVERTED
            _th_pad("2", _A_BOT - 104.0, 1, "/A_P"),
            _th_pad("3", _B_TOP - 104.0, 3, "/B_P"),  # straight
            _th_pad("4", _B_BOT - 104.0, 4, "/B_N"),
        ]
    )
    stubs = "\n".join(
        [
            _stub(_SRC_X, y, _SRC_X + _STUB, n)
            for y, n in ((_A_TOP, 1), (_A_BOT, 2), (_B_TOP, 3), (_B_BOT, 4))
        ]
        + [
            _stub(_TGT_X, y, _TGT_X - _STUB, n)
            for y, n in ((_A_TOP, 2), (_A_BOT, 1), (_B_TOP, 3), (_B_BOT, 4))
        ]
    )

    def fp(ref: str, x: float, pads: str) -> str:
        return (
            f'\t(footprint "test:conn"\n\t\t(layer "F.Cu")\n\t\t(at {x} 104)\n'
            f'\t\t(property "Reference" "{ref}" (at 0 -6 0) (layer "F.SilkS")\n'
            "\t\t\t(effects (font (size 1 1) (thickness 0.15))))\n"
            '\t\t(property "Value" "CONN" (at 0 8 0) (layer "F.Fab")\n'
            "\t\t\t(effects (font (size 1 1) (thickness 0.15))))\n"
            f"{pads}\n\t)"
        )

    return (
        "(kicad_pcb\n"
        "\t(version 20241229)\n"
        '\t(generator "pcbnew")\n'
        '\t(generator_version "9.0")\n'
        "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n"
        '\t(paper "A4")\n'
        "\t(layers\n"
        '\t\t(0 "F.Cu" signal)\n'
        '\t\t(2 "B.Cu" signal)\n'
        '\t\t(25 "Edge.Cuts" user)\n'
        "\t)\n"
        "\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n"
        '\t(net 0 "")\n'
        '\t(net 1 "/A_P")\n'
        '\t(net 2 "/A_N")\n'
        '\t(net 3 "/B_P")\n'
        '\t(net 4 "/B_N")\n'
        f"{fp('J1', _SRC_X, j1_pads)}\n"
        f"{fp('J2', _TGT_X, j2_pads)}\n"
        f"{stubs}\n"
        "\t(gr_rect (start 92 94) (end 138 116)\n"
        '\t\t(stroke (width 0.1) (type default)) (layer "Edge.Cuts"))\n'
        ")\n"
    )


def _plan(stage: RouteStage) -> LayoutPlan:
    return LayoutPlan(
        rules=DesignRules(
            clearance=_CLR, track_width=_W, diff_pair_width=_W, diff_pair_gap=_GAP
        ),
        board=Board(
            stackup=Stackup(
                layers=[
                    StackupLayer(name="F.Cu", type="copper", thickness=0.035),
                    StackupLayer(
                        name="d1",
                        type="dielectric",
                        thickness=1.5,
                        material="FR4",
                        epsilon_r=4.5,
                    ),
                    StackupLayer(name="B.Cu", type="copper", thickness=0.035),
                ]
            )
        ),
        route_stages=[stage],
    )


_IR = {
    "signal_nets": {
        "top.a.p": "/A_P",
        "top.a.n": "/A_N",
        "top.b.p": "/B_P",
        "top.b.n": "/B_N",
    },
    "nets": {},
}

# analysis script run under the SYSTEM interpreter (vendor kicad_parser is not
# a venv dependency) — dumps pads / per-net segments / per-net vias as JSON
_ANALYZE = textwrap.dedent(
    """
    import json
    import sys

    sys.path.insert(0, sys.argv[1])
    from kicad_parser import parse_kicad_pcb

    pcb = parse_kicad_pcb(sys.argv[2])
    out = {
        "pads": [
            {"ref": ref, "num": p.pad_number, "net": p.net_name}
            for ref, fp in sorted(pcb.footprints.items())
            for p in fp.pads
        ],
        "segments": {},
        "vias": {},
    }
    for net_id, net in pcb.nets.items():
        out["segments"][net.name] = [
            [s.start_x, s.start_y, s.end_x, s.end_y]
            for s in pcb.segments
            if s.net_id == net_id
        ]
        out["vias"][net.name] = [[v.x, v.y] for v in pcb.vias if v.net_id == net_id]
    print("JSON_OUT:" + json.dumps(out))
    """
)


def _analyze(board: Path) -> dict:
    proc = subprocess.run(
        [str(_SYS_PY), "-c", _ANALYZE, str(_ROUTER_ROOT), str(board)],
        capture_output=True,
        text=True,
        cwd=str(_ROUTER_ROOT),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("JSON_OUT:"))
    return json.loads(line.removeprefix("JSON_OUT:"))


def _net_ys_at_x(segments: list[list[float]], x: float) -> list[float]:
    ys = []
    for x1, y1, x2, y2 in segments:
        if min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6 and abs(x2 - x1) > 1e-6:
            t = (x - x1) / (x2 - x1)
            ys.append(y1 + t * (y2 - y1))
    return ys


@needs_router
@pytest.mark.slow
@pytest.mark.regression
def test_inverted_pair_routes_with_physical_crossover(tmp_path):
    board = tmp_path / "xover.kicad_pcb"
    board.write_text(_synth_board())

    stage = RouteStage(
        name="pairs",
        mode="diff",
        nets=["top.a.p", "top.a.n", "top.b.p", "top.b.n"],
        config=GridRouteOverride(
            track_width=_W,
            clearance=_CLR,
            diff_pair_gap=_GAP,
            via_size=_VIA,
            via_drill=_DRILL,
        ),
        # fix_polarity deliberately UNSET: the runner injects False (R1b) and
        # the router must now ROUTE the inversion, not fail or swap pads
    )
    report = run_route_stages(_plan(stage), _IR, input_board=board, workdir=tmp_path)
    assert report.totals["failed"] == 0, report.to_dict()
    summary = report.stages[0].summary
    assert summary["polarity_swapped_pairs"] == [], (
        "router rewrote pad nets under fix_polarity=False"
    )

    routed = Path(report.final_board)
    out = _analyze(routed)

    # netlist honored: pad -> net mapping identical to the authored board
    expect = {
        ("J1", "1"): "/A_P",
        ("J1", "2"): "/A_N",
        ("J1", "3"): "/B_P",
        ("J1", "4"): "/B_N",
        ("J2", "1"): "/A_N",
        ("J2", "2"): "/A_P",
        ("J2", "3"): "/B_P",
        ("J2", "4"): "/B_N",
    }
    got = {(p["ref"], p["num"]): p["net"] for p in out["pads"]}
    assert got == expect, f"pad nets rewritten: {got}"

    # crossover exists: each net of the inverted pair owns >= 1 via ...
    ap_vias, an_vias = out["vias"]["/A_P"], out["vias"]["/A_N"]
    assert ap_vias and an_vias, (
        f"no crossover vias: A_P={len(ap_vias)} A_N={len(an_vias)}"
    )
    # ... staggered, never coincident (DRC-safe hole-to-hole)
    min_pitch = min(
        ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5
        for px, py in ap_vias
        for nx, ny in an_vias
    )
    assert min_pitch >= _VIA + _CLR - 1e-3, (
        f"crossover vias not staggered: min P-N pitch {min_pitch:.3f}mm"
    )

    # ... and the P/N lateral order SWAPS across the transition
    via_xs = [x for x, _ in ap_vias + an_vias]
    xs = max(_SRC_X + _STUB + 0.3, min(via_xs) - 3.0)
    xt = min(_TGT_X - _STUB - 0.3, max(via_xs) + 3.0)
    p_src = _net_ys_at_x(out["segments"]["/A_P"], xs)
    n_src = _net_ys_at_x(out["segments"]["/A_N"], xs)
    p_tgt = _net_ys_at_x(out["segments"]["/A_P"], xt)
    n_tgt = _net_ys_at_x(out["segments"]["/A_N"], xt)
    assert p_src and n_src and p_tgt and n_tgt, "cannot sample pair order"
    assert (min(p_src) < min(n_src)) != (min(p_tgt) < min(n_tgt)), (
        "P/N lateral order did not swap across the layer transition"
    )

    # negative control: the straight pair grew NO vias
    assert out["vias"]["/B_P"] == [] and out["vias"]["/B_N"] == [], (
        f"straight pair B grew vias: {out['vias']['/B_P']} {out['vias']['/B_N']}"
    )

    # coupling resumes outside the crossover window
    lo_w, hi_w = min(via_xs) - 3.0, max(via_xs) + 3.0
    x = _SRC_X + _STUB + 1.0
    uncoupled = []
    while x < _TGT_X - _STUB - 1.0:
        if not (lo_w <= x <= hi_w):
            py = _net_ys_at_x(out["segments"]["/A_P"], x)
            ny = _net_ys_at_x(out["segments"]["/A_N"], x)
            if py and ny:
                d = min(abs(a - b) for a in py for b in ny)
                if d > (_W + _GAP) * 2.0:
                    uncoupled.append((round(x, 1), round(d, 3)))
        x += 0.5
    assert not uncoupled, f"pair uncoupled outside crossover window: {uncoupled[:5]}"

    # kicad-cli DRC: 0 unconnected, no copper violations. `track_dangling`
    # is baselined against the UNROUTED fixture: kicad-cli flags the synthetic
    # fixture's stub-at-TH-pad ends as dangling even before routing (fixture
    # artifact), so the routed board must only not ADD any.
    if not _HAS_KICAD_CLI:
        pytest.skip("kicad-cli absent - copper assertions above still ran")

    def _drc(board_path: Path, out_name: str) -> dict:
        drc_out = tmp_path / out_name
        subprocess.run(
            [
                "kicad-cli",
                "pcb",
                "drc",
                str(board_path),
                "--format",
                "json",
                "-o",
                str(drc_out),
                "--severity-all",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(drc_out.read_text())

    drc = _drc(routed, "drc.json")
    assert drc.get("unconnected_items") == [], (
        f"unconnected items: {drc['unconnected_items']}"
    )
    copper_types = {
        "clearance",
        "tracks_crossing",
        "shorting_items",
        "hole_clearance",
        "hole_near_hole",
        "via_dangling",
    }
    copper_viols = [
        v for v in drc.get("violations", []) if v.get("type") in copper_types
    ]
    assert copper_viols == [], f"copper DRC violations: {copper_viols}"

    def _dangling(d: dict) -> int:
        return sum(
            1 for v in d.get("violations", []) if v.get("type") == "track_dangling"
        )

    baseline = _dangling(_drc(board, "drc_input.json"))
    assert _dangling(drc) <= baseline, (
        f"routing ADDED dangling track ends: {_dangling(drc)} > baseline {baseline}"
    )
