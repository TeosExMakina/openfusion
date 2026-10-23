"""References: lazy, named pointers to geometry.

Scripts never hold geometry directly. `plate.anchor("+Z")` returns an
AnchorRef that is resolved when the feature using it runs, against the
model as it exists at that point in the timeline. That is what makes
re-running with new parameters safe.

Fallback chain (design doc, Reference resolution): a face point or face
anchor whose face no longer exists falls back to the body's design anchor
of the same name, then to the body origin. Every fallback is reported as a
warning; `.strict()` turns it into an error instead.
"""
from __future__ import annotations

import copy as _copy
import re
import math

import numpy as np

from . import topo
from .body import Body, NameError_
from .geom import Frame


class DependencyError(Exception):
    """A referenced body does not exist (yet) or failed upstream."""


class RefError(Exception):
    """A name could not be resolved and no fallback applies."""


def sig(x) -> str:
    """Stable textual signature used for cache keys."""
    if isinstance(x, (Handle, GroupHandle, Ref)):
        return x.sig()
    if isinstance(x, float):
        return repr(round(x, 10))
    if isinstance(x, (list, tuple)):
        return "[" + ",".join(sig(v) for v in x) + "]"
    if isinstance(x, dict):
        return "{" + ",".join(f"{k}:{sig(v)}" for k, v in sorted(x.items())) + "}"
    if isinstance(x, Frame):
        return repr(x)
    return repr(x)


# ===================================================================== handles
class Handle:
    """What a script gets back from an operation.

    For operations that create a body, the handle names that body. For
    operations that modify a body (combine, fillet...), the handle still
    points at the modified body, so `join1.anchor("+Z")` == `plate.anchor("+Z")`.
    """

    def __init__(self, feature, body_feature=None, index=None):
        self._feature = feature
        self._body_feature = body_feature or feature
        self._index = index

    @property
    def body_id(self) -> str:
        base = self._body_feature
        if isinstance(base, Handle):
            return base.body_id
        name = base.name
        if self._index:
            return f"{name}_{self._index}"
        return name

    def sig(self):
        return f"<{self.body_id}>"

    def __repr__(self):
        return f"<body {self.body_id}>" if self._feature.name else "<body (unnamed)>"

    # -- references -------------------------------------------------------
    def anchor(self, name: str = "origin", **params) -> "AnchorRef":
        return AnchorRef(self, name, params)

    def face(self, name: str) -> "FaceRef":
        hint = self._feature if self._feature is not self._body_feature else None
        return FaceRef(self, name, hint)

    def edge(self, name: str) -> "EdgeRef":
        return EdgeRef(self, name)

    def edges(self, face: str | None = None, kind: str = "all") -> "Ref":
        if face is None:
            return AllEdgesRef(self)
        return FaceEdgesRef(self.face(face), kind)

    def vertex(self, name: str) -> "VertexRef":
        return VertexRef(self, name)


class GroupHandle:
    """Result of a pattern: the seed plus its instances. Iterable and indexable."""

    def __init__(self, feature, seed: Handle):
        self._feature = feature
        self._seed = seed

    def members(self, ctx) -> list[str]:
        return ctx.groups[self._feature.name]

    def __getitem__(self, i: int) -> Handle:
        if i == 0:
            return self._seed
        return Handle(self._feature, self._feature, index=i)

    def sig(self):
        return f"<group {self._feature.name}>"

    def __repr__(self):
        return f"<group {self._feature.name}>"


# ===================================================================== refs
class Ref:
    strict_ = False

    def strict(self):
        c = _copy.copy(self)
        c.strict_ = True
        return c


class FrameRef(Ref):
    """Anything that resolves to a Frame. Supports offset/spin/flip modifiers."""

    def __init__(self):
        self._ofs = (0.0, 0.0, 0.0)
        self._spin = 0.0
        self._flip = False

    def offset(self, x=0.0, y=0.0, z=0.0):
        c = _copy.copy(self)
        c._ofs = (self._ofs[0] + x, self._ofs[1] + y, self._ofs[2] + z)
        return c

    def spin(self, angle):
        c = _copy.copy(self)
        c._spin = self._spin + angle
        return c

    def flipped(self):
        c = _copy.copy(self)
        c._flip = not self._flip
        return c

    def _mods_sig(self):
        return f"o{sig(self._ofs)}s{sig(self._spin)}f{int(self._flip)}"

    def frame(self, ctx) -> Frame:
        fr = self._base(ctx)
        fr = fr.offset(*self._ofs)
        if self._spin:
            fr = fr.spin(self._spin)
        if self._flip:
            fr = fr.flipped()
        return fr

    def owner(self):
        return None

    def describe(self) -> str:
        return repr(self)


class AnchorRef(FrameRef):
    def __init__(self, handle, name, params):
        super().__init__()
        self.handle, self.name, self.params = handle, name, dict(params)

    def owner(self):
        return self.handle

    def sig(self):
        return f"A({sig(self.handle)},{self.name!r},{sig(self.params)},{self._mods_sig()})"

    def _base(self, ctx):
        body = ctx.body(self.handle, allow_removed=True)
        try:
            return body.anchor(self.name, **self.params)
        except NameError_ as e:
            raise RefError(f"{self.describe()}: {e}. Available: {', '.join(body.anchor_names())}")

    def __repr__(self):
        p = "".join(f", {k}={v}" for k, v in self.params.items())
        return f'{self.handle.body_id}.anchor("{self.name}"{p})'


WORLD_PLANES = {
    "origin": ((1, 0, 0), (0, 0, 1)), "XY": ((1, 0, 0), (0, 0, 1)), "bed": ((1, 0, 0), (0, 0, 1)),
    "XZ": ((1, 0, 0), (0, -1, 0)), "YZ": ((0, 1, 0), (1, 0, 0)),
}


class WorldRef(FrameRef):
    def __init__(self, name="origin", point=(0, 0, 0)):
        super().__init__()
        if name not in WORLD_PLANES:
            raise ValueError(f"world anchors: {', '.join(WORLD_PLANES)}")
        self.name, self.point = name, tuple(float(c) for c in point)

    def sig(self):
        return f"W({self.name},{sig(self.point)},{self._mods_sig()})"

    def _base(self, ctx):
        x, z = WORLD_PLANES[self.name]
        return Frame.make(self.point, x, z)

    def __repr__(self):
        return f'world.anchor("{self.name}")'


class _World:
    """`world.anchor("XY")`, `world.point(x, y, z)`, `world.bed`."""

    def anchor(self, name="origin"):
        return WorldRef(name)

    def point(self, x=0.0, y=0.0, z=0.0):
        return WorldRef("origin", (x, y, z))

    @property
    def bed(self):
        return WorldRef("bed")

    def __repr__(self):
        return "world"


world = _World()


# ----------------------------------------------------------------- faces
class FaceRef(Ref):
    def __init__(self, handle, name, feature_hint=None):
        self.handle, self.name, self.hint = handle, name, feature_hint

    def sig(self):
        h = self.hint.name if self.hint is not None else ""
        return f"Fc({sig(self.handle)},{self.name!r},{h})"

    def __repr__(self):
        return f'{self.handle.body_id}.face("{self.name}")'

    def resolve(self, ctx) -> tuple[Body, list]:
        """Return (body that contains the faces, faces)."""
        bid = self.handle.body_id
        own = ctx.state.get(bid)
        if own is None or bid in ctx.tainted:
            ctx.body(self.handle)  # raises DependencyError
        # a feature-scoped name, e.g. ext1.face("end") -> faces named by ext1
        # (the faces may have gone into another body: a hole or a sketch's extrude cuts or
        # joins into the body under it)
        if self.hint is not None:
            m = re.match(r"^(.*)#(\d+)$", self.name)
            nm, pc = (m.group(1), int(m.group(2))) if m else (self.name, None)
            others = [o for o in ctx.state.values() if o is not own and o.role != "removed"]
            for cand in ([own] if own is not None else []) + others:
                fs = cand.find_faces(self.hint.name, nm, pc)
                if fs:
                    return cand, fs
        if own is not None and own.role != "removed":
            fs = own.faces_named(self.name)
            if fs:
                return own, fs
        # the body was consumed (Remove semantics): look for its faces elsewhere
        origin, name, piece = (bid, self.name, None)
        if own is not None:
            origin, name, piece = own.parse_face(self.name)
        for other in ctx.state.values():
            if other.id == bid or other.role == "removed":
                continue
            fs = other.find_faces(origin, name, piece)
            if fs:
                return other, fs
        avail = ", ".join(own.face_labels()) if own is not None and own.role != "removed" else "body was removed"
        raise RefError(f'{self!r} not found ({avail})')

    # derived references
    def anchor(self) -> "FaceAnchorRef":
        return FaceAnchorRef(self)

    def corner(self, i: int) -> "FacePointRef":
        return FacePointRef(self, "corner", i)

    def mid(self, i: int) -> "FacePointRef":
        return FacePointRef(self, "mid", i)

    def hole(self, i: int) -> "FacePointRef":
        return FacePointRef(self, "hole", i)

    def arc(self, i: int) -> "FacePointRef":
        return FacePointRef(self, "arc", i)

    def boss(self, i: int) -> "FacePointRef":
        return FacePointRef(self, "boss", i)

    def inner(self, i: int) -> "FacePointRef":
        """A corner of an opening in the face that isn't round (a square hole, a slot)."""
        return FacePointRef(self, "inner", i)

    def edges(self, kind="all") -> "FaceEdgesRef":
        return FaceEdgesRef(self, kind)


def face_anchor_frame(body: Body, faces: list, design_name: str | None) -> Frame:
    """Topology anchor of a (possibly split) face: largest piece's center."""
    face = max(faces, key=lambda f: topo.face_area_centroid(f)[0])
    x_hint = None
    if design_name and body.has_anchor(design_name):
        x_hint = body.anchor(design_name).x
    if topo.surface_kind(face) == "plane":
        _, c = topo.face_area_centroid(face)
        n = topo.plane_normal(face)
        if x_hint is None:
            lines = [e for e in topo.edges(face) if topo.edge_kind(e) == "line"]
            if lines:
                a, b = topo.edge_endpoints(max(lines, key=topo.edge_length))
                x_hint = b - a
            else:
                x_hint = body.frame.x
        return Frame.make(c, x_hint, n)
    p, n = topo.face_point_normal_mid(face)
    return Frame.make(p, x_hint if x_hint is not None else np.cross(body.frame.z, n), n)


def _design_name(body: Body, face_ref: FaceRef) -> str:
    origin, name, piece = body.parse_face(face_ref.name)
    return name


def _fallback(ctx, face_ref: FaceRef, who: str, why: str, strict: bool) -> Frame:
    if strict:
        raise RefError(f"{who}: {why} (strict reference, no fallback)")
    body = ctx.body(face_ref.handle, allow_removed=True)
    name = _design_name(body, face_ref)
    if body.has_anchor(name):
        ctx.warn(f"{who}: {why}; fell back to {body.id}.anchor(\"{name}\")")
        return body.anchor(name)
    ctx.warn(f"{who}: {why}; fell back to {body.id}.anchor(\"origin\")")
    return body.anchor("origin")


class FaceAnchorRef(FrameRef):
    def __init__(self, face_ref: FaceRef):
        super().__init__()
        self.face_ref = face_ref

    def owner(self):
        return self.face_ref.handle

    def sig(self):
        return f"FA({self.face_ref.sig()},{int(self.strict_)},{self._mods_sig()})"

    def __repr__(self):
        return f"{self.face_ref!r}.anchor()"

    def _base(self, ctx):
        try:
            body, faces = self.face_ref.resolve(ctx)
        except RefError as e:
            return _fallback(ctx, self.face_ref, repr(self), str(e), self.strict_)
        return face_anchor_frame(body, faces, _design_name(body, self.face_ref))


# ----------------------------------------------------------------- derived points
MAX_SIMPLE_EDGES = 400        # beyond this (engraved text, imported meshes) a face offers no corners


def face_points(body: Body, face, frame: Frame) -> dict:
    """Named points on a face. On every flat face: its outline's corners, the middles of its
    edges and the centres of its arcs (whatever the outline is made of, notches and cut-outs
    included), and the corners of its non-round openings ("inner"). On any face: the centres
    of circular holes and bosses (round features standing on the face)."""
    out = {"corner": [], "mid": [], "arc": [], "hole": [], "boss": [], "inner": [], "simple": False}
    planar = topo.surface_kind(face) == "plane"
    ow = topo.outer_wire(face)
    efmap = topo.edge_face_map(body.shape)
    inner_loops = []
    # inner circular loops: a hole if the wall beyond it goes into the material
    # (below the face), a boss if it rises above the face
    for w in topo.wires(face):
        if w.IsSame(ow):
            continue
        es = topo.edges(w)
        if not es or not all(topo.edge_kind(e) == "circle" for e in es):
            if es and planar:
                inner_loops.append(w)
            continue
        cs = [topo.circle_center_radius(e) for e in es]
        if not all(np.linalg.norm(c[0] - cs[0][0]) < 1e-6 for c in cs):
            continue
        walls = [g for g in efmap.get(es[0], []) if not g.IsSame(face)]
        side = 0.0
        if walls:
            side = float((topo.face_area_centroid(walls[0])[1] - cs[0][0]) @ frame.z)
        out["boss" if side > 0 else "hole"].append(cs[0][0])
    for k in ("hole", "boss"):
        out[k].sort(key=lambda p: tuple(np.round(frame.to_local(p)[:2], 6)))
    oedges = topo.ordered_wire_edges(ow, face)
    if not planar or len(oedges) > MAX_SIMPLE_EDGES:
        return out
    # corners of openings that aren't round (a square hole, a slot): loop by loop, in order of
    # where the loops are, each loop from its corner nearest angle 0
    loops = []
    for w in inner_loops:
        vs = topo.ordered_wire_vertices(w, face)
        if len(vs) >= 2 and len(vs) <= MAX_SIMPLE_EDGES:
            lc = frame.to_local(np.mean(vs, axis=0))

            def turn(p, lc=lc):
                d = frame.to_local(p) - lc
                a = math.degrees(math.atan2(d[1], d[0])) % 360
                return round(min(a, 360 - a), 6)
            k = min(range(len(vs)), key=lambda i: (turn(vs[i]), i))
            loops.append((tuple(np.round(lc[:2], 6)), vs[k:] + vs[:k]))
    for _, vs in sorted(loops, key=lambda x: x[0]):
        out["inner"] += vs
    verts = topo.ordered_wire_vertices(ow, face)
    n = len(verts)
    if n < 2:
        return out
    # ensure counterclockwise about the outward normal
    area = sum(np.cross(verts[i] - frame.origin, verts[(i + 1) % n] - frame.origin) @ frame.z for i in range(n))
    if area < 0:
        verts = verts[::-1]
        oedges = oedges[::-1]
        verts = [verts[-1]] + verts[:-1]
    # start at the corner nearest angle 0 of the face frame's X
    def ang(p):
        l = frame.to_local(p)
        a = math.degrees(math.atan2(l[1], l[0])) % 360
        return min(a, 360 - a)
    k = min(range(n), key=lambda i: (round(ang(verts[i]), 6), i))
    verts = verts[k:] + verts[:k]
    oedges = oedges[k:] + oedges[:k]
    out["corner"] = verts
    out["mid"] = [topo.edge_midpoint(e) for e in oedges]
    out["arc"] = [topo.circle_center_radius(e)[0] for e in oedges if topo.edge_kind(e) == "circle"]
    out["simple"] = True
    return out


class FacePointRef(FrameRef):
    def __init__(self, face_ref: FaceRef, kind: str, index: int):
        super().__init__()
        self.face_ref, self.kind, self.index = face_ref, kind, int(index)

    def owner(self):
        return self.face_ref.handle

    def sig(self):
        return f"FP({self.face_ref.sig()},{self.kind},{self.index},{int(self.strict_)},{self._mods_sig()})"

    def __repr__(self):
        return f"{self.face_ref!r}.{self.kind}({self.index})"

    def _base(self, ctx):
        who = repr(self)
        try:
            body, faces = self.face_ref.resolve(ctx)
        except RefError as e:
            return _fallback(ctx, self.face_ref, who, str(e), self.strict_)
        if len(faces) > 1:
            ctx.warn(f"{who}: face is split into {len(faces)} pieces; using the largest")
        face = max(faces, key=lambda f: topo.face_area_centroid(f)[0])
        fr = face_anchor_frame(body, [face], _design_name(body, self.face_ref))
        pts = face_points(body, face, fr)
        lst = pts[self.kind]
        if self.kind in ("corner", "mid", "arc") and not pts["simple"]:
            return _fallback(ctx, self.face_ref, who, "corners, middles and arc centres are on flat faces only", self.strict_)
        if self.index >= len(lst):
            return _fallback(ctx, self.face_ref, who, f"face has only {len(lst)} {self.kind} point(s)", self.strict_)
        return Frame.make(lst[self.index], fr.x, fr.z)


# ----------------------------------------------------------------- edges / vertices
class EdgeRef(Ref):
    def __init__(self, handle, name):
        self.handle, self.name = handle, name

    def sig(self):
        return f"E({sig(self.handle)},{self.name!r})"

    def __repr__(self):
        return f'{self.handle.body_id}.edge("{self.name}")'

    def resolve(self, ctx):
        from .body import split_edge_name
        body = ctx.body(self.handle, allow_removed=True)
        for n, e, _ in body.curves():           # a sketch's curve, by name
            if n == self.name:
                return body, [e]
        if body.curves():
            raise RefError(f"{self!r}: the sketch has no curve {self.name!r} "
                           f"(it has {', '.join(n for n, _, _ in body.curves())})")
        a, b = split_edge_name(self.name)
        return EdgeBetweenRef(FaceRef(self.handle, a), FaceRef(self.handle, b)).resolve(ctx, who=repr(self))


class EdgeBetweenRef(Ref):
    def __init__(self, a: FaceRef, b: FaceRef):
        self.a, self.b = a, b

    def sig(self):
        return f"EB({self.a.sig()},{self.b.sig()})"

    def __repr__(self):
        return f"edge_between({self.a!r}, {self.b!r})"

    def resolve(self, ctx, who=None):
        ba, fa = self.a.resolve(ctx)
        bb, fb = self.b.resolve(ctx)
        if ba.id != bb.id:
            raise RefError(f"{who or repr(self)}: faces are on different bodies ({ba.id}, {bb.id})")
        es = ba.edges_between(fa, fb)
        if not es:
            raise RefError(f"{who or repr(self)}: the faces do not share an edge")
        return ba, es


class FaceEdgesRef(Ref):
    def __init__(self, face_ref: FaceRef, kind="all"):
        if kind not in ("all", "outer", "inner"):
            raise ValueError("kind must be 'all', 'outer' or 'inner'")
        self.face_ref, self.kind = face_ref, kind

    def sig(self):
        return f"FE({self.face_ref.sig()},{self.kind})"

    def __repr__(self):
        return f"{self.face_ref!r}.edges(\"{self.kind}\")"

    def resolve(self, ctx, who=None):
        body, faces = self.face_ref.resolve(ctx)
        if self.kind == "outer" and len(faces) > 1:
            # islands (the middle of an engraved 'A' or 'O', a ring inside a groove) are pieces
            # of the face too, but their edges are not the face's outline
            faces = [f for f in faces if not _island(f, [g for g in faces if g is not f])]
        out = []
        for f in faces:
            ow = topo.outer_wire(f)
            outer = topo.edges(ow)
            if self.kind == "outer":
                es = outer
            elif self.kind == "inner":
                es = [e for e in topo.edges(f) if not any(e.IsSame(o) for o in outer)]
            else:
                es = topo.edges(f)
            out += [e for e in es if not any(e.IsSame(x) for x in out)]
        if not out:
            raise RefError(f"{self!r}: no edges")
        return body, out


def _island(face, others) -> bool:
    """Is `face` inside the outline of one of `others` (in a hole of it)?"""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt, gp_Pnt2d
    # a point on the face's own outline: inside a bigger face's outline only for an island
    p = topo.edge_midpoint(topo.edges(topo.outer_wire(face))[0])
    area = topo.face_area_centroid(face)[0]
    for g in others:
        if topo.face_area_centroid(g)[0] <= area:
            continue
        try:
            surf = BRep_Tool.Surface_s(g)
            whole = BRepBuilderAPI_MakeFace(surf, topo.outer_wire(g), True).Face()
            proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), surf)
            if proj.NbPoints() == 0 or proj.LowerDistance() > 1e-6 * max(1.0, float(np.linalg.norm(p))):
                continue
            u, v = proj.LowerDistanceParameters()
            if BRepClass_FaceClassifier(whole, gp_Pnt2d(u, v), 1e-7).State() == TopAbs_IN:
                return True
        except Exception:
            continue
    return False


class AllEdgesRef(Ref):
    def __init__(self, handle):
        self.handle = handle

    def sig(self):
        return f"AE({sig(self.handle)})"

    def __repr__(self):
        return f"{self.handle.body_id}.edges()"

    def resolve(self, ctx, who=None):
        body = ctx.body(self.handle)
        return body, topo.edges(body.shape)


class VertexRef(FrameRef):
    def __init__(self, handle, name):
        super().__init__()
        self.handle, self.name = handle, name

    def owner(self):
        return self.handle

    def sig(self):
        return f"V({sig(self.handle)},{self.name!r},{self._mods_sig()})"

    def __repr__(self):
        return f'{self.handle.body_id}.vertex("{self.name}")'

    def _base(self, ctx):
        body = ctx.body(self.handle)
        p = body.vertex_named(self.name)
        if p is None:
            if body.has_anchor(self.name) and not self.strict_:
                ctx.warn(f"{self!r}: vertex no longer exists; fell back to its design anchor")
                return body.anchor(self.name)
            raise RefError(f"{self!r}: no such vertex")
        return Frame.make(p, body.frame.x, body.frame.z)


def edge_between(a: FaceRef, b: FaceRef) -> EdgeBetweenRef:
    """Edges shared by two named faces (on the same body)."""
    return EdgeBetweenRef(a, b)
