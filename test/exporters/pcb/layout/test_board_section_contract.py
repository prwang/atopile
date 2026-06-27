# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_board_section_contract — D-Tier3 self-contained BOARD section: outline +
COMPLETE stackup (BACKLOG §D-Tier3, 桶① part 2: TB1-TB3 + TS1-TS5 + TS-LM = 19
cases) and the two layer-count authority ratchets (桶②: TS-AUTH-A/B = 2 cases).

== WHY ====================================================================

A pure-text end-to-end board needs the board-level facts every downstream stage
consumes to have a TEXT schema (or be loud when missing — no silent default):

  * `board.outline` — no outline is a silent disaster: the router routes in
    unbounded space and copper spills off-board (`obstacle_map.py:393-395`
    returns early with no board_bounds), the fab boundary is unknowable.
  * `board.stackup` — COMPLETE (ordered copper names + per-dielectric
    thickness/material/Er), NOT just a layer count: controlled differential
    impedance depends on the real stackup (`route.py:256-258` warns and falls
    back to a fixed width when impedance is requested without a stackup =
    impedance out of control = the whole D-Tier2 diff-pair coupling/impedance
    work wasted). A complete stackup is a HARD dependency, not a nice-to-have.

`board.stackup` is also the SINGLE authority for layer count, feeding BOTH the
atopile-generated board's layer table AND the router's `layers`. Today these are
two unrelated magic numbers: atopile defaults to 2 copper (`config.py:374,380`
F.Cu/B.Cu) while the router defaults to 4 (`routing_constants.py:8`
DEFAULT_4_LAYER_STACK, taken at `route.py:231` when no `layers` is passed) — so
the router would route to In1/In2.Cu layers that the board does not have.

== THE RATCHET (S0 discipline) ============================================

桶① (TB/TS, gated `_DT3_LANDED`): schema + pure-function oracle, all strict-xfail
until D-Tier3 lands; negatives paired with a positive control so they xfail
cleanly, never XPASS for the wrong reason.

TS-LM is the forensic LOCK: it is GREEN NOW and pins the two divergent magic
numbers with NO shared source. It is intentionally un-gated — it goes RED the
instant either number moves, INCLUDING the authoritative fix, at which point you
update it to the single-authority reality and TS-AUTH-A/B flip green.

桶② (TS-AUTH-A/B): the "the 2-layer/4-layer bug must be RED now" ratchets.
  * TS-AUTH-A (gated `_DT3_BUILD_LANDED`): the atopile-GENERATED board's copper
    layer table == `stackup_layers(board.stackup)` — kills the hardcoded 2-layer
    default. RED until the board generator derives layers from the stackup.
  * TS-AUTH-B (gated `_E1_LANDED`): E1 passes `stackup_layers(board.stackup)` to
    the router and never eats the 4-layer default. RED until E1 is built.
Both MUST be red until the single-authority fix lands (user requirement).
"""

import ast
import os
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import atopile.build_steps as _build_steps
import atopile.config as _config_mod
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.util import repo_root as _repo_root
from faebryk.libs.util import run_live

# ---------------------------------------------------------------------------
# S0 ratchet guard — import the FULL D-Tier3 surface; half-landing stays red.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.layout_plan import (  # type: ignore
        Board,
        BoardOutline,
        LayoutPlan,
        LayoutPlanError,
        Placement,  # noqa: F401
        Room,  # noqa: F401
        Stackup,
        StackupLayer,
        load_layout_plan,
        outline_bounds,
        resolve_placement,  # noqa: F401
        stackup_layers,
    )

    if not (
        "polygon" in Room.model_fields
        and "placements" in LayoutPlan.model_fields
        and "board" in LayoutPlan.model_fields
    ):
        raise ImportError("D-Tier3 model fields not present (half-landed)")
    _DT3_LANDED = True
except Exception:
    _DT3_LANDED = False

    class LayoutPlanError(Exception):  # placeholder so raises-tuples stay well-formed
        ...

    def _unlanded(*_a, **_k):
        raise RuntimeError("D-Tier3 not landed (S0 ratchet)")

    Board = BoardOutline = Stackup = StackupLayer = _unlanded
    LayoutPlan = load_layout_plan = outline_bounds = stackup_layers = _unlanded

needs_dt3 = pytest.mark.xfail(
    not _DT3_LANDED,
    reason="D-Tier3 board section not landed (S0 ratchet)",
    strict=True,
)


# ===========================================================================
# downstream-authority probes (AST over the source FILE — build steps are wrapped
# by @muster.register into a MusterTarget, so the live object is not
# introspectable; route a module is not importable from the test path).
# ===========================================================================
def _names_used(path: Path) -> set[str]:
    return {
        n.id for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Name)
    }


def _board_generator_uses_stackup_authority() -> bool:
    """The board generator derives its layer table from `stackup_layers` rather
    than the hardcoded F.Cu/B.Cu 2-layer default. Scoped to config.py — the board
    is created there (the `KicadPcb(layers=[...])` block) — so this gate and the
    TS-LM forensic lock (which pins `stackup_layers` absent from config.py until
    the fix) can never silently disagree on where the authority lands."""
    return "stackup_layers" in _names_used(Path(_config_mod.__file__))


def _e1_passes_stackup_to_router() -> bool:
    """E1 dispatch passes `stackup_layers(...)` as the router `layers`: it
    references the stackup authority (as a real Name, not a bare import) AND
    invokes a router entry. The router is dispatched as a SUBPROCESS string driver
    (the rust ext is not built for the venv), so its entry name lives in a string
    LITERAL — detect it as a source substring (ast.Constant), never an ast.Name."""
    src = Path(_build_steps.__file__).read_text()
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
    calls_router = "batch_route" in src  # string driver substring, not a Name
    return calls_router and "stackup_layers" in names


_DT3_BUILD_LANDED = _DT3_LANDED and _board_generator_uses_stackup_authority()
_E1_LANDED = _DT3_LANDED and _e1_passes_stackup_to_router()

needs_dt3_build = pytest.mark.xfail(
    not _DT3_BUILD_LANDED,
    reason="board generator does not derive layers from board.stackup (S0 ratchet)",
    strict=True,
)
needs_e1 = pytest.mark.xfail(
    not _E1_LANDED,
    reason="E1 does not pass stackup_layers to the router (S0 ratchet)",
    strict=True,
)


# a complete, minimal 2-copper stackup reused as the positive control everywhere.
def _complete_stackup_layers():
    return [
        StackupLayer(name="F.Cu", type="copper", thickness=0.035),
        StackupLayer(
            name="d1", type="dielectric", thickness=1.51, material="FR4", epsilon_r=4.5
        ),
        StackupLayer(name="B.Cu", type="copper", thickness=0.035),
    ]


# ===========================================================================
# TB1 — board.outline positive, both forms (origin/size and polygon).
# ===========================================================================
@needs_dt3
def test_outline_both_forms_parse():
    rect = BoardOutline(origin=(0.0, 0.0), size=(50.0, 30.0))
    assert tuple(rect.origin) == (0.0, 0.0) and tuple(rect.size) == (50.0, 30.0)

    tri = BoardOutline(polygon=[(0.0, 0.0), (50.0, 0.0), (25.0, 30.0)])
    assert [tuple(p) for p in tri.polygon] == [(0.0, 0.0), (50.0, 0.0), (25.0, 30.0)]


# ===========================================================================
# TB2 — board.outline negative (loud), paired with a positive control.
# ===========================================================================
_BOWTIE = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]


@needs_dt3
@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"polygon": [(0.0, 0.0), (1.0, 1.0)]}, "3 points"),  # < 3 points
        ({"polygon": _BOWTIE}, "self-intersecting"),  # self-intersecting
    ],
    ids=["too_few_points", "self_intersecting"],
)
def test_outline_invalid_is_loud(kwargs, msg):
    assert BoardOutline(origin=(0.0, 0.0), size=(5.0, 5.0)).size is not None  # control
    with pytest.raises(ValidationError) as ei:
        BoardOutline(**kwargs)
    assert msg in str(ei.value)  # rejected for the RIGHT reason


# ===========================================================================
# TB3 — outline missing ⇒ LOUD (sign-off): the bounds authority refuses to invent
# an unbounded board. Pins the silent `obstacle_map.py:393-395` no-bounds disaster.
# ===========================================================================
@needs_dt3
def test_missing_outline_bounds_is_loud():
    bounds = outline_bounds(
        BoardOutline(origin=(2.0, 3.0), size=(10.0, 6.0))
    )  # control
    assert bounds == pytest.approx((2.0, 3.0, 12.0, 9.0))
    with pytest.raises(LayoutPlanError):
        outline_bounds(None)  # no silent default — loud


# ===========================================================================
# TS1 — board.stackup positive, FIELD-BY-FIELD (complete, not just count).
# ===========================================================================
@needs_dt3
def test_stackup_fields_complete():
    stk = Stackup(layers=_complete_stackup_layers())
    coppers = [layer for layer in stk.layers if layer.type == "copper"]
    assert [c.name for c in coppers] == ["F.Cu", "B.Cu"]  # ordered copper names
    (diel,) = [layer for layer in stk.layers if layer.type == "dielectric"]
    assert diel.thickness == pytest.approx(1.51)  # per-dielectric thickness
    assert diel.material == "FR4"  # material
    assert diel.epsilon_r == pytest.approx(4.5)  # Er


# ===========================================================================
# TS2 — board.stackup negative (loud), paired with a positive control. Note the
# FIRST case: "just a layer count, no dielectric" is degenerate and EXPLICITLY
# rejected — a stackup must be complete, never silently reduced to a count.
# ===========================================================================
@needs_dt3
@pytest.mark.parametrize(
    "layers, msg",
    [
        # only copper, no dielectric between them ⇒ incomplete (= just a count)
        (
            [
                {"name": "F.Cu", "type": "copper", "thickness": 0.035},
                {"name": "B.Cu", "type": "copper", "thickness": 0.035},
            ],
            "adjacent",
        ),
        # dielectric thickness ≤ 0 (rejected at the StackupLayer level)
        (
            [
                {"name": "F.Cu", "type": "copper", "thickness": 0.035},
                {
                    "name": "d1",
                    "type": "dielectric",
                    "thickness": 0.0,
                    "material": "FR4",
                    "epsilon_r": 4.5,
                },
                {"name": "B.Cu", "type": "copper", "thickness": 0.035},
            ],
            "thickness",
        ),
        # duplicate layer names
        (
            [
                {"name": "F.Cu", "type": "copper", "thickness": 0.035},
                {
                    "name": "d1",
                    "type": "dielectric",
                    "thickness": 1.51,
                    "material": "FR4",
                    "epsilon_r": 4.5,
                },
                {"name": "F.Cu", "type": "copper", "thickness": 0.035},
            ],
            "unique",
        ),
        # fewer than 2 copper layers
        (
            [
                {"name": "F.Cu", "type": "copper", "thickness": 0.035},
            ],
            "copper",
        ),
    ],
    ids=[
        "count_only_no_dielectric",
        "nonpositive_thickness",
        "dup_names",
        "copper_lt_2",
    ],
)
def test_stackup_invalid_is_loud(layers, msg):
    assert len(Stackup(layers=_complete_stackup_layers()).layers) == 3  # control
    with pytest.raises(ValidationError) as ei:
        Stackup(layers=[StackupLayer(**layer) for layer in layers])
    assert msg in str(ei.value)  # rejected for the RIGHT reason


# ===========================================================================
# TS3 — stackup → layer table SINGLE authority (pure oracle == hand-computed).
# This is the ONLY truth for layer count (both 桶② ratchets derive from it).
# ===========================================================================
@needs_dt3
def test_stackup_layers_is_the_ordered_copper_set():
    two = Stackup(layers=_complete_stackup_layers())
    assert stackup_layers(two) == ["F.Cu", "B.Cu"]  # hand-computed

    four = Stackup(
        layers=[
            StackupLayer(name="F.Cu", type="copper", thickness=0.035),
            StackupLayer(
                name="d1",
                type="dielectric",
                thickness=0.2,
                material="PP",
                epsilon_r=4.2,
            ),
            StackupLayer(name="In1.Cu", type="copper", thickness=0.035),
            StackupLayer(
                name="d2",
                type="dielectric",
                thickness=1.0,
                material="Core",
                epsilon_r=4.5,
            ),
            StackupLayer(name="In2.Cu", type="copper", thickness=0.035),
            StackupLayer(
                name="d3",
                type="dielectric",
                thickness=0.2,
                material="PP",
                epsilon_r=4.2,
            ),
            StackupLayer(name="B.Cu", type="copper", thickness=0.035),
        ]
    )
    assert stackup_layers(four) == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]  # ordered


# ===========================================================================
# TS4 — stackup missing ⇒ LOUD (sign-off): the layer authority refuses a silent
# default. Mirrors TB3 for the stackup half.
# ===========================================================================
@needs_dt3
def test_missing_stackup_is_loud():
    assert stackup_layers(Stackup(layers=_complete_stackup_layers())) == [
        "F.Cu",
        "B.Cu",
    ]  # control
    with pytest.raises(LayoutPlanError):
        stackup_layers(None)  # no silent default — loud


# ===========================================================================
# TS5 — impedance HARD dependency: a route stage requesting impedance with NO
# board.stackup is rejected at parse. Pins route.py:256-258 (impedance without a
# stackup silently falls back to a fixed width = impedance out of control).
# ===========================================================================
_IMPEDANCE_NO_STACKUP = """
route_stages:
  - name: hs
    mode: diff
    config:
      impedance: 100
"""
_IMPEDANCE_WITH_STACKUP = """
board:
  stackup:
    layers:
      - {name: F.Cu, type: copper, thickness: 0.035}
      - {name: d1, type: dielectric, thickness: 1.51, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu, type: copper, thickness: 0.035}
route_stages:
  - name: hs
    mode: diff
    config:
      impedance: 100
"""


@needs_dt3
def test_impedance_without_stackup_is_loud():
    ok = load_layout_plan(_IMPEDANCE_WITH_STACKUP)  # control: complete stackup ⇒ ok
    assert ok.board is not None and ok.board.stackup is not None
    with pytest.raises(LayoutPlanError):
        load_layout_plan(_IMPEDANCE_NO_STACKUP)


# ===========================================================================
# TS-LM — layer-count divergence FORENSIC LOCK (un-gated, GREEN NOW).
#
# Pins the two unauthoritative magic numbers with NO shared source:
#   * atopile default board copper = [F.Cu, B.Cu]   (config.py:374,380, 2 layers)
#   * router default = DEFAULT_4_LAYER_STACK         (routing_constants.py:8, 4)
# Goes RED the instant either number moves — INCLUDING the authoritative fix (at
# which point: replace this with TS-AUTH-A/B-green and a single-authority lock).
# ===========================================================================
def _config_signal_layers() -> list[str]:
    """The copper (SIGNAL) layer NAMES atopile writes into a fresh board."""
    names = []
    for node in ast.walk(ast.parse(Path(_config_mod.__file__).read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Layer"
        ):
            kw = {k.arg: k.value for k in node.keywords}
            t, nm = kw.get("type"), kw.get("name")
            if (
                isinstance(t, ast.Attribute)
                and t.attr == "SIGNAL"
                and isinstance(nm, ast.Constant)
            ):
                names.append(nm.value)
    return names


def _router_default_stack() -> list[str]:
    src = (
        _repo_root() / "vendor" / "KiCadRoutingTools" / "routing_constants.py"
    ).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DEFAULT_4_LAYER_STACK"
            for t in node.targets
        ):
            return [e.value for e in node.value.elts]
    raise AssertionError("DEFAULT_4_LAYER_STACK not found")


def test_layer_count_defaults_diverge_with_no_shared_source():
    atopile_default = _config_signal_layers()
    router_default = _router_default_stack()
    config_src = Path(_config_mod.__file__).read_text()

    # the two divergent magic numbers, pinned by value
    assert set(atopile_default) == {"F.Cu", "B.Cu"}  # config.py:374,380 — 2 layers
    assert "In1.Cu" not in atopile_default and "In2.Cu" not in atopile_default
    assert router_default == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]  # 4 layers

    # they genuinely diverge in count
    assert len(atopile_default) == 2 and len(router_default) == 4

    # and NO shared source: the board generator references neither the router
    # default nor a stackup authority — proving the two numbers are unrelated.
    # (BOTH references appear only once the single-authority fix lands; then this
    # forensic lock goes red and is replaced by the TS-AUTH ratchets going green.)
    assert "DEFAULT_4_LAYER_STACK" not in config_src
    assert "stackup_layers" not in config_src


# ===========================================================================
# TS-AUTH-A — board layer-table authority (桶②, gated _DT3_BUILD_LANDED, RED now).
# The atopile-GENERATED board's copper layer table == stackup_layers(board.stackup)
# — asserted on the REAL built board (schema-parse alone is NOT enough). Kills the
# config.py:374,380 hardcoded 2-layer default. Flips green when the generator
# derives its layers from the stackup authority.
# ===========================================================================
_AUTH_EXAMPLE = _repo_root() / "examples" / "sata_bundle"  # carries a board.stackup


def _build(cwd: Path) -> None:
    bindir = os.path.dirname(sys.executable)
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-b", "top", "-v"],
        env={
            **os.environ,
            "NONINTERACTIVE": "1",
            "FBRK_PARTS_NO_REFRESH": "y",
            "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
        },
        cwd=cwd,
        stdout=print,
        stderr=print,
        timeout=300,
    )
    out = stdout + stderr
    assert "Build successful! 🚀" in out, out[-2000:]


def _copper_layers(pcb) -> list[str]:
    return [layer.name for layer in pcb.layers if layer.type == "signal"]


@pytest.fixture(scope="module")
def built_board(tmp_path_factory):
    """Build the authority example once; return (generated copper layers, expected
    from stackup). Skipped (not run) until the build wiring lands ⇒ clean xfail."""
    if not _DT3_BUILD_LANDED:
        return None  # skip the expensive copy+build while strict-xfailing
    work = tmp_path_factory.mktemp("dt3_auth_build")
    dst = work / "sata_bundle"
    shutil.copytree(_AUTH_EXAMPLE, dst)
    _build(dst)
    boards = list((dst / "build" / "builds" / "top").glob("*.kicad_pcb"))
    assert boards, "no generated .kicad_pcb"
    pcb = kicad.loads(kicad.pcb.PcbFile, boards[0].read_text()).kicad_pcb
    plan = load_layout_plan((_AUTH_EXAMPLE / "layout.yaml"))
    return _copper_layers(pcb), stackup_layers(plan.board.stackup)


@needs_dt3_build
@pytest.mark.not_in_ci
@pytest.mark.slow
def test_generated_board_layers_equal_stackup_authority(built_board):
    generated, expected = built_board
    assert generated == expected  # the board's copper table == the stackup authority
    # the 2-layer hardcode is dead: no ghost In*/B-only default sneaking through
    assert "In1.Cu" not in generated or "In1.Cu" in expected


# ===========================================================================
# TS-AUTH-B — router layer-table authority (桶②, gated _E1_LANDED, RED now).
# E1 passes stackup_layers(board.stackup) to the router as `layers`, NEVER eating
# the route.py:231 4-layer default. Flips green when E1 is built.
# ===========================================================================
@needs_e1
def test_e1_passes_stackup_layers_to_router():
    # E1 sources the router's `layers` from the stackup authority, not the default
    assert _e1_passes_stackup_to_router()
    # the router default it must NOT eat (documented, for the failure message)
    assert _router_default_stack() == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
