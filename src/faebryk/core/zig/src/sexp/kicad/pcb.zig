const std = @import("std");
const compat = @import("compat");
const structure = @import("../structure.zig");

const str = []const u8;

fn list(comptime T: type) type {
    return compat.DoublyLinkedList(T);
}

// Constants
// P0.2 S7 flag day (2026-06-13): write dialect is now v10. atopile writes the
// KiCad 10 format for every board; v9 is read-only (upgrade-on-write, Option B).
pub const KICAD_PCB_VERSION: i32 = 20260206;
pub const KICAD_FP_VERSION: i32 = 20260206; // Footprint version - same as PCB version
// Boards at/after this format version are READ in the v10 dialect (nested
// tenting family, name-only net references, no net table). Writing is always
// v10 since the flag day; this gate only steers the reader.
pub const KICAD_PCB_V10_MIN_VERSION: i32 = 20250000;

// Basic geometry types
pub const Xy = struct {
    x: f64,
    y: f64,

    pub const fields_meta = .{
        .x = structure.SexpField{ .positional = true },
        .y = structure.SexpField{ .positional = true },
    };
};

pub const Xyz = struct {
    x: f64,
    y: f64,
    z: f64,

    pub const fields_meta = .{
        .x = structure.SexpField{ .positional = true },
        .y = structure.SexpField{ .positional = true },
        .z = structure.SexpField{ .positional = true },
    };
};

pub const Xyr = struct {
    x: f64,
    y: f64,
    r: ?f64 = null,

    pub const fields_meta = .{
        .x = structure.SexpField{ .positional = true },
        .y = structure.SexpField{ .positional = true },
        .r = structure.SexpField{ .positional = true },
    };
};

pub const Wh = struct {
    w: f64,
    h: ?f64 = null,

    pub const fields_meta = .{
        .w = structure.SexpField{ .positional = true },
        .h = structure.SexpField{ .positional = true },
    };
};

pub const E_stroke_type = enum {
    solid,
    dash,
    dash_dot,
    dash_dot_dot,
    dot,
    default,
};

// Text and effects structures
pub const Stroke = struct {
    width: f64,
    type: E_stroke_type,
};

pub const Font = struct {
    size: Wh,
    thickness: ?f64 = null,
    bold: ?bool = null,
    italic: ?bool = null,
};

pub const Justify = struct {
    // null = center/normal
    justify1: ?E_justify = null,
    justify2: ?E_justify = null,
    justify3: ?E_justify = null,

    pub const fields_meta = .{
        .justify1 = structure.SexpField{ .positional = true },
        .justify2 = structure.SexpField{ .positional = true },
        .justify3 = structure.SexpField{ .positional = true },
    };
};

pub const Effects = struct {
    font: Font,
    hide: ?bool = null,
    justify: ?Justify = null,
};

pub const TextLayer = struct {
    layer: str,
    knockout: ?E_knockout = null,

    pub const fields_meta = .{
        .layer = structure.SexpField{ .positional = true },
        .knockout = structure.SexpField{ .positional = true },
    };
};

// Enums
pub const E_justify = enum {
    left,
    right,
    bottom,
    top,
    mirror,
};

pub const E_fill = enum {
    yes,
    no,
    none,
    solid,
};

// Text layer knockout enum
pub const E_knockout = enum {
    knockout,
};

// Paper type enum
pub const E_paper_type = enum {
    A5,
    A4,
    A3,
    A2,
    A1,
    A0,
    A,
    B,
    C,
    D,
    E,
    USLetter,
    USLegal,
    USLedger,
    Custom,
};

// Paper orientation enum
pub const E_paper_orientation = enum {
    portrait,
    landscape,
};

// Layer type enum
pub const E_layer_type = enum {
    signal,
    user,
    mixed,
    jumper,
    power,
};

// Footprint text type enum
pub const E_fp_text_type = enum {
    user,
    reference,
    value,
};

// Pad type enum
pub const E_pad_type = enum {
    thru_hole,
    smd,
    np_thru_hole, // non_plated_through_hole
    connect, // edge_connector
};

// Pad shape enum
pub const E_pad_shape = enum {
    circle,
    rect,
    oval, // stadium
    roundrect,
    custom,
    trapezoid,
    chamfered_rect,
};

// Pad clearance enum
pub const E_pad_clearance = enum {
    outline,
    convexhull,
};

// Pad anchor enum
pub const E_pad_anchor = enum {
    rect,
    circle,
};

// Pad property enum
pub const E_pad_property = enum {
    pad_prop_bga,
    pad_prop_fiducial_glob,
    pad_prop_fiducial_loc,
    pad_prop_testpoint,
    pad_prop_castellated,
    pad_prop_heatsink,
    pad_prop_mechanical,
    none,
};

// Pad chamfer corner tokens. KiCad writes (chamfer top_left [top_right ...])
// — a list of bare corner symbols (pcb_io_kicad_sexpr 10.0.3
// formatCornerProperties); the same shape appears per-layer inside a pad
// padstack.
pub const E_pad_chamfer_corner = enum {
    top_left,
    top_right,
    bottom_left,
    bottom_right,
};

// Pad drill shape enum
pub const E_pad_drill_shape = enum {
    circle,
    oval, // stadium

    pub const fields_meta = .{
        .circle = .{ .sexp_name = "" },
        .oval = .{ .sexp_name = "oval" },
    };
};

// Tenting (and friends) for setup/pad/via. Two serialized shapes:
//   v9:  (tenting front back)            — bare presence symbols
//   v10: (tenting (front yes) (back none)) — nested tri-state key-values
// Reading accepts both; writing follows structure.write_dialect (dual_bool).
// front/back are tri-state (KiCad FormatOptBool: yes | no | none, where
// "none" = unspecified/inherit ≠ "no"); null maps to the none token and the
// whole block is omitted when both sides are null, exactly like KiCad's
// has_value() gate. The bare "none" field is the v9 pad-level explicit
// opt-out token and is never written in the v10 dialect (v9_only).
pub const Tenting = struct {
    front: ?bool = null,
    back: ?bool = null,
    none: bool = false,

    pub const fields_meta = .{
        .front = structure.SexpField{ .dual_bool = true },
        .back = structure.SexpField{ .dual_bool = true },
        .none = structure.SexpField{ .dual_bool = true, .v9_only = true },
    };
};

// Zone connection enum
pub const E_zone_connection = enum(i32) {
    INHERITED = -1,
    NONE = 0,
    THERMAL = 1,
    FULL = 2,
    THT_THERMAL = 3,
};

// Padstack mode enum
pub const E_padstack_mode = enum {
    front_inner_back,
    custom,
};

// Zone hatch mode enum
pub const E_zone_hatch_mode = enum {
    edge,
    full,
    none,
};

// Zone connect pads mode enum
pub const E_zone_connect_pads_mode = enum {
    no, // none
    yes, // solid
    thermal_reliefs, // empty string in KiCad
    thru_hole_only,
};

// Zone fill mode enum
pub const E_zone_fill_mode = enum {
    hatch,
    polygon,
};

// Zone smoothing enum
pub const E_zone_smoothing = enum {
    fillet,
    chamfer,
    none, // empty string in KiCad
};

// Zone hatch border algorithm enum
pub const E_zone_hatch_border_algorithm = enum {
    hatch_thickness,
    min_thickness, // empty string in KiCad
};

// Zone island removal mode enum
pub const E_zone_island_removal_mode = enum(i32) {
    always = 0,
    do_not_remove = 1,
    below_area_limit = 2,
};

// Zone keepout enum
pub const E_zone_keepout = enum {
    allowed,
    not_allowed,
};

// Zone placement source type enum
pub const E_zone_placement_source_type = enum {
    sheetname,
    component_class,
};

// Zone teardrop type enum
pub const E_zone_teardrop_type = enum {
    padvia,
    track_end,
};

// Dimension type enum
pub const E_dimension_type = enum {
    aligned,
    orthogonal,
    leader,
    center,
    radial,
};

// Dimension arrow direction enum
pub const E_dimension_arrow_direction = enum {
    inward,
    outward,
};

// Stackup copper finish enum
pub const E_copper_finish = enum {
    ENIG,
    ENEPIG,
    @"HAL SnPb",
    @"HAL lead-free",
    @"Hard Gold",
    @"Immersion tin",
    @"Immersion silver",
    @"Immersion nickel",
    @"Immersion gold",
    OSP,
    HT_OSP,
    None,
    @"User defined",
};

// Stackup edge connector enum
pub const E_edge_connector = enum {
    bevelled,
    yes,
};

// Embedded file type enum
pub const E_embedded_file_type = enum {
    other,
    model,
    font,
    datasheet,
    worksheet,
};

// Shapes

pub const Line = struct {
    start: Xy,
    end: Xy,

    // shape common
    solder_mask_margin: ?f64 = null,
    stroke: ?Stroke = null,
    fill: ?E_fill = null,
    layer: ?str = null,
    layers: list(str) = .{},
    locked: ?bool = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .start = structure.SexpField{ .order = -2 },
        .end = structure.SexpField{ .order = -1 },
        .uuid = structure.SexpField{ .order = 100 },
    };
};

pub const Arc = struct {
    start: Xy,
    mid: Xy,
    end: Xy,

    // shape common
    solder_mask_margin: ?f64 = null,
    stroke: ?Stroke = null,
    fill: ?E_fill = null,
    layer: ?str = null,
    layers: list(str) = .{},
    locked: ?bool = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .start = structure.SexpField{ .order = -3 },
        .mid = structure.SexpField{ .order = -2 },
        .end = structure.SexpField{ .order = -1 },
        .uuid = structure.SexpField{ .order = 100 },
    };
};

pub const Circle = struct {
    center: Xy,
    end: Xy,

    // shape common
    solder_mask_margin: ?f64 = null,
    stroke: ?Stroke = null,
    fill: ?E_fill = null,
    layer: ?str = null,
    layers: list(str) = .{},
    locked: ?bool = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .center = structure.SexpField{ .order = -2 },
        .end = structure.SexpField{ .order = -1 },
        .uuid = structure.SexpField{ .order = 100 },
    };
};

pub const Rect = struct {
    start: Xy,
    end: Xy,

    // shape common
    solder_mask_margin: ?f64 = null,
    stroke: ?Stroke = null,
    fill: ?E_fill = null,
    layer: ?str = null,
    layers: list(str) = .{},
    locked: ?bool = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .start = structure.SexpField{ .order = -2 },
        .end = structure.SexpField{ .order = -1 },
        .uuid = structure.SexpField{ .order = 100 },
    };
};

// An arc entry inside (pts ...): KiCad writes curved outline segments of a
// SHAPE_LINE_CHAIN as (arc (start ..) (mid ..) (end ..)) interleaved with
// the (xy ..) points (formatPolyPts).
pub const PtsArc = struct {
    start: Xy,
    mid: Xy,
    end: Xy,
};

pub const Pts = struct {
    xys: list(Xy) = .{},
    arcs: list(PtsArc) = .{},

    pub const fields_meta = .{
        .xys = structure.SexpField{ .multidict = true, .sexp_name = "xy" },
        .arcs = structure.SexpField{ .multidict = true, .sexp_name = "arc" },
    };
};

pub const Polygon = struct {
    pts: Pts,

    // shape common
    solder_mask_margin: ?f64 = null,
    stroke: ?Stroke = null,
    fill: ?E_fill = null,
    layer: ?str = null,
    layers: list(str) = .{},
    locked: ?bool = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .pts = structure.SexpField{ .order = -1 },
        .uuid = structure.SexpField{ .order = 100 },
    };
};

pub const Curve = struct {
    pts: Pts,

    // shape common
    solder_mask_margin: ?f64 = null,
    stroke: ?Stroke = null,
    fill: ?E_fill = null,
    layer: ?str = null,
    layers: list(str) = .{},
    locked: ?bool = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .pts = structure.SexpField{ .order = -1 },
        .uuid = structure.SexpField{ .order = 100 },
    };
};

// Text structures
pub const Text = struct {
    text: str,
    at: Xyr,
    layer: TextLayer,
    uuid: ?str = null,
    effects: Effects,

    pub const fields_meta = .{
        .text = structure.SexpField{ .positional = true },
    };
};

pub const FpText = struct {
    type: E_fp_text_type,
    text: str,
    at: Xyr,
    layer: TextLayer,
    hide: ?bool = null,
    uuid: ?str = null,
    effects: Effects,

    pub const fields_meta = .{
        .type = structure.SexpField{ .positional = true },
        .text = structure.SexpField{ .positional = true },
    };
};

// Pad structures
pub const Drill = f64;

// Pad custom-shape primitives (KiCad 10.0.3 formatPrimitives :1941-2040 and
// the padstack-layer T_primitives parser): bare geometry with the legacy
// (width d) token and (fill yes|no) — no stroke/layer/uuid. gr_vector and
// gr_bbox are proxy items: no width; gr_bbox (a rectangle) still carries
// fill.
pub const PrimitiveLine = struct {
    start: Xy,
    end: Xy,
    width: f64,
};

pub const PrimitiveVector = struct {
    start: Xy,
    end: Xy,
};

pub const PrimitiveRect = struct {
    start: Xy,
    end: Xy,
    radius: ?f64 = null,
    width: f64,
    fill: ?bool = null,
};

pub const PrimitiveBbox = struct {
    start: Xy,
    end: Xy,
    fill: ?bool = null,
};

pub const PrimitiveArc = struct {
    start: Xy,
    mid: Xy,
    end: Xy,
    width: f64,
};

pub const PrimitiveCircle = struct {
    center: Xy,
    end: Xy,
    width: f64,
    fill: ?bool = null,
};

pub const PrimitiveCurve = struct {
    pts: Pts,
    width: f64,
};

pub const PrimitivePoly = struct {
    pts: Pts,
    width: f64,
    fill: ?bool = null,
};

pub const PadPrimitives = struct {
    gr_lines: list(PrimitiveLine) = .{},
    gr_vectors: list(PrimitiveVector) = .{},
    gr_rects: list(PrimitiveRect) = .{},
    gr_bboxes: list(PrimitiveBbox) = .{},
    gr_arcs: list(PrimitiveArc) = .{},
    gr_circles: list(PrimitiveCircle) = .{},
    gr_curves: list(PrimitiveCurve) = .{},
    gr_polys: list(PrimitivePoly) = .{},

    pub const fields_meta = .{
        .gr_lines = structure.SexpField{ .multidict = true, .sexp_name = "gr_line" },
        .gr_vectors = structure.SexpField{ .multidict = true, .sexp_name = "gr_vector" },
        .gr_rects = structure.SexpField{ .multidict = true, .sexp_name = "gr_rect" },
        .gr_bboxes = structure.SexpField{ .multidict = true, .sexp_name = "gr_bbox" },
        .gr_arcs = structure.SexpField{ .multidict = true, .sexp_name = "gr_arc" },
        .gr_circles = structure.SexpField{ .multidict = true, .sexp_name = "gr_circle" },
        .gr_curves = structure.SexpField{ .multidict = true, .sexp_name = "gr_curve" },
        .gr_polys = structure.SexpField{ .multidict = true, .sexp_name = "gr_poly" },
    };
};

// PadDrill can be either a simple number (drill 1.2) or a structured drill with shape/size/offset
pub const PadDrill = struct {
    shape: ?E_pad_drill_shape = null,
    size_x: ?f64 = null,
    size_y: ?f64 = null,
    offset: ?Xy = null,

    pub const fields_meta = .{
        .shape = structure.SexpField{ .positional = true },
        .size_x = structure.SexpField{ .positional = true },
        .size_y = structure.SexpField{ .positional = true },
        .offset = structure.SexpField{},
    };
};

pub const PadOptions = struct {
    clearance: ?E_pad_clearance = null,
    anchor: ?E_pad_anchor = null,
};

// Per-layer pad padstack entry: (layer "Inner"|"B.Cu"|"<Cu name>" ...).
// Grammar = KiCad 10.0.3 parsePadstack (pcb_io_kicad_sexpr_parser
// :6567-6905); field order = the formatPadLayer emission order
// (pcb_io_kicad_sexpr :2078-2150). The layer name is positional: "Inner"
// (front_inner_back mode) or a copper layer name (custom mode).
// tenting is parse-accepted by KiCad per layer but never emitted by the
// 10.0.3 formatter; it is modeled read-side for completeness.
pub const PadstackLayer = struct {
    name: str,
    shape: ?E_pad_shape = null,
    size: ?Wh = null,
    rect_delta: ?Xy = null,
    offset: ?Xy = null,
    roundrect_rratio: ?f64 = null,
    chamfer_ratio: ?f64 = null,
    chamfer: list(E_pad_chamfer_corner) = .{},
    options: ?PadOptions = null,
    primitives: ?PadPrimitives = null,
    thermal_bridge_angle: ?f64 = null,
    thermal_gap: ?f64 = null,
    thermal_bridge_width: ?f64 = null,
    clearance: ?f64 = null,
    zone_connect: ?E_zone_connection = null,
    tenting: ?Tenting = null,

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
        .primitives = structure.SexpField{ .sexp_name = "primitives" },
    };
};

// Pad-level (padstack (mode front_inner_back|custom) (layer ...)...).
// mode is a KEYED sub-list — KiCad's parser rejects a bare mode token.
pub const PadPadstack = struct {
    mode: E_padstack_mode,
    layers: list(PadstackLayer) = .{},

    pub const fields_meta = .{
        .layers = structure.SexpField{ .multidict = true, .sexp_name = "layer" },
    };
};

// Field order = the KiCad 10.0.3 pad emission order (pcb_io_kicad_sexpr
// format(PAD) :1698-2185): ... options, primitives, teardrops, tenting,
// uuid, padstack.
pub const Pad = struct {
    name: str,
    type: E_pad_type,
    shape: E_pad_shape,
    at: Xyr,
    size: Wh,
    rect_delta: ?Xy = null,
    drill: ?PadDrill = null,
    layers: list(str) = .{},
    remove_unused_layers: ?bool = null,
    keep_end_layers: ?bool = null,
    // present-but-empty is meaningful (forces no-zone-connection on every
    // layer); KiCad writes the clause, possibly bare, whenever the
    // unconnected-layer mode removes copper — hence optional list
    zone_layer_connections: ?list(str) = null,
    roundrect_rratio: ?f64 = null,
    chamfer_ratio: ?f64 = null,
    chamfer: list(E_pad_chamfer_corner) = .{},
    net: ?Net = null,
    pinfunction: ?str = null,
    pintype: ?str = null,
    die_length: ?f64 = null,
    die_delay: ?f64 = null,
    solder_mask_margin: ?f64 = null,
    solder_paste_margin: ?f64 = null,
    solder_paste_margin_ratio: ?f64 = null,
    clearance: ?f64 = null,
    zone_connect: ?E_zone_connection = null,
    thermal_bridge_width: ?f64 = null,
    thermal_bridge_angle: ?f64 = null,
    thermal_gap: ?f64 = null,
    properties: ?E_pad_property = null,
    options: ?PadOptions = null,
    primitives: ?PadPrimitives = null,
    teardrops: ?Teardrop = null,
    tenting: ?Tenting = null,
    uuid: ?str = null,
    padstack: ?PadPadstack = null,

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
        .type = structure.SexpField{ .positional = true },
        .shape = structure.SexpField{ .positional = true },
        .properties = structure.SexpField{ .sexp_name = "property" },
        .primitives = structure.SexpField{ .sexp_name = "primitives" },
    };
};

// Net structure. Numbers are process-internal handles: v9 files carry
// (net <number> "<name>"), v10 files carry (net "<name>") only — reading a
// v10 file synthesizes the numbering (see PcbFile.loads), writing the v10
// dialect emits the resolved name and never the number.
pub const Net = struct {
    number: i32,
    name: ?str = null,

    pub const fields_meta = .{
        .number = structure.SexpField{ .positional = true, .net_ref = true },
        .name = structure.SexpField{ .positional = true, .v9_only = true },
    };
};

// Property structure. at/layer are optional: KiCad 10 writes bare metadata
// properties like (property ki_fp_filters "C_*") without placement.
pub const Property = struct {
    name: str,
    value: str,
    at: ?Xyr = null,
    unlocked: ?bool = null,
    layer: ?str = null,
    hide: ?bool = null,
    uuid: ?str = null,
    effects: ?Effects = null,

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
        .value = structure.SexpField{ .positional = true },
    };
};

pub const ModelXyz = struct {
    xyz: Xyz,
};

// 3D model structure
pub const Model = struct {
    path: str,
    offset: ModelXyz,
    scale: ModelXyz,
    rotate: ModelXyz,

    pub const fields_meta = .{
        .path = structure.SexpField{ .positional = true },
    };
};

pub const E_Attr = enum {
    smd,
    dnp,
    board_only,
    through_hole,
    exclude_from_pos_files,
    exclude_from_bom,
    allow_missing_courtyard,
    allow_soldermask_bridges,
};

// Multi-unit symbol mapping, new in the v10 dialect:
// (units (unit (name "A") (pins "1" "2")))
pub const FootprintUnit = struct {
    name: str,
    pins: list(str) = .{},
};

pub const FootprintUnits = struct {
    units: list(FootprintUnit) = .{},

    pub const fields_meta = .{
        .units = structure.SexpField{ .multidict = true, .sexp_name = "unit" },
    };
};

// Footprint structure
pub const Footprint = struct {
    name: str,
    locked: ?bool = null,
    layer: str = "F.Cu",
    uuid: ?str = null,
    at: Xyr,
    descr: ?str = null,
    // list(str) to match footprint.zig's Footprint.tags: the transformer copies
    // this field by name from a lib footprint into a pcb footprint
    // (_fp_common_fields_dict), so the two schemas must agree on its type. KiCad
    // writes a single (tags "a b c") string, which round-trips as a 1-element
    // list — byte-identical to the scalar form.
    tags: list(str) = .{},
    path: ?str = null,
    sheetname: ?str = null,
    sheetfile: ?str = null,
    units: ?FootprintUnits = null,
    propertys: list(Property) = .{},
    attr: list(E_Attr) = .{},
    duplicate_pad_numbers_are_jumpers: ?bool = null,
    // net-tie groups: (net_tie_pad_groups "1,2" "3,4"). The sibling
    // jumper_pad_groups construct nests headless lists — ("1" "2") — which
    // the schema engine cannot represent; it stays S5a-loud by decision.
    net_tie_pad_groups: list(str) = .{},
    fp_lines: list(Line) = .{},
    fp_arcs: list(Arc) = .{},
    fp_circles: list(Circle) = .{},
    fp_rects: list(Rect) = .{},
    fp_poly: list(Polygon) = .{},
    fp_texts: list(FpText) = .{},
    pads: list(Pad) = .{},
    embedded_fonts: ?bool = null,
    models: list(Model) = .{},

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
        .propertys = structure.SexpField{ .multidict = true, .sexp_name = "property" },
        .fp_texts = structure.SexpField{ .multidict = true, .sexp_name = "fp_text" },
        .fp_lines = structure.SexpField{ .multidict = true, .sexp_name = "fp_line" },
        .fp_arcs = structure.SexpField{ .multidict = true, .sexp_name = "fp_arc" },
        .fp_circles = structure.SexpField{ .multidict = true, .sexp_name = "fp_circle" },
        .fp_rects = structure.SexpField{ .multidict = true, .sexp_name = "fp_rect" },
        .fp_poly = structure.SexpField{ .multidict = true },
        .pads = structure.SexpField{ .multidict = true, .sexp_name = "pad" },
        .models = structure.SexpField{ .multidict = true, .sexp_name = "model" },
    };
};

// Via padstack per-layer entry. The v10 file grammar (KiCad 10.0.3
// parseViastack, pcb_io_kicad_sexpr_parser :7726-7789; writer :2776-2812) is
// exactly: layer name positional ("Inner" | copper layer name) + a single
// scalar (size <diameter>) — nothing else is legal. The former
// thermal_*/zone_connect fields here (and the two-value Xy size) mirrored
// KiCad's in-memory per-layer PADSTACK model, not the file syntax — the same
// file-vs-memory category error as ZonePlacement.source_type (see CLAUDE.md);
// they made a real v10 via padstack a hard MissingField parse error.
pub const ViaLayer = struct {
    name: str,
    size: ?f64 = null,

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
    };
};

// (padstack (mode front_inner_back|custom) (layer "..." (size d)) ...).
// mode is a KEYED sub-list — KiCad's parser (and ours, before this fix,
// wrongly wrote it positionally) rejects a bare mode token.
pub const ViaPadstack = struct {
    mode: E_padstack_mode,
    layers: list(ViaLayer) = .{},

    pub const fields_meta = .{
        .layers = structure.SexpField{ .multidict = true, .sexp_name = "layer" },
    };
};

// Via type token: (via blind ...) etc.; a through via carries no token.
pub const E_via_type = enum {
    blind,
    buried,
    micro,
};

// (backdrill (size d) (layers "start" "end")) — also used for
// tertiary_drill. size is a single scalar diameter.
pub const Backdrill = struct {
    size: ?f64 = null,
    layers: list(str) = .{},
};

pub const E_post_machining_mode = enum {
    counterbore,
    countersink,
};

// (front_post_machining counterbore (size d) (depth d) (angle a)) — mode is
// positional. angle is stored in the file in degrees; KiCad keeps tenths
// internally and divides by 10 on write (parse multiplies by 10) — we carry
// the file value verbatim.
pub const PostMachining = struct {
    mode: E_post_machining_mode,
    size: ?f64 = null,
    depth: ?f64 = null,
    angle: ?f64 = null,

    pub const fields_meta = .{
        .mode = structure.SexpField{ .positional = true },
    };
};

// Field order = the KiCad 10.0.3 via emission order (pcb_io_kicad_sexpr
// format(PCB_TRACK) :2605-2816): type token, at/size/drill, backdrill,
// tertiary_drill, post machining, layers, unconnected-layer mode, locked,
// free, zone_layer_connections, tenting/capping/covering/plugging/filling,
// padstack, teardrops, net, uuid.
pub const Via = struct {
    type: ?E_via_type = null,
    at: Xy,
    size: f64,
    drill: f64,
    backdrill: ?Backdrill = null,
    tertiary_drill: ?Backdrill = null,
    front_post_machining: ?PostMachining = null,
    back_post_machining: ?PostMachining = null,
    layers: list(str) = .{},
    remove_unused_layers: ?bool = null,
    keep_end_layers: ?bool = null,
    start_end_only: ?bool = null,
    locked: ?bool = null,
    free: ?bool = null,
    // present-but-empty is meaningful — see Pad.zone_layer_connections
    zone_layer_connections: ?list(str) = null,
    // IPC-4761 protection family (v10, format 20250228): covering/plugging
    // share the nested front/back shape with tenting; capping/filling are
    // plain opt-bools.
    tenting: ?Tenting = null,
    capping: ?bool = null,
    covering: ?Tenting = null,
    plugging: ?Tenting = null,
    filling: ?bool = null,
    padstack: ?ViaPadstack = null,
    teardrops: ?Teardrop = null,
    net: i32 = 0,
    uuid: ?str = null,

    pub const fields_meta = .{
        .type = structure.SexpField{ .positional = true },
        .net = structure.SexpField{ .net_ref = true, .net_ref_explicit_empty = true },
    };
};

// Zone structures
pub const Hatch = struct {
    mode: E_zone_hatch_mode,
    pitch: f64,

    pub const fields_meta = .{
        .mode = structure.SexpField{ .positional = true },
        .pitch = structure.SexpField{ .positional = true },
    };
};

pub const ConnectPads = struct {
    mode: ?E_zone_connect_pads_mode = null,
    clearance: ?f64 = null,

    pub const fields_meta = .{
        .mode = structure.SexpField{ .positional = true },
    };
};

pub const E_zone_fill_enable = enum {
    yes,
};

pub const ZoneFill = struct {
    enable: ?E_zone_fill_enable = null,
    mode: ?E_zone_fill_mode = null,
    hatch_thickness: ?f64 = null,
    hatch_gap: ?f64 = null,
    hatch_orientation: ?f64 = null,
    hatch_smoothing_level: ?f64 = null,
    hatch_smoothing_value: ?f64 = null,
    hatch_border_algorithm: ?E_zone_hatch_border_algorithm = null,
    hatch_min_hole_area: ?f64 = null,
    arc_segments: ?i32 = null,
    thermal_gap: ?f64 = null,
    thermal_bridge_width: ?f64 = null,
    smoothing: ?E_zone_smoothing = null,
    radius: ?f64 = null,
    //island_removal_mode: ?E_zone_island_removal_mode = null,
    island_removal_mode: ?i32 = null,
    island_area_min: ?f64 = null,

    pub const fields_meta = .{
        .enable = structure.SexpField{ .positional = true },
    };
};

pub const FilledPolygon = struct {
    layer: str,
    // (island yes): the filled polygon is an island (KiCad writer :3081)
    island: ?bool = null,
    pts: Pts,
};

// Zone per-layer properties: (property (layer "B.Cu") (hatch_position (xy ..)))
// — only written when a hatching offset is set (KiCad writer :3094-3110).
pub const HatchPosition = struct {
    xy: Xy,
};

pub const ZoneLayerProperty = struct {
    layer: str,
    hatch_position: ?HatchPosition = null,
};

pub const ZoneKeepout = struct {
    tracks: E_zone_keepout,
    vias: E_zone_keepout,
    pads: E_zone_keepout,
    copperpour: E_zone_keepout,
    footprints: E_zone_keepout,
};

pub const ZonePlacement = struct {
    source_type: ?E_zone_placement_source_type = null,
    source: ?str = null,
    enabled: bool = true,
    sheetname: ?str = null,
};

pub const ZoneTeardrop = struct {
    type: E_zone_teardrop_type,
};

pub const ZoneAttr = struct {
    teardrop: ?ZoneTeardrop = null,
};

pub const Zone = struct {
    net: i32 = 0,
    net_name: ?str = null,
    locked: ?bool = null,
    layer: ?str = null,
    layers: list(str) = .{},
    uuid: ?str = null,
    name: ?str = null,
    hatch: Hatch,
    priority: ?i32 = null,
    attr: ?ZoneAttr = null,
    connect_pads: ?ConnectPads = null,
    min_thickness: ?f64 = null,
    filled_areas_thickness: ?bool = null,
    keepout: ?ZoneKeepout = null,
    placement: ?ZonePlacement = null,
    fill: ?ZoneFill = null,
    propertys: list(ZoneLayerProperty) = .{},
    polygon: Polygon,
    filled_polygon: list(FilledPolygon) = .{},

    pub const fields_meta = .{
        .net = structure.SexpField{ .net_ref = true },
        .net_name = structure.SexpField{ .v9_only = true },
        .propertys = structure.SexpField{ .multidict = true, .sexp_name = "property" },
        .filled_polygon = structure.SexpField{ .multidict = true },
    };
};

// Track (segment) structures
pub const Segment = struct {
    start: Xy,
    end: Xy,
    width: f64,
    layer: ?str = null,
    net: i32 = 0,
    uuid: ?str = null,

    pub const fields_meta = .{
        .start = structure.SexpField{ .order = -3 },
        .end = structure.SexpField{ .order = -2 },
        .width = structure.SexpField{ .order = -1 },
        .net = structure.SexpField{ .net_ref = true, .net_ref_explicit_empty = true },
    };
};

pub const ArcSegment = struct {
    start: Xy,
    mid: Xy,
    end: Xy,
    width: f64,
    layer: ?str = null,
    net: i32 = 0,
    uuid: ?str = null,

    pub const fields_meta = .{
        .net = structure.SexpField{ .net_ref = true, .net_ref_explicit_empty = true },
    };
};

// Board setup structures
pub const General = struct {
    thickness: f64 = 1.6,
    legacy_teardrops: bool = false,
};

pub const Paper = struct {
    type: E_paper_type = .A4,
    size: ?Xy = null,
    orientation: ?E_paper_orientation = null,

    pub const fields_meta = .{
        .type = structure.SexpField{ .positional = true, .symbol = false },
        .size = structure.SexpField{ .positional = true },
        .orientation = structure.SexpField{ .positional = true },
    };
};

pub const TitleBlock = struct {
    title: ?str = null,
    date: ?str = null,
    revision: ?str = null,
    company: ?str = null,
    comment: list(Comment) = .{},

    pub const fields_meta = .{
        // the file key is (rev "..."), not (revision ...)
        .revision = structure.SexpField{ .sexp_name = "rev" },
        .comment = structure.SexpField{ .multidict = true },
    };
};

pub const Comment = struct {
    number: i32,
    text: str,

    pub const fields_meta = .{
        .number = structure.SexpField{ .positional = true },
        .text = structure.SexpField{ .positional = true },
    };
};

pub const Layer = struct {
    number: i32,
    name: str,
    type: E_layer_type,
    alias: ?str = null,

    pub const fields_meta = .{
        .number = structure.SexpField{ .positional = true },
        .name = structure.SexpField{ .positional = true },
        .type = structure.SexpField{ .positional = true, .symbol = true },
        .alias = structure.SexpField{ .positional = true },
    };
};

pub const Stackup = struct {
    layers: list(StackupLayer) = .{},
    copper_finish: ?E_copper_finish = null,
    dielectric_constraints: ?bool = null,
    edge_connector: ?E_edge_connector = null,
    castellated_pads: ?bool = null,
    edge_plating: ?bool = null,

    pub const fields_meta = .{
        .layers = structure.SexpField{ .multidict = true, .sexp_name = "layer" },
        .copper_finish = structure.SexpField{ .symbol = false },
    };
};

pub const Thickness = struct {
    thickness: f64,
    locked: ?bool = null,

    pub const fields_meta = .{
        .thickness = structure.SexpField{ .positional = true },
        .locked = structure.SexpField{ .positional = true },
    };
};

pub const StackupLayer = struct {
    name: str,
    type: str,
    color: ?str = null,
    thickness: ?Thickness = null,
    material: ?str = null,
    epsilon_r: ?f64 = null,
    loss_tangent: ?f64 = null,

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
    };
};

pub const Rules = struct {
    max_error: f64 = 0.005,
    min_clearance: f64 = 0.0,
    min_connection: f64 = 0.0,
    min_copper_edge_clearance: f64 = 0.5,
    min_hole_clearance: f64 = 0.25,
    min_hole_to_hole: f64 = 0.25,
    min_microvia_diameter: f64 = 0.2,
    min_microvia_drill: f64 = 0.1,
    min_resolved_spokes: i32 = 2,
    min_silk_clearance: f64 = 0.0,
    min_text_height: f64 = 0.8,
    min_text_thickness: f64 = 0.08,
    min_through_hole_diameter: f64 = 0.3,
    min_track_width: f64 = 0.0,
    min_via_annular_width: f64 = 0.1,
    min_via_diameter: f64 = 0.5,
    solder_mask_to_copper_clearance: f64 = 0.0,
    use_height_for_length_calcs: bool = true,
};

pub const PcbPlotParams = struct {
    layerselection: str = "0x00010fc_ffffffff",
    plot_on_all_layers_selection: str = "0x0000000_00000000",
    disableapertmacros: ?bool = null,
    usegerberextensions: ?bool = null,
    usegerberattributes: ?bool = null,
    usegerberadvancedattributes: ?bool = null,
    creategerberjobfile: ?bool = null,
    dashed_line_dash_ratio: f64 = 12.0,
    dashed_line_gap_ratio: f64 = 3.0,
    svgprecision: i32 = 4,
    plotframeref: ?bool = null,
    viasonmask: ?bool = null,
    mode: i32 = 1,
    useauxorigin: ?bool = null,
    hpglpennumber: i32 = 1,
    hpglpenspeed: i32 = 20,
    hpglpendiameter: f64 = 15.0,
    pdf_front_fp_property_popups: ?bool = null,
    pdf_back_fp_property_popups: ?bool = null,
    pdf_metadata: ?bool = null,
    pdf_single_document: ?bool = null,
    dxfpolygonmode: ?bool = null,
    dxfimperialunits: ?bool = null,
    dxfusepcbnewfont: ?bool = null,
    psnegative: ?bool = null,
    psa4output: ?bool = null,
    plot_black_and_white: ?bool = null,
    plotinvisibletext: ?bool = null,
    sketchpadsonfab: ?bool = null,
    plotreference: ?bool = null,
    plotvalue: ?bool = null,
    plotpadnumbers: ?bool = null,
    hidednponfab: ?bool = null,
    sketchdnponfab: ?bool = null,
    crossoutdnponfab: ?bool = null,
    plotfptext: ?bool = null,
    subtractmaskfromsilk: ?bool = null,
    outputformat: i32 = 1,
    mirror: ?bool = null,
    drillshape: i32 = 1,
    scaleselection: i32 = 1,
    outputdirectory: str = "",

    pub const fields_meta = .{
        .layerselection = structure.SexpField{ .symbol = true },
        .plot_on_all_layers_selection = structure.SexpField{ .symbol = true },
    };
};
pub const Setup = struct {
    stackup: ?Stackup = null,
    // millimetres: KiCad writes formatInternalUnits (e.g. 0.1016), not an int
    pad_to_mask_clearance: f64 = 0,
    allow_soldermask_bridges_in_footprints: bool = false,
    tenting: ?Tenting = null,
    // v10 mask/via-treatment family; covering/plugging share the nested
    // front/back shape with tenting, capping/filling are plain bools
    covering: ?Tenting = null,
    plugging: ?Tenting = null,
    capping: ?bool = null,
    filling: ?bool = null,
    aux_axis_origin: ?Xy = null,
    grid_origin: ?Xy = null,
    pcbplotparams: PcbPlotParams = .{},
    rules: ?Rules = null,
};

// Main PCB structure
pub const KicadPcb = struct {
    version: i32 = KICAD_PCB_VERSION,
    generator: str,
    generator_version: str,
    general: General = .{},
    paper: ?E_paper_type = .A4,
    title_block: ?TitleBlock = null,
    layers: list(Layer) = .{},
    setup: Setup = .{},
    // board-level text variables: (property "NAME" "value"), written by
    // KiCad right after setup (formatProperties, 10.0.3 writer :710-757)
    propertys: list(Property) = .{},
    nets: list(Net) = .{},
    footprints: list(Footprint) = .{},
    vias: list(Via) = .{},
    segments: list(Segment) = .{},
    arcs: list(ArcSegment) = .{},
    gr_lines: list(Line) = .{},
    gr_arcs: list(Arc) = .{},
    gr_curves: list(Curve) = .{},
    gr_circles: list(Circle) = .{},
    gr_rects: list(Rect) = .{},
    gr_polys: list(Polygon) = .{},
    gr_texts: list(Text) = .{},
    gr_text_boxes: list(TextBox) = .{},
    zones: list(Zone) = .{},
    images: list(Image) = .{},
    dimensions: list(Dimension) = .{},
    groups: list(Group) = .{},
    targets: list(Target) = .{},
    embedded_fonts: ?bool = null,
    embedded_files: ?EmbeddedFiles = null,
    tables: list(Table) = .{},
    generateds: list(Generated) = .{},

    pub const fields_meta = .{
        // Note: layers is NOT multidict - it's a single (layers ...) entry containing multiple Layer items
        // nets: the v10 dialect has no top-level net table at all
        .propertys = structure.SexpField{ .multidict = true, .sexp_name = "property" },
        .nets = structure.SexpField{ .multidict = true, .sexp_name = "net", .v9_only = true },
        .footprints = structure.SexpField{ .multidict = true, .sexp_name = "footprint" },
        .vias = structure.SexpField{ .multidict = true, .sexp_name = "via" },
        .zones = structure.SexpField{ .multidict = true, .sexp_name = "zone" },
        .segments = structure.SexpField{ .multidict = true, .sexp_name = "segment" },
        .arcs = structure.SexpField{ .multidict = true, .sexp_name = "arc" },
        .gr_lines = structure.SexpField{ .multidict = true, .sexp_name = "gr_line" },
        .gr_arcs = structure.SexpField{ .multidict = true, .sexp_name = "gr_arc" },
        .gr_curves = structure.SexpField{ .multidict = true, .sexp_name = "gr_curve" },
        .gr_circles = structure.SexpField{ .multidict = true, .sexp_name = "gr_circle" },
        .gr_rects = structure.SexpField{ .multidict = true, .sexp_name = "gr_rect" },
        .gr_polys = structure.SexpField{ .multidict = true, .sexp_name = "gr_poly" },
        .gr_texts = structure.SexpField{ .multidict = true, .sexp_name = "gr_text" },
        .gr_text_boxes = structure.SexpField{ .multidict = true, .sexp_name = "gr_text_box" },
        .images = structure.SexpField{ .multidict = true, .sexp_name = "image" },
        .dimensions = structure.SexpField{ .multidict = true, .sexp_name = "dimension" },
        .groups = structure.SexpField{ .multidict = true, .sexp_name = "group" },
        .targets = structure.SexpField{ .multidict = true, .sexp_name = "target" },
        .generateds = structure.SexpField{ .multidict = true, .sexp_name = "generated" },
        .tables = structure.SexpField{ .multidict = true, .sexp_name = "table" },
        .paper = structure.SexpField{ .symbol = false },
    };
};

pub const Generated = struct {
    uuid: str,
    type: str,
    name: str,
    layer: str,
    members: list(str) = .{},
    locked: ?bool = null,
};

pub const Image = struct {
    at: Xy,
    layer: str,
    scale: f64 = 1.0,
    locked: ?bool = null,
    data: list(str) = .{},
    uuid: ?str = null,
};

pub const EmbeddedFile = struct {
    name: str,
    type: E_embedded_file_type,
    data: list(str) = .{},
    checksum: ?str = null,
};

pub const EmbeddedFiles = struct {
    files: list(EmbeddedFile) = .{},

    pub const fields_meta = .{
        .files = structure.SexpField{ .multidict = true, .sexp_name = "file" },
    };
};

// Teardrop parameters on pads and vias. Field order = the KiCad 10.0.3
// emission order (formatTeardropParameters, pcb_io_kicad_sexpr :780-799).
// The pre-v9 legacy (curve_points N) read alias is deliberately NOT modeled:
// only pre-v9 files carry it, outside the declared v9/v10 read scope, and it
// surfaces loudly via the S5a unknown-key report instead of being silently
// coerced.
pub const Teardrop = struct {
    best_length_ratio: f64,
    max_length: f64,
    best_width_ratio: f64,
    max_width: f64,
    curved_edges: bool,
    filter_ratio: f64,
    enabled: bool = false,
    allow_two_segments: bool = false,
    prefer_zone_connections: bool = true,
};

pub const RenderCache = struct {
    text: str = "",
    rotation: f64 = 0,
    polygons: list(Polygon) = .{},

    pub const fields_meta = .{
        .text = structure.SexpField{ .positional = true },
        .rotation = structure.SexpField{ .positional = true },
        .polygons = structure.SexpField{ .multidict = true },
    };
};

pub const Margins = struct {
    left: f64,
    top: f64,
    right: f64,
    bottom: f64,

    pub const fields_meta = .{
        .left = structure.SexpField{ .positional = true },
        .top = structure.SexpField{ .positional = true },
        .right = structure.SexpField{ .positional = true },
        .bottom = structure.SexpField{ .positional = true },
    };
};

pub const Span = struct {
    cols: i32,
    rows: i32,

    pub const fields_meta = .{
        .cols = structure.SexpField{ .positional = true },
        .rows = structure.SexpField{ .positional = true },
    };
};

pub const TextBox = struct {
    text: str,
    start: ?Xy = null,
    end: ?Xy = null,
    pts: ?Pts = null,
    margins: ?Margins = null,
    angle: ?f64 = null,
    layer: str,
    uuid: ?str = null,
    effects: Effects,
    border: ?bool = null,
    stroke: ?Stroke = null,
    knockout: ?bool = null,
    locked: ?bool = null,
    //span: ?Span = null,
    //render_cache: ?RenderCache = null,

    pub const fields_meta = .{
        .text = structure.SexpField{ .positional = true },
    };
};

pub const TableCell = struct {
    text: str,
    locked: bool = false,
    start: ?Xy = null,
    end: ?Xy = null,
    pts: ?Pts = null,
    angle: ?f64 = null,
    stroke: ?Stroke = null,
    border: ?bool = null,
    margins: ?Margins = null,
    layer: str,
    span: ?Span = null,
    effects: Effects,
    render_cache: ?RenderCache = null,
    uuid: ?str = null,

    pub const fields_meta = .{
        .text = structure.SexpField{ .positional = true },
    };
};

pub const Cells = struct {
    table_cells: list(TableCell) = .{},

    pub const fields_meta = .{
        .table_cells = structure.SexpField{ .multidict = true },
    };
};

pub const Border = struct {
    external: bool,
    header: bool,
    stroke: Stroke,
};

pub const Separator = struct {
    rows: bool,
    cols: bool,
    stroke: Stroke,
};

pub const Table = struct {
    column_count: i32,
    locked: ?bool = null,
    layer: str,
    column_widths: list(f64) = .{},
    row_heights: list(f64) = .{},
    cells: Cells,
    border: Border,
    separators: Separator,
};

pub const DimensionPts = struct {
    xys: list(Xy) = .{},

    pub const fields_meta = .{
        .xys = structure.SexpField{ .multidict = true, .sexp_name = "xy" },
    };
};

pub const DimensionFormat = struct {
    prefix: str,
    suffix: str,
    units: i32,
    units_format: i32,
    precision: i32,
    override_value: ?str = null,
    suppress_zeroes: bool = false,
};

pub const DimensionStyle = struct {
    thickness: ?f64 = null,
    arrow_length: ?f64 = null,
    arrow_direction: E_dimension_arrow_direction = .outward,
    text_position_mode: ?i32 = null,
    extension_height: ?f64 = null,
    extension_offset: ?f64 = null,
    keep_text_aligned: bool = true,
    text_frame: ?i32 = null,
};

pub const Dimension = struct {
    type: E_dimension_type,
    layer: str,
    uuid: ?str = null,
    pts: DimensionPts,
    // only aligned/orthogonal dimensions carry height; leader/radial/center
    // do not (KiCad 10.0.3 writer :913-914)
    height: ?f64 = null,
    orientation: ?f64 = null,
    leader_length: ?f64 = null,
    format: ?DimensionFormat = null,
    style: ?DimensionStyle = null,
    // center dimensions carry no text (KiCad 10.0.3 writer :983-984)
    gr_text: ?Text = null,
};

pub const Group = struct {
    name: ?str = null,
    uuid: ?str = null,
    locked: ?bool = null,
    members: list(str) = .{},

    pub const fields_meta = .{
        .name = structure.SexpField{ .positional = true },
    };
};

pub const Target = struct {
    at: Xy,
    size: Xy,
    width: f64,
    layer: str,
    uuid: ?str = null,
};

// Cheap text sniff of (version N) so the v10 pre-scan only runs when needed.
fn sniffVersion(content: []const u8) i32 {
    const window = content[0..@min(content.len, 2048)];
    const idx = std.mem.indexOf(u8, window, "(version") orelse return 0;
    var i = idx + "(version".len;
    while (i < window.len and window[i] == ' ') i += 1;
    var end = i;
    while (end < window.len and window[end] >= '0' and window[end] <= '9') end += 1;
    if (end == i) return 0;
    return std.fmt.parseInt(i32, window[i..end], 10) catch 0;
}

fn collectNetNames(sexp: structure.SExp, out: *std.array_list.Managed([]const u8)) !void {
    const ast = @import("../ast.zig");
    const items = ast.getList(sexp) orelse return;
    // a v10 net reference is exactly (net "<name>"); the v9 table entry
    // (net <number> "<name>") has 3 items and the number is not a string
    if (items.len == 2) {
        if (ast.getSymbol(items[0])) |sym| {
            if (std.mem.eql(u8, sym, "net") and items[1].value == .string) {
                try out.append(items[1].value.string);
            }
        }
    }
    for (items) |item| {
        try collectNetNames(item, out);
    }
}

// File structure
pub const PcbFile = struct {
    kicad_pcb: KicadPcb,

    const root_symbol = "kicad_pcb";

    pub fn loads(allocator: std.mem.Allocator, in: structure.input) !PcbFile {
        switch (in) {
            .string => |content| return loadsFromContent(allocator, content),
            .path => |p| {
                const content = try std.fs.cwd().readFileAlloc(allocator, p, 200 * 1024 * 1024);
                defer allocator.free(content);
                return loadsFromContent(allocator, content);
            },
            // pre-parsed input: no raw text to pre-scan, so name-only (v10)
            // net references cannot be resolved on this path
            .sexp => {
                const pcb = try structure.loads(KicadPcb, allocator, in, root_symbol);
                return PcbFile{ .kicad_pcb = pcb };
            },
        }
    }

    fn loadsFromContent(allocator: std.mem.Allocator, content: []const u8) !PcbFile {
        // v9 and older: no name-only references, decode as-is
        if (sniffVersion(content) < KICAD_PCB_V10_MIN_VERSION) {
            const pcb = try structure.loads(KicadPcb, allocator, .{ .string = content }, root_symbol);
            return PcbFile{ .kicad_pcb = pcb };
        }

        const ast = @import("../ast.zig");
        const tokenizer = @import("../tokenizer.zig");

        // pre-scan: collect every referenced net name (pads, tracks, vias,
        // zones) and synthesize the numbering — sorted unique names get
        // 1..n, "" is always 0. The rule is reference-order independent and
        // process-deterministic by construction (BACKLOG P0.2 M0).
        var scan_arena = std.heap.ArenaAllocator.init(allocator);
        defer scan_arena.deinit();
        const scan_alloc = scan_arena.allocator();

        const tokens = try tokenizer.tokenize(scan_alloc, content);
        const root = try ast.parseBorrowedFast(scan_alloc, content, tokens);

        var found = std.array_list.Managed([]const u8).init(scan_alloc);
        try collectNetNames(root, &found);

        std.mem.sort([]const u8, found.items, {}, struct {
            fn lessThan(_: void, a: []const u8, b: []const u8) bool {
                return std.mem.order(u8, a, b) == .lt;
            }
        }.lessThan);

        var names = std.array_list.Managed([]const u8).init(scan_alloc);
        for (found.items) |name| {
            if (name.len == 0) continue; // "" is the implicit net 0
            if (names.items.len > 0 and std.mem.eql(u8, names.items[names.items.len - 1], name)) continue;
            try names.append(name);
        }

        var table = structure.NetNameTable{ .names = names.items };
        structure.read_net_names = &table;
        defer structure.read_net_names = null;

        var pcb = try structure.loads(KicadPcb, allocator, .{ .string = content }, root_symbol);

        // synthesize the in-memory net table the rest of the toolchain
        // expects (v10 files have none)
        if (pcb.nets.first == null and names.items.len > 0) {
            const NodeType = @TypeOf(pcb.nets).Node;
            const zero = try allocator.create(NodeType);
            zero.* = NodeType{ .data = Net{ .number = 0, .name = try allocator.dupe(u8, "") } };
            pcb.nets.append(zero);
            for (names.items, 1..) |name, number| {
                const node = try allocator.create(NodeType);
                node.* = NodeType{ .data = Net{
                    .number = @intCast(number),
                    .name = try allocator.dupe(u8, name),
                } };
                pcb.nets.append(node);
            }

            // Back-fill pad net names. A v10 pad carries (net "name") only; the
            // net_ref resolves the number but leaves Net.name null (it is a
            // v9-only field). The rest of the toolchain reads pad.net.name to
            // re-associate fbrk nets with existing kicad nets on rebuild
            // (libs/nets.bind_fbrk_nets_to_kicad_nets) — an absent name makes
            // that binding silently fail, so the net is re-inserted with a new
            // number and the old one removed, which disconnects the routed
            // segments referencing it (BACKLOG BUG-2). Numbering is dense
            // (name index+1, "" = 0), so names.items[number-1] is authoritative.
            var fp_it = pcb.footprints.first;
            while (fp_it) |fp_node| : (fp_it = fp_node.next) {
                var pad_it = fp_node.data.pads.first;
                while (pad_it) |pad_node| : (pad_it = pad_node.next) {
                    if (pad_node.data.net) |*net| {
                        if (net.name == null) {
                            const num = net.number;
                            if (num == 0) {
                                net.name = try allocator.dupe(u8, "");
                            } else if (num >= 1 and @as(usize, @intCast(num)) <= names.items.len) {
                                net.name = try allocator.dupe(u8, names.items[@as(usize, @intCast(num)) - 1]);
                            }
                        }
                    }
                }
            }
        }

        return PcbFile{ .kicad_pcb = pcb };
    }

    pub fn dumps(self: PcbFile, allocator: std.mem.Allocator, out: structure.output) !void {
        // P0.2 S7 flag day (Option B, upgrade-on-write): atopile always writes
        // the v10 dialect; v9 is read-only. A board read as v9 is re-emitted as
        // v10 and stamped with the current format version.
        structure.write_dialect = .v10;
        defer structure.write_dialect = .v9;

        var pcb = self.kicad_pcb;
        pcb.version = KICAD_PCB_VERSION;

        var net_names = std.AutoHashMap(i32, []const u8).init(allocator);
        defer net_names.deinit();
        var it = pcb.nets.first;
        while (it) |node| : (it = node.next) {
            try net_names.put(node.data.number, node.data.name orelse "");
        }
        structure.write_net_names = &net_names;
        defer structure.write_net_names = null;

        try structure.dumps(pcb, allocator, root_symbol, out);
    }

    pub fn free(self: *PcbFile, allocator: std.mem.Allocator) void {
        structure.free(KicadPcb, allocator, self.kicad_pcb);
    }
};
