"""H-partial: place-what-you-can, report-the-rest.

`collect_unresolved_modules` must list exactly the designated parts that have no
footprint (so are absent from the board), with the right reason, and drop a part
from the list the moment it gains a footprint.
"""

import faebryk.core.node as fabll
import faebryk.library._F as F
import pytest
from faebryk.core.faebrykpy import EdgeComposition
from faebryk.libs.app.partial import collect_unresolved_modules
from faebryk.libs.kicad.paths import GLOBAL_FP_DIR_PATH
from faebryk.libs.util import not_none
from test.compiler.conftest import build_instance


def _get_child(node, name):
    return not_none(
        EdgeComposition.get_child_by_identifier(bound_node=node, child_identifier=name)
    )


@pytest.mark.usefixtures("setup_project_config")
def test_unresolved_reports_and_clears_on_attach():
    if not (GLOBAL_FP_DIR_PATH / "Resistor_SMD.pretty").is_dir():
        pytest.skip("KiCad standard footprint library not installed")
    from faebryk.libs.app.package_footprint import attach_package_footprint

    _, _, _, _, app_instance = build_instance(
        """
        import Resistor

        module A:
            r1 = new Resistor
            r1.package = "R0402"
            r2 = new Resistor
            r2.package = "R2220"
        """,
        "A",
    )
    app = fabll.Node.bind_instance(app_instance)

    # nothing attached yet: both designated parts are unresolved
    before = collect_unresolved_modules(app)
    assert {u.address for u in before} == {"r1", "r2"}
    assert all(u.reason == "no-standard-footprint" for u in before)

    # attach r1's package footprint -> only r2 remains unresolved
    r1 = F.Resistor.bind_instance(_get_child(app_instance, "r1"))
    assert attach_package_footprint(r1) is True

    after = collect_unresolved_modules(app)
    assert [u.address for u in after] == ["r2"]  # sorted, r1 dropped


@pytest.mark.usefixtures("setup_project_config")
def test_unresolved_reason_deferred_pick_without_package():
    _, _, _, _, app_instance = build_instance(
        """
        import Resistor

        module A:
            r1 = new Resistor
        """,
        "A",
    )
    app = fabll.Node.bind_instance(app_instance)
    unresolved = collect_unresolved_modules(app)
    assert [(u.address, u.reason) for u in unresolved] == [("r1", "deferred-pick")]
