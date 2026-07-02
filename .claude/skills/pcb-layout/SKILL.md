---
name: pcb-layout
description: "Authoritative skill for the text-first PCB layout layer: authoring the layout.yaml sidecar (rooms, placements, board outline/stackup, net classes, pours, keepouts, silk, route_stages), running the build→route→diagnose loop, reading diagnostics.json, and a finding→remedy playbook (forced waypoints, stage ordering, router tuning). Use when a board's PLACEMENT / ROUTING / BOARD RULES need authoring or a DRC / unrouted-net / router-misbehavior needs fixing. The PCB-level parallel to the `ato` (schematic) skill."
---

# PCB Layout (the `layout.yaml` sidecar)

This skill is for **layout**, the layer below the schematic. The `ato` skill owns
the circuit (`.ato`); this skill owns geometry, routing, and board rules
(`layout.yaml`). Reach for it when the user wants a board *placed / routed / DRC-clean*,
or when `ato route` / `ato diagnose` surfaced a failure to fix.

> **Layering — do not skip down.** The vendored router has its own skills under
> `vendor/KiCadRoutingTools/.claude/skills/` (`diagnose-routing-failures`,
> `plan-pcb-routing`, …). Those operate on **raw KiCad boards at the router-API
> level** and emit router retry commands. That is the *wrong layer* for a board
> built by atopile: fixes belong in `layout.yaml`, not in hand-run `route.py`.
> Use the router skills only to debug the router *itself* (§7), never to author a
> board.

---

## 1. Mental model (why the feedback lives outside the file)

```
   .ato  (circuit truth)   +   layout.yaml  (layout truth)
                    │  ato build         → stamps board, emits layout IR / plan / .kicad_pro
                    ▼
   layout/<target>/<target>.kicad_pcb            ← the AUTHORED board (open in pcbnew)
                    │  ato route          → drives the router over route_stages
                    ▼
   build/builds/<target>/<target>.route/<laststage>.kicad_pcb   ← the ROUTED board
                    │  ato diagnose       → route report + KiCad DRC, attributed to ato addresses
                    ▼
   build/builds/<target>/<target>.diagnostics.json   ← imperative feedback
                    └──────────  YOU edit layout.yaml, rebuild  ◄──────────┘
```

**The core discipline (BACKLOG / `layout_plan.py` docstring):** `layout.yaml` holds
only *declarative intent*. The imperative feedback — "this net didn't route, space
ran out, what now" — deliberately lives **outside** the file, in
`diagnostics.json`. Your job as the agent *is* that outer loop: read the JSON,
change the intent, rebuild. Do **not** try to encode retry/rip-up state into
`layout.yaml`.

**Everything keys off ato addresses.** The `<target>.layout_ir.json` `signal_nets`
map (`ato.address → /KiCadNetName`) is the bridge (§B). You reference **ato
addresses** in `route_stages.nets`, `pours.net`, `net_classes.nets` — never raw
KiCad net names. Read them first:

```bash
ato build
python3 -c "import json;d=json.load(open('build/builds/<target>/<target>.layout_ir.json'));print(list(d['signal_nets'].items())[:10])"
```

**Code is SSOT.** Every schema below is a pydantic model in
`src/faebryk/exporters/pcb/layout/layout_plan.py` with `extra='forbid'` — a typo'd
key is a loud `ValidationError`, never silent. When in doubt, read the model
docstring; this skill points at it, it does not replace it.

---

## 2. Authoring `layout.yaml` — schema by feature

`layout.yaml` sits next to `ato.yaml`; wire it into the build target:

```yaml
# ato.yaml
builds:
  top:
    entry: myproject.ato:Top
    layout_config: ./layout.yaml
```

Top-level `layout.yaml` keys: `rooms`, `board`, `route_stages`. All optional.

### 2.1 rooms — module regions → KiCad rule areas  (`Room`, layout_plan.py)
A room = a module's footprints, stamped as a **rule area** (room = footprint
`sheetname`; atopile builds/deletes no KiCad groups — §C3).
```yaml
rooms:
  - module: host                 # ato instance path resolving to a sub-module
    origin: [0, 0]               # optional; else auto
    size: [30, 20]               # optional
    rotation: 0.0
    # polygon: [[..],[..]]       # non-rectangular room (overrides origin/size)
```

### 2.2 placements — per-component pose  (`Placement`, layout_plan.py)
```yaml
placements:
  - component: host.u1           # ato address
    at: [5, 5]                   # relative to the room (unless absolute)
    rotation: 90
    side: F                      # F | B
    absolute: false              # true ⇒ board-absolute, skip room composition
```

### 2.3 board — outline, stackup, and board-authoring  (`Board`, layout_plan.py)
```yaml
board:
  outline:                       # drawn on Edge.Cuts
    origin: [0, 0]
    size: [60, 70]               # or: polygon: [[..],[..],..]
  stackup:                       # REQUIRED when any stage routes (layers come from here)
    layers:
      - {name: F.Cu,  type: copper, thickness: 0.035}
      - {name: d1,    type: dielectric, thickness: 0.2, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu,  type: copper, thickness: 0.035}
```
Invariants: a stage asking for `impedance` **requires** a stackup (hard-checked);
the router's layer list is derived from the stackup alone (single authority —
never set `config.layers` per stage, it is loud).

#### net_classes — F-drc-rules (§F5): make DRC judge *intent*  (`NetClass`)
Emitted into `<target>.kicad_pro`; KiCad DRC honors them (proven: a wide clearance
class turns 0 clearance violations into many).
```yaml
board:
  net_classes:
    - name: PWR
      clearance: 0.5
      track_width: 0.4
      via_diameter: 0.8
      via_drill: 0.4
      diff_pair_gap: 0.2         # optional
      diff_pair_width: 0.2       # optional
      nets: [top.pwr, top.gnd]   # ato addresses (resolved via bridge②)
```
Loud at parse: duplicate class name, or a net in two classes.

#### pours / keepouts / silk — §F6/F7/F8  (`Pour` / `Keepout` / `SilkText`)
```yaml
board:
  pours:                         # copper zone bound to a REAL net (net-0 pour is GC'd → loud)
    - net: top.gnd               # ato address (REQUIRED)
      layer: F.Cu
      polygon: [[5,5],[55,5],[55,65],[5,65]]
      clearance: 0.2
      priority: 0
  keepouts:                      # no net, no placement (SEGFAULT footgun structurally avoided)
    - polygon: [[20,20],[30,20],[30,30],[20,30]]
      layers: [F.Cu, B.Cu]
      tracks: true               # true = DISALLOWED. defaults: tracks/vias/copperpour off, pads/footprints on
      vias: true
      pads: false
  silk:
    - {text: "REV A", at: [25, 15], size: 1.0, layer: F.SilkS, rotation: 0.0, thickness: 0.15}
```
Idempotent across rebuilds (managed pours/keepouts by name prefix; silk by exact
text+pos+layer). HONEST LIMIT: a silk whose text/position you *change* orphans the
old copy — needs a manual clean.

### 2.4 route_stages — single & diff  (`RouteStage`, layout_plan.py)
Each stage is one router invocation over a set of nets, in a `mode`. **Stage order
is priority**: earlier stages' copper is an immovable obstacle for later ones (§4).
```yaml
route_stages:
  - name: power                  # names must be unique (loud otherwise)
    mode: single                 # single | diff
    nets: [top.pwr, top.gnd]     # ato addresses; block list (not flow — see §6)
    config:                      # per-stage router knobs (GridRouteOverride); typo = loud
      track_width: 0.4
      clearance: 0.2
  - name: pairs
    mode: diff
    nets: [top.usb.p, top.usb.n]
    config: { diff_pair_gap: 0.2, impedance: 90 }
  - name: signals
    mode: single
    nets: [top.a, top.b, top.c]
    corridor: [[10,10],[10,40],[30,40]]   # forced waypoint — see §5.3 (single only)
```

### 2.5 bundle — a coupled bus  (`BundleStage`; worked example: examples/sata_bundle/layout.yaml)
```yaml
route_stages:
  - type: bundle                 # NB: `type: bundle`, not `mode:`
    name: sata_link
    lanes:
      - diff: [host.tx.p.line, host.tx.n.line]
        gap: 0.2
        width: 0.2
        impedance: 100
    trunk:
      centerline:                # the bus path — this is how you STEER a bundle (no corridor)
        - {at: [40, 8],  spacing: 0.5}
        - {at: [22, 58], spacing: 0.3}
    breakouts:
      - {at: host}
      - {at: device}
    config: { track_width: 0.2, clearance: 0.15, impedance: 100 }
```

---

## 3. Running the loop

```bash
ato build                 # stamps board + rule areas + .kicad_pro + pours/keepouts/silk
ato route                 # router runs under SYSTEM python3 (rust ext); writes route_report.json
ato route --up-to power   # optional: route only through stage `power` (breakpoint) to inspect
ato diagnose              # aggregates route report + KiCad DRC → diagnostics.json
```
Artifacts under `build/builds/<target>/`: `*.layout_ir.json`, `*.layout_plan.json`,
`*.route_report.json`, `*.diagnostics.json`. Open boards in pcbnew:
authored = `layout/<target>/<target>.kicad_pcb`; routed =
`build/builds/<target>/<target>.route/<laststage>.kicad_pcb`.

---

## 4. Reading `diagnostics.json`

Shape (`build_diagnostics`, diagnostics.py):
```jsonc
{
  "findings": [ /* sorted, deterministic */ ],
  "summary":  { "route_failures": N, "route_failures_unaccounted": M, ... },
  "totals":   { "successful": .., "failed": .., "board_vias": .., "board_segments": .. }
}
```
`route_failures_unaccounted > 0` means the route report claimed more failures than
were turned into findings — a residual **honesty guard**, never a silent drop.

**Each finding** (`make_finding`): `rule_id`, `severity` (error|warning|info),
`confidence` (deterministic|heuristic), `ato_path`, `room`, `stage`, `reason`,
`nets`, `blocking_nets`, `failed_endpoints`, `is_new`, `suggestions`.

- `rule_id` is either **`ROUTE-FAIL`** (a net the router could not complete) or
  **`DRC-<type>`** (`DRC-clearance`, `DRC-track_width`, `DRC-unconnected_items`,
  `DRC-shorting_items`, `DRC-tracks_crossing`, `DRC-silk_overlap`,
  `DRC-solder_mask_bridge`, `DRC-lib_footprint_issues`, `DRC-invalid_outline`, …).
- `is_new: true` ⇒ this DRC violation appeared *because of routing* (diffed against
  the pre-route baseline). `is_new: false` ⇒ it pre-existed (a placement/footprint
  issue, usually **not** a routing problem).
- `blocking_nets` (on ROUTE-FAIL) = the nets whose copper blocked this one (the
  router's obstacle model). `failed_endpoints` = the pads it could not join.
- **`suggestions` is currently always `[]`** — the builder does not yet populate it
  (see §8). Until it does, the remedy is *your* job: use §5.

**The obstacle model you must internalize:** the router lays copper stage by stage
and **never rips up a prior stage** (cross-stage copper is a hard obstacle;
priority is expressed by stage *order*, BACKLOG fact 16). So a `ROUTE-FAIL` with
`blocking_nets: [top.pwr]` means *`top.pwr`'s copper (routed earlier) walled this
net off* — the fix is almost always about **order, space, or a waypoint**, not
about the failing net's own knobs.

---

## 5. Finding → remedy playbook

Work one finding at a time; rebuild and re-diagnose to confirm it cleared before
moving on. Prefer the *least invasive* fix that addresses the mechanism.

### 5.1 `ROUTE-FAIL` / `DRC-unconnected_items` (a net didn't route)
`DRC-unconnected_items` is the same failure surfaced by KiCad; treat both together.
Diagnose from `blocking_nets` / `reason` / `room`, then, in rough order of preference:

1. **Reorder stages** so the failing net routes *earlier* (before the copper that
   blocked it). Move its stage up, or split it into its own earlier stage. This is
   the single most effective fix because of the obstacle model (§4).
2. **Add a forced waypoint** (`corridor`) to steer it around the blockage — §5.3.
3. **Make space**: widen `board.outline`, move the blocking parts via
   `placements` / `rooms`, or add breathing room between rooms.
4. **Relax the failing stage's geometry**: lower `config.track_width` / `clearance`
   for *that* stage so it fits through a tight channel.
5. **Relieve the blocker**: if a wide early net (a plane-like `power_nets` set) over-claimed
   space, narrow it or convert it to a `pour` instead of routed copper.
6. If it is a coupled bus, re-route the **bundle trunk** (`trunk.centerline`) along a
   clearer path, and/or fix `breakouts.order`.

### 5.2 `DRC-clearance` / `DRC-track_width` / `DRC-shorting_items` / `DRC-tracks_crossing`
Copper too close / too thin / touching. Check `is_new` first:
- `is_new: true` (routing caused it): the routed geometry violates a rule.
  - **clearance/shorting/crossing**: author or widen a **`net_class`** clearance for
    the involved nets so the router respects it next run; or add a **`keepout`** to
    forbid routing in the pinch region; or reorder/space (§5.1) so the two nets are
    not forced together.
  - **track_width**: set a consistent width — a `net_class.track_width` *and* the
    stage `config.track_width` — so the routed width satisfies the rule.
- `is_new: false` (pre-existed): usually a **placement/footprint** issue, not a
  routing one — fix the placement (`placements`/`rooms`) or the part in `.ato`,
  not the router.

### 5.3 Forced waypoint (the `corridor` knob) — single-mode only
When the router picks a bad path or can't find one, hand it a guide polyline. The
build draws it on `User.1` and flips the router's native guide reader (Tier0
corridor-as-data — zero router change; `RouteStage.corridor`).
```yaml
- name: signals
  mode: single
  nets: [top.clk]
  corridor: [[10,10],[10,40],[30,40]]   # ≥ 2 points; the trace follows this
```
Constraints: **single mode only** (the diff entry has no guide knob — loud if you
set `corridor` on a diff stage). To steer a **diff pair**, put it in its own early
stage and clear its channel (§5.1). To steer a **bundle**, shape `trunk.centerline`.

### 5.4 Router misbehaves (bad geometry, not an outright failure)
If the net *routes* but the geometry is ugly/wrong, tune the stage `config`
(GridRouteOverride) — all optional, `extra='forbid'`:

| knob | modes | use |
|---|---|---|
| `track_width`, `clearance` | both | fit tighter channels / enforce spacing |
| `via_size`, `via_drill` | both | via geometry |
| `impedance` | both | controlled-Z (requires stackup) |
| `keepout_enabled`, `keepout_layer` | both | route-avoid a layer region |
| `length_match_groups`, `length_match_tolerance` | both | equal-length groups |
| `guide_corridor_*` | single | (prefer the `corridor:` polyline over raw knobs) |
| `power_nets`, `power_nets_widths` | single | wide power routing |
| `diff_pair_gap`, `diff_pair_intra_match`, `fix_polarity`, `gnd_via_enabled` | diff | diff-pair geometry |

A knob valid only for the other mode is loud at parse (wrong-mode guard). If a
construct is genuinely unsupported (teardrops, length-tuned serpentine, some via
padstack sub-keys), the build emits a **loud S5a warning** rather than silently
mis-emitting — treat that as "not authorable yet" (BACKLOG §P1+), not a bug to
route around.

### 5.5 What `layout.yaml` CANNOT fix (route these elsewhere)
Be honest with the user instead of thrashing the sidecar:
- **`DRC-lib_footprint_issues`, most `DRC-silk_overlap`, `DRC-solder_mask_bridge`**
  → footprint/library problems. Fix the footprint or the part selection in `.ato`.
- **`DRC-invalid_outline`** → fix `board.outline` (self-intersecting / open).
- **`is_new: false` DRC** → generally placement/footprint, not routing.
These are not sidecar failures; surface them as schematic/footprint work.

---

## 6. Gotchas (all empirically bitten — see BACKLOG)

- **ato addresses with `[N]`** (e.g. `sub_chains[0].r_chain[0]`): a YAML *flow*
  sequence `nets: [a[0]]` is a **ParserError**. Use a **block list**:
  ```yaml
  nets:
    - sub_chains[0].r_chain[0]
  ```
  (or quote the flow item). Reserved-word/number-like tokens (`on`, `yes`, `0x10`)
  are coerced by YAML but rejected loudly by pydantic's str fields — still, prefer
  block lists.
- **A pour must bind a real net** — a net-0 pour is GC'd by KiCad, so an
  unresolvable `pours.net` is loud, never a dropped pour.
- **Never author `ZonePlacement source_type/source`** — those SIGSEGV kicad-cli.
  The sidecar structurally avoids them (keepouts/pours carry no placement); you
  cannot express them from `layout.yaml`, which is intentional.
- **uuids are opaque** — two builds may differ byte-wise but be semantically equal
  (position + net connectivity). Never assert byte-equality on generated boards.
- **The router needs system `python3`** (numpy/scipy/shapely + rust ext); the
  runner selects it automatically. Don't force the venv interpreter.

---

## 7. Debugging the router itself (rare, lower layer)

If you suspect the *router* is wrong (not your intent), the vendored router skills
(`vendor/KiCadRoutingTools/.claude/skills/`) and docs
(`vendor/KiCadRoutingTools/docs/`) operate at the raw-board / API level. Capture a
run with `2>&1 | tee` and use `diagnose-routing-failures` there. **Then bring the
fix back up to `layout.yaml`** (a stage config, a corridor, an order change) — do
not leave the board routed by a hand-run `route.py`, or the next `ato build` will
overwrite it.

---

## 8. Known limits (current, honest)

- **`suggestions` in `diagnostics.json` is always empty** — the builder
  (`diagnostics.py`) does not yet populate it, so machine-readable remedies don't
  exist; §5 is the substitute. *Recommended follow-up:* populate `suggestions` in
  the three `make_finding` call sites (per finding class) behind the existing
  contract tests, so the loop becomes machine-actionable.
- **Blocking cause is by net-name, not per-cell** — `blocking_nets` names the nets,
  not the exact contended cells (that lives only inside the router loop).
- **uuid→address correlation is footprint/pad only** — track/via/zone DRC items are
  matched by net name + coordinate, best-effort.
- **DRC `nets` are extracted from KiCad's description text** (no structured field) —
  best-effort.
- **Silk idempotency holds only for unchanged text** (§2.3).

---

## SSOT pointers (verify claims here, don't trust prose)
- `src/faebryk/exporters/pcb/layout/layout_plan.py` — all `layout.yaml` models +
  invariants (`Room`, `Placement`, `Board`, `NetClass`, `Pour`, `Keepout`,
  `SilkText`, `RouteStage`, `BundleStage`, `GridRouteOverride`).
- `src/faebryk/exporters/pcb/layout/layout_plan_runner.py` — how stages dispatch to
  the router (`build_invocations`, `run_route_stages`).
- `src/faebryk/exporters/pcb/layout/diagnostics.py` — `diagnostics.json` schema +
  `make_finding` / `build_diagnostics`.
- `src/atopile/cli/{route,diagnose}.py` — the `ato route` / `ato diagnose` shells.
- `examples/sata_bundle/layout.yaml` — worked bundle + board section.
- `CLAUDE.md` §"layout sidecar modules" — the developer module map.
- `remote/RUNBOOK.md` — a headed, human demo of the whole loop.
