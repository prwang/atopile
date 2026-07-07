# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
test_layout_plan_contract — stage D2 acceptance contract (BACKLOG §D, task D2).

== THE PROTOCOL ===========================================================

layout.yaml is the layout-INTENT source (peer of the .ato circuit source and the
.kicad_pcb geometry source). D2 parses & validates it into a pydantic
`LayoutPlan`, the single typed object every downstream stage (D3 rule areas, E1
router) consumes. Everything is addressed by **ato address**, never by net name
or designator — net names drift (v10 has no net table) and designators are
unstable, so the text layer may only name ato addresses, which the IR / bridge②
translate.

Shape (the interface D2 lands; field names are the pinned contract):

    LayoutPlan
      rooms:        list[Room]
      route_stages: list[RouteStage]

    Room                       # a placement region == one footprint sheetname
      module:   str            # ato address (== the room's sheetname value, §C3)
      origin:   (x, y) | None  # mm, a rectangle corner; None ⇒ derive from members
      size:     (w, h) | None  # mm; None ⇒ derive bbox from member pads (D3)
      rotation: float = 0.0
      layers:   list[str]
      anchor:   str | None

    RouteStage
      name:   str
      nets:   list[str]              # ato signal ADDRESSES, resolved via bridge②
      mode:   "diff" | "single"      # drives the §C dual-entry dispatch (D-spec)
      config: GridRouteOverride

    GridRouteOverride              # optional subset of the router ENTRY kwargs
      <field>: ...                 # field names == batch_route/batch_route_diff_pairs
                                   # kwargs (NOT GridRouteConfig — see drift guard)

    load_layout_plan(source) -> LayoutPlan   # parse YAML text/path, validate loudly
    LayoutPlan.resolve_nets(ir) -> dict[str, list[str]]
                                   # {stage name -> [kicad net name]}, each net
                                   # address resolved through ir["signal_nets"]

== FORM: declarative placement + ORDERED routing pipeline =================

`rooms` is declarative (a set of regions; order-free). `route_stages` is an
ORDERED PIPELINE: stages run in list order over accumulating board state, so the
order IS part of the intent — reordering is a semantic change, never cosmetic.
It is a `list`, never a set, and the order is preserved verbatim through
parse + resolve (pinned by `test_route_stage_order_is_preserved`). See the
`layout_plan` module docstring for the full why-data-not-a-script rationale.

== LOUD-OR-NOTHING (S5a) ==================================================

Nothing is silently dropped or coerced. The following are hard errors, not
warnings or best-effort:
  * an unknown YAML key at any level (top / room / stage / config) — a typo or a
    router field the override does not expose must not be silently swallowed;
  * `mode` outside the closed set {diff, single} — the dual-entry dispatch is
    not allowed to fall through to a default;
  * partial room geometry — exactly one of origin/size given (both ⇒ explicit
    rectangle, neither ⇒ derived bbox, one ⇒ ambiguous) and a non-positive size;
  * a net reference that is not a resolvable ato signal address — a bare kicad
    net name, or an address with no stable name in bridge② (I4) — raised at
    resolve time, never resolved to "";
  * a config key not accepted by the entry the stage's `mode` dispatches to — the
    two router entries take DIFFERENT kwargs (guide_corridor_* single-only,
    diff_pair_* diff-only), so a wrong-mode key is rejected at parse time rather
    than passed to a kwarg expansion that TypeErrors in E1 (D2.5).

== CONSUMER-ORACLE (the D-spec ② pin) =====================================

D2 must not reinvent address→net resolution: `resolve_nets` is exactly a lookup
into the IR's bridge② (`ir["signal_nets"]`, the one graph-derived map that the
rest of atopile uses). Pinned two ways per the test discipline:
  * fast/constructed: resolution is verbatim delegation to ir["signal_nets"]
    (no transformation, no fallback);
  * slow/real: on examples/layout_reuse, resolving a stage's real signal
    addresses yields exactly the live `signal_nets` values — so a future change
    to net naming is caught here, not in production.

== GridRouteOverride DRIFT GUARD (the D-spec ③ pin) =======================

Every GridRouteOverride field must be a real router-entry KWARG, so a YAML plan
can never emit a key the router rejects (E1 expands the override 1:1 into
batch_route / batch_route_diff_pairs **kwargs). The oracle is therefore the
*union of those two functions' parameter names*, NOT routing_config.GridRouteConfig
— verified independently 2026-06-16: the entries take flat kwargs and build a
GridRouteConfig internally, translating some names (e.g. the kwarg `impedance` ->
the field `impedance_target`, `power_nets_widths` -> `power_net_widths`), and the
kwarg set differs from the dataclass by 15 router-only knobs + 19 dataclass-only
fields. Using GridRouteConfig as the oracle would both wrongly reject valid knobs
and wrongly admit fields that TypeError when expanded. Some kwargs are
mode-exclusive (guide_corridor_* single-only; diff_pair_*/fix_polarity/gnd_via_*
diff-only) — the union admits them at the model level; rejecting a key used with
the wrong mode is E1's stage-dispatch concern. If the router repo is absent the
test is loudly skipped (never silently passed).

== THE RATCHET (S0 discipline) ============================================

`_D2_LANDED` is a pure import probe: the layout_plan module + its public symbols
present == landed. Until then every test is strict-xfail (the symbols bind to a
sentinel that raises, so the bodies fail), and the flip to green is automatic
when D2 lands. A half-landed module (some symbols missing) keeps it red.
"""

import ast
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import faebryk.library._F as F  # noqa: F401  # prevents a circular import

# ---------------------------------------------------------------------------
# S0 ratchet guard: probe the not-yet-existing layout_plan module + symbols.
# ---------------------------------------------------------------------------
try:
    from faebryk.exporters.pcb.layout.layout_plan import (  # type: ignore
        GridRouteOverride,
        LayoutPlan,
        LayoutPlanError,
        Room,
        RouteStage,
        load_layout_plan,
    )

    _D2_LANDED = True
except Exception:  # module / symbol not present yet
    _D2_LANDED = False

    class LayoutPlanError(Exception):  # placeholder so raises-tuples stay well-formed
        ...

    def _unlanded(*_a, **_k):
        # a distinct error type the loud-tests' pytest.raises will NOT catch, so
        # an unlanded D2 produces a clean XFAIL (never an accidental XPASS).
        raise RuntimeError("D2 layout_plan not landed (S0 ratchet)")

    LayoutPlan = Room = RouteStage = GridRouteOverride = load_layout_plan = _unlanded

needs_d2 = pytest.mark.xfail(
    not _D2_LANDED,
    reason="D2 layout.yaml plan model not landed (S0 ratchet)",
    strict=True,
)

# loud errors the validation paths may raise (named D error OR pydantic's own)
_LOUD = (LayoutPlanError, ValidationError)


# ---------------------------------------------------------------------------
# minimal valid plan text + helpers
# ---------------------------------------------------------------------------
_VALID_YAML = """
rooms:
  - module: top.power_supply_3v3
    origin: [10.0, 20.0]
    size: [15.0, 12.0]
    rotation: 0
    layers: [F.Cu]
  - module: top.mcu
route_stages:
  - name: power
    mode: single
    nets:
      - top.power_supply_3v3.vout
    config:
      track_width: 0.3
      clearance: 0.15
  - name: usb
    mode: diff
    nets:
      - top.usb.dp
    config: {}
"""


# route_stages require a `rules:` header since the DesignRules contract (see
# test_design_rules_contract.py, which pins the loudness); these D2 fixtures
# predate it and are rules-agnostic, so inject a minimal header where absent.
_DEFAULT_RULES = (
    "rules:\n"
    "  clearance: 0.1\n"
    "  track_width: 0.15\n"
    "  diff_pair_width: 0.15\n"
    "  diff_pair_gap: 0.15\n"
)


def _plan(yaml_text: str) -> "LayoutPlan":
    if "rules:" not in yaml_text:
        yaml_text = _DEFAULT_RULES + yaml_text
    return load_layout_plan(yaml_text)


# ===========================================================================
# D2.1 — the model parses a well-formed plan into typed objects.
# ===========================================================================
@needs_d2
def test_parses_well_formed_plan():
    plan = _plan(_VALID_YAML)
    assert isinstance(plan, LayoutPlan)
    assert [r.module for r in plan.rooms] == ["top.power_supply_3v3", "top.mcu"]
    # explicit geometry kept verbatim; omitted geometry stays None (derived later)
    psu, mcu = plan.rooms
    assert tuple(psu.origin) == (10.0, 20.0)
    assert tuple(psu.size) == (15.0, 12.0)
    assert mcu.origin is None and mcu.size is None
    # stages keep their declared mode (closed set) and a typed override
    stages = {s.name: s for s in plan.route_stages}
    assert stages["power"].mode == "single"
    assert stages["usb"].mode == "diff"
    assert isinstance(stages["power"].config, GridRouteOverride)
    assert stages["power"].nets == ["top.power_supply_3v3.vout"]


# ===========================================================================
# D2.1b — route_stages is an ORDERED PIPELINE, not a set. The list order is the
# intent (earlier stages claim copper later ones avoid), so it must survive parse
# AND resolve verbatim — in BOTH orderings, so a sort/set refactor goes red.
# ===========================================================================
@needs_d2
@pytest.mark.parametrize(
    "order",
    [["alpha", "beta", "gamma"], ["gamma", "beta", "alpha"]],
    ids=["forward", "reversed"],
)
def test_route_stage_order_is_preserved(order):
    bridge = {"top.a.x": "VNET1"}
    stages = "".join(
        f"  - name: {n}\n    mode: single\n    nets: [top.a.x]\n    config: {{}}\n"
        for n in order
    )
    plan = _plan(f"rooms: []\nroute_stages:\n{stages}")
    # parse order == declared order (not sorted, not deduped-into-a-set)
    assert [s.name for s in plan.route_stages] == order
    # resolve preserves it too (dict insertion order == stage order)
    assert list(plan.resolve_nets(_ir(bridge)).keys()) == order


# ===========================================================================
# D2.2 — LOUD validation (S5a). Unknown keys / bad enums / bad geometry raise.
# ===========================================================================
@needs_d2
@pytest.mark.parametrize(
    "yaml_text",
    [
        # unknown top-level key
        "rooms: []\nroute_stages: []\nbogus_top_key: 1\n",
        # unknown room key
        "rooms:\n  - module: top.a\n    not_a_field: 1\nroute_stages: []\n",
        # unknown stage key
        (
            "rooms: []\nroute_stages:\n  - name: s\n    mode: single\n"
            "    nets: []\n    config: {}\n    bonus: 1\n"
        ),
        # unknown override key (a router field the override does NOT expose, or typo)
        (
            "rooms: []\nroute_stages:\n  - name: s\n    mode: single\n"
            "    nets: []\n    config:\n      not_a_router_field: 1\n"
        ),
    ],
    ids=["top", "room", "stage", "override"],
)
def test_unknown_key_is_loud(yaml_text):
    with pytest.raises(_LOUD):
        _plan(yaml_text)


@needs_d2
def test_illegal_mode_is_loud():
    """mode must be in the closed set {diff, single}; a stray value cannot fall
    through to a default — the §C dual-entry dispatch depends on it."""
    yaml_text = (
        "rooms: []\nroute_stages:\n  - name: s\n    mode: serpentine\n"
        "    nets: []\n    config: {}\n"
    )
    with pytest.raises(_LOUD):
        _plan(yaml_text)


@needs_d2
@pytest.mark.parametrize(
    "geom",
    [
        "origin: [0, 0]\n    size: [0, 5]",  # non-positive width
        "origin: [0, 0]\n    size: [5, -1]",  # negative height
        "size: [5, 5]",  # size without origin (partial geometry, ambiguous)
        "origin: [1, 1]",  # origin without size (partial geometry, ambiguous)
    ],
    ids=["zero_w", "neg_h", "size_no_origin", "origin_no_size"],
)
def test_bad_room_geometry_is_loud(geom):
    """size≤0 and partial (xor) origin/size are hard errors. Both-present =
    explicit rectangle, both-absent = derived bbox (D3); exactly one is
    ambiguous and must be loud."""
    yaml_text = f"rooms:\n  - module: top.a\n    {geom}\nroute_stages: []\n"
    with pytest.raises(_LOUD):
        _plan(yaml_text)


# ===========================================================================
# D2.3 — consumer-oracle ②: resolve_nets is verbatim bridge② delegation.
# ===========================================================================
def _ir(signal_nets: dict[str, str]) -> dict:
    return {
        "layout_ir_version": 2,
        "components": {},
        "nets": {},
        "rooms": {},
        "signal_nets": signal_nets,
    }


@needs_d2
def test_resolve_nets_delegates_to_bridge2_verbatim():
    bridge = {"top.a.x": "VNET1", "top.b.y": "GND"}
    yaml_text = (
        "rooms: []\nroute_stages:\n  - name: s\n    mode: single\n"
        "    nets: [top.a.x, top.b.y]\n    config: {}\n"
    )
    plan = _plan(yaml_text)
    resolved = plan.resolve_nets(_ir(bridge))
    # exactly the bridge② values, in stage order — no transformation, no fallback
    assert resolved == {"s": ["VNET1", "GND"]}
    assert resolved["s"] == [bridge["top.a.x"], bridge["top.b.y"]]


@needs_d2
@pytest.mark.parametrize(
    "net_ref",
    [
        "GND",  # a bare kicad net NAME, not an ato address
        "top.does_not_exist",  # an address with no stable name in bridge② (I4)
    ],
    ids=["bare_net_name", "unstable_address"],
)
def test_unresolvable_net_is_loud(net_ref):
    bridge = {"top.a.x": "VNET1"}
    yaml_text = (
        f"rooms: []\nroute_stages:\n  - name: s\n    mode: single\n"
        f"    nets: [{net_ref}]\n    config: {{}}\n"
    )
    plan = _plan(yaml_text)
    with pytest.raises(_LOUD):
        plan.resolve_nets(_ir(bridge))


# ===========================================================================
# D2.4 — drift guard ③: every GridRouteOverride key is a real router-entry kwarg
# (oracle = union of the two entry signatures, NOT GridRouteConfig — see docstring).
# ===========================================================================
def _router_entry_kwargs_by_mode() -> dict[str, set[str]] | None:
    """{"single": batch_route params, "diff": batch_route_diff_pairs params} — the
    REAL kwargs E1 expands an override into, per dispatched entry. AST-parsed (not
    imported: the router pulls in scipy + the rust ext). None if the router repo is
    absent (tests then skip loudly). Deliberately NOT routing_config.GridRouteConfig:
    the entries take flat kwargs and translate some names internally (module
    docstring), so the dataclass is the wrong oracle."""
    root = Path(
        os.environ.get(
            "KICAD_ROUTING_TOOLS", str(_repo_root() / "vendor" / "KiCadRoutingTools")
        )
    )
    entries = {"single": ("route.py", "batch_route"),
               "diff": ("route_diff.py", "batch_route_diff_pairs")}
    out: dict[str, set[str]] = {}
    for mode, (fname, fn_name) in entries.items():
        path = root / fname
        if not path.exists():
            return None
        names: set[str] | None = None
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                a = node.args
                names = {p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)}
        if names is None:
            return None
        out[mode] = names
    return out


@needs_d2
def test_override_fields_are_real_router_kwargs():
    by_mode = _router_entry_kwargs_by_mode()
    if by_mode is None:
        pytest.skip(
            "KiCadRoutingTools (route.py / route_diff.py) not found; set "
            "KICAD_ROUTING_TOOLS to run the override drift guard"
        )
    accepted = by_mode["single"] | by_mode["diff"]
    override_fields = set(GridRouteOverride.model_fields)
    assert override_fields, "GridRouteOverride exposes no router knobs"
    extra = override_fields - accepted
    assert not extra, (
        f"GridRouteOverride has keys NO router entry accepts as a kwarg "
        f"(drift — E1 expands these into batch_route/batch_route_diff_pairs and "
        f"would TypeError): {sorted(extra)}"
    )


def _router_kwarg_annotation(fname: str, fn_name: str, arg: str) -> str | None:
    """The declared annotation SOURCE of one entry kwarg, AST-parsed (same
    non-importing method as _router_entry_kwargs_by_mode). None if absent."""
    root = Path(
        os.environ.get(
            "KICAD_ROUTING_TOOLS", str(_repo_root() / "vendor" / "KiCadRoutingTools")
        )
    )
    path = root / fname
    if not path.exists():
        return None
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            a = node.args
            for p in a.posonlyargs + a.args + a.kwonlyargs:
                if p.arg == arg and p.annotation is not None:
                    return ast.unparse(p.annotation)
    return None


@needs_d2
@pytest.mark.parametrize(
    "fname,fn_name",
    [("route.py", "batch_route"), ("route_diff.py", "batch_route_diff_pairs")],
    ids=["single", "diff"],
)
def test_length_match_groups_router_shape_is_nested(fname, fn_name):
    """Drift guard ③b — SHAPE, not just name: both entries declare
    `length_match_groups: Optional[List[List[str]]]`, and the override's
    canonical type is `list[list[str]]` to match. The historical bug this pins
    against: the override declared a FLAT `list[str]` and passed it verbatim —
    a shape mismatch the name-only guard above cannot see."""
    ann = _router_kwarg_annotation(fname, fn_name, "length_match_groups")
    if ann is None:
        pytest.skip(
            "KiCadRoutingTools not found; set KICAD_ROUTING_TOOLS to run the "
            "length-match shape guard"
        )
    assert "List[List[str]]" in ann, (
        f"{fn_name}.length_match_groups is annotated {ann!r} — no longer the "
        "nested List[List[str]] the override's canonical shape mirrors"
    )
    # and the override side keeps the matching nested canonical shape — probed
    # FUNCTIONALLY (the annotation now carries MinLen metadata inside Annotated,
    # so a string match on its repr would be brittle): a nested group passes
    # through verbatim, i.e. the canonical stored shape IS list[list[str]].
    ov = GridRouteOverride(length_match_groups=[["top.a", "top.b"]])
    assert ov.length_match_groups == [["top.a", "top.b"]], (
        f"GridRouteOverride.length_match_groups stored "
        f"{ov.length_match_groups!r} — must be canonically list[list[str]] "
        "(the flat authoring form is wrapped by a validator, never passed "
        "flat to the router)"
    )
    assert isinstance(ov.length_match_groups[0], list)


# ===========================================================================
# D2.5 — MODE/ENTRY CONFORMANCE (the in-place fix for the diff↔single API
# mismatch). The two entries accept DIFFERENT kwargs: guide_corridor_* only on
# the single entry, diff_pair_* only on the diff entry (measured 2026-06-16).
# So a stage's `config` key must be valid for the entry its `mode` dispatches to;
# D2 rejects a wrong-mode key AT PARSE TIME (mode + config are both known then),
# never silently passing it to a kwarg expansion that TypeErrors in E1.
#
# These bite hard: if the implementation expands `**override` without mode-aware
# filtering, the rejected-in-wrong-mode tests stop raising and go RED. The
# representative keys are re-validated against the live router (premise guard), so
# the test can't quietly test nothing if the router signatures move.
# ===========================================================================
# (key, value) pairs: a single-only knob and a diff-only knob
_SINGLE_ONLY = ("guide_corridor_enabled", True)  # batch_route only
_DIFF_ONLY = ("diff_pair_gap", 0.15)  # batch_route_diff_pairs only


def _stage_yaml(mode: str, key: str, value) -> str:
    lit = {True: "true", False: "false"}.get(value, value)
    return (
        f"rooms: []\nroute_stages:\n  - name: s\n    mode: {mode}\n"
        f"    nets: []\n    config:\n      {key}: {lit}\n"
    )


def _require_mode_premise() -> None:
    """Confirm against the live router that the representative keys are genuinely
    mode-exclusive; skip loudly if the router repo is absent (never test nothing)."""
    by_mode = _router_entry_kwargs_by_mode()
    if by_mode is None:
        pytest.skip(
            "KiCadRoutingTools not found; set KICAD_ROUTING_TOOLS to run the "
            "mode-conformance ratchet"
        )
    single, diff = by_mode["single"], by_mode["diff"]
    sk, dk = _SINGLE_ONLY[0], _DIFF_ONLY[0]
    assert sk in single and sk not in diff, f"{sk!r} no longer single-only in router"
    assert dk in diff and dk not in single, f"{dk!r} no longer diff-only in router"


@needs_d2
@pytest.mark.parametrize(
    "mode,key,value", [("single", *_SINGLE_ONLY), ("diff", *_DIFF_ONLY)],
    ids=["single_key_in_single", "diff_key_in_diff"],
)
def test_mode_exclusive_key_accepted_in_its_own_mode(mode, key, value):
    """A mode-exclusive knob IS accepted when the stage's mode matches its entry
    (proves the wrong-mode rejection below is mode-based, not unknown-key-based)."""
    _require_mode_premise()
    plan = _plan(_stage_yaml(mode, key, value))
    assert getattr(plan.route_stages[0].config, key) == value


@needs_d2
@pytest.mark.parametrize(
    "mode,key,value",
    [("single", *_DIFF_ONLY), ("diff", *_SINGLE_ONLY)],
    ids=["diff_key_in_single", "single_key_in_diff"],
)
def test_mode_exclusive_key_rejected_in_wrong_mode(mode, key, value):
    """A knob the stage's mode entry does NOT accept is loud at parse — the entry
    would TypeError on it. This is the test that goes RED if downstream code
    expands the override without filtering by mode."""
    _require_mode_premise()
    with pytest.raises(_LOUD):
        _plan(_stage_yaml(mode, key, value))


# ===========================================================================
# D2.6 — consumer-oracle on the REAL board (slow): resolved net names == live
# signal_nets on examples/layout_reuse. Pins resolution against the genuine
# bridge②, so net-naming drift is caught here rather than in production.
# ===========================================================================
from faebryk.libs.util import repo_root as _repo_root  # noqa: E402
from faebryk.libs.util import run_live  # noqa: E402

_EXAMPLE = _repo_root() / "examples" / "layout_reuse"


@pytest.fixture(scope="module")
def layout_reuse_signal_nets(tmp_path_factory) -> dict[str, str]:
    """Build examples/layout_reuse once and return the real bridge② map."""
    work = tmp_path_factory.mktemp("layout_plan_oracle")
    dst = work / "layout_reuse"
    shutil.copytree(_EXAMPLE, dst)
    bindir = os.path.dirname(sys.executable)
    env = {
        **os.environ,
        "NONINTERACTIVE": "1",
        "FBRK_PARTS_NO_REFRESH": "y",
        "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
    }
    stdout, stderr, _ = run_live(
        [sys.executable, "-m", "atopile", "build", "-v"],
        env=env, cwd=dst, stdout=print, stderr=print, timeout=300,
    )
    assert "Build successful! 🚀" in (stdout + stderr), (stdout + stderr)[-2000:]
    ir_path = dst / "build" / "builds" / "top" / "top.layout_ir.json"
    ir = json.loads(ir_path.read_text())
    return ir["signal_nets"]


@needs_d2
@pytest.mark.not_in_ci  # requires a full build (kicad-cli + parts)
@pytest.mark.slow
def test_resolve_nets_matches_real_signal_nets(layout_reuse_signal_nets):
    bridge = layout_reuse_signal_nets
    assert bridge, "no signal nets on a routed design"
    # take a couple of real signal addresses and resolve them through D2. Real
    # addresses carry instance indices (e.g. "sub_chains[0].r_chain[0]..."), and
    # the `[` makes a YAML FLOW sequence (`[a, b]`) unparseable — so nets must be
    # a BLOCK list (the representative authoring form for indexed addresses).
    addrs = sorted(bridge)[:2]
    nets_block = "".join(f"      - {a}\n" for a in addrs)
    yaml_text = (
        "rooms: []\nroute_stages:\n  - name: s\n    mode: single\n"
        f"    nets:\n{nets_block}    config: {{}}\n"
    )
    plan = _plan(yaml_text)
    resolved = plan.resolve_nets({"signal_nets": bridge})
    assert resolved["s"] == [bridge[a] for a in addrs]


# ===========================================================================
# D2.7 — stage names must be UNIQUE. They key resolve_nets (a dup would silently
# clobber the earlier stage's resolved nets) and the `--up-to <name>` breakpoint,
# so a duplicate is a hard error, not a last-one-wins coercion.
# ===========================================================================
@needs_d2
def test_duplicate_stage_name_is_loud():
    dup = (
        "rooms: []\nroute_stages:\n"
        "  - name: power\n    mode: single\n    nets: [top.a.x]\n    config: {}\n"
        "  - name: power\n    mode: diff\n    nets: [top.a.y]\n    config: {}\n"
    )
    with pytest.raises(_LOUD) as ei:
        _plan(dup)
    assert "power" in str(ei.value) and "unique" in str(ei.value)


@needs_d2
def test_distinct_stage_names_pass():
    """positive control: the same two stages with distinct names parse fine, so
    the dup check rejects duplication, not multi-stage plans."""
    ok = (
        "rooms: []\nroute_stages:\n"
        "  - name: power\n    mode: single\n    nets: [top.a.x]\n    config: {}\n"
        "  - name: usb\n    mode: diff\n    nets: [top.a.y]\n    config: {}\n"
    )
    assert [s.name for s in _plan(ok).route_stages] == ["power", "usb"]
