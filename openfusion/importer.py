"""Bringing in parts made elsewhere: STEP (exact solids) and STL / 3MF meshes (turned into
solids, with coplanar triangles merged back into flat faces).

    bracket = import_file("bracket.step")                  # next to the script, or a full path
    knob = import_file("knob.stl", at=plate.anchor("+Z"))  # sits on the plate's top

An imported body has no design history, so its faces are named by what they are, in an order
that only depends on the geometry:

  * flat faces square to the axes: by outward normal, like a box: "+Z", "-X"... When several
    face the same way they are numbered from the outermost in: "+Z#0" is the top, "+Z#1" the
    next one down (equal heights: largest first);
  * other flat faces "plane#k", cylinders "cylinder#k", cones "cone#k", spheres "sphere#k",
    tori "torus#k", anything else "surface#k" (largest first).

Its anchors are those of its bounding box: "center", "+X" ... "-Z" (the middles of the box's
sides), "-X-Y-Z" style corners, like a box. The file's own origin is "origin".

The body is rebuilt when the file changes (the engine notices its time stamp and size)."""
from __future__ import annotations

import os
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

from . import topo
from .body import Body, FaceName, NameError_
from .geom import Frame
from .primitives import BoxDesign, sign_name

MESH_LIMIT = 200_000            # triangles; beyond this the solid would be too heavy to work with


class ImportDesign(BoxDesign):
    """An imported part's anchors: its bounding box's, like a box's."""
    kind = "import"

    def get(self, name, params):
        try:
            return super().get(name, params)
        except NameError_:
            raise NameError_(f"an imported part has no anchor '{name}' (its anchors are its bounding box's; "
                             "for a face's centre use .face(\"...\").anchor())") from None


def resolve(path: str, script: str | None) -> str:
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p) and script and os.path.isabs(script):
        p = os.path.join(os.path.dirname(script), p)
    return os.path.abspath(p)


def stamp(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


# ------------------------------------------------------------------ reading
def read_step(path: str):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    r = STEPControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        raise ValueError(f"couldn't read {os.path.basename(path)} as STEP")
    r.TransferRoots()
    shape = r.OneShape()
    if shape is None or shape.IsNull():
        raise ValueError(f"{os.path.basename(path)} has no shapes in it")
    return shape


def _stl_triangles(path: str) -> np.ndarray:
    """(n, 3, 3) triangles from a binary or ASCII STL."""
    data = open(path, "rb").read()
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n, offset=84)
            return rec["v"].astype(float)
    text = data.decode("utf-8", "replace")
    vs = [list(map(float, ln.split()[1:4])) for ln in text.splitlines() if ln.strip().startswith("vertex")]
    if not vs or len(vs) % 3:
        raise ValueError(f"{os.path.basename(path)} isn't an STL file")
    return np.array(vs, float).reshape(-1, 3, 3)


def _3mf_triangles(path: str) -> np.ndarray:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError(f"{os.path.basename(path)} isn't a 3MF file") from None
    tris = []
    for name in z.namelist():
        if not name.lower().endswith(".model"):
            continue
        root = ET.fromstring(z.read(name))
        unit = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}.get(root.get("unit", "millimeter"), 1.0)
        for mesh in root.iter():
            if not mesh.tag.endswith("mesh"):
                continue
            verts = [[float(v.get(k)) * unit for k in "xyz"] for v in mesh.iter() if v.tag.endswith("vertex")]
            V = np.array(verts, float)
            for t in mesh.iter():
                if t.tag.endswith("triangle"):
                    tris.append(V[[int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))]])
    if not tris:
        raise ValueError(f"{os.path.basename(path)} has no triangles in it")
    return np.array(tris)


def _weld(tris: np.ndarray):
    """Shared vertices: (points (m, 3), triangles as index triples (n, 3)), slivers dropped."""
    pts = tris.reshape(-1, 3)
    span = float(np.ptp(pts, axis=0).max()) or 1.0
    tol = span * 1e-7
    key = np.round(pts / tol).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    P = np.zeros((len(uniq), 3))
    np.add.at(P, inv, pts)
    P /= np.bincount(inv)[:, None]
    T = inv.reshape(-1, 3)
    ok = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])
    T = T[ok]
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    T = T[np.linalg.norm(np.cross(b - a, c - a), axis=1) > tol * tol]
    return P, T, tol


def _closed(T) -> bool:
    """Every edge shared by exactly two triangles."""
    e = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return bool(len(counts)) and bool((counts == 2).all())


def _consistent(T) -> bool:
    """Neighbouring triangles wound the same way (each directed edge used once)."""
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    return len(np.unique(e, axis=0)) == len(e)


def _shell_direct(P, T):
    """One face per triangle on shared vertices and edges: no sewing needed (fast)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Wire
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    V = [BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, p))).Vertex() for p in P]
    E = {}

    def edge(i, j):                      # running from i to j
        k = (i, j) if i < j else (j, i)
        e = E.get(k)
        if e is None:
            e = E[k] = BRepBuilderAPI_MakeEdge(V[k[0]], V[k[1]]).Edge()
        return e if i < j else e.Reversed()
    bb = BRep_Builder()
    shell = TopoDS_Shell()
    bb.MakeShell(shell)
    N = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    N /= np.linalg.norm(N, axis=1)[:, None]
    for (a, b, c), n in zip(T, N):
        w = TopoDS_Wire()
        bb.MakeWire(w)
        for e in (edge(a, b), edge(b, c), edge(c, a)):
            bb.Add(w, e)
        w.Closed(True)
        f = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*map(float, P[a])), gp_Dir(*map(float, n))), w, True)
        if f.IsDone():
            bb.Add(shell, f.Face())
    shell.Closed(True)
    return shell


def _shell_closed(sh) -> bool:
    m = topo.edge_face_map(sh)
    return all(len(m.get(e) or ()) == 2 for e in topo.edges(sh))


def _shell_sewn(P, T, tol):
    """For meshes that aren't cleanly closed: sew the triangles (slower, forgives small gaps)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_Sewing
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.gp import gp_Pnt
    sew = BRepBuilderAPI_Sewing(max(tol * 10, 1e-6))
    for a, b, c in T:
        poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*P[a]), gp_Pnt(*P[b]), gp_Pnt(*P[c]), True)
        if poly.IsDone():
            f = BRepBuilderAPI_MakeFace(poly.Wire(), True)
            if f.IsDone():
                sew.Add(f.Face())
    sew.Perform()
    return topo._explore(sew.SewedShape(), TopAbs_SHELL, topo._cast("Shell"))


def mesh_to_solid(tris: np.ndarray, name: str):
    """Triangles -> a solid: one closed shell of triangles, then each group of coplanar
    neighbours merged into one flat face (a cube's 12 triangles become its 6 faces)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.ShapeFix import ShapeFix_Shell, ShapeFix_Solid
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    if len(tris) > MESH_LIMIT:
        raise ValueError(f"{name} has {len(tris):,} triangles; up to {MESH_LIMIT:,} can be made into a solid "
                         "(decimate it in your slicer or mesh tool first)")
    P, T, tol = _weld(np.asarray(tris, float))
    if not len(T):
        raise ValueError(f"{name} has no usable triangles")
    direct = _closed(T)
    if direct:
        sh = _shell_direct(P, T)
        if not _consistent(T):                 # some triangles face the wrong way
            fix = ShapeFix_Shell(sh)
            fix.Perform()
            sh = fix.Shell()
        shells = [sh]
    else:
        if len(T) > 30_000:
            raise ValueError(f"{name} has holes or loose edges and {len(T):,} triangles: repair it in your mesh tool first "
                             "(OpenFusion can patch small meshes, up to 30,000 triangles)")
        shells = [sh for sh in _shell_sewn(P, T, tol) if _shell_closed(sh)]
    if not shells:
        raise ValueError(f"{name}: the triangles don't join up into a closed surface")
    solids = []
    for sh in shells:
        mk = BRepBuilderAPI_MakeSolid(sh)
        if not mk.IsDone():
            continue
        if direct:
            solids.append(mk.Solid())
            continue
        fix = ShapeFix_Solid(mk.Solid())
        fix.Perform()
        solids.append(topo.solids(fix.Solid())[0] if topo.solids(fix.Solid()) else fix.Solid())
    if not solids:
        raise ValueError(f"{name}: the triangles don't close up into a solid")
    shape = solids[0] if len(solids) == 1 else topo.compound(solids)
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
    u.SetLinearTolerance(tol * 10)
    u.SetAngularTolerance(1e-6)
    u.Build()
    shape = u.Shape()
    if topo.volume(shape) < 0:
        shape = shape.Reversed()               # an inside-out mesh: turn it over
    return shape, len(tris)


def read_any(path: str):
    """(shape, info) for a STEP, STL or 3MF file."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise ValueError(f"no such file: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".step", ".stp"):
        return read_step(path), {"format": "step"}
    if ext == ".stl":
        shape, n = mesh_to_solid(_stl_triangles(path), name)
        return shape, {"format": "stl", "triangles": n}
    if ext == ".3mf":
        shape, n = mesh_to_solid(_3mf_triangles(path), name)
        return shape, {"format": "3mf", "triangles": n}
    raise ValueError(f"OpenFusion imports .step, .stp, .stl and .3mf files, not {ext or name}")


# ------------------------------------------------------------------ naming
KIND_NAMES = {"plane": "plane", "cylinder": "cylinder", "cone": "cone", "sphere": "sphere", "torus": "torus"}


def name_faces(fid: str, shape) -> list:
    groups: dict[str, list] = {}
    for f in topo.faces(shape):
        kind = topo.surface_kind(f)
        area, c = topo.face_area_centroid(f)
        c = np.asarray(c, float)
        if kind == "plane":
            n = topo.face_outward_normal(f)
            sn = sign_name(n, tol=1e-6)
            if sn:
                i = "XYZ".index(sn[1])
                out = c[i] if sn[0] == "+" else -c[i]
                groups.setdefault(sn, []).append((f, (-round(out, 6), -round(area, 6), *np.round(c, 6))))
                continue
        base = KIND_NAMES.get(kind, "surface")
        groups.setdefault(base, []).append((f, (-round(area, 6), *np.round(c, 6))))
    names = []
    for base, items in groups.items():
        items.sort(key=lambda x: x[1])
        if len(items) == 1 and base[0] in "+-":
            names.append((items[0][0], FaceName(fid, base)))
        else:
            names += [(f, FaceName(fid, base, k)) for k, (f, _) in enumerate(items)]
    return names


def make_import(fid: str, path: str, frame: Frame, role: str, scale: float = 1.0):
    shape, info = read_any(path)
    if scale != 1.0:
        if not scale > 0:
            raise ValueError("scale must be positive")
        m = np.eye(4)
        m[:3, :3] *= scale
        shape = topo.transform_shape(shape, m)[0]
    sols = topo.solids(shape)
    if not sols:
        raise ValueError(f"{os.path.basename(path)} has no solids (only surfaces); OpenFusion needs closed solids")
    shape = sols[0] if len(sols) == 1 else topo.compound(sols)
    lo, hi = topo.bbox(shape)
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    names = name_faces(fid, shape)
    body = Body(id=fid, shape=shape, frame=Frame.world(), face_names=tuple(names), design=ImportDesign(lo, hi - lo),
                kind="import", role=role, params=(("file", os.path.basename(path)),), history=(fid,))
    m = frame.matrix()
    if not np.allclose(m, np.eye(4)):
        body = body.moved(m, fid).evolve(history=(fid,))
    info.update(solids=len(sols), faces=len(names))
    return body, info


# ------------------------------------------------------------------ the script function
def _import_file(ctx, path, at=None, spin=0.0, scale=1.0, role="solid", _file=None, _stamp=None):
    """A STEP, STL or 3MF file as a body (see the module docstring for its names)."""
    from .ops import _creation_frame, _role
    from .refs import FaceRef
    if isinstance(at, FaceRef):
        at = at.anchor()
    if _stamp == "missing":
        raise ValueError(f"no such file: {_file}")
    body, info = make_import(ctx.fid, _file, _creation_frame(ctx, at, spin), _role(role), float(scale))
    if info["solids"] > 1:
        ctx.warn(f"{os.path.basename(_file)} holds {info['solids']} solids; they are one body here (split or combine them as needed)")
    ctx.put(body)
    ctx.info["import"] = info


def _make_api():
    from .document import current_doc
    from .ops import _record
    recorded = _record("create")(_import_file)

    def import_file(path, at=None, spin=0.0, scale=1.0, role="solid"):
        """Bring in a part from a STEP, STL or 3MF file. A relative path is next to the script.
        at: where its origin goes (default the world origin); scale: 25.4 for a part drawn in inches.
        Meshes become solids with their flat areas merged into faces."""
        doc = current_doc()
        full = resolve(path, doc.path)
        return recorded(path, at=at, spin=spin, scale=scale, role=role, _file=full, _stamp=stamp(full))

    return import_file


import_file = _make_api()
