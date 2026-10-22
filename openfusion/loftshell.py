"""The inside of a loft's wall as an ordinary loft.

OpenCASCADE offsets a free-form face as an 'offset surface', which booleans intersect slowly
and unreliably. A two-section loft's side is ruled (straight lines from one section to the
other), and its offset by the wall thickness is close to a loft too: at a series of planes
parallel to the sections, find exactly where the offset surface crosses each plane (a few
Newton steps per point), run a curve through those points with the same parameters the loft's
own sections used, and loft through the curves. The result is a plain cubic B-spline that
booleans handle as easily as the loft itself, exact at the sample points and within microns
between them.
"""
from __future__ import annotations

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Pnt, gp_Vec

from . import topo


def ruled_loft_face(face):
    """(C0, C1, flip) for a loft side that is one periodic face ruled between two sections:
    points on it are C0(u) + v (C1(u) - C0(u)); flip says the face's outward normal is
    -(dS/du x dS/dv). None for any other face."""
    from OCP.GeomAbs import GeomAbs_BSplineSurface
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_BSplineSurface:
        return None
    s = ad.BSpline()
    if not s.IsUPeriodic() or s.VDegree() != 1 or s.NbVPoles() != 2:
        return None
    v0, v1 = s.VKnot(1), s.VKnot(s.NbVKnots())
    u_knots = [s.UKnot(i) for i in range(1, s.NbUKnots() + 1)]
    uu, vv = topo.face_uv_mid(face)
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    s.D1(uu, vv, p, du, dv)
    n = np.cross(topo.to_np(du), topo.to_np(dv))
    flip = float(n @ topo.face_normal_at(face, uu, vv)) < 0
    return s, (v0, v1), u_knots, flip


class _Ruled:
    def __init__(self, s, vr, flip):
        self.s, self.v0, self.v1, self.flip = s, vr[0], vr[1], flip

    def ends(self, u):
        a, b = gp_Pnt(), gp_Pnt()
        self.s.D0(u, self.v0, a)
        self.s.D0(u, self.v1, b)
        return topo.to_np(a), topo.to_np(b)

    def ends_d(self, u):
        a, b, da, db = gp_Pnt(), gp_Pnt(), gp_Vec(), gp_Vec()
        dv = gp_Vec()
        self.s.D1(u, self.v0, a, da, dv)
        self.s.D1(u, self.v1, b, db, dv)
        return topo.to_np(a), topo.to_np(b), topo.to_np(da), topo.to_np(db)

    def point(self, u, w, d):
        """The point at ruling fraction w (0 at the first section, may run past 0..1, the
        loft extended straight) moved d along the outward normal."""
        a, b, da, db = self.ends_d(u)
        p = a + w * (b - a)
        su = da + w * (db - da)
        n = np.cross(su, b - a)
        n = n / np.linalg.norm(n)
        if self.flip:
            n = -n
        return p + d * n


def offset_level_curves(face, t, normal, levels, inward=True):
    """For a ruled loft face: closed curves where the face offset by t (inward: against its
    outward normal) crosses the planes {x : normal . x = h} for h in levels. Returns
    (curves, u_params) or None."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_double, HArray1_gp_Pnt
    got = ruled_loft_face(face)
    if got is None:
        return None
    s, vr, knots, flip = got
    rl = _Ruled(s, vr, flip)
    d = -t if inward else t
    us, period = _samples(knots)
    # the section points and tangents don't depend on the level: evaluate them once, then run
    # Newton for every sample at once
    ends = [rl.ends_d(u) for u in us]
    A = np.array([e[0] for e in ends])
    AB = np.array([e[1] for e in ends]) - A
    DA = np.array([e[2] for e in ends])
    DD = np.array([e[3] for e in ends]) - DA
    span = AB @ normal
    if np.any(np.abs(span) < 1e-12):
        return None
    sgn = -1.0 if flip else 1.0

    def at(w):
        nn = np.cross(DA + w[:, None] * DD, AB)
        nn *= sgn / np.linalg.norm(nn, axis=1)[:, None]
        return A + w[:, None] * AB + d * nn
    curves = []
    for h in levels:
        w = (h - A @ normal) / span            # start where the loft itself crosses the plane
        for _ in range(30):
            f = at(w) @ normal - h
            if np.max(np.abs(f)) < 1e-10:
                break
            e = 1e-6
            df = (at(w + e) @ normal - at(w - e) @ normal) / (2 * e)
            if np.any(np.abs(df) < 1e-12):
                return None
            w = w - f / df
        else:
            return None
        pts = at(w)
        arr = HArray1_gp_Pnt(1, len(pts))
        for i, p in enumerate(pts):
            arr.SetValue(i + 1, gp_Pnt(*p))
        par = HArray1_double(1, len(us) + 1)
        for i, u in enumerate(us):
            par.SetValue(i + 1, u)
        par.SetValue(len(us) + 1, us[0] + period)
        it = GeomAPI_Interpolate(arr, par, True, 1e-9)
        it.Perform()
        if not it.IsDone():
            return None
        curves.append(it.Curve())
    return curves


# ---------------------------------------------------------------- shelling bodies made of lofts
def _dbg(*a):
    import os
    if os.environ.get("OPENFUSION_DEBUG"):
        print("[loftshell]", *a, flush=True)


ANALYTIC = ("plane", "cylinder", "cone", "sphere", "torus")
MARGIN = 0.1        # x wall: how far a chunk's inside runs past a junction plane (must stay < 0.2)


def _section_plane(rl, us, v):
    """(normal, h) of the plane a ruled loft's section at ruling fraction v lies in, or None."""
    pts = np.array([rl.point(u, v, 0.0) for u in us])
    c = pts.mean(axis=0)
    _, sv, vt = np.linalg.svd(pts - c)
    n = vt[-1]
    if sv[-1] / max(1.0, len(pts) ** 0.5) > 1e-6:
        return None
    return n / np.linalg.norm(n), float(n @ c) / np.linalg.norm(n)


def _samples(knots, n_min=128):
    """Parameters to sample a periodic loft at: its own knots (which carry the sections'
    corners), filled in evenly where they are sparse (a circle has only a few)."""
    k0, k1 = knots[0], knots[-1]
    us = set(knots[:-1])
    us.update(k0 + (k1 - k0) * i / n_min for i in range(n_min))
    us = sorted(us)
    out = [us[0]]
    for u in us[1:]:
        if u - out[-1] > 1e-9 * (k1 - k0):
            out.append(u)
    return out, k1 - k0


def loft_shell(shape, t, open_faces=()):
    """Inside shell of a body made of smooth two-section lofts (one ruled periodic side each,
    between parallel flat sections) and flat or simple round faces. Returns the wall solid,
    or None when the body isn't of that kind or a step fails."""
    from .sweepshell import TUBE, BALL, _ball, _cut_one, _gvol, _tube, _union, remember_volume
    import time
    t_start = time.perf_counter()
    faces = topo.faces(shape)
    is_open = lambda f: any(f.IsSame(o) for o in open_faces)        # noqa: E731
    lofts = []
    for f in faces:
        k = topo.surface_kind(f)
        if k in ANALYTIC:
            continue
        got = ruled_loft_face(f)
        if got is None:
            return None                              # some other free-form face
        if is_open(f):
            return None
        lofts.append((f, got))
    if not lofts:
        return None
    efmap = topo.edge_face_map(shape)
    planes, info = [], []
    for f, (s, vr, knots, flip) in lofts:
        rl = _Ruled(s, vr, flip)
        us, period = _samples(knots)
        p0 = _section_plane(rl, us, 0.0)
        p1 = _section_plane(rl, us, 1.0)
        if p0 is None or p1 is None or abs(abs(float(p0[0] @ p1[0])) - 1) > 1e-9:
            _dbg("sections not flat and parallel")
            return None
        n = p0[0]
        a, b = rl.ends(us[0])
        if float(n @ (b - a)) < 0:
            n = -n
        h = sorted([float(n @ rl.point(us[0], 0.0, 0.0)), float(n @ rl.point(us[0], 1.0, 0.0))])
        # what each end meets: a flat face in the section's plane (a cap: closed or open), or the
        # sides of another body (a junction, where the body gets cut)
        ends = []
        for hk in h:
            kinds = set()
            for e in topo.edges(f):
                pe = topo.edge_midpoint(e)
                if abs(float(n @ pe) - hk) > 1e-5:
                    continue
                for g in efmap.get(e, []):
                    if g.IsSame(f):
                        continue
                    if topo.surface_kind(g) == "plane" and abs(abs(float(topo.plane_normal(g) @ n)) - 1) < 1e-9 \
                            and abs(float(n @ topo.face_area_centroid(g)[1]) - hk) < 1e-5:
                        kinds.add("open" if is_open(g) else "cap")
                    else:
                        kinds.add("junction")
            if len(kinds) != 1:
                _dbg("loft end meets", kinds)
                return None
            ends.append(kinds.pop())
        info.append((f, rl, n, h, ends))
        for hk, kind in zip(h, ends):
            if kind == "junction" and not any(abs(abs(float(n @ m)) - 1) < 1e-9 and abs(float(m @ n) * hk - hm) < 1e-6
                                              for m, hm in planes):
                planes.append((n, hk))
    # cut the body at the junction planes
    chunks, cut_faces = _split(shape, planes) if planes else ([shape], topo.ShapeSet())
    if chunks is None:
        _dbg("split failed")
        return None
    _dbg(f"{len(lofts)} lofts, {len(planes)} junction planes, {len(chunks)} chunks ({time.perf_counter() - t_start:.2f}s)")
    opened = topo.ShapeSet()
    for o in open_faces:
        opened.add(o)
    m = MARGIN * t
    cavities, plugs = [], []
    for c in chunks:
        cf = topo.faces(c)
        mine = [x for x in info if any(g.IsSame(x[0]) or _same_surface(g, x[0]) for g in cf)]
        if mine:
            f, rl, n, h, ends = mine[0]
            if len(mine) > 1 or any(topo.surface_kind(g) != "plane" for g in cf if not _same_surface(g, f)):
                _dbg("a loft chunk holds more than the loft")
                return None
            if "open" in ends:
                _dbg("openings on a loft's end aren't handled here")
                return None
            lo = h[0] + (-m if ends[0] == "junction" else t)
            hi = h[1] + (m if ends[1] == "junction" else -t)
            if hi - lo < 1e-6:
                return None
            cav = _loft_cavity_range(f, t, n, lo, hi)
            if cav is None:
                _dbg("loft cavity failed")
                return None
            cavities.append(cav)
            continue
        cav = _chunk_inside(c, cut_faces, opened, t, m, push_open=False)
        if cav is None:
            _dbg("a chunk's inside failed")
            return None
        cavities.append(cav)
        if any(_opened(g, opened) for g in cf):
            # what to cut away under an opening: the inside as it runs out through the opening,
            # less the closed inside
            pushed = _chunk_inside(c, cut_faces, opened, t, m, push_open=True)
            if pushed is None:
                _dbg("a chunk's opening failed")
                return None
            plug = _cut_one(pushed, cav)
            if plug is None:
                return None
            plugs.append(plug)
    _dbg(f"insides built ({time.perf_counter() - t_start:.2f}s)")
    # where chunks meet, their insides nearly coincide next to the wall; a tube along the
    # junction (the wall's ground anyway) cuts both back so they only touch its surface
    if planes:
        tubes, ball_at = [], topo.ShapeSet()
        for f, rl, n, h, ends in info:
            for hk, kind in zip(h, ends):
                if kind != "junction":
                    continue
                for e in topo.edges(f):
                    if abs(float(n @ topo.edge_midpoint(e)) - hk) < 1e-5:
                        pieces = _plain_tube(e, n, TUBE * t)
                        if pieces is None:
                            tb = _tube(e, TUBE * t)
                            if tb is None:
                                _dbg("junction tube failed")
                                return None
                            pieces = [tb]
                        tubes += pieces
                        vs = topo.vertices(e)
                        if len(vs) == 2 and not vs[0].IsSame(vs[1]):
                            for v in vs:
                                ball_at.add(v)
        from OCP.BRep import BRep_Tool
        tubes += [_ball(topo.to_np(BRep_Tool.Pnt_s(v)), BALL * t) for v, _ in ball_at.items()]
        ring = _union(tubes, one=False)      # simple shapes: quick; one ring per junction
        if ring is None:
            _dbg("joining the junction tubes failed")
            return None
        _dbg(f"junction tubes ({len(tubes)} pieces) {time.perf_counter() - t_start:.2f}s")
        rings = topo.solids(ring)
        trimmed = []
        for cav in cavities:
            got = cav
            for rg in rings:
                if not _boxes_meet(got, rg):
                    continue
                cut = _cut_crossing(got, rg)
                if cut is None:
                    cut = _cut_one(got, rg)          # the fully checked way
                if cut is None:
                    _dbg("trimming an inside by a junction tube failed")
                    return None
                got = cut
            trimmed.append(got)
        cavities = trimmed
        _dbg(f"insides trimmed ({time.perf_counter() - t_start:.2f}s)")
    inside = _union(cavities)
    if inside is None:
        _dbg("joining the insides failed")
        return None
    _dbg(f"insides joined ({time.perf_counter() - t_start:.2f}s)")
    # The inside lies wholly inside the body, so the wall is the body's skin with the inside's
    # skin (turned inside out) as a void: no boolean between the loft's surface and its
    # parallel inner surface, which booleans get subtly wrong at some wall thicknesses.
    wall = _with_void(shape, inside)
    if wall is None:
        _dbg("the inside touches the skin")
        return None
    if plugs:
        for pl in plugs:
            wall = _cut_one(wall, pl)
            if wall is None:
                _dbg("cutting an opening failed")
                return None
        sols = topo.solids(wall)
        if len(sols) != 1 or not topo.is_valid(sols[0]):
            _dbg("openings left", len(sols), "solids")
            return None
        wall = sols[0]
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    v_wall = _gvol(wall)                     # known already: the void's or the last cut's
    us = ShapeUpgrade_UnifySameDomain(wall, True, True, True)
    us.Build()
    out = topo.solids(us.Shape())
    if len(out) == 1 and topo.is_valid(out[0]):
        wall = out[0]
        remember_volume(wall, v_wall)
    _dbg(f"done ({time.perf_counter() - t_start:.2f}s)")
    return wall


def _loft_cavity_range(face, t, normal, lo, hi, n_levels=9):
    """The solid inside the loft face's offset wall between the planes normal.x = lo and hi:
    a loft through offset level curves, with flat ends."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    curves = offset_level_curves(face, t, normal, list(np.linspace(lo, hi, n_levels)))
    if curves is None:
        return None
    mk = BRepOffsetAPI_ThruSections(True, False, 1e-6)
    mk.CheckCompatibility(False)
    mk.SetMaxDegree(3)                       # cubic across the levels too: lighter for booleans
    for c in curves:
        mk.AddWire(BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(c).Edge()).Wire())
    mk.Build()
    if not mk.IsDone():
        return None
    sols = topo.solids(mk.Shape())
    return sols[0] if len(sols) == 1 and topo.is_valid(sols[0]) else None


def _same_surface(g, f):
    from OCP.BRep import BRep_Tool
    return BRep_Tool.Surface_s(g) == BRep_Tool.Surface_s(f) if topo.surface_kind(g) == topo.surface_kind(f) else False


def _opened(g, opened):
    if g in opened:
        return True
    # the splitter may hand back a copy of an open face: same surface and same place
    from OCP.BRep import BRep_Tool
    for o, _ in opened.items():
        if BRep_Tool.Surface_s(o) == BRep_Tool.Surface_s(g):
            return True
    return False


def _split(shape, planes):
    """Cut a solid with planes: (chunks, the faces two chunks share), or (None, None)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln
    from .sweepshell import _shapes_list
    lo, hi = topo.bbox(shape)
    size = float(np.linalg.norm(hi - lo)) * 2 + 10
    tools = []
    for n, h in planes:
        x = np.cross(n, (1, 0, 0) if abs(n[0]) < 0.9 else (0, 1, 0))
        pln = gp_Pln(gp_Ax3(gp_Pnt(*(n * h)), gp_Dir(*n), gp_Dir(*x)))
        tools.append(BRepBuilderAPI_MakeFace(pln, -size, size, -size, size).Face())
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_shapes_list([shape]))
    sp.SetTools(_shapes_list(tools))
    sp.SetFuzzyValue(1e-5)
    sp.SetNonDestructive(True)
    sp.Build()
    if not sp.IsDone():
        return None, None
    chunks = topo.solids(sp.Shape())
    seen, shared = topo.ShapeDict(), topo.ShapeSet()
    for c in chunks:
        for f in topo.faces(c):
            if f in seen and not seen.get(f) is c:
                shared.add(f)
            seen[f] = c
    return chunks, shared


def _plain_tube(e, n, r):
    """A tube around a flat closed edge that is really lines and arcs (a loft's section curve
    following a circle, slot or rounded rectangle), built from cylinders, torus pieces and balls
    at the joints: shapes booleans handle quickly and reliably, unlike a tube swept along a
    curve of hundreds of control points. None if the edge isn't lines and arcs."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeTorus
    from OCP.gp import gp_Ax2, gp_Dir
    from .sweepshell import _ball
    ad = BRepAdaptor_Curve(e)
    u0, u1 = ad.FirstParameter(), ad.LastParameter()
    closed = bool(np.linalg.norm(topo.to_np(ad.Value(u0)) - topo.to_np(ad.Value(u1))) < 1e-7)
    N = 1500
    us = np.linspace(u0, u1, N, endpoint=not closed)
    pts, kap = [], []
    for u in us:
        p, d1, d2 = gp_Pnt(), gp_Vec(), gp_Vec()
        ad.D2(u, p, d1, d2)
        a, b = topo.to_np(d1), topo.to_np(d2)
        pts.append(topo.to_np(p))
        kap.append(float(np.linalg.norm(np.cross(a, b)) / max(np.linalg.norm(a) ** 3, 1e-30)))
    pts, kap = np.array(pts), np.array(kap)
    # one circle?
    c, R = _fit_circle(pts, n) if closed else (None, None)
    if c is not None and np.max(np.abs(np.linalg.norm(pts - c - np.outer((pts - c) @ n, n), axis=1) - R)) < 1e-4:
        x = pts[0] - c
        x = x - (x @ n) * n
        return [BRepPrimAPI_MakeTorus(gp_Ax2(gp_Pnt(*c), gp_Dir(*n), gp_Dir(*(x / np.linalg.norm(x)))), R, r + 1e-4).Shape()]
    # runs of (nearly) zero curvature are lines, runs of steady curvature are arcs
    kmax = float(kap.max())
    straight = kap < 1e-3 * kmax if kmax > 1e-9 else np.ones(N, bool)
    labels = []
    for i in range(N):
        labels.append("L" if straight[i] else "A")
    runs, start = [], 0
    for i in range(1, N + 1):
        if i == N or labels[i] != labels[start]:
            runs.append((labels[start], start, i))
            start = i if i < N else start
    # merge a wrap-around run with the first one
    if closed and len(runs) > 1 and runs[0][0] == runs[-1][0]:
        lab, s0, s1 = runs.pop()
        runs[0] = (lab, s0 - N, runs[0][2])

    # a curved run that one circle doesn't fit (two arcs of different radii meeting) is split
    # where its curvature jumps most, until every piece fits
    def fits(s0, s1):
        q = pts[[i % N for i in range(s0, s1)]]
        cc, RR = _fit_circle(q, n)
        return cc is not None and float(np.max(np.abs(np.linalg.norm(q - cc - np.outer((q - cc) @ n, n), axis=1) - RR))) < 1e-4
    out, todo = [], list(runs)
    while todo:
        lab, s0, s1 = todo.pop(0)
        if lab == "A" and s1 - s0 > 8 and not fits(s0, s1):
            k = [i % N for i in range(s0, s1)]
            jump = np.abs(np.diff(kap[k]))[2:-2]
            m = s0 + 3 + int(np.argmax(jump)) if len(jump) else (s0 + s1) // 2
            todo[:0] = [(lab, s0, m), (lab, m, s1)]
        else:
            out.append((lab, s0, s1))
    runs = out
    pieces, err = [], 0.0
    joints = []
    for lab, s0, s1 in runs:
        idx = [i % N for i in range(s0, s1)]
        q = pts[idx]
        if len(q) < 3:
            continue                      # a blur at a joint: the balls and neighbours cover it
        joints += [q[0], q[-1]]
        if lab == "L":
            a, b = q[0], q[-1]
            d = b - a
            L = float(np.linalg.norm(d))
            if L < 1e-9:
                continue
            dd = d / L
            off = q - a - np.outer((q - a) @ dd, dd)
            err = max(err, float(np.max(np.linalg.norm(off, axis=1))))
            pieces.append(("L", a, dd, L))
        else:
            cc, RR = _fit_circle(q, n)
            if cc is None:
                return None
            rad = np.linalg.norm(q - cc - np.outer((q - cc) @ n, n), axis=1)
            err = max(err, float(np.max(np.abs(rad - RR))))
            x = q[0] - cc
            x = x - (x @ n) * n
            x = x / np.linalg.norm(x)
            y = np.cross(n, x)
            ang = [np.arctan2(float((p - cc) @ y), float((p - cc) @ x)) for p in q]
            ang = np.unwrap(ang)
            pieces.append(("A", cc, x, y, RR, float(ang[0]), float(ang[-1])))
    if err > 2e-3 or not pieces:
        return None
    rr = r + err
    out = []
    for pc in pieces:
        if pc[0] == "L":
            _, a, dd, L = pc
            # neighbours along a smooth outline are tangent where they meet, which booleans handle
            # worst: stop each piece a little short; the ball at the joint bridges the gap
            g = min(0.1 * rr, 0.25 * L)
            out.append(BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*(a + g * dd)), gp_Dir(*dd)), rr, L - 2 * g).Shape())
        else:
            _, cc, x, y, RR, a0, a1 = pc
            if a1 < a0:
                a0, a1 = a1, a0
            g = min(0.1 * rr / RR, 0.25 * (a1 - a0))
            a0, a1 = a0 + g, a1 - g
            xs = np.cos(a0) * x + np.sin(a0) * y
            nn = np.cross(x, y)
            out.append(BRepPrimAPI_MakeTorus(gp_Ax2(gp_Pnt(*cc), gp_Dir(*nn), gp_Dir(*xs)), RR, rr, a1 - a0).Shape())
    # one ball per joint between runs; an open edge's own ends get theirs from the caller
    ends = [] if closed else [pts[0], topo.to_np(ad.Value(u1))]
    uniq = []
    for p in joints:
        if all(np.linalg.norm(p - q) > 0.5 * rr for q in uniq + ends):
            uniq.append(p)
    out += [_ball(p, rr * 1.02) for p in uniq]
    return out


def _fit_circle(pts, n):
    """Centre and radius of the circle in the plane with normal n best through pts."""
    c0 = pts.mean(axis=0)
    x = pts[0] - c0
    x = x - (x @ n) * n
    if np.linalg.norm(x) < 1e-12:
        return None, None
    x = x / np.linalg.norm(x)
    y = np.cross(n, x)
    P = np.stack([(pts - c0) @ x, (pts - c0) @ y], axis=1)
    A = np.column_stack([2 * P, np.ones(len(P))])
    bb = (P ** 2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, bb, rcond=None)
    cx, cy, k = sol
    R2 = k + cx * cx + cy * cy
    if R2 <= 0:
        return None, None
    c = c0 + cx * x + cy * y + ((pts - c0) @ n).mean() * n
    return c, float(np.sqrt(R2))


def _with_void(shape, inside):
    """shape with `inside` as an internal void, or None if inside isn't strictly inside it."""
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopoDS import TopoDS_Solid
    inside_of = _ray_tester(shape)
    for v in topo.vertices(inside):
        if not inside_of(topo.to_np(BRep_Tool.Pnt_s(v))):
            _dbg("inside's corner outside the body at", topo.to_np(BRep_Tool.Pnt_s(v)).round(3))
            return None
    for f in topo.faces(inside):
        p, _ = topo.face_point_normal_mid(f)
        if not inside_of(p):
            _dbg("inside's face outside the body at", p.round(3), topo.surface_kind(f))
            return None
    outer = topo._explore(shape, TopAbs_SHELL, topo._cast("Shell"))
    inner = topo._explore(inside, TopAbs_SHELL, topo._cast("Shell"))
    if len(outer) != 1 or len(inner) != 1:
        return None
    sol = TopoDS_Solid()
    bb = BRep_Builder()
    bb.MakeSolid(sol)
    bb.Add(sol, outer[0])
    bb.Add(sol, inner[0].Reversed())
    from .sweepshell import _gvol, remember_volume
    if not topo.is_valid(sol):
        return None
    remember_volume(sol, _gvol(shape) - _gvol(inside))      # a skin with a void: exactly the difference
    return sol


def _chunk_inside(c, cut_faces, opened, t, m, push_open):
    """The inside of a chunk of flat and simple round faces: its faces moved in by t, faces
    where it was cut from the body moved out by m (past the junction), openings moved out by t
    when push_open. OpenCASCADE's offset first; checked to keep its distance from the chunk's
    faces (it can go wrong where two roundings meet in a full round); else by sweeping."""
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection
    from .sweepshell import _gvol
    cf = topo.faces(c)
    plain = [g for g in cf if g not in cut_faces and not (push_open and _opened(g, opened))]
    for join in (GeomAbs_Intersection, GeomAbs_Arc):
        mk = BRepOffset_MakeOffset()
        try:
            mk.Initialize(c, -t, 1e-4, BRepOffset_Skin, False, False, join, False, False)
            for g in cf:
                if g in cut_faces:
                    mk.SetOffsetOnFace(g, m)
                elif push_open and _opened(g, opened):
                    mk.SetOffsetOnFace(g, t)
            mk.MakeOffsetShape()
        except Exception:
            continue
        if not mk.IsDone():
            continue
        sols = topo.solids(mk.Shape())
        if len(sols) == 1 and topo.is_valid(sols[0]) and 0 < _gvol(sols[0]) < _gvol(c) and \
                _keeps_off(sols[0], plain, 0.9 * t):
            return sols[0]
    _dbg("chunk offset unusable; sweeping instead")
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from .sweepshell import _bop, _union, sweep_shell
    holes = [g for g in cf if g in cut_faces or (push_open and _opened(g, opened))]
    wall = sweep_shell(c, t, holes)
    if wall is None:
        return None
    cu = _bop(BRepAlgoAPI_Cut, c, wall)
    sols = [x for x in topo.solids(cu.Shape()) if _gvol(x) > 1e-6 * _gvol(c)]
    if len(sols) != 1:
        return None
    cav = sols[0]
    # carry the inside on past the cut faces (m) and out through openings (t)
    ext = []
    for g in topo.faces(cav):
        if topo.surface_kind(g) != "plane":
            continue
        for h in holes:
            if topo.surface_kind(h) == "plane" and abs(abs(float(topo.plane_normal(g) @ topo.plane_normal(h))) - 1) < 1e-9 \
                    and abs(float(topo.plane_normal(h) @ (topo.face_area_centroid(g)[1] - topo.face_area_centroid(h)[1]))) < 1e-6:
                d = m if h in cut_faces else t
                ext.append(BRepPrimAPI_MakePrism(g, gp_Vec(*(topo.face_outward_normal(h) * d))).Shape())
                break
    return _union([cav] + ext) if ext else cav


def _keeps_off(solid, faces, d):
    """True when solid stays at least d from every face."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    for g in faces:
        ex = BRepExtrema_DistShapeShape(solid, g)
        if not ex.IsDone() or ex.Value() < d:
            return False
    return True


def _ray_tester(shape):
    """A point-in-solid test by counting crossings along three rays (majority): the kernel's
    own classifier can answer 'on the surface' for points a millimetre inside a large
    free-form face."""
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.gp import gp_Dir, gp_Lin
    ray = IntCurvesFace_ShapeIntersector()
    ray.Load(shape, 1e-7)
    dirs = [np.array(d, float) / np.linalg.norm(d) for d in ((0.13, 0.27, 0.95), (0.91, -0.33, 0.24), (-0.4, 0.88, -0.27))]

    def test(p):
        votes = 0
        for d in dirs:
            ray.Perform(gp_Lin(gp_Pnt(*p), gp_Dir(*d)), 0, 1e9)
            votes += ray.NbPnt() % 2
        return votes >= 2
    return test


def _boxes_meet(a, b):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    x, y = Bnd_Box(), Bnd_Box()
    BRepBndLib.Add_s(a, x)
    BRepBndLib.Add_s(b, y)
    return not x.IsOut(y)


def _cut_crossing(s, tool):
    """s - tool where tool is known to cross s (a junction ring always crosses the insides it
    meets): checked by the volume going down, by no more than the tool's own volume. Saves
    the extra boolean the general check needs; None if the check fails."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from .sweepshell import FUZZY, _gvol, _shapes_list
    cu = BRepAlgoAPI_Cut()
    cu.SetArguments(_shapes_list([s]))
    cu.SetTools(_shapes_list([tool]))
    cu.SetFuzzyValue(FUZZY)
    cu.SetNonDestructive(True)
    cu.SetUseOBB(True)
    cu.SetRunParallel(True)
    cu.Build()
    if not cu.IsDone():
        return None
    r = cu.Shape()
    sols = topo.solids(r)
    if len(sols) != 1 or not topo.is_valid(sols[0]):
        return None
    removed = _gvol(s) - _gvol(sols[0])
    return sols[0] if 1e-9 < removed <= _gvol(tool) * 1.001 else None
