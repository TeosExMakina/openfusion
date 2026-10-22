"""Profile-based features: cross-sections (profile), revolve, loft, sweep; and shell, draft.

A profile is a flat cross-section drawn on any anchor's XY plane. It is a reference
body (a ghost: snappable, never exported or used in booleans), so it can be placed,
moved and patterned like any body, and used by revolve / loft / sweep. Any flat face
of a solid works as a cross-section too.

revolve / loft / sweep make a new body named after the feature (op="new"), or add to
(op="join") or remove from (op="cut") a target body; with a solid's face as the profile,
the target defaults to that face's body.
"""
from __future__ import annotations

import math
import time

import numpy as np
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
                                BRepBuilderAPI_MakeWire)
from OCP.collections import List_TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Vec

from . import topo
from .body import Body, FaceName, NameError_, _iter_list, propagate_names, unify_faces
from .geom import Frame, unit
from .ops import FixedDesign, _boolean, _creation_frame, _is_line_ref, _record, _single_solid, ref_line
from .refs import FaceRef, FrameRef, Handle, RefError


def _P(p):
    return gp_Pnt(*[float(c) for c in p])


def _D(d):
    return gp_Dir(*[float(c) for c in d])


# ===================================================================== profile
class ProfileDesign(FixedDesign):
    kind = "profile"


@_record("create")
def _profile(ctx, points=None, rect=None, r=None, at=None, origin="corner", closed=True, spin=0.0, length=None, width=None):
    """A flat cross-section on the XY plane of `at` (default: the world XY plane), in mm:
      points=[(x, y), ...]   a polygon through these points (closed=False: an open polyline, a path for sweep)
      rect=(length, width)   a rectangle (or length=, width=); origin 'corner' (default) or 'center'
      r=radius               a circle centred on the anchor
    The profile is a reference body: a ghost that revolve, loft and sweep use and exports skip.
    Anchors: 'origin' (the anchor it was drawn on), 'center' (centroid, normal up); open paths
    also have 'start' and 'end', whose Z follows the path, ready to draw a cross-section on."""
    if length is not None or width is not None:
        if rect is not None or length is None or width is None:
            raise ValueError("give rect=(length, width), or both length= and width=")
        rect = (length, width)
    given = [x is not None for x in (points, rect, r)]
    if sum(given) != 1:
        raise ValueError("give exactly one of points=[...], rect=(length, width) or r=radius")
    fr = _creation_frame(ctx, at, spin)
    w = lambda x, y: fr.origin + float(x) * fr.x + float(y) * fr.y
    frames = {}
    if r is not None:
        if not r > 0:
            raise ValueError("radius must be positive")
        circ = gp_Circ(gp_Ax2(_P(fr.origin), _D(fr.z), _D(fr.x)), float(r))
        wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire()
        pts2 = None
    else:
        if rect is not None:
            L, W = (float(v) for v in rect)
            if L <= 0 or W <= 0:
                raise ValueError("rect sizes must be positive")
            x0, y0 = (-L / 2, -W / 2) if origin == "center" else (0.0, 0.0)
            pts2 = [(x0, y0), (x0 + L, y0), (x0 + L, y0 + W), (x0, y0 + W)]
            closed = True
        else:
            pts2 = [(float(p[0]), float(p[1])) for p in points]
            if len(pts2) < (3 if closed else 2):
                raise ValueError("a closed profile needs at least 3 points (an open path 2)")
            if closed and np.allclose(pts2[0], pts2[-1]):
                pts2 = pts2[:-1]
        poly = BRepBuilderAPI_MakePolygon()
        for x, y in pts2:
            poly.Add(_P(w(x, y)))
        if closed:
            poly.Close()
        if not poly.IsDone():
            raise ValueError("the profile points do not make a polyline (repeated points?)")
        wire = poly.Wire()
    if closed:
        mk = BRepBuilderAPI_MakeFace(wire, True)
        if not mk.IsDone():
            raise ValueError("the profile does not make a flat face (does the outline cross itself?)")
        shape = mk.Face()
        _, c = topo.face_area_centroid(shape)
        frames["center"] = fr.frame_to_local(Frame.make(c, fr.x, fr.z))
        names = ((shape, FaceName(ctx.fid, "face")),)
    else:
        shape = wire
        names = ()
        a, b = np.array(w(*pts2[0])), np.array(w(*pts2[1]))
        y, z = np.array(w(*pts2[-2])), np.array(w(*pts2[-1]))
        side = lambda d: fr.z if abs(float(np.dot(unit(d), fr.z))) < 0.99 else fr.x
        frames["start"] = fr.frame_to_local(Frame.make(a, side(b - a), unit(b - a)))
        frames["end"] = fr.frame_to_local(Frame.make(z, side(z - y), unit(z - y)))
        frames["center"] = fr.frame_to_local(Frame.make(np.mean([w(*p) for p in pts2], axis=0), fr.x, fr.z))
    params = (("points", tuple(pts2) if pts2 else None), ("r", r), ("closed", closed))
    ctx.put(Body(id=ctx.fid, shape=shape, frame=fr, face_names=names, design=ProfileDesign(frames),
                 kind="profile", role="reference", params=params, history=(ctx.fid,)))


# ===================================================================== resolving cross-sections and paths
def _section(ctx, ref, what="profile"):
    """(face, frame, owner body or None) for a profile body or a flat face of any body."""
    if isinstance(ref, Handle):
        b = ctx.body(ref, allow_removed=True)
        fs = (b.faces_named("profile") or topo.faces(b.shape)) if b.kind == "profile" else []
        if not fs:
            raise RefError(f"{what}: {b.id} is not a closed profile; pick a profile or a flat face")
        if len(fs) > 1:
            raise RefError(f"{what}: {b.id} has {len(fs)} separate regions; pick one, e.g. "
                           f"{b.id}.face({b.label_of(fs[0])!r})")
        return fs[0], b.frame, None
    if isinstance(ref, FaceRef):
        b, faces = ref.resolve(ctx)
        face = max(faces, key=lambda f: topo.face_area_centroid(f)[0])
        if topo.surface_kind(face) != "plane":
            raise RefError(f"{what}: {ref!r} is not flat")
        from .refs import face_anchor_frame
        fr = face_anchor_frame(b, [face], None)
        return face, fr, (b if b.kind != "profile" else None)
    raise TypeError(f"{what}: use a profile, e.g. profile(rect=(10, 5)), or a flat face such as plate.face('+Z')")


def _chain(edges, near=None, normal=None):
    """Order edges into one connected chain (a sweep path). The chain starts at the end
    nearest `near` (for a closed loop: the corner nearest it), so a cross-section drawn at
    either end of a path, or anywhere on a loop's corner, starts the sweep there."""
    from OCP.TopAbs import TopAbs_FORWARD
    # edges taken from faces carry the face's orientation; walk them in their curve's own direction
    edges = [topo._edge(e.Oriented(TopAbs_FORWARD)) for e in edges]
    if not edges:
        raise ValueError("the path has no edges")
    tol = 1e-5
    ends = {i: topo.edge_endpoints(e) for i, e in enumerate(edges)}
    segs = [(0, False)]                       # (edge index, traversed backwards)
    used = {0}
    head = lambda: ends[segs[0][0]][1 if segs[0][1] else 0]
    tail = lambda: ends[segs[-1][0]][0 if segs[-1][1] else 1]
    while len(segs) < len(edges):
        for i, (p, q) in ends.items():
            if i in used:
                continue
            if np.linalg.norm(p - tail()) < tol:
                segs.append((i, False)); break
            if np.linalg.norm(q - tail()) < tol:
                segs.append((i, True)); break
            if np.linalg.norm(q - head()) < tol:
                segs.insert(0, (i, False)); break
            if np.linalg.norm(p - head()) < tol:
                segs.insert(0, (i, True)); break
        else:
            raise ValueError("the path edges are not connected end to end")
        used.add(segs[-1][0] if segs[-1][0] not in used else segs[0][0])
    closed = np.linalg.norm(head() - tail()) < tol
    if near is not None:
        near = np.asarray(near, float)
        if closed:
            starts = [ends[i][1 if back else 0] for i, back in segs]
            k = int(np.argmin([np.linalg.norm(p - near) for p in starts]))
            segs = segs[k:] + segs[:k]
            if normal is not None:     # go round the loop the way that leaves the start across the section
                rev = [(i, not back) for i, back in reversed(segs)]
                first = lambda sg: (lambda p, q: (q - p) / (np.linalg.norm(q - p) or 1))(
                    *((ends[sg[0][0]][1], ends[sg[0][0]][0]) if sg[0][1] else ends[sg[0][0]]))
                if abs(float(np.dot(first(rev), normal))) > abs(float(np.dot(first(segs), normal))) + 1e-9:
                    segs = rev
        elif np.linalg.norm(tail() - near) < np.linalg.norm(head() - near):
            segs = [(i, not back) for i, back in reversed(segs)]
    mk = BRepBuilderAPI_MakeWire()
    for i, back in segs:
        mk.Add(topo._edge(edges[i].Reversed()) if back else edges[i])
    if not mk.IsDone():
        raise ValueError("could not join the path edges into one wire")
    return mk.Wire()


def _start_tangent(wire):
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    c = BRepAdaptor_CompCurve(wire)
    from OCP.gp import gp_Pnt as _Pt, gp_Vec as _V
    p, v = _Pt(), _V()
    c.D1(c.FirstParameter(), p, v)
    t = np.array([v.X(), v.Y(), v.Z()])
    return t / (np.linalg.norm(t) or 1.0)


def _path(ctx, ref, near=None, normal=None):
    """A wire from a profile (open or closed), or from edge references on solids,
    starting at the end nearest `near`."""
    if isinstance(ref, Handle):
        b = ctx.body(ref, allow_removed=True)
        ws = topo.wires(b.shape)
        if not ws:
            raise RefError(f"path: {b.id} has no edges")
        return _chain(topo.edges(ws[0]), near, normal)
    items = ref if isinstance(ref, (list, tuple)) else [ref]
    edges = []
    for it in items:
        if isinstance(it, Handle):
            edges += topo.edges(topo.wires(ctx.body(it, allow_removed=True).shape)[0])
            continue
        _, es = it.resolve(ctx)
        edges += [e for e in es if not any(e.IsSame(x) for x in edges)]
    return _chain(edges, near, normal)


# ===================================================================== new body or boolean into a target
def _finish(ctx, shape, names, frame, design, kind, op, target, default_target):
    if op not in ("new", "join", "cut"):
        raise ValueError("op must be 'new', 'join' or 'cut'")
    shape, nsol = _single_solid(shape)
    if nsol == 0:
        raise RuntimeError(f"{kind} made no solid (does the profile cross the axis or itself?)")
    if not topo.is_valid(shape) or topo.volume(shape) <= 0:
        raise RuntimeError(f"{kind} made a broken solid (the profile may cross the axis, or the path bends too tightly)")
    if op == "new":
        ctx.put(Body(id=ctx.fid, shape=shape, frame=frame, face_names=tuple(names), design=design, kind=kind,
                     history=(ctx.fid,)))
        return
    t = ctx.body(target) if target is not None else default_target
    if t is None:
        raise ValueError(f"op='{op}' needs target=<body> (the profile does not belong to a solid)")
    tool = Body(id=f"{ctx.fid}#tool", shape=shape, frame=frame, face_names=tuple(names), design=design)
    algo = _boolean(op, t, [tool], ctx.fid)
    res, n = _single_solid(algo.Shape())
    if n == 0:
        raise RuntimeError(f"{kind} {op} left no solid")
    if n > 1:
        ctx.warn(f"result has {n} separate solids")
    nm = propagate_names(algo, list(t.face_names) + list(names), res, t.frame, t.id)
    if n == 1:
        res, nm = unify_faces(res, nm, t.frame, t.id)
    ctx.put(t.evolve(shape=res, face_names=nm, touched_by=ctx.fid))


def _cap_names(mk, shape, fid, edges_from=None):
    """'start' / 'end' for the first and last caps, 'side' pieces for the rest."""
    out, used = [], topo.ShapeSet()
    for getter, nm in (("FirstShape", "start"), ("LastShape", "end")):
        try:
            cap = getattr(mk, getter)()
        except Exception:
            cap = None
        if cap is None or cap.IsNull():
            continue
        for f in topo.faces(cap):
            for g in topo.faces(shape):
                if g.IsSame(f) and g not in used:
                    out.append((g, FaceName(fid, nm)))
                    used.add(g)
    return out, used


def _name_rest(shape, named, used, fid, frame):
    from .body import sort_key_local
    rest = [f for f in topo.faces(shape) if f not in used]
    rest.sort(key=lambda f: sort_key_local(frame, topo.face_area_centroid(f)[1]))
    if len(rest) == 1:
        return named + [(rest[0], FaceName(fid, "side"))]
    return named + [(f, FaceName(fid, "side", i)) for i, f in enumerate(rest)]


# ===================================================================== revolve
@_record("create")
def _revolve(ctx, profile, axis="Y", angle=360.0, op="new", target=None):
    """Spin a cross-section about an axis. profile: a profile or a flat face.
    axis: 'X' or 'Y' of the profile's own plane (through its origin), an anchor (its Z),
    a straight edge, or a circular edge / round face (its axis).
    angle in degrees, right-hand rule about the axis; negative turns the other way.
    Faces: 'side' pieces; a partial turn adds 'start' and 'end'."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    face, pfr, owner = _section(ctx, profile)
    if isinstance(axis, str) and axis.upper() in ("X", "Y"):
        pt, d = pfr.origin, (pfr.x if axis.upper() == "X" else pfr.y)
    elif _is_line_ref(axis):
        pt, d = ref_line(ctx, axis, "axis")
    else:
        f = ctx.frame(axis)
        pt, d = f.origin, f.z
    a = float(angle)
    if a == 0 or abs(a) > 360:
        raise ValueError("angle must be between -360 and 360 (not 0)")
    if a < 0:
        d, a = -np.asarray(d), -a
    n = topo.plane_normal(face)
    if abs(float(np.dot(unit(d), n))) > 1e-6:
        raise ValueError("the axis must lie in the profile's plane (it crosses it); use X or Y of the profile, "
                         "or an edge in that plane")
    _, c = topo.face_area_centroid(face)
    rel = c - pt
    radial = rel - np.dot(rel, d) * np.asarray(d)
    if np.linalg.norm(radial) < 1e-9:
        raise ValueError("the profile's center lies on the axis; move the axis to its edge")
    mk = BRepPrimAPI_MakeRevol(face, gp_Ax1(_P(pt), _D(d)), math.radians(a), True)
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("revolve failed in the geometry kernel")
    shape = mk.Shape()
    sols = topo.solids(shape)
    shape = sols[0] if len(sols) == 1 else shape
    origin = np.asarray(pt) + np.dot(rel, d) * np.asarray(d)
    frame = Frame.make(origin, unit(radial), d)
    named, used = _cap_names(mk, shape, ctx.fid) if a < 360 else ([], topo.ShapeSet())
    names = _name_rest(shape, named, used, ctx.fid, frame)
    design = FixedDesign({"axis": Frame.world()})
    _finish(ctx, shape, names, frame, design, "revolve", op, target, owner)


# ===================================================================== loft
@_record("create")
def _loft(ctx, sections, ruled=False, op="new", target=None):
    """A solid through two or more cross-sections, in order: profiles or flat faces
    (e.g. the top of one body and the bottom of another). ruled=True gives straight
    sides between sections instead of a smooth blend.
    Faces: 'start' (first section), 'end' (last), 'side' pieces."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    secs = sections if isinstance(sections, (list, tuple)) else [sections]
    if len(secs) < 2:
        raise ValueError("loft needs at least two cross-sections")
    resolved = [_section(ctx, s, f"section {i + 1}") for i, s in enumerate(secs)]
    c_first = topo.face_area_centroid(resolved[0][0])[1]
    c_last = topo.face_area_centroid(resolved[-1][0])[1]
    axis = unit(c_last - c_first) if np.linalg.norm(c_last - c_first) > 1e-9 else resolved[0][1].z
    wires = _loft_wires([face for face, _, _ in resolved], axis, resolved[0][1].x)
    mk = BRepOffsetAPI_ThruSections(True, bool(ruled), 1e-6)
    for w in wires:
        mk.AddWire(w)
    # sections made into single smooth curves already start in line with each other; letting
    # the kernel re-align them moves the seam, and a seam the join then has to cut through
    # gives booleans tolerances of tens of millimetres
    mk.CheckCompatibility(not all(len(topo.edges(w)) == 1 for w in wires))
    try:
        mk.Build()
    except Exception as e:
        raise RuntimeError(f"loft failed: {e}")
    if not mk.IsDone():
        raise RuntimeError("loft failed: the sections may be too different or overlap")
    shape = mk.Shape()
    c0 = topo.face_area_centroid(resolved[0][0])[1]
    c1 = topo.face_area_centroid(resolved[-1][0])[1]
    z = c1 - c0
    if np.linalg.norm(z) < 1e-9:
        raise ValueError("the first and last sections are in the same place")
    z = unit(z)
    x = resolved[0][1].x - np.dot(resolved[0][1].x, z) * z
    frame = Frame.make(c0, x if np.linalg.norm(x) > 1e-6 else (1, 0, 0), z)
    named, used = _cap_names(mk, shape, ctx.fid)
    names = _name_rest(shape, named, used, ctx.fid, frame)
    design = FixedDesign({"start": Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1)),
                          "end": frame.frame_to_local(Frame.make(c1, frame.x, z))})
    owner = next((o for _, _, o in resolved if o is not None), None)
    _finish(ctx, shape, names, frame, design, "loft", op, target, owner)


def _g1_closed(edges):
    """True when a closed chain of edges turns smoothly at every vertex (a circle, a slot,
    a rounded rectangle): no corner that a loft would have to keep."""
    n = len(edges)
    for i in range(n):
        t1 = _edge_tangent(edges[i], at_end=True)
        t2 = _edge_tangent(edges[(i + 1) % n], at_end=False)
        if float(t1 @ t2) < math.cos(math.radians(0.5)):
            return False
    return True


def _edge_tangent(e, at_end):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_REVERSED
    c = BRepAdaptor_Curve(e)
    rev = e.Orientation() == TopAbs_REVERSED
    u = c.LastParameter() if (at_end != rev) else c.FirstParameter()
    p, v = gp_Pnt(), gp_Vec()
    c.D1(u, p, v)
    t = np.array([v.X(), v.Y(), v.Z()])
    t = t / np.linalg.norm(t)
    return -t if rev else t


SMOOTH_SAMPLES = 200       # points per smooth section with corners in its outline; clustered at edge ends


class _Loop:
    """A section's closed outline as one arc-length coordinate, running counterclockwise
    about the loft axis."""

    def __init__(self, face, axis):
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.TopAbs import TopAbs_REVERSED
        es = topo.ordered_wire_edges(topo.outer_wire(face), face)
        self.parts = []
        for e in es:
            self.parts.append((BRepAdaptor_Curve(e), topo.edge_length(e), e.Orientation() == TopAbs_REVERSED))
        self.length = sum(le for _, le, _ in self.parts)
        self.flip = False
        pts = np.array([self.at(self.length * k / 64) for k in range(64)])
        c = pts.mean(axis=0)
        area = sum(np.cross(pts[i] - c, pts[(i + 1) % 64] - c) for i in range(64))
        self.flip = float(area @ axis) < 0
        self.centre = c

    def at(self, s):
        from OCP.GCPnts import GCPnts_AbscissaPoint
        s = s % self.length
        if self.flip:
            s = (self.length - s) % self.length
        for ad, le, rev in self.parts:
            if s <= le or (ad, le, rev) == self.parts[-1]:
                d = min(max(s, 0.0), le)
                if rev:
                    d = le - d
                return topo.to_np(ad.Value(GCPnts_AbscissaPoint(ad, d, ad.FirstParameter()).Parameter()))
            s -= le

    def jumps(self):
        """Arc positions (in this loop's own direction) clustered at the ends of its edges,
        where curvature jumps (a line meeting an arc)."""
        out, s0 = [], 0.0
        for _, le, _ in self.parts:
            n = max(24, int(round(SMOOTH_SAMPLES * le / self.length)))     # short arcs need samples too
            out += [s0 + (1 - math.cos(math.pi * k / n)) / 2 * le for k in range(n)]
            s0 += le
        if self.flip:
            out = [(self.length - x) % self.length for x in out]
        return out

    def corners(self):
        """Arc positions (in this loop's own direction) of the vertices between its edges."""
        out, s0 = [], 0.0
        for _, le, _ in self.parts:
            out.append(s0)
            s0 += le
        if self.flip:
            out = [(self.length - x) % self.length for x in out]
        return out

    def start(self, ref_dir, axis):
        """Arc position whose direction from the centre is closest to ref_dir."""
        r = ref_dir - np.dot(ref_dir, axis) * axis
        if np.linalg.norm(r) < 1e-9:
            return 0.0
        best, bs = -2.0, 0.0
        for k in range(720):
            s = self.length * k / 720
            v = self.at(s) - self.centre
            v = v - np.dot(v, axis) * axis
            c = float(v @ r) / max(float(np.linalg.norm(v) * np.linalg.norm(r)), 1e-12)
            if c > best:
                best, bs = c, s
        def f(s):
            v = self.at(s) - self.centre
            v = v - np.dot(v, axis) * axis
            return -float(v @ r) / max(float(np.linalg.norm(v) * np.linalg.norm(r)), 1e-12)
        lo, hi = bs - self.length / 720, bs + self.length / 720     # golden-section between the samples
        g = (math.sqrt(5) - 1) / 2
        for _ in range(60):
            a, b = hi - g * (hi - lo), lo + g * (hi - lo)
            if f(a) < f(b):
                hi = b
            else:
                lo = a
        return ((lo + hi) / 2) % self.length


def _smooth_curves(faces, ref_dir, axis):
    """Smooth sections (circles, slots, rounded rectangles) as periodic C2 curves that share
    one parametrisation, or {} if fewer than two sections are smooth with corners to keep.
    OpenCASCADE lofts each edge of a section separately, so a slot lofted to a circle gets
    creases between the pieces that later steps cannot handle; one curve per section gives
    one smooth side, like Fusion's loft. Interpolating every section at the same parameters
    keeps the side surface light (cubic, a few hundred control points); curves made
    separately get merged into something of degree 14 with thousands of points, which slows
    every later boolean on the part. Each starts in ref_dir and runs counterclockwise."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_double, HArray1_gp_Pnt
    loops = {}
    for i, f in enumerate(faces):
        es = topo.ordered_wire_edges(topo.outer_wire(f), f)
        if (len(es) >= 2 and _g1_closed(es)) or (len(es) == 1 and topo.edge_kind(es[0]) != "line"):
            loops[i] = _Loop(f, axis)
    if not any(len(lp.parts) >= 2 for lp in loops.values()):
        return {}                # only round sections: OpenCASCADE lofts those cleanly as they are
    starts = {i: lp.start(ref_dir, axis) for i, lp in loops.items()}
    fr = set()
    for i, lp in loops.items():
        if len(lp.parts) >= 2:
            fr.update(round(((x - starts[i]) % lp.length) / lp.length, 12) for x in lp.jumps())
    fr = sorted(x % 1.0 for x in fr)
    # every section starts at parameter 0 with period 1. Shift all of them by the same fraction
    # (which keeps the side's straight lines where they were) so the start lands on a corner of
    # a section's outline: a loft whose closed edge starts in the middle of another body's edge
    # makes the join split that edge at the curve's wrap-around point, which fuzzy booleans
    # get badly wrong (a tolerance of tens of millimetres).
    i0 = next(i for i, lp in loops.items() if len(lp.parts) >= 2)
    corners = [round(((x - starts[i0]) % loops[i0].length) / loops[i0].length, 12) % 1.0 for x in loops[i0].corners()]
    shift = min(corners, key=lambda x: min(x, 1 - x))
    fr = sorted((x - shift) % 1.0 for x in fr)
    gap = 1e-6                                  # drop near-duplicates (two sections' samples landing together)
    keep = [0.0]
    for x in fr[1:]:
        if x - keep[-1] > gap and 1 - x > gap:
            keep.append(x)
    samples = {i: [lp.at(starts[i] + ((x + shift) % 1.0) * lp.length) for x in keep] for i, lp in loops.items()}
    # parameters by chord length along the section with corners (closing chord included),
    # shared by every section so the side surface stays light
    q = np.array(samples[i0])
    chords = np.linalg.norm(np.diff(np.vstack([q, q[:1]]), axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(chords)]) / chords.sum()
    par = HArray1_double(1, len(keep) + 1)
    for k in range(len(keep) + 1):
        par.SetValue(k + 1, float(cum[k]))
    out = {}
    for i, lp in loops.items():
        pts = HArray1_gp_Pnt(1, len(keep))
        for k, pt in enumerate(samples[i]):
            pts.SetValue(k + 1, gp_Pnt(*pt))
        it = GeomAPI_Interpolate(pts, par, True, 1e-9)
        it.Perform()
        if not it.IsDone():
            return {}
        out[i] = it.Curve()
    return out


def _loft_wires(faces, axis, x_dir):
    """The section wires for a loft: smooth sections become single smooth curves, all starting
    on the same side (that of an exact round section's seam if there is one), so the side
    surface doesn't twist; sections with corners are used as they are."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    ref = np.asarray(x_dir, float)
    for f in faces:              # a single-edge round section has a fixed start: follow it
        w = topo.outer_wire(f)
        es = topo.edges(w)
        if len(es) == 1:
            v = topo.vertices(es[0])
            if v:
                from OCP.BRep import BRep_Tool
                ref = topo.to_np(BRep_Tool.Pnt_s(v[0])) - topo.face_area_centroid(f)[1]
            break
    try:
        curves = _smooth_curves(faces, ref, axis)
    except Exception:
        curves = {}
    out = []
    for i, f in enumerate(faces):
        if i in curves:
            out.append(BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curves[i]).Edge()).Wire())
        else:
            out.append(topo.outer_wire(f))
    return out


# ===================================================================== sweep
@_record("create")
def _sweep(ctx, profile, path, op="new", target=None, twist="follow"):
    """Move a cross-section along a path. profile: a profile or a flat face.
    path: an open or closed profile (a polyline drawn with profile(points=..., closed=False)),
    or edges of solids that join end to end, e.g. [plate.edge("+X+Z"), plate.edge("+Y+Z")].
    The cross-section is swept from where it is: draw it at the start of the path
    (profile(..., at=path.anchor("start")) puts it there, square to the path).
    twist: 'follow' (default, the section turns with the path) or 'frenet' (for helices).
    Faces: 'start', 'end' (open paths), 'side' pieces."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    face, pfr, owner = _section(ctx, profile)
    spine = _path(ctx, path, near=topo.face_area_centroid(face)[1], normal=topo.plane_normal(face))
    if twist not in ("follow", "frenet"):
        raise ValueError("twist must be 'follow' or 'frenet'")
    start = topo.edge_endpoints(topo.edges(spine)[0])
    n = topo.plane_normal(face)
    tangent = _start_tangent(spine)
    if abs(float(np.dot(tangent, n))) < 0.05:
        raise ValueError("the cross-section lies along the path, so sweeping it makes no volume: draw it across the "
                         "path, e.g. on a face the path starts on, or at path.anchor('start') for a profile path")
    _, c = topo.face_area_centroid(face)
    gap = min(abs(float(np.dot(p - c, n))) for p in start)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_RightCorner
    mk = BRepOffsetAPI_MakePipeShell(spine)
    mk.SetMode(twist == "frenet")
    mk.SetTransitionMode(BRepBuilderAPI_RightCorner)      # sharp path corners get mitred joints
    mk.Add(topo.outer_wire(face), False, False)
    try:
        mk.Build()
        ok = mk.IsDone()
        if ok:
            mk.MakeSolid()
    except Exception as e:
        raise RuntimeError(f"sweep failed: {e}")
    if not ok:
        raise RuntimeError("sweep failed: the path may bend more tightly than the cross-section's size")
    shape = mk.Shape()
    if gap > 1e-3 * max(1.0, float(np.linalg.norm(topo.bbox(face)[1] - topo.bbox(face)[0]))):
        ctx.warn("the cross-section is not on the start of the path; the sweep follows the path's shape from where "
                 "the section is (draw it at path.anchor('start') to start on the path)")
    frame = Frame.make(c, pfr.x, n)
    named, used = _cap_names(mk, shape, ctx.fid)
    names = _name_rest(shape, named, used, ctx.fid, frame)
    design = FixedDesign({"start": Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1))})
    _finish(ctx, shape, names, frame, design, "sweep", op, target, owner)


# ===================================================================== shell
def _body_faces(ctx, b, items):
    out = []
    for it in (items if isinstance(items, (list, tuple)) else [items]):
        if isinstance(it, str):
            fs = b.faces_named(it)
            if not fs:
                raise RefError(f'{b.id}.face("{it}") not found')
        elif isinstance(it, FaceRef):
            ob, fs = it.resolve(ctx)
            if ob.id != b.id:
                raise RefError(f"{it!r} is not on {b.id}")
        else:
            raise TypeError(f"expected a face of {b.id}, got {it!r}")
        out += [f for f in fs if not any(f.IsSame(x) for x in out)]
    return out


def _dbg(*a):
    import os
    if os.environ.get("OPENFUSION_DEBUG"):
        print("[shell]", *a, flush=True)


@_record("modify")
def _shell(ctx, body, thickness, faces=None, direction="inside"):
    """Hollow a body into walls `thickness` thick. faces: the faces to remove (openings),
    e.g. [box1.face("+Z")] for an open box; none gives a closed hollow part.
    direction 'inside' (default) keeps the outer size and grows walls inward; 'outside'
    keeps the inner size (the original shape becomes the cavity) and grows walls outward.
    New faces: 'inner(<face>)' for each wall's inner side."""
    from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection
    if not thickness > 0:
        raise ValueError("thickness must be positive")
    if direction not in ("inside", "outside"):
        raise ValueError("direction must be 'inside' or 'outside'")
    b = ctx.body(body)
    t = float(thickness)
    open_faces = _body_faces(ctx, b, faces) if faces else []
    v0 = topo.volume(b.shape)
    area = sum(topo.face_area_centroid(f)[0] for f in topo.faces(b.shape)
               if not any(f.IsSame(o) for o in open_faces))
    # OpenCASCADE's offset is picky: sharp joints suit prisms (exact inner corners); curved
    # lofts meeting flat walls often need intersection handling with rounded joints. Try them
    # in that order and keep the first result that is a sound solid of a believable volume.
    tries = [(False, GeomAbs_Intersection), (False, GeomAbs_Arc), (True, GeomAbs_Arc), (True, GeomAbs_Intersection)]
    fail = (f"shell {t} mm failed: OpenCASCADE (the geometry kernel) could not build the inner walls of this shape "
            f"at {t} mm. Your part isn't too thin; the kernel struggles where faces meet at shallow or changing "
            "angles, typically where a loft joins another body. Another wall thickness may work.")
    if open_faces and _walls_fall_apart(b, open_faces):
        raise RuntimeError(f"shell {t} mm: leaving out {'that face' if len(open_faces) == 1 else 'those faces'} "
                           "would cut the walls into separate pieces; pick a face that leaves the rest connected")
    if direction == "inside":
        lo, hi = topo.bbox(b.shape)
        thin = float(min(hi - lo))
        if 2 * t >= thin - 1e-9:
            raise RuntimeError(f"shell {t} mm: the walls would meet; this body is only {thin:g} mm across at its "
                               f"thinnest (walls inside need less than half of that)")
    tight = [r for r in _convex_radii(b.shape, skip=open_faces) if r <= t * (1 + 1e-6)]
    if tight:
        fail = (f"shell {t} mm failed: a rounded edge of radius {min(tight):g} mm is not bigger than the wall, so its "
                "inside would shrink to nothing. Use a wall thinner than the smallest rounding, or round more.")
    # OpenCASCADE's offset is quick and exact on bodies of flat and simple round faces, but
    # free-form faces (lofts) break it where a crease fades out or turns from convex to concave,
    # with results that differ from one processor to another. Those bodies, and anything else
    # it can't do, are hollowed by sweeping a ball over the skin (see sweepshell.py).
    freeform = any(topo.surface_kind(f) not in ("plane", "cylinder", "cone", "sphere", "torus")
                   for f in topo.faces(b.shape))
    # a modelled thread's turns are closer together than any useful wall: the kernel's offset
    # fails on them (slowly), and sweeping a ball over them means hundreds of overlapping helical
    # tubes, which can take hours to join. Say so at once instead.
    threads = sorted({n.origin for _, n in b.face_names if n.name == "thread"})
    if threads:
        raise RuntimeError(f"shell {t:g} mm can't go round the modelled thread of {', '.join(threads)}: "
                           f"shell the part first, then add the thread (move the shell before {threads[0]})")
    got = None
    if tight and direction == "inside" and freeform:
        # the inner side of that rounding would shrink to nothing: every method fails there,
        # and the fallbacks would take minutes to find out
        raise RuntimeError(fail)
    if freeform and direction == "inside":
        # smooth lofts: their inner walls built as lofts, the rest by the kernel, chunk by chunk
        from .loftshell import loft_shell
        _t0 = time.perf_counter()
        res = loft_shell(b.shape, t, open_faces)
        _dbg(f"loft shell {time.perf_counter() - _t0:.2f}s: {'ok' if res is not None else 'not applicable or failed'}")
        from .sweepshell import _gvol
        if res is not None and _plausible_wall(_gvol(res), v0, area, t, direction):     # its volume is known
            got = (res, _names_by_geometry(ctx, b, res, t))
    if got is None:
        _t0 = time.perf_counter()
        if freeform:            # on free-form faces only the rounded joins come out right, if at all
            tries = [(True, GeomAbs_Arc), (False, GeomAbs_Arc)]
        got = _kernel_shell(ctx, b, t, open_faces, direction, tries, v0, area, freeform)
        _dbg(f"kernel offset {time.perf_counter() - _t0:.2f}s: {'ok' if got else 'failed'}")
    if got is None and tight and direction == "inside":
        raise RuntimeError(fail)            # the kernel's offset was the only thing that could cope
    if got is None:
        # free-form bodies, and anything the kernel's offset can't do: sweep a ball over the skin
        from .sweepshell import sweep_shell
        _t0 = time.perf_counter()
        res = sweep_shell(b.shape, t, open_faces, direction)
        _dbg(f"sweep {time.perf_counter() - _t0:.2f}s: {'ok' if res is not None else 'failed'}")
        if res is None:
            raise RuntimeError(fail)
        wall = topo.volume(res)                  # the result is the wall itself
        if not _plausible_wall(wall, v0, area, t, direction):
            _dbg(f"sweep result implausible: wall {wall:.1f}, area x t {area * t:.1f}")
            raise RuntimeError(fail)
        got = (res, _names_by_geometry(ctx, b, res, t))
    shape, names = got
    ctx.put(b.evolve(shape=shape, face_names=names, rounds=None, touched_by=ctx.fid))



def _kernel_shell(ctx, b, t, open_faces, direction, tries, v0, area, freeform):
    """OpenCASCADE's own offset: exact sharp inner corners, quick on bodies of flat and simple
    round faces. Returns (shape, names) or None."""
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape, BRepOffsetAPI_MakeThickSolid
    off = -t if direction == "inside" else t
    mk = None
    if open_faces:
        lst = List_TopoDS_Shape()
        for f in open_faces:
            lst.Append(f)
        for tt in ((t,) if freeform else (t, t - min(1e-5, t * 1e-3))):   # a hair thinner when walls exactly meet a rounding
            o = -tt if direction == "inside" else tt
            for inter, join in tries:
                cand = BRepOffsetAPI_MakeThickSolid()
                try:
                    cand.MakeThickSolidByJoin(b.shape, lst, o, 1e-4, BRepOffset_Skin, inter, False, join)
                    cand.Build()
                except Exception:
                    continue
                res = cand.Shape() if cand.IsDone() else None
                if res is not None and len(topo.solids(res)) == 1 and topo.is_valid(res) and \
                        _plausible_wall(topo.volume(res), v0, area, tt, direction):
                    mk = cand
                    break
            if mk is not None:
                break
    if open_faces and mk is None:
        return None
    if open_faces:
        shape, _ = _single_solid(mk.Shape())
        present = topo.ShapeDict()
        for f in topo.faces(shape):
            present[f] = True
        out = []
        for f, n in b.face_names:
            label = n.name if n.origin == b.id else f"{n.origin}.{n.name}"
            mods = list(_iter_list(mk.Modified(f)))
            kept = mods if mods else ([] if mk.IsDeleted(f) else [f])
            gen = list(_iter_list(mk.Generated(f)))
            own, new = (n, FaceName(ctx.fid, f"inner({label})")) if direction == "inside" else \
                (FaceName(ctx.fid, f"inner({label})"), n)
            for k in kept:
                c = present.canonical(k)
                if c is not None:
                    out.append((c, own if not any(k.IsSame(o) for o in open_faces) else n))
            for g in gen:
                c = present.canonical(g)
                if c is not None:
                    out.append((c, new))
        seen = topo.ShapeSet()
        names = []
        for f, n in out:
            if f not in seen:
                seen.add(f)
                names.append((f, n))
        names = propagate_names(_Same(), names, shape, b.frame, b.id)
    else:
        offset = None
        for inter, join in tries:
            mk = BRepOffsetAPI_MakeOffsetShape()
            try:
                mk.PerformByJoin(b.shape, off, 1e-4, BRepOffset_Skin, inter, False, join)
            except Exception:
                continue
            res = (_as_solid(mk.Shape(), inside_of=b.shape) if direction == "inside" else
                   _as_solid(mk.Shape(), around=b.shape)) if mk.IsDone() else None
            if res is None:
                continue
            vo = topo.volume(res)
            wall = (v0 - vo) if direction == "inside" else (vo - v0)
            if _plausible_wall(wall, v0, area, t, direction):
                offset = res
                break
        if offset is None:
            return None
        o_names = []
        for f, n in b.face_names:
            label = n.name if n.origin == b.id else f"{n.origin}.{n.name}"
            for g in _images(mk, f):
                o_names.append((g, FaceName(ctx.fid, f"inner({label})") if direction == "inside" else n))
        if direction == "inside":
            outer = b
            inner = Body(id=f"{ctx.fid}#cavity", shape=offset, frame=b.frame, face_names=tuple(o_names), design=b.design)
        else:
            outer = b.evolve(shape=offset, face_names=tuple(o_names))
            inner = Body(id=f"{ctx.fid}#cavity", shape=b.shape, frame=b.frame,
                         face_names=tuple((f, FaceName(ctx.fid, f"inner({n.name if n.origin == b.id else n.origin + '.' + n.name})"))
                                          for f, n in b.face_names), design=b.design)
        algo = _boolean("cut", outer, [inner], ctx.fid)
        shape, nsol = _single_solid(algo.Shape())
        if nsol == 0 or not topo.is_valid(shape):
            return None
        names = propagate_names(algo, list(outer.face_names) + list(inner.face_names), shape, b.frame, b.id)
        if direction == "inside" and abs(topo.volume(shape) - topo.volume(b.shape)) < 1e-9:
            return None
    return shape, names


def _convex_radii(shape, skip=()):
    """Radii of rounded (cylindrical / toroidal minor) faces, other than those in skip (the
    openings); an inward wall can't be thicker."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus
    out = []
    for f in topo.faces(shape):
        if any(f.IsSame(o) for o in skip):
            continue
        s = BRepAdaptor_Surface(f)
        if s.GetType() == GeomAbs_Cylinder:
            # convex when the outward normal points away from the axis
            ax = s.Cylinder().Axis()
            p, n = topo.face_point_normal_mid(f)
            o = topo.to_np(ax.Location())
            d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
            rad = (p - o) - np.dot(p - o, d) * d
            if np.dot(rad, n) > 0:
                out.append(s.Cylinder().Radius())
        elif s.GetType() == GeomAbs_Torus:
            # convex when the outward normal points away from the tube's own centre line
            tor = s.Torus()
            ax = tor.Axis()
            p, n = topo.face_point_normal_mid(f)
            o = topo.to_np(ax.Location())
            d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
            rad = (p - o) - np.dot(p - o, d) * d
            centre = o + tor.MajorRadius() * rad / (np.linalg.norm(rad) or 1.0)
            if np.dot(p - centre, n) > 0:
                out.append(tor.MinorRadius())
    return out


def _as_solid(shape, inside_of=None, around=None):
    """The kernel sometimes returns an offset as a closed skin; make it a properly oriented solid.
    The repair step can crash on a badly broken offset, so it only runs on offsets that already
    look right: inside the original body's box for an inward offset (inside_of), around it for an
    outward one (around), with a sensible volume."""
    sols = topo.solids(shape)
    if len(sols) == 1 and topo.is_valid(sols[0]):
        return sols[0]
    try:
        lo, hi = topo.bbox(shape)
        v = topo.volume(shape)
    except Exception:
        return None
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.isfinite(v) and v > 0):
        return None
    if inside_of is not None:
        blo, bhi = topo.bbox(inside_of)
        tol = 1e-6 * max(1.0, float(np.linalg.norm(bhi - blo)))
        if np.any(lo < blo - tol) or np.any(hi > bhi + tol) or v >= topo.volume(inside_of):
            return None
    if around is not None:
        blo, bhi = topo.bbox(around)
        tol = 1e-6 * max(1.0, float(np.linalg.norm(bhi - blo)))
        if np.any(lo > blo + tol) or np.any(hi < bhi - tol) or v <= topo.volume(around):
            return None
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.ShapeFix import ShapeFix_Shape, ShapeFix_Solid
    try:
        fx = ShapeFix_Shape(shape)
        fx.Perform()
        shape = fx.Shape()
    except Exception:
        return None
    sols = topo.solids(shape)
    if len(sols) == 1 and topo.is_valid(sols[0]):
        return sols[0]
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    ex = TopExp_Explorer(shape, TopAbs_SHELL)
    shells = []
    while ex.More():
        shells.append(ex.Current())
        ex.Next()
    if len(shells) != 1:
        return None
    try:
        ms = BRepBuilderAPI_MakeSolid(topo._cast("Shell")(shells[0]))
        fix = ShapeFix_Solid(ms.Solid())
        fix.Perform()
        sol = fix.Solid()
        sols = topo.solids(sol)
        sol = sols[0] if sols else None
    except Exception:
        return None
    return sol if sol is not None and topo.is_valid(sol) and topo.volume(sol) > 0 else None


def _images(mk, f):
    """Where an offset put a face (OCCT reports it as Modified or as Generated)."""
    return list(_iter_list(mk.Modified(f))) or list(_iter_list(mk.Generated(f)))


def _crease_angles(e, faces):
    """Signed crease angle (degrees) at samples along an edge between two faces: positive
    where the edge is convex (an outside corner), negative where concave."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d, BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt2d, gp_Vec2d
    f1, f2 = faces
    ad = BRepAdaptor_Curve(e)
    c1, c2 = BRepAdaptor_Curve2d(e, f1), BRepAdaptor_Curve2d(e, f2)
    s1 = BRepAdaptor_Surface(f1)
    span = max(s1.LastUParameter() - s1.FirstUParameter(), s1.LastVParameter() - s1.FirstVParameter())
    span = span if math.isfinite(span) and 0 < span < 1e6 else 1.0
    out = []
    for s_ in (0.1, 0.25, 0.5, 0.75, 0.9):        # away from the ends, where 'into the face' is ambiguous
        u = ad.FirstParameter() + s_ * (ad.LastParameter() - ad.FirstParameter())
        uv1, d1 = gp_Pnt2d(), gp_Vec2d()
        c1.D1(u, uv1, d1)
        uv2 = c2.Value(u)
        n1 = topo.face_normal_at(f1, uv1.X(), uv1.Y())
        n2 = topo.face_normal_at(f2, uv2.X(), uv2.Y())
        ang = math.degrees(math.acos(max(-1.0, min(1.0, float(n1 @ n2)))))
        # a step across the edge into face 1, found in its parameter plane
        perp = np.array([-d1.Y(), d1.X()])
        perp = perp / (np.linalg.norm(perp) or 1.0)
        sign = 1.0
        for k in (1.0, -1.0):
            q = gp_Pnt2d(uv1.X() + k * perp[0] * 1e-4 * span, uv1.Y() + k * perp[1] * 1e-4 * span)
            if BRepClass_FaceClassifier(f1, q, 1e-9).State() == TopAbs_IN:
                m = topo.to_np(s1.Value(q.X(), q.Y())) - topo.to_np(s1.Value(uv1.X(), uv1.Y()))
                if float(m @ n2) > 1e-12:
                    sign = -1.0
                break
        out.append(sign * ang)
    return out


def _walls_fall_apart(b, open_faces):
    """Would leaving out these faces split the remaining walls into unconnected pieces?
    Decided from which faces share edges, so it never depends on the kernel."""
    faces = [f for f in topo.faces(b.shape) if not any(f.IsSame(o) for o in open_faces)]
    if not faces:
        return False
    index = topo.ShapeDict()
    for i, f in enumerate(faces):
        index[f] = i
    parent = list(range(len(faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for e, fs in topo.edge_face_map(b.shape).items():
        ids = [index.get(f) for f in fs]
        ids = [i for i in ids if i is not None]
        for i in ids[1:]:
            parent[find(i)] = find(ids[0])
    return len({find(i) for i in range(len(faces))}) > 1


def _names_by_geometry(ctx, b, shape, t):
    """Name the faces of a shelled body from where they lie: on an original face keeps that
    face's name; a wall's inner side, t inside a face, is '<step>.inner(<face>)'."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    orig = list(b.face_names)
    boxes = [(f, n, topo.bbox(f)) for f, n in orig]
    out = []
    for g in topo.faces(shape):
        p, _ = topo.face_point_normal_mid(g)
        try:
            from OCP.BRepClass import BRepClass_FaceClassifier
            from OCP.TopAbs import TopAbs_IN
            from OCP.BRepTools import BRepTools
            from OCP.gp import gp_Pnt2d
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            u0, u1, v0_, v1 = BRepTools.UVBounds_s(g)
            a = BRepAdaptor_Surface(g)
            for fu, fv in ((0.5, 0.5), (0.3, 0.4), (0.7, 0.6), (0.25, 0.75), (0.75, 0.25)):
                q = gp_Pnt2d(u0 + fu * (u1 - u0), v0_ + fv * (v1 - v0_))
                if BRepClass_FaceClassifier(g, q, 1e-9).State() == TopAbs_IN:
                    p = topo.to_np(a.Value(q.X(), q.Y()))
                    break
        except Exception:
            pass
        v = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
        best, bestd = None, None
        for f, n, (lo, hi) in boxes:
            if np.any(p < lo - t * 1.5 - 1e-6) or np.any(p > hi + t * 1.5 + 1e-6):
                continue
            d = BRepExtrema_DistShapeShape(v, f).Value()
            if bestd is None or d < bestd:
                best, bestd = n, d
        if best is not None and bestd < 1e-4:
            out.append((g, best))
        elif best is not None and abs(bestd - t) < 0.06 * t + 1e-4:     # rounded inside corners sit up to 4 % deeper
            label = best.name if best.origin == b.id else f"{best.origin}.{best.name}"
            out.append((g, FaceName(ctx.fid, f"inner({label})")))
    return propagate_names(_Same(), out, shape, b.frame, b.id)


def _plausible_wall(vol, v0, area, t, direction):
    """A real shell's wall volume is roughly area x thickness (less where thick walls meet).
    Catches results the kernel reports as done that kept the body solid or lost walls."""
    if vol <= 0:
        return False
    if direction == "inside" and vol >= v0 * (1 - 1e-7):
        return False
    expect = area * t if direction == "outside" else min(area * t, v0)
    if t * area < 0.5 * v0 and vol > 1.5 * area * t:
        return False            # far more material than walls this thick can hold: the inside stayed solid
    return vol >= 0.6 * expect * (0.5 if t * area > v0 else 1.0)


class _Same:
    """History for names already matched to the result's faces."""

    def IsDeleted(self, s):
        return False

    def Modified(self, s):
        return List_TopoDS_Shape()

    def Generated(self, s):
        return List_TopoDS_Shape()


# ===================================================================== draft
@_record("modify")
def _draft(ctx, body, faces, angle, plane):
    """Tilt faces by `angle` degrees, as for mould release or overhang-free printing.
    plane: a flat face (or an anchor) whose normal is the pull direction; faces stay where
    they cross that plane and lean away from it going along the normal. A negative angle
    leans them the other way."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
    b = ctx.body(body)
    fs = _body_faces(ctx, b, faces)
    if not fs:
        raise ValueError("pick the faces to draft")
    if isinstance(plane, FaceRef):
        pb, pf = plane.resolve(ctx)
        pface = max(pf, key=lambda f: topo.face_area_centroid(f)[0])
        if topo.surface_kind(pface) != "plane":
            raise RefError(f"{plane!r} is not flat")
        n = topo.plane_normal(pface)
        p0 = topo.face_area_centroid(pface)[1]
    else:
        f = ctx.frame(plane)
        n, p0 = f.z, f.origin
    a = float(angle)
    if a == 0 or abs(a) >= 89:
        raise ValueError("angle must be between -89 and 89 degrees (not 0)")
    mk = BRepOffsetAPI_DraftAngle(b.shape)
    pln = gp_Pln(_P(p0), _D(n))
    for f in fs:
        # positive: the part narrows going along the pull direction (no overhangs when that is up)
        mk.Add(f, _D(n), -math.radians(a), pln, True)
        if not mk.AddDone():
            lbl = b.label_of(f) or "a face"
            raise RuntimeError(f"cannot draft {lbl}: faces square to the pull direction (or curved the wrong way) "
                               "can't tilt; pick side walls")
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError("draft failed in the geometry kernel")
    shape, _ = _single_solid(mk.Shape())
    if not topo.is_valid(shape):
        raise RuntimeError("draft made a broken solid (the angle may be too steep for this body)")
    names = propagate_names(_ModifyHistory(mk), list(b.face_names), shape, b.frame, b.id)
    ctx.put(b.evolve(shape=shape, face_names=names, rounds=None, touched_by=ctx.fid))


class _ModifyHistory:
    """Shape-modification algorithms report one image per face through ModifiedShape."""

    def __init__(self, mk):
        self.mk = mk

    def IsDeleted(self, s):
        return False

    def Modified(self, s):
        out = List_TopoDS_Shape()
        try:
            img = self.mk.ModifiedShape(s)
            if img is not None and not img.IsNull() and not img.IsSame(s):
                out.Append(img)
        except Exception:
            pass
        return out

    def Generated(self, s):
        return List_TopoDS_Shape()


profile, revolve, loft, sweep, shell, draft = _profile, _revolve, _loft, _sweep, _shell, _draft
__all__ = ["profile", "revolve", "loft", "sweep", "shell", "draft"]
_ = (FrameRef, NameError_)
