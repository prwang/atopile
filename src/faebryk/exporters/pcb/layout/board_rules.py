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

Loud-or-nothing (S5a): a class net that does not resolve through bridge② is a
hard error (LayoutPlanError) — never a silently-dropped assignment. Duplicate
class names / a net in two classes are rejected earlier, at parse (Board model).
The honest non-error case is "no net_classes authored" → returns None (write
nothing; KiCad defaults stand, visibly, not silently swallowed).
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
    if board is None or not board.net_classes:
        return None

    signal_nets: dict[str, str] = ir.get("signal_nets", {})
    project = base_project if base_project is not None else C_kicad_project_file()

    # KiCad requires a Default class; keep an existing one (or seed the model
    # default) so authored classes are ADDED, never replacing Default.
    existing = {c.name: c for c in project.net_settings.classes}
    default_class = existing.get(_DEFAULT_CLASS_NAME) or _NS.C_classes(
        name=_DEFAULT_CLASS_NAME
    )
    classes: list = [default_class]
    patterns: list = []

    for nc in board.net_classes:
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
