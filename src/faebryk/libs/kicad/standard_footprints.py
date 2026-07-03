"""Map an SMD chip size (`r.package="R0402"`) to a generic KiCad standard
footprint, WITHOUT the part picker/solver (H2).

This is the footprint-source that makes `ato build --no-pick` useful for generic
passives: a resistor/capacitor/inductor whose only constraint is its package can
get a real footprint (`Resistor_SMD:R_0402_1005Metric`) and thus a routable
board, deferring the concrete-part (MPN) resolution to `ato bom`.

Loud-or-nothing (CLAUDE.md S5a): the mapping computes the canonical KiCad chip
name but only returns it if the `.kicad_mod` actually exists on disk. KiCad does
not ship every chip size (e.g. `R_2220_5750Metric` is absent), so an unmapped
size returns ``None`` (the caller warns), never a silently-wrong attach.
"""

from pathlib import Path

from faebryk.libs.kicad.paths import GLOBAL_FP_DIR_PATH
from faebryk.libs.smd import SMDSize

# The load-bearing data: chip prefix -> KiCad standard footprint library.
# (prefix selection by module type mirrors picker_lib._from_smd_size.)
STANDARD_CHIP_LIBS: dict[str, str] = {
    "R": "Resistor_SMD",
    "C": "Capacitor_SMD",
    "L": "Inductor_SMD",
}


def resolve_standard_footprint(
    size: SMDSize, prefix: str, fp_dir: Path = GLOBAL_FP_DIR_PATH
) -> tuple[str, Path] | None:
    """Resolve a chip ``size`` + ``prefix`` ("R"/"C"/"L") to a KiCad standard
    footprint, or ``None`` if there is no such standard footprint.

    Returns ``(identifier, path)`` where identifier is ``"Lib:Name"`` (e.g.
    ``"Resistor_SMD:R_0402_1005Metric"``) and path is the on-disk ``.kicad_mod``.
    Returns ``None`` (never guesses) when:

    * ``prefix`` is not a known chip prefix, or
    * the size cannot be expressed in both imperial and metric (exotic packages
      raise :class:`SMDSize.UnableToConvert`), or
    * KiCad does not ship that chip size (the computed file does not exist).
    """
    lib = STANDARD_CHIP_LIBS.get(prefix)
    if lib is None:
        return None

    try:
        # KiCad chip naming: e.g. I0402 -> "R_0402_1005Metric"
        stem = f"{prefix}_{size.imperial.without_prefix}_{size.metric.without_prefix}Metric"
    except SMDSize.UnableToConvert:
        return None

    path = fp_dir / f"{lib}.pretty" / f"{stem}.kicad_mod"
    if not path.is_file():
        return None

    return f"{lib}:{stem}", path
