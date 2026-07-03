"""H3 out-of-source part-picker sidecar: inject picks/constraints onto the
instance graph from a parts.yaml, without editing the `.ato`.

Bites: the sidecar attaches the SAME traits the compiler attaches for an
in-source pin; addressing matches the board's `atopile_address`; an unknown
address is loud; an `.ato` pin wins.
"""

import faebryk.core.node as fabll
import faebryk.library._F as F
import pytest
from atopile.errors import UserException
from faebryk.core.faebrykpy import EdgeComposition
from faebryk.libs.app.parts_sidecar import (
    PartEntry,
    PartsSidecar,
    apply_parts_sidecar,
)
from faebryk.libs.smd import SMDSize
from faebryk.libs.util import not_none
from test.compiler.conftest import build_instance


def _get_child(node, name):
    return not_none(
        EdgeComposition.get_child_by_identifier(bound_node=node, child_identifier=name)
    )


def _app_with_r1():
    _, _, _, _, app_instance = build_instance(
        """
        import Resistor

        module A:
            r1 = new Resistor
        """,
        "A",
    )
    return fabll.Node.bind_instance(app_instance)


# --- schema (pure) ------------------------------------------------------------

def test_entry_rejects_lcsc_and_mpn():
    with pytest.raises(ValueError):
        PartEntry(lcsc="C1", mpn="X", manufacturer="Y")


def test_entry_mpn_requires_manufacturer():
    with pytest.raises(ValueError):
        PartEntry(mpn="X")


def test_entry_rejects_empty():
    with pytest.raises(ValueError):
        PartEntry()


def test_entry_rejects_unknown_key():
    with pytest.raises(Exception):
        PartEntry(lcsc="C1", bogus="x")


def test_to_yaml_is_sorted_and_minimal():
    sc = PartsSidecar(
        entries={
            "r2": PartEntry(package="R0603"),
            "r1": PartEntry(lcsc="C25819"),
        }
    )
    out = sc.to_yaml()
    assert out.index("r1:") < out.index("r2:")  # deterministic order
    assert "manufacturer" not in out  # unset fields excluded


# --- apply (graph) ------------------------------------------------------------

@pytest.mark.usefixtures("setup_project_config")
def test_sidecar_lcsc_injects_supplier_trait():
    app = _app_with_r1()
    r1 = _get_child(app.instance, "r1")
    assert apply_parts_sidecar(app, PartsSidecar(entries={"r1": PartEntry(lcsc="C25819")})) == 1

    node = fabll.Node.bind_instance(r1)
    trait = node.get_trait(F.Pickable.is_pickable_by_supplier_id)
    assert trait.get_supplier_part_id() == "C25819"


@pytest.mark.usefixtures("setup_project_config")
def test_sidecar_mpn_injects_partnumber_trait():
    app = _app_with_r1()
    r1 = _get_child(app.instance, "r1")
    apply_parts_sidecar(
        app,
        PartsSidecar(entries={"r1": PartEntry(mpn="RC0402FR-0710KL", manufacturer="Yageo")}),
    )
    node = fabll.Node.bind_instance(r1)
    trait = node.get_trait(F.Pickable.is_pickable_by_part_number)
    assert trait.get_partno() == "RC0402FR-0710KL"
    assert trait.get_manufacturer() == "Yageo"


@pytest.mark.usefixtures("setup_project_config")
def test_sidecar_package_constrains_size():
    app = _app_with_r1()
    r1 = _get_child(app.instance, "r1")
    apply_parts_sidecar(app, PartsSidecar(entries={"r1": PartEntry(package="R0402")}))
    node = fabll.Node.bind_instance(r1)
    assert node.get_trait(F.has_package_requirements).get_sizes() == [SMDSize.I0402]


@pytest.mark.usefixtures("setup_project_config")
def test_sidecar_unknown_address_is_loud():
    app = _app_with_r1()
    with pytest.raises(UserException):
        apply_parts_sidecar(app, PartsSidecar(entries={"nope": PartEntry(lcsc="C1")}))


@pytest.mark.usefixtures("setup_project_config")
def test_sidecar_skips_ato_pinned():
    app = _app_with_r1()
    r1 = _get_child(app.instance, "r1")
    node = fabll.Node.bind_instance(r1)
    # simulate an in-source pick: mark as already picked
    fabll.Traits.create_and_add_instance_to(node=node, trait=F.Pickable.has_part_picked)

    applied = apply_parts_sidecar(
        app, PartsSidecar(entries={"r1": PartEntry(lcsc="C25819")})
    )
    assert applied == 0
    assert not node.has_trait(F.Pickable.is_pickable_by_supplier_id)
