# This file is part of the faebryk project
# SPDX-License-Identifier: MIT


import subprocess
import tempfile
from pathlib import Path

from kicadcliwrapper.lib import find_kicad_cli

from faebryk.libs.kicad.fileformats import C_kicad_drc_report_file


def run_drc(pcb: Path) -> C_kicad_drc_report_file:
    """kicad-cli DRC → typed report. `--refill-zones`: build-authored pours
    are `(fill yes)` zones WITHOUT stored fill polygons (board_features.py),
    so judging the stored fill falsely reports plane-only-connected pads as
    unconnected — connectivity is judged on the refilled state instead. The
    board file is not touched (no --save-board). Direct subprocess because
    kicadcliwrapper's generated drc surface predates the flag (contract:
    test_drc_refill_contract.py)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "drc.json"
        cmd = [
            str(find_kicad_cli()),
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--refill-zones",
            "-o",
            str(out),
            str(pcb),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return C_kicad_drc_report_file.loads(out)
