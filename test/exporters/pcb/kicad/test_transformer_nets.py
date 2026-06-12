# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Unit tests for the transformer's net bookkeeping (BACKLOG P0.1 T6).

These pin the *current* v9-dialect behavior before the v10 net-model migration
(P0.2): number synthesis, disconnect/rename propagation, and the name-based
lookup contract. Each test documents which contract the migration must keep
and which v9 quirk it is allowed to remove.

The transformer is built through a __new__ seam: the net methods only touch
self.pcb and self._net_number_generator, and pulling in a full instance graph
would make these E2E tests, not unit tests.
"""

import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.exporters.pcb.kicad.transformer import PCB_Transformer
from faebryk.libs.kicad.fileformats import kicad
from faebryk.libs.util import yield_missing

BOARD = """
(kicad_pcb
    (version 20241229)
    (generator "test_atopile")
    (generator_version "latest")
    (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
    (net 0 "")
    (net 1 "VCC")
    (net 3 "GND")
    (footprint "test:FP"
        (layer "F.Cu")
        (at 10 10)
        (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC"))
        (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu") (net 3 "GND"))
    )
    (segment (start 0 0) (end 1 1) (width 0.2) (layer "F.Cu") (net 1)
        (uuid "11111111-2222-3333-4444-555555555555"))
    (via (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1)
        (uuid "22222222-3333-4444-5555-666666666666"))
    (zone (net 3) (net_name "GND") (layer "F.Cu")
        (uuid "33333333-4444-5555-6666-777777777777")
        (hatch edge 0.5)
        (connect_pads (clearance 0.2))
        (min_thickness 0.2)
        (fill yes (thermal_gap 0.2) (thermal_bridge_width 0.2))
        (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10)))
    )
)
"""


@pytest.fixture
def transformer():
    pcb_file = kicad.loads(kicad.pcb.PcbFile, BOARD)
    t = PCB_Transformer.__new__(PCB_Transformer)
    t.pcb = pcb_file.kicad_pcb
    t._net_number_generator = iter(
        yield_missing({net.number for net in t.pcb.nets}, 1)
    )
    # pcb_file owns the zig memory; keep it alive alongside the transformer
    t.__pcb_file_keepalive = pcb_file
    return t


def _numbers(t) -> dict[str, int]:
    return {n.name: n.number for n in t.pcb.nets}


def test_insert_net_fills_number_holes(transformer):
    """Contract: synthesized numbers fill gaps deterministically (existing
    {0,1,3} → next are 2, then 4) and never collide with the table."""
    assert transformer.insert_net("first").number == 2
    assert transformer.insert_net("second").number == 4
    assert _numbers(transformer) == {"": 0, "VCC": 1, "GND": 3, "first": 2, "second": 4}


def test_inserted_net_survives_serialization(transformer):
    transformer.insert_net("fresh_net")
    dump = kicad.dumps(transformer.__pcb_file_keepalive)
    reloaded_file = kicad.loads(kicad.pcb.PcbFile, dump)
    assert {n.name for n in reloaded_file.kicad_pcb.nets} == {
        "", "VCC", "GND", "fresh_net",
    }


def test_removed_numbers_are_not_recycled(transformer):
    """Pin: the generator snapshots the table at construction and counts
    monotonically — removing a net does NOT return its number to the pool.
    The migration may relax this, but must do so deliberately."""
    vcc = next(n for n in transformer.pcb.nets if n.name == "VCC")
    transformer.remove_net(vcc)
    assert transformer.insert_net("a").number == 2
    assert transformer.insert_net("b").number == 4  # not the freed 1


def test_remove_net_disconnects_pads_and_routing(transformer):
    vcc = next(n for n in transformer.pcb.nets if n.name == "VCC")
    transformer.remove_net(vcc)

    assert 1 not in {n.number for n in transformer.pcb.nets}
    pad1 = transformer.pcb.footprints[0].pads[0]
    assert pad1.net is not None
    assert (pad1.net.number, pad1.net.name) == (0, "")
    assert transformer.pcb.segments[0].net == 0
    assert transformer.pcb.vias[0].net == 0
    # the GND pad is untouched
    pad2 = transformer.pcb.footprints[0].pads[1]
    assert pad2.net is not None and pad2.net.number == 3


def test_remove_net_disconnects_matching_zone(transformer):
    gnd = next(n for n in transformer.pcb.nets if n.name == "GND")
    transformer.remove_net(gnd)
    zone = transformer.pcb.zones[0]
    assert (zone.net, zone.net_name) == (0, "")


def test_remove_net_skips_zone_with_stale_name(transformer):
    """Pin a v9 dual-key quirk: remove_net only disconnects a zone when number
    AND net_name both match, so a zone with a stale net_name stays connected
    by number. v10 has a single key — P0.2 M5 must delete this ambiguity, at
    which point this test should be inverted."""
    zone = transformer.pcb.zones[0]
    zone.net_name = "STALE"
    gnd = next(n for n in transformer.pcb.nets if n.name == "GND")
    transformer.remove_net(gnd)
    assert zone.net == 3  # left dangling — current (questionable) behavior


def test_rename_net_propagates_to_pads_and_zones(transformer):
    vcc = next(n for n in transformer.pcb.nets if n.name == "VCC")
    transformer.rename_net(vcc, "VCC_RENAMED")

    assert "VCC_RENAMED" in _numbers(transformer)
    pad1 = transformer.pcb.footprints[0].pads[0]
    assert pad1.net is not None and pad1.net.name == "VCC_RENAMED"
    # routing references by number only — must be untouched
    assert transformer.pcb.segments[0].net == 1

    gnd = next(n for n in transformer.pcb.nets if n.name == "GND")
    transformer.rename_net(gnd, "GND2")
    assert transformer.pcb.zones[0].net_name == "GND2"


class _StubFbrkNet:
    """Duck-typed stand-in for F.Net: get_net only calls
    .get_trait(F.has_net_name).get_name(). Building a real instance graph here
    would turn a lookup-contract test into an E2E test."""

    def __init__(self, name: str):
        self._name = name

    def get_trait(self, _trait):
        name = self._name

        class _Trait:
            @staticmethod
            def get_name() -> str:
                return name

        return _Trait()


def test_get_net_looks_up_by_name_not_number(transformer):
    """Contract the migration relies on: get_net keys the table by *name*.
    Renumber everything; lookup must still land on the same net object."""
    for net, new_number in zip(transformer.pcb.nets, (7, 5, 6)):
        net.number = new_number

    found = transformer.get_net(_StubFbrkNet("GND"))
    assert found.name == "GND"
    assert found.number == 6

    with pytest.raises(KeyError):
        transformer.get_net(_StubFbrkNet("NO_SUCH_NET"))
