# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""length_report — board-side per-net routed-length metrology (F2 lane).

The build → route → snapshot/diagnose loop had ZERO numeric length data in its
artifacts: the router knows lengths internally but they never crossed into
anything an agent reads. This module computes them from the board itself, as
pure functions over a loaded `kicad.pcb.KicadPcb`:

  build_length_report(pcb, project=None) -> {"nets", "pairs", "classes"}

  nets    — per NAMED net: `track_mm` (summed centerline length of the net's
            straight track segments + track arcs, mm), `via_count`,
            `segment_count` (number of copper track items: segments + arcs).
            Arc length = circumcircle through start/mid/end (radius × the
            subtended angle, direction chosen so the arc passes through mid).
  pairs   — differential pairs detected by the SAME suffix conventions the
            router uses (vendor net_queries.extract_diff_pair_base semantics
            for `_P`/`_N`, bare `P`/`N` after a digit/underscore, and `+`/`-`,
            reimplemented here — vendor code is not importable from
            src/faebryk). A net pairs only WITHIN one convention: `CLK+`
            pairs with `CLK-`, never with an unrelated `CLK_N`. Shape:
            {base: {p_net, n_net, p_track_mm, n_track_mm, skew_mm}}.
  classes — when a `.kicad_pro` project is supplied: per netclass
            {longest: {net, track_mm}, shortest: {net, track_mm}, spread_mm}
            over the project's `netclass_patterns`, which are EXACT net names
            (the shape board_rules.py F5 authors). A class with no patterns
            has nothing to aggregate and is omitted.

HONESTY: the field is named `track_mm` because that is exactly what it
measures — routed track centerline length. KiCad's DRC length/skew rules
additionally count via Z-length and pad-to-die lengths, so this number is NOT
the DRC-effective length and no fabricated "total" is emitted. DRC rules
remain the authority on length constraints; this report is iteration guidance
for an agent tuning routed lengths.

Loud-or-nothing (S5a): a degenerate (collinear start/mid/end) arc, copper
referencing a net number absent from the net table, and a netclass pattern
naming a net that is not on the board all raise `LengthReportError` — a
silent zero would misguide the tuning loop. Unnamed copper (net 0 / the ""
net) is excluded BY DEFINITION (the report is per named net), not silently
dropped: KiCad itself garbage-collects net-0 copper on re-save.
"""

import math
from pathlib import Path
from typing import Any

_ROUND = 6  # mm decimals in emitted numbers (sub-nm — never masks real skew)
_COLLINEAR_EPS = 1e-9  # |2·cross| below this (mm²) = degenerate circumcircle


class LengthReportError(ValueError):
    """A board construct the length report cannot honestly measure."""


def arc_length_mm(
    start: tuple[float, float],
    mid: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Length of a KiCad track arc through start/mid/end: circumcircle radius ×
    the subtended angle, sweep direction chosen so the arc passes through mid
    (the major arc when mid lies on the far side). Collinear points have no
    circumcircle — loud, not a chord-length guess."""
    ax, ay = start
    bx, by = mid
    cx, cy = end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < _COLLINEAR_EPS:
        raise LengthReportError(
            f"degenerate arc (collinear start/mid/end): {start} {mid} {end} — "
            "no circumcircle; a straight track must be a segment, not an arc"
        )
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)
    tau = 2.0 * math.pi
    t_start = math.atan2(ay - uy, ax - ux)
    t_mid = math.atan2(by - uy, bx - ux)
    t_end = math.atan2(cy - uy, cx - ux)
    sweep = (t_end - t_start) % tau
    mid_off = (t_mid - t_start) % tau
    if mid_off > sweep:  # mid not on the CCW start→end sweep → the arc goes CW
        sweep = tau - sweep
    return radius * sweep


def extract_pair_suffix(net_name: str) -> tuple[str, bool, str] | None:
    """(base, is_positive, style) when `net_name` is one half of a differential
    pair by suffix convention, else None. Styles: `"_P"` (`X_P`/`X_N`), `"P"`
    (bare `XP`/`XN`, only after a digit or underscore so e.g. `STOP` is not a
    pair half), `"+"` (`X+`/`X-`). Reimplements the vendor router's
    extract_diff_pair_base semantics for these three conventions — pairing is
    only valid within one style (the caller keys on (base, style))."""
    if net_name.endswith("_P"):
        return net_name[:-2], True, "_P"
    if net_name.endswith("_N"):
        return net_name[:-2], False, "_P"
    if len(net_name) > 1 and net_name[-1] in "PN" and net_name[-2] in "0123456789_":
        return net_name[:-1], net_name[-1] == "P", "P"
    if net_name.endswith("+"):
        return net_name[:-1], True, "+"
    if net_name.endswith("-"):
        return net_name[:-1], False, "+"
    return None


def _net_stats(pcb: Any) -> dict[str, dict]:
    """{net_name: {track_mm, via_count, segment_count}} for every NAMED net —
    including unrouted ones (track_mm 0.0 is honest data for the loop)."""
    by_number: dict[int, str] = {n.number: (n.name or "") for n in pcb.nets}

    def name_of(number: int, what: str) -> str:
        if number not in by_number:
            raise LengthReportError(
                f"{what} references net number {number}, absent from the "
                "board's net table — cannot attribute its copper to a net"
            )
        return by_number[number]

    nets: dict[str, dict] = {
        name: {"track_mm": 0.0, "via_count": 0, "segment_count": 0}
        for name in by_number.values()
        if name
    }
    for seg in pcb.segments:
        name = name_of(seg.net, "track segment")
        if not name:
            continue  # unnamed (net-0) copper: excluded by definition, see doc
        stats = nets[name]
        stats["track_mm"] += math.hypot(
            seg.end.x - seg.start.x, seg.end.y - seg.start.y
        )
        stats["segment_count"] += 1
    for arc in pcb.arcs:
        name = name_of(arc.net, "track arc")
        if not name:
            continue
        stats = nets[name]
        stats["track_mm"] += arc_length_mm(
            (arc.start.x, arc.start.y),
            (arc.mid.x, arc.mid.y),
            (arc.end.x, arc.end.y),
        )
        stats["segment_count"] += 1
    for via in pcb.vias:
        name = name_of(via.net, "via")
        if not name:
            continue
        nets[name]["via_count"] += 1
    for stats in nets.values():
        stats["track_mm"] = round(stats["track_mm"], _ROUND)
    return nets


def _pairs(nets: dict[str, dict]) -> dict[str, dict]:
    """Differential-pair aggregation over the measured nets. Keyed by base name
    (suffix-style-qualified only on the pathological collision of two complete
    pairs sharing a base across conventions). Incomplete halves are simply not
    pairs — an unpaired net stays in `nets` and is not an error."""
    halves: dict[tuple[str, str], dict[str, str]] = {}
    for name in nets:
        extracted = extract_pair_suffix(name)
        if extracted is None:
            continue
        base, is_p, style = extracted
        halves.setdefault((base, style), {})["p" if is_p else "n"] = name
    pairs: dict[str, dict] = {}
    for (base, style), half in sorted(halves.items()):
        if "p" not in half or "n" not in half:
            continue
        key = base if base not in pairs else f"{base}({style})"
        p_net, n_net = half["p"], half["n"]
        p_mm, n_mm = nets[p_net]["track_mm"], nets[n_net]["track_mm"]
        pairs[key] = {
            "p_net": p_net,
            "n_net": n_net,
            "p_track_mm": p_mm,
            "n_track_mm": n_mm,
            "skew_mm": round(abs(p_mm - n_mm), _ROUND),
        }
    return pairs


def _classes(nets: dict[str, dict], project: Any) -> dict[str, dict]:
    """Per-netclass {longest, shortest, spread_mm} from the project's
    `netclass_patterns` — exact net names (the F5-authored shape); a pattern
    that names no net on the board is loud, not skipped."""
    members: dict[str, list[str]] = {}
    for pat in project.net_settings.netclass_patterns:
        if pat.pattern not in nets:
            raise LengthReportError(
                f"netclass pattern {pat.pattern!r} (class {pat.netclass!r}) "
                "does not name a net on the board — patterns must be exact "
                "net names (the board_rules F5 shape)"
            )
        members.setdefault(pat.netclass, []).append(pat.pattern)
    classes: dict[str, dict] = {}
    for cls_name, net_names in sorted(members.items()):
        ranked = sorted(net_names, key=lambda n: (nets[n]["track_mm"], n))
        shortest, longest = ranked[0], ranked[-1]
        classes[cls_name] = {
            "longest": {"net": longest, "track_mm": nets[longest]["track_mm"]},
            "shortest": {"net": shortest, "track_mm": nets[shortest]["track_mm"]},
            "spread_mm": round(
                nets[longest]["track_mm"] - nets[shortest]["track_mm"], _ROUND
            ),
        }
    return classes


def build_length_report(pcb: Any, project: Any | None = None) -> dict:
    """The full report: {"nets", "pairs", "classes"} (see module docstring for
    the exact shapes and the honesty statement). `pcb` is a loaded
    `kicad.pcb.KicadPcb`; `project` an optional `C_kicad_project_file` whose
    netclass_patterns drive the `classes` aggregation ({} without one)."""
    nets = _net_stats(pcb)
    return {
        "nets": nets,
        "pairs": _pairs(nets),
        "classes": _classes(nets, project) if project is not None else {},
    }


def length_report_for_board(board_path: Path) -> dict:
    """`build_length_report` over a board FILE, picking up the netclass
    aggregation from `<board>.kicad_pro` when it sits next to the board (the
    snapshot staging layout; absent → classes {})."""
    from faebryk.libs.kicad.fileformats import kicad
    from faebryk.libs.kicad.other_fileformats import C_kicad_project_file

    board_path = Path(board_path)
    pcb = kicad.loads(kicad.pcb.PcbFile, board_path.read_text()).kicad_pcb
    project = None
    project_path = board_path.with_suffix(".kicad_pro")
    if project_path.exists():
        project = C_kicad_project_file.loads(project_path)
    return build_length_report(pcb, project=project)
