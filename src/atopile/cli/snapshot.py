# pylint: disable=logging-fstring-interpolation
"""`ato snapshot` — headless board picture + DRC (the diagnosis EYES).

The diagnostics closed loop (build → route → diagnose → edit plan → rebuild)
needs an agent to SEE the copper without a human driving the KiCad GUI: a short
circuit is obvious in one glance at the board and invisible in a route report
that says "16 segments, 0 failed". This command renders the board headlessly and
runs DRC, so the loop can iterate placement/rules until the board is actually
clean — never "green because nobody looked".

Pipeline (all headless, empirically verified):
  * `kicad-cli pcb export svg --mode-single --page-size-mode 1` — page mode 1
    keeps the SVG origin at the board-absolute (0,0) with width/height in mm, so
    DRC coordinates map to pixels by pure scaling (mode 2 crops to content and
    LOSES the absolute origin — unusable for overlay).
  * rasterize via `rsvg-convert` (ImageMagick's builtin SVG renderer silently
    drops most of KiCad's SVG — pads rendered, tracks gone; librsvg is faithful).
  * `kicad-cli pcb drc --format json --severity-all --units mm` next to it; every
    violation is marked on the image (numbered circle at its position) and
    printed with its description + position.
  * crop to the content bounding box (plus margin) AFTER marking, so the marks
    survive and the picture is actually zoomed on the board, not an A4 page.
  * the F2-lane net-length report (`libs.kicad.length_report`) is written as
    `<out_base>.lengths.json` (per-net track_mm / via_count / segment_count,
    pair skews, netclass spread from the staged .kicad_pro) and each pair is
    logged as a compact table — the numbers an agent tuning routed lengths
    reads INSIDE the snapshot loop. track_mm is routed copper centerline only
    (KiCad DRC length rules additionally count via Z-length and pad-to-die;
    DRC remains the authority).

Loud-or-nothing: a missing board, a failing kicad-cli, or a missing rasterizer
is a hard error with the install hint — never a silently absent picture.
"""

import contextlib
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from atopile.errors import UserResourceException

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

_BACKGROUND = "#001023"  # dark blue-black, KiCad-editor-like
_COPPER_ORDER = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, 31)] + ["B.Cu"]


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise UserResourceException(
            f"{what} failed (rc={proc.returncode}):\n"
            f"$ {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def board_copper_layers(board: Path) -> list[str]:
    """The copper layers present in the board's layer TABLE, in stack order.
    Scans only the top-level `(layers ...)` header block — a whole-file scan
    false-positives on the `(stackup ...)` section, which names inner layers
    even on a 2-layer board (seen live: a 2-layer fixture rendered as 4-layer,
    and kicad-cli silently exports NOTHING for a nonexistent layer)."""
    text = board.read_text(encoding="utf-8", errors="replace")
    start = text.find("\n\t(layers\n")
    if start != -1:
        end = text.find("\n\t)", start)
        if end != -1:
            text = text[start:end]
    return [name for name in _COPPER_ORDER if f'"{name}"' in text]


def run_drc(board: Path, out_json: Path) -> dict:
    """kicad-cli DRC → parsed report dict (written to out_json). Uses mm and all
    severities; honors the project's .kicad_pro / .kicad_dru next to the board.
    `--refill-zones`: build-authored pours are `(fill yes)` zones WITHOUT
    stored fill polygons (board_features.py) and the router round-trips them
    verbatim — judging the stored (empty) fill falsely reports every
    plane-only-connected pad as unconnected. Connectivity is judged on the
    refilled state; the board file itself is not touched (no --save-board)."""
    _run(
        [
            "kicad-cli", "pcb", "drc",
            "--format", "json",
            "--severity-all",
            "--units", "mm",
            "--refill-zones",
            "-o", str(out_json),
            str(board),
        ],
        "kicad-cli pcb drc",
    )
    return json.loads(out_json.read_text())


def drc_violation_marks(drc: dict) -> list[tuple[float, float, str]]:
    """(x_mm, y_mm, label) for every violation item position in the DRC report.
    One mark per violation (its first item's position), labelled by index into
    the printed summary."""
    marks: list[tuple[float, float, str]] = []
    idx = 0
    for kind in ("violations", "unconnected_items"):
        for v in drc.get(kind, []):
            idx += 1
            items = v.get("items") or []
            if not items:
                continue
            pos = items[0].get("pos") or {}
            marks.append((float(pos.get("x", 0)), float(pos.get("y", 0)), str(idx)))
    return marks


def summarize_drc(drc: dict) -> list[str]:
    """Human/agent-readable per-violation lines, numbered to match the marks."""
    lines: list[str] = []
    idx = 0
    for kind in ("violations", "unconnected_items"):
        for v in drc.get(kind, []):
            idx += 1
            items = v.get("items") or []
            pos = (items[0].get("pos") or {}) if items else {}
            at = f"({pos.get('x', '?')}, {pos.get('y', '?')})mm"
            lines.append(
                f"  [{idx}] {v.get('severity', '?')} {v.get('type', kind)}: "
                f"{v.get('description', '')} @ {at}"
            )
    return lines


def _rasterize_layers(
    board: Path, layers: list[str], out_png: Path, ppmm: float
) -> float:
    """kicad-cli SVG (single-mode, absolute page origin) → transparent-background
    RGBA PNG for one layer group. Returns the page width in mm (the mm→px
    authority, parsed from the SVG header — loud if the header changes)."""
    import re

    svg_path = out_png.with_suffix(".svg")
    _run(
        [
            "kicad-cli", "pcb", "export", "svg",
            "--mode-single",
            "--page-size-mode", "1",  # absolute origin: mm → px is pure scaling
            "--exclude-drawing-sheet",
            "--layers", ",".join(layers),
            "-o", str(svg_path),
            str(board),
        ],
        "kicad-cli pcb export svg",
    )
    if not svg_path.exists():
        raise UserResourceException(
            f"kicad-cli export svg produced no file for layers {layers} — it "
            "silently no-ops (rc=0) when a requested layer does not exist on "
            "the board (loud-or-nothing)"
        )
    head = svg_path.read_text(encoding="utf-8", errors="replace")[:2000]
    m = re.search(r'width="([0-9.]+)mm" height="([0-9.]+)mm"', head)
    if not m:
        raise UserResourceException(
            f"cannot parse page size from {svg_path} — kicad-cli SVG header "
            "changed; the mm→px mapping would be a guess (loud-or-nothing)"
        )
    _run(
        [
            "rsvg-convert",
            "--dpi-x", str(ppmm * 25.4),
            "--dpi-y", str(ppmm * 25.4),
            "-o", str(out_png),
            str(svg_path),
        ],
        "rsvg-convert",
    )
    svg_path.unlink()  # the SVG is an intermediate, not a deliverable
    return float(m.group(1))


_INNER_LAYER_ALPHA = 0.45  # inner (plane) copper dimming in the composite


def render_board_png(
    board: Path,
    out_png: Path,
    *,
    layers: list[str] | None = None,
    ppmm: float = 20.0,
    marks: list[tuple[float, float, str]] | None = None,
    crop_margin_mm: float = 3.0,
) -> Path:
    """Render the board to a cropped PNG with DRC marks; see module docstring.

    Default compositing (layers=None): INNER copper layers are drawn first and
    DIMMED (alpha 0.45), then B.Cu, F.Cu and Edge.Cuts opaque on top — the
    eyes exist to show SIGNALS; a full-board plane fill painted opaque over
    the outer layers hid every track (seen live on a 4-layer pour board).
    kicad-cli paints --layers in list order with no translucency, so the
    grouping is done here. NB the z-order is presentation, not physics: B.Cu
    is drawn above the planes (and below F.Cu). An explicit `layers` list
    bypasses grouping entirely (single pass, listed order)."""
    from PIL import Image, ImageChops, ImageDraw

    if not board.exists():
        raise UserResourceException(f"missing board {board} — run `ato build` first")
    if shutil.which("rsvg-convert") is None:
        raise UserResourceException(
            "rsvg-convert not found — install librsvg2-bin (ImageMagick's builtin "
            "SVG renderer silently drops KiCad tracks, so it is not a fallback)"
        )
    if layers is None:
        copper = board_copper_layers(board)
        inner = [name for name in copper if name not in ("F.Cu", "B.Cu")]
        outer = [name for name in ("B.Cu", "F.Cu") if name in copper]
        groups: list[tuple[list[str], float]] = []
        if inner:
            groups.append((inner, _INNER_LAYER_ALPHA))
        groups.append((outer + ["Edge.Cuts"], 1.0))
    else:
        groups = [(layers, 1.0)]

    page_w_mm = 0.0
    composite: "Image.Image | None" = None
    for i, (group, alpha) in enumerate(groups):
        group_png = Path(f"{out_png}.layer{i}.png")
        page_w_mm = _rasterize_layers(board, group, group_png, ppmm)
        layer_im = Image.open(group_png).convert("RGBA")
        group_png.unlink()
        if alpha < 1.0:
            a = layer_im.getchannel("A").point(lambda v: int(v * alpha))
            layer_im.putalpha(a)
        if composite is None:
            composite = Image.new("RGBA", layer_im.size, _BACKGROUND)
        composite.alpha_composite(layer_im)
    assert composite is not None  # ≥ 1 group by construction
    im = composite.convert("RGB")
    scale = im.size[0] / page_w_mm  # px per mm (uniform: same dpi both axes)

    draw = ImageDraw.Draw(im)
    r = max(6.0, 1.0 * scale)  # ≥ 1 mm radius, visible at any zoom
    for x_mm, y_mm, label in marks or []:
        x, y = x_mm * scale, y_mm * scale
        draw.ellipse([x - r, y - r, x + r, y + r], outline="#ffd500", width=3)
        draw.text((x + r + 2, y - r), label, fill="#ffd500")

    # crop AFTER marking (marks count as content, and stay in the picture)
    bg = Image.new("RGB", im.size, _BACKGROUND)
    bbox = ImageChops.difference(im, bg).getbbox()
    if bbox is not None:
        mpx = int(crop_margin_mm * scale)
        bbox = (
            max(0, bbox[0] - mpx),
            max(0, bbox[1] - mpx),
            min(im.size[0], bbox[2] + mpx),
            min(im.size[1], bbox[3] + mpx),
        )
        im = im.crop(bbox)
    im.save(out_png)
    return out_png


def lengths_table_lines(report: dict) -> list[str]:
    """The compact per-pair table (net names, track_mm, skew) logged with every
    snapshot — pairs are where length symmetry actually matters, so they are
    surfaced in the loop's log, not only in the JSON artifact."""
    lines: list[str] = []
    for base in sorted(report.get("pairs", {})):
        pair = report["pairs"][base]
        lines.append(
            f"  pair {base}: P({pair['p_net']}) {pair['p_track_mm']:.3f}mm / "
            f"N({pair['n_net']}) {pair['n_track_mm']:.3f}mm "
            f"skew {pair['skew_mm']:.3f}mm"
        )
    return lines


def write_board_lengths(board: Path, out_base: Path) -> dict:
    """Compute the F2-lane net-length report for `board` and write it as
    `<out_base>.lengths.json` (next to the PNG); returns the report. Netclass
    aggregation rides on the `.kicad_pro` staged next to the board."""
    from faebryk.libs.kicad.length_report import length_report_for_board

    report = length_report_for_board(board)
    Path(f"{out_base}.lengths.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return report


@contextlib.contextmanager
def _refilled_render_source(board: Path):
    """Yield the board to RENDER: if it carries `(fill yes)` zones, a temp
    copy with the fills actually computed and saved (kicad-cli drc
    --refill-zones --save-board — there is no standalone fill command), else
    the board itself. The eyes must show the copper DRC judged (run_drc
    judges the refilled state), but the input board is never mutated — raw
    mode may point at a user's hand-authored file, and --save-board would
    also silently rewrite/upgrade it with kicad-cli's formatter."""
    if "(fill yes" not in board.read_text(encoding="utf-8", errors="replace"):
        yield board
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        copy = Path(tmpdir) / board.name
        shutil.copyfile(board, copy)
        _run(
            [
                "kicad-cli", "pcb", "drc",
                "--format", "json",
                "--refill-zones",
                "--save-board",
                "-o", str(Path(tmpdir) / "refill_drc.json"),
                str(copy),
            ],
            "kicad-cli pcb drc (zone refill for render)",
        )
        yield copy


def _stage_project_sidecars(board: Path, project_src: Path | None) -> None:
    """kicad-cli DRC only honors design rules from `<board>.kicad_pro` /
    `<board>.kicad_dru` SITTING NEXT TO the board. A routed board lives in the
    route workdir without them — silently judging KiCad defaults instead of the
    authored rules. Copy them alongside (renamed to the board's basename)."""
    if project_src is None:
        return
    for suffix in (".kicad_pro", ".kicad_dru"):
        src = project_src.with_suffix(suffix)
        dst = board.with_suffix(suffix)
        if src.exists() and src != dst:
            shutil.copyfile(src, dst)
    # the footprint library table too — else DRC reports every footprint as
    # "library not in current configuration" noise.
    lib_src = project_src.parent / "fp-lib-table"
    lib_dst = board.parent / "fp-lib-table"
    if lib_src.exists() and lib_src != lib_dst:
        shutil.copyfile(lib_src, lib_dst)


def snapshot_board(
    board: Path,
    out_base: Path,
    *,
    ppmm: float = 20.0,
    project_src: Path | None = None,
) -> dict:
    """DRC + annotated render + net-length report for one board. Returns the
    DRC report; writes `<out_base>.drc.json`, `<out_base>.lengths.json` and
    `<out_base>.png`. `project_src` is the BUILT board path whose
    .kicad_pro/.kicad_dru carry the authored rules (staged next to `board` so
    DRC judges design intent, not KiCad defaults — and so the length report's
    netclass aggregation sees the authored classes)."""
    out_base.parent.mkdir(parents=True, exist_ok=True)
    _stage_project_sidecars(board, project_src)
    # append, never with_suffix: out_base may carry dots ("top.snapshot.routed")
    # and with_suffix would silently eat the last segment.
    drc = run_drc(board, Path(f"{out_base}.drc.json"))
    # CONTAINMENT: the lengths lane is optional metrology riding along with the
    # snapshot — a board it cannot honestly measure (LengthReportError, e.g. a
    # degenerate arc) must never cost the loop its EYES (the PNG + DRC summary).
    # Loud (a WARNING naming the cause), not fatal.
    from faebryk.libs.kicad.length_report import LengthReportError

    try:
        lengths = write_board_lengths(board, out_base)
    except LengthReportError as e:
        log.warning(f"length report skipped for {board.name}: {e}")
        lengths = None
    if lengths is not None:
        log.info(
            f"lengths: {len(lengths['nets'])} net(s), {len(lengths['pairs'])} "
            f"pair(s) → {out_base}.lengths.json"
        )
        for line in lengths_table_lines(lengths):
            log.info(line)
    marks = drc_violation_marks(drc)
    with _refilled_render_source(board) as render_src:
        png = render_board_png(
            render_src, Path(f"{out_base}.png"), ppmm=ppmm, marks=marks
        )
    n_v = len(drc.get("violations", []))
    n_u = len(drc.get("unconnected_items", []))
    log.info(f"snapshot: {png}")
    if n_v or n_u:
        log.warning(
            f"DRC: {n_v} violation(s), {n_u} unconnected item(s) on {board.name}"
        )
        for line in summarize_drc(drc):
            log.warning(line)
    else:
        log.info(f"DRC: clean ({board.name})")
    return drc


def snapshot(
    entry: Annotated[str | None, typer.Argument()] = None,
    build: Annotated[list[str], typer.Option("--build", "-b", envvar="ATO_BUILD")] = [],
    board: Annotated[
        Path | None,
        typer.Option(
            "--board",
            help="Snapshot this .kicad_pcb directly (skip build-config resolution).",
        ),
    ] = None,
    routed: Annotated[
        bool,
        typer.Option(
            "--routed/--built",
            help="Snapshot the ROUTED board from the last `ato route` report "
            "(default) or the raw built board.",
        ),
    ] = True,
    ppmm: Annotated[
        float, typer.Option("--ppmm", help="Render resolution, pixels per mm.")
    ] = 20.0,
):
    """Headlessly render the board to PNG and run DRC, marking every violation on
    the image — the eyes of the route/diagnose loop (no GUI, no user input)."""
    if board is not None:
        snapshot_board(board, board.with_suffix(".snapshot"), ppmm=ppmm)
        return

    from atopile.config import config

    config.apply_options(entry=entry, selected_builds=build if build else ())
    build_name = list(config.selected_builds)[0]
    with config.select_build(build_name):
        paths = config.build.paths
        output_base = paths.output_base
        target = Path(paths.layout)
        which = "built"
        report_path = output_base.with_suffix(".route_report.json")
        if routed and report_path.exists():
            final = json.loads(report_path.read_text()).get("final_board")
            if final and Path(final).exists():
                target = Path(final)
                which = "routed"
        log.info(f"Snapshotting the {which} board: {target}")
        snapshot_board(
            target,
            output_base.with_suffix(f".snapshot.{which}"),
            ppmm=ppmm,
            project_src=Path(paths.layout),
        )
