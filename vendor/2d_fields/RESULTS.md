# 100 Ohm Differential Pair — JLC-style 4-Layer Stackup

Computed with the vendored js_2d_fields quasi-static 2D FDM field solver
(see `VENDOR.md`), driven headless via `cli.js` under node.

## Stackup (~1.6 mm total, all copper 17 um / 0.5 oz)

| # | Layer            | Material          | Thickness | er  |
|---|------------------|-------------------|-----------|-----|
| 1 | L1 Top signal    | Copper            | 0.017 mm  | —   |
| 2 | Dielectric       | 7628 prepreg      | 0.176 mm  | 4.6 |
| 3 | L2 Ground1 plane | Copper            | 0.017 mm  | —   |
| 4 | Dielectric       | FR4 core          | 1.100 mm  | 4.6 |
| 5 | L3 Ground2 plane | Copper            | 0.017 mm  | —   |
| 6 | Dielectric       | 7628 prepreg      | 0.176 mm  | 4.6 |
| 7 | L4 Bottom signal | Copper            | 0.017 mm  | —   |

Total: 1.520 mm dielectric + copper ≈ 1.57 mm (solder mask extra).

## Result: edge-coupled microstrip on L1, referenced to L2

Model: substrate 0.176 mm, er 4.6, tan d 0.02; trace and plane copper
17 um; air above (no solder mask); f = 1 GHz; adaptive mesh,
energy tolerance 0.002.

**Chosen geometry:**

| Parameter | mm      | mil   |
|-----------|---------|-------|
| Width W   | 0.20 mm | 7.87  |
| Gap S     | 0.15 mm | 5.91  |

**Solver-computed impedances at chosen (W, S):**

| Quantity  | Value    |
|-----------|----------|
| Z_odd     | 49.72 Ω  |
| Z_even    | 71.46 Ω  |
| Z_diff    | 99.44 Ω  |
| Z_common  | 35.73 Ω  |
| eps_eff (odd / even) | 2.838 / 3.512 |

Error vs. 100 Ω target: −0.6 %.

**Nearby sweep points (same solver settings):**

| W (mm) | S (mm) | Z_odd | Z_even | Z_diff |
|--------|--------|-------|--------|--------|
| 0.18   | 0.13   | 50.48 | 76.48  | 100.96 |
| 0.18   | 0.15   | 51.24 | 74.71  | 102.48 |
| 0.20   | 0.13   | 48.31 | 72.49  | 96.62  |
| **0.20** | **0.15** | **49.72** | **71.46** | **99.44** |
| 0.20   | 0.17   | 50.91 | 70.55  | 101.82 |
| 0.22   | 0.15   | 47.67 | 68.00  | 95.35  |

Both W and S satisfy the manufacturability floor (>= 0.09 mm).

Note: solder mask is not modeled (solder-mask-free approximation). A
typical ~20 um LPI mask lowers Z_diff by roughly 3-5 Ω; if the board is
mask-covered, nudge toward W = 0.18 mm / S = 0.15 mm (Z_diff 102.5 Ω bare).

## Sanity check (single-ended 50 Ω)

Single-ended microstrip on the same substrate (0.176 mm, er 4.6):
W = 0.30 mm gives Z0 = 49.3 Ω (expected ~0.3 mm for 50 Ω — within 2 %).

```
node cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.30
```

## Reproduce

From `vendor/2d_fields`:

```
node cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.20 --s 0.15 --tol 0.002
```

Sweep (comma lists take the cartesian product):

```
node cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.18,0.20,0.22 --s 0.13,0.15,0.17 --tol 0.002
```

Output is JSON on stdout (`Z_odd`, `Z_even`, `Z_diff`, `Z_common`,
`eps_eff_odd`, `eps_eff_even`); solver convergence logs go to stderr.

## KiCad board stackup snippet

Body of the `(stackup ...)` s-expression inside `(setup ...)` of the
`.kicad_pcb` file (4-layer board, thicknesses per the table above):

```lisp
(stackup
  (layer "F.SilkS" (type "Top Silk Screen"))
  (layer "F.Paste" (type "Top Solder Paste"))
  (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
  (layer "F.Cu" (type "copper") (thickness 0.017))
  (layer "dielectric 1" (type "prepreg") (thickness 0.176) (material "7628") (epsilon_r 4.6) (loss_tangent 0.02))
  (layer "In1.Cu" (type "copper") (thickness 0.017))
  (layer "dielectric 2" (type "core") (thickness 1.1) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
  (layer "In2.Cu" (type "copper") (thickness 0.017))
  (layer "dielectric 3" (type "prepreg") (thickness 0.176) (material "7628") (epsilon_r 4.6) (loss_tangent 0.02))
  (layer "B.Cu" (type "copper") (thickness 0.017))
  (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
  (layer "B.Paste" (type "Bottom Solder Paste"))
  (layer "B.SilkS" (type "Bottom Silk Screen"))
  (copper_finish "HAL lead-free")
  (dielectric_constraints no)
)
```
