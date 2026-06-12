# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import logging
from pathlib import Path

import faebryk

logger = logging.getLogger(__name__)

_RESOURCES_SUFFIX = "test/common/resources"


def ROOT_PATH() -> Path:
    editable_root = Path(faebryk.__file__).parent.parent.parent
    if (editable_root / _RESOURCES_SUFFIX).exists():
        return editable_root
    cwd_root = Path.cwd()
    if (cwd_root / _RESOURCES_SUFFIX).exists():
        return cwd_root
    raise FileNotFoundError("Could not find root directory")


RESOURCES_PATH = ROOT_PATH() / _RESOURCES_SUFFIX
FILEFORMATS_PATH = RESOURCES_PATH / "fileformats/kicad"


def _VERSION_DIR(version: int) -> Path:
    return FILEFORMATS_PATH / f"v{version}"


DEFAULT_VERSION = 9


def _FP_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "fp"


def _NETLIST_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "netlist"


def _SCH_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "sch"


def _SYM_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "sym"


def _PRJ_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "prj"


def _FPLIB_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "fplib"


def _PCB_DIR(version: int = DEFAULT_VERSION) -> Path:
    return _VERSION_DIR(version) / "pcb"


def all_pcb_fixtures() -> list[tuple[int, Path]]:
    """All board fixtures across format versions, as (version, path).

    Non-recursive on purpose: special-purpose sub-corpora (e.g. v9/pcb/modular)
    are owned by their own tests.
    """
    out: list[tuple[int, Path]] = []
    for vdir in sorted(FILEFORMATS_PATH.glob("v*"), key=lambda p: int(p.name[1:])):
        pcb_dir = vdir / "pcb"
        if pcb_dir.is_dir():
            version = int(vdir.name[1:])
            out.extend((version, p) for p in sorted(pcb_dir.glob("*.kicad_pcb")))
    return out


PRJFILE = _PRJ_DIR() / "test.kicad_pro"
PCBFILE = _PCB_DIR() / "test.kicad_pcb"
FPFILE = _FP_DIR() / "test.kicad_mod"
NETFILE = _NETLIST_DIR() / "test_e.net"
FPLIBFILE = _FPLIB_DIR(7) / "fp-lib-table"
SCHFILE = _SCH_DIR() / "test.kicad_sch"
SYMFILE = _SYM_DIR() / "test.kicad_sym"
