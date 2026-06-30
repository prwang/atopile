# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""diagnostics — `ato diagnose`'s structured `diagnostics.json` builder (§F / F4).

The closing of the project's defining loop: build → route → **diagnose** →
SKILL-edits-plan → rebuild. This module turns a route run + a KiCad DRC pass into
ONE structured, ato-address-indexed findings document the PCB-layout SKILL (and
lighter LLMs) consume to decide what to change.

It AGGREGATES three sources and CORRELATES every finding back to an ato address /
room through the bridge② reverse engine (`layout_ir_resolve.LayoutResolver`,
F3):

  1. route failures — per-stage `diag` (F2: net_name / reason / blocking_nets /
     history), falling back to the summary's failed lists for stages with no diag
     (bundle stages, or a router predating F2);
  2. DRC violations — `kicad-cli pcb drc` output, correlated via pad-uuid →
     component (F3) and coordinate → room (rule-area ring hit-test), and tagged
     new-vs-existing against a pre-route baseline (Ki-Stack: only routing-
     introduced DRC is freshly actionable);
  3. (the CLI also rereads the routed board for the true via/geometry totals — G3
     — and folds them into the summary).

The finding SCHEMA is adapted from kicad-happy's `make_finding`
(rule_id / severity / confidence / report_context) — that repo is a sandbox
reference, not an atopile dependency, so the schema is vendored here — extended
with the §F fields: stage, room, ato_path, constraint (a layout.yaml JSON
pointer), reason, blocking_nets, failed_endpoints, suggestions, is_new.

`sort_findings` gives a DETERMINISTIC order so `diagnostics.json` does not churn
across runs (semantic, uuid-free — same discipline as the rest of the fork).

HONEST LIMITS (BACKLOG §F, recorded here as the SSOT):
  * uuid correlation is footprint/pad only; a DRC item carrying a route-time
    track/via/zone uuid resolves by COORDINATE (room) + net name, not uuid (G2).
  * blocking_nets is the router's top-blocker net NAMES, not per-blocker cell
    counts (F2 limit).
  * DRC `nets` are recovered from the description text (kicad-cli emits no
    structured net field) — best-effort token extraction, not authoritative.
"""

import re
from typing import Any

from faebryk.libs.kicad.layout_ir_resolve import LayoutResolver

# --- schema vocabularies (loud on anything outside them) --------------------
VALID_SEVERITIES = ("error", "warning", "info")
VALID_CONFIDENCES = ("deterministic", "heuristic")
VALID_EVIDENCE_SOURCES = ("router", "drc", "geometry", "topology", "heuristic_rule")

# KiCad C_Severity (other_fileformats.py) → the 3-level finding severity.
_SEVERITY_MAP = {
    "error": "error",
    "warning": "warning",
    "info": "info",
    "action": "info",
    "exclusion": "info",
    "debug": "info",
}

# the §F finding fields, always present (stable schema; None/[] when N/A) so a
# consumer can read f["room"] etc. without KeyErrors.
_F_FIELDS_LIST = ("ato_path", "blocking_nets", "failed_endpoints", "suggestions")
_F_FIELDS_SCALAR = ("stage", "room", "constraint", "reason")

_NET_TOKEN = re.compile(r"/[A-Za-z0-9_+\-./]+")


def make_finding(
    *,
    rule_id: str,
    category: str,
    summary: str,
    description: str,
    detector: str = "ato_diagnose",
    severity: str = "warning",
    confidence: str = "heuristic",
    evidence_source: str = "heuristic_rule",
    components: list | None = None,
    nets: list | None = None,
    recommendation: str = "",
    report_section: str | None = None,
    impact: str | None = None,
    standard_ref: str | None = None,
    # --- §F extension fields ---
    stage: str | None = None,
    room: str | None = None,
    ato_path: list | None = None,
    constraint: str | None = None,
    reason: str | None = None,
    blocking_nets: list | None = None,
    failed_endpoints: list | None = None,
    suggestions: list | None = None,
    is_new: bool | None = None,
    **extra,
) -> dict:
    """A rich, self-describing finding dict (kicad-happy schema + §F fields).

    Loud-or-nothing (S5a): a severity / confidence / evidence_source outside its
    vocabulary is a hard error — a typo'd category must never silently downgrade."""
    if severity not in VALID_SEVERITIES:
        raise ValueError(
            f"make_finding: invalid severity {severity!r} (valid: {VALID_SEVERITIES})"
        )
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(
            f"make_finding: invalid confidence {confidence!r} "
            f"(valid: {VALID_CONFIDENCES})"
        )
    if evidence_source not in VALID_EVIDENCE_SOURCES:
        raise ValueError(
            f"make_finding: invalid evidence_source {evidence_source!r} "
            f"(valid: {VALID_EVIDENCE_SOURCES})"
        )
    finding: dict[str, Any] = {
        "detector": detector,
        "rule_id": rule_id,
        "category": category,
        "summary": summary,
        "description": description,
        "components": components if components is not None else [],
        "nets": nets if nets is not None else [],
        "severity": severity,
        "confidence": confidence,
        "evidence_source": evidence_source,
        "recommendation": recommendation,
    }
    finding["report_context"] = {
        "section": report_section or category.replace("_", " ").title(),
        "impact": impact or "",
        "standard_ref": standard_ref or "",
    }
    # §F fields — always present for a stable schema.
    finding["stage"] = stage
    finding["room"] = room
    finding["constraint"] = constraint
    finding["reason"] = reason
    finding["ato_path"] = ato_path if ato_path is not None else []
    finding["blocking_nets"] = blocking_nets if blocking_nets is not None else []
    finding["failed_endpoints"] = (
        failed_endpoints if failed_endpoints is not None else []
    )
    finding["suggestions"] = suggestions if suggestions is not None else []
    if is_new is not None:
        finding["is_new"] = is_new
    if extra:
        finding.update(extra)
    return finding


def sort_findings(findings: list) -> list:
    """Sort findings in place by a stable composite key so diagnostics.json is
    byte-stable across runs. Canonicalizes nested list fields first so upstream
    set/dict iteration order never leaks into output."""
    for f in findings:
        if not isinstance(f, dict):
            continue
        for key in ("components", "nets", "blocking_nets", "ato_path"):
            v = f.get(key)
            if isinstance(v, list) and all(
                not isinstance(x, (dict, list)) for x in v
            ):
                f[key] = sorted(v, key=str)

    def _key(f):
        if not isinstance(f, dict):
            return (1, "", "", "", "", "")
        comps = f.get("components") or []
        nets = f.get("nets") or []
        return (
            0,
            str(f.get("rule_id") or ""),
            str(f.get("detector") or ""),
            str(comps[0]) if comps else "",
            str(nets[0]) if nets else "",
            str(f.get("summary") or ""),
        )

    findings.sort(key=_key)
    return findings


def drc_violation_key(violation: dict) -> tuple:
    """A stable identity for a DRC violation, for new-vs-baseline matching. Uses
    the rule type + each item's (uuid, rounded position) — coordinate-rounded so
    a re-emit at sub-nm float jitter still matches (uuids are stable per item)."""
    items = violation.get("items", [])
    item_keys = tuple(
        sorted(
            (
                str(it.get("uuid", "")),
                round(float(it.get("x", 0.0)), 3),
                round(float(it.get("y", 0.0)), 3),
            )
            for it in items
        )
    )
    return (violation.get("type", ""), item_keys)


def _addr_room_index(ir: dict) -> dict:
    """addr → room name, inverted from ir['rooms'].member_addrs (the IR carries the
    forward room→members map; route findings need the reverse)."""
    out: dict[str, str] = {}
    for room, rd in ir.get("rooms", {}).items():
        for addr in rd.get("member_addrs", []):
            out[addr] = room
    return out


def _route_finding(rec: dict, stage_name: str, resolver: LayoutResolver,
                   addr_room: dict) -> dict:
    """One ROUTE-FAIL finding from an F2 diag record (or a bare summary net)."""
    net = rec.get("net_name", "")
    endpoints = resolver.net_endpoints(net)  # ["addr.pad", ...]
    addrs = sorted({ep.rsplit(".", 1)[0] for ep in endpoints})
    rooms = sorted({addr_room[a] for a in addrs if a in addr_room})
    room = rooms[0] if len(rooms) == 1 else None  # a single-room net → its room
    blocking = list(rec.get("blocked_by") or [])
    reason = rec.get("reason")
    recommendation = (
        f"net {net} did not route ({reason or 'no path'}). "
        + (f"Clear space taken by {', '.join(blocking)}, " if blocking else "")
        + "or relax this stage's corridor/spacing in layout.yaml and re-route."
    )
    return make_finding(
        rule_id="ROUTE-FAIL",
        category="routing",
        summary=f"net {net} failed to route in stage {stage_name!r}",
        description=(
            f"The router could not connect net {net} in route stage "
            f"{stage_name!r}: {reason or 'no path found'}."
        ),
        severity="error",
        confidence="deterministic",
        evidence_source="router",
        nets=[net],
        components=addrs,
        stage=stage_name,
        room=room,
        ato_path=addrs,
        reason=reason,
        blocking_nets=blocking,
        failed_endpoints=endpoints,
        recommendation=recommendation,
    )


def _summary_failed_nets(stage: dict) -> list:
    """Failed net identifiers from a stage summary, for stages without an F2 diag
    (bundle: failed_members; single/diff fallbacks). Best-effort flatten to names."""
    summary = stage.get("summary") or {}
    out: list = []
    for key in ("failed_single", "failed_diff_pairs", "failed_members"):
        for item in summary.get(key, []) or []:
            out.append(item if isinstance(item, str) else item.get("net", str(item)))
    return out


def _drc_finding(violation: dict, resolver: LayoutResolver,
                 baseline_drc_keys: set | None) -> dict:
    """One DRC finding, correlated to components (pad/footprint uuid) + room
    (coordinate hit-test) through F3, with new-vs-baseline tagging."""
    items = violation.get("items", [])
    components: set[str] = set()
    room = None
    for it in items:
        uuid = it.get("uuid")
        if uuid is not None:
            fa = resolver.footprint_addr(uuid)
            if fa:
                components.add(fa)
            pa = resolver.pad_addr(uuid)
            if pa:
                components.add(pa[0])
        if room is None and it.get("x") is not None:
            room = resolver.room_at(float(it["x"]), float(it["y"]))
    description = violation.get("description", "")
    nets = sorted(set(_NET_TOKEN.findall(description)))  # best-effort (no struct field)
    vtype = violation.get("type", "unknown")
    severity = _SEVERITY_MAP.get(str(violation.get("severity", "")).lower(), "warning")
    is_new = (
        None
        if baseline_drc_keys is None
        else drc_violation_key(violation) not in baseline_drc_keys
    )
    return make_finding(
        rule_id=f"DRC-{vtype}",
        category="drc",
        summary=f"DRC {vtype}: {description[:80]}",
        description=description or f"DRC {vtype} violation",
        severity=severity,
        confidence="deterministic",
        evidence_source="drc",
        components=sorted(components),
        nets=nets,
        room=room,
        is_new=is_new,
    )


def build_diagnostics(
    *,
    route_report: dict,
    drc_violations: list,
    ir: dict,
    room_polygons: dict | None = None,
    baseline_drc_keys: set | None = None,
    board_totals: dict | None = None,
) -> dict:
    """Aggregate a route run + DRC into the ato-indexed diagnostics document.

    Pure: every input is canned data (a route_report dict, DRC violation dicts, an
    IR, optional room rings + a DRC baseline + reread board totals). Returns
    `{findings, summary, totals}` with findings deterministically ordered."""
    resolver = LayoutResolver(ir, room_polygons=room_polygons)
    addr_room = _addr_room_index(ir)
    findings: list = []

    route_failures = 0
    for stage in route_report.get("stages", []):
        stage_name = stage.get("stage_name")
        diag = stage.get("diag")
        if diag:
            for rec in diag:
                findings.append(_route_finding(rec, stage_name, resolver, addr_room))
                route_failures += 1
        else:
            for net in _summary_failed_nets(stage):
                findings.append(
                    _route_finding(
                        {"net_name": net, "reason": None, "blocked_by": []},
                        stage_name, resolver, addr_room,
                    )
                )
                route_failures += 1

    drc_count = 0
    for violation in drc_violations:
        findings.append(_drc_finding(violation, resolver, baseline_drc_keys))
        drc_count += 1

    sort_findings(findings)
    return {
        "findings": findings,
        "summary": {
            "route_failures": route_failures,
            "drc_violations": drc_count,
            "total_findings": len(findings),
            "new_drc": sum(1 for f in findings if f.get("is_new") is True),
        },
        "totals": {**route_report.get("totals", {}), **(board_totals or {})},
    }
