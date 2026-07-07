---
name: pcb-layout
description: "Authoritative skill for the text-first PCB layout layer: authoring the layout.yaml sidecar (rules header, rooms, placements, board outline/stackup, net classes, pours, keepouts, silk, route_stages), running the build→route→snapshot→diagnose loop (ato snapshot = headless board PNG + DRC, the eyes), impedance geometry from the vendored 2D field solver, reading diagnostics.json, and a finding→remedy playbook (forced waypoints, stage ordering, pad-facing fanout, router tuning). Use when a board's PLACEMENT / ROUTING / BOARD RULES need authoring or a DRC / unrouted-net / router-misbehavior needs fixing. The PCB-level parallel to the `ato` (schematic) skill."
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
   .ato  (circuit truth)   +   layout.yaml  (layout truth: rules header + geometry intent)
                    │  ato build         → stamps board (outline, rule areas, placements),
                    │                      emits layout IR / plan / .kicad_pro / .kicad_dru
                    ▼
   layout/<target>/<target>.kicad_pcb            ← the AUTHORED board (open in pcbnew)
                    │  ato route          → drives the router over route_stages
                    ▼
   build/builds/<target>/<target>.route/<laststage>.kicad_pcb   ← the ROUTED board
                    │  ato snapshot       → headless PNG + DRC, violations marked (the EYES)
                    │  ato diagnose       → route report + KiCad DRC, attributed to ato addresses
                    ▼
   *.snapshot.*.png / *.drc.json / *.diagnostics.json   ← imperative feedback
                    └──────────  YOU edit layout.yaml, rebuild  ◄──────────┘
```

**Acceptance = zero DRC errors that you have LOOKED at, never "routed N/N, 0
failed".** A route report can be fully green while every pair on the board is a
short (empirically: the pre-rules SATA board routed 4/4 with the pair's copper
edges touching). Do not claim a board is done without an `ato snapshot` run whose
error count is zero and whose PNG you have actually viewed.

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

Top-level `layout.yaml` keys: `rules`, `rooms`, `placements`, `board`,
`route_stages`. All optional — EXCEPT `rules`, which is **required the moment
`route_stages` is non-empty** (parse-time loud: "the router cannot start with no
rules").

### 2.0 rules — the board-wide design-rules header  (`DesignRules`, layout_plan.py)
Write this FIRST, before any stage. It plays three roles at once:
1. **Defaults** — a stage/lane that omits a geometry knob inherits it
   (`track_width`, `clearance`, `diff_pair_width`, `diff_pair_gap`), so every
   stage reaches the router with concrete geometry.
2. **Minimums, enforced at parse** — an explicit value below the board minimum
   (stage clearance < `clearance`; intra-pair gap < `clearance` — P/N are
   different nets, that is a short *by construction*; trunk spacing <
   `inter_pair_clearance`) is a `LayoutPlanError` before any copper exists.
3. **DRC authority** — the build writes the same numbers into the
   `.kicad_pro` Default net class and a generated `.kicad_dru`
   (clearance floor, `courtyard_clearance` = component spacing,
   `diff_pair_uncoupled` max, and the board-wide intra-pair skew rule
   below). kicad-cli DRC honors all of them — empirically including
   uncoupled-length and skew on atopile's net names.
```yaml
rules:
  clearance: 5mil            # copper-copper minimum (the short-circuit rule)
  track_width: 0.15          # single-ended default (mm when unitless)
  diff_pair_width: 0.2       # pair geometry — from the field solver, see §2.6
  diff_pair_gap: 0.15        # intra-pair copper EDGE gap (not center-to-center)
  inter_pair_clearance: 20mil
  component_spacing: 20mil   # courtyard-to-courtyard (DRC courtyard_clearance)
  uncoupled_max_length: 200mil   # see §5.6 for how to pick this honestly
  intra_pair_skew_max: 20mil # P vs N length delta budget, EVERY _P/_N pair
```
`intra_pair_skew_max` becomes one `.kicad_dru` rule conditioned on
`A.inDiffPair('*')` with `(constraint skew (max ..) (within_diff_pairs))` —
kicad-cli flags a breach as `skew_out_of_range` (per-class matched-length
rules live on `net_classes`, §2.3).
Lengths accept mm numbers or `"<n>mil"` / `"<n>mm"` strings. `extra='forbid'`.
**Change-propagation rule:** `ato route` re-parses `layout.yaml`, so a rules edit
reaches the *copper* with route alone — but the `.kicad_pro`/`.kicad_dru` DRC
files are written by `ato build`. After any rules edit run
`ato build && ato route && ato snapshot`, or DRC judges the new copper against
the old numbers (the exact silent mismatch this header exists to kill).

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
    # source: component_class    # default sheetname; see below
```
`source: component_class` (D5) keys the rule area to a KiCad component class
instead of the sheetname. Membership rides two channels with different
authority: the build stamps `(component_classes (class "<module>"))` statically
on every member footprint — that is the **headless authority** (kicad-cli DRC
resolves it, so `.kicad_dru` rules conditioned on
`A.hasComponentClass('<module>')` bite) — and mirrors one SHEET_NAME assignment
into `.kicad_pro` `component_class_settings` for the GUI (kicad-cli never runs
the GUI's class synchronization, so the project-file channel alone is inert
headlessly — empirically pinned by
`test_rule_area_contract.py::test_kicad_drc_enforces_component_class_headlessly`).
Both channels GC on rename/revert (ownership = structural fingerprint /
address-ancestor). Class name == `room.module`; there is no second name field.

### 2.2 placements — per-component pose  (`Placement`, layout_plan.py + placement.py)
```yaml
placements:
  - component: host.u1           # ato address
    at: [5, 5]                   # relative to the room (unless absolute)
    rotation: 90
    side: F                      # F | B
    absolute: false              # true ⇒ board-absolute, skip room composition
```
**Text placement is the pose AUTHORITY**, and that has a copper consequence: a
placement moving any footprint of a room **invalidates that room's pulled
intra-room copper** (`apply_placements` → `LayoutSync.clean_room_copper`). Reuse
tracks are anchored to the OLD poses — after a move they can only dangle or
short, and worse, they make their net *unroutable* (the router must reach ALL of
a net's copper; an orphaned island reads as `no rippable blockers`). A placed
room's copper is derived-only: the route stages re-lay it.

**Placement conventions that make fanouts route clean (all empirically pinned on
`examples/sata_bundle` — violating any one produced shorts/crossings):**
- **Pad 1 (the connected pad) must FACE the routing target.** The breakout fanout
  is a straight segment trunk-end → pad-1 center; if pad 1 is on the far side,
  the segment plows through pad 2 (`shorting_items` + `solder_mask_bridge` on the
  SAME footprint's two pads is the signature). For a 2-pad 0402: rotation 90 vs
  270 flips which side pad 1 faces — check with a snapshot zoom, don't guess.
- **Match the pad row order to the trunk lane order.** Bundle lane offsets:
  `+offset == WEST of southbound travel` (a north→south trunk reads
  right-to-left in lane order). Pads in a different left-right order X-cross the
  fanout (`tracks_crossing` + clearance at the row). Both ends of a straight
  trunk read the SAME left-to-right order.
- **Pitch**: components in a breakout row need
  pitch ≥ courtyard width + `component_spacing`; tighter pitch also shortens the
  uncoupled fanout (§5.6). 1.6mm works for 0402 at 20mil spacing.

### 2.3 board — outline, stackup, and board-authoring  (`Board`, layout_plan.py)
```yaml
board:
  outline:                       # STAMPED on Edge.Cuts as a closed loop (board_features.py)
    origin: [0, 0]
    size: [60, 70]               # or: polygon: [[..],[..],..]
  stackup:                       # REQUIRED when any stage routes (layers come from here)
    layers:
      - {name: F.Cu,  type: copper, thickness: 0.035}
      - {name: d1,    type: dielectric, thickness: 0.2, material: FR4, epsilon_r: 4.5}
      - {name: B.Cu,  type: copper, thickness: 0.035}
```
A declared `outline` is the Edge.Cuts **authority**: the build replaces existing
edge lines (never accretes); with no `outline` key the board's own edges are left
untouched (the reuse case). Always declare one on an authored board — a missing
outline is `DRC-invalid_outline` AND an unbounded router space.
Invariants: a stage asking for `impedance` **requires** a stackup (hard-checked);
the router's layer list is derived from the stackup alone (single authority —
never set `config.layers` per stage, it is loud). A 4-layer board is just 4
copper entries with dielectrics between (see `examples/sata_bundle/layout.yaml`).

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
      # matched-length rules (F1) — these CANNOT live in .kicad_pro; they are
      # emitted as .kicad_dru rules scoped (condition "A.hasNetclass('PWR')"):
      skew_max: 1.0              # group skew: each class net vs the group's LONGEST
      intra_pair_skew_max: 20mil # within each _P/_N pair only (within_diff_pairs)
      length_min: 40             # absolute per-net length window — emit only the
      length_max: 60             # bounds you set (mm numbers or mil/mm strings)
      nets: [top.pwr, top.gnd]   # ato addresses (resolved via bridge②)
```
kicad-cli fires `skew_out_of_range` / `length_out_of_range` on breach; KiCad
measures track+arc+via length and pad-to-die (live pins:
`test_matched_length_rules_contract.py`). Loud at parse: duplicate class name, a
net in two classes, a matched-length field on a class with an EMPTY `nets` list
(it could never bite), or a quote in the class name (unrepresentable in the dru
condition string).

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
        impedance: 100           # width/gap omitted ⇒ inherited from rules (§2.0)
    trunk:
      centerline:                # the bus path — this is how you STEER a bundle (no corridor)
        - {at: [30, 14], spacing: 0.6}
        - {at: [30, 56], spacing: 0.508}
    breakouts:
      - {at: host}
      - {at: device}
    config: { impedance: 100 }
```
Geometry semantics (bundle_geometry.py, pinned): a diff lane's `gap` is the
copper **EDGE-to-EDGE** gap (center pitch = gap + width; the pair envelope =
2·width + gap). `spacing` is the edge gap between lane slots, checked against
`rules.inter_pair_clearance`. *(Historical bug, fixed: gap was once applied
center-to-center, so gap == width meant the pair's edges touched — if you see a
pair rendered as one fat trace, you are on a stale build of this code.)*

### 2.6 impedance geometry — the vendored 2D field solver  (vendor/2d_fields)
`rules.diff_pair_width/gap` for a controlled-impedance pair come from the
headless field solver, not from guessing:
```bash
# stackup facts: h = dielectric to the reference plane (mm), er, t = copper (mm)
node vendor/2d_fields/cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.20 --s 0.15 --tol 0.002
# → JSON with Z_odd / Z_even / Z_diff; comma lists in --w/--s do cartesian sweeps
```
Workflow: sweep `--w/--s` to the target Z_diff (100Ω SATA on 0.176mm 7628 er 4.6
⇒ W=0.20 S=0.15, Z_diff 99.44Ω), write the pair into `rules:`, then
`ato build && ato route && ato snapshot` (§2.0 change-propagation rule).
Reference results + a 50Ω sanity check live in `vendor/2d_fields/RESULTS.md`.

---

## 3. Running the loop

```bash
ato build                 # stamps board (outline, placements, rule areas) + .kicad_pro/.kicad_dru
ato route                 # router runs under SYSTEM python3 (rust ext); writes route_report.json
ato route --up-to power   # optional: route only through stage `power` (breakpoint) to inspect
ato snapshot              # headless PNG + DRC of the routed board, violations marked  ← the EYES
ato diagnose              # aggregates route report + KiCad DRC → diagnostics.json
```
Artifacts under `build/builds/<target>/`: `*.layout_ir.json`, `*.layout_plan.json`,
`*.route_report.json`, `*.snapshot.*.png` + `*.snapshot.*.drc.json`,
`*.diagnostics.json`. Boards: authored = `layout/<target>/<target>.kicad_pcb`;
routed = `build/builds/<target>/<target>.route/<laststage>.kicad_pcb`.

### 3.1 `ato snapshot` — look before you conclude  (cli/snapshot.py)
No GUI, no user input: renders the board (kicad-cli SVG → rsvg-convert → PNG,
content-cropped) and runs `kicad-cli pcb drc --severity-all`, drawing a
**numbered yellow circle at every violation** — the numbers match the printed
finding list, so a mark on the image is findable in the text and vice versa.
```bash
ato snapshot                    # the routed board (falls back to built if never routed)
ato snapshot --built            # the pre-route board (isolate placement vs routing issues)
ato snapshot --board any.kicad_pcb   # raw mode, any board file
ato snapshot --ppmm 40          # higher resolution
```
It stages the built board's `.kicad_pro` / `.kicad_dru` / `fp-lib-table` next to
the routed board first — kicad-cli DRC only honors rule files **sitting beside
the board**; without staging it silently judges KiCad defaults.

**The diagnosis discipline that saves time: never theorize past one hypothesis —
zoom instead.** Violation positions are absolute board mm; render high-res and
crop the region before proposing a second fix:
```bash
kicad-cli pcb export svg --mode-single --page-size-mode 1 --exclude-drawing-sheet \
  --layers F.Cu,Edge.Cuts -o /tmp/z.svg <board>.kicad_pcb
rsvg-convert --dpi-x 2032 --dpi-y 2032 --background-color '#001023' -o /tmp/z.png /tmp/z.svg
python3 -c "
from PIL import Image; Image.MAX_IMAGE_PIXELS=None
im=Image.open('/tmp/z.png'); ppmm=im.size[0]/297.0022   # page-size-mode 1 = A4, origin (0,0)
x0,y0,x1,y1 = 26,10,34,20                               # ← the violation's neighborhood, mm
im.crop((int(x0*ppmm),int(y0*ppmm),int(x1*ppmm),int(y1*ppmm))).save('/tmp/zoom.png')"
```
(Empirically this ended a wrong-theory loop in one look: the "shorted pads"
turned out to be the fanout entering from the wrong side of the footprint —
invisible in the DRC text, obvious in the crop.) Do NOT rasterize KiCad SVGs
with ImageMagick: its builtin renderer silently drops track segments.

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
Copper too close / too thin / touching. Two breakout-row signatures first — both
diagnosed by zooming the row (§3.1), both fixed in `placements`, NOT in router
knobs:
- **shorting_items / solder_mask_bridge naming the SAME footprint's two pads**
  ⇒ pad 1 faces away from the routing target and the fanout segment crosses
  pad 2 to reach it. Flip the placement `rotation` by 180° (§2.2).
- **tracks_crossing + clearance clustered at a breakout row** ⇒ the pad
  left-right order doesn't match the trunk lane order; the fanout X-crosses.
  Reorder the row's placements (§2.2 lane-order convention).

Otherwise check `is_new` first:
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

A knob valid only for the other mode is loud at parse (wrong-mode guard).
GUI-authored constructs — teardrops (pad/via/zone), via & pad padstacks +
hole treatments, and length-tuned serpentine `generated` patterns — are in the
**fidelity set** (2026-07-03): they survive managed rewrites losslessly, room
clean/pull keeps tuning patterns coherent (all-members-or-loud-drop), and
`semantic_view` sees them. They are still not *authorable* from `layout.yaml`
(author length targets via `length_match_groups`; fine-tune in the GUI — the
edit survives rebuilds). A genuinely unknown key still emits the **loud S5a
warning** rather than silently mis-emitting.

### 5.5 What `layout.yaml` CANNOT fix (route these elsewhere)
Be honest with the user instead of thrashing the sidecar:
- **`DRC-lib_footprint_issues`, most `DRC-silk_overlap`** → footprint/library
  problems (0402 refdes silk is bigger than the part — warning-class noise).
  Fix the footprint or the part selection in `.ato`.
  (`solder_mask_bridge` naming one footprint's two pads is the §5.2 fanout
  signature — that one IS a placement fix.)
- **`is_new: false` DRC** → generally placement/footprint, not routing.
These are not sidecar failures; surface them as schematic/footprint work.
(`DRC-invalid_outline` used to be on this list — it is now authorable: declare
`board.outline`, §2.3.)

### 5.6 `diff_pair_uncoupled_length_too_long` (the rules-header uncoupled budget)
The dru rule fires per pair; "actual" in the description is the TOTAL uncoupled
length (both breakout ends). Remedies, in order:
1. **Shorten the fanout**: move the trunk's end vertices closer to the pad rows
   (leave ≥ ~1mm to the courtyards) and/or tighten the breakout pitch (§2.2).
2. **Set an honest budget**: the geometric floor for a discrete breakout is
   roughly `hypot(row-to-trunk distance, max lateral pad-to-lane offset)` per
   end — for 0402 rows at 1.6mm pitch that is ~2.3mm/end, so **50mil is
   unattainable**; `200mil` is a met-with-margin budget for that shape. Pick the
   tightest value your geometry actually meets — the rule must keep biting on
   regressions, so do not just crank it up until green.

### 5.7 Relic / dead copper (`copper_edge_clearance` off-board + `track_dangling` + `no rippable blockers` together)
That trio = orphaned copper from a stale build: reuse-pulled tracks whose
footprints later moved (they dangle at the OLD poses, often off-board, and make
their nets unroutable — the router must reach ALL of a net's copper). Current
code prevents new occurrences (placements invalidate the placed room's copper;
unmappable-net pulls are dropped loudly — placement.py / layout_sync.py). On a
board built before that: the authored `.kicad_pcb` is a DERIVED artifact —
delete `layout/<target>/<target>.kicad_pcb` (never `layout/<sub>/` reuse
sources), rebuild, reroute.

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
  invariants (`DesignRules` incl. the rules gate + defaults fill, `Room`,
  `Placement`, `Board`, `NetClass`, `Pour`, `Keepout`, `SilkText`, `RouteStage`,
  `BundleStage`, `GridRouteOverride`).
- `src/faebryk/exporters/pcb/layout/bundle_geometry.py` — the pinned cross-section
  convention (edge gap, pair envelope); `test_bundle_contract.py` oracles.
- `src/faebryk/exporters/pcb/layout/board_rules.py` — rules → `.kicad_pro` Default
  class + `generate_dru_rules` (`.kicad_dru`, incl. the F1 matched-length rules)
  + D5 component-class membership (static footprint stamp = headless authority,
  `.kicad_pro` mirror); `test_design_rules_contract.py`,
  `test_rule_area_contract.py` (D5.*), `test_board_rules_contract.py`,
  `test_matched_length_rules_contract.py`.
- GUI-construct fidelity oracles — `src/faebryk/libs/kicad/semantic_view.py` (v3:
  teardrops/padstacks/treatments/generateds/placement-source/pts chains) +
  `test/libs/kicad/test_{padstack,generated}_dialect.py`, `test_pts_interleave.py`,
  `test_construct_corruption.py`, `test/layout_server/test_gui_edit_roundtrip.py`.
- `src/faebryk/exporters/pcb/layout/placement.py` — pose authority + placed-room
  copper invalidation; `test_placement_apply_contract.py`.
- `src/atopile/cli/snapshot.py` — the headless eyes (render + DRC + marks);
  `test/cli/test_snapshot_contract.py`.
- `vendor/2d_fields/` — the field solver (`cli.js`, `RESULTS.md`, `VENDOR.md`).
- `src/faebryk/exporters/pcb/layout/layout_plan_runner.py` — how stages dispatch to
  the router (`build_invocations`, `run_route_stages`).
- `src/faebryk/exporters/pcb/layout/diagnostics.py` — `diagnostics.json` schema +
  `make_finding` / `build_diagnostics`.
- `src/atopile/cli/{route,diagnose}.py` — the `ato route` / `ato diagnose` shells.
- `examples/sata_bundle/layout.yaml` — worked bundle + board section.
- `CLAUDE.md` §"layout sidecar modules" — the developer module map.
- `remote/RUNBOOK.md` — a headed, human demo of the whole loop.
