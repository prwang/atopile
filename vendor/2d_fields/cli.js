#!/usr/bin/env node
/**
 * Headless CLI for the vendored js_2d_fields 2D transmission-line field solver.
 *
 * Computes quasi-static impedance for (coupled) microstrip cross-sections
 * using the upstream FDM solver (src/microstrip.js -> src/field_solver.js),
 * with no browser or user interaction required.
 *
 * Usage (all dimensions in mm unless noted):
 *   node cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.30              # single-ended
 *   node cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.30 --s 0.20     # edge-coupled (diff)
 *   node cli.js --h 0.176 --er 4.6 --t 0.017 --w 0.25,0.30 --s 0.15,0.20   # sweep (cartesian)
 *   node cli.js --json '{"substrate_height":0.176e-3,...}'         # raw solver options (SI units)
 *
 * Options:
 *   --h <mm>       dielectric height between trace and reference plane (required)
 *   --er <x>       substrate relative permittivity (required)
 *   --t <mm>       trace copper thickness (default 0.017)
 *   --tgnd <mm>    reference plane copper thickness (default = --t)
 *   --w <mm[,mm]>  trace width(s) (required)
 *   --s <mm[,mm]>  edge-to-edge gap(s); presence enables coupled/differential mode
 *   --tand <x>     substrate loss tangent (default 0.02)
 *   --freq <Hz>    analysis frequency (default 1e9)
 *   --sm           enable solder mask (default off = solder-mask-free)
 *   --nx/--ny <n>  initial coarse mesh (default 10; adaptive refinement follows)
 *   --tol <x>      adaptive energy tolerance (default 0.005)
 *
 * Output: JSON to stdout. Single point -> object; sweep -> {results: [...]}.
 * Fields: Z0 (single-ended) or Z_odd/Z_even/Z_diff/Z_common, eps_eff, C/L per mode.
 */
import { MicrostripSolver } from './src/microstrip.js';

function parseArgs(argv) {
    const args = {};
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (!a.startsWith('--')) continue;
        const key = a.slice(2);
        if (key === 'sm') { args.sm = true; continue; }
        args[key] = argv[++i];
    }
    return args;
}

function mmList(s) {
    return String(s).split(',').map(v => parseFloat(v) * 1e-3);
}

async function solveOne(options) {
    const solver = new MicrostripSolver(options);
    // Upstream solver logs convergence info via console.log; keep stdout
    // clean for JSON by diverting logs to stderr during the solve.
    const origLog = console.log;
    console.log = console.error;
    let results;
    try {
        results = await solver.solve_adaptive({ energy_tol: options.energy_tol ?? 0.005 });
    } finally {
        console.log = origLog;
    }

    const out = {
        input: {
            substrate_height_mm: options.substrate_height * 1e3,
            epsilon_r: options.epsilon_r,
            trace_width_mm: options.trace_width * 1e3,
            trace_thickness_mm: options.trace_thickness * 1e3,
            gnd_thickness_mm: options.gnd_thickness * 1e3,
            trace_spacing_mm: options.trace_spacing ? options.trace_spacing * 1e3 : null,
            coupled: !!options.trace_spacing,
            solder_mask: !!options.use_sm,
            tan_delta: options.tan_delta,
            freq_Hz: options.freq,
        },
    };

    if (options.trace_spacing) {
        const odd = results.modes.find(m => m.mode === 'odd');
        const even = results.modes.find(m => m.mode === 'even');
        out.Z_odd = odd.Z0;
        out.Z_even = even.Z0;
        out.Z_diff = results.Z_diff;
        out.Z_common = results.Z_common;
        out.eps_eff_odd = odd.eps_eff;
        out.eps_eff_even = even.eps_eff;
    } else {
        const mode = results.modes[0];
        out.Z0 = mode.Z0;
        out.eps_eff = mode.eps_eff;
        out.C_pF_per_m = mode.RLGC.C * 1e12;
        out.L_nH_per_m = mode.RLGC.L * 1e9;
    }
    return out;
}

async function main() {
    const args = parseArgs(process.argv.slice(2));

    if (args.json) {
        // Raw pass-through: full upstream MicrostripSolver options in SI units.
        const options = JSON.parse(args.json);
        options.nx = options.nx ?? 10;
        options.ny = options.ny ?? 10;
        const out = await solveOne(options);
        process.stdout.write(JSON.stringify(out, null, 2) + '\n');
        return;
    }

    if (!args.h || !args.er || !args.w) {
        console.error('Required: --h <mm> --er <x> --w <mm>. See header of cli.js for usage.');
        process.exit(2);
    }

    const t = args.t !== undefined ? parseFloat(args.t) * 1e-3 : 17e-6;
    const tgnd = args.tgnd !== undefined ? parseFloat(args.tgnd) * 1e-3 : t;
    const widths = mmList(args.w);
    const gaps = args.s !== undefined ? mmList(args.s) : [null];

    const base = {
        substrate_height: parseFloat(args.h) * 1e-3,
        epsilon_r: parseFloat(args.er),
        trace_thickness: t,
        gnd_thickness: tgnd,
        tan_delta: args.tand !== undefined ? parseFloat(args.tand) : 0.02,
        sigma_cond: 5.8e7,
        freq: args.freq !== undefined ? parseFloat(args.freq) : 1e9,
        nx: args.nx !== undefined ? parseInt(args.nx) : 10,
        ny: args.ny !== undefined ? parseInt(args.ny) : 10,
        use_sm: !!args.sm,
        energy_tol: args.tol !== undefined ? parseFloat(args.tol) : 0.005,
        boundaries: ['open', 'open', 'open', 'gnd'],
    };

    const points = [];
    for (const w of widths) {
        for (const s of gaps) {
            const options = { ...base, trace_width: w };
            if (s !== null) options.trace_spacing = s;
            points.push(await solveOne(options));
        }
    }

    const out = points.length === 1 ? points[0] : { results: points };
    process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

main().catch(err => {
    console.error(err.stack || String(err));
    process.exit(1);
});
