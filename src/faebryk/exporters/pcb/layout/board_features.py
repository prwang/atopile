# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""board_features — author copper pours / keepouts / silk into the board (§F/F6-F8).

Three board-authoring concerns that were previously REUSE-ONLY (copied from a
source layout, never authored as text) become declarative `layout.yaml` intent:

  * F6 (F-fill)    — `board.pours`    → a copper Zone with `(fill yes)` bound to a
                     REAL net (bridge②). A net-0 pour is silently GC'd by KiCad, so
                     an unresolvable net is loud (S5a), never a dropped pour.
  * F7 (F-keepout) — `board.keepouts` → a Zone with a ZoneKeepout restriction and
                     NO net / NO placement (so the ZonePlacement source_type/source
                     SEGFAULT footgun is structurally unreachable here).
  * F8 (F-silk)    — `board.silk`     → a gr_text on a silk layer.
  * board outline  — `board.outline`  → the Edge.Cuts boundary (a declared
                     outline is the edge AUTHORITY: replace, never accrete).

Idempotent across rebuilds: managed pour/keepout zones carry a name prefix
(`fbrk_pour_` / `fbrk_keepout_`) and are removed before re-emit (the rule_area.py
pattern); managed silk has no name slot, so it is de-duplicated by exact
(text, position, layer) match before re-insert. HONEST LIMIT: silk whose text or
position the author CHANGES is not auto-removed (the old text orphans) — only an
unchanged re-emit is fully idempotent; a renamed silk needs a manual cleanup.

Empirically verified: all three round-trip through `kicad-cli pcb upgrade` rc=0.
"""

from typing import Any

from faebryk.exporters.pcb.layout.layout_plan import LayoutPlan, LayoutPlanError
from faebryk.libs.kicad.fileformats import kicad

_P = kicad.pcb
_MANAGED_POUR_PREFIX = "fbrk_pour_"
_MANAGED_KEEPOUT_PREFIX = "fbrk_keepout_"


def _ring(points) -> "kicad.pcb.Polygon":
    return _P.Polygon(pts=_P.Pts(xys=[_P.Xy(x=x, y=y) for x, y in points]))


def _keepout_flag(disallowed: bool):
    return _P.E_zone_keepout.NOT_ALLOWED if disallowed else _P.E_zone_keepout.ALLOWED


def _net_number(pcb, net_name: str) -> int:
    """The board net NUMBER for a kicad net name, or loud if absent (a pour must
    bind to a real net or KiCad garbage-collects it as net-0 floating copper)."""
    for net in pcb.nets:
        if net.name == net_name:
            return net.number
    raise LayoutPlanError(
        f"pour net {net_name!r} is not on the board — cannot bind a pour to a "
        "non-existent net (a net-0 pour is silently GC'd by KiCad)"
    )


_ELECTRICAL_STACKUP_TYPES = ("copper", "prepreg", "core")


def _stamp_stackup(pcb, stackup) -> int:
    """Replace the electrical core of `pcb.setup.stackup` with the declared
    layout.yaml `board.stackup` (see the authority note in
    `generate_board_features`). Returns the number of stamped layers.

    Mapping: plan `copper` → KiCad `copper` (+ thickness when declared); plan
    `dielectric` → KiCad `prepreg` when the declared material mentions prepreg,
    else `core`, renamed canonically "dielectric N" and carrying the declared
    thickness/material/Er (all three are guaranteed by the Stackup model).
    Cosmetic entries of an existing section (everything above the first / below
    the last electrical entry: silk, paste, mask) are preserved verbatim."""
    existing = pcb.setup.stackup
    head: list = []
    tail: list = []
    finish = None
    if existing is not None:
        entries = list(existing.layers)
        electrical_idx = [
            i for i, e in enumerate(entries) if e.type in _ELECTRICAL_STACKUP_TYPES
        ]
        if electrical_idx:
            head = entries[: electrical_idx[0]]
            tail = entries[electrical_idx[-1] + 1 :]
        else:
            head = entries
        finish = existing.copper_finish

    stamped: list = []
    dielectric_n = 0
    for layer in stackup.layers:
        if layer.type == "copper":
            stamped.append(
                _P.StackupLayer(
                    name=layer.name,
                    type="copper",
                    thickness=(
                        _P.Thickness(thickness=layer.thickness)
                        if layer.thickness is not None
                        else None
                    ),
                )
            )
        else:
            dielectric_n += 1
            kind = "prepreg" if "prepreg" in (layer.material or "").lower() else "core"
            stamped.append(
                _P.StackupLayer(
                    name=f"dielectric {dielectric_n}",
                    type=kind,
                    thickness=_P.Thickness(thickness=layer.thickness),
                    material=layer.material,
                    epsilon_r=layer.epsilon_r,
                )
            )

    pcb.setup.stackup = _P.Stackup(
        layers=[*head, *stamped, *tail],
        copper_finish=finish if finish is not None else _P.E_copper_finish.ENIG,
    )
    return len(stamped)


def generate_board_features(
    pcb, plan: LayoutPlan, ir: dict[str, Any]
) -> dict[str, int]:
    """Emit `plan.board` pours/keepouts/silk onto `pcb` (idempotent). Returns the
    inserted counts. A no-board / empty plan is a no-op (returns zeros)."""
    board = plan.board
    if board is None:
        return {"pours": 0, "keepouts": 0, "silk": 0}

    signal_nets: dict[str, str] = ir.get("signal_nets", {})

    # --- physical stackup (setup.stackup) ------------------------------------
    # `board.stackup` was previously only the layer-TABLE authority (layer count,
    # TS-AUTH-A/B) — the PHYSICAL `(stackup ...)` section stayed whatever the
    # board carried (a fresh board: KiCad's 2-layer 1.51 mm-core default). Every
    # consumer of dielectric geometry then silently read the WRONG board: the
    # router's impedance mode computed 100Ω pair width against the default core
    # and laid ~0.76 mm traces that shorted P to N (found live on F4). A declared
    # stackup is now the physical-section authority too: the electrical core
    # (copper + dielectrics) is replaced; cosmetic outer entries (silk/paste/
    # mask) and copper_finish are preserved — a declared stackup carries no
    # cosmetic facts. Dielectrics are canonically named "dielectric N" (KiCad's
    # own naming) and typed prepreg/core from the declared material. A
    # copper_finish is always present after stamping (the router's stackup
    # parser anchors its section scan on it). No declared stackup ⇒ untouched
    # (the reuse case).
    stackup_stamped = 0
    if board.stackup is not None:
        stackup_stamped = _stamp_stackup(pcb, board.stackup)

    # --- board outline (Edge.Cuts) -------------------------------------------
    # `board.outline` was previously validated but NEVER DRAWN — the board went
    # out with no Edge.Cuts, DRC said "malformed outline", and the fab boundary
    # existed only in yaml (the exact silent disaster the schema docstring
    # warns about). A declared outline is the Edge.Cuts AUTHORITY: existing
    # edge lines are replaced, never accreted. No declared outline ⇒ untouched
    # (a reuse board keeps its own edges).
    outline_drawn = 0
    if board.outline is not None:
        if board.outline.polygon is not None:
            pts = [tuple(p) for p in board.outline.polygon]
        else:
            (ox, oy), (w, h) = board.outline.origin, board.outline.size
            pts = [(ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)]
        kicad.filter(
            pcb, "gr_lines", pcb.gr_lines,
            lambda line: getattr(line, "layer", None) != "Edge.Cuts",
        )
        for a, b in zip(pts, pts[1:] + pts[:1]):
            kicad.insert(
                pcb, "gr_lines", pcb.gr_lines,
                _P.Line(
                    start=_P.Xy(x=a[0], y=a[1]),
                    end=_P.Xy(x=b[0], y=b[1]),
                    solder_mask_margin=None,
                    stroke=_P.Stroke(width=0.1, type="solid"),
                    fill=None,
                    layer="Edge.Cuts",
                    layers=["Edge.Cuts"],
                    locked=False,
                    uuid=kicad.gen_uuid(),
                ),
            )
            outline_drawn += 1

    # idempotency: drop previously-managed pour/keepout zones before re-emitting.
    kicad.filter(
        pcb, "zones", pcb.zones,
        lambda z: not (
            z.name is not None
            and (
                z.name.startswith(_MANAGED_POUR_PREFIX)
                or z.name.startswith(_MANAGED_KEEPOUT_PREFIX)
            )
        ),
    )

    # --- F6 pours -----------------------------------------------------------
    for i, pour in enumerate(board.pours):
        if pour.net not in signal_nets:
            raise LayoutPlanError(
                f"pour net address {pour.net!r} is not a resolvable ato signal "
                "address (not in bridge② signal_nets)"
            )
        net_name = signal_nets[pour.net]
        zone = _P.Zone(
            net=_net_number(pcb, net_name),
            net_name=net_name,
            layer=pour.layer,
            layers=[],
            uuid=kicad.gen_uuid(),
            name=f"{_MANAGED_POUR_PREFIX}{i}",
            hatch=_P.Hatch(mode=_P.E_zone_hatch_mode.EDGE, pitch=0.5),
            priority=pour.priority,
            connect_pads=_P.ConnectPads(mode=None, clearance=pour.clearance),
            min_thickness=0.25,
            filled_areas_thickness=False,
            fill=_P.ZoneFill(
                enable=_P.E_zone_fill_enable.YES,
                thermal_gap=0.5,
                thermal_bridge_width=0.5,
                island_removal_mode=0,
            ),
            polygon=_ring(pour.polygon),
        )
        kicad.insert(pcb, "zones", pcb.zones, zone)

    # --- F7 keepouts --------------------------------------------------------
    for i, ko in enumerate(board.keepouts):
        zone = _P.Zone(
            net=0,
            net_name="",
            layers=list(ko.layers) if len(ko.layers) > 1 else [],
            layer=ko.layers[0] if len(ko.layers) == 1 else None,
            uuid=kicad.gen_uuid(),
            name=f"{_MANAGED_KEEPOUT_PREFIX}{i}",
            hatch=_P.Hatch(mode=_P.E_zone_hatch_mode.EDGE, pitch=0.5),
            connect_pads=_P.ConnectPads(mode=None, clearance=0),
            min_thickness=0.25,
            filled_areas_thickness=False,
            # NO placement (the SEGFAULT footgun is structurally avoided).
            keepout=_P.ZoneKeepout(
                tracks=_keepout_flag(ko.tracks),
                vias=_keepout_flag(ko.vias),
                pads=_keepout_flag(ko.pads),
                copperpour=_keepout_flag(ko.copperpour),
                footprints=_keepout_flag(ko.footprints),
            ),
            fill=_P.ZoneFill(
                mode=None,
                thermal_gap=0.5,
                thermal_bridge_width=0.5,
                island_removal_mode=0,
            ),
            polygon=_ring(ko.polygon),
        )
        kicad.insert(pcb, "zones", pcb.zones, zone)

    # --- F8 silk ------------------------------------------------------------
    # de-dupe managed silk by exact (text, position, layer) so an unchanged
    # re-emit is idempotent (see the module-doc HONEST LIMIT for changed text).
    incoming = {
        (s.text, round(s.at[0], 6), round(s.at[1], 6), s.layer) for s in board.silk
    }
    kicad.filter(
        pcb, "gr_texts", pcb.gr_texts,
        lambda t: (
            t.text,
            round(t.at.x, 6),
            round(t.at.y, 6),
            t.layer.layer,
        )
        not in incoming,
    )
    for s in board.silk:
        text = _P.Text(
            text=s.text,
            at=_P.Xyr(x=s.at[0], y=s.at[1], r=s.rotation),
            layer=_P.TextLayer(layer=s.layer),
            uuid=kicad.gen_uuid(),
            effects=_P.Effects(
                font=_P.Font(size=_P.Wh(w=s.size, h=s.size), thickness=s.thickness)
            ),
        )
        kicad.insert(pcb, "gr_texts", pcb.gr_texts, text)

    return {
        "pours": len(board.pours),
        "keepouts": len(board.keepouts),
        "silk": len(board.silk),
        "outline_edges": outline_drawn,
        "stackup_layers": stackup_stamped,
    }
