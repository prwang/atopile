# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_router_smoke_batch_route — the E3 thin slice: the router's structured-results
API + JSON_SUMMARY schema (BACKLOG §C "下游契约 + 自测计划", §E E3).

This is the minimal regression bench atopile's §C consumer-oracle tests depend on
(a forced via is honored; a copied room stays connected) — pinned at C-time so the
C→E interface is locked BEFORE §E is written, moving back-patch risk from
"discovered while writing E" to "caught at C". It pins the C→E interface only,
nothing about routing quality:

  1. `batch_route_diff_pairs(..., return_results=True)` returns the documented
     4-tuple (successful, failed, total_time, results_data);
  2. `results_data` carries the keys a caller applies to a board (`results`,
     `all_swap_vias`, ...) and each per-pair result carries the geometry fields a
     consumer reads (`new_segments`, `new_vias`, net ids, P/N paths);
  3. the `JSON_SUMMARY: {...}` stdout line is valid JSON with the stable summary
     schema downstream parses.

The router (`vendor/KiCadRoutingTools`, a submodule) runs under the SYSTEM
interpreter — its rust grid_router ext is not built for the atopile venv — so this
shells out to `python3`, the same pattern as the §C router-oracle in
test_room_ops_contract.py. Board: the submodule's 2-layer LVDS board (one routable
diff pair), so the smoke is fast (~0.1s) and self-contained. Loudly SKIPS (never
silently passes) if the submodule is absent or system python3 cannot import the
router.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from faebryk.libs.util import repo_root

_ROUTER_ROOT = repo_root() / "vendor" / "KiCadRoutingTools"
_BOARD = _ROUTER_ROOT / "kicad_files" / "lvds_converter_dualclk.kicad_pcb"
_SYS_PY = shutil.which("python3")

# the C→E contract this slice pins (kept here, in the test, so a drift goes red).
_RESULTS_DATA_KEYS = {"results", "all_swap_vias", "exclusion_zone_lines"}
_RESULT_GEOMETRY_KEYS = {
    "new_segments", "new_vias", "p_net_id", "n_net_id", "p_path", "n_path"
}
_SUMMARY_SCHEMA = {
    "routed_diff_pairs": list,
    "failed_diff_pairs": list,
    "polarity_swapped_pairs": list,
    "target_swaps": list,
    "successful": int,
    "failed": int,
    "total_time": (int, float),
    "total_iterations": int,
    "total_vias": int,
}

needs_router_board = pytest.mark.skipif(
    not _BOARD.exists() or _SYS_PY is None,
    reason=f"router submodule board or system python3 absent ({_BOARD})",
)


def _run_router() -> dict:
    """Shell out to system python3, route the one diff pair with structured
    results, and return the JSON the driver emits. Skips loudly if the router can't
    be imported (ext not built for this python3) — never a silent pass."""
    header = (
        f"import sys; sys.path.insert(0, {str(_ROUTER_ROOT)!r})\n"
        f"BOARD = {str(_BOARD)!r}\n"
    )
    body = r'''
import io, json, os, re, tempfile
from contextlib import redirect_stdout
import route_diff
NETS = ["/DATA+", "/DATA-"]
GEOM = dict(layers=["F.Cu", "B.Cu"], track_width=0.2, diff_pair_gap=0.25, clearance=0.2)
fd, out = tempfile.mkstemp(suffix=".kicad_pcb", prefix="smoke_"); os.close(fd)
try:
    buf = io.StringIO()
    with redirect_stdout(buf):
        res = route_diff.batch_route_diff_pairs(
            BOARD, out, NETS, return_results=True, verbose=False, **GEOM
        )
    m = re.search(r"JSON_SUMMARY: (\{.*\})", buf.getvalue())
    summary = json.loads(m.group(1)) if m else None
finally:
    if os.path.exists(out):
        os.remove(out)
successful, failed, total_time, data = res
print("JSON_OUT" + json.dumps({
    "tuple_len": len(res),
    "successful": successful,
    "failed": failed,
    "total_time_is_num": isinstance(total_time, (int, float)),
    "data_keys": sorted(data.keys()),
    "per_pair_keys": [sorted(r.keys()) for r in data.get("results", [])],
    "summary": summary,
}))
'''
    proc = subprocess.run(
        [_SYS_PY, "-c", header + body],
        capture_output=True, text=True, cwd=str(_ROUTER_ROOT), timeout=120,
    )
    if proc.returncode != 0:
        if "ModuleNotFoundError" in proc.stderr or "ImportError" in proc.stderr:
            pytest.skip(
                "system python3 cannot import the router (ext not built): "
                + proc.stderr.strip()[-300:]
            )
        raise AssertionError(f"router driver failed:\n{proc.stderr[-2000:]}")
    line = next(
        ln for ln in proc.stdout.splitlines() if ln.startswith("JSON_OUT")
    )
    return json.loads(line[len("JSON_OUT"):])


@pytest.fixture(scope="module")
def routed() -> dict:
    return _run_router()


@needs_router_board
def test_return_results_tuple_shape(routed):
    assert routed["tuple_len"] == 4, "return_results=True must yield a 4-tuple"
    assert isinstance(routed["successful"], int) and isinstance(routed["failed"], int)
    assert routed["total_time_is_num"]
    # the one routable pair must route (proves the smoke exercised the router,
    # not an early-out empty result)
    assert routed["successful"] == 1 and routed["failed"] == 0, (
        f"expected 1/0, got {routed['successful']}/{routed['failed']}"
    )


@needs_router_board
def test_results_data_schema(routed):
    missing = _RESULTS_DATA_KEYS - set(routed["data_keys"])
    assert not missing, f"results_data missing keys: {sorted(missing)}"
    assert routed["per_pair_keys"], "no per-pair results"
    for keys in routed["per_pair_keys"]:
        miss = _RESULT_GEOMETRY_KEYS - set(keys)
        assert not miss, f"per-pair result missing geometry keys: {sorted(miss)}"


@needs_router_board
def test_json_summary_schema(routed):
    summary = routed["summary"]
    assert summary is not None, "no JSON_SUMMARY line on stdout"
    for key, typ in _SUMMARY_SCHEMA.items():
        assert key in summary, f"JSON_SUMMARY missing {key!r}"
        assert isinstance(summary[key], typ), (
            f"JSON_SUMMARY[{key!r}] is {type(summary[key]).__name__}, want {typ}"
        )
    # counts agree with the lists (internally consistent)
    assert summary["successful"] == len(summary["routed_diff_pairs"])
    assert summary["failed"] == len(summary["failed_diff_pairs"])
