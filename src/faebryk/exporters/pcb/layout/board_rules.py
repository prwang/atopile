# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""board_rules — F-drc-rules: author board net classes into the .kicad_pro (§F/F5).

KiCad's DRC judges copper against NET CLASSES (clearance, track width, via
geometry, diff-pair geometry), which live in the PROJECT file
(`<board>.kicad_pro` → `net_settings`). atopile did not previously write a
project file, so a generated board's DRC silently used KiCad's defaults — a
designed 0.5 mm HV clearance was invisible to DRC.

This module textualizes those rules: `board.net_classes` (layout.yaml) →
`C_kicad_project_file.net_settings` (the classes + per-net assignment patterns),
each net resolved ato-address → kicad-net-name through bridge② (`ir["signal_nets"]`,
the same authority as `resolve_nets`). Written next to the board so
`kicad-cli pcb drc` (and `ato diagnose`, F4) judge DESIGN INTENT.

Empirically verified: kicad-cli honors the emitted classes (a 5 mm clearance
class turns 0 clearance violations into 36) and round-trips rc=0 (no SEGFAULT).

The `rules:` header (DesignRules) is ALSO authored here: it becomes the Default
net class (clearance/track_width/diff pair geometry), and `generate_dru_rules`
emits the `<project>.kicad_dru` custom rules a net class cannot express
(component courtyard spacing, diff-pair max uncoupled length, the board-wide
clearance floor, the F1 matched-length rules: board-wide intra-pair skew + the
per-class skew/length constraints) — so kicad-cli DRC judges the same numbers
the router started from.

Loud-or-nothing (S5a): a class net that does not resolve through bridge② is a
hard error (LayoutPlanError) — never a silently-dropped assignment. Duplicate
class names / a net in two classes are rejected earlier, at parse (Board model).
The honest non-error case is "no net_classes AND no rules authored" → returns
None (write nothing; KiCad defaults stand, visibly, not silently swallowed).

D5 component-class membership is authored on TWO channels with distinct
authority (empirically pinned 2026-07-03,
test_rule_area_contract.py::test_kicad_drc_enforces_component_class_headlessly):

* **Headless authority = the board file**: `generate_component_class_membership`
  stamps the static `(component_classes (class "<module>"))` token onto every
  footprint of a `source: component_class` room. kicad-cli 10.0.3 does NOT run
  `BOARD::SynchronizeComponentClasses` on headless board load (only the GUI's
  `PCB_EDIT_FRAME::OpenProjectFiles` does; the CLI-path sync exists only in
  KiCad master's board_loader.cpp) — so a DRC rule conditioned on
  `A.hasComponentClass(...)` resolves ONLY through this static token under
  `kicad-cli pcb drc` / `ato diagnose`.
* **GUI mirror = the project file**: `generate_component_classes` declares the
  same class as one `component_class_settings` assignment — class name =
  room.module, one SHEET_NAME condition whose primary is that same module (the
  C3-stamped sheetname). The GUI resolves it on project open and its Board
  Setup UI shows the class; headlessly it is inert. Merge-preserving at TWO
  levels: only the component_class_settings section is authored, and within it
  user-authored assignments survive; atopile's own output (recognized by the
  `_atopile_authored` structural fingerprint) is replaced on re-emit and
  garbage-collected when its room is renamed or reverted to
  `source: sheetname`.
"""

from typing import Any

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, LayoutPlanError
from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.kicad.other_fileformats import C_kicad_project_file

_NS = C_kicad_project_file.C_net_settings
_DEFAULT_CLASS_NAME = "Default"


def generate_project_rules(
    plan: LayoutPlan,
    ir: dict[str, Any],
    base_project: C_kicad_project_file | None = None,
) -> C_kicad_project_file | None:
    """Build the project file carrying `plan.board.net_classes`, or None when none
    are authored. `base_project` is merged (only `net_settings` is authored — every
    other project setting is preserved). Nets are resolved through bridge②; an
    unresolvable one is loud."""
    from faebryk.libs.kicad.length_report import extract_pair_suffix

    board = plan.board
    net_classes = board.net_classes if board is not None else []
    if not net_classes and plan.rules is None:
        return None

    signal_nets: dict[str, str] = ir.get("signal_nets", {})
    intra_budget = plan.rules.intra_pair_skew_max if plan.rules is not None else None
    project = base_project if base_project is not None else C_kicad_project_file()

    # KiCad requires a Default class; keep an existing one (or seed the model
    # default) so authored classes are ADDED, never replacing Default.
    existing = {c.name: c for c in project.net_settings.classes}
    default_class = existing.get(_DEFAULT_CLASS_NAME) or _NS.C_classes(
        name=_DEFAULT_CLASS_NAME
    )
    # the rules header IS the Default class: every net not assigned to an authored
    # class is judged against the board-wide rules, not KiCad's silent defaults.
    if plan.rules is not None:
        r = plan.rules
        default_class.clearance = r.clearance
        default_class.track_width = r.track_width
        if r.diff_pair_width is not None:
            default_class.diff_pair_width = r.diff_pair_width
        if r.diff_pair_gap is not None:
            default_class.diff_pair_gap = r.diff_pair_gap
    classes: list = [default_class]
    patterns: list = []

    for nc in net_classes:
        # only set the fields the author specified — KiCad's class defaults stand
        # for the rest (the C_classes model carries them).
        cls = _NS.C_classes(name=nc.name)
        if nc.clearance is not None:
            cls.clearance = nc.clearance
        if nc.track_width is not None:
            cls.track_width = nc.track_width
        if nc.via_diameter is not None:
            cls.via_diameter = nc.via_diameter
        if nc.via_drill is not None:
            cls.via_drill = nc.via_drill
        if nc.diff_pair_gap is not None:
            cls.diff_pair_gap = nc.diff_pair_gap
        if nc.diff_pair_width is not None:
            cls.diff_pair_width = nc.diff_pair_width
        classes.append(cls)

        for addr in nc.nets:
            if addr not in signal_nets:
                raise LayoutPlanError(
                    f"net class {nc.name!r}: net address {addr!r} is not a "
                    "resolvable ato signal address (not in bridge② signal_nets) — "
                    "give an ato signal address, not a bare net name"
                )
            net_name = signal_nets[addr]
            # skew-rule exclusivity (NetClass docstring / generate_dru_rules):
            # the class `skew_max` rule is emitted AFTER the board-wide
            # `rules.intra_pair_skew_max` rule and KiCad keeps only the LAST
            # matching skew rule per item — for a diff-pair member the class
            # rule would silently disable the intra-pair budget. Loud (S5a).
            if (
                intra_budget is not None
                and nc.skew_max is not None
                and extract_pair_suffix(net_name) is not None
            ):
                raise LayoutPlanError(
                    f"rules.intra_pair_skew_max and net class {nc.name!r} "
                    f"skew_max both target diff-pair net {net_name!r} "
                    f"({addr!r}): KiCad DRC keeps only the LAST matching skew "
                    "rule per item, so the class rule would silently disable "
                    "the board-wide intra-pair budget for this net — drop one "
                    "of the two (the lengths report still measures intra-pair "
                    "skew)"
                )
            # assign by EXACT resolved net name (a precise, non-wildcard pattern).
            patterns.append(
                _NS.C_netclass_pattern(netclass=nc.name, pattern=net_name)
            )

    project.net_settings.classes = classes
    project.net_settings.netclass_patterns = patterns
    return project


def _atopile_authored(
    a: "C_kicad_project_file.C_component_class_settings.C_assignment",
) -> bool:
    """True iff `a` carries the rigid structural fingerprint of an assignment
    THIS module emits: operator ALL + exactly one SHEET_NAME condition whose
    primary equals the class name, no secondary. Emitted assignments carry no
    other ownership marker, so this fingerprint IS the ownership boundary:
    stale atopile output (a renamed room, a room reverted to `source:
    sheetname`) is garbage-collected by it, and a USER assignment written in
    this exact shape is treated as atopile-owned (documented contract — the
    shape is atopile's namespace)."""
    cond = a.conditions.get("SHEET_NAME")
    return (
        a.conditions_operator == "ALL"
        and set(a.conditions) == {"SHEET_NAME"}
        and cond is not None
        and cond.primary == a.component_class
        and not cond.secondary
    )


def generate_component_classes(
    plan: LayoutPlan,
    base_project: C_kicad_project_file | None = None,
) -> C_kicad_project_file | None:
    """Author one component-class assignment per `source: component_class` room
    into the project file's `component_class_settings` (the GUI mirror — see the
    module docstring; the headless authority is
    `generate_component_class_membership`), or None when there is nothing to
    write.

    Class name == room.module; membership = a single SHEET_NAME condition on
    that module (conditions_operator ALL), i.e. the same C3 sheetname stamped on
    the room's footprints — the rule area's `(component_class <module>)` source
    and this assignment agree by construction. `base_project` is merged:
    assignments atopile does not own (per `_atopile_authored`) are preserved in
    their original order; atopile-owned ones are (re)emitted after them in plan
    order (deterministic, idempotent re-emit) and STALE ones — a fingerprint
    match whose class is no longer a plan room — are dropped. A plan with zero
    component_class rooms still PRUNES stale atopile output from
    `base_project` (returning the cleaned project); only when there is nothing
    to author AND nothing stale to remove does it return None."""
    cc_rooms = [r for r in plan.rooms if r.source == "component_class"]
    if not cc_rooms:
        # nothing to author — but a previous build's assignments (room since
        # reverted to `source: sheetname`) must not leak into .kicad_pro
        # forever: prune our own stale output, keep user assignments.
        if base_project is None:
            return None
        settings = base_project.component_class_settings
        kept = [a for a in settings.assignments if not _atopile_authored(a)]
        if len(kept) == len(settings.assignments):
            return None
        settings.assignments = kept
        return base_project

    project = base_project if base_project is not None else C_kicad_project_file()
    _CC = C_kicad_project_file.C_component_class_settings

    owned = {room.module for room in cc_rooms}
    settings = project.component_class_settings
    # drop what the plan owns NOW (idempotent re-emit) AND any stale
    # atopile-authored assignment left by an earlier build (renamed room GC).
    kept = [
        a
        for a in settings.assignments
        if a.component_class not in owned and not _atopile_authored(a)
    ]
    settings.assignments = kept + [
        _CC.C_assignment(
            component_class=room.module,
            conditions_operator="ALL",
            conditions={"SHEET_NAME": _CC.C_condition(primary=room.module)},
        )
        for room in cc_rooms
    ]
    return project


def generate_component_class_membership(
    pcb: "kicad.pcb.KicadPcb", plan: LayoutPlan
) -> int:
    """Stamp static per-footprint component-class membership onto the board —
    the ONLY channel kicad-cli 10.0.3 honors headlessly (module docstring).

    Every footprint whose C3-stamped sheetname equals a `source:
    component_class` room's module gets `(component_classes (class
    "<module>"))`; footprints of other rooms get their atopile-owned class (if
    any) removed. Returns the number of footprints modified (0 on an
    already-converged board — idempotent re-stamp).

    Ownership boundary (union-merge, ours added, user's kept): atopile only
    ever stamps a class named after a room the footprint belongs to, i.e. the
    footprint's own sheetname == a dotted ancestor of its `atopile_address`.
    So a class equal to the current sheetname OR a dotted ancestor of the
    address is atopile-owned and re-derived every build (this also GCs the
    ghost left when sync_rooms re-stamps the same footprint under a different
    room level); every other static class is user-authored and preserved in
    its original position. Footprints without a sheetname are untouched.

    S5a: a component_class room matching no footprint sheetname is loud (the
    same real-room condition rule_area enforces — checked here too so this
    function is safe standalone), raised BEFORE any mutation."""
    cc_modules = {r.module for r in plan.rooms if r.source == "component_class"}

    sheetnames = {fp.sheetname for fp in pcb.footprints if fp.sheetname}
    missing = sorted(cc_modules - sheetnames)
    if missing:
        raise LayoutPlanError(
            f"component_class room(s) {missing} match no footprint sheetname on "
            "the board — a typo or stale reference; stamping would classify "
            "nothing"
        )

    def _owned(class_name: str, sheet: str, addr: str | None) -> bool:
        if class_name == sheet:
            return True
        return addr is not None and (
            addr == class_name or addr.startswith(class_name + ".")
        )

    changed = 0
    for fp in pcb.footprints:
        sheet = fp.sheetname
        if not sheet:
            continue
        addr = Property.try_get_property(fp.propertys, "atopile_address")
        old_names = (
            [c.name for c in fp.component_classes.classes]
            if fp.component_classes is not None
            else []
        )
        new_names = [n for n in old_names if not _owned(n, sheet, addr)]
        if sheet in cc_modules:
            new_names.append(sheet)
        if new_names == old_names:
            continue
        changed += 1
        if new_names:
            fp.component_classes = kicad.pcb.FootprintComponentClasses(
                classes=[
                    kicad.pcb.FootprintComponentClass(name=n) for n in new_names
                ]
            )
        else:
            # no classes left: drop the whole block, never an empty
            # (component_classes) token.
            fp.component_classes = None
    return changed


def generate_dru_rules(plan: LayoutPlan) -> str | None:
    """The `.kicad_dru` custom-rules text from the plan's `rules:` header + the
    net classes' matched-length fields (F1), or None when neither authors any.

    Net-class settings (the function above) cover clearance/width per class; the
    dru carries the rules a net class CANNOT express: the courtyard-to-courtyard
    component spacing, the diff-pair max uncoupled length, and the
    matched-length constraints. The board-wide copper clearance is ALSO emitted
    here as a hard floor — a dru min beats any accidentally-looser class.
    kicad-cli pcb drc loads `<project>.kicad_dru` automatically when it sits
    next to the project file.

    Matched-length emission (violation types empirically pinned on kicad-cli
    10.0.3, test_matched_length_rules_contract.py):

      * `rules.intra_pair_skew_max` → ONE board-wide rule over every
        suffix-convention pair: `(condition "A.inDiffPair('*')")` +
        `(constraint skew (max ..) (within_diff_pairs))`
        → "skew_out_of_range" within each _P/_N pair;
      * per class (scoped `(condition "A.hasNetclass('<name>')")` — the class
        itself reaches KiCad via the .kicad_pro written by
        `generate_project_rules`, always emitted alongside):
        `skew_max` → plain skew (group match: every class net judged against
        the group's LONGEST net), `intra_pair_skew_max` → the
        `(within_diff_pairs)` variant, `length_min`/`length_max` → one
        `(constraint length ...)` carrying only the authored bounds
        → "length_out_of_range".

    SKEW-RULE EXCLUSIVITY: KiCad has ONE skew constraint type (the
    `(within_diff_pairs)` token is an option on it) and applies only the LAST
    matching skew rule per item, so two skew rules over the same nets shadow
    each other. The representable combinations are enforced upstream: one class
    never sets both `skew_max` and `intra_pair_skew_max` (NetClass parse), and
    `rules.intra_pair_skew_max` never coexists with a class `skew_max` over
    diff-pair nets (generate_project_rules, always emitted alongside this dru —
    the seam where class nets resolve to board net names). A class
    `intra_pair_skew_max` after the board-wide intra rule is the one benign
    overlap: same semantics, class value for class nets (a scoped override).

    Class-name representability (no quotes), the never-bites empty-nets case
    and the inverted length window are rejected at parse (NetClass validator)
    — this emitter never sees them."""
    lines: list[str] = []
    r = plan.rules
    if r is not None:
        lines.append(
            f'(rule "board-min-clearance"\n'
            f"  (constraint clearance (min {r.clearance}mm)))"
        )
        if r.component_spacing is not None:
            lines.append(
                f'(rule "board-component-spacing"\n'
                f"  (constraint courtyard_clearance (min {r.component_spacing}mm)))"
            )
        if r.uncoupled_max_length is not None:
            lines.append(
                f'(rule "board-diffpair-uncoupled-max"\n'
                f"  (constraint diff_pair_uncoupled (max {r.uncoupled_max_length}mm)))"
            )
        if r.intra_pair_skew_max is not None:
            lines.append(
                f'(rule "board-diffpair-intra-skew"\n'
                f'  (condition "A.inDiffPair(\'*\')")\n'
                f"  (constraint skew (max {r.intra_pair_skew_max}mm)"
                f" (within_diff_pairs)))"
            )

    net_classes = plan.board.net_classes if plan.board is not None else []
    for nc in net_classes:
        condition = f'  (condition "A.hasNetclass(\'{nc.name}\')")\n'
        if nc.skew_max is not None:
            lines.append(
                f'(rule "class-{nc.name}-skew"\n'
                f"{condition}"
                f"  (constraint skew (max {nc.skew_max}mm)))"
            )
        if nc.intra_pair_skew_max is not None:
            lines.append(
                f'(rule "class-{nc.name}-intra-skew"\n'
                f"{condition}"
                f"  (constraint skew (max {nc.intra_pair_skew_max}mm)"
                f" (within_diff_pairs)))"
            )
        if nc.length_min is not None or nc.length_max is not None:
            bounds = "".join(
                f" ({kind} {value}mm)"
                for kind, value in (("min", nc.length_min), ("max", nc.length_max))
                if value is not None
            )
            lines.append(
                f'(rule "class-{nc.name}-length"\n'
                f"{condition}"
                f"  (constraint length{bounds}))"
            )

    if not lines:
        return None
    return "\n".join(["(version 1)"] + lines) + "\n"
