"""H2: SMDSize -> KiCad standard footprint mapping (pure, offline).

Bites two invariants: the computed name matches KiCad's chip naming exactly, and
a size KiCad does not ship resolves to None (never a silently-wrong attach).
"""

import pytest

from faebryk.libs.kicad.paths import GLOBAL_FP_DIR_PATH
from faebryk.libs.kicad.standard_footprints import (
    STANDARD_CHIP_LIBS,
    resolve_standard_footprint,
)
from faebryk.libs.smd import SMDSize

_HAS_STDLIB = (GLOBAL_FP_DIR_PATH / "Resistor_SMD.pretty").is_dir()
pytestmark = pytest.mark.skipif(
    not _HAS_STDLIB, reason="KiCad standard footprint library not installed"
)


@pytest.mark.parametrize(
    "size,prefix,identifier",
    [
        (SMDSize.I0402, "R", "Resistor_SMD:R_0402_1005Metric"),
        (SMDSize.I0603, "C", "Capacitor_SMD:C_0603_1608Metric"),
        (SMDSize.I0805, "L", "Inductor_SMD:L_0805_2012Metric"),
        (SMDSize.I1206, "R", "Resistor_SMD:R_1206_3216Metric"),
    ],
)
def test_resolve_known_sizes(size, prefix, identifier):
    result = resolve_standard_footprint(size, prefix)
    assert result is not None, f"{prefix}{size} should resolve"
    ident, path = result
    assert ident == identifier
    assert path.is_file(), f"resolved path must exist: {path}"


def test_metric_input_resolves_same_as_imperial():
    # M1005 == I0402; both must land on the same standard footprint.
    assert (
        resolve_standard_footprint(SMDSize.M1005, "R")
        == resolve_standard_footprint(SMDSize.I0402, "R")
    )


def test_unshipped_size_is_none_not_guess():
    # KiCad ships no R_2220_5750Metric -> must be a loud None, not a fabricated name.
    assert resolve_standard_footprint(SMDSize.I2220, "R") is None


def test_exotic_package_is_none():
    # non-chip SMD sizes cannot convert to imperial -> None (UnableToConvert).
    assert resolve_standard_footprint(SMDSize.SMD4_4x4_1mm, "R") is None


def test_unknown_prefix_is_none():
    assert resolve_standard_footprint(SMDSize.I0402, "Q") is None


def test_every_resolved_footprint_file_exists():
    # coverage guard: whatever we claim to resolve must be a real .kicad_mod.
    for prefix in STANDARD_CHIP_LIBS:
        for size in SMDSize:
            result = resolve_standard_footprint(size, prefix)
            if result is not None:
                _, path = result
                assert path.is_file()
