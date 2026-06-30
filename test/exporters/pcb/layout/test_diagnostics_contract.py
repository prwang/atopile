# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""§F / F4 contract — `ato diagnose` → diagnostics.json (the core deliverable).

F4 is the closing of the diagnostics loop: it AGGREGATES a route run + KiCad DRC
into one structured, ato-address-indexed `diagnostics.json`. The pure builder
(`build_diagnostics`) takes CANNED inputs (a route_report dict + DRC violation
dicts + an IR + room rings) so its correlation + schema + deterministic ordering
are unit-testable with NO board, DRC binary, or router — the answer is fixed by
construction (a known unroutable net → a finding that names the blocker; a DRC
clearance at a known pad → a finding attributed to that component/room).

What this pins:
  - a ROUTE-FAIL finding per failed net, carrying the F2 cause (reason +
    blocking_nets) and resolved (F3) to its ato_path / room / stage;
  - a DRC finding per violation, correlated through F3 (pad-uuid → component,
    coord → room) and tagged new-vs-existing against a baseline;
  - the kicad-happy finding SCHEMA (rule_id/severity/confidence/report_context)
    + the §F fields (stage/room/ato_path/reason/blocking_nets/...);
  - deterministic `sort_findings` — shuffled inputs produce identical output;
  - loud schema validation (a bad severity is rejected).

S0 strict-xfail: gated on the builder landing.
"""

import pytest

try:
    from faebryk.exporters.pcb.layout.diagnostics import (
        build_diagnostics,
        make_finding,
        sort_findings,
    )

    _F4_LANDED = True
except Exception:  # noqa: BLE001
    _F4_LANDED = False

    def build_diagnostics(*a, **k):
        raise RuntimeError("F4 diagnostics not landed")

    def make_finding(*a, **k):
        raise RuntimeError("F4 diagnostics not landed")

    def sort_findings(*a, **k):
        raise RuntimeError("F4 diagnostics not landed")


needs_f4 = pytest.mark.xfail(
    not _F4_LANDED, reason="F4 diagnostics not landed yet", strict=True
)


# --- canned inputs (the consumer-frozen shapes) -----------------------------
def _ir() -> dict:
    return {
        "layout_ir_version": 2,
        "components": {
            "top.u1": {
                "ref": "U1", "footprint_uuid": "fp-u1", "at": [10, 10, 0],
                "layer": "F.Cu",
                "pads": {
                    "3": {"uuid": "pad-clk", "net": "/CLK", "at": [10, 9, 0], "layers": ["F.Cu"]},
                },
            },
            "top.u2": {
                "ref": "U2", "footprint_uuid": "fp-u2", "at": [40, 40, 0],
                "layer": "F.Cu",
                "pads": {
                    "1": {"uuid": "pad-gnd", "net": "/GND", "at": [40, 39, 0], "layers": ["F.Cu"]},
                },
            },
        },
        "nets": {"/CLK": ["top.u1.3"], "/GND": ["top.u2.1"]},
        "rooms": {
            "top.u1": {"sheetname": "top.u1", "member_addrs": ["top.u1"]},
            "top.u2": {"sheetname": "top.u2", "member_addrs": ["top.u2"]},
        },
    }


_RINGS = {
    "top.u1": [(5.0, 5.0), (15.0, 5.0), (15.0, 15.0), (5.0, 15.0)],
    "top.u2": [(35.0, 35.0), (45.0, 35.0), (45.0, 45.0), (35.0, 45.0)],
}


def _route_report() -> dict:
    return {
        "stages": [
            {
                "stage_name": "sigs", "stage_type": "single",
                "successful": 0, "failed": 1, "total_vias": 0,
                "summary": {"successful": 0, "failed": 1, "failed_single": ["/CLK"]},
                "diag": [
                    {
                        "net_name": "/CLK", "reason": "no path found",
                        "blocked_by": ["/GND"],
                        "history": [{"event": "reroute_failed", "sequence": 3,
                                     "details": {"reason": "no path found"}}],
                    }
                ],
            }
        ],
        "totals": {"successful": 0, "failed": 1, "total_vias": 0},
        "by_type": {"single": {"failed_single": ["/CLK"]}},
        "up_to": None, "final_board": "out.kicad_pcb",
    }


def _drc_violation(uuid="pad-clk", x=10.0, y=10.0) -> dict:
    """A clearance violation at U1's CLK pad (the builder-facing DRC dict shape —
    the CLI adapts the typed C_Violation into this)."""
    return {
        "type": "clearance",
        "severity": "error",
        "description": "Clearance violation (net /CLK to net /GND)",
        "items": [
            {"uuid": uuid, "x": x, "y": y, "description": "pad"},
            {"uuid": "track-zzz", "x": x + 1, "y": y, "description": "track"},
        ],
    }


# ===========================================================================
@needs_f4
def test_route_failure_finding_is_attributed_and_carries_cause():
    diag = build_diagnostics(
        route_report=_route_report(), drc_violations=[], ir=_ir(),
        room_polygons=_RINGS,
    )
    findings = diag["findings"]
    rf = [f for f in findings if f["rule_id"].startswith("ROUTE")]
    assert len(rf) == 1
    f = rf[0]
    assert f["nets"] == ["/CLK"]
    assert f["severity"] == "error" and f["confidence"] == "deterministic"
    # F2 cause is carried through:
    assert f["reason"] == "no path found"
    assert f["blocking_nets"] == ["/GND"]
    assert f["stage"] == "sigs"
    # F3 correlation: the net endpoint resolves to its ato address + room.
    assert "top.u1" in f["ato_path"]
    assert f["room"] == "top.u1"


@needs_f4
def test_drc_finding_is_correlated_to_component_and_room():
    diag = build_diagnostics(
        route_report={"stages": [], "totals": {}, "by_type": {}, "up_to": None,
                      "final_board": ""},
        drc_violations=[_drc_violation()], ir=_ir(), room_polygons=_RINGS,
    )
    drc = [f for f in diag["findings"] if f["rule_id"].startswith("DRC")]
    assert len(drc) == 1
    f = drc[0]
    assert f["rule_id"] == "DRC-clearance"
    assert f["severity"] == "error"
    # pad-uuid → component (F3); the unresolvable track-zzz uuid does NOT crash it.
    assert "top.u1" in f["components"]
    # coordinate (10,10) → room via the rule-area ring (F3).
    assert f["room"] == "top.u1"


@needs_f4
def test_drc_new_vs_existing_against_baseline():
    """A violation present in the pre-route baseline is `is_new=False`; a fresh one
    is `is_new=True` (Ki-Stack: only routing-introduced DRC is actionable here)."""
    v = _drc_violation()
    # the baseline key set already contains this violation → existing, not new.
    from faebryk.exporters.pcb.layout.diagnostics import drc_violation_key

    baseline = {drc_violation_key(v)}
    diag = build_diagnostics(
        route_report={"stages": [], "totals": {}, "by_type": {}, "up_to": None,
                      "final_board": ""},
        drc_violations=[v], ir=_ir(), room_polygons=_RINGS,
        baseline_drc_keys=baseline,
    )
    assert diag["findings"][0]["is_new"] is False

    diag_new = build_diagnostics(
        route_report={"stages": [], "totals": {}, "by_type": {}, "up_to": None,
                      "final_board": ""},
        drc_violations=[v], ir=_ir(), room_polygons=_RINGS,
        baseline_drc_keys=set(),
    )
    assert diag_new["findings"][0]["is_new"] is True


@needs_f4
def test_findings_are_deterministically_ordered():
    """Two builds with the DRC list in different order produce byte-identical
    findings (deterministic sort — diagnostics.json must not churn)."""
    import json

    v1 = _drc_violation(uuid="pad-clk", x=10, y=10)
    v2 = _drc_violation(uuid="pad-gnd", x=40, y=40)
    a = build_diagnostics(route_report=_route_report(), drc_violations=[v1, v2],
                          ir=_ir(), room_polygons=_RINGS)
    b = build_diagnostics(route_report=_route_report(), drc_violations=[v2, v1],
                          ir=_ir(), room_polygons=_RINGS)
    assert json.dumps(a["findings"]) == json.dumps(b["findings"])


@needs_f4
def test_make_finding_rejects_bad_severity():
    with pytest.raises(ValueError):
        make_finding(rule_id="X", category="c", summary="s", description="d",
                     severity="catastrophic")


@needs_f4
def test_sort_findings_is_stable_and_canonicalizes_lists():
    fs = [
        make_finding(rule_id="B", category="c", summary="s", description="d",
                     nets=["z", "a"]),
        make_finding(rule_id="A", category="c", summary="s", description="d"),
    ]
    sort_findings(fs)
    assert [f["rule_id"] for f in fs] == ["A", "B"]
    # nested net lists are canonicalized so set-iteration order never leaks.
    assert fs[1]["nets"] == ["a", "z"]


@needs_f4
def test_multipoint_failures_are_not_dropped():
    """A failed MULTIPOINT net (tap pads unconnected) lives only in the summary's
    failed_multipoint — never in diag — and must still produce a ROUTE-FAIL
    finding, even when a single-ended net ALSO failed in the same stage (the diag
    branch must not shadow it). S5a: no silently-dropped actionable failure."""
    rr = _route_report()  # already has a failed single /CLK with a diag
    rr["stages"][0]["summary"]["failed_multipoint"] = [
        {"net_name": "/GND",
         "failed_pads": [
             {"component_ref": "U1", "pad_number": "7", "x": 10.0, "y": 10.0},
             {"component_ref": "U2", "pad_number": "1", "x": 40.0, "y": 40.0},
         ]}
    ]
    rr["totals"]["failed"] = 2  # the single + the multipoint
    diag = build_diagnostics(route_report=rr, drc_violations=[], ir=_ir(),
                             room_polygons=_RINGS)
    rf = [f for f in diag["findings"] if f["rule_id"] == "ROUTE-FAIL"]
    nets = {f["nets"][0] for f in rf}
    assert nets == {"/CLK", "/GND"}  # BOTH surfaced, not just the single
    mp = next(f for f in rf if f["nets"] == ["/GND"])
    # multipoint pads correlated to ato addresses (designator → addr) + endpoints.
    assert "top.u1" in mp["ato_path"] and "top.u2" in mp["ato_path"]
    assert mp["failed_endpoints"] == ["U1.7", "U2.1"]
    # nothing left unaccounted vs the router's failed total.
    assert diag["summary"]["route_failures_unaccounted"] == 0


@needs_f4
def test_unaccounted_failures_are_surfaced_not_hidden():
    """If the router reports more failures than the diagnostics layer can attribute,
    the gap is SURFACED in the summary (never silently swallowed)."""
    rr = _route_report()
    rr["totals"]["failed"] = 5  # router says 5 failed, but only /CLK is attributable
    diag = build_diagnostics(route_report=rr, drc_violations=[], ir=_ir(),
                             room_polygons=_RINGS)
    assert diag["summary"]["route_failures"] == 1
    assert diag["summary"]["route_failures_unaccounted"] == 4


@needs_f4
def test_diagnostics_has_summary_totals():
    diag = build_diagnostics(route_report=_route_report(),
                             drc_violations=[_drc_violation()], ir=_ir(),
                             room_polygons=_RINGS)
    s = diag["summary"]
    assert s["route_failures"] == 1
    assert s["drc_violations"] == 1
    assert s["total_findings"] == len(diag["findings"]) == 2
