"""Partial place & route support (H-partial): a global solve/pick is not always
feasible, so the pipeline places/routes the subset it CAN and *reports* the rest
-- never silently dropping a component.

`collect_unresolved_modules` finds every module that is a designated part
(`has_designator_prefix` -> it is meant to land on the board) but has no
footprint after picking, so it will be absent from the board. That set is the
"the rest" of "place what you can, report the rest": it is written to a
deterministic `<output_base>.unresolved.json` artifact and logged loudly, closing
the loud-or-nothing gap (CLAUDE.md S5a) where a footprint-less deferred part was
silently missing from the board with only a soft warning.
"""

import logging
from dataclasses import dataclass

import faebryk.core.node as fabll
import faebryk.library._F as F

logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class UnresolvedModule:
    address: str
    reason: str  # "deferred-pick" | "no-standard-footprint"


def collect_unresolved_modules(app: fabll.Node) -> list[UnresolvedModule]:
    """Designated parts (has_designator_prefix) that have no footprint, sorted by
    address (deterministic).

    reason:
    * ``no-standard-footprint`` -- the part declares a package but no KiCad
      standard footprint applies (exotic/unshipped size); a defect to fix.
    * ``deferred-pick`` -- no footprint and no package to derive one (e.g. a
      value-only part deferred under ``--no-pick``); resolve with ``ato bom``.
    """
    out: list[UnresolvedModule] = []
    for module in app.get_children(
        direct_only=False, types=fabll.Node, required_trait=F.has_designator_prefix
    ):
        if module.has_trait(F.Footprints.has_associated_footprint):
            continue
        if module.has_trait(F.has_part_removed):
            continue
        reason = (
            "no-standard-footprint"
            if module.has_trait(F.has_package_requirements)
            else "deferred-pick"
        )
        out.append(
            UnresolvedModule(module.get_full_name(include_uuid=False), reason)
        )
    return sorted(out)
