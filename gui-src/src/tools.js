// Tool definitions. Each field's value is kept as Python source text, so what the
// dialog shows is exactly what lands in the script.
//
// kinds: num | choice | anchor | body | bodies | edges | face | facename | pair | text
// Every field is always shown, so Tab reaches every parameter.

export const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

// Shortcuts: operations on Control (Mac) / Alt (elsewhere); view on Option (Mac) / Alt+Shift.
// Written as "B", "Shift+F". Matched on the physical key (event.code), so Option's special
// characters never get in the way.
export const OPS_MOD = IS_MAC ? "ctrl" : "alt";
export const VIEW_MOD = IS_MAC ? "alt" : "altshift";
const OPS_LABEL = IS_MAC ? "⌃" : "Alt+";
const VIEW_LABEL = IS_MAC ? "⌥" : "Alt+Shift+";
export const keyLabel = (k, mod = OPS_LABEL) => k ? mod + k.replace("Shift+", IS_MAC ? "⇧" : "Shift+") : "";
export const viewKeyLabel = (k) => keyLabel(k, VIEW_LABEL);
// spelled out for dialogs: "Control+B" (Mac) / "Alt+B"
export const keyWords = (k) => k ? (IS_MAC ? "Control+" : "Alt+") + k : "";

const ROLE = { key: "role", label: "Role", kind: "choice", options: [["solid", "Solid"], ["reference", "Reference (ghost)"]], def: "solid" };
const AT = { key: "at", label: "On", kind: "anchor", placeholder: "world origin (click a face or anchor)" };
const SPIN = { key: "spin", label: "Angle 0", kind: "num", unit: "°", placeholder: "0" };

const OP_NEW = { key: "op", label: "Result", kind: "choice", options: [["new", "New body"], ["join", "Join"], ["cut", "Cut"]], def: "new" };
const TARGET = { key: "target", label: "Into body", kind: "body", noAuto: true, placeholder: "the profile's body (Join / Cut)" };
const PROFILE = { key: "profile", label: "Profile", kind: "face", required: true, placeholder: "click a profile or a flat face" };

// Toolbar: groups with their most-used tools as big buttons; the group label opens all of them.
export const GROUPS = [
  { id: "create", label: "Create", main: ["sketch", "box", "cylinder"], tools: ["sketch", "text", "box", "cylinder", "sphere", "cone", "torus", "coil", "polygon",
    "profile_line", "profile_rect", "profile_circle"] },
  { id: "form", label: "Form", main: ["revolve", "loft", "sweep"], tools: ["revolve", "loft", "sweep", "extrude"] },
  { id: "modify", label: "Modify", main: ["extrude", "hole", "fillet", "shell", "combine"], tools: ["extrude", "hole", "thread", "fillet", "chamfer", "shell", "draft", "combine", "split", "scale"] },
  { id: "position", label: "Position", main: ["move", "place"], tools: ["move", "move_p2p", "place", "rotate", "place_on_bed"] },
  { id: "pattern", label: "Pattern", main: ["pattern_rect", "mirror"], tools: ["pattern_rect", "pattern_circ", "mirror", "copy"] },
  { id: "construct", label: "Construct", main: ["plane", "axis"], tools: ["plane", "plane_edge", "midplane", "axis"] },
  { id: "inspect", label: "Inspect", main: ["measure", "section"], tools: ["measure", "section", "set_anchor"] },
];

// Tools whose subject is a whole body: a selected face or edge counts as its body.
export const BODY_TOOLS = new Set(["move", "rotate", "place_on_bed", "pattern_rect", "pattern_circ", "mirror", "copy",
  "scale", "combine", "split", "set_anchor", "shell"]);

export const TOOLS = {
  box: { label: "Box", key: "B", op: "box", size: { kind: "rect", x: "length", y: "width" }, height: "height",
    help: "Click the face to build on, then the start point, then the opposite corner. Drag the arrows or type sizes. A negative height grows into the face.",
    fields: [AT, { key: "length", label: "Length (X)", kind: "num", def: "20" }, { key: "width", label: "Width (Y)", kind: "num", def: "20" },
      { key: "height", label: "Height (Z)", kind: "num", def: "10" },
      { key: "origin", label: "Origin", kind: "choice", options: [["corner", "Corner"], ["center_xy", "Center of base"], ["center", "Center"]], def: "corner" }, SPIN, ROLE] },
  cylinder: { label: "Cylinder", key: "C", op: "cylinder", size: { kind: "radial", r: "r" }, height: "h",
    help: "Click the face, then the center, then a point on the rim (it becomes angle 0). A negative height grows into the face.",
    fields: [AT, { key: "r", label: "Radius", kind: "num", def: "5" }, { key: "h", label: "Height", kind: "num", def: "10" }, SPIN, ROLE] },
  cone: { label: "Cone", key: "K", op: "cone", size: { kind: "radial", r: "r1" }, height: "h",
    fields: [AT, { key: "r1", label: "Base radius", kind: "num", def: "6" }, { key: "r2", label: "Top radius", kind: "num", def: "2" },
      { key: "h", label: "Height", kind: "num", def: "10" }, SPIN, ROLE] },
  sphere: { label: "Sphere", key: "S", op: "sphere", size: { kind: "radial", r: "r" },
    fields: [AT, { key: "r", label: "Radius", kind: "num", def: "5" }, SPIN, ROLE] },
  torus: { label: "Torus", key: "T", op: "torus", size: { kind: "radial", r: "R" },
    fields: [AT, { key: "R", label: "Ring radius", kind: "num", def: "10" }, { key: "r", label: "Tube radius", kind: "num", def: "2" }, SPIN, ROLE] },
  coil: { label: "Coil", key: "Shift+H", op: "coil", size: { kind: "radial", r: "R" }, height: "pitch",
    fields: [AT, { key: "R", label: "Radius", kind: "num", def: "8" }, { key: "turns", label: "Turns", kind: "num", def: "4" },
      { key: "pitch", label: "Pitch", kind: "num", def: "5" }, { key: "size", label: "Wire size", kind: "num", def: "2" },
      { key: "section", label: "Section", kind: "choice", options: [["circle", "Circle"], ["square", "Square"]], def: "circle" },
      { key: "hand", label: "Hand", kind: "choice", options: [["right", "Right"], ["left", "Left"]], def: "right" }, SPIN, ROLE] },
  polygon: { label: "Polygon", key: "N", op: "polygon", size: { kind: "radial", r: "r" }, height: "h",
    help: "Click the face, the center, then a vertex: that vertex is v0 at angle 0.",
    fields: [AT, { key: "n", label: "Sides", kind: "num", def: "6" }, { key: "r", label: "Radius to vertex", kind: "num", def: "10" },
      { key: "h", label: "Height", kind: "num", def: "5" }, SPIN, ROLE] },

  sketch: { label: "Sketch", key: "Shift+K", op: "sketch", sketch: true,
    help: "Draw lines, rectangles, circles and arcs on a flat face (or the bed), constrain and dimension them; the closed regions extrude, revolve, loft and sweep.",
    fields: [AT] },
  text: { label: "Text", key: "Shift+X", op: "text",
    help: "Click a flat face (or an anchor) for the lettering, then Push/Pull it: a positive distance embosses, a negative one engraves. Size is the height of capital letters.",
    fields: [{ key: "text", label: "Text", kind: "text", required: true, def: "Text", placeholder: "letters (more lines: in the script, with \\n)" }, AT,
      { key: "size", label: "Size", kind: "num", def: "5" },
      { key: "bold", label: "Weight", kind: "choice", options: [["False", "Regular"], ["True", "Bold"]], def: "False", raw: ["False", "True"] },
      { key: "halign", label: "Align", kind: "choice", options: [["center", "Centre"], ["left", "Left"], ["right", "Right"]], def: "center" },
      { key: "valign", label: "Vertical", kind: "choice", options: [["center", "Centre"], ["baseline", "Baseline"], ["top", "Top"], ["bottom", "Bottom"]], def: "center" },
      { key: "font", label: "Font", kind: "text", placeholder: "sans (built in), a .ttf path or a font name" }, SPIN] },
  profile_line: { label: "Polyline profile", key: "Shift+L", op: "profile", polyline: true,
    help: "Click the face to draw on, then click points (they snap to the face grid and to dots). Click the first point to close, or press Enter. Backspace removes the last point.",
    fields: [AT, { key: "points", label: "Points", kind: "points", required: true, placeholder: "click points in the view" },
      { key: "closed", label: "Shape", kind: "choice", options: [["True", "Closed (cross-section)"], ["False", "Open (sweep path)"]], def: "True", raw: ["True", "False"] }] },
  profile_rect: { label: "Rectangle profile", key: "Shift+B", op: "profile", size: { kind: "rect", x: "length", y: "width" },
    help: "A flat rectangle to revolve, loft or sweep. Click the face, the start corner, then the opposite corner.",
    fields: [AT, { key: "length", label: "Length (X)", kind: "num", def: "20" }, { key: "width", label: "Width (Y)", kind: "num", def: "10" },
      { key: "origin", label: "Origin", kind: "choice", options: [["corner", "Corner"], ["center", "Center"]], def: "corner" }, SPIN] },
  profile_circle: { label: "Circle profile", key: "Shift+C", op: "profile", size: { kind: "radial", r: "r" },
    help: "A flat circle to revolve, loft or sweep. Click the face, the center, then a point on the rim.",
    fields: [AT, { key: "r", label: "Radius", kind: "num", def: "5" }, SPIN] },

  revolve: { label: "Revolve", key: "V", op: "revolve",
    help: "Click a profile (or a flat face), then the axis: X or Y of the profile, an edge, or a round face. Negative angle or ⇄ turns the other way.",
    fields: [PROFILE, { key: "axis", label: "Axis", kind: "line", axes: true, placeholder: "the profile's Y (or click an edge)" },
      { key: "angle", label: "Angle", kind: "num", unit: "°", def: "360", flip: true }, OP_NEW, TARGET] },
  loft: { label: "Loft", key: "L", op: "loft",
    help: "Click the cross-sections in order: profiles or flat faces (the top of one body, the bottom of another…).",
    fields: [{ key: "sections", label: "Sections", kind: "faces", required: true, ordered: true },
      { key: "ruled", label: "Sides", kind: "choice", options: [["False", "Smooth"], ["True", "Straight (ruled)"]], def: "False", raw: ["True", "False"] }, OP_NEW, TARGET] },
  sweep: { label: "Sweep", key: "W", op: "sweep",
    help: "Click the cross-section, then the path: an open profile, or edges that join end to end. Draw the section across the start of the path.",
    fields: [PROFILE, { key: "path", label: "Path", kind: "path", required: true },
      { key: "twist", label: "Twist", kind: "choice", options: [["follow", "Follow the path"], ["frenet", "Frenet (helices)"]], def: "follow" }, OP_NEW, TARGET] },
  shell: { label: "Shell", key: "O", op: "shell",
    help: "Hollow a body. Click faces to leave open (none: a closed hollow part). Inside keeps the outer size.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "thickness", label: "Wall", kind: "num", def: "2" },
      { key: "faces", label: "Open faces", kind: "faces", placeholder: "none: closed hollow (click faces)" },
      { key: "direction", label: "Walls grow", kind: "choice", options: [["inside", "Inside (keep outer size)"], ["outside", "Outside (keep inner size)"]], def: "inside" }] },
  draft: { label: "Draft", key: "G", op: "draft",
    help: "Click the pull plane (e.g. the bottom), then the faces to tilt. Positive angles narrow the part going away from that plane.",
    fields: [{ key: "plane", label: "Pull plane", kind: "face", required: true, placeholder: "click the face it pulls away from" },
      { key: "faces", label: "Faces", kind: "faces", required: true },
      { key: "angle", label: "Angle", kind: "num", unit: "°", def: "3", flip: true }, { key: "body", label: "Body", kind: "body", hidden: true }] },

  move: { label: "Move", key: "M", op: "move", gizmo: true,
    help: "Drag the arrows to slide and the rings to turn, or type values. Turns happen about the pivot.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "dx", label: "X", kind: "num" },
      { key: "dy", label: "Y", kind: "num" }, { key: "dz", label: "Z", kind: "num" },
      { key: "rx", label: "Turn X", kind: "num", unit: "°" }, { key: "ry", label: "Turn Y", kind: "num", unit: "°" },
      { key: "rz", label: "Turn Z", kind: "num", unit: "°" },
      { key: "pivot", label: "Pivot", kind: "anchor", placeholder: "body origin (Pick to set)", noAuto: true },
      { key: "frame", label: "Axes", kind: "choice", options: [["local", "Body (local)"], ["world", "World"]], def: "local" },
      { key: "along", label: "Axes from", kind: "anchor", placeholder: "an anchor's axes (optional)", noAuto: true }] },
  move_p2p: { label: "Point to point", key: "Shift+M", op: "move",
    help: "Click a point on the body that moves, then where it goes. The body slides; it does not turn (Place aligns faces).",
    fields: [{ key: "point", label: "From", kind: "anchor", required: true, setsBody: true, placeholder: "a point on the moving body" },
      { key: "to", label: "To", kind: "anchor", required: true, placeholder: "target point" },
      { key: "body", label: "Body", kind: "body", required: true },
      { key: "dx", label: "Extra X", kind: "num" }, { key: "dy", label: "Extra Y", kind: "num" }, { key: "dz", label: "Extra Z", kind: "num" }] },
  place: { label: "Place", key: "J", op: "place",
    help: "Click the anchor on the part that moves, then where it goes. Tab cycles snap points on a face.",
    fields: [{ key: "source", label: "Move this", kind: "anchor", required: true, placeholder: "anchor on the moving body" },
      { key: "onto", label: "Onto", kind: "anchor", placeholder: "target anchor (default: print bed)" },
      { key: "mode", label: "Mode", kind: "choice", options: [["mate", "Mate (faces touch)"], ["flush", "Flush (same direction)"], ["axis", "Axis"]], def: "mate" },
      { key: "gap", label: "Gap", kind: "num" }, { key: "angle", label: "Spin", kind: "num", unit: "°" },
      { key: "offset", label: "Offset X, Y", kind: "pair", labels: ["x", "y"] }] },
  rotate: { label: "Rotate", key: "R", op: "rotate",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "angle", label: "Angle", kind: "num", unit: "°", def: "90" },
      { key: "about", label: "About anchor", kind: "anchor", placeholder: "its Z axis (default: body axis)", noAuto: true },
      { key: "axis", label: "Axis", kind: "choice", options: [["Z", "Z"], ["X", "X"], ["Y", "Y"]], def: "Z" },
      { key: "frame", label: "Axes", kind: "choice", options: [["local", "Body (local)"], ["world", "World"]], def: "local" }] },
  place_on_bed: { label: "Place on bed", key: "Shift+J", op: "place_on_bed", help: "Click the face that should lie on the print bed.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "face", label: "Face down", kind: "facename", def: "-Z" }] },

  extrude: { label: "Push/Pull", key: "E", alt: "Q", op: "extrude", help: "Click a flat face. A negative distance pushes into the part.",
    fields: [{ key: "face", label: "Face", kind: "face", required: true }, { key: "distance", label: "Distance", kind: "num", def: "5" },
      { key: "op", label: "Result", kind: "choice", options: [["join", "Join"], ["cut", "Cut"], ["new", "New body"]], def: "join" }] },
  hole: { label: "Hole", key: "H", op: "hole",
    help: "Click a point on a face (it snaps to centres and the grid). Pick a screw size for standard clearance, tap drill or a modelled thread, or type a diameter. Leave Depth empty to go through.",
    fields: [{ key: "at", label: "Position", kind: "anchor", required: true, placeholder: "click a point on a face" },
      { key: "size", label: "Screw size", kind: "text", placeholder: "e.g. M3 (or type a diameter below)" },
      { key: "fit", label: "Fit", kind: "choice", options: [["clearance", "Clearance (normal)"], ["close", "Clearance (close)"], ["loose", "Clearance (loose)"],
        ["tapped", "Tapped (modelled thread)"], ["tap_drill", "Tap drill only"]], def: "clearance" },
      { key: "d", label: "Diameter", kind: "num", placeholder: "from the screw size" },
      { key: "depth", label: "Depth", kind: "num", placeholder: "through all" },
      { key: "head", label: "Head", kind: "choice", options: [["None", "None"], ["counterbore", "Counterbore"], ["countersink", "Countersink"]], def: "None", raw: ["None"] },
      { key: "head_d", label: "Head diameter", kind: "num", placeholder: "from the screw size" },
      { key: "head_depth", label: "Counterbore depth", kind: "num", placeholder: "from the screw size" },
      { key: "cs_angle", label: "Countersink angle", kind: "num", unit: "°", placeholder: "90" },
      { key: "tip", label: "Drill point", kind: "num", unit: "°", placeholder: "flat (118 for a drill)" },
      { key: "thread_length", label: "Thread length", kind: "num", placeholder: "the whole hole" },
      { key: "allowance", label: "Print allowance", kind: "num", placeholder: "0 (0.2–0.4 for FDM)" },
      { key: "hand", label: "Thread hand", kind: "choice", options: [["right", "Right"], ["left", "Left"]], def: "right" },
      { key: "body", label: "Body", kind: "body", noAuto: true, placeholder: "the body under the point" }] },
  thread: { label: "Thread", key: "Shift+T", op: "thread",
    help: "Click a shaft's side or a hole's wall. The size comes from the diameter (a shaft's, or a hole's tap drill) unless you type one. Allowance gives printed threads room.",
    fields: [{ key: "face", label: "Face", kind: "face", required: true, placeholder: "a round face" },
      { key: "size", label: "Size", kind: "text", placeholder: "from the diameter (e.g. M8, M8x1)" },
      { key: "length", label: "Length", kind: "num", placeholder: "the whole face" },
      { key: "offset", label: "Start in by", kind: "num", placeholder: "0" },
      { key: "allowance", label: "Print allowance", kind: "num", placeholder: "0 (0.2–0.4 for FDM)" },
      { key: "hand", label: "Hand", kind: "choice", options: [["right", "Right"], ["left", "Left"]], def: "right" }] },
  fillet: { label: "Fillet", key: "F", op: "fillet", help: "Click edges (or a face for all its edges), or one edge then All. Fillets can be added in any order.",
    fields: [{ key: "edges", label: "Edges", kind: "edges", required: true }, { key: "r", label: "Radius", kind: "num", def: "1" },
      { key: "body", label: "Body", kind: "body", hidden: true }] },
  chamfer: { label: "Chamfer", key: "Shift+F", op: "chamfer", help: "Click edges (or a face for all its edges).",
    fields: [{ key: "edges", label: "Edges", kind: "edges", required: true }, { key: "d", label: "Distance", kind: "num", def: "0.6" },
      { key: "d2", label: "Second distance", kind: "num" }, { key: "body", label: "Body", kind: "body", hidden: true }] },
  combine: { label: "Combine", key: "U", op: "join", ops: ["join", "cut", "intersect"], help: "The target keeps its name. Tools are removed unless you keep them.",
    fields: [{ key: "target", label: "Target", kind: "body", required: true }, { key: "tools", label: "Tools", kind: "bodies", required: true },
      { key: "keep_tools", label: "Tools after", kind: "choice", options: [["False", "Remove"], ["True", "Keep"], ["reference", "Keep as reference"]], def: "False", raw: ["False", "True"] }] },
  split: { label: "Split", key: "X", op: "split", help: "The body keeps the part behind the plane; the part in front becomes a new body.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "plane", label: "Plane", kind: "anchor", planes: true, def: '"XY"' }] },
  scale: { label: "Scale", key: "Shift+S", op: "scale",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "factor", label: "Factor", kind: "num", def: "1.5" },
      { key: "about", label: "About", kind: "anchor", placeholder: "body origin", noAuto: true }] },

  pattern_rect: { label: "Rect pattern", key: "P", op: "pattern_rect", inspect: true,
    help: "Pick the body, then an edge for direction 1 (or leave it to use the body's axes). Direction 2 is the right-hand perpendicular unless you pick an edge for it. Negative spacing or ⇄ flips a direction.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true },
      { key: "along", label: "Direction 1", kind: "line", placeholder: "body X (click an edge)" },
      { key: "x", label: "1: count, gap", kind: "pair", labels: ["count", "spacing"], def: ["3", "10"], flip: true },
      { key: "y_along", label: "Direction 2", kind: "line", placeholder: "right-hand from 1 (or click an edge)", noAuto: true },
      { key: "y", label: "2: count, gap", kind: "pair", labels: ["count", "spacing"], flip: true },
      { key: "z_along", label: "Direction 3", kind: "line", placeholder: "up: 1 × 2 (or click an edge)", noAuto: true },
      { key: "z", label: "3: count, gap", kind: "pair", labels: ["count", "spacing"], flip: true },
      { key: "frame", label: "No edge: axes", kind: "choice", options: [["local", "Body (local)"], ["world", "World"]], def: "local" }] },
  pattern_circ: { label: "Circular pattern", key: "Shift+P", op: "pattern_circ", inspect: true,
    help: "Pick the body, then the axis: a hole, round face, circular or straight edge, or a flat face (its normal). Copies turn by the right-hand rule; a negative angle or ⇄ turns the other way.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true },
      { key: "axis", label: "Axis", kind: "line", placeholder: "world Z through origin (click an edge or hole)" },
      { key: "count", label: "Count", kind: "num", def: "6" },
      { key: "angle", label: "Angle", kind: "num", unit: "°", def: "360", flip: true }] },
  mirror: { label: "Mirror", key: "Y", op: "mirror", help: "Makes a mirrored copy, named as if built from scratch.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "plane", label: "Plane", kind: "anchor", planes: true, def: '"YZ"', noAuto: true }] },
  copy: { label: "Copy", key: "D", op: "copy", fields: [{ key: "body", label: "Body", kind: "body", required: true }] },

  set_anchor: { label: "Set anchor", key: "A", op: "set_anchor", help: "Names a point on a body so later steps can use it. Redefining a name affects only later steps.",
    fields: [{ key: "body", label: "Body", kind: "body", required: true }, { key: "name", label: "Name", kind: "text", def: "mount" },
      { key: "at", label: "At", kind: "anchor", required: true }] },
  plane: { label: "Offset plane", op: "plane",
    help: "A construction plane: click a face or anchor, then give a distance along its normal and/or a tilt about its X or Y. Sketch on it, split or mirror with it.",
    fields: [{ key: "at", label: "From", kind: "anchor", placeholder: "the bed (click a face or anchor)" },
      { key: "offset", label: "Offset", kind: "num", def: "10" }, { key: "angle", label: "Tilt", kind: "num", unit: "°" },
      { key: "about", label: "Tilt about", kind: "choice", options: [["X", "Its X axis"], ["Y", "Its Y axis"]], def: "X" },
      { key: "size", label: "Shown size", kind: "num", placeholder: "about the part's size" }] },
  plane_edge: { label: "Plane through edge", op: "plane",
    help: "A construction plane through a straight edge, turned by the angle from the face beside it (or from the face you pick).",
    fields: [{ key: "about", label: "Edge", kind: "line", required: true, placeholder: "click a straight edge",
        hint: "Click the straight edge the plane turns about." },
      { key: "angle", label: "Angle", kind: "num", unit: "°", def: "45", flip: true },
      { key: "at", label: "From face", kind: "anchor", noAuto: true, placeholder: "the edge's flat face" },
      { key: "size", label: "Shown size", kind: "num", placeholder: "about the part's size" }] },
  midplane: { label: "Midplane", op: "plane",
    help: "A construction plane halfway between two parallel faces: click both.",
    fields: [{ key: "mid", label: "Faces", kind: "faces", required: true, ordered: true, placeholder: "click two parallel faces",
        hint: "Click two parallel faces (they may be on different bodies)." },
      { key: "size", label: "Shown size", kind: "num", placeholder: "about the part's size" }] },
  axis: { label: "Axis", op: "axis",
    help: "A construction axis along a straight edge, or through the centre of a hole, round face or circular edge. Use it to revolve, pattern or rotate about.",
    fields: [{ key: "ref", label: "Through", kind: "line", required: true, placeholder: "click an edge, hole or round face",
        hint: "Click a straight edge (the axis runs along it), or a hole, round face or circular edge (its centre line)." },
      { key: "length", label: "Shown length", kind: "num", placeholder: "the edge, or the part's size" }] },
  section: { label: "Section", key: "Shift+I", op: null, section: true,
    help: "Cut the view open with a plane to see inside: click a flat face or pick a world plane, then move the cut. View only: nothing is written." },
  measure: { label: "Measure", key: "I", op: null, help: "Click two points: anchors, face centers, holes, corners, or anywhere on a face." },
};

export const OP_TO_TOOL = Object.fromEntries(Object.entries(TOOLS).filter(([id]) => id !== "move_p2p").flatMap(([id, t]) => (t.ops || [t.op]).filter(Boolean).map((o) => [o, id])));

// View shortcuts (Option on Mac)
export const VIEW_KEYS = [
  ["0", "home", "Home view"], ["F", "fit", "Fit all"], ["1", "front", "Front"], ["2", "back", "Back"], ["3", "left", "Left"],
  ["4", "right", "Right"], ["5", "top", "Top"], ["6", "bottom", "Bottom"], ["A", "anchors", "Show anchors"],
  ["R", "ghosts", "Show removed bodies"], ["G", "grid", "Show grid"], ["E", "script", "Show script"],
];

// ------------------------------------------------------------------ icons (24 px, stroke = currentColor)
const S = (body) => `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">${body}</svg>`;
const A = "fill=\"var(--accent)\" fill-opacity=\".22\"";
export const ICONS = {
  box: S(`<path d="M4 8l8-4 8 4v8l-8 4-8-4z" ${A}/><path d="M4 8l8 4 8-4M12 12v8"/>`),
  cylinder: S(`<ellipse cx="12" cy="6" rx="7" ry="2.6" ${A}/><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6"/>`),
  cone: S(`<path d="M12 3l7 15c0 1.5-3.1 2.6-7 2.6S5 19.5 5 18z" ${A}/><path d="M5 18c0-1.4 3.1-2.6 7-2.6s7 1.2 7 2.6"/>`),
  sphere: S(`<circle cx="12" cy="12" r="8" ${A}/><path d="M4 12c0 1.9 3.6 3.4 8 3.4s8-1.5 8-3.4"/>`),
  torus: S(`<ellipse cx="12" cy="12" rx="9" ry="5" ${A}/><ellipse cx="12" cy="11.4" rx="3.6" ry="1.6"/>`),
  coil: S(`<path d="M6 19c4 0 12-2 12-3.5S6 14 6 12.5 18 11 18 9.5 6 8 6 6.5 14 5 18 5"/>`),
  polygon: S(`<path d="M8 4h8l4 8-4 8H8l-4-8z" ${A}/>`),
  extrude: S(`<path d="M4 15l8-4 8 4-8 4z" ${A}/><path d="M12 13V3M9 6l3-3 3 3"/>`),
  hole: S(`<path d="M3 9l9-4 9 4v6l-9 4-9-4z" ${A}/><ellipse cx="12" cy="9.2" rx="3.4" ry="1.5" fill="var(--panel)"/><path d="M8.6 9.2v5M15.4 9.2v5" stroke-dasharray="1.6 1.4"/>`),
  thread: S(`<path d="M8 3h8M8 21h8M8 3v18M16 3v18" /><path d="M8 6l8 2M8 10l8 2M8 14l8 2M8 18l8 2" stroke="var(--accent)" stroke-width="1.8"/>`),
  fillet: S(`<path d="M4 20V10a6 6 0 0 1 6-6h10" /><path d="M4 20h16V4" stroke-dasharray="2 2"/><path d="M4 10a6 6 0 0 1 6-6" stroke="var(--accent)" stroke-width="2.2"/>`),
  chamfer: S(`<path d="M4 20V10l6-6h10"/><path d="M4 10l6-6" stroke="var(--accent)" stroke-width="2.2"/><path d="M4 20h16V4" stroke-dasharray="2 2"/>`),
  combine: S(`<rect x="3" y="7" width="11" height="11" rx="1" ${A}/><circle cx="15" cy="10" r="6"/>`),
  split: S(`<path d="M4 9l8-4 8 4v6l-8 4-8-4z"/><path d="M2 12h20" stroke="var(--accent)" stroke-width="2" stroke-dasharray="3 2"/>`),
  scale: S(`<rect x="3" y="11" width="10" height="10" rx="1" ${A}/><path d="M13 3h8v8M21 3l-8 8"/>`),
  move: S(`<path d="M12 3v18M3 12h18M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5"/>`),
  move_p2p: S(`<circle cx="6" cy="18" r="2" ${A}/><circle cx="18" cy="6" r="2" ${A}/><path d="M7.5 16.5l9-9M13 7.5h3.5V11" stroke-dasharray="0"/>`),
  place: S(`<rect x="7" y="3" width="10" height="7" rx="1" ${A}/><path d="M3 20h18M12 10v6M9.5 13.5L12 16l2.5-2.5"/>`),
  rotate: S(`<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4v4h-4"/>`),
  place_on_bed: S(`<path d="M7 9l5-3 5 3v5l-5 3-5-3z" ${A}/><path d="M3 21h18"/>`),
  pattern_rect: S(`<rect x="3" y="3" width="7" height="7" rx="1" ${A}/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>`),
  pattern_circ: S(`<circle cx="12" cy="4.5" r="2.2" ${A}/><circle cx="19" cy="14" r="2.2"/><circle cx="5" cy="14" r="2.2"/><circle cx="12" cy="12" r="1"/>`),
  mirror: S(`<path d="M12 2v20" stroke-dasharray="2 2"/><path d="M9 6L3 18h6z" ${A}/><path d="M15 6l6 12h-6z"/>`),
  copy: S(`<rect x="8" y="8" width="12" height="12" rx="1.5" ${A}/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>`),
  plane: S(`<path d="M3 16l6-9h12l-6 9z" ${A}/><path d="M9 7v-3M21 7v-3M3 16v3" stroke-dasharray="1.6 1.4"/>`),
  plane_edge: S(`<path d="M3 18h12l6-6H9z" ${A}/><path d="M3 18L9 6h12l-6 12" stroke-dasharray="1.6 1.4"/>`),
  midplane: S(`<path d="M3 6h14l4-3M3 20h14l4-3" /><path d="M3 13h14l4-3" stroke="var(--accent)" stroke-width="2"/>`),
  axis: S(`<path d="M4 20L20 4" stroke="var(--accent)" stroke-width="2" stroke-dasharray="3 2"/><circle cx="12" cy="12" r="5" ${A}/>`),
  section: S(`<path d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path d="M4 8l8 4 8-4" /><path d="M12 12v8" /><path d="M2 13h20" stroke="var(--section)" stroke-width="2.2"/>`),
  measure: S(`<path d="M3 16l13-13 5 5L8 21z" ${A}/><path d="M7 12l2 2M10 9l2 2M13 6l2 2"/>`),
  set_anchor: S(`<circle cx="12" cy="12" r="2.5" ${A}/><path d="M12 9.5V3M14.5 12H21M12 12l-6 6"/>`),
  sketch: S(`<path d="M4 20h16" stroke-dasharray="2 2"/><path d="M5 16l5-10 9 4-4 6z" ${A}/><circle cx="5" cy="16" r="1.4"/><circle cx="10" cy="6" r="1.4"/><circle cx="19" cy="10" r="1.4"/><circle cx="15" cy="16" r="1.4"/>`),
  text: S(`<path d="M5 6V4h14v2M12 4v16M9 20h6"/><path d="M3 21h18" stroke="var(--accent)" stroke-width="1.8"/>`),
  profile_line: S(`<path d="M4 18l4-11 6 6 6-9" /><circle cx="4" cy="18" r="1.6" ${A}/><circle cx="8" cy="7" r="1.6" ${A}/><circle cx="14" cy="13" r="1.6" ${A}/><circle cx="20" cy="4" r="1.6" ${A}/>`),
  profile_rect: S(`<rect x="4" y="7" width="16" height="10" ${A} stroke-dasharray="0"/><circle cx="4" cy="17" r="1.4"/><circle cx="20" cy="7" r="1.4"/>`),
  profile_circle: S(`<circle cx="12" cy="12" r="7.5" ${A}/><circle cx="12" cy="12" r="1.2"/><path d="M12 12h7.5"/>`),
  revolve: S(`<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M12 6h5v8h-5" ${A}/><path d="M7 9a5 2 0 0 0 0 4M5 17c1.8 1.3 4.3 2 7 2s5.2-.7 7-2"/><path d="M17 15.5l2 1.5-2.4.8"/>`),
  loft: S(`<path d="M5 18l3-6h8l3 6z" ${A}/><ellipse cx="12" cy="6" rx="4" ry="1.6"/><path d="M8 6l-3 12M16 6l3 12" stroke-dasharray="2 1.6"/>`),
  sweep: S(`<path d="M4 18c3-9 9-12 16-12" stroke-dasharray="2 2"/><circle cx="5" cy="17" r="2.5" ${A}/><path d="M7 19.5c3-8 8-11 13-11M3 14.5c2-7 9-11 17-11"/>`),
  shell: S(`<path d="M4 7l8-4 8 4v10l-8 4-8-4z" ${A}/><path d="M7 8.5l5-2.5 5 2.5v6.5l-5 2.5-5-2.5z"/>`),
  draft: S(`<path d="M7 5h10l3 15H4z" ${A}/><path d="M7 5v15" stroke-dasharray="2 2"/><path d="M2 20h20"/>`),
};

// ------------------------------------------------------------------ value text helpers
const UNIT = /^\s*(-?[\d.]+)\s*(mm|cm|deg|inch|in)\s*$/;

export function normalizeNum(text, params) {
  let t = String(text ?? "").trim();
  if (!t) return "";
  const m = t.match(UNIT);
  if (m) t = `${m[1]} * ${m[2] === "in" ? "inch" : m[2]}`;
  if (params && Object.prototype.hasOwnProperty.call(params, t)) t = `P.${t}`;
  return t;
}

// Evaluate a value for drawing previews: numbers, units, P.name and simple arithmetic.
export function evalNum(text, params, depth = 0) {
  let t = String(text ?? "").trim();
  if (!t) return null;
  if (depth > 8) return null;
  t = t.replace(/\bP\.([A-Za-z_]\w*)/g, (_, n) => {
    const v = params && params[n] !== undefined ? evalNum(params[n], params, depth + 1) : null;
    return v === null ? "NaN" : `(${v})`;
  });
  if (params) t = t.replace(/\b([A-Za-z_]\w*)\b/g, (w) => (["mm", "cm", "inch", "deg", "m", "rad"].includes(w) || !(w in params)) ? w : `(${evalNum(params[w], params, depth + 1)})`);
  t = t.replace(/\bmm\b/g, "1").replace(/\bcm\b/g, "10").replace(/\binch\b/g, "25.4").replace(/\bdeg\b/g, "1").replace(/\bm\b/g, "1000");
  if (!/^[\d\s.+\-*/()eE]+$/.test(t)) return null;
  try { const v = Function(`"use strict"; return (${t});`)(); return Number.isFinite(v) ? v : null; } catch (e) { return null; }
}

// split "[a, b(c, d), 'e,f']" -> ["a", "b(c, d)", "'e,f'"]
export function splitList(text) {
  let t = String(text || "").trim();
  if (t.startsWith("[") && t.endsWith("]")) t = t.slice(1, -1);
  else if (t.startsWith("(") && t.endsWith(")")) t = t.slice(1, -1);
  const out = []; let depth = 0, cur = "", quote = null;
  for (const ch of t) {
    if (quote) { cur += ch; if (ch === quote) quote = null; continue; }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
    if ("([{".includes(ch)) depth++;
    if (")]}".includes(ch)) depth--;
    if (ch === "," && depth === 0) { if (cur.trim()) out.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

export const unquote = (t) => { const m = String(t || "").match(/^\s*["'](.*)["']\s*$/); return m ? m[1] : t; };
