"""Holes and threads: simple, counterbored and countersunk holes, clearance and tapped sizes
for ISO metric screws, and modelled ISO metric threads (inside a hole or on a shaft).

Threads are real geometry (a helical groove), so they print. Both sides use the ISO basic
profile (60 degree flanks, flat crests and roots); `allowance` opens internal threads and
closes external ones by that much on the diameter, which is what printed threads need
(0.2 to 0.4 mm is typical for FDM)."""
from __future__ import annotations

import math
import re

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeWire
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d

from . import topo
from .body import Body, Design, FaceName, propagate_names, unify_faces
from .geom import Frame, any_perpendicular, unit

# ISO 261 coarse pitches, mm
COARSE = {1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 3.5: 0.6, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5,
          12: 1.75, 14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 33: 3.5, 36: 4.0}
# ISO 273 clearance holes: fine / medium (normal) / coarse
CLEARANCE = {1.6: (1.7, 1.8, 2.0), 2: (2.2, 2.4, 2.6), 2.5: (2.7, 2.9, 3.1), 3: (3.2, 3.4, 3.6),
             3.5: (3.7, 3.9, 4.2), 4: (4.3, 4.5, 4.8), 5: (5.3, 5.5, 5.8), 6: (6.4, 6.6, 7.0),
             8: (8.4, 9.0, 10.0), 10: (10.5, 11.0, 12.0), 12: (13.0, 13.5, 14.5), 14: (15.0, 15.5, 16.5),
             16: (17.0, 17.5, 18.5), 18: (19.0, 20.0, 21.0), 20: (21.0, 22.0, 24.0), 22: (23.0, 24.0, 26.0),
             24: (25.0, 26.0, 28.0), 27: (28.0, 30.0, 32.0), 30: (31.0, 33.0, 35.0), 33: (34.0, 36.0, 38.0),
             36: (37.0, 39.0, 42.0)}
# socket head cap screws (ISO 4762): counterbore diameter and depth
COUNTERBORE = {1.6: (3.5, 1.8), 2: (4.4, 2.3), 2.5: (5.5, 2.8), 3: (6.5, 3.4), 4: (8.0, 4.4), 5: (10.0, 5.4),
               6: (11.0, 6.4), 8: (15.0, 8.6), 10: (18.0, 10.6), 12: (20.0, 12.6), 14: (24.0, 14.6),
               16: (26.0, 16.6), 20: (33.0, 20.6), 24: (40.0, 24.8), 30: (48.0, 31.0), 36: (57.0, 37.0)}
# countersunk screws (ISO 10642, 90 degrees): head diameter
COUNTERSINK = {2: 4.4, 2.5: 5.5, 3: 6.72, 4: 8.96, 5: 11.2, 6: 13.44, 8: 17.92, 10: 22.4, 12: 26.88,
               14: 30.8, 16: 33.6, 20: 40.32}
FITS = ("close", "clearance", "loose", "tapped", "tap_drill")
START_ANGLE = 0.37                       # radians: where a groove starts (see thread_groove)
H_BASIC = 5 * math.sqrt(3) / 16          # basic thread depth per unit pitch (5H/8, H = 0.866 P)


def parse_size(size):
    """'M6' -> (6.0, 1.0) coarse; 'M8x1' -> (8.0, 1.0) fine."""
    m = re.fullmatch(r"\s*[Mm]\s*(\d+(?:\.\d+)?)\s*(?:[xX×]\s*(\d+(?:\.\d+)?))?\s*", str(size))
    if not m:
        raise ValueError(f"size {size!r}: write it like 'M6' or 'M8x1'")
    d = float(m.group(1))
    key = int(d) if d == int(d) else d
    if m.group(2):
        p = float(m.group(2))
    elif key in COARSE:
        p = COARSE[key]
    else:
        raise ValueError(f"{size}: not an ISO coarse size ({', '.join('M%g' % k for k in COARSE)}); "
                         f"give the pitch, e.g. 'M{d:g}x1'")
    if not 0 < p < d / 2:
        raise ValueError(f"{size}: pitch {p:g} doesn't fit a {d:g} mm thread")
    return d, p


def minor_internal(d, p):
    """Internal minor diameter D1 (the tap drill size, basic profile)."""
    return d - 2 * H_BASIC * p


def _table(tab, d, what, size):
    key = int(d) if d == int(d) else d
    if key not in tab:
        raise ValueError(f"{size}: no standard {what} size; give it yourself")
    return tab[key]


# ===================================================================== hole solids
def _revolve_profile(pts, labels, fid):
    """Revolve a closed (r, z) outline about Z; each segment's face is named by `labels`
    (None: a cap that the cut removes anyway). Returns (solid, names)."""
    poly = BRepBuilderAPI_MakePolygon()
    for r, z in pts:
        poly.Add(gp_Pnt(float(r), 0.0, float(z)))
    poly.Close()
    wire = poly.Wire()
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    mk = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 2 * math.pi, True)
    mk.Build()
    solid = mk.Shape()
    names = []
    edges = topo.ordered_wire_edges(topo.outer_wire(face), face)
    for e in edges:
        a, b = topo.edge_endpoints(e)
        seg = _segment_index(pts, a, b)
        lbl = labels[seg] if seg is not None else None
        if lbl is None:
            continue
        for g in mk.Generated(e):
            for f in topo.faces(solid):
                if f.IsSame(g):
                    names.append((f, FaceName(fid, lbl)))
    return solid, names


def _segment_index(pts, a, b):
    P = [np.array([r, 0.0, z]) for r, z in pts]
    n = len(P)
    for i in range(n):
        p, q = P[i], P[(i + 1) % n]
        if (np.linalg.norm(p - a) < 1e-7 and np.linalg.norm(q - b) < 1e-7) or \
                (np.linalg.norm(p - b) < 1e-7 and np.linalg.norm(q - a) < 1e-7):
            return i
    return None


def hole_outline(d, depth, head=None, head_d=None, head_depth=None, cs_angle=90.0, tip=0.0, above=0.5):
    """The (r, z) outline of a hole drilled down -Z from z=0, and a label per segment.
    The outline starts on the axis above the surface and runs down the outside."""
    r = d / 2
    pts, lab = [(0.0, above)], []
    if head == "counterbore":
        R = head_d / 2
        pts += [(R, above), (R, -head_depth), (r, -head_depth)]
        lab += [None, "cb_wall", "cb_floor"]
    elif head == "countersink":
        R = head_d / 2
        drop = (R - r) / math.tan(math.radians(cs_angle) / 2)
        pts += [(R, above), (R, 0.0), (r, -drop)]
        lab += [None, None, "countersink"]
    else:
        pts += [(r, above)]
        lab += [None]
    pts.append((r, -depth))
    lab.append("wall")
    if tip:
        pts.append((0.0, -depth - r / math.tan(math.radians(tip) / 2)))
        lab += ["tip", None]
    else:
        pts.append((0.0, -depth))
        lab += ["bottom", None]
    return pts, lab


# ===================================================================== thread grooves
def thread_groove(d, p, length, internal, hand="right", run_in=None):
    """A helical groove (a solid to cut away) for an ISO metric thread of major diameter d
    and pitch p, in a frame whose Z runs into the threaded part from its open end at z=0.
    The groove starts a pitch before z=0 (outside the part) so the thread runs out cleanly,
    and ends at z=length."""
    if hand not in ("right", "left"):
        raise ValueError("hand must be 'right' or 'left'")
    h = H_BASIC * p
    t30 = math.tan(math.radians(30))
    R = d / 2
    if internal:
        # the space between the ridges of a nut: wide at the minor diameter, narrow at the major
        r_in, r_out = R - h - 0.05 * p, R
        w_in, w_out = 0.75 * p + 2 * 0.05 * p * t30, p / 8
    else:
        # the space between the ridges of a bolt: narrow at the root, wide past the crest
        r_in, r_out = R - h, R + 0.05 * p
        w_in, w_out = p / 4, 0.875 * p + 2 * 0.05 * p * t30
    run_in = p if run_in is None else run_in
    z0 = -run_in
    turns = (length + run_in) / p
    if abs(turns - round(turns)) < 0.1:
        # a whole number of turns puts the groove's two end faces in the same plane, which
        # the kernel's booleans get wrong (the cut silently removes nothing): stop a bit short
        turns = round(turns) - 0.12
    s = 1 if hand == "right" else -1
    surf = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), float(r_in))
    # start off the X axis: a cylinder's seam lies there, and the groove's end face lying on
    # a seam makes the kernel's cut silently remove nothing
    a0 = START_ANGLE
    line = Geom2d_Line(gp_Pnt2d(a0, z0), gp_Dir2d(s * 2 * math.pi, p))
    edge = BRepBuilderAPI_MakeEdge(line, surf, 0.0, turns * math.hypot(2 * math.pi, p)).Edge()
    BRepLib.BuildCurves3d_s(edge)
    spine = BRepBuilderAPI_MakeWire(edge).Wire()
    poly = BRepBuilderAPI_MakePolygon()
    ca, sa = math.cos(a0), math.sin(a0)
    for r, w in ((r_in, w_in), (r_out, w_out)):
        poly.Add(gp_Pnt(r * ca, r * sa, z0 - w / 2))
    for r, w in ((r_out, w_out), (r_in, w_in)):
        poly.Add(gp_Pnt(r * ca, r * sa, z0 + w / 2))
    poly.Close()
    pipe = BRepOffsetAPI_MakePipeShell(spine)
    pipe.SetMode(gp_Dir(0, 0, 1))         # binormal along the axis: the profile stays in axial planes
    pipe.Add(poly.Wire())
    pipe.Build()
    if not pipe.IsDone() or not pipe.MakeSolid():
        raise RuntimeError("the thread's helical groove could not be built")
    shape = pipe.Shape()
    sols = topo.solids(shape)
    return sols[0] if sols else shape


# ===================================================================== the features
def _target(ctx, refs, body, what):
    from .refs import Handle
    if body is not None:
        return ctx.body(body)
    owners = set()
    for r in refs:
        o = r.owner() if hasattr(r, "owner") else None
        if isinstance(o, Handle):
            b = ctx.body(o, allow_removed=True)
            own = dict(b.params).get("owner") if b.kind == "profile" else b.id
            if own:
                owners.add(own)
    if len(owners) == 1:
        return ctx.body(owners.pop())
    if not owners:
        raise ValueError(f"{what}: say which body with body=... (the position isn't on a body)")
    raise ValueError(f"{what}: the positions are on different bodies ({', '.join(sorted(owners))}); pick one with body=")


def _cut_once(ctx, b, tools):
    """One boolean cut of `tools` from body b: (the evolved body, not stored yet; solids)."""
    from .ops import _boolean, _single_solid
    algo = _boolean("cut", b, tools, ctx.fid)
    shape, nsol = _single_solid(algo.Shape())
    if nsol == 0:
        return None, 0
    inputs = list(b.face_names) + [fn for t in tools for fn in t.face_names]
    names = propagate_names(algo, inputs, shape, b.frame, b.id)
    if nsol == 1:
        shape, names = unify_faces(shape, names, b.frame, b.id)
    return b.evolve(shape=shape, face_names=names, touched_by=ctx.fid), nsol


def _cut_stages(ctx, b, stages, what):
    """Cut stages of tools in turn: [(tools, the face name each must leave behind), ...].
    Booleans with helical thread grooves are fragile: the kernel sometimes drops a cut without
    an error. So the result is checked (a valid solid, every stage's faces present) and, if the
    check fails, the other orders are tried: reversed, then all at once."""
    v0 = topo.volume(b.shape)
    orders = [stages]
    if len(stages) > 1:
        orders += [list(reversed(stages)), [([t for ts, _ in stages for t in ts], None)]]
    need = {n for _, n in stages}
    tried = None
    for order in orders:
        cur, nsol = b, 1
        for tools, _ in order:
            cur, nsol = _cut_once(ctx, cur, tools)
            if cur is None:
                raise RuntimeError(f"{what} removed the whole body")
        have = {n.name for _, n in cur.face_names if n.origin == ctx.fid}
        if topo.is_valid(cur.shape) and need <= have:
            break
        tried = tried or (cur, nsol)
        if abs(topo.volume(cur.shape) - v0) < 1e-9 * max(1.0, v0):
            break                                  # nothing at all was removed: the tools miss
    else:
        raise RuntimeError(f"the geometry kernel couldn't cut this {what} cleanly (it dropped part of "
                           "the cut); a slightly different size, length or position usually works")
    if nsol > 1:
        ctx.warn(f"result has {nsol} separate solids")
    if abs(topo.volume(cur.shape) - v0) < 1e-9 * max(1.0, v0):
        ctx.warn(f"{what} misses the body: nothing was removed (holes go down the anchor's -Z, "
                 "into a face; use .flipped() if the anchor points into the part)")
    ctx.put(cur)


def _through_depth(b, frames):
    """How deep a through hole has to go: to where the deepest of its axes leaves the body
    (plus a margin), so the tool (and any thread) is no longer than it needs to be."""
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Lin
    lo, hi = topo.bbox(b.shape)
    diag = float(np.linalg.norm(hi - lo))
    deepest = 0.0
    for fr in frames:
        o, z = np.asarray(fr.origin, float), -np.asarray(fr.z, float)
        inter = BRepIntCurveSurface_Inter()
        inter.Init(b.shape, gp_Lin(gp_Pnt(*o), gp_Dir(*z)), 1e-7)
        far = None
        while inter.More():
            t = inter.W()
            if t > 1e-6:
                far = t if far is None else max(far, t)
            inter.Next()
        deepest = max(deepest, far if far is not None else diag)
    return deepest, max(0.5, 0.01 * diag)


def _hole(ctx, at, d=None, depth=None, size=None, fit="clearance", head=None, head_d=None, head_depth=None,
               cs_angle=90.0, tip=0.0, thread_length=None, allowance=0.0, hand="right", body=None):
    """Drill holes into a body at one or more positions (anchors, face points, sketch points),
    down each position's -Z (into the face it sits on).

    d: diameter, or size='M3' with fit: 'clearance' (ISO 273 normal), 'close', 'loose', 'tap_drill'
    (the tap's drill, for self-tapping screws) or 'tapped' (a modelled ISO thread).
    depth: None drills through the whole body; otherwise the depth of the full-diameter part.
    head: None, 'counterbore' or 'countersink'; with size= the head fits a socket cap screw
    (ISO 4762) or a countersunk screw (ISO 10642); head_d / head_depth / cs_angle override.
    tip: 0 for a flat bottom, or the drill point angle (118) for blind holes.
    allowance: added to every diameter (printed holes come out small); for a tapped hole it
    opens the thread.
    New faces: '<feature>.wall', '.bottom' or '.tip', '.cb_wall', '.cb_floor', '.countersink',
    '.thread'; with several positions, each hole's faces are numbered by its place in the list:
    'wall#0' is the first position's wall."""
    refs = list(at) if isinstance(at, (list, tuple)) else [at]
    if not refs:
        raise ValueError("hole: give at least one position")
    if fit not in FITS:
        raise ValueError(f"fit must be one of {', '.join(FITS)}")
    if head not in (None, "counterbore", "countersink"):
        raise ValueError("head must be None, 'counterbore' or 'countersink'")
    if depth is not None and not depth > 0:
        raise ValueError("depth must be positive (or None for through)")
    if not 0 <= tip < 180:
        raise ValueError("tip is the drill point angle, e.g. 118 (0 = flat)")
    if allowance < 0:
        raise ValueError("allowance must be zero or more")
    thread = None
    if size is not None:
        nd, p = parse_size(size)
        if fit == "tapped":
            thread = (nd + allowance, p)
            dd = minor_internal(nd, p)
        elif fit == "tap_drill":
            dd = minor_internal(nd, p)
        else:
            dd = _table(CLEARANCE, nd, "clearance hole", size)[("close", "clearance", "loose").index(fit)]
        if d is not None and abs(d - dd) > 1e-9:
            raise ValueError(f"hole: give d or size, not both (size {size} {fit} means d={dd:g})")
        d = dd
        if head == "counterbore":
            cbd, cbh = _table(COUNTERBORE, nd, "counterbore", size)
            head_d = cbd if head_d is None else head_d
            head_depth = cbh if head_depth is None else head_depth
        elif head == "countersink":
            head_d = _table(COUNTERSINK, nd, "countersink", size) if head_d is None else head_d
    elif fit == "tapped":
        raise ValueError("a tapped hole needs size='M...'")
    if d is None or not d > 0:
        raise ValueError("hole: give a diameter d (or size='M3')")
    d = d + allowance
    if head is not None:
        if head_d is None:
            raise ValueError(f"{head}: give head_d (or size='M...')")
        head_d = head_d + allowance
        if head_d <= d:
            raise ValueError(f"{head} diameter {head_d:g} must be bigger than the hole ({d:g})")
        if head == "counterbore" and not (head_depth and head_depth > 0):
            raise ValueError("counterbore: give head_depth")
        if head == "countersink" and not 0 < cs_angle < 180:
            raise ValueError("cs_angle must be between 0 and 180")
    b = _target(ctx, refs, body, "hole")
    frames = [ctx.frame(r) for r in refs]
    through = depth is None
    pitch = thread[1] if thread is not None else 0.0
    if through:
        exit_, margin = _through_depth(b, frames)
        dep = exit_ + margin + 2 * pitch          # past the far side (and past a thread's run-out)
    else:
        dep = float(depth)
    if head == "counterbore" and head_depth >= (exit_ if through else dep):
        what = "the part's thickness here" if through else "the hole depth"
        raise ValueError(f"counterbore depth {head_depth:g} must be less than {what} "
                         f"({exit_ if through else dep:g})")
    pts, lab = hole_outline(d, dep, head, head_d, head_depth, cs_angle, 0.0 if through else tip,
                            above=max(0.5, 0.2 * (head_d or d)))
    local, local_names = _revolve_profile(pts, lab, ctx.fid)
    groove = None
    if thread is not None:
        nd, p = thread
        run_in = p
        if head == "counterbore":
            top = head_depth
        elif head == "countersink":
            # the thread starts just below the cone (which then leads the screw in): the
            # kernel can't reliably cut a cone across a helical groove
            top = (head_d - d) / 2 / math.tan(math.radians(cs_angle) / 2) + 0.5 * p
            run_in = 1e-3
        else:
            top = 0.0
        # a blind thread stops half a pitch short of the bottom (the groove is that wide)
        most = (exit_ + p - top) if through else (dep - top - 0.5 * p)
        if most < p:
            raise ValueError(f"the hole is too shallow for an M{nd - allowance:g} thread (less than a turn)")
        tl = most if thread_length is None else float(thread_length)
        if not 0 < tl <= most + 1e-9:
            raise ValueError(f"thread_length must be between 0 and {most:g}")
        groove = thread_groove(nd, p, tl, internal=True, hand=hand, run_in=run_in)
        # the groove's frame runs into the part: Z down, starting at the bottom of any counterbore
        flip = np.diag([1.0, -1.0, -1.0, 1.0])
        flip[2, 3] = -top
        groove = topo.transform_shape(groove, flip)[0]
    tools, grooves = [], []
    for i, fr in enumerate(frames):
        m = fr.matrix()
        shape = topo.transform_shape(local, m)[0]
        names = _moved_names(local, local_names, shape)
        if len(frames) > 1:
            # several positions: each hole's faces carry its place in the list (wall#0 is the
            # first position given), so adding a position never renumbers the others
            names = [(f, FaceName(n.origin, n.name, i)) for f, n in names]
        tools.append(Body(id=f"{ctx.fid}#tool{i}", shape=shape, frame=fr, face_names=tuple(names), design=Design()))
        if groove is not None:
            g = topo.transform_shape(groove, m)[0]
            grooves.append(Body(id=f"{ctx.fid}#thread{i}", shape=g, frame=fr,
                                face_names=tuple((f, FaceName(ctx.fid, "thread")) for f in topo.faces(g)), design=Design()))
    # a thread's groove goes first: the other way round the kernel more often trips where the
    # groove crosses the hole's wall (see _cut_stages for what happens when it does)
    _cut_stages(ctx, b, ([(grooves, "thread")] if grooves else []) + [(tools, "wall")], "hole")
    ctx.info["hole"] = {"d": d, "depth": None if through else dep, "head": head, "head_d": head_d,
                        "thread": None if thread is None else f"M{thread[0] - allowance:g}x{thread[1]:g}"}


def _moved_names(src, names, moved):
    """Face names of a transformed copy: faces keep their order through a transform."""
    by = topo.ShapeDict()
    for f, n in names:
        by[f] = n
    out = []
    for f0, f1 in zip(topo.faces(src), topo.faces(moved)):
        n = by.get(f0)
        if n is not None:
            out.append((f1, n))
    return out


def _cylinder_of(face):
    ax = topo.surface_axis(face)
    if ax is None or topo.surface_kind(face) != "cylinder":
        raise ValueError("thread needs a cylindrical face (a shaft's side or a hole's wall)")
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    r = BRepAdaptor_Surface(face).Cylinder().Radius()
    o, z = np.asarray(ax[0], float), unit(ax[1])
    # the face's extent along the axis
    zs = [float((topo.to_np(v) - o) @ z) for v in _sample_points(face)]
    return o, z, float(r), min(zs), max(zs)


def _sample_points(face):
    pts = []
    for e in topo.edges(face):
        a, b = topo.edge_endpoints(e)
        pts += [gp_Pnt(*a), gp_Pnt(*b), gp_Pnt(*topo.edge_midpoint(e))]
    return pts


def _is_internal(face, o, z):
    p, n = topo.face_point_normal_mid(face)
    radial = (p - o) - ((p - o) @ z) * z
    return float(n @ radial) < 0                 # the normal points at the axis: a hole


def _open_end(b, face, o, z, lo, hi):
    """Which end of the cylinder the thread starts from: the free end (a shaft's tip, a hole's
    mouth), told by the face across its circular edge facing away along the axis. Both free
    (a through hole): the end further along the axis."""
    efmap = topo.edge_face_map(b.shape)
    ends = {}
    for e in topo.edges(face):
        if topo.edge_kind(e) != "circle":
            continue
        c = topo.circle_center_radius(e)[0]
        t = float((np.asarray(c) - o) @ z)
        which, out = ("hi", z) if abs(t - hi) < abs(t - lo) else ("lo", -z)
        for g in efmap.get(e) or []:
            if g.IsSame(face):
                continue
            n = topo.plane_normal(g) if topo.surface_kind(g) == "plane" else topo.face_point_normal_mid(g)[1]
            if float(n @ out) > 0.2:
                ends[which] = True
    if ends.get("hi") or not ends:
        return "hi"
    return "lo"


def nearest_size(diam, internal):
    """The ISO coarse size a cylinder of diameter `diam` would be threaded with: a shaft by its
    major diameter, a hole by its tap drill (within 0.3 mm). None if nothing is close."""
    best, bd = None, 0.3 + 1e-9
    for nd, p in COARSE.items():
        ref = minor_internal(nd, p) if internal else nd
        if abs(diam - ref) < bd:
            best, bd = (float(nd), p), abs(diam - ref)
    return best


def _thread(ctx, face, size=None, length=None, offset=0.0, allowance=0.0, hand="right"):
    """A modelled ISO metric thread on a cylindrical face: on a shaft (external) the groove is
    cut into it, in a hole (internal) the hole's wall gets the thread. size: 'M8' or 'M8x1';
    by default the coarse size that fits the diameter (a shaft's diameter, a hole's tap drill).
    length: from the free end (default: the whole face); offset: start this far in.
    allowance: clearance for printing, added to an internal thread's diameter and taken off an
    external one. New faces: '<feature>.thread'."""
    from .refs import FaceRef
    if not isinstance(face, FaceRef):
        raise TypeError("thread needs a cylindrical face, e.g. post.face('side')")
    if allowance < 0:
        raise ValueError("allowance must be zero or more")
    b, faces = face.resolve(ctx)
    if len(faces) != 1:
        raise ValueError(f"thread needs one cylindrical face ({face!r} is {len(faces)} faces)")
    f = faces[0]
    o, z, r, lo, hi = _cylinder_of(f)
    internal = _is_internal(f, o, z)
    if size is None:
        found = nearest_size(2 * r, internal)
        if found is None:
            what = "tap drill" if internal else "diameter"
            raise ValueError(f"no ISO coarse thread has a {what} near {2 * r:g} mm; give size='M...'")
        nd, p = found
    else:
        nd, p = parse_size(size)
        if internal and 2 * r >= nd:
            raise ValueError(f"a {2 * r:g} mm hole is too big for {size} (its tap drill is {minor_internal(nd, p):.2f} mm)")
        if not internal and 2 * r <= minor_internal(nd, p):
            raise ValueError(f"a {2 * r:g} mm shaft is too thin for {size}")
        if not internal and abs(2 * r - nd) > 0.5 * p:
            ctx.warn(f"the shaft is {2 * r:g} mm, {size} is {nd:g} mm: the crests are cut at the shaft")
    nd_eff = nd + allowance if internal else nd - allowance
    end = _open_end(b, f, o, z, lo, hi)
    full = hi - lo
    if offset < 0 or offset >= full:
        raise ValueError(f"offset must be between 0 and {full:g}")
    tl = full - offset if length is None else float(length)
    if not 0 < tl <= full - offset + 1e-9:
        raise ValueError(f"length must be between 0 and {full - offset:g}")
    # a frame at the free end with Z running along the face
    zz = -z if end == "hi" else z
    start = o + (hi if end == "hi" else lo) * z + offset * zz
    fr = Frame.make(start, any_perpendicular(zz), zz)
    run_in = p if offset == 0 else 0.0
    if offset:
        # starting inside the part: the groove can't run in from outside, so it starts there
        ctx.warn("the thread starts inside the face; its first turn ends abruptly")
    g = thread_groove(nd_eff, p, tl, internal=internal, hand=hand, run_in=run_in if run_in else 1e-3)
    g = topo.transform_shape(g, fr.matrix())[0]
    tool = Body(id=f"{ctx.fid}#thread", shape=g, frame=fr,
                face_names=tuple((x, FaceName(ctx.fid, "thread")) for x in topo.faces(g)), design=Design())
    _cut_stages(ctx, b, [([tool], "thread")], "thread")
    ctx.info["thread"] = {"size": f"M{nd:g}x{p:g}", "internal": internal, "length": tl}


def _make_api():
    from .ops import _record
    return _record("modify")(_hole), _record("modify")(_thread)


hole, thread = _make_api()
