"""Shell by sweeping a ball over the skin: the wall is every point of the body within the wall
thickness of a kept face.

OpenCASCADE's own offset has to trim every offset face against its neighbours, and it fails
where a crease changes from convex to concave or nearly flattens (a loft meeting the body it
starts on), with results that even differ between processors. This builds the same wall
from simple pieces that are allowed to overlap, and lets one boolean sort them out:

  - a slab under each smooth patch of kept faces (the patch thickened inward by t, starting
    a little outside the body so no slab face lies on the body's skin),
  - a round tube around each edge where neighbouring slabs splay apart (inside corners, and
    the rims of openings that lean in), and a ball at the ends of those edges,

then wall = body ∩ (union of the pieces). For an outside shell the pieces grow outward and
wall = (union of the pieces) - body. Inside corners come out rounded at the wall thickness
(the true offset) instead of sharp, which suits printing.

Tubes and balls are a hair (2 %, 4 %) bigger than the wall so they cross the slabs instead of
grazing them; grazing surfaces are what booleans handle worst. The extra is 0.02 mm on a
1 mm wall, far below a printer's resolution.
"""
from __future__ import annotations

import math
import time

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.collections import List_TopoDS_Shape
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Shell

from . import topo

TUBE = 1.02          # tube radius / wall
BALL = 1.04          # ball radius / wall
OVERSHOOT = 0.05     # slabs start this far (x wall) outside the skin
SMOOTH_DEG = 0.5     # creases flatter than this all along an edge join faces into one patch
NEAR_DEG = 30.0      # convex creases sharper than this all along need no tube (slabs overlap cleanly)
FUZZY = 1e-5
FUZZIES = (FUZZY, 0.0, 1e-4, 1e-3)


def _dbg(*a):
    import os
    if os.environ.get("OPENFUSION_DEBUG"):
        print("[sweep]", *a, flush=True)


def _dump(name, shape):
    import os
    d = os.environ.get("OPENFUSION_DEBUG_DIR")
    if d:
        from OCP.BRepTools import BRepTools
        BRepTools.Write_s(shape, os.path.join(d, name + ".brep"))


def _bop(cls, a, b, fuzzy=0.0):
    """A boolean of a and b that leaves both untouched (the kernel otherwise edits its inputs,
    which may be bodies the engine keeps for other steps)."""
    op = cls()
    op.SetArguments(_shapes_list([a]))
    op.SetTools(_shapes_list([b]))
    op.SetFuzzyValue(fuzzy)
    op.SetNonDestructive(True)
    op.Build()
    return op


def _shapes_list(ss):
    lst = List_TopoDS_Shape()
    for s in ss:
        lst.Append(s)
    return lst


def _crease(e, fs):
    from .features import _crease_angles
    return _crease_angles(e, fs)


def _patches(faces, smooth_pairs):
    """Group faces (indices) joined by smooth edges."""
    parent = list(range(len(faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in smooth_pairs:
        parent[find(i)] = find(j)
    groups = {}
    for i in range(len(faces)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _slab(faces, t, sign):
    """The patch (faces sharing edges) thickened by t against its normals (sign -1: inward),
    starting OVERSHOOT*t on the other side of the skin."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape, BRepOffsetAPI_MakeThickSolid
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    eps = OVERSHOOT * t
    if len(faces) == 1 and topo.surface_kind(faces[0]) == "plane":
        f = BRepBuilderAPI_Copy(faces[0]).Shape()
        n = topo.face_outward_normal(faces[0])
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Trsf
        tr = gp_Trsf()
        tr.SetTranslation(gp_Vec(*(-sign * eps * n)))
        f = BRepBuilderAPI_Transform(f, tr, True).Shape()
        pr = BRepPrimAPI_MakePrism(f, gp_Vec(*(sign * (t + eps) * n)))
        sols = topo.solids(pr.Shape())
        return sols[0] if len(sols) == 1 else None
    sh = TopoDS_Shell()
    bb = BRep_Builder()
    bb.MakeShell(sh)
    for f in faces:
        bb.Add(sh, f)
    sh = BRepBuilderAPI_Copy(sh).Shape()
    # closed faces (a cylinder's side) would give walls that carry a seam, which booleans
    # misread; split them first
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed
    dc = ShapeUpgrade_ShapeDivideClosed(sh)
    dc.SetNbSplitPoints(1)
    dc.Perform()
    sh = dc.Result()
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    fb = ShapeAnalysis_FreeBounds(sh)
    closed = not topo.edges(fb.GetClosedWires()) and not topo.edges(fb.GetOpenWires())
    if closed:
        # a whole smooth skin (a sphere, a fully rounded box): the slab is the region between
        # the skin pushed out a little and the skin pushed in by the wall
        outer = _offset_solid(sh, -sign * eps)
        inner = _offset_solid(sh, sign * t)
        if outer is None or inner is None:
            return None
        big, small = (outer, inner) if _gvol(outer) > _gvol(inner) else (inner, outer)
        cu = _bop(BRepAlgoAPI_Cut, big, small)
        sols = topo.solids(cu.Shape()) if cu.IsDone() else []
        if len(sols) != 1:
            return None
        s = sols[0]
    else:
        mo = BRepOffsetAPI_MakeOffsetShape()
        mo.PerformBySimple(sh, -sign * eps)
        if not mo.IsDone():
            return None
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidBySimple(mo.Shape(), sign * (t + eps))
        mk.Build()
        if not mk.IsDone():
            return None
        sols = topo.solids(mk.Shape())
        if len(sols) != 1:
            return None
        s = sols[0]
    # offsets of free-form faces come as offset surfaces, which booleans intersect slowly and
    # unreliably; approximate them by ordinary B-splines (to well under a micron)
    from OCP.ShapeCustom import ShapeCustom
    try:
        c = ShapeCustom.ConvertToBSpline_s(s, False, False, True, False)
        cs = topo.solids(c)
        if len(cs) == 1 and topo.is_valid(cs[0]):
            s = cs[0]
    except Exception:
        pass
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, g)
    if g.Mass() < 0:
        s = topo._cast("Solid")(s.Reversed())
    return s


def _offset_solid(shell, d):
    """A closed smooth skin moved d along its normals, as a solid."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.TopAbs import TopAbs_SHELL
    mo = BRepOffsetAPI_MakeOffsetShape()
    mo.PerformBySimple(shell, d)
    if not mo.IsDone():
        return None
    shells = topo._explore(mo.Shape(), TopAbs_SHELL, topo._cast("Shell"))
    if len(shells) != 1:
        return None
    sol = BRepBuilderAPI_MakeSolid(shells[0]).Solid()
    if _gvol(sol) < 0:
        sol = topo._cast("Solid")(sol.Reversed())
    return sol


def _tube(e, r):
    """A solid round tube of radius r around an edge, flat at its ends."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeTorus
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    ad = BRepAdaptor_Curve(e)
    u0, u1 = ad.FirstParameter(), ad.LastParameter()
    if ad.GetType() == GeomAbs_Line:
        p, q = topo.to_np(ad.Value(u0)), topo.to_np(ad.Value(u1))
        d = q - p
        L = float(np.linalg.norm(d))
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p), gp_Dir(*(d / L))), r, L).Shape()
    if ad.GetType() == GeomAbs_Circle and ad.Circle().Radius() > r * 1.01:
        c = ad.Circle()
        ax = c.Position()                     # x towards parameter 0, turning with the parameter
        ang = u1 - u0
        a2 = gp_Ax2(ax.Location(), ax.Direction(), ax.XDirection())
        a2.Rotate(ax.Axis(), u0)
        if ang >= 2 * math.pi - 1e-9:
            return BRepPrimAPI_MakeTorus(a2, c.Radius(), r).Shape()
        return BRepPrimAPI_MakeTorus(a2, c.Radius(), r, ang).Shape()
    # a flat edge that is really lines and arcs (a loft's section curve): simple pieces
    pts = np.array([topo.to_np(ad.Value(u0 + (u1 - u0) * k / 64)) for k in range(65)])
    c = pts.mean(axis=0)
    _, sv, vt = np.linalg.svd(pts - c)
    if sv[1] > 1e-6 and sv[-1] < 1e-6 * max(1.0, sv[0]):
        from .loftshell import _plain_tube
        ps = _plain_tube(e, vt[-1], r)
        if ps:
            u = _union(ps)
            if u is not None:
                return u
    # anything else (a loft's corner edge): a chain of straight cylinders with balls at the
    # joints. A circle swept along such an edge gives a heavy surface that booleans mishandle
    # where the crease fades out.
    return _bead_tube(ad, r)


def _bead_tube(ad, r):
    """A tube as a chain of straight cylinders with a ball at each joint, for edges a circle
    that aren't flat. Chords are short enough to sag less than 2 % of the radius, and the
    radius grows by that sag so the true tube is covered."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    d = 0.02 * r
    pts = GCPnts_QuasiUniformDeflection(ad, d)
    if not pts.IsDone() or pts.NbPoints() < 2:
        return None
    ps = [topo.to_np(pts.Value(i)) for i in range(1, pts.NbPoints() + 1)]
    rr = r + d
    parts = []
    for p, q in zip(ps, ps[1:]):
        v = q - p
        L = float(np.linalg.norm(v))
        if L < 1e-9:
            continue
        parts.append(BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p), gp_Dir(*(v / L))), rr, L).Shape())
    for p in ps[1:-1]:
        parts.append(_ball(p, rr * 1.01))
    if not parts:
        return None
    return _union(parts)


_VOLUMES = {}       # hash -> [(shape, volume)]: shapes are compared with IsEqual (orientation counts)


def _gvol(s):
    """Volume, remembered per shape. Flat and simple round faces: the quick formula, which is
    exact for them. Anything with free-form faces: adaptive integration, since the quick
    formula is percents off there, enough to make a sound boolean look broken; a loose
    tolerance is exact enough for these checks and fast."""
    hit = next((v for x, v in _VOLUMES.get(hash(s), ()) if x.IsEqual(s)), None)
    if hit is not None:
        return hit
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    if all(topo.surface_kind(f) in ("plane", "cylinder", "cone", "sphere", "torus") for f in topo.faces(s)):
        BRepGProp.VolumeProperties_s(s, g)
    else:
        BRepGProp.VolumeProperties_s(s, g, 1e-3)
    remember_volume(s, g.Mass())
    return g.Mass()


def remember_volume(s, v):
    """Record a volume known some other way (a solid built from shells of known volume)."""
    if len(_VOLUMES) > 2000:
        _VOLUMES.clear()
    _VOLUMES.setdefault(hash(s), []).append((s, v))


def _close(got, want, scale):
    return abs(got - want) <= 0.02 * abs(want) + 1e-5 * abs(scale)


def _cut_one(s, tool):
    """s - tool, checked: booleans sometimes hand back the input untouched, or a broken result,
    instead of failing. Retries with other tolerances; None if nothing works."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    a, b = Bnd_Box(), Bnd_Box()
    BRepBndLib.Add_s(s, a)
    BRepBndLib.Add_s(tool, b)
    if a.IsOut(b):
        return s
    common = _bop(BRepAlgoAPI_Common, s, tool)
    overlap = _gvol(common.Shape()) if common.IsDone() else None
    if overlap is not None and overlap < 1e-9:
        return s
    v0 = _gvol(s)
    for fz in FUZZIES:
        cu = BRepAlgoAPI_Cut()
        cu.SetArguments(_shapes_list([s]))
        cu.SetTools(_shapes_list([tool]))
        cu.SetFuzzyValue(fz)
        cu.SetNonDestructive(True)
        cu.SetUseOBB(True)
        cu.SetRunParallel(True)
        cu.Build()
        if not cu.IsDone():
            continue
        r = cu.Shape()
        if overlap is not None and _close(v0 - _gvol(r), overlap, v0) and topo.is_valid(r):
            return r
        _dbg(f"cut went wrong (fuzzy {fz}): removed {v0 - _gvol(r):.3f}, overlap {overlap}, "
             f"tool {[topo.surface_kind(f) for f in topo.faces(tool)][:3]}")
    return None


def _union(pieces, one=True):
    """One solid (or with one=False, any number) from overlapping pieces: all at once, or piece
    by piece (checked) when the one-shot union fails."""
    if len(pieces) == 1:
        return pieces[0]
    fu = BRepAlgoAPI_Fuse()
    fu.SetArguments(_shapes_list(pieces[:1]))
    fu.SetTools(_shapes_list(pieces[1:]))
    fu.SetFuzzyValue(FUZZY)
    fu.SetNonDestructive(True)
    fu.SetRunParallel(True)
    fu.Build()
    if fu.IsDone() and (len(topo.solids(fu.Shape())) == 1 or not one) and topo.is_valid(fu.Shape()):
        want = None if one else sum(_gvol(x) for x in pieces)
        if one or _gvol(fu.Shape()) <= want * 1.0001:
            return fu.Shape()
    _dbg("one-shot union failed; going piece by piece")
    acc, todo = pieces[0], list(pieces[1:])
    while todo:
        left = []
        for pc in todo:
            got = _fuse_one(acc, pc)
            if got is None:
                left.append(pc)              # try again once the rest is in: the context differs
            else:
                acc = got
        if len(left) == len(todo):
            return None
        todo = left
    return acc if len(topo.solids(acc)) == 1 or not one else None


def _fuse_one(a, b):
    common = _bop(BRepAlgoAPI_Common, a, b)
    overlap = _gvol(common.Shape()) if common.IsDone() else None
    want = _gvol(a) + _gvol(b) - (overlap or 0.0)
    for fz in FUZZIES:
        fu = BRepAlgoAPI_Fuse()
        fu.SetArguments(_shapes_list([a]))
        fu.SetTools(_shapes_list([b]))
        fu.SetFuzzyValue(fz)
        fu.SetNonDestructive(True)
        fu.Build()
        if not fu.IsDone():
            continue
        r = fu.Shape()
        if overlap is not None and _close(_gvol(r), want, want) and topo.is_valid(r):
            return r
        _dump("fail_acc", a)
        _dump("fail_piece", b)
        _dbg(f"fusing a piece went wrong (fuzzy {fz}): {_gvol(r):.2f} vs {want:.2f}; piece "
             f"{sorted(set(topo.surface_kind(f) for f in topo.faces(b)))} {_gvol(b):.2f} valid {topo.is_valid(b)}")
    return None


def _ball(p, r):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    return BRepPrimAPI_MakeSphere(gp_Pnt(*p), r).Shape()


def sweep_shell(shape, t, open_faces=(), direction="inside"):
    """The hollowed solid, or None. Raises nothing for kernel trouble; returns None."""
    t0 = time.perf_counter()
    inside = direction == "inside"
    sign = -1 if inside else 1
    faces = topo.faces(shape)
    is_open = [any(f.IsSame(o) for o in open_faces) for f in faces]
    idx = topo.ShapeDict()
    for i, f in enumerate(faces):
        idx[f] = i
    efmap = topo.edge_face_map(shape)
    smooth, tubes = [], []
    fading = topo.ShapeSet()      # creases that fade out somewhere: the neighbouring slabs nearly coincide there
    for e in topo.edges(shape):
        if BRep_Tool.Degenerated_s(e):
            continue
        fs = efmap.get(e, [])
        if len(fs) != 2 or fs[0].IsSame(fs[1]):
            continue
        i, j = idx[fs[0]], idx[fs[1]]
        if is_open[i] and is_open[j]:
            continue
        try:
            angs = _crease(e, fs)
        except Exception:
            angs = [90.0, -90.0]              # unknown: treat as needing a tube
        if not is_open[i] and not is_open[j]:
            if max(abs(a) for a in angs) < SMOOTH_DEG:
                smooth.append((i, j))
                continue
            # neighbouring slabs splay apart at inside corners (inside shell) or outside
            # corners (outside shell), and nearly coincide where a crease fades out
            if (min(angs) < NEAR_DEG) if inside else (max(angs) > -NEAR_DEG):
                tubes.append(e)
                if min(abs(x) for x in angs) < NEAR_DEG:
                    fading.add(e)
        else:
            # the rim of an opening: the kept face's slab ends square to it; where the face
            # leans so the opening's face isn't square to it, a tube fills the corner
            if inside and min(angs) < 88.0:
                tubes.append(e)
    groups = _patches(faces, smooth)
    groups = [[i for i in g if not is_open[i]] for g in groups]
    groups = [g for g in groups if g]
    tube_solids = []
    for e in tubes:
        tb = _tube(e, TUBE * t)
        if tb is None:
            _dbg("tube failed", topo.edge_kind(e))
            return None
        tube_solids.append(tb)
    skip_v = topo.ShapeSet()
    if not inside:                            # an outside shell's opening stays flush
        for i, f in enumerate(faces):
            if is_open[i]:
                for v in topo.vertices(f):
                    skip_v.add(v)
    balls = topo.ShapeDict()
    for e in tubes:
        for v in topo.vertices(e):
            if v not in skip_v and v not in balls:
                balls[v] = _ball(topo.to_np(BRep_Tool.Pnt_s(v)), BALL * t)
    # where a crease fades out, the slabs on either side have walls that nearly coincide, which
    # booleans can't sort out; there each slab is cut back by the tube on that edge, which covers
    # the ground anyway, so the slab meets the tube on exactly the tube's own surface
    pieces = []
    for g in groups:
        s = _slab([faces[i] for i in g], t, sign)
        if s is None:
            _dbg(f"slab failed for faces {g} ({[topo.surface_kind(faces[i]) for i in g]})")
            return None
        mine = topo.ShapeSet()
        for i in g:
            for e in topo.edges(faces[i]):
                mine.add(e)
        tools = [tb for e, tb in zip(tubes, tube_solids) if e in mine and e in fading]
        _ts = time.perf_counter()
        for tool in tools:
            _tc = time.perf_counter()
            cut = _cut_one(s, tool)
            _dbg(f"  slab {g} - {topo.surface_kind(topo.faces(tool)[0])}: {time.perf_counter() - _tc:.2f}s")
            if cut is None:
                _dbg(f"could not trim slab {g} by a {topo.surface_kind(topo.faces(tool)[0])} piece; left as is")
            else:
                s = cut
        _dbg(f"slab {g} ({[topo.surface_kind(faces[i]) for i in g]}): {len(tools)} trims, {time.perf_counter() - _ts:.2f}s")
        pieces.append(s)
    n_slabs = len(pieces)
    pieces += tube_solids + [bl for _, bl in balls.items()]
    n_balls = len(balls)
    _dbg(f"{n_slabs} slabs, {len(tubes)} tubes, {n_balls} balls in {time.perf_counter() - t0:.2f}s")
    t1 = time.perf_counter()
    u = _union(pieces)
    if u is None:
        _dbg("union failed")
        return None
    _dump("union", u)
    for k, pc in enumerate(pieces):
        _dump(f"piece{k}", pc)
    _dbg(f"union {time.perf_counter() - t1:.2f}s, {len(topo.solids(u))} solids")
    t2 = time.perf_counter()
    op = BRepAlgoAPI_Common() if inside else BRepAlgoAPI_Cut()
    op.SetArguments(_shapes_list([shape] if inside else [u]))
    op.SetTools(_shapes_list([u] if inside else [shape]))
    op.SetFuzzyValue(FUZZY)
    op.SetNonDestructive(True)
    op.SetRunParallel(True)
    op.Build()
    if not op.IsDone():
        _dbg("final boolean failed")
        return None
    res = op.Shape()
    _dump("final", res)
    sols = topo.solids(res)
    _dbg(f"final {time.perf_counter() - t2:.2f}s, {len(sols)} solids, valid {topo.is_valid(res)}")
    if len(sols) != 1:
        return None
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    us = ShapeUpgrade_UnifySameDomain(sols[0], True, True, True)
    us.Build()
    out = topo.solids(us.Shape())
    out = out[0] if len(out) == 1 and topo.is_valid(out[0]) else sols[0]
    if not topo.is_valid(out):
        return None
    _dbg(f"total {time.perf_counter() - t0:.2f}s")
    return out
