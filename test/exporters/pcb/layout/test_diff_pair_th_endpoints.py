# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_diff_pair_th_endpoints — through-hole pads must not collapse a diff pair's
source and target onto the same physical pads.

The failure mode this pins (found live on the F4 two-SATA-connector board): a
through-hole pad's `(*.Cu)` layer list expands to ONE endpoint tuple PER copper
layer at the SAME (x, y). `get_diff_pair_endpoints` picks the closest P–N
endpoint pair as the "source" and then removed only that exact tuple —
`[p for p in p_all if p != p_src]` — so the same physical pad's other-layer
copies stayed in the remaining pool, the "target" search re-picked the SAME
pads one layer down, and the router laid a ~10 mm serpentine loop P→N at one
connector (reported as success + polarity swap) while the far connector stayed
unconnected.

Contract pinned here (adversarial by construction, answer fixed): two nets
A_P/A_N, each with exactly two TH pads 40 mm apart on a 4-copper-layer config.
The returned source and target MUST sit at physically distinct positions —
one at each connector (|Δx| == 40 mm), for both P and N.

Runs the router under the SYSTEM interpreter (its deps are not in the venv),
same pattern as test_router_smoke_batch_route.py. Loudly SKIPS (never silently
passes) if the submodule is absent or system python3 cannot import the router.
"""

import json
import subprocess
import textwrap

import pytest

from faebryk.exporters.pcb.layout.layout_plan_runner import _system_python3
from faebryk.libs.util import repo_root

_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_SYS_PY = _system_python3()

_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    import diff_pair_routing  # noqa: F401
    print("ok")
    """
)

_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    sys.path.insert(0, sys.argv[1])

    from diff_pair_routing import get_diff_pair_endpoints
    from kicad_parser import BoardInfo, Net, PCBData, Pad
    from routing_config import GridRouteConfig

    LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def th_pad(ref, num, x, y, net_id, net_name):
        return Pad(
            component_ref=ref,
            pad_number=num,
            global_x=x,
            global_y=y,
            local_x=0.0,
            local_y=0.0,
            size_x=1.0,
            size_y=1.0,
            shape="circle",
            layers=["*.Cu"],  # through-hole: expands to every copper layer
            net_id=net_id,
            net_name=net_name,
            drill=0.7,
        )

    # Two connectors 40 mm apart; the pair pins sit 1.7 mm apart vertically —
    # far closer to each other than to the far connector, exactly the SATA
    # geometry that tripped the collapse.
    pads = {
        1: [th_pad("P1", "2", 10.0, 10.0, 1, "A_P"),
            th_pad("P2", "2", 50.0, 10.0, 1, "A_P")],
        2: [th_pad("P1", "3", 10.0, 11.7, 2, "A_N"),
            th_pad("P2", "3", 50.0, 11.7, 2, "A_N")],
    }
    pcb_data = PCBData(
        board_info=BoardInfo(
            layers={0: "F.Cu", 1: "In1.Cu", 2: "In2.Cu", 31: "B.Cu"},
            copper_layers=list(LAYERS),
            board_bounds=(0.0, 0.0, 60.0, 20.0),
        ),
        nets={
            1: Net(net_id=1, name="A_P", pads=pads[1]),
            2: Net(net_id=2, name="A_N", pads=pads[2]),
        },
        footprints={},
        vias=[],
        segments=[],
        pads_by_net=pads,
    )
    config = GridRouteConfig(layers=list(LAYERS))

    sources, targets, err = get_diff_pair_endpoints(pcb_data, 1, 2, config)
    out = {"error": err}
    if not err and sources and targets:
        # tuples: (p_gx, p_gy, n_gx, n_gy, layer, p_x, p_y, n_x, n_y)
        out["src_p_xy"] = [sources[0][5], sources[0][6]]
        out["src_n_xy"] = [sources[0][7], sources[0][8]]
        out["tgt_p_xy"] = [targets[0][5], targets[0][6]]
        out["tgt_n_xy"] = [targets[0][7], targets[0][8]]
    print("JSON_OUT:" + json.dumps(out))
    """
)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_SYS_PY), "-c", code, str(_ROUTER_ROOT)],
        capture_output=True,
        text=True,
        cwd=str(_ROUTER_ROOT),
        timeout=120,
    )


@pytest.fixture(scope="module", autouse=True)
def _require_router():
    if not (_ROUTER_ROOT / "diff_pair_routing.py").exists():
        pytest.skip("vendor/KiCadRoutingTools submodule not present")
    probe = _run(_PROBE)
    if probe.returncode != 0 or "ok" not in probe.stdout:
        pytest.skip(f"system python3 cannot import the router: {probe.stderr[-500:]}")


def test_th_pads_source_and_target_are_distinct_connectors():
    proc = _run(_SCRIPT)
    assert proc.returncode == 0, proc.stderr
    line = next(
        line for line in proc.stdout.splitlines() if line.startswith("JSON_OUT:")
    )
    out = json.loads(line.removeprefix("JSON_OUT:"))
    assert out["error"] is None, out

    # The pair must span the two connectors: source at one x, target at the
    # other, 40 mm apart — NOT the same pads on another copper layer.
    src_x = out["src_p_xy"][0]
    tgt_x = out["tgt_p_xy"][0]
    assert abs(src_x - tgt_x) == pytest.approx(40.0), (
        f"diff pair source/target collapsed onto one connector: "
        f"src P at x={src_x}, tgt P at x={tgt_x} (expected 40 mm apart) — {out}"
    )
    # N must agree with P about which end is which (same connector per end).
    assert out["src_n_xy"][0] == pytest.approx(src_x)
    assert out["tgt_n_xy"][0] == pytest.approx(tgt_x)
