# KiCad v10 dialect corpus (`(version 20260206)`)

Paired with the same-stem files in `../v9/pcb/` for cross-dialect equivalence
tests. Generated 2026-06-12 with kicad-cli 10.0.3:

| file | origin | v9 pair |
|---|---|---|
| `pcb/test.kicad_pcb` | `kicad-cli pcb upgrade --force` of `v9/pcb/test.kicad_pcb` | yes |
| `pcb/layout_reuse_top.kicad_pcb` | upgrade of an atopile-built `examples/layout_reuse` top board (contains atopile groups, placement rule area, manual segments) | yes |
| `pcb/interf_u_unrouted.kicad_pcb` | upgrade of KiCadRoutingTools `kicad_files/interf_u_unrouted.kicad_pcb` (real-world KiCad-authored board, many nets) | yes |
| `pcb/lvds_converter_dualclk.kicad_pcb` | copied verbatim from KiCadRoutingTools — **natively saved by KiCad 10**, not an upgrade artifact | no (no v9 original exists) |

Key v10 dialect properties these files exhibit (vs v9):

- **No top-level net table at all.** Nets exist only as `(net "<name>")`
  references at point of use (pads, segments, vias, zones). Numbers are gone
  from the file entirely; zones lost the redundant `(net_name ...)` field;
  net-less zones (keepouts) simply omit the net clause.
- Pad-surface handling fields are nested: `(tenting front back)` →
  `(tenting (front yes) (back yes))` — applies to via/pad padstacks.
- Assorted new/changed keys (plot params, `duplicate_pad_numbers_are_jumpers`,
  `locked` removal, layer id renumbering, ...). The authoritative inventory is
  whatever the data-loss tests in `test/libs/kicad/test_fileformats_corpus.py`
  report — not this README.

Excluded for repo size (regenerate locally if needed):
KiCadRoutingTools boards `flat_hierarchy` (807K), `kit-dev-coldfire-xilinx_5213`
(2.3M), `sonde_u`, `haasoscope_pro_max_test`, and `lvds_converter_dualclk_gnd`
(native v10, near-duplicate of the included lvds board). Command:
`kicad-cli pcb upgrade --force <copy-of-board>.kicad_pcb`
(note: upgrade also emits a stray `.kicad_prl` next to the file — discard it).
