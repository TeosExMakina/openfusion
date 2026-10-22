"""Thin helpers over OCCT (via OCP): iteration, identity, measurement, transforms."""
from __future__ import annotations

from typing import Iterable

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform, BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import (GeomAbs_BSplineSurface, GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder,
                         GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus)
from OCP.GProp import GProp_GProps
from OCP.gp import gp_GTrsf, gp_Mat, gp_Pnt, gp_Trsf, gp_Vec, gp_XYZ
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.BRep import BRep_Builder


def _cast(name):
    return getattr(TopoDS, name + "_s", None) or getattr(TopoDS, name)


_face, _edge, _vertex, _solid, _wire = (_cast(n) for n in ("Face", "Edge", "Vertex", "Solid", "Wire"))

SURFACE_KIND = {
    GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere", GeomAbs_Torus: "torus", GeomAbs_BSplineSurface: "bspline",
}


# ---------------------------------------------------------------- iteration
def _explore(shape, kind, cast) -> list:
    out, seen = [], ShapeSet()
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        s = cast(ex.Current())
        if s not in seen:
            seen.add(s)
            out.append(s)
        ex.Next()
    return out


def faces(shape) -> list:
    return _explore(shape, TopAbs_FACE, _face)


def edges(shape) -> list:
    return _explore(shape, TopAbs_EDGE, _edge)


def vertices(shape) -> list:
    return _explore(shape, TopAbs_VERTEX, _vertex)


def solids(shape) -> list:
    return _explore(shape, TopAbs_SOLID, _solid)


def wires(shape) -> list:
    return _explore(shape, TopAbs_WIRE, _wire)


# ---------------------------------------------------------------- identity
class ShapeDict:
    """Dict keyed by shape identity (IsSame: same TShape and location, any orientation)."""

    def __init__(self):
        self._d: dict[int, list] = {}

    def __setitem__(self, shape, value):
        bucket = self._d.setdefault(hash(shape), [])
        for i, (s, _) in enumerate(bucket):
            if s.IsSame(shape):
                bucket[i] = (s, value)
                return
        bucket.append((shape, value))

    def get(self, shape, default=None):
        for s, v in self._d.get(hash(shape), ()):
            if s.IsSame(shape):
                return v
        return default

    def canonical(self, shape):
        """The stored shape that IsSame() the given one (keeps its orientation in context)."""
        for s, _ in self._d.get(hash(shape), ()):
            if s.IsSame(shape):
                return s
        return None

    def __contains__(self, shape):
        return any(s.IsSame(shape) for s, _ in self._d.get(hash(shape), ()))

    def __getitem__(self, shape):
        for s, v in self._d.get(hash(shape), ()):
            if s.IsSame(shape):
                return v
        raise KeyError("shape not found")

    def items(self):
        for bucket in self._d.values():
            yield from bucket

    def __len__(self):
        return sum(len(b) for b in self._d.values())


class ShapeSet(ShapeDict):
    def add(self, shape):
        self[shape] = True


def edge_face_map(shape) -> ShapeDict:
    """edge -> list of faces that use it."""
    m = ShapeDict()
    for f in faces(shape):
        for e in edges(f):
            lst = m.get(e)
            if lst is None:
                lst = []
                m[e] = lst
            if not any(x.IsSame(f) for x in lst):
                lst.append(f)
    return m


# ---------------------------------------------------------------- measurement
def to_np(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()])


def surface_kind(face) -> str:
    return SURFACE_KIND.get(BRepAdaptor_Surface(face).GetType(), "other")


# The default Gauss integration is fine for planes, cylinders and the like but can be off by
# 10% or more on free-form surfaces (a smooth loft's side); integrate those adaptively.
PROPS_EPS = 1e-7


def _freeform(shape) -> bool:
    return any(surface_kind(f) in ("bspline", "other") for f in faces(shape))


def face_area_centroid(face):
    props = GProp_GProps()
    if surface_kind(face) in ("bspline", "other"):
        BRepGProp.SurfaceProperties_s(face, props, PROPS_EPS)
    else:
        BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass(), to_np(props.CentreOfMass())


def face_normal_at(face, u, v) -> np.ndarray:
    gf = BRepGProp_Face(face)  # accounts for face orientation
    p, n = gp_Pnt(), gp_Vec()
    gf.Normal(u, v, p, n)
    nv = to_np(n)
    nn = np.linalg.norm(nv)
    return nv / nn if nn > 1e-12 else nv


def face_uv_mid(face):
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    return (umin + umax) / 2, (vmin + vmax) / 2


def face_point_normal_mid(face):
    u, v = face_uv_mid(face)
    s = BRepAdaptor_Surface(face)
    return to_np(s.Value(u, v)), face_normal_at(face, u, v)


def face_outward_normal(face) -> np.ndarray:
    u, v = face_uv_mid(face)
    return face_normal_at(face, u, v)


def plane_normal(face) -> np.ndarray:
    """Outward normal of a planar face (robust to mirrored, left-handed placements)."""
    return face_outward_normal(face)


def surface_axis(face):
    """(point, direction) of the axis of a cylinder/cone/sphere/torus face."""
    s = BRepAdaptor_Surface(face)
    t = s.GetType()
    if t == GeomAbs_Cylinder:
        ax = s.Cylinder().Axis()
    elif t == GeomAbs_Cone:
        ax = s.Cone().Axis()
    elif t == GeomAbs_Torus:
        ax = s.Torus().Axis()
    elif t == GeomAbs_Sphere:
        ax = s.Sphere().Position().Axis()
    else:
        return None
    d = ax.Direction()
    return to_np(ax.Location()), np.array([d.X(), d.Y(), d.Z()])


def edge_kind(edge) -> str:
    t = BRepAdaptor_Curve(edge).GetType()
    return {GeomAbs_Line: "line", GeomAbs_Circle: "circle"}.get(t, "curve")


def edge_endpoints(edge):
    c = BRepAdaptor_Curve(edge)
    return to_np(c.Value(c.FirstParameter())), to_np(c.Value(c.LastParameter()))


def edge_midpoint(edge) -> np.ndarray:
    c = BRepAdaptor_Curve(edge)
    return to_np(c.Value((c.FirstParameter() + c.LastParameter()) / 2))


def edge_length(edge) -> float:
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    return props.Mass()


def circle_center_radius(edge):
    circ = BRepAdaptor_Curve(edge).Circle()
    ax = circ.Axis().Direction()
    return to_np(circ.Location()), circ.Radius(), np.array([ax.X(), ax.Y(), ax.Z()])


def _has_offset_faces(shape) -> bool:
    return any(surface_kind(f) == "other" for f in faces(shape))


def _mesh_volume_centroid(shape):
    """Volume and centroid from a fine triangulation: OpenCASCADE's integration is unreliable
    on offset surfaces (the inner walls a shell makes), off by a third or more."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location
    lo, hi = bbox(shape)
    tol = max(1e-3, float(np.linalg.norm(hi - lo)) * 1e-4)
    BRepMesh_IncrementalMesh(shape, tol, False, 0.1, True)
    vol, mom = 0.0, np.zeros(3)
    for f in faces(shape):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        pts = np.array([[q.X(), q.Y(), q.Z()] for q in (tri.Node(i + 1).Transformed(trsf) for i in range(tri.NbNodes()))])
        idx = np.array([tri.Triangle(i + 1).Get() for i in range(tri.NbTriangles())]) - 1
        if f.Orientation() == TopAbs_REVERSED:
            idx = idx[:, [0, 2, 1]]
        a, b, c = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
        v = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6
        vol += v.sum()
        mom += ((a + b + c) / 4 * v[:, None]).sum(axis=0)
    return vol, (mom / vol if vol else mom)


def volume(shape) -> float:
    if _has_offset_faces(shape):
        return _mesh_volume_centroid(shape)[0]
    props = GProp_GProps()
    if _freeform(shape):
        BRepGProp.VolumeProperties_s(shape, props, PROPS_EPS, True)
    else:
        BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def center_of_mass(shape) -> np.ndarray:
    if _has_offset_faces(shape):
        return _mesh_volume_centroid(shape)[1]
    props = GProp_GProps()
    if _freeform(shape):
        BRepGProp.VolumeProperties_s(shape, props, PROPS_EPS, True)
    else:
        BRepGProp.VolumeProperties_s(shape, props)
    return to_np(props.CentreOfMass())


def outer_wire(face):
    return BRepTools.OuterWire_s(face)


def ordered_wire_edges(wire, face) -> list:
    """Edges of a wire in connection order."""
    from OCP.BRepTools import BRepTools_WireExplorer
    out = []
    ex = BRepTools_WireExplorer(wire, face)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def ordered_wire_vertices(wire, face) -> list:
    from OCP.BRepTools import BRepTools_WireExplorer
    out = []
    ex = BRepTools_WireExplorer(wire, face)
    while ex.More():
        out.append(to_np(BRep_Tool.Pnt_s(ex.CurrentVertex())))
        ex.Next()
    return out


# ---------------------------------------------------------------- transforms
def trsf_from_matrix(m: np.ndarray) -> gp_Trsf:
    t = gp_Trsf()
    t.SetValues(m[0, 0], m[0, 1], m[0, 2], m[0, 3],
                m[1, 0], m[1, 1], m[1, 2], m[1, 3],
                m[2, 0], m[2, 1], m[2, 2], m[2, 3])
    return t


def is_similarity(m: np.ndarray) -> bool:
    a = m[:3, :3]
    s = abs(np.linalg.det(a)) ** (1 / 3)
    return np.allclose(a.T @ a, (s ** 2) * np.eye(3), atol=1e-7 * max(1, s * s))


def transform_shape(shape, m: np.ndarray):
    """Apply a 4x4 affine. Returns (new_shape, builder) where builder has Modified()."""
    if is_similarity(m):
        b = BRepBuilderAPI_Transform(shape, trsf_from_matrix(m), True)
    else:
        g = gp_GTrsf()
        a = m[:3, :3]
        g.SetVectorialPart(gp_Mat(*[float(a[i, j]) for i in range(3) for j in range(3)]))
        g.SetTranslationPart(gp_XYZ(*[float(c) for c in m[:3, 3]]))
        b = BRepBuilderAPI_GTransform(shape, g, True)
    b.Build()
    return b.Shape(), b


def compound(shapes: Iterable) -> TopoDS_Compound:
    c = TopoDS_Compound()
    bld = BRep_Builder()
    bld.MakeCompound(c)
    for s in shapes:
        bld.Add(c, s)
    return c


def bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    b.SetGap(0.0)
    return to_np(b.CornerMin()), to_np(b.CornerMax())


def is_valid(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Analyzer
    return BRepCheck_Analyzer(shape).IsValid()
