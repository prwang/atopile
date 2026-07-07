# BACKLOG — Text-first layout workflow (deterministic text-first layout layer)

## Background

On top of atopile + KiCad 10 + KiCadRoutingTools, build a deterministic PCB layout-and-routing
workflow with text as the single source of truth: `.ato` = circuit source of truth, `layout.yaml` =
layout-intent source of truth, the KiCad project is a derived artifact; the tooling emits structured
JSON diagnostics for the **PCB Layout SKILL** to iterate on. Architecture overview see
`CLAUDE.md` "Architecture overview"; the key facts are all self-contained below, evidence = the KiCad
source / code file:line annotated in place on each item (the original feasibility-study report
KicadDecisions.md was retired on 2026-06-19, its live content folded into these two places).

**Discipline for this document (no entropy increase allowed)**: keep only ① settled decisions ②
currently-valid facts that must be read before changing code ③ unfinished tasks. **Completed features
are documented in code per atopile convention** (module docstring / same-named test / schema = SSOT),
BACKLOG keeps only "conclusion + code pointer", never restating implementation details or debugging narrative.

## Settled decisions

- Do not fork KiCad 10 (the required objects have been empirically verified to be externally generatable with a full round-trip).
- fork baseline = this repo's HEAD; also fork KiCadRoutingTools (§E).
- Python 3.14 dev environment = **`uv sync`** (`ziglang==0.15.1` provided by pip build dependency). Clone must
  `git fetch --tags` otherwise setuptools-scm produces a non-SemVer version number and the `ato` CLI crashes at startup.
- **room source = footprint `sheetname` [changed by C3, old "= KiCad named group" is void]**: group has an unpatchable
  ownership defect (see §C3); atopile creates zero groups. The component_class source coexists since D5 (opt-in `Room.source`, class name == sheetname == room.module; see the D5 side-task entry).
- **v10-only route (2026-06-13 decided by the user, Option B, upgrade-on-write)**: this branch's target dialect = v10;
  **forward-compatible v9 write-out is not done** (v9 is read-only, reading it in upgrades it to v10 at write-out). Corollaries: (i) no "write v9" code
  path; (ii) net name is the unique key within the file, name drift = geometry-ownership drift; (iii) after S7 the "one-way gate / read-only don't-save" discipline
  **is lifted**——KiCad 10 GUI save no longer corrupts managed boards.

## Priority overview

| Stage | Content | Status |
|---|---|---|
| P0 | Determinism and foundational fixes (§A) | ✅ |
| P0.1 | v10 migration test scaffold (T0–T9) | ✅ |
| P0.2 | v10 dialect migration proper (S0–S6 + D1 + BUG-1/2) | 🔶 only S7 flag-day acceptance remaining |
| B | layout_ir (text↔geometry sole interface) | ✅ |
| C | room geometry (forced via / room copy) | ✅ |
| **C3** | **group→sheetname migration (room de-group-ification)** | ✅ 2026-06-15 (see §C3 code pointers) |
| **D** | **layout.yaml loading + placement rule area (D1–D4)** | ✅ 2026-06-16 (see §D code pointers); D5 component_class ✅ 2026-07-03 (side-task entry) |
| **D-Tier2** | **bundle bus-transport schema + geometry (bucket ①)** | ✅ bucket ① 2026-06-20 (model+geometry) + D-side build integration 2026-06-25 (`generate_layout_plan` calls `bundle_artifact`, e2e `examples/sata_bundle`); bucket ② `batch_route_bundle` contract strict-xfail, implementation moved to §E E-Tier2 (decided by the user) |
| **D-Tier3** | **self-contained: placement (room-relative coordinates) + board outline + full stackup** | 🟢 bucket ① + bucket ② TS-AUTH-A + placements→transformer landed (2026-06-26 / 2026-06-28): placement schema+`apply_placements` build consumption / `Room.polygon`+rotation+layers / `board.outline`+full `stackup` / impedance→stackup hard dependency / **single layer-count authority, board side** `config` derived from `stackup_layers` (kills 2-layer hardcode); contracts `test_placement_contract.py`+`test_placement_apply_contract.py`+`test_board_section_contract.py`. **TS-AUTH-B (router layer list) ✅ 2026-06-28 (E1 passes `layers` from `stackup_layers`)**; remaining **bucket ③=§E DoD** (pure-text e2e); net-class/pour/keepout/silk go to §F |
| **Incremental execution** | **route_stages `--up-to` breakpoint + stage name unique** | ✅ 2026-06-28: stage name uniqueness + `--up-to` (name/1-based index, out-of-range → loud, writes partial board + `route_report.json`) lands in the E1 runner |
| **Tier0** | **corridor-as-data (pure-schema side branch, zero router changes)** | ✅ 2026-06-28 (`corridor.py` / `RouteStage.corridor`) |
| E–F | routing fork + diagnostics closed loop——**chapter letter = execution order** | ✅ **E1 ✅ 2026-06-28** (runner+`route_report.json`+`--up-to`+TS-AUTH-B) + **E-Tier2 bundle ✅ 2026-06-29** (`batch_route_bundle` parallel bus+direct-connect fanout+e2e; E2 rip-up knob cancelled per the user's decision) + **§F ✅ 2026-06-30** (F1–F8: `ato route`/`ato diagnose`→`diagnostics.json`+`JSON_DIAG` cause+F3 reverse-resolve+net-class/pour/keepout/silk textualization; build→route→diagnose e2e closed loop green); remaining E3 regression bench / §E DoD bucket ③ self-contained board build |
| G | Won't do / deferred | — |

### Dependencies & critical path (derived from v10 data model; **chapter letter = execution order**)

The three hard constraints of v10 force the ordering: ① no top-level net table + random FBRK uuid → **sole stable key = ato address**;
② **net name = net's unique key**, name drift = geometry-ownership drift; ③ guide/copper-layer geometry hangs off net by layer (fact 9).

```
A Determinism foundation (✅)
B layout_ir (✅ cornerstone) ── addr↔uuid↔net-name bridge table, all downstream depends
├─► C room geometry (✅ forced via / room copy)
└─► C3 group→sheetname migration (✅ 2026-06-15) room = footprint sheetname, atopile does not create/delete groups
                       │
   C3 ─► D layout.yaml + rule area (✅ 2026-06-16; room via (placement (sheetname)))
                       │
   C1 + D(plan+rule area) ─► E routing fork (E1 plan_runner→E2 locking) ─► F diagnostics closed loop
E3 regression bench (built first, parallel throughout = §E's S0 scaffold; minimal slice [✅ pulled forward to §C] as router-oracle)

   D ─► D-Tier2 contract freeze (bundle schema + batch_route_bundle oracle, xfail first)
            │  (consumer-oracle: D's bundle schema is pinned on a router entry that does not yet exist,
            │    so D and E must co-freeze the same contract——cannot do E first and then force a schema refactor)
            └─► E-Tier2 implements this contract (batch_route_bundle + breakout fanout; cross-stage locking is a hard obstacle the router gives for free,
                 see "key facts" 16, E2 only exposes the in-stage rip-up knob, creates no new locks)
   D ─► D-Tier3 bucket ① + TS-AUTH-A (schema + pure functions + rule_area geometry + config board table←stackup, ✅)
            └─► remaining downstream (ratchet kept in D, contract-first): TS-AUTH-B → §E1 (router layers←stackup); bucket ③ e2e → §E DoD
   E1 dispatch designed from the start around the three stage types "single / diff / bundle" (don't change it after the fact)
```

**Why C3 comes before D (completed background)**: room=KiCad group has an unpatchable ownership defect——group is a generic selection primitive
(`pcb_group.h:44`), name not unique, copy immediately collides, no provenance slot (forced the uuid-name-stuffing hack). The KiCad multi-channel source of truth
is each footprint's sheet identity, group is just a derived projection. C3 restores the original intent: room = footprint `sheetname`. **See §C3.**

**Bidirectional pinning discipline** (before writing the consumer, first use the consumer's real interface to pin down the upstream acceptance): B→C uses
the `_generate_net_map` consumer-oracle (pinned); C→E uses the real router consumer-oracle (the E3 thin slice was pulled forward).
**D-Tier2→E-Tier2 direction reversal (contract-first co-design)**: `batch_route_bundle` does not exist today, there is no ready interface to pin against, so
D defines and strict-xfail pins the contract first and E implements accordingly (cannot do E first and then force a schema refactor, see §D-Tier2).

---

## Key facts & constraints (must read before changing code; all empirically verified, evidence = the KiCad source / code file:line annotated in place on each item)

### File dialects & parsers

1. **Write dialect = v10** (after S7): `PcbFile.dumps` (`src/faebryk/core/zig/src/sexp/kicad/pcb.zig`) always writes v10,
   stamps `version=20260206`; v9/v5 are readable, reading in then writing out upgrades to v10 (upgrade-on-write).
   `KICAD_PCB_VERSION/KICAD_FP_VERSION = 20260206` (`pcb.zig:12-15`).
2. **v10 net model**: a v10 file has **no top-level net table**——net exists only at reference sites `(net "name")`; no `net_name`
   redundancy; keepout zone omits the net clause. `pcb.nets` on read is **synthesized with numbering sorted by name** from a reference scan
   ("" is always 0, 1..n dense, reference-order-independent, cross-process deterministic). The number = an in-process handle, the Python-visible model is unchanged
   (`segment.net:int`, `pad.net:Net{number,name}`). Corollary: **an unreferenced net is not persisted**——
   `insert_net` must subsequently bind a pad/routing to survive.
3. **Parser toward unknown content**: KiCad errors out on an unknown token (`pcb_io_kicad_sexpr_parser.cpp:1388`) →
   own metadata can only go into footprint `(property ...)` or a sidecar, inventing custom tokens is forbidden. On the Zig side unknown keys
   were changed to loud warning / strict throw (`fileformats.py` `UnknownSexpKeys`/`last_unknown_keys`).
4. **`kicad.loads` cache + pyzig ownership [✅ S1]**: cache invalidates by (mtime_ns, size),
   `kicad.dumps(obj, path)` writes back to the cache; child-object wrappers hold a strong-reference chain to the owner, `loads(...).kicad_pcb` is safe.
5. **version guard [✅ S2]**: exceeding `PCB_MAX_SUPPORTED_VERSION`(20260206) throws
   `kicad.UnsupportedKicadVersion`.

### Determinism boundary

6. **UUID opaque ⟹ determinism = semantic equivalence (not bytes) [redefined 2026-06-15, old "uuid4+FBRK suffix / incremental steady-state
   byte-identical / .kicad_pcb must be committed" is void]**: `gen_uuid` (`fileformats.py`) = pure uuid4, no mark
   (§G/fact 7). uuid is both random and meaningless ⟹ **byte equivalence must not be desired**, determinism tests always use `semantic_view`
   (position+net-name connectivity+structure, stripping uuid/net numbering); a generated `.kicad_pcb` need not be committed as a byte anchor (CI can build
   from scratch twice and compare semantic). Full corollaries see §G.
7. **uuid opaque = no metadata stuffing / no flag reading (FBRK side channel deleted, 2026-06-15)**: `gen_uuid`
   (`fileformats.py`) now has no `mark` parameter, purely returns uuid4; `transformer.py`'s `gen_uuid(mark)`/
   `is_marked`/`_add_group` are cleared. The old "variable-length mark stuffed into a fixed-length uuid, silent malformation" root cause (stuffing data into uuid) vanishes
   with it. provenance goes through the `atopile_address` property + `FBRK:notouch` fp_text. Resident invariants see §G.
8. **`keep_net_names` defaults to follow `frozen`** (`config.py:605-606,639-640`): normally each build re-derives
   net names——the source of name drift. Under v10 name drift = geometry-ownership drift (fact 2).

### KiCad behavior

9. **net-0 dangling copper is cleaned up by KiCad on re-save** (along with empty groups): copper-layer geometry written must hang off a real net; guide/marker
   geometry goes on User.x (User.1=guide corridor, User.2=keepout region).
10. **atopile does not generate .kicad_sch, but `sheetname` is still usable (old conclusion corrected, see §C3)**: room/multi-channel
    **does not go through group**, goes through **each footprint's synthesized `sheetname`(+`sheetfile`)** (derived from the ato hierarchy) + rule area
    `(placement (sheetname "<addr>"))`. **Empirically verified (2026-06-14, landed as `test_C3_4`)**: a 31-byte room name applied as `sheetname`/`sheetfile`
    + zone placement to a footprint with no corresponding .kicad_sch, `kicad-cli pcb upgrade --force`
    re-saves (v20260206) with **`sheetname`/`sheetfile` verbatim faithful**, placement faithful, `drc` rc unchanged.
    **Key correction: `path` is not faithful**——KiCad owns the sheet-instance path, and on upgrade rewrites the synthesized `path` into a brand-new
    UUID (empirically verified). Hence **room key = `sheetname` (not `path`), atopile does not write `path`** (if written it is changed by KiCad and introduces
    UUID nondeterminism). The zig schema already models `sheetname`/`sheetfile` (`pcb.zig:690-692`), `ZonePlacement.sheetname`
    (`pcb.zig:828`), so **rule area going through sheetname does not require changing zig**.
11. **DRC JSON has no structured net field** (net name embedded in description text): the diagnostics layer uses items[].uuid to reverse-resolve via layout_ir.
12. **SWIG Python binding fully banned** (KiCad 10 removed the board-level API); Repeat Layout is GUI-only entry.

### Periphery

13. **KiCadRoutingTools already has structured results** (`return_results=True`, `JSON_SUMMARY`, `BlockingInfo`):
    the diagnostics layer is aggregation+mapping; its parser is independent, v9/v10 compatible, does not parse groups——unaffected by this repo's migration.
14. **room rule area goes through the sheetname source, no zig change**: room rule area uses `(placement (sheetname ...))`,
    `ZonePlacement.sheetname` (`pcb.zig:828`) already exists → no zig change needed. (There was a plan to add a `group` enum/field
    to zig `pcb.zig:322/824` as the room source, abandoned after C3 switched to sheetname——group has no provenance slot, see §C3.)
15. **EasyEDA part fetching = CloudFront WAF (not "rate limiting") [✅ D1]**: (a) UA deny-list——`easyeda2kicad`
    hardcoded UA / `python-requests` / `Mozilla` all 403, `curl/*`, node UA allowed; (b) per-IP rate——
    after a burst even an allowed UA is briefly all-403 (response body `Request blocked` HTML → `r.json()` throws
    `Expecting value: line 1 column 1`). Fix see `easyeda_resilient.py`; warm build goes through the 1-day cache with zero calls.
16. **router locking model = cross-stage hard obstacle (free) + in-stage soft mutual rip-up (the only tunable) + no per-net lock** [source empirically verified
    2026-06-19]: E1 calls each route stage as an **independent** `batch_route`/`batch_route_diff_pairs`/`batch_route_bundle`,
    accumulating pcb_data stage by stage. ① **cross-stage = hard obstacle, cannot be ripped**: when building the obstacle map, any segment/via/pad not in this run's
    `nets_to_route` is treated as a hard obstacle (`obstacle_map.py:82-120`: segment :82-96 / via :98- / pad :114-), rip-up only touches nets within this run's `net_ids`
    (`rip_up_reroute.py`)——copper from a prior stage is inherently unrippable for a later stage. Hence "ordered pipeline = routing-priority **hard guarantee**"
    needs no lock added to the router, **put the net to be protected into an earlier stage and it is unrippable**. ② **within a stage (the same batch) sibling nets
    can soft-rip each other**, and this is the **only tunable** item: `max_rip_up_count` (default 3) / `ripped_route_avoidance_cost` / `_radius`
    (`routing_config.py:60/83-84`). ③ the router has **no per-net `lock/fixed/frozen` primitive whatsoever** (searched and confirmed).
    **Corollary**: the correct means for layout.yaml to express "priority/unrippable" = **stage splitting and ordering**, not a per-net flag; to keep a trace
    untouched even by siblings → give it its own earlier stage.

---

## Completed (conclusion + code pointer; for details see the code)

> The implementation/contract/invariants of each item below are all written into the source (docstring / same-named test / schema). BACKLOG keeps only
> "conclusions not visible in the source + pointers". For details read the pointer files, **do not come back here to look**.

### §A Determinism foundation [✅ 2026-06-12]
group member ordering drift, keep_designators, manually-named groups silently deleted——all fixed.
Pointers: `test/end_to_end/test_group_determinism.py` (mutation-verified); fix = full sort at the end of `pull_group_layout`,
`_is_managed_group()` (only cleans self-managed groups whose uuid suffix = group-name hex).

### P0.1 v10 migration test scaffold (T0–T9) [✅ 2026-06-12]
Principle: test diversity is manufactured by the harness (corrupter / PYTHONHASHSEED), not relying on natural corpus (natural corpus table order =
numbering order, a position-bound loader can slip through; T8 mutation self-check empirically proves the corrupter is necessary).
Pointers: `libs/test/sexp_tree.py`, `test_fileformats_corpus.py`, `semantic_view.py`,
`test_net_binding_corruption.py`, `test_net_name_properties.py`, CI `.github/workflows/pytest.yml`,
offline fixture `test/common/resources/{fileformats/kicad/v10,easyeda-cache}/`.

### P0.2 v10 dialect migration S0–S6 + D1 [✅ 2026-06-13]
v10-only (upgrade-on-write) landed: write dialect=v10, version guard, tenting nesting, net model synthesized by name,
unknown-key loudening + schema completion, Python consumer-side migration, EasyEDA part-fetching resilience.
Pointers (tests are the SSOT): `test_v10_acceptance.py`, `test_pyzig_ownership.py`, `test_version_guard.py`,
`test_tenting_dialect.py`, `test_unknown_key_loudness.py`, `test_gui_edit_roundtrip.py` (GUI-edit
round-trip gate), `easyeda_resilient.py`+`test_easyeda_resilient.py`.
Two CONFIRMED+FIXED bugs carry regression tests, the debugging narrative is not kept in BACKLOG:
- **BUG-2** v10 read side does not backfill pad net names → rebuild loses room routing: fixed in `pcb.zig PcbFile.loads`
  by backfilling `pad.net.name` after synthesizing the net table, regression `test_v10_acceptance.py::test_v10_read_backfills_pad_net_names`.
  (The lesson "pull≠sync incremental steady-state" is pinned by the e2e determinism test; the PATH footgun is recorded in `CLAUDE.md`.)
- **BUG-1** test writes back to source fixture: the `app` fixture changed to `shutil.copy2` to a tmp copy.

### §B layout_ir (text↔geometry sole interface) [✅ 2026-06-14 all green; I7 to be replaced by C3 with I7′]
Invariants **I1–I8 + Igeo + B2 are all = same-named tests in `test/libs/kicad/test_layout_ir_contract.py`**
(**I7 "group members == sync_groups" is replaced by C3 with I7′ "room = atopile_address prefix-derived"**, IR drops `groups{}`);
IR shape/semantics = `src/faebryk/libs/kicad/layout_ir.py` module docstring;
schema = `src/faebryk/libs/kicad/layout_ir.schema.json` (draft 2020-12, shipped with the package);
the build step produces `build/builds/<t>/<t>.layout_ir.json` (`build_steps.py` registers "layout-ir").
Conclusions not visible in the source:
- net-name resolution reuses `semantic_view._NetTable` (I3 shares the same source as the second reader, not an independent derive).
- **bridge② (`signal_nets`, I4b) is the only part that needs the graph**, the pcb-only IR does not include it——so §C is not blocked on it.
- **consumer-oracle**: rebuilding `_generate_net_map` using only the IR must == the in-service one (`layout_sync.py`)——the C2 rewrite
  must uphold this equivalence (bound to outstanding issue 3).

### §C room geometry (forced via / room copy) [✅ 2026-06-14 all green; room representation changed by C3 (C3.6 reopens)]
Implementation = `src/faebryk/exporters/pcb/layout/room_ops.py` (the module docstring is the behavior authority);
(room_ops is already address-derived, logic unchanged; it merely stays compatible with C3's new IR shape, the contract test pins the new boundary C3.6.)
Contract/DoD = `test/exporters/pcb/layout/test_room_ops_contract.py` (8 tests: Tier-1 pure functions 6 +
Tier-2 real router 2). Frozen API: `pad_board_xy`, `insert_forced_via→ForcedVia`,
`address_prefix_map`, `room_net_map` (== the in-service `_generate_net_map`), `copy_room_layout→RoomCopy`.
Conclusions not visible in the source:
- the three copper pieces of a forced via (pad→via@in / cross-layer via / via@out→pad) all hang off a real net (not 0, fact 9),
  the guide line is only on User.1; the C2 copied segment with unmapped net→0 (KiCad cleans it up on write, faithful rather than silently mis-connecting).
- **§B reality-check passed**: the three pure functions were correct on first implementation, `layout_ir` zero changes.

#### C→E/F boundary facts (outside the source——the router is not inside atopile, must read for §E/§F)
Real router = consumer-oracle (empirically verified system python3, `grid_router.so` not in this venv):
1. **Two entries, different key sets**: single-ended net → `route.py:batch_route` (`JSON_SUMMARY` keys
   `routed_single`/`failed_single`); differential pair (net with `_P/_N`, `P/N`, `+/-`) →
   `route_diff.py:batch_route_diff_pairs` (`routed_diff_pairs`/`failed_diff_pairs`). Shared keys
   `failed`/`successful`/`total_vias`. **Feed the wrong entry → no `JSON_SUMMARY` printed**.
2. **Only routes unconnected nets**: an already-connected net → `failed=1` with no output, or "nothing to route" prints no summary.
   Hence there is no "preset via as a soft waypoint to detour" mode——a forced via is "respected" = it has already made the net connected and the router doesn't touch it;
   the C2 copied segment likewise (ordinary already-connected copper) is auto-preserved, **§E does not need to lock/preserve §C preset geometry**.
3. **`total_vias` only counts router-added vias**, not preset forced vias——the surviving/on-board total must **re-read the output board**.
4. **User.* is not read by the router by default** (reads copper layers only)——but the router has native switches
   (`vendor/KiCadRoutingTools/routing_config.py:117-123`): `guide_corridor_enabled` (reads the User.1 guide line,
   pulls the net along the corridor) + `keepout_enabled` (reads the User.2 keepout polygon, blocks traces). Hence §C's User.1 guide
   / §D's User.2 room boundary **can be explicitly enabled by E1 as a first-class routing constraint** (off by default, on per stage, see §D/§E1)——
   not merely a visual artifact. Do not confuse these two paths with §D's placement rule area (KiCad's own grouping/DRC, a different mechanism).
- **Design decision (dual entry is not a defect)**: two entries = two real algorithm sets sharing the same Rust grid kernel
  (`grid_router:GridObstacleMap/GridRouter`). Differential = `PoseRouter` pose method + `diff_pair_gap`
  constant spacing + `centerline_setback` (auto fanout/spread near pads, corresponding to decoupling taps / connector
  pitch) + `fix_polarity` + `length/time_matching` + `gnd_via`; single-ended = `route_multipoint_main`/
  `power_nets`. Industry convention (Altium also splits differential/single into two routers), **not a bug**. **Do not merge the algorithms (merging = regression, losing
  coupling/polarity/length-matching/pose/centerline)**; mode is explicitly declared by `route_stages` (§D/§E1).
- the E3 thin slice only verifies the **differential-pair** schema (`test/exporters/pcb/layout/test_router_smoke_batch_route.py`, shells out to system py3 to run `vendor/KiCadRoutingTools`);
  the single-ended schema awaits §E3 to fill in.

### §C3 room de-group-ification (room = footprint sheetname) [✅ 2026-06-15]
Decision: room is no longer = KiCad group. atopile **creates zero/deletes zero groups** (user groups persist by construction, the A4 failure mode disappears);
room carrier = footprint `sheetname`(+`sheetfile`) (value = ato address prefix, derived by `_get_room_name`; **does not write `path`**——
KiCad owns and rewrites it, fact 10); route/via/zone ownership = internal net; rule area goes through `(placement (sheetname))`, no zig change.
Protocol + interface delta = code SSOT:
- contract `test/exporters/pcb/layout/test_room_migration_contract.py` (module docstring = full protocol + ratchet);
  e2e `test/end_to_end/test_room_migration_e2e.py` (groups-created=0 / pull-semantics determinism).
- implementation `layout_ir.py` (`rooms` derivation) + `layout_sync.py` (`sync_rooms`/`pull_room_layout`/`_clean_room`,
  deletes `_is_managed_group`) + `room_ops.py` (`copy_room_layout` copies only this room) + `build_steps.py`/`cli/kicad_ipc.py`.
Conclusions not visible in the source:
- committed fixtures still contain old groups, but **no test reads group as a room source anymore**: the former one-time migration proof
  `test_C3_1_corpus_groups_are_address_prefix_recoverable` (reads old groups from the pcb and compares against address prefixes) **is retired**
  (`test_room_migration_contract.py` comment records its removal——keeping it would perpetuate the ato→group coupling that C3 abolished); the in-service
  `test_C3_1_inline_room_equals_address_prefix_grouping` only reads the sheetname-derived `ir['rooms']`. A board containing groups
  is covered only by the parser corpus (`test_fileformats_corpus`) as opaque user content round-trip.
- `transformer._add_group`/`is_marked`/`gen_uuid(mark)` are deleted (§G uuid opaque)——atopile has no ato→group mapping.
- cascade: §B I7→I7′ (IR drops `groups{}`), §C room_ops follows the new IR shape (contract pins C3.6).

### §D layout.yaml loading + KiCad placement rule area (D1–D4) [✅ 2026-06-16]
layout.yaml = layout-intent source (a peer source to the .ato circuit source / .kicad_pcb geometry source). D1 path config → D2 parse-and-validate into `LayoutPlan`
→ D3 lands a placement rule area per room → D4 build step wiring + produces `<t>.layout_plan.json` (for E1).
**Architecture (declarative rooms + ordered route_stages pipeline, why yaml not .tcl) = the "FORM" section of the `layout_plan.py` module docstring (SSOT).**
Implementation:
- `src/atopile/config.py` (`BuildTargetPaths.layout_config`, D1).
- `src/faebryk/exporters/pcb/layout/layout_plan.py` (`LayoutPlan/Room/RouteStage/GridRouteOverride` +
  `load_layout_plan`/`resolve_nets`, D2; module docstring = full protocol).
- `src/faebryk/exporters/pcb/layout/rule_area.py` (`generate_rule_areas`, D3).
- `src/atopile/build_steps.py` (`generate_layout_plan` step, hooked into `generate_default`, D4).
Contracts: `test/test_config.py` (D1), `test_layout_plan_contract.py` (D2), `test_rule_area_contract.py` (D3),
`test/end_to_end/test_layout_plan_build.py` (D4).
Conclusions not visible in the source:
- **`GridRouteOverride`'s oracle = the union of the two router entries' parameters, NOT `GridRouteConfig`** [independently verified correction 2026-06-16]:
  `route.py:batch_route`/`route_diff.py:batch_route_diff_pairs` take flat kwargs, and only internally build `GridRouteConfig`
  and rename (`impedance`→`impedance_target` etc.). Mode-exclusive keys (`guide_corridor_*` single-ended /
  `diff_pair_*`·`fix_polarity`·`gnd_via_*` differential) are **rejected by mode during D2 parse**, E1 does not re-validate. Drift pin =
  `test_layout_plan_contract`'s union drift guard + mode-consistency self-test (AST takes the real router signature, drift goes red).
- Two real defects found and fixed during implementation: ① placement writing `source_type`/`source` → **SEGFAULT KiCad loader** (mis-built memory/
  protobuf fields, the file syntax has no such token; see the "key facts" placement item + regression `test_generated_placement_has_no_source_type`);
  ② an unknown room module with explicit origin/size once **silently produced no rule area** (S5a violation) → changed so both geometry modes are loud
  (regression `test_unknown_room_module_is_loud`).
- D's artifacts = E/F inputs: `<t>.layout_plan.json` (resolved route_stages + room metadata) for E1; the on-board rule area serves
  KiCad placement/DRC + F-diag hit-testing. E1 expands `RouteStage.config` verbatim into entry kwargs (translation happens inside the router).

### §D-Tier2 bundle bus-transport (mixed single/diff schema + geometry) [✅ bucket ① 2026-06-20; D-side build integration 2026-06-25]
bundle = a routing-intent unit of ordered lanes (single/diff) + segmented trunk + breakouts at both ends (flat `route_stages` cannot express it).
Bucket ① (in-D model + geometry SSOT + build injection) landed; bucket ② (`batch_route_bundle` implementation + breakout fanout) = **§E-Tier2**
(contract already strict-xfail frozen, implementation see unfinished §E-Tier2; full protocol = `test_bundle_contract.py` module docstring SSOT).
Pointers: `layout_plan.py` (`BundleStage`/`SingleLane`/`DiffLane`/`Trunk`/`Breakout`/`RipUpBudget`, module docstring = protocol),
`bundle_geometry.py` (`cross_section_offsets` geometry SSOT + `bundle_artifact`), `build_steps.generate_layout_plan`
(a bundle stage calls `bundle_artifact` to inject the computed offsets, a plain stage goes through model_dump); contract `test_bundle_contract.py`
(bucket ① all green, bucket ② strict-xfail awaiting E-Tier2); e2e `examples/sata_bundle` + `test/end_to_end/test_bundle_build.py`.
Conclusion outside the source: the differential pair is never dissolved throughout (L1 intrinsic coupling), the bundle only adds ordering + lane spacing at the outer layer; the transition-segment offsets at both ends are fixed, only E searches for morph.

### §D-Tier3 self-contained placement + board-level `board` section [✅ bucket ① 2026-06-26; bucket ② TS-AUTH-A 2026-06-28]
In a generative / agent flow, `.ato` (circuit) + `layout.yaml` (layout) must be self-contained and the single source of authority; `.kicad_pcb` is a derived artifact,
reuse is only an optional optimization, and must not be the sole channel for any fact ("to avoid the GUI you must first use the GUI" = design defect). Landed:
- placement: `Placement`/`resolve_placement` (room-relative resolution + `absolute` board-absolute escape hatch)/`resolve_component_pose`
  (placements>reuse priority; text value overrides, absent falls back to reuse, neither → loud), coordinate system = room-relative (block reusable).
- room geometry: `Room.polygon` (≥3 points, non-self-intersecting, dependency-free self-intersection detection) + rotation (CCW around the first point) + per-room layers
  wired into effect (`rule_area._room_boundary`/`_rotate`; fixed the S5a hazard of "declared but silently ignored by D3"——either it takes effect or the field is removed).
- `board` section: `BoardOutline` (origin/size or polygon)/`Stackup`/`StackupLayer` + pure functions `stackup_layers`/`outline_bounds`;
  impedance→stackup **hard dependency** plan-level validation (no stackup → loud, pinning `route.py:256-258` silently falling back to fixed track width = impedance out of control).
- **single layer-count authority (board side closed the loop, TS-AUTH-A green)**: `config._stackup_copper_names`/`_copper_layer_table` derives the fresh board's copper layer list from
  `stackup_layers(board.stackup)` (KiCad numbering F.Cu=0/inner 1../B.Cu=31), killing
  the old 2-layer hardcode in `config.py`——board and router layer counts share one source, physically no longer forkable.
Pointers: `layout_plan.py`, `rule_area.py`, `config.py` (`ensure_layout`/`_stackup_copper_names`/`_copper_layer_table`);
contracts `test_placement_contract.py` (TP/TR), `test_board_section_contract.py` (TB/TS/TS-AUTH-A green, TS-LM′ single-authority lock).
Conclusions outside the source (still unfinished, belong to §E): **routing-side** layer-count authority (E1 passes `stackup_layers` as `layers` to the router, does not eat the 4-layer default) =
**TS-AUTH-B = §E1**; pure-text no-reuse end-to-end board build = **bucket ③ = §E DoD**; reuse-only board-level net-class/pour/keepout/silk
(missing must be loud, no default) = **§F** (F-drc-rules/F-fill/F-keepout/F-silk).

### Tier0 corridor-as-data [✅ 2026-06-28]
A single stage's `corridor: [[x,y],...]` text field → build draws a User.1 polyline + sets `guide_corridor_enabled`, zero router
changes (reuses the router's native guide reader, §C boundary fact 4); soft pull, not a hard checkpoint. Pointers: `corridor.py` (`draw_corridors`,
idempotent), `RouteStage.corridor` (`layout_plan.py`, single-only + ≥2 points → loud), `build_steps.generate_layout_plan`;
contract `test_corridor_contract.py`.

### route_stage `name` unique [✅ 2026-06-28]
stage-name uniqueness loud validation = the prerequisite for `--up-to` breakpoints addressing by name, and prevents `resolve_nets` silently overwriting a same-named stage. Pointers:
`LayoutPlan._validate_unique_stage_names` (`layout_plan.py`); contract
`test_layout_plan_contract.py::test_duplicate_stage_name_is_loud` (+ positive control `test_distinct_stage_names_pass`).

### placements → consumed by transformer at build time [✅ 2026-06-28]
The pure-function authority `resolve_component_pose` (text>reuse priority, landed in §D-Tier3) is now wired into build: `apply_placements` moves each
managed footprint hit by a text placement (matched by `atopile_address` property) to the resolved pose, **overriding** transformer's
automatic 10mm grid spread (`transformer.py:176`/`:2013-2080`)——room-relative composes through room origin, `absolute` lands verbatim,
rotation+side apply through transformer's flip-aware `move_fp`; hitting a nonexistent footprint = loud. Called inside `generate_layout_plan` before
`layout_ir`, so the room-derived bbox reflects final positions. Pointers: `placement.py` (`apply_placements`/`_room_for`, room =
the room with the longest prefix of the component address), `build_steps.generate_layout_plan`; contract `test_placement_apply_contract.py` (PA1-6).

### §E1 route runner (route_stages → routing calls + route_report.json) [✅ 2026-06-28]
`layout_plan_runner.py`: pure `build_invocations` (route_stages → `StageInvocation` list, zero subprocess / zero router import)
+ `run_route_stages` (drives the invoker per stage, aggregates, writes `route_report.json`) + `default_subprocess_invoker` (shells to
system python3 to run the router, parses `JSON_SUMMARY`, early-return=None). Landed contract: dispatch by stage type (single→`route.batch_route` /
diff→`route_diff.batch_route_diff_pairs`, union tag precedes `RouteStage.mode`); config expanded verbatim (only explicitly-set, the rest go to
router defaults, no None leaks, no re-validating the mode keys D2 already validated); **layer-list single authority** = `stackup_layers(board.stackup)`, never eating
the `route.py:230` four-layer default (**turns TS-AUTH-B green**); missing board/stackup or per-stage `config.layers` are all loud (single authority); cross-stage board accumulation (prior copper = free hard obstacle, no lock added); tolerates missing `JSON_SUMMARY` (early-return = zero routing, no KeyError); aggregates common
scalar keys (including `total_time`/`total_iterations`) by summing from the summary dict, per-type lists bucketed; `--up-to <name|1-based index>`
breakpoint (writes partial board + report, out-of-range/unknown → loud, a bundle outside the slice is not reported, `report.up_to` normalized to the stage name).
Pointers: `layout_plan_runner.py`, contract `test_layout_plan_runner_contract.py` (R1-R14 pure + E15/E16/E17 e2e;
bundle dispatch R2/R2b/R2c/R2d/R2e + bundle e2e E18 see below "§E-Tier2"); the TS-AUTH-B ratchet probe re-points to the runner
(`test_board_section_contract.py::_e1_passes_stackup_to_router`, previously pointed to build_steps——routing is not a build step, corrected).
Conclusions outside the source (honestly recording limitations):
- E1 is a **library**: currently consumed by e2e tests, the CLI `ato route` goes through §F (routing is not an `ato build` step, so it does not enter the build pipeline).
- **bundle dispatch** (`batch_route_bundle`) = **§E-Tier2 landed** (see below), E1 expands via `_bundle_invocation` the
  `bundle_artifact` into a geometry payload, member names resolved via bridge②, breakout `at`→`part`+kicad order.
- e2e (E15/E16/E17/E18) gated on system python3 + router board; **this sandbox has scipy present, actually ran** (LVDS 2-layer board, diff pair
  actually routed through `successful>=1`, bundle actually laid 3 members' copper); no single-ended fixture board, so E15 routes one trace of a diff pair through the single-ended entry and tolerates the early return.
- **per-stage layer subsetting = intentional non-goal** (single authority): per-stage `config.layers` is loudly rejected; if per-stage layer limiting is ever needed it will be discussed separately.

### §E-Tier2 bundle routing (`batch_route_bundle` + runner dispatch + e2e) [✅ 2026-06-29]
`vendor/KiCadRoutingTools/route_bundle.py:batch_route_bundle`: turns the frozen bundle contract (segmented trunk + ordered member offset
table + 2 breakouts) into a **parallel bus**——one offset track per member along the centerline. trunk = **deterministic geometry, zero A***: the cross-section
is **repacked** by each vertex's `spacing` (`_repack_offsets` mirrors `bundle_geometry.cross_section_offsets`), member width and each
diff pair's intra gap constant (bundle-global invariant); rigid segments unchanged, transition segments morph but **L1 coupling does not disperse** (only touches inter-lane
spacing, never touches the intra-pair gap). Member input offset is taken as a trusted entry profile, per-vertex offset = input + repack DELTA (rigid = input verbatim,
transition = plus morph increment, P/N same increment so gap constant). breakout fanout (pad→trunk end) runs only when a board is present. Per-member routed/blocked results +
stdout `JSON_SUMMARY` (including `members[*].polyline`/`routed_members`/`failed_members`/scalar keys). geometry-only mode (no
input_file) = pure python, no rust / no parser, so the D-Tier2 contract drives it directly; board mode lazily imports parser/writer.
Runner side: `build_invocations` dispatches the bundle via `_bundle_invocation` (entry/module=`route_bundle`/`batch_route_bundle`,
config expanded verbatim, layer list←stackup, cross-stage board accumulation); `default_subprocess_invoker` selects the calling convention by stage type (bundle =
geometry-driven, trunk/members/breakouts in KW, input/output as keyword arguments).
Pointers: `route_bundle.py`, `layout_plan_runner.py:_bundle_invocation`; contract `test_bundle_contract.py` (T-B1 AST drift pin /
T-B2 result-shape / T-B3 parallel bus / T-B3b offset→track mutation self-check / T-B4 rigid diff coupling / T-B6 transition morph pins SSOT + rigid segment does not
morph + intra-pair gap constant) + `test_layout_plan_runner_contract.py` (R2/R2b dispatch / R2c config / R2d layer conflict / R2e aggregation /
E18 e2e: LVDS board actually runs, route_report by_type['bundle'], board actually gains copper).
Conclusions outside the source (honestly recording limitations):
- **breakout fanout = direct-connect** (a straight segment pad→the nearer trunk end), **not** obstacle-aware A*; breakout `order` permutation and
  `spacing_overrides` are **not yet consumed by the geometry** (only schema/validation exist); crossing localization/fanout optimization = later.
- **per-member failure paths are not exhaustively tested**: trunk geometry always succeeds ⇒ routed=True; when a board is present but a pad is missing, `blocked` records a string but does not flip routed=False,
  and does not count as failed. The genuine failed-count path (invalid geometry) is not triggered by the fixture.
- **transition morph fidelity**: using the "input offset + repack DELTA" model, rigid segments exact, transitions repacked per cross_section_offsets semantics (intra-pair
  gap constant); a winding centerline's vertex normal uses the angle bisector of adjacent segments (straight trunk exact, sharp bend miter approximation).

---

## Unfinished tasks

**This section top-to-bottom = execution order** (chapter letter = critical-path order). Completed prerequisites (C3+D / D-Tier2 buckets ①② / D-Tier3 bucket ①·TS-AUTH-A /
Tier0 corridor / stage-name unique / placements→transformer / **§E1 runner+TS-AUTH-B+`--up-to`** /
**§E-Tier2 bundle routing+e2e**) all see "Completed" above.
**Remaining critical path = §E DoD (bucket ③ pure-text self-contained board-build e2e) → §F**.

Under contract-first the downstream ratchet's contract stays in its respective D test file, and the implementation **lands in place in §E**, so **no separate D residual block is listed**:
bucket ② = **§E-Tier2 ✅**; **TS-AUTH-B (router layer list) ✅ lands in §E1**; bucket ③ pure-text e2e = **§E DoD** (the routing half is already proven by the
E18 bundle e2e; the self-contained board-build half remains).

Side branches genuinely not on the critical path (P0.2-S7 final acceptance; D5 component_class, shipped 2026-07-03) are collected into the "Side tasks" section **after** §E/§F, **deliberately not numbered in the E/F
sequence** (numbering them would falsely claim they are on the critical path).

### E. [needs §C + §D] KiCadRoutingTools fork
E1 needs §D's plan + on-board rule area, and uses §C1's forced via as a routing-stage capability. E3 is built first in parallel throughout.
**E1 dispatch is designed from the start around the three stage types "single / diff / bundle"** (fill in the bundle implementation after the D-Tier2 contract freeze, don't change it after the fact).
- [x] **E1** `layout_plan_runner.py` [✅ 2026-06-28, see "Completed §E1" above]: route_stages → routing calls dispatched by stage type
  (single→`batch_route` / diff→`batch_route_diff_pairs` / bundle→`route_bundle.batch_route_bundle` see E-Tier2) +
  `route_report.json`; config expanded verbatim, layer list ← `stackup_layers` (**turns TS-AUTH-B green**), `--up-to` breakpoint, cross-stage
  board accumulation without locking, tolerates missing `JSON_SUMMARY`, aggregates by type. Contract `test_layout_plan_runner_contract.py`.
- [x] **E-Tier2 `batch_route_bundle` + breakout fanout + e2e** [✅ 2026-06-29, see "Completed §E-Tier2" above]: parallel-bus
  deterministic geometry (per-vertex repack = `cross_section_offsets` mirror, rigid segments unchanged/transition morph, diff intra-pair gap constant does not disperse L1) +
  direct-connect breakout fanout (pad→trunk end when a board is present) + per-member routed/blocked + `JSON_SUMMARY`; runner `_bundle_invocation`
  dispatch + `default_subprocess_invoker` bundle calling convention. Contract `test_bundle_contract.py` T-B1..T-B6 +
  `test_layout_plan_runner_contract.py` R2/R2b/R2c/R2d/R2e + E18 e2e (LVDS board actually runs).
  - Limitations (honestly recorded, see "§E-Tier2" above): breakout = direct-connect not A*, `order` permutation/`spacing_overrides` not consumed, per-member failure
    paths not exhaustively tested, sharp-bend centerline normal miter approximation. Crossing localization/fanout optimization = later as needed.
  - **E2 in-stage rip-up knob = won't do (decided by the user 2026-06-29: unnecessary, duplicates "key facts" 16)**: cross-stage prior copper is already the router's free unrippable hard obstacle (priority expressed by stage order), the bundle/pipeline's "front line occupies, later line detours" is already a hard guarantee.
    The `BundleStage.rip_up` (`RipUpBudget`) schema is already present (landed in D-Tier2); if a stage-internal "almost no rerouting" is ever truly needed, then wire
    `max_rip_up_count`/`ripped_route_avoidance_cost`/`_radius` into config——no need to create a new cross-stage lock.
- [ ] **E3** independent regression bench (built first in parallel throughout = §E's S0 scaffold): no KiCad install needed, precompiled Rust binary +
  built-in test board + numpy/scipy/shapely; failure injection + report schema validation.
  - the minimal slice was pulled forward to §C [✅]: `test/exporters/pcb/layout/test_router_smoke_batch_route.py`
    pins the differential-pair C→E interface (`return_results=True` 4-tuple / `results_data` geometry fields / `JSON_SUMMARY`
    schema). The full E3 bench (failure injection + multi-board matrix) still awaits here.
  - **to fill in: JSON_SUMMARY schema validation for single-ended `route.py:batch_route`** (boundary fact 1)——the thin slice only verifies
    the differential-pair key set, single-ended goes through `routed_single`/`failed_single`, must extend a second schema validation to prevent silent mismatch.
- [ ] **§E DoD — bucket ③ pure-text e2e (turns the D-Tier3 bucket ③ ratchet green)**: no reuse, `board.outline` + full `board.stackup` +
  `placements` all given as text → generate .kicad_pcb → re-read `semantic_view` to verify layer list / board outline / placement landing == plan (no
  In1/In2.Cu ghost layers on the board). Proves `.ato`+`layout.yaml` self-contained board build, the completion criterion after §E routing lands.
  - **the routing half is proven** (§E-Tier2 E18 bundle e2e: real board real routing + `route_report.json` + board actually gains copper); **the self-contained board-build half remains**
    = `ato build` produces from pure text (no reuse source board) a routable .kicad_pcb with outline + full stackup (build side, heavier,
    gated not_in_ci), decoupled from the routing runner.

### F. Diagnostics closed loop + board-level rule textualization ✅ 2026-06-30 (F1–F8 all landed, e2e closed loop + three rounds of adversarial review)

Capstone chapter——the project's founding premise ("imperative feedback lives in a closed loop outside the files") is realized:
the **build→route→diagnose→(SKILL edits layout.yaml)→rebuild** feedback closed loop is in place. One routing run + KiCad DRC
are aggregated into a structured `diagnostics.json` indexed by ato address; board-level authoring (DRC rules / pour / keepout / silk)
is textualized, making DRC "green" reflect design intent rather than KiCad defaults. **Conclusion + code pointers below; invariants have code/docstring as SSOT.**

#### Landed items (conclusion + pointers)
- **F1 `ato route`** (`src/atopile/cli/route.py`, registered in `cli.py`): thin shell. `run_route_for_build` (testable core, explicit paths +
  injectable invoker) re-parses layout.yaml + reads `.layout_ir.json`, drives `run_route_stages`, writes `.route_report.json`;
  `--up-to` passthrough; missing artifact → loud (`UserResourceException`). Contract `test/cli/test_route_cli_contract.py`.
- **F2 router diagnostics exposure** (submodule `vendor/KiCadRoutingTools`, branch `feat/f2-json-diag`, parent-repo gitlink already bumped):
  the new pure module `diag.py:failed_net_diagnostics` reshapes `RoutingState.net_history` into per-failed-net records
  `{net_name, reason, blocked_by, history}`; `route.py`/`route_diff.py` print an **independent** `JSON_DIAG:` line after the summary
  (distinct from `JSON_SUMMARY`, does not pollute the runner rollup). Runner side `StageResult.diag` + `default_subprocess_invoker`
  grabs `JSON_DIAG` → `route_report.json`. Contract `test_route_diag_contract.py`.
- **F3 bridge② reverse-resolve engine** (`src/faebryk/libs/kicad/layout_ir_resolve.py`, pure venv): `LayoutResolver` inverts the IR =
  footprint_uuid→addr / pad_uuid→(addr,pad) / net→endpoints (reuses `ir["nets"]`) / coord→room (rule-area ring
  point-in-polygon, smallest-area most-specific first); `room_polygons_from_pcb` extracts rings from managed zones. Contract
  `test/libs/kicad/test_layout_ir_resolve_contract.py`.
- **F4 `ato diagnose` → `diagnostics.json` (core)** (`diagnostics.py` pure builder + `cli/diagnose.py` shell): aggregates
  route_report failures (including F2 cause) + `run_drc()` violations + board re-read (G3 real via/geometry totals), correlated via F3 back to
  ato address/room/stage, producing findings (kicad-happy `make_finding` schema embedded in `diagnostics.py` + §F fields
  stage/room/ato_path/reason/blocking_nets/failed_endpoints/…); `sort_findings` deterministic order; DRC against the **automatic pre-route
  baseline** (`paths.layout`, route writes to workdir without overwriting it) splits new vs existing. Contracts `test_diagnostics_contract.py` +
  `test/cli/test_diagnose_cli_contract.py`.
- **F5 F-drc-rules** (`board_rules.py:generate_project_rules`, schema `layout_plan.Board.net_classes`/`NetClass`):
  `board.net_classes` (clearance/track_width/via/diff-pair + assigned ato nets) → `C_kicad_project_file.net_settings`
  (classes + per-net `netclass_patterns`, net resolved via bridge②). `generate_layout_plan` writes to `paths.kicad_project`
  (same basename as the board, kicad-cli auto-associates). **Empirically verified kicad-cli DRC recognizes these classes** (5mm clearance → 0→36 violations, rc=0 no crash).
  Contract `test_board_rules_contract.py`.
- **F6/F7/F8 pour/keepout/silk** (`board_features.py:generate_board_features`, schema `Pour`/`Keepout`/`SilkText`):
  `board.pours` (copper Zone fill=yes, bound to a real net via bridge② / net-0 → loud), `board.keepouts` (ZoneKeepout restriction,
  **no net / no placement → structurally avoids the SEGFAULT footgun**), `board.silk` (gr_text). `generate_layout_plan` emits,
  idempotent across rebuilds (managed zones deleted first by name prefix `fbrk_pour_`/`fbrk_keepout_`; silk exact-deduplicated by (text,pos,layer)).
  All three kicad-cli upgrade rc=0; empirically verified build×2 does not proliferate. Contract `test_board_features_contract.py`.
- **e2e closed loop** (`test/end_to_end/test_diagnose_loop_build.py`, `@slow @not_in_ci @skipif(no kicad-cli/py3)`): build(examples/layout_reuse) → `ato route` → `ato diagnose` → asserts `diagnostics.json` correlates failures/DRC to
  ato_path/room, distinguishes new vs old DRC. **Empirically verified green** (39 DRC findings, 10 new vs the automatic baseline).

#### Gap closure (G1/G2/G3)
- **G1** (the "why" was only in stdout and discarded) → F2 `JSON_DIAG` exposure + runner capture.
- **G2** (no uuid→address) → F3: footprint/pad uuid reverse best-effort; track/via/zone uuid **never enters the build-time IR**,
  falls back on net name + coordinate hit.
- **G3** (`total_vias` is only the additions) → F4 `_default_board_reader` re-reads `.kicad_pcb` to get `board_vias`/`board_segments`.

#### Honestly recording limitations (recorded as landed)
- **the e2e caught three genuine integration bugs all missed by unit tests** (fixed, see commit): ① runner shell router used a relative board path but cwd=router_root →
  absolutized (the invoker is responsible for the cross-cwd boundary); ② `shutil.which("python3")` picks the venv python (no rust ext) when the venv is on PATH →
  the new `_system_python3` skips venv-prefixed interpreters; ③ F3 originally raised on a duplicate uuid, but **layout_reuse's reused instances legitimately duplicate
  uuids** → changed to ambiguous→None (downgrade to coordinate/net correlation), not corrupt.
- **caught by adversarial review** (fixed): multipoint routing failures were originally only in the summary, swallowed by the `if diag/else` branch → `build_diagnostics`
  unconditionally harvests `failed_multipoint` into a ROUTE-FAIL finding, and adds `route_failures_unaccounted` to explicitly expose any residual difference;
  an explicit `--baseline` that does not exist was originally silently ignored → changed to loud.
- uuid→address is footprint/pad only (a duplicate is ambiguous); track/via/zone DRC items are by coordinate hit + net name, not uuid.
- blocking cause = router net_history `top_blockers` (net name + reason, not per-blocker cell count; the latter is only inside the routing loop,
  not in net_history——exposing it needs intrusive loop-site capture, deferred).
- DRC `nets` extracted from description text (kicad-cli has no structured net field)——best-effort.
- board authoring: first delivery = text→board emit + missing → loud; DRC-rule coverage of KiCad's full rule grammar is incremental.
  F-silk idempotency is only for **unchanged** text (gr_text has no managed-marker slot; changing text/shifting leaves an old orphan, needs manual cleanup).

### H. Non-blocking pipeline: deferred BOM + partial P&R + picker sidecar + concurrency (driver: "no designer finalizes the BOM before P&R")

**Motivation** (decided by user 2026-07-03): the part picker (and its symbolic solver) must NOT sit on the
critical path to a routable board. Real hardware flow = constrain footprints → place & route → finalize BOM,
possibly all three *concurrently by different agents*. A global solve is not always feasible (the solver can
even non-terminate, see the backstop below); the same philosophy that lets a board *route partially* should
let it *pick partially*.

**Grounded seam** (verified against `build_steps.py` muster + `picker.py` + `cli/route.py`, 2026-07-03):
- Muster order: `load_pcb → picker → prepare_nets → update_pcb → … → generate_bom`.
- `update_pcb`'s only hard requirement is a footprint per node (`transformer.check_unattached_fps`); it does
  **not** need the MPN. `generate_bom` is the only consumer of the picked MPN.
- `ato route` reads **only** `.kicad_pcb` + `layout_ir.json` + `layout.yaml` — zero picker dependency.
- Footprint sources today: `is_atomic_part` (local, no solver) and the picker's EasyEDA asset. `r.package="R0402"`
  sets only `has_package_requirements.size` — a **picker constraint, not a footprint source**. There is no
  package→standard-KiCad-footprint table (gap → H2).
- In `picker.py::pick_topologically`, explicit picks (`is_pickable_by_supplier_id`/`by_part_number`) attach a
  footprint with no type-solver; only `_pick_tree` + the terminal "verify design" `simplify` are solver-heavy.

**Solver convergence backstop** [✅ 2026-07-03]: `solver.py::simplify` iteration cap now degrades to the
best-effort partial state (gated on `ALLOW_PARTIAL_STATE` / `FBRK_SPARTIAL`, default true) instead of raising
`TimeoutError`. The per-algorithm `dirty` flag is bookkeeping-derived, not a graph diff, so it can churn
equivalent forms forever (confirmed: frozen |V|/|E|/|ops|, drifting content, 60 strict iters never converge —
trigger `ResistorVoltageDivider`). Strict mode (`FBRK_SPARTIAL=false`) keeps the hard failure for CI. Regression
`test_solver.py::test_iteration_limit_degrades_to_partial_state` (mutation-verified). Caveat: partial solve
picks loose passive values; it is a *don't-crash* net, not a *correct-pick* guarantee — that is what H1 (defer)
and H3 (sidecar constraints) properly address.

- [x] **H1 — deferred BOM (`ato build --no-pick` + `ato bom`)** [✅ 2026-07-03]: `--no-pick` threads
  `config.build.no_pick` → `pick_parts` → `pick_parts_recursively(no_solve=True)`, which runs the cheap explicit
  picks + skips `_pick_tree`/verify (`picker.py`). Produces a routable board from footprints alone (pinned/atomic
  designs). `ato bom` (`cli/bom.py`, v1) resolves the full BOM on demand by driving the pipeline to `generate-bom`
  with picking on. Env crosses the build-queue worker via `ATO_NO_PICK` (mirrors `keep_picked_parts`). Tests:
  `test_picks.py::test_no_solve_defers_type_picking` (mutation-verified, zero-network). e2e: `picked_demo`
  `--no-pick` → full artifacts + BOM. **Known limit**: a *type-picked* design (bare `package=` passives) with
  `--no-pick` correctly skips the solver but then fails loudly at `check_unattached_fps` (no footprint) until H2.
- [x] **H2 — footprint-from-package provider** [✅ 2026-07-03]: `package="R0402"` now yields a generic KiCad
  standard footprint (`Resistor_SMD:R_0402_1005Metric`) with no picker/solver, so generic passives route under
  `--no-pick`. Pieces: `libs/kicad/standard_footprints.py` (`resolve_standard_footprint(size, prefix)` — computes
  the canonical chip name, gated on the `.kicad_mod` existing on disk, so unshipped sizes like R_2220 return None
  → loud-or-nothing); `libs/app/package_footprint.py` (`attach_package_footprint` — strict gap-filler: skips
  modules that already have a footprint; creates `is_pad` nodes from the standard fp's pad names + matches the
  R/C/L leads via `can_attach_to_any_pad`; attaches `has_associated_kicad_library_footprint` + registers the
  std lib in the project fp-lib-table via `_insert_fp_lib`); wired in `build_steps.pick_parts` gated on
  `config.build.no_pick`. **Also fixed** `designators.py::attach_random_designators`: it assigned designators only
  to `has_part_picked` modules, so a footprint-without-pick had none and failed `ingest_footprint`; broadened to
  "anything with a footprint" (a no-op superset in normal builds). Source = KiCad stdlib (`kicad-footprints` →
  `/usr/share/kicad/footprints/`). Tests: `test_standard_footprints.py` (9), `test_picks.py`
  attach + loud-noop (2). e2e verified: a `package=`-only 2-resistor design builds a full board with both
  footprints and the shared net bound (`R1 R_0402 ~ R2 R_0603` on `unnamed[0]-1`). **Limits**: chip R/C/L only
  (2-terminal); polarized/multi-pad and non-chip packages are a loud skip. Optional `footprint="Lib:Name"` ato pin
  not yet added.
- [x] **H3 — part-picker sidecar (incremental subset picking + extra constraints, `.ato` unchanged)**
  [✅ 2026-07-03]: `libs/app/parts_sidecar.py` — an out-of-source `parts.yaml` keyed by ato address (same address
  as `layout.yaml`/`atopile_address` = `get_full_name(include_uuid=False)`, e.g. `r1`). Per entry: `lcsc:` /
  `mpn:`+`manufacturer:` (pin a part) and/or `package:` (add/narrow a picker constraint). `apply_parts_sidecar`
  injects the SAME traits the compiler attaches for an in-source pin (`is_pickable_by_supplier_id` /
  `is_pickable_by_part_number` / `has_package_requirements`), so a conflict surfaces via the solver and an `.ato`
  pin (`has_part_picked`) always wins (skipped). Unknown address = loud (`UserException`). Wired in `pick_parts`
  before picking; path = declared `BuildTargetPaths.parts_config` (config.py, opt-in in ato.yaml) else the default
  `<output_base>.parts.yaml`. `ato bom --pick <addr>=<lcsc>` records pins into that sidecar (atomic write, sorted)
  then resolves the BOM — `.ato` untouched. Tests: `test_parts_sidecar.py` (10: schema validators + apply for
  lcsc/mpn/package + loud-unknown + ato-pin-wins), two load-bearing guards mutation-verified. e2e: a `--no-pick`
  build with `r1` pinned in parts.yaml → r1 gets the real LCSC footprint (solver-free explicit path) while r2
  falls back to the H2 package footprint. **Follow-on**: arbitrary-parameter `constrain: {resistance: '10k ±1%'}`
  (needs the ato value/unit parser at the libs layer) and a `has_sidecar_source` provenance trait are not yet in.
- **H4 — concurrency safety (parallel place/route ‖ BOM finalize)**: prove/enforce that the artifact set is
  safe for two agents to write concurrently.
  - [x] **H4 foundation** [✅ 2026-07-03]: atomic writes + lock correctness. `util.py` `atomic_write_bytes`/
    `atomic_write_text` (temp in same dir → fsync → `os.replace`, EXDEV-safe); the board writer
    `fileformats.py::dumps` (was truncate-in-place `write_text` → a concurrent reader could see a torn/empty
    board) now routes through it. `global_lock` TOCTOU fixed: create+stamp pid in one `O_CREAT|O_EXCL` step
    (the old touch-then-write left an empty-file window during which a reader unlinked a freshly-held lock);
    same-pid re-acquire now raises instead of `assert`. `sqlite.py` adds `PRAGMA busy_timeout=30000` (WAL already
    on). Tests `test/libs/test_concurrency.py` (torn-read, crash-leaves-original, cross-process mutual exclusion);
    both load-bearing guards mutation-verified. 197 kicad/util tests green; real build writes the board atomically.
- **H5 — partial place & route (place what you can, report the rest)**: a global solve/pick is not always
  feasible, so an incomplete design must still yield a board for the resolvable subset while surfacing the rest.
  - [x] **H5 report core** [✅ 2026-07-03]: `libs/app/partial.py::collect_unresolved_modules` — every designated
    part (`has_designator_prefix`) lacking a footprint (so absent from the board), sorted, with reason
    `no-standard-footprint` (has a package but no KiCad std footprint) or `deferred-pick` (no footprint, no
    package — resolve via `ato bom`). `pick_parts` writes it to a deterministic `<output_base>.unresolved.json`
    (atomic) + a loud summary. Closes the loud-or-nothing gap where a footprint-less deferred part was silently
    missing from the board with only a soft warning (picker.py:574-576's intended-but-nonexistent loud path).
    Tests `test_partial.py` (2: reports+clears-on-attach, deferred-pick reason), guard mutation-verified. e2e:
    a `--no-pick` build with an unshipped-package r2 completes with r1 placed and r2 in unresolved.json.
  - [ ] **H5 remaining**: `apply_placements` (placement.py) tolerating a `layout.yaml`-named deferred module
    (return it in `unresolved`, do NOT raise; a typo address still raises); route-subset guard (a route stage
    whose net has no endpoints on the subset board degrades to a counted failure, not a crash) + test; feed
    unresolved into `diagnostics.json` findings (UNRESOLVED-COMPONENT) so the closed loop can act; optional
    `ingest_footprint` catch-and-continue for a missing atomic .kicad_mod, gated behind partial mode only.
  - [ ] **H4 partition + snapshot lock** (remaining): route OWNS copper (writes only under `<output_base>.route/`);
    bom/finalize OWNS picks (writes `.bom.*` + the H3 sidecar, must NOT re-enter `update_pcb`/write `paths.layout`
    or `.kicad_pro`). `ato route` should acquire `global_lock` only to snapshot `paths.layout` into its workdir
    (ms) then route the private copy. Route the other artifact writers (jlcpcb/json_bom/other_fileformats/
    layout_plan_runner) through the atomic helper. Backup names → sub-second/uuid unique (backups collide within
    one second today). Add the two-process stress harness on `examples/layout_reuse`.

### I. DRC-honest P&R: rules header + headless eyes + placement authority [✅ 2026-07-03]
Driver (user): "the DRC is obviously failing but we are cheating to say it is green" — the acceptance boards routed with
touching diff pairs, no board outline, and relic copper, and nothing looked. Landed (conclusion + pointer; code is SSOT):

- [x] **I1 diff-pair short-circuit geometry fix**: `DiffLane.gap` was applied CENTER-TO-CENTER (`bundle_geometry.cross_section_offsets`
  put P/N at ±gap/2), so gap == width meant the pair's copper edges touched — a hard short on the SATA board. Now gap = copper
  EDGE gap (pitch = gap + width), diff slot envelope = 2·width + gap. `route_bundle` self-corrects (derives pitch from nominal
  offsets). Oracles updated + a pinned edge-gap assertion (`test_bundle_contract.py`).
- [x] **I2 `rules:` header (DesignRules)**: the router cannot start with no rules — route_stages without `rules:` is loud AT PARSE.
  Rules fill stage/lane geometry defaults (track_width/clearance/diff_pair_width/diff_pair_gap; fills marked set so
  `exclude_unset` forwarding carries them) and enforce board minimums at parse (clearance floor, intra-pair gap ≥ clearance,
  trunk spacing ≥ inter_pair_clearance). "5mil"/"mm" strings accepted. DRC judges the same numbers: rules → `.kicad_pro`
  Default net class + generated `.kicad_dru` (clearance floor / courtyard component spacing / diff-pair max uncoupled /
  F1 matched-length: `rules.intra_pair_skew_max` = board-wide `inDiffPair('*')`+`within_diff_pairs` skew, per-NetClass
  `skew_max`/`intra_pair_skew_max`/`length_min`/`length_max` scoped `hasNetclass('<name>')` — ALL empirically honored by
  kicad-cli DRC on our net names, violation types `skew_out_of_range`/`length_out_of_range` pinned live).
  `layout_plan.py DesignRules`+`NetClass` + `board_rules.generate_dru_rules` + `test_design_rules_contract.py` (21) +
  `test_matched_length_rules_contract.py` (18).
- [x] **I3 `ato snapshot` (headless eyes)**: headless board PNG + DRC so the loop can SEE shorts without a human in the GUI.
  kicad-cli export svg page-size-mode 1 (absolute origin ⇒ DRC mm → px = pure scaling) → `rsvg-convert` (**ImageMagick's
  builtin SVG renderer silently DROPS KiCad tracks** — pads render, copper gone; librsvg is faithful) → PIL numbered circle
  per violation → content-crop. Stages the built board's `.kicad_pro`/`.kicad_dru`/`fp-lib-table` next to the routed board
  (DRC only honors rule files SITTING NEXT TO the board — without staging it silently judges KiCad defaults).
  `cli/snapshot.py` + `test_snapshot_contract.py`.
- [x] **I4 `board.outline` → Edge.Cuts**: was validated but NEVER drawn (boards shipped with no outline; DRC "malformed
  outline"). A declared outline is now stamped as a closed gr_line loop — the edge AUTHORITY (replace, never accrete;
  no declared outline leaves reuse edges untouched). `board_features.py` + 3 tests.
- [x] **I5 placement invalidates pulled room copper**: `apply_placements` moving a footprint orphans its room's pulled
  intra-room copper (anchored to the OLD poses ⇒ dangles off-board or shorts; empirically the 6 layout_reuse relic tracks
  ALSO made every chain net unroutable — the router must reach all of a net's copper → "no rippable blockers"). A placed
  room's copper is now derived-only: `placement.py` calls `LayoutSync.clean_room_copper` for each moved room.
  Companion S5a fix: `_sync_routes` no longer pulls an unmapped-net track as net-0 dead copper (KiCad GC's it on save
  anyway) — drop + warn. Contract updates in `test_placement_apply_contract.py` + `test_layout_sync_nets.py`.
- [x] **I6 acceptance, honestly green**: sata_bundle = 4-layer (17um Cu / 7628 0.176mm er4.6 / 1.1 core), pair geometry from
  the vendored 2D field solver (`vendor/2d_fields`, headless node CLI, W=0.2 S=0.15 ⇒ Zdiff 99.44Ω; 50Ω sanity 49.31Ω),
  placements iterated WITH the eyes: empirically pinned trunk offset + == west of southbound travel, pad 1 faces the trunk,
  both breakout rows read rx.n/rx.p/tx.n/tx.p left→right, 1.6mm pitch, uncoupled_max_length 200mil (50mil unattainable for
  a discrete 0402 breakout — ~2.3mm uncoupled per end is inherent). Result: **sata_bundle 4/4 routed, 0 DRC errors**;
  **layout_reuse 9/9 routed, 0 DRC errors, 0 unconnected** (was 57 violations). Remaining warnings = silk overlap (0402
  refdes bigger than the part) + lib_footprint_issues (DRC library config noise) — cosmetic, not copper.
- Follow-ons (not scheduled): silk refdes auto-placement (kill the silk_overlap warnings); fp-lib-table staging does not
  silence lib_footprint_issues (investigate kicad-cli library resolution); `_generate_net_map` misses that forced the old
  net-less pulls (see Outstanding issue 3 — the drop is now loud, the map miss is diagnosable from the warning).

### Side tasks (can be parallelized / do not block the critical path; deliberately not numbered in the E/F sequence)
No hard dependency on §E/§F, can be done when convenient; each keeps its original identifier (not stuffed into the E/F numbering, to avoid falsely claiming they are on the critical path).

- [x] **Agent-facing layout skill** [✅ 2026-07-02]: `.claude/skills/pcb-layout/SKILL.md`——the board-level parallel of the schematic-level `ato` skill
  (handoff after `ato` skill Step 6). Fills the long-missing **consumer-side documentation** in "the imperative feedback closed loop needs the PCB Layout SKILL to consume `diagnostics.json`":
  `layout.yaml` per-feature schema + `diagnostics.json` contract + **finding→remedy playbook**
  (`ROUTE-FAIL`/`DRC-*`, forced waypoint `corridor`, stage order=priority, `config` knobs, classes layout.yaml cannot fix). Previously the agent
  had to reverse-read `layout_plan.py`/diagnostics to change the board, counterproductive.
- [ ] **`diagnostics.json` finding `suggestions` population** (a genuine gap found when the above skill landed): `diagnostics.py`'s finding
  schema has a `suggestions` field but **all 3 `make_finding` sites leave it unfilled** (always `[]`), so machine-readable remedies do not exist, temporarily backstopped by skill §5
  human reading. Remedy = under the existing contract tests, fill `suggestions=` by finding class (`ROUTE-FAIL` blocking→reorder/corridor suggestion; `DRC-clearance`→net_class
  suggestion …), raising the closed loop from "human-read playbook" to "machine-appliable". **Must go through the 3-gate discipline** (test-first then code, mutation-verified).

- [ ] **P0.2-S7 flag-day final acceptance remainder** (write-v10 code has landed + unit/e2e determinism is green; **needs an environment that can run the full example build**——
  this repo's sandbox lacks build dependencies like router scipy, cannot verify here, so not done, must not blindly change committed input boards):
  - the v9 input boards of examples/fixtures (esp32_minimal / layout_reuse·sub / led_badge×3 / test-project×2 / faebryk example
    8 boards total; sata_bundle is already v10) one-time v9→v10 upgrade commit (load+dump is the upgrade, `kicad-cli` present); build→build→diff confirms
    incremental steady-state (fact 6, reusing `test_group_determinism.py`+`semantic_view`, currently only covers layout_reuse, needs light parameterization to the rest).
    **Do not touch** the `fileformats/kicad/v8|v9` corpus boards (they are deliberately kept old-version read-only inputs).
  - BOM / manufacturing artifacts / DRC smoke: BOM+DRC already run in the default build, mfg-data in the `all` target; all that's missing is a smoke e2e asserting
    `.bom.csv`/`.bom.json`/`.gerber.zip`/`.pick_and_place.csv` are produced and the BOM is non-empty (reusing the existing `_build` fixture).
- [x] **D5 component_class placement** ✅ SHIPPED 2026-07-03 (placement rule area from the component_class source, coexisting with the sheetname source):
  - **File grammar (KiCad-10 SSOT)**: `(placement (enabled ...) <ONE source token>)`, source token = `(sheetname "X")` | `(component_class "X")` | `(group "X")` —
    the token NAME is the source type. `(component_class "X")` is legal grammar, empirically verified safe through `kicad-cli pcb upgrade --force`
    (rc=0, verbatim round-trip; 2026-06-28), **not in** the `source_type`/`source` SEGFAULT class (those two mis-built memory/protobuf fields stay
    forever unwritten — regressions `test_generated_placement_has_no_source_type` + the D5 `test_placement_carries_exactly_one_source`).
  - **Shipped shape**: `Room.source: "sheetname" (default) | "component_class"` (layout_plan.py; the default preserves every existing plan). Class name ==
    `room.module` == the C3-stamped sheetname — ONE room identity, no second name field. `rule_area.py` emits exactly one source token per
    `room.source`; the S5a real-room check applies to both sources. Class membership rides TWO channels with distinct authority
    (empirically pinned 2026-07-03, `test_kicad_drc_enforces_component_class_headlessly`):
    - **Headless authority = static board tokens**: `board_rules.generate_component_class_membership` (called by
      `build_steps.generate_layout_plan` right after `generate_rule_areas`) stamps `(component_classes (class "<module>"))` onto every
      footprint whose sheetname == a component_class room's module. kicad-cli 10.0.3 **never** runs `BOARD::SynchronizeComponentClasses`
      on headless board load (only the GUI's `PCB_EDIT_FRAME::OpenProjectFiles` does; the CLI-path sync exists only in KiCad master's
      board_loader.cpp) — so `A.hasComponentClass(...)` DRC rules resolve ONLY through this token under `kicad-cli pcb drc`/`ato diagnose`.
      Idempotent + GC'd: ownership = a class equal to the footprint's sheetname or a dotted ancestor of its `atopile_address`
      (atopile's namespace); user static classes outside it survive union-merge.
    - **GUI mirror = the `.kicad_pro`**: `board_rules.generate_component_classes` authors one `component_class_settings` assignment per room
      (`{component_class: <module>, conditions_operator: ALL, conditions: {SHEET_NAME: {primary: <module>}}}`; JSON shape SSOT =
      KiCad `common/project/component_class_settings.cpp`, model = `other_fileformats.C_component_class_settings`, absent condition keys OMITTED never
      null — KiCad's loader throws on null), merge-preserving at two levels (only its section authored; user assignments inside the section survive).
      Atopile's own assignments are recognized by the `_atopile_authored` structural fingerprint (ALL + single SHEET_NAME whose primary == class
      name, no secondary) and garbage-collected on room rename AND on revert-to-sheetname (the zero-class-rooms path prunes instead of skipping).
      It rides the **live** `.kicad_pro` write channel §F5 opened (`build_steps.generate_layout_plan` → `generate_project_rules` +
      `generate_component_classes` → one `project.dumps`); the old premise "atopile never writes .kicad_pro" was retired by §F5
      (`set_kicad_netlist_path_in_project` remains dead code and is NOT the channel).
    F3 reverse-resolution accepts `placement.component_class` as the room key
    (`layout_ir_resolve.room_polygons_from_pcb`). The zig schema fields (`ZonePlacement.component_class`, `Footprint.component_classes`) landed as
    D5-prep (see the §P1+ fidelity entry).
  - **Tests (all mutation-verified)**: `test_rule_area_contract.py` D5.x (component_class round-trip, exactly-one-source, S5a for both sources,
    kicad-cli ingest of board+project with DRC-neutrality, and D5.3 positive headless detection: a hasComponentClass-conditioned .kicad_dru
    clearance rule FIRES on the stamped board and matches NOTHING on a .kicad_pro-only board — the pinned 10.0.3 burn),
    `test_board_rules_contract.py` D5 block (assignment emit + KiCad-exact JSON shape, no-class-rooms→None-or-prune, merge-not-clobber,
    rename GC, revert-to-sheetname prune, composition with F5 net classes),
    `test_layout_ir_resolve_contract.py::test_room_polygons_from_pcb_accepts_component_class_source`, and
    `test_component_class_e2e.py` (layout.yaml → rule area + static membership stamp + .kicad_pro on a synthetic board, the unit-level chain
    proof; stamping idempotence/union-merge/ghost-GC/S5a).

### G. Won't do / deferred
- ❌ **hack uuid = absolutely forbidden** (inviolable principle): uuid is a 128-bit opaque id, atopile **neither writes
  any non-standard content into it nor reads any flag from it**. The old FBRK uuid side channel written by `gen_uuid(mark="FBRK")` + read by `is_marked`
  **is thoroughly deleted from the code** (`fileformats.py gen_uuid` has no mark parameter, pure uuid4;
  `transformer.py` `gen_uuid`/`is_marked`/`_add_group` are cleared). provenance/ownership goes only through legal carriers:
  footprint `atopile_address` property (managed determination) + `FBRK:notouch` fp_text (user lock).
  *(This is the endgame of the original "outstanding issue 3 gen_uuid variable-length-mark overflow": root cause = stuffing metadata into uuid, root fix = don't stuff,
  so the whole item is moved from outstanding issues to here as a resident invariant.)*
  - **Corollary (byte equivalence must not be desired)**: uuid opaque ⟹ two builds can be byte-different yet semantically identical ⟹ **any
    test asserting byte equivalence is a category error and must be changed to semantic equivalence** (`semantic_view`: position+net-name connectivity+structure,
    stripping uuid/net numbering). Already changed the three byte assertions in `test_group_determinism.py` + `test_room_migration_e2e.py::test_C3_3`.
    **Further corollary**: a generated `.kicad_pcb` need not be committed as a byte anchor——CI can build from scratch twice and compare semantic;
    only **input-state** layouts (`examples/layout_reuse/.../sub.kicad_pcb`) + parser corpus samples are still committed.
    Residual: `semantic_view` group members are still expressed by uuid, the full oracle should be changed to by member address (not scheduled).
- ❌ fork KiCad 10; ❌ old SWIG binding; ❌ **v9 write-out / bidirectional dialect shim** (v10-only decision);
  ❌ blind random syntax fuzz (the dangerous bug class = "legal file silently mis-bound", covered by corrupter + property tests);
  ❌ KiCad-native design block library; ❌ calling GUI Repeat Layout.
- ⏸ clean-checkout reproducibility (incremental steady-state is enough, fact 6); ⏸ submitting a DRC JSON enhancement patch to KiCad upstream;
  ⏸ changing `.ato` syntax (layout goes through sidecar).
- **§P1+ GUI advanced routing-construct fidelity** — padstack + teardrop parts ✅ SHIPPED 2026-07-03; generated serpentine remains ⏸:
  - ✅ **padstack + teardrop + via treatments** are schema-complete against the KiCad 10.0.3 formatter and lossless (parse / idempotence /
    no-data-loss / kicad-cli-DRC-readable-after-rewrite): via padstack (keyed `(mode ...)`, positional layer name, single-scalar size),
    pad padstack (per-layer shape/size/offset/rect_delta/roundrect/chamfer/thermal/clearance/zone_connect/options/primitives),
    pad + via teardrops (KiCad emission order), via blind/buried/micro tokens, start_end_only, backdrill/tertiary_drill,
    front/back_post_machining, capping/covering/plugging/filling, tri-state FormatOptBool (`yes|no|none`, none = unspecified ≠ no),
    present-but-empty `(zone_layer_connections)`. Tests: `test_padstack_dialect.py`; corpus fixtures `padstacks_complex` /
    `teardrop_elongated_pad` / `two_segment_teardrop` / `via_treatments` (v10/README.md). Once got burned: a real v10 via padstack was a
    **hard board-blocking MissingField parse error** (the old schema modeled KiCad's in-memory PADSTACK — Xy size + thermal_* — instead of
    the file grammar, the ZonePlacement.source_type category error again), not the S5a warning this entry used to claim.
    The pre-v9 legacy teardrop `(curve_points N)` token stays S5a-loud by decision (outside the v9/v10 read scope).
    semantic_view / GUI-edit fidelity-set promotion of these constructs is owned by a follow-up (semantic_view still omits them).
  - ⏸ **generated serpentine length-matching**: `Generated` still drops the ~19 tuning-pattern properties (S5a-loud, warning set);
    kicad-cli DRC cannot detect that loss (KiCad's parser treats them as an open property map) — needs its own schema pass.

## Outstanding issues (unfinished / hardening items)
1. **net name drift** (auto-numbered `unnamed[N]`, fact 8): under v10 the name = unique key, drift = geometry-ownership drift.
   Already welded into B's I4 (a net with no stable address is loudly flagged and cannot be referenced) + acceptance "all net names byte-stable under steady-state rebuild".
2. Whether to submit a DRC JSON enhancement patch to KiCad upstream (see §G).
3. **`_generate_net_map` tie-breaking dead code** (`layout_sync.py:183`, function definition `:116`):
   `mapping_counts[src][tgt] > max(values())` is always false after self-increment → the comment says "most-frequent mapping" but it is actually "first wins".
   Currently the pull iteration order is deterministic so it does not manifest, but it is a dormant hazard under ambiguous mapping. Fix: on a tie take the lexicographically smallest tgt + iterate sorted by
   src_addr/pad. **Note**: B/C's consumer-oracle takes the in-service `_generate_net_map` as ground truth; hardening the behavior here
   must synchronously update the oracle expectations (the two are bound).
