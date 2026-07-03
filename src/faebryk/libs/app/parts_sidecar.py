"""Out-of-source part-picker sidecar (H3).

A `parts.yaml` overlay that pins/picks a *subset* of components and adds picking
constraints, WITHOUT editing the `.ato` source. Keyed by ato address (the same
address `layout.yaml` and the board's `atopile_address` use, i.e.
`module.get_full_name(include_uuid=False)` -- e.g. `r1`, `sub.r_chain[0]`).

    # parts.yaml
    r1:
      lcsc: C25819            # pin a concrete LCSC part
    r2:
      mpn: RC0402FR-0710KL    # pin a manufacturer part number
      manufacturer: Yageo
    r3:
      package: R0402          # add a picker constraint (narrow the search)

This lets an agent finalize the BOM incrementally (pick some now, constrain more,
resolve the rest later with `ato bom`) while the `.ato` stays fixed. The applied
traits are the SAME ones the compiler attaches for an in-source `r1.lcsc_id=...`,
so a conflict with a design constraint surfaces loudly via the solver, and an
`.ato` pin (`has_part_picked`) always wins (the sidecar skips it).

Loud-or-nothing (CLAUDE.md S5a): an address not present in the design is a hard
error, never a silent no-op.
"""

import logging
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

import faebryk.core.node as fabll
import faebryk.library._F as F
from atopile.errors import UserException

logger = logging.getLogger(__name__)


class PartEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown keys are loud

    lcsc: str | None = None
    mpn: str | None = None
    manufacturer: str | None = None
    package: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.lcsc and self.mpn:
            raise ValueError("give at most one of `lcsc` / `mpn`")
        if self.mpn and not self.manufacturer:
            raise ValueError("`mpn` requires `manufacturer`")
        if not (self.lcsc or self.mpn or self.package):
            raise ValueError("entry must set at least one of lcsc / mpn / package")
        return self


class PartsSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: dict[str, PartEntry]

    @classmethod
    def from_file(cls, path: Path) -> "PartsSidecar":
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise UserException(f"parts.yaml must be a mapping of address -> entry: {path}")
        return cls(entries={addr: PartEntry(**(v or {})) for addr, v in raw.items()})

    def to_yaml(self) -> str:
        # deterministic: sorted keys, exclude unset fields
        data = {
            addr: self.entries[addr].model_dump(exclude_none=True)
            for addr in sorted(self.entries)
        }
        return yaml.safe_dump(data, sort_keys=True, default_flow_style=False)


def _module_address_map(app: fabll.Node) -> dict[str, fabll.Node]:
    """address (as on the board's `atopile_address`) -> module node."""
    out: dict[str, fabll.Node] = {}
    for m in app.get_children(
        direct_only=False, types=fabll.Node, required_trait=fabll.is_module
    ):
        out.setdefault(m.get_full_name(include_uuid=False), m)
    return out


def apply_parts_sidecar(app: fabll.Node, sidecar: PartsSidecar) -> int:
    """Inject the sidecar's picks/constraints onto the instance graph.

    Returns the number of entries applied. Skips a module already resolved by an
    `.ato` pin (`has_part_picked`) -- the source pin wins. An address not in the
    design raises (loud).
    """
    addr_map = _module_address_map(app)
    applied = 0

    for address, entry in sidecar.entries.items():
        node = addr_map.get(address)
        if node is None:
            raise UserException(
                f"parts.yaml references `{address}`, which is not a module in this "
                f"design. Known addresses include: "
                f"{', '.join(sorted(addr_map)[:10])}..."
            )

        if node.has_trait(F.Pickable.has_part_picked):
            logger.info(f"parts.yaml: `{address}` already picked in source; skipping")
            continue

        if entry.lcsc:
            fabll.Traits.create_and_add_instance_to(
                node=node, trait=F.Pickable.is_pickable_by_supplier_id
            ).setup(
                supplier_part_id=entry.lcsc,
                supplier=F.Pickable.is_pickable_by_supplier_id.Supplier.LCSC,
            )
            logger.info(f"parts.yaml: pinned `{address}` -> LCSC {entry.lcsc}")
        elif entry.mpn:
            fabll.Traits.create_and_add_instance_to(
                node=node, trait=F.Pickable.is_pickable_by_part_number
            ).setup(manufacturer=entry.manufacturer, partno=entry.mpn)
            logger.info(
                f"parts.yaml: pinned `{address}` -> {entry.manufacturer} {entry.mpn}"
            )

        if entry.package:
            _apply_package(node, address, entry.package)

        applied += 1

    return applied


def _apply_package(node: fabll.Node, address: str, package: str) -> None:
    from atopile.compiler.overrides import _parse_smd_size

    size = _parse_smd_size(package)
    if node.has_trait(F.has_package_requirements):
        # narrow the existing constraint instead of attaching a conflicting one
        node.get_trait(F.has_package_requirements).size.get().set_superset(size)
    else:
        trait = fabll.Traits.create_and_add_instance_to(
            node=node, trait=F.has_package_requirements
        )
        trait.size.get().set_superset(size)
    logger.info(f"parts.yaml: constrained `{address}` package -> {package}")


def load_and_apply(app: fabll.Node, path: Path) -> int:
    """Convenience: load a parts.yaml and apply it, loudly."""
    logger.info(f"Applying parts sidecar {path}")
    return apply_parts_sidecar(app, PartsSidecar.from_file(path))


def default_parts_path() -> Path:
    """The default parts.yaml for the selected build (`<output_base>.parts.yaml`),
    where `ato bom --pick` writes and the build reads when no `parts_config` is
    declared in ato.yaml."""
    from atopile.config import config

    return config.build.paths.output_base.with_suffix(".parts.yaml")


def merge_pick(path: Path, address: str, entry: PartEntry) -> None:
    """Incrementally add/replace one entry in a parts.yaml, atomically.

    Reads the existing sidecar (or starts empty), sets ``address`` to ``entry``,
    and writes it back deterministically (sorted keys) via an atomic write so a
    concurrent build reading the file never sees a torn write (H4).
    """
    from faebryk.libs.util import atomic_write_text

    sidecar = PartsSidecar.from_file(path) if path.exists() else PartsSidecar(entries={})
    sidecar.entries[address] = entry
    atomic_write_text(path, sidecar.to_yaml())
    logger.info(f"parts.yaml: recorded `{address}` in {path}")
