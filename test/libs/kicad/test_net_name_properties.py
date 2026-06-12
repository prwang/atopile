# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Property tests for net-name string handling (BACKLOG P0.1 T5).

After the v10 migration the net *name* is the only key a net has in the file,
so a writer escaping bug stops being cosmetic and becomes a silent rename —
which disconnects copper. These tests push hostile names through the
write→parse→bind cycle.

Derandomized so CI is deterministic; the explicit @example cases are the
documented, always-run minimum.
"""

from hypothesis import example, given, settings
from hypothesis import strategies as st

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.kicad.semantic_view import semantic_view

TEMPLATE = """
(kicad_pcb
    (version 20241229)
    (generator "test_atopile")
    (generator_version "latest")
    (layers (0 "F.Cu" signal))
    (net 0 "")
    (net 1 "PLACEHOLDER")
    (footprint "test:FP"
        (layer "F.Cu")
        (at 10 10)
        (pad "1" smd rect
            (at 0 0)
            (size 1 1)
            (layers "F.Cu")
            (net 1 "PLACEHOLDER")
        )
    )
    (segment
        (start 0 0)
        (end 1 1)
        (width 0.2)
        (layer "F.Cu")
        (net 1)
        (uuid "11111111-2222-3333-4444-555555555555")
    )
)
"""

# \x00 is out of scope (C-string boundary, not representable in KiCad files).
# "" is excluded because the empty name is the reserved no-net name.
names = st.text(
    alphabet=st.characters(blacklist_characters="\x00"), min_size=1, max_size=64
)


@given(name=names)
@example('foo"bar')
@example("back\\slash")
@example('trailing backslash\\')
@example("new\nline")
@example("tab\there")
@example("(net 2)")
@example("{placeholder}")
@example("中文・ユニコード")
@example("🜲emoji")
@example(" leading and trailing ")
@example("a" * 255)
@settings(derandomize=True, max_examples=150, deadline=None)
def test_net_name_survives_write_parse_bind(name: str):
    pcb_file = kicad.loads(kicad.pcb.PcbFile, TEMPLATE)
    pcb = pcb_file.kicad_pcb
    net = next(n for n in pcb.nets if n.number == 1)
    net.name = name
    pad = pcb.footprints[0].pads[0]
    assert pad.net is not None
    pad.net.name = name

    dump = kicad.dumps(pcb_file)
    reloaded_file = kicad.loads(kicad.pcb.PcbFile, dump)
    reloaded = reloaded_file.kicad_pcb

    # the name itself survives a write→parse cycle
    assert next(n for n in reloaded.nets if n.number == 1).name == name

    # binding through the table still resolves everything to that exact name
    view = semantic_view(reloaded)
    assert view["nets"] == [name]
    assert view["footprints"][0]["pads"][0]["net"] == name
    assert view["segments"][0]["net"] == name

    # and the serialization is stable
    assert kicad.dumps(kicad.loads(kicad.pcb.PcbFile, dump)) == dump
