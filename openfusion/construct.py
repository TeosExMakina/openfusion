"""Construction planes and axes: reference bodies whose 'origin' anchor is the plane (Z its
normal) or the axis (Z along it), so they go anywhere an anchor does.

    mid = plane(mid=(block.face("-X"), block.face("+X")))     # halfway between two faces
    tilt = plane(block.face("+Z"), angle=30, about=block.edge("+Y+Z"))
    s = sketch(at=tilt, ...)
    a = axis(points=(block.anchor("+X+Y+Z"), block.anchor("-X-Y-Z")))
    pattern_circ(pin, 5, axis=a)

They are ghosts: never exported, never in booleans; the view shows them as a translucent
square or a line."""
from __future__ import annotations

import math

import numpy as np

from . import topo
from .geom import Frame, any_perpendicular, rotation_about, unit


def _turn(axis, angle, v):
    return rotation_about((0, 0, 0), axis, angle)[:3, :3] @ np.asarray(v, float)


def _square(fr, size):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    h = size / 2
    poly = BRepBuilderAPI_MakePolygon()
    for a, b in ((-h, -h), (h, -h), (h, h), (-h, h)):
        poly.Add(gp_Pnt(*(fr.origin + a * fr.x + b * fr.y)))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()


def _segment(a, b):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt
    return BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b)).Edge()


def _scale_near(ctx, refs, default=40.0):
    """A display size: the diagonal of the body the references are on, else `default`."""
    from .refs import Handle
    for r in refs:
        o = r.owner() if hasattr(r, "owner") else (r.handle if hasattr(r, "handle") else None)
        if isinstance(o, Handle):
            try:
                b = ctx.body(o, allow_removed=True)
                lo, hi = topo.bbox(b.shape)
                return max(5.0, float(np.linalg.norm(hi - lo)))
            except Exception:
                pass
    return default


def _frame_of(ctx, ref):
    from .refs import FaceRef
    if isinstance(ref, FaceRef):
        ref = ref.anchor()
    return ctx.frame(ref)


def _put(ctx, fr, shape, label, design_frames, size):
    from .body import Body, FaceName
    from .features import ProfileDesign
    names = tuple((f, FaceName(ctx.fid, label)) for f in topo.faces(shape))
    ctx.put(Body(id=ctx.fid, shape=shape, frame=fr, face_names=names, design=ProfileDesign(design_frames),
                 kind="construction", role="reference", params=(("size", size),), history=(ctx.fid,)))


def _plane(ctx, at=None, offset=0.0, angle=0.0, about="X", mid=None, points=None, size=None):
    """A construction plane. Its 'origin' anchor is the plane, Z its normal: use it as at= for
    a sketch or primitive, plane= for split and mirror, and so on.
      plane(at, offset=5)                    parallel to a face / anchor's XY plane, 5 mm along its normal
      plane(at, angle=30, about="X")         tilted about the anchor's own X (or "Y") axis
      plane(at, angle=30, about=edge)        through a straight edge, tilted from the face
      plane(mid=(face_a, face_b))            halfway between two faces or anchors
      plane(points=(p1, p2, p3))             through three points (normal: right-hand rule)
    size: the square drawn in the view (default: about the size of the part)."""
    from .ops import _is_line_ref, ref_line
    if mid is not None and points is not None:
        raise ValueError("give mid=(a, b) or points=(p1, p2, p3), not both")
    if points is not None:
        if len(points) != 3:
            raise ValueError("points: give three points")
        ps = [np.asarray(_frame_of(ctx, p).origin, float) for p in points]
        n = np.cross(ps[1] - ps[0], ps[2] - ps[0])
        if np.linalg.norm(n) < 1e-9 * max(1.0, float(np.linalg.norm(ps[1] - ps[0])) ** 2):
            raise ValueError("the three points are in a line: they don't fix a plane")
        fr = Frame.make(ps[0], unit(ps[1] - ps[0]), unit(n))
        refs = list(points)
    elif mid is not None:
        if len(mid) != 2:
            raise ValueError("mid: give two faces or anchors")
        fa, fb = (_frame_of(ctx, m) for m in mid)
        if abs(float(np.dot(fa.z, fb.z))) < 1 - 1e-6:
            raise ValueError("mid: the two faces aren't parallel")
        # the midpoint of the anchors, moved onto the plane halfway between the two planes
        o = (np.asarray(fa.origin, float) + np.asarray(fb.origin, float)) / 2
        z = np.asarray(fa.z, float)
        da, db = float(z @ np.asarray(fa.origin)), float(z @ np.asarray(fb.origin))
        o = o - (float(z @ o) - (da + db) / 2) * z
        fr = Frame.make(o, fa.x, fa.z)
        refs = list(mid)
    elif not isinstance(about, str) and _is_line_ref(about):
        # through a straight edge, turned `angle` from the face's plane (or the edge's flat face)
        normals = []
        pt, d = ref_line(ctx, about, "about", normals)
        base = _frame_of(ctx, at) if at is not None else None
        n0 = np.asarray(base.z if base is not None else (normals[0] if normals else any_perpendicular(d)), float)
        n0 = n0 - float(n0 @ d) * d
        if np.linalg.norm(n0) < 1e-9:
            raise ValueError("about: the edge runs along the plane's normal; pick an edge lying in the face")
        n = _turn(d, angle, unit(n0)) if angle else unit(n0)
        fr = Frame.make(pt, d, n)
        refs = ([at] if at is not None else []) + [about]
    else:
        if about not in ("X", "Y"):
            raise ValueError("about: 'X', 'Y' or a straight edge")
        fr = _frame_of(ctx, at) if at is not None else Frame.world()
        refs = [at] if at is not None else []
        if angle:
            ax = fr.x if about == "X" else fr.y
            fr = Frame.make(fr.origin, _turn(ax, angle, fr.x), _turn(ax, angle, fr.z))
    if offset:
        fr = fr.offset(z=float(offset))
    size = float(size) if size is not None else _scale_near(ctx, refs)
    if not size > 0:
        raise ValueError("size must be positive")
    _put(ctx, fr, _square(fr, size), "plane", {"center": Frame.world()}, size)


def _axis(ctx, ref=None, points=None, length=None):
    """A construction axis. Its 'origin' anchor's Z runs along it: use it as axis= for revolve
    and circular patterns, about= for rotate, or anywhere an anchor goes.
      axis(body.edge(...))                   along a straight edge
      axis(body.face("side"))                the axis of a round face or circular edge
      axis(anchor)                           an anchor's Z
      axis(points=(p1, p2))                  through two points, from p1 to p2
    length: the line drawn in the view (default: the edge, the points' distance, or the part)."""
    from .ops import _is_line_ref, ref_line
    from .refs import EdgeBetweenRef, EdgeRef
    if (ref is None) == (points is None):
        raise ValueError("give an edge, face or anchor, or points=(p1, p2)")
    span = None
    if points is not None:
        if len(points) != 2:
            raise ValueError("points: give two points")
        a, b = (np.asarray(_frame_of(ctx, p).origin, float) for p in points)
        if np.linalg.norm(b - a) < 1e-9:
            raise ValueError("the two points are the same")
        o, d, span = a, unit(b - a), (0.0, float(np.linalg.norm(b - a)))
        refs = list(points)
    elif _is_line_ref(ref):
        o, d = ref_line(ctx, ref, "axis")
        refs = [ref]
        if isinstance(ref, (EdgeRef, EdgeBetweenRef)):
            _, edges = ref.resolve(ctx)
            if topo.edge_kind(edges[0]) == "line":
                p, q = topo.edge_endpoints(edges[0])
                ts = sorted(float(np.dot(np.asarray(x) - o, d)) for x in (p, q))
                span = (ts[0], ts[1])
    else:
        f = _frame_of(ctx, ref)
        o, d = np.asarray(f.origin, float), np.asarray(f.z, float)
        refs = [ref]
    L = float(length) if length is not None else (span[1] - span[0] if span else _scale_near(ctx, refs))
    if not L > 0:
        raise ValueError("length must be positive")
    if span is None or length is not None:
        span = (-L / 2, L / 2) if points is None else (0.0, L)
    fr = Frame.make(o, any_perpendicular(d), d)
    shape = _segment(o + span[0] * d, o + span[1] * d)
    _put(ctx, fr, shape, "axis", {"start": Frame.make((0, 0, span[0])), "end": Frame.make((0, 0, span[1]))}, L)


def _make_api():
    from .ops import _record
    return _record("create")(_plane), _record("create")(_axis)


plane, axis = _make_api()
