# OpenFusion

Parametric solid modeling for 3D printing where every face, edge and anchor has a
stable name, so changing a parameter and rebuilding never quietly breaks your part.

Model by clicking in the app, by writing a `.ofs` script (plain Python), or both at once:
every click becomes one line in the script, and every script edit shows up in the app.
This covers Phases 1 and 2 of the OpenFusion design doc: the engine, the command line,
and the modeling app.

**Everything is scriptable.** The app has no private features: each tool writes one line of the
same Python API documented below, and a script you write by hand opens in the app with every step
editable. The only things that live outside the script are view settings (camera, what is shown),
Measure, and export choices, which on the command line are `openfusion build` options.

## Install (macOS, Apple Silicon)

Needs Python 3.11 to 3.14. The OpenCASCADE geometry kernel comes as a native arm64 wheel.
macOS's own `python3` (in `/usr/bin`) is 3.9 and too old, so build the venv from a newer one:
Anaconda's (`/opt/anaconda3/bin/python3`) or Homebrew's (`brew install python@3.13`).

```bash
cd openfusion
/opt/anaconda3/bin/python3 -m venv .venv     # or: python3.13 -m venv .venv
source .venv/bin/activate
python --version                             # must say 3.11 or newer
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest                             # about 240 tests, 5 to 10 minutes (shell tests measure every wall)
```

If pip says "editable mode currently requires a setuptools-based build", the venv was made
from a Python that is too old: delete `.venv` and recreate it as above.

No Node.js is needed: the app's frontend ships prebuilt in `openfusion/gui/static/`.


## Building the Mac app (for sharing)

OpenFusion also runs as a normal Mac app in its own window: a start screen (new part, open,
recent files, examples), native Open and Save dialogs, a File menu, exports saved where you
choose, and double-click to open `.ofs` files from Finder.

**Try the window from your venv first** (quick check, nothing is bundled):

```bash
pip install -e ".[app]"      # adds pywebview, the native window
openfusion app               # start screen; or: openfusion app part.ofs
```

**Build the app to share** (Apple Silicon, macOS 12+; about 10 minutes the first time):

```bash
./packaging/mac/build_app.sh          # add --open to launch it when done
```

It downloads a standalone Python 3.12 (through `uv`), installs OpenFusion and its libraries into
`build-mac/OpenFusion.app`, trims it, precompiles it, tests it by building the showcase, signs
it ad hoc, and writes `dist/OpenFusion-<version>-arm64.dmg` (and a `.zip`). Your venv and system
Python are not touched. Send the `.dmg`: it contains the app, an Applications shortcut and
"Read me first", which explains the one-time "Open Anyway" step. The build has no Apple
Developer signature, so macOS asks once. The app writes its log to
`~/Library/Logs/OpenFusion.log`, recent files to `~/Library/Application Support/OpenFusion`,
and example copies to `~/Documents/OpenFusion`.

## Building for Linux (Bash)

```bash
cd openfusion
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test,app]"
python -m pytest
openfusion app
```

## Building for Windows (PowerShell)

```bash

cd openfusion
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test,app]"
python -m pytest
openfusion app
```


## Quick start

```bash
openfusion new bracket.ofs           # starter script
openfusion gui bracket.ofs           # the modeling app opens in your browser
```

### The modeling app

- **Highlighted edges** (under the cursor or selected) are drawn thick, at a fixed width on screen at
  any zoom, like the anchor dots.
- **Selecting, like Fusion.** A click picks the face or edge under the cursor (the edge wins when
  you're within a few pixels of it); double-click picks the whole body; Shift-click adds. Tools
  take what they need from the selection: **body tools** (Move, Rotate, patterns, Mirror, Copy,
  Scale, Combine, Split, Place on bed) use the body of whatever face or edge you picked, Push/Pull
  uses the face, Fillet and Chamfer use the edges (a selected face means all its edges), and a
  primitive builds on a selected face. The filter at the top left can switch clicks to Bodies,
  Faces or Edges only.
- **Mouse and trackpad, like Fusion.** Left-drag draws a selection box: left to right (solid blue)
  takes what is entirely inside, right to left (dashed green) anything it touches; inside a tool the
  box adds to the list being picked (edges, faces, bodies). Right-drag orbits and a right-click
  without dragging opens the body menu. Two-finger scroll pans, Shift + two fingers orbits, pinch
  or the mouse wheel zooms at the pointer, middle-drag pans. Only one grid shows at a time: a face
  grid replaces the bed grid while you work on a face.
- **Sketch** (⌃⇧K, first button under CREATE). Click a flat face (or empty space for the bed); the
  view turns to face it. Draw with single keys: **L** line (clicks chain; nearly horizontal or
  vertical lines get that constraint), **R** rectangle, **C** circle, **A** arc (centre, start, end,
  counterclockwise), **O** point, **J** project (click edges of the model: fixed copies that follow it, their ends usable as points), **X** construction (dashed guides that bound no region). Clicking
  near an existing point reuses it. **S** selects (Shift adds); drag a point and the sketch follows,
  keeping its constraints. Constrain the selection with the panel's buttons or keys (**H**
  horizontal, **V** vertical, **I** coincident / on a curve, **P** parallel, **⇧P** perpendicular,
  **T** tangent, **E** equal, **M** midpoint, **⇧C** concentric, **⇧Y** symmetric, **F** fix). **D**
  dimensions it: a line's length, a circle's diameter, an arc's radius, the distance between two
  points (aligned, horizontal or vertical) or from a point to a line, the angle or gap between two
  lines; type a number or a parameter (`P.width`). Double-click a dimension to change it. Every change
  is solved by the engine itself, so what you see is what builds; closed regions are shaded, the
  panel counts the degrees of freedom left ("Fully constrained" at 0), and constraints that can't
  all be met are refused with the reason. ⌘Z undoes inside the sketch; **Finish sketch** writes one
  `sketch(...)` statement; double-click it in the timeline to edit it again. Push/Pull, Revolve,
  Loft and Sweep take the sketch (its outline with holes) or one region of it.
- **Hole** (⌃H) and **Thread** (⌃⇧T, in MODIFY ▾). Click a point on a face for a hole; type a screw size (M3)
  and pick the fit: clearance (close, normal, loose), tap drill, or **tapped** for a real, printable ISO thread.
  Counterbore and countersink sizes come from the screw size too. Thread: click a shaft or a hole's wall.
  (Coil moved to ⌃⇧H.)
- **Text** (⌃⇧X, under CREATE ▾): click a face, type the letters and size; then Push/Pull the text: a
  positive distance embosses, a negative one engraves.
- **Section** (⌃⇧I, under INSPECT): cut the view open to see inside (a shell's walls, a thread's profile, a
  hole's depth). Click a flat face to cut parallel to it, or pick a world plane; drag the slider to move the
  cut, ⇄ (or F) to show the other half. The cut faces are filled in orange. **Keep** leaves it on (the
  *Section ✕* chip at the top turns it off); it only changes the view, nothing is written to the script.
- **Construct** group: Offset plane (also tilted), Plane through edge (at an angle), Midplane (click two
  parallel faces) and Axis (an edge, or a hole's centre line). They are ghosts you can sketch on, split
  or mirror with, and revolve or pattern about.
- **Profiles and forms.** Draw a cross-section on any face (Polyline ⌃⇧L: click points, click the
  first to close; Rectangle ⌃⇧B; Circle ⌃⇧C), then Revolve (⌃V), Loft (⌃L, through profiles or flat
  faces in order) or Sweep (⌃W, along an open profile or a chain of edges). Shell (⌃O) hollows a
  body (pick faces to leave open; walls inside or outside) and Draft (⌃G) tilts faces from a pull
  plane. Profiles are ghosts: snappable, never exported.
- **Toolbar.** Each group shows its most-used tools as big buttons; click the group name
  (CREATE ▾, MODIFY ▾...) for all of them, with their shortcuts.
- **Shortcuts.** Operations are **Control+letter** and the view is **Option+letter/number**
  (on Windows and Linux: Alt+letter and Alt+Shift+letter). Control has almost no system
  conflicts on a Mac, and nothing fires while you type in the script pane. Press **?** for the
  full sheet. The main ones: ⌃B box, ⌃C cylinder, ⌃S sphere, ⌃E (or ⌃Q) Push/Pull, ⌃F fillet,
  ⌃⇧F chamfer, ⌃U combine, ⌃M move, ⌃⇧M point to point, ⌃J place, ⌃P pattern, ⌃Y mirror,
  ⌃I measure. View: ⌥0 home, ⌥F fit, ⌥1 to ⌥6 front, back, left, right, top, bottom.
- **Every tool dialog shows its shortcut** under the help text (e.g. *Shortcut: ⌃B Control+B*).
- **Selection readout.** Whatever is selected is measured in a small panel at the bottom of the view:
  a straight edge's length, an arc's radius, diameter, length and angle, a face's area (plus its radius,
  cone angle or normal), a body's volume, surface area and size. With two things selected it shows the
  minimum distance between them (edge to edge, edge to face, face to face, body to body), the ΔX/ΔY/ΔZ
  and the angle between them; with more, the total length or area.
- **Nothing runs until OK.** A tool shows the exact line it will write; OK (Enter) runs it. If
  the step fails, nothing is written and the error stays in the dialog so you can fix the value.
  Tick **Live result** in a dialog to see the real result on every change instead.
- **Tab** moves through every field of a tool, including Angle 0 and Role, and wraps around.
  Over the view, Tab cycles snap points.
- **Points anywhere.** When a tool wants a point, click anywhere on a face: the point is kept as an
  offset from that face's anchor, e.g. `plate.anchor("+Z").offset(x=12.5, y=-3)`. It snaps to a
  quarter of the visible grid (1 mm on a 5 mm grid; the status bar shows both). Orange dots are
  snap points (center, hole and boss centers, corners, midpoints); within 16 px of one, it wins.
  Pointing into a hole offers the hole's center. Empty space picks a point on the bed. Turn on
  **Anchors** to click any body's anchor triads.
- **Building primitives, Fusion style.** Click the face (or anchor) to build on: the grid turns
  to it. Click the start point anywhere on that plane, then the second point: the opposite corner
  for a box, a point on the rim for the round ones (that point becomes angle 0). A see-through
  ghost follows the cursor, then gets **arrows**: drag them to resize. Drag a height arrow through
  the face for a **negative height**, which builds the shape the other way (into the part, ready
  to cut a hole). The dialog's size fields follow the cursor and the arrows while you drag, and typing
in a field updates the ghost. The arrows only change the dialog's numbers; one statement with the final sizes
  is written on OK.
- **Move** (⌃M): drag the arrows to slide and the rings to turn (0.5 mm and 5° steps; Shift for
  0.1 mm and 1°), or type values. The body moves on screen as you go. **Pivot**: click Set, then
  any point; turns happen about it (default: the body's origin). **Point to point** (⌃⇧M): click
  a point on the moving body, then where it should go.
- **View.** The **ViewCube** (top right) turns with the camera: click any of its faces, edges or
  corners to look from there, drag it to orbit, or use the home and fit buttons under it. The world
  origin and the X (red), Y (green) and Z (blue) axes are always drawn. The **grid follows the
  zoom**: zoom in and it gets finer (down to 0.1 mm), zoom out and it gets coarser.
- **Fillets** can be added in any order: if the kernel cannot add one on top of earlier fillets,
  OpenFusion rebuilds them together as one rounding with proper corner blends. Two roundings that
  meet exactly (a 1 mm wall with r=0.5 on both edges) work too. In the Fillet tool, click one edge
  and then **All** to round every edge of the body.
- **Feeding fields from lists.** With a tool open, clicking a body in the Bodies list fills a body
  field, and clicking a pattern in the timeline adds the whole pattern as tools.
- **Patterns** show their copies see-through, with numbered arrows for each direction (or the axis and turning
  sense), before you press OK. Click an edge of any body for a direction (a hole, round face or circular edge
  for a circular pattern's axis); ⇄ next to a spacing or angle flips it. Errors show in the dialog right away.
- **Timeline** (bottom): double-click a step to edit it; right-click to rename (every use in the
  script follows), roll back to it (new steps go there), show it in the script, or delete it.
  Deleting something other steps depend on offers **Remove instead**, which keeps everything built
  from it.
- **Opacity.** Right-click a body (in the list or the view) and drag the Opacity slider to see
  through it, handy for checking a shell's inner walls. It is remembered per part on this computer
  and never changes the script.
- **Bodies** (left): right-click for Make reference (ghost), Remove from here on, Delete. Removed
  bodies are grouped at the bottom.
- **Parameters** (ƒx Parameters in the top bar, ⌃⇧V, or Edit… beside the list on the left): a table of
  every parameter with its expression, current value, comment and the steps that use it. Edit any cell and
  press Enter; rename one and every `P.old` in the script follows; click a step under *Used by* to edit it;
  delete is offered only for unused ones. Everything is written into the script's `params({...})` block,
  one undoable edit at a time. A bare number means millimetres. Typing a parameter's name in any tool field
  (`plate_w`) writes `P.plate_w`.
- **Script** (`Cmd+E`): the file itself. Save with `Cmd+S`. Edits you make in any other editor are
  picked up too. If the script has a syntax error, the app keeps showing the last version that
  worked until you fix it.
- **Undo / Redo** (`Cmd+Z`, `Shift+Cmd+Z`) step through every change to the file, including saves
  from other editors.
- **Measure** (⌃I): click two points; the distance can become a parameter.
- **Export…** (⌃⇧E) opens a panel listing every body with its size, volume and triangle count. Tick bodies there or
  click them in the view; pick 3MF, STL or STEP, one file or one per body (zipped), and Draft / Standard / Fine
  mesh quality. Solid bodies are ticked by default; reference bodies only export if you tick them; a body left out
  of date by a failed step can't be exported until that step is fixed. A single STL merges the bodies into one
  mesh (the panel warns); 3MF keeps them as separate objects.
- **Right-click a body** (in the list or the view) for Export as STL / 3MF / STEP, or Export… for the panel.
  With several bodies selected, these export all of them.

The app only ever edits the characters it has to. Comments, blank lines and formatting in the
rest of your script survive byte for byte. If a script uses loops or functions at the top level,
the app opens it in **code-only mode**: it builds and displays normally and new steps can still
be added, but existing steps are edited in the script.

### Without the app

```bash
openfusion watch part.ofs                              # read-only live viewer, rebuilds on save
openfusion build part.ofs -o part.3mf -o part.step     # report + exports
openfusion build part.ofs --set wall=2.4 --set "len=3*cm" --quiet
openfusion build part.ofs -o part.stl --body lid --body base --each   # part-lid.stl, part-base.stl
openfusion view  part.ofs -o part.html                 # single offline HTML file
```

`build` exits with status 1 and exports nothing if any feature fails, unless you name the bodies with `--body`
(then only those must be up to date). `--each` writes one file per body.

## A script

```python
from openfusion import *

params({
    "plate_l": 80 * mm,   # plate length
    "plate_w": 40 * mm,
    "plate_t": 6 * mm,    # thickness
    "post_r":  8 * mm,
    "post_h":  30 * mm,
})

plate = box(length=P.plate_l, width=P.plate_w, height=P.plate_t, origin="center_xy")
post  = cylinder(r=P.post_r, h=P.post_h)
place(post.anchor("-Z"), onto=plate.anchor("+Z"))      # stand the post on the top face
join(plate, tools=[post])                               # plate absorbs the post
fillet(plate, edges=[edge_between(post.face("side"), plate.face("+Z"))], r=2)
```

`params({...})` is the parameter table the app reads and edits: one entry per line, with an
optional `# comment` that the app shows and edits too. (The keyword form `params(plate_l=80 * mm, ...)`
also works; the app rewrites it as a dict the first time it renames or comments a parameter.)
`openfusion build part.ofs --set plate_l=100` overrides any entry for one build.

Variable names become feature names in the timeline. In the app, a step that makes a new body is
named `body1`, `body2`... (a box rarely stays a box for long); other steps are named after their
operation (`fillet1`, `pattern1`, `profile1`). Rename any of them from the timeline. Running a script records features;
the engine then evaluates them in order and caches each one, so a change near the end of
the timeline only recomputes the end. More in `examples/`:

| File | Shows |
| --- | --- |
| `plate_post.ofs` | Placement by anchor, hole pattern as tools, fillets on generated edges |
| `enclosure.ofs` | A printable project box and lid; bosses placed from a consumed body's anchors |
| `showcase.ofs` | Every primitive, mirror naming, a reference body |

## Naming rules

Every primitive is built in its own right-handed frame. Local Z is the build direction;
angle 0 is local +X; angles grow counterclockwise about +Z.

- **Faces** aligned with the axes are named by outward normal: `+X -X +Y -Y +Z -Z`.
- **Edges** are named by the two faces they join, X before Y before Z: `+X+Z`, `-X+Y`.
  Cylinder rims are `rim+Z`, `rim-Z`. Any edge can be named `faceA|faceB`.
- **Vertices** of a box: three signs, `+-+`.
- **Pieces.** When a step splits a face (a slot across the top), the pieces are `+Z#0`, `+Z#1`...
  in order along the body's X, then Y, then Z, and `+Z` still means all of them. A piece keeps
  its number through later steps: cut the left piece in two and the right one is still `+Z#1`
  (the new piece takes the next free number). Faces made by a step are named after it
  (`lug.side(top)`, `h1.wall`, `f1(+X+Z)`), so adding or changing an earlier step doesn't
  rename them; a hole with several positions numbers each hole's faces by its place in the list.
- **Anchor frames on faces**: Z points out of the face. On top and bottom faces X = +X;
  on side faces X points right as seen from outside and Y points up.

| Primitive | Faces | Anchors (besides `origin`) |
| --- | --- | --- |
| `box(length, width, height, origin="corner"/"center"/"center_xy")` | `+X` ... `-Z` | `center`, each face, each edge (`+X+Z`), each vertex (`+-+`) |
| `cylinder(r, h)`, `cone(r1, r2, h)` (negative h builds the other way) | `+Z`, `-Z`, `side` | `axis`, `center`, `+Z`, `-Z`, `side(angle=, z=)`, `rim+Z(angle=)`, `rim-Z(angle=)` |
| `sphere(r)` | `surface` | `center`, `+Z`, `-Z`, `surface(lon=, lat=)` |
| `torus(R, r)` | `surface` | `center`, `tube(theta=)`, `surface(theta=, phi=)` |
| `polygon(n, h, r=)` or `(n, h, a=)` | `+Z`, `-Z`, `s0`...`s{n-1}` | `v0`... (bottom ring), `v0+Z`... (top), each side `s{i}` |
| `coil(R, turns, pitch, size, section="circle"/"square", hand="right")` | `start`, `end`, `surface` (or `outer inner top bottom`) | `axis`, `start`, `end`, `path(t=)` |

`r=` on a polygon is the circumradius and puts `v0` at angle 0; `a=` is the apothem and puts
the middle of side `s0` at angle 0. All primitives take `at=` (an anchor to build on),
`spin=` (rotate angle 0) and `role="reference"`. A negative height (`height` for a box, `h` for
cylinder, cone and polygon, `pitch` for a coil) turns the primitive's frame over, so it grows the
other way; a corner-based box keeps its footprint.

**After operations**, faces keep their names. Faces that came from a tool body keep the
tool's name (`plate.face("post.side")`); fillet and chamfer faces are named after the edge
(`fillet1(+X+Z)`); a face split into pieces becomes `+Z#0`, `+Z#1`, ordered along X, then Y.
`plate.face("+Z")` still returns every piece.

**Points on a face**: `face.hole(i)` and `face.boss(i)` (centers of round holes and round
features standing on the face) always exist. `face.corner(i)`, `face.mid(i)` and `face.arc(i)`
exist when the face is simple: flat, at most 12 edges, all lines or arcs. Corners run
counterclockwise from the one nearest angle 0.

**Design anchors vs topology anchors.** `plate.anchor("+Z")` is computed from the box's
parameters, so cuts and fillets never move it. `plate.face("+Z").anchor()` follows the real
face. Use design anchors for placement.

**Fallbacks.** If a face point or face anchor no longer exists after a parameter change, the
reference falls back to the design anchor of the same name, then the body origin. The feature
turns yellow and the report says exactly what fell back. Add `.strict()` to a reference to make
that an error instead.

**Mirror** makes a physically mirrored copy named as if it had been built from scratch:
`+X`/`-X` and `+Z`/`-Z` match the original's reflected faces, the mirror's `+Y` is the
reflection of the original's `-Y`, and angles still run counterclockwise in its own frame.

## Operations

| Call | What it does |
| --- | --- |
| `place(src_anchor, onto=anchor, mode="mate"/"flush"/"axis", gap=, angle=, offset=(x, y))` | Moves the source's body so the anchors meet. `mate`: faces touch. `flush`: same direction. |
| `place_on_bed(body, face="-Z")` | Print orientation: that face down at the origin |
| `move(body, dx=, dy=, dz=, rx=, ry=, rz=, pivot=anchor)` | Turns (degrees, about X then Y then Z through the pivot), then slides, along the body's own axes or `frame="world"` |
| `move(body, point=anchor, to=anchor or (x, y, z))` | Point to point: slides the body so `point` lands on `to` |
| `rotate(body, angle, about=anchor)` | About the anchor's Z. Or `axis="X"/"Y"/"Z"`, `frame="world"` |
| `copy(body)` | Independent copy with the same names |
| `pattern_rect(body, x=(n, spacing), y=..., z=..., along=, y_along=, z_along=, frame="local")` | Copies along the body's axes (default), the world's (`frame="world"`), an anchor's (`along=anchor`), or edges of another body: `along=block.edge("-Y+Z")` runs direction 1 along that edge and direction 2 at right angles on the face it borders (right-hand rule); `y_along=edge` sets direction 2 from a second edge. An edge points the positive way of its body's axis it follows; negative spacing flips any direction. Returns a group: `g[0]` is the seed |
| `pattern_circ(body, count, axis=..., angle=360)` | Copies about an axis: an anchor's Z, a straight edge, or a circular edge / round face (its axis). Right-hand rule; a negative angle turns the other way |
| `mirror(body, plane="YZ")` | Mirrored copy across a world plane or an anchor's XY plane |
| `scale(body, factor or (sx, sy, sz), about=anchor)` | Non-uniform scale turns faces into B-splines |
| `join / cut / intersect(target, tools=[...], keep_tools=False)` | Target keeps its identity. Tools are removed (their design anchors stay usable); `keep_tools=True` keeps them, `"reference"` turns them into ghosts |
| `fillet(body, edges=[...], r=)`, `chamfer(body, edges=[...], d=, d2=None)` | Edges as names (`"+X+Z"`) or refs (`body.edge(...)`, `edge_between(a, b)`, `body.edges("+Z", kind="outer")`, `body.edges()` for all). Smooth chains are rounded together; fillets work in any order; an edge an earlier chain already rounded is skipped with a note; a radius too big for the part is an error |
| `extrude(body.face(name), distance, op="join"/"cut"/"new")` | Push or pull a flat face along its normal |
| `split(body, plane="XY")` | Cuts with a world plane or an anchor's XY plane. The body keeps the part behind the plane; the part in front becomes a new body |
| `sketch(at=, points={...}, curves={...}, constraints=[...])` | A constrained sketch on the anchor's (or flat face's) XY plane; see **Sketches** below |
| `profile(points=[(x, y), ...] / rect=(l, w) / r=, at=, closed=True)` | A flat cross-section on the anchor's XY plane: a reference body for revolve, loft and sweep. `closed=False` makes an open path; its `start` / `end` anchors point along the path |
| `revolve(profile, axis="Y", angle=360, op="new", target=None)` | Spin a profile (or a flat face) about its own X/Y axis, an edge, a round face or an anchor's Z. Right-hand rule; negative angle turns the other way |
| `loft(sections=[...], ruled=False, op="new")` | Solid through profiles or flat faces in order (e.g. the top of one body and a circle above it). Smooth sections (circles, slots, rounded rectangles) give one smooth side face, like Fusion, a light cubic surface (a few hundred control points) that later steps handle quickly; corners in a section stay as seams in the side |
| `sweep(profile, path, op="new", twist="follow")` | Move a cross-section along an open profile or edges that join end to end; sharp corners are mitred. Draw the section across the path's start |
| `shell(body, thickness, faces=None, direction="inside")` | Hollow a body; `faces` (flat or curved) are left open. Inner walls are named `<feature>.inner(<face>)`. Works the same at any wall thickness and on any computer: bodies of flat and simple round faces use OpenCASCADE's offset (sharp inner corners, instant); a body with smooth lofts is cut at the flat planes where each loft meets the rest, each loft's inner wall is built as a loft through exact offset curves, the other pieces use the offset, and the pieces' insides are joined across each junction by a tube along it; anything else is hollowed by sweeping a ball over the skin (inside corners come out rounded at the wall thickness). A lofted part takes 5 to 25 s; the dialog counts the seconds. Openings that would cut the walls apart are refused at once with a message; if the kernel itself fails, the message says so instead of blaming the part |
| `draft(body, faces=[...], angle, plane)` | Tilt faces from a pull plane (a flat face or anchor); positive narrows the part going along the plane's normal |
| revolve / loft / sweep `op=` | `"new"` body named after the step, `"join"` or `"cut"` into `target` (default: the body the profile face belongs to) |
| `hole(at, d=, depth=None, size="M3", fit="clearance", head=None, tip=0, allowance=0, body=None)` | Drills down the position's -Z (into the face it is on): one position or a list, anchors, face points or sketch points (the hole goes into the body the sketch is on). `depth=None` goes through. `size` with `fit`: `"clearance"` / `"close"` / `"loose"` (ISO 273), `"tap_drill"`, or `"tapped"` for a modelled ISO thread (`thread_length=`, `hand=`). `head="counterbore"` / `"countersink"` sized for socket cap / countersunk screws (ISO 4762 / 10642) or by `head_d`, `head_depth`, `cs_angle`. `tip=118` gives a drill point. `allowance` widens everything for printing. Faces: `<hole>.wall`, `.bottom` / `.tip`, `.cb_wall`, `.cb_floor`, `.countersink`, `.thread` (pieces `#0`, `#1`... for several positions) |
| `thread(face, size=None, length=None, offset=0, allowance=0, hand="right")` | A modelled ISO metric thread on a shaft's side (external) or a hole's wall (internal), from the free end. Without `size`, the coarse size that fits (a shaft's diameter, a hole's tap drill). Both use the ISO basic profile; `allowance` (0.2 to 0.4 mm for FDM) opens an internal thread and slims an external one so printed parts screw together. Shell a part before threading it: a shell can't follow a thread's turns, and says so |
| `text("label", size=5, at=, bold=False, halign="center", valign="center", font="sans", spacing=1, spin=)` | Lettering as flat faces on the anchor's (or face's) plane: `size` is the height of capital letters; `\n` starts a new line. Then `extrude(t, 0.6)` embosses it into the body under it and `extrude(t, -0.6)` engraves it. The built-in font is Liberation Sans (bundled, SIL Open Font License) so text builds the same everywhere; `font=` also takes a `.ttf`/`.otf` path or an installed font's name. Anchors `center`, `baseline` |
| `plane(at, offset=, angle=, about="X"/"Y"/edge)`, `plane(mid=(a, b))`, `plane(points=(p1, p2, p3))` | A construction plane (a ghost): offset from a face or anchor, tilted about its X/Y or turned about a straight edge, halfway between two parallel faces, or through three points. Its `origin` anchor is the plane, so it goes anywhere an anchor does: `sketch(at=p)`, `split(b, plane=p)`, `mirror(b, plane=p)`, `cylinder(at=p)` |
| `axis(edge or round face or anchor)`, `axis(points=(p1, p2))` | A construction axis (a ghost) whose `origin` anchor's Z runs along it: `revolve(s, axis=a)`, `pattern_circ(b, 6, axis=a)`, `rotate(b, 30, about=a)`. Anchors `start`, `end` |
| `set_anchor(body, "name", at=ref)` | Define or redefine a named anchor from that step on |
| `set_role(body, "solid"/"reference"/"removed")`, `remove(body)` | Reference bodies are snappable ghosts, never in booleans or exports unless named as tools |

Any anchor reference accepts `.offset(x, y, z)` (in its own frame), `.spin(angle)` and
`.flipped()`. Face anchors point out of the face, so use `.flipped()` to grow a cutter into
it. `world.bed`, `world.anchor("XY"/"XZ"/"YZ")` and `world.point(x, y, z)` reference the world.

## Sketches

```python
s = sketch(at=plate.face("+Z"),
    points={"a": (0, 0), "b": (40, 0), "c": (40, 20), "d": (0, 20), "o": (20, 10)},
    curves={"bottom": ("line", "a", "b"), "right": ("line", "b", "c"),
            "top": ("line", "c", "d"), "left": ("line", "d", "a"),
            "hole": ("circle", "o", 4),
            "guide": ("line", "a", "c", "construction")},
    constraints=[("horizontal", "bottom"), ("horizontal", "top"), ("vertical", "left"),
                 ("vertical", "right"), ("length", "bottom", P.l), ("midpoint", "o", "guide"),
                 ("diameter", "hole", 8)])
lug = extrude(s, 5)                    # the outline with its hole; or s.face("hole") etc.
```

- **Points** are `(x, y)` on the plane (a starting guess the solver moves) or an anchor (fixed there).
- **Curves**: `("line", p, q)`, `("circle", centre, r)`, `("arc", centre, start, end)` (counterclockwise),
  `("spline", p1, p2, ..., ["closed"])`, `("project", body.edge(...))` (a fixed copy of an edge, its
  ends usable as `name.start` / `name.end`). Add `"construction"` at the end for a guide. Any curve's
  ends and centre are points too: `"bottom.start"`, `"hole.center"`.
- **Constraints**: `coincident`, `on` (point on curve), `midpoint`, `horizontal`, `vertical`
  (a line or two points), `parallel`, `perpendicular`, `collinear`, `tangent`, `equal`, `concentric`,
  `symmetric` (two points about a line), `fix`. **Dimensions**: `length`, `distance` (point-point,
  point-line, line-line), `hdistance`, `vdistance`, `radius`, `diameter`, `angle`, `x`, `y`; values
  may be expressions (`P.width / 2`).
- The solver (Gauss-Newton, least change from the drawn positions) reports the degrees of freedom
  left; constraints that contradict each other are an error naming them.
- **Regions**: the curves split the plane into regions. The sketch's `profile` face is every region
  inside an odd number of loops (an outline with its holes). Each region is also named by the curves
  around it, sorted and joined with `+` (`s.face("hole")`, `s.face("bottom+hole+left+right+top")`);
  regions with the same curves get `#1`, `#2`... largest first. Extruding a sketch drawn on a body's face
  joins into (or with `op="cut"` cuts) that body, and the side faces are named `side(<curve>)`.

## Tolerances

Booleans treat surfaces closer than 0.0002 mm as touching, like Fusion's tolerant modelling. Two
roundings that meet exactly (a 1 mm wall rounded r=0.5 on both edges) are built a hair smaller and
the resulting 0.00001 mm sliver face is removed, so later fillets, shells and lofts on that body
see a clean full round.

Volumes are exact for flat and analytic faces, computed adaptively for B-spline faces (lofts,
scaled bodies), and taken from a fine mesh for the inner walls of shells, so the export panel's
numbers match what a slicer reports. Exported meshes close any gap a face's triangulation leaves.

## Errors

- A feature that fails is marked with an error and a message (for example a fillet radius too
  large for its edge). Features that depend on it are skipped, not built from wrong inputs;
  everything else still builds.
- A cut whose tools miss the body is flagged, with a hint about `.flipped()`.
- A Python error in the script reports its line number.


## What is not built yet

Sketch fillets/trims, offsets and patterns inside a sketch, inch (UNC/UNF) threads, rib, web,
components and STEP import. In the app, a new
step is always inserted as a whole statement; reordering steps by drag and drop is not there yet
(right-click > Roll back to here, then add, does the same job).

## Layout

```
openfusion/
  geom.py        right-handed frames and transforms
  topo.py        OpenCASCADE helpers
  body.py        bodies, face names, name propagation, mirror/scale naming
  primitives.py  primitives and their naming rules
  refs.py        lazy references, face points, the fallback chain
  document.py    features, evaluator, cache, report
  ops.py         operations
  features.py    profiles, revolve, loft, sweep, shell, draft
  sketch.py      constrained sketches: model, solver, regions, projected edges
  holes.py       holes (clearance, counterbore, countersink, tapped) and modelled ISO threads
  text.py        lettering profiles (fonts/: Liberation Sans, SIL Open Font License)
  construct.py   construction planes and axes
  loftshell.py   shell for bodies with smooth lofts: inner walls as lofts, chunks joined by tubes
  sweepshell.py  shell by sweeping a ball over the skin, for everything else the offset can't do
  export.py      STEP, STL, 3MF, viewer scene
  scriptedit.py  surgical script edits: the GUI <-> script round trip
  viewer/        read-only live and static viewer (three.js r128 vendored, MIT)
  gui/server.py  the modeling app's server: sessions, undo, preview, file watching
  gui/sketchsrc.py  reads a sketch statement back apart for the app's sketch mode
  gui/workspace.py  documents for the desktop app: start screen, open/new/recent, examples
  app.py         the desktop app: native window (pywebview), dialogs, File menu, Finder opens
  gui/static/    the app's built frontend (three.js, CodeMirror; licenses in app.js.LEGAL.txt)
  cli.py         openfusion command
gui-src/         frontend source; `npm install && npm run build` rebuilds gui/static/app.js
examples/        sample parts
packaging/mac/   build_app.sh (the shareable .app/.dmg), icons, friends' read-me
tests/           every operation, reference, route and command; randomized sweeps; the GUI in a browser
tools/           function_coverage.py: lists any function no test runs
```

## Tests

```bash
python -m pytest                                   # everyday run: about a minute, 20 s limit per test
python -m pytest --slow                            # also the long shell checks (lofted parts): ~6 min
python -m pytest --slow --cov=openfusion --cov-report= && python tools/function_coverage.py
cd gui-src && npm test                             # front-end helpers (Node 18+, no install needed)
```

Every test has a time limit (20 s; `--max-seconds 60` for another, 180 s for the slow ones), and a
test past it fails saying so. Tests marked `slow` (shells of lofted parts, the browser test of every
tool) only run with `--slow`. `tools/function_coverage.py` lists every function in the package that
no test runs; measure it with `--slow`. `tests/test_completeness.py` fails when a new public function, operation, reference
method, server route or command has no test, and checks that every field of every toolbar tool is a
real parameter of its operation. `tests/test_gui_browser.py` drives every toolbar tool and the
Parameters table in Chromium; it is skipped unless Playwright is installed
(`pip install playwright pytest-cov && python -m playwright install chromium`).
