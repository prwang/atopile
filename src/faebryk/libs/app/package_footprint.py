"""Attach a generic KiCad standard footprint to a package-only R/C/L module,
without the part picker/solver (H2).

Gap-filler: for a Resistor/Capacitor/Inductor whose only constraint is its
package (`r.package="R0402"`), give it a real footprint
(`Resistor_SMD:R_0402_1005Metric`) so `update_pcb` can place it and `ato route`
can route it -- deferring the concrete-part (MPN) resolution to `ato bom`. It is
a strict gap-filler: modules that already have a footprint (a real pick, an
atomic part) are never touched, so solver-mode builds are unchanged.

Loud-or-nothing (CLAUDE.md S5a): every reason a module cannot get a package
footprint (ambiguous size, non-chip type, KiCad ships no such size) is logged
and the module is left footprint-less (surfaced downstream by
``check_unattached_fps``), never silently half-attached.
"""

import logging

import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.standard_footprints import (
    STANDARD_CHIP_LIBS,
    resolve_standard_footprint,
)

logger = logging.getLogger(__name__)


def smd_prefix_for_module(module: fabll.Node) -> str | None:
    """Chip prefix ("R"/"C"/"L") for a module by its type, else None.

    Mirrors picker_lib._from_smd_size so the package-footprint path and the
    backend query agree on the prefix.
    """
    type_name = module.get_type_name()
    for prefix, cls in (
        ("R", F.Resistor),
        ("C", F.Capacitor),
        ("L", F.Inductor),
    ):
        if type_name == cls._type_identifier():
            return prefix
    return None


def attach_package_footprint(module: fabll.Node) -> bool:
    """Attach a KiCad standard footprint derived from the module's package.

    Returns True if a footprint was attached, False otherwise (already had one,
    or no standard footprint applies -- each False path logs a reason).
    """
    # strict gap-filler: never clobber a real pick / atomic part
    if module.has_trait(F.Footprints.has_associated_footprint):
        return False

    pkg = module.try_get_trait(F.has_package_requirements)
    if pkg is None:
        return False

    sizes = pkg.get_sizes()
    if len(sizes) != 1:
        logger.warning(
            f"Cannot derive a footprint for {module.get_full_name()}: package is "
            f"ambiguous ({[s.name for s in sizes]}); pin a package or run `ato bom`."
        )
        return False
    size = sizes[0]

    prefix = smd_prefix_for_module(module)
    if prefix is None:
        logger.warning(
            f"Cannot derive a package footprint for {module.get_full_name()}: "
            f"only chip R/C/L ({'/'.join(STANDARD_CHIP_LIBS)}) are supported."
        )
        return False

    resolved = resolve_standard_footprint(size, prefix)
    if resolved is None:
        logger.warning(
            f"No KiCad standard footprint for {module.get_full_name()} "
            f"(package {size.name}); pin an explicit footprint or `ato bom`."
        )
        return False
    identifier, fp_path = resolved

    if not module.has_trait(F.Footprints.can_attach_to_footprint):
        logger.warning(
            f"{module.get_full_name()} cannot attach to a footprint; skipping "
            f"package footprint {identifier}."
        )
        return False

    # extract the copper pad names from the standard .kicad_mod (e.g. "1","2")
    fp_file = kicad.loads(kicad.footprint.FootprintFile, fp_path)
    pad_names = (
        F.KiCadFootprints.has_associated_kicad_library_footprint
        ._extract_pad_names_from_kicad_footprint_file(fp_file)
    )
    if not pad_names:
        logger.warning(f"Standard footprint {identifier} has no copper pads; skipping.")
        return False

    # create pad nodes carrying is_pad(name==number) so the module's leads can be
    # matched to the real footprint pads (2-terminal passives use
    # can_attach_to_any_pad, so binding is by availability, not name).
    is_pads: list[F.Footprints.is_pad] = []
    for name in pad_names:
        node = fabll.Node.bind_typegraph_from_instance(
            module.instance
        ).create_instance(g=module.instance.g())
        is_pads.append(
            fabll.Traits.create_and_add_instance_to(
                node=node, trait=F.Footprints.is_pad
            ).setup(pad_number=name, pad_name=name)
        )

    leads = [
        n.get_trait(F.Lead.is_lead)
        for n in module.get_children(
            direct_only=False, types=fabll.Node, required_trait=F.Lead.is_lead
        )
    ]

    associated = fabll.Traits.create_and_add_instance_to(
        node=module, trait=F.Footprints.has_associated_footprint
    )
    footprint = associated.setup_from_pads_and_leads(
        component_node=module, pads=is_pads, leads=leads
    ).get_footprint()

    # register the standard library so downstream ingest can resolve "lib:name"
    lib_name = identifier.split(":", 1)[0]
    from faebryk.libs.part_lifecycle import PartLifecycle

    PartLifecycle.singleton().library._insert_fp_lib(lib_name, fp_path.parent)

    fabll.Traits.create_and_add_instance_to(
        node=footprint,
        trait=F.KiCadFootprints.has_associated_kicad_library_footprint,
    ).setup(library_name=lib_name, kicad_footprint_file_path=str(fp_path))

    logger.info(f"Attached package footprint {identifier} to {module.get_full_name()}")
    return True


def attach_package_footprints(app: fabll.Node) -> int:
    """Attach package footprints to every gap-filler-eligible module under ``app``.

    Returns the number of footprints attached. A no-op for modules that already
    have a footprint, so it is safe to run in every build.
    """
    count = 0
    for module in app.get_children(
        direct_only=False, types=fabll.Node, required_trait=F.has_package_requirements
    ):
        if attach_package_footprint(module):
            count += 1
    return count
