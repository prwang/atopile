# Vendored: js_2d_fields

- Upstream URL: https://github.com/Ttl/js_2d_fields
- Author: Henrik Forsten (Ttl)
- Vendored commit: `bd27c5c46e64f2bde600d55b6e8dc5e9da755591`
  ("Fix C0 calculation for asymmetric geometry", 2026-06-13)
- License: GPL-3.0 (see `LICENSE`)
- Vendored on: 2026-07-03

## What it is

A quasi-static 2D FDM field solver for PCB transmission lines (microstrip,
stripline, GCPW; single-ended and edge-coupled differential). Geometry is
rasterized onto an adaptive non-uniform grid (`src/mesher.js`), the Laplace
equation is solved for the potential (`src/field_solver.js`, sparse linear
solve via the bundled Eigen/WASM module in `src/wasm_solver/`), and
capacitance / RLGC / impedance are extracted from the field energy. For
coupled lines the odd and even modes are solved separately to yield
Z_odd / Z_even / Z_diff / Z_common.

## Local modifications

- Removed `.git` (source is vendored, not a submodule).
- Added `cli.js`: headless CLI entry point (node, no browser). See its
  header comment for usage. All other upstream files are unmodified.
- Added `RESULTS.md` (100 Ohm differential pair design for a JLC-style
  4-layer stackup) and this `VENDOR.md`.

Note: the `src/wasm_solver/eigen` git submodule (Eigen headers, only needed
to rebuild `solver.wasm`) is not vendored; the prebuilt `solver.wasm` is
included upstream and works as-is under node.

## Headless verification

Upstream reference tests (validated against HFSS) run headless:

```
node tests/test_vs_ref.js
```
