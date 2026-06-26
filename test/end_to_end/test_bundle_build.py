# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_bundle_build — D-Tier2 bundle, the build-level half (BACKLOG §D-Tier2).

The bundle unit contract (`test_bundle_contract.py`) pins the model, the geometry
SSOT (`cross_section_offsets`) and the `bundle_artifact` fragment in isolation. But
a green unit suite proves only that the function is correct — NOT that the build
actually calls it. Until `generate_layout_plan` invokes `bundle_artifact`, the
computed cross-section never reaches `<t>.layout_plan.json` and E1 would have to
re-derive geometry (violating the SSOT). This file closes that hole: it drives a
REAL bundle through a REAL build and asserts the on-disk artifact carries the
COMPUTED geometry — and that those offsets equal the SSOT function, so the artifact
is provably not a second source.

The fixture is `examples/sata_bundle`: a SATA channel (host ⇄ device) over a TX and
an RX differential pair, routed as one bundle from the host (northeast) down to the
device (south) — a rigid run that necks down (a transition segment) into the device
breakout. It ships its own local part + reuse board, so it builds fully offline.

S0 ratchet: `_BUNDLE_BUILD_WIRED` AST-probes that `generate_layout_plan` references
`bundle_artifact`. Revert the wiring and every test here goes strict-xfail — the
unit suite alone cannot keep it green. The expensive build runs only when wired.
"""

import ast
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

import atopile.build_steps as _build_steps
from atopile.config import BuildTargetPaths
from faebryk.exporters.pcb.layout.bundle_geometry import cross_section_offsets
from faebryk.exporters.pcb.layout.layout_plan import BundleStage, load_layout_plan
from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

EXAMPLE = _repo_root() / "examples" / "sata_bundle"
PLAN_JSON = Path("build") / "builds" / "top" / "top.layout_plan.json"

def _generate_layout_plan_calls_bundle_artifact() -> bool:
    """AST-probe the build step's source (it is wrapped by @muster.register into a
    MusterTarget, so its source is not introspectable via the live object): the
    `generate_layout_plan` FunctionDef must reference `bundle_artifact`."""
    src = Path(_build_steps.__file__).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "generate_layout_plan":
            return any(
                isinstance(n, ast.Name) and n.id == "bundle_artifact"
                for n in ast.walk(node)
            )
    return False


# the bundle build step exists (D4) AND generate_layout_plan actually calls
# bundle_artifact (the wiring this file guards). Either missing ⇒ strict-xfail.
_D4_LANDED = hasattr(_build_steps, "generate_layout_plan") and (
    "layout_config" in BuildTargetPaths.model_fields
)
_BUNDLE_BUILD_WIRED = _D4_LANDED and _generate_layout_plan_calls_bundle_artifact()
needs_bundle_build = pytest.mark.xfail(
    not _BUNDLE_BUILD_WIRED,
    reason="generate_layout_plan does not call bundle_artifact (S0 ratchet)",
    strict=True,
)

# the SATA bundle member order (lane order, diff P before N) and its resolved nets.
_MEMBERS = [
    "host.tx.p.line",
    "host.tx.n.line",
    "host.rx.p.line",
    "host.rx.n.line",
]
_RESOLVED = ["tx_P", "tx_N", "rx_P", "rx_N"]


def _build(cwd: Path) -> str:
    bindir = os.path.dirname(sys.executable)
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-b", "top", "-v"],
        env={
            **os.environ,
            "NONINTERACTIVE": "1",
            "FBRK_PARTS_NO_REFRESH": "y",
            "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
        },
        cwd=cwd, stdout=print, stderr=print, timeout=300,
    )
    out = stdout + stderr
    assert "Build successful! 🚀" in out, out[-2000:]
    return out


@pytest.fixture(scope="module")
def built_artifact(tmp_path_factory) -> dict:
    """Build the SATA example once and return the parsed layout-plan artifact.

    When the wiring has not landed the expensive build is skipped (not the test):
    the bodies then fail cheaply on the missing artifact — a clean strict-xfail."""
    work = tmp_path_factory.mktemp("sata_bundle_build")
    dst = work / "sata_bundle"
    shutil.copytree(EXAMPLE, dst)
    if not _BUNDLE_BUILD_WIRED:
        return {}
    _build(dst)
    return json.loads((dst / PLAN_JSON).read_text())


@needs_bundle_build
@pytest.mark.not_in_ci  # requires a full build (kicad-cli + parts)
@pytest.mark.slow
def test_bundle_stage_lands_in_artifact(built_artifact):
    assert "route_stages" in built_artifact
    bundles = [s for s in built_artifact["route_stages"] if s.get("type") == "bundle"]
    assert len(bundles) == 1, "the SATA bundle stage is missing from the artifact"
    st = bundles[0]
    assert st["name"] == "sata_link"
    # the bundle's nets resolved through bridge②, in member order
    assert st["resolved_nets"] == _RESOLVED


@needs_bundle_build
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_artifact_offsets_equal_the_geometry_ssot(built_artifact):
    """The COMPUTED cross-section must reach the artifact AND equal the SSOT
    function — proving the build injects geometry, not that it re-derives it."""
    (st,) = [s for s in built_artifact["route_stages"] if s.get("type") == "bundle"]
    members = st["members"]
    assert [m["net"] for m in members] == _MEMBERS  # lane order, P before N

    # recompute from the example's own layout.yaml via the SSOT and compare
    plan = load_layout_plan((EXAMPLE / "layout.yaml").read_text())
    (stage,) = [s for s in plan.route_stages if isinstance(s, BundleStage)]
    entry_spacing = stage.trunk.centerline[0].spacing
    # mirror bundle_artifact's default_width exactly (the bundle's track_width)
    oracle = cross_section_offsets(
        stage.lanes, entry_spacing, default_width=stage.config.track_width
    )
    assert [m["offset"] for m in members] == [
        pytest.approx(o.offset) for o in oracle
    ]
    # and the realized cross-section is centered (symmetric about the centerline)
    left = min(m["offset"] - m["width"] / 2 for m in members)
    right = max(m["offset"] + m["width"] / 2 for m in members)
    assert left == pytest.approx(-right)


@needs_bundle_build
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_artifact_trunk_is_segmented_ne_to_south(built_artifact):
    """The NE→south trunk: two equal spacings ⇒ a rigid run, the final unequal pair
    ⇒ a transition (the neck-down into the device breakout)."""
    (st,) = [s for s in built_artifact["route_stages"] if s.get("type") == "bundle"]
    assert [s["kind"] for s in st["trunk"]["segments"]] == ["rigid", "transition"]
    # breakouts: host derives order from the lanes; device carries the explicit
    # mirrored permutation from layout.yaml.
    bo = {b["at"]: b["order"] for b in st["breakouts"]}
    assert bo["host"] == _MEMBERS
    assert bo["device"] == list(reversed(_MEMBERS))
