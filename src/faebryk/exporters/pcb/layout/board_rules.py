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
clearance floor) — so kicad-cli DRC judges the same numbers the router started
from.

Loud-or-nothing (S5a): a class net that does not resolve through bridge② is a
hard error (LayoutPlanError) — never a silently-dropped assignment. Duplicate
class names / a net in two classes are rejected earlier, at parse (Board model).
The honest non-error case is "no net_classes AND no rules authored" → returns
None (write nothing; KiCad defaults stand, visibly, not silently swallowed).

`generate_component_classes` (D5) is the sibling for rooms with
`source: component_class`: each such room becomes one `component_class_settings`
assignment — class name = room.module, one SHEET_NAME condition whose primary is
that same module (the C3-stamped sheetname is the single membership authority).
Merge-preserving at TWO levels: only the component_class_settings section is
authored, and within it user-authored assignments for OTHER classes survive;
only the atopile-owned class names (= the plan's component_class rooms) are
replaced. kicad-cli resolves the assignments headlessly on board load.
"""

from typing import Any

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, LayoutPlanError
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
    board = plan.board
    net_classes = board.net_classes if board is not None else []
    if not net_classes and plan.rules is None:
        return None

    signal_nets: dict[str, str] = ir.get("signal_nets", {})
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
            # assign by EXACT resolved net name (a precise, non-wildcard pattern).
            patterns.append(
                _NS.C_netclass_pattern(netclass=nc.name, pattern=signal_nets[addr])
            )

    project.net_settings.classes = classes
    project.net_settings.netclass_patterns = patterns
    return project


def generate_component_classes(
    plan: LayoutPlan,
    base_project: C_kicad_project_file | None = None,
) -> C_kicad_project_file | None:
    """Author one component-class assignment per `source: component_class` room
    into the project file's `component_class_settings`, or None when the plan has
    no such room (write nothing — the honest non-error case).

    Class name == room.module; membership = a single SHEET_NAME condition on
    that module (conditions_operator ALL), i.e. the same C3 sheetname stamped on
    the room's footprints — the rule area's `(component_class <module>)` source
    and this assignment agree by construction. `base_project` is merged:
    assignments for class names atopile does NOT own are preserved in their
    original order; atopile-owned ones are (re)emitted after them in plan
    order (deterministic, idempotent re-emit)."""
    cc_rooms = [r for r in plan.rooms if r.source == "component_class"]
    if not cc_rooms:
        return None

    project = base_project if base_project is not None else C_kicad_project_file()
    _CC = C_kicad_project_file.C_component_class_settings

    owned = {room.module for room in cc_rooms}
    settings = project.component_class_settings
    kept = [a for a in settings.assignments if a.component_class not in owned]
    settings.assignments = kept + [
        _CC.C_assignment(
            component_class=room.module,
            conditions_operator="ALL",
            conditions={"SHEET_NAME": _CC.C_condition(primary=room.module)},
        )
        for room in cc_rooms
    ]
    return project


def generate_dru_rules(plan: LayoutPlan) -> str | None:
    """The `.kicad_dru` custom-rules text from the plan's `rules:` header, or None
    when the plan has none.

    Net-class settings (the function above) cover clearance/width per class; the
    dru carries the rules a net class CANNOT express: the courtyard-to-courtyard
    component spacing and the diff-pair max uncoupled length. The board-wide
    copper clearance is ALSO emitted here as a hard floor — a dru min beats any
    accidentally-looser class. kicad-cli pcb drc loads `<project>.kicad_dru`
    automatically when it sits next to the project file."""
    r = plan.rules
    if r is None:
        return None
    lines = ["(version 1)"]
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
    return "\n".join(lines) + "\n"
