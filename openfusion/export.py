"""Exports (STEP, STL, 3MF) and the mesh/scene data used by the viewer.

Only solid bodies are exported. Reference and removed bodies never are.
"""
from __future__ import annotations

import io
import json
import struct
import zipfile
from xml.sax.saxutils import escape

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

from . import topo
from .body import Body

PRINT_TOL = 0.01      # mm chordal deviation for print exports
VIEW_TOL = 0.04       # mm for the viewer (scaled up for large parts)
ANGULAR = 0.25        # radians


# ---------------------------------------------------------------- meshing
def _tol_for(shape, tol):
    lo, hi = topo.bbox(shape)
    diag = float(np.linalg.norm(hi - lo))
    return max(tol, diag * 2e-4)


def mesh_faces(shape, tol=VIEW_TOL, angular=ANGULAR):
    """Yield (face, positions Nx3, normals Nx3, triangles Mx3) per face."""
    BRepMesh_IncrementalMesh(shape, _tol_for(shape, tol), False, angular, True)
    for f in topo.faces(shape):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        if tri is None:
            continue        # the mesher gives up on some hairline faces; body_mesh closes the gap
        trsf = loc.Transformation()
        n = tri.NbNodes()
        pos = np.empty((n, 3))
        for i in range(n):
            p = tri.Node(i + 1).Transformed(trsf)
            pos[i] = (p.X(), p.Y(), p.Z())
        if not tri.HasNormals():
            tri.ComputeNormals()
        nrm = np.empty((n, 3))
        for i in range(n):
            d = tri.Normal(i + 1)
            nrm[i] = (d.X(), d.Y(), d.Z())
        if not loc.IsIdentity():
            r = np.array([[trsf.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
            nrm = nrm @ r.T
        reverse = f.Orientation() == TopAbs_REVERSED
        if reverse:
            nrm = -nrm
        m = tri.NbTriangles()
        tris = np.empty((m, 3), dtype=np.int64)
        for i in range(m):
            a, b, c = tri.Triangle(i + 1).Get()
            tris[i] = (a - 1, c - 1, b - 1) if reverse else (a - 1, b - 1, c - 1)
        yield f, pos, nrm, tris


def _ear_clip(p):
    """Triangulate a simple polygon (2D points); triangles counterclockwise."""
    n = len(p)
    area = sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1] for i in range(n))
    idx = list(range(n)) if area > 0 else list(range(n))[::-1]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    out, guard = [], 0
    while len(idx) > 3 and guard < 10 * n * n:
        guard += 1
        for k in range(len(idx)):
            i, j, l = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross(p[i], p[j], p[l]) <= 0:
                continue
            if any(m not in (i, j, l) and cross(p[i], p[j], p[m]) >= 0 and cross(p[j], p[l], p[m]) >= 0 and
                   cross(p[l], p[i], p[m]) >= 0 for m in idx):
                continue
            out.append((i, j, l))
            idx.pop(k)
            break
        else:
            # nothing convex left (degenerate sliver): fan out what remains
            out += [(idx[0], idx[m], idx[m + 1]) for m in range(1, len(idx) - 1)]
            idx = idx[:3]
            break
    if len(idx) == 3:
        out.append(tuple(idx))
    return out


def body_mesh(shape, tol=PRINT_TOL):
    """Single indexed mesh with vertices merged across faces (watertight for slicers).
    Meshes a copy: OCCT keeps the finest mesh ever made on a shape and reuses it, so
    meshing the shared shape would let a Fine export leak into later Draft ones."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
    verts, index, tris = [], {}, []
    for _, pos, _, t in mesh_faces(shape, tol):
        remap = []
        for p in pos:
            key = tuple(np.round(p, 6))
            k = index.get(key)
            if k is None:
                k = len(verts)
                index[key] = k
                verts.append(p)
            remap.append(k)
        for a, b, c in t:
            ia, ib, ic = remap[a], remap[b], remap[c]
            if ia != ib and ib != ic and ia != ic:
                tris.append((ia, ib, ic))
    verts = np.array(verts)
    tris += _close_holes(verts, tris)
    return verts, np.array(tris, dtype=np.int64)


def _close_holes(verts, tris, max_loop=4000):
    """Triangles closing any boundary loops left in a mesh (hairline gaps where the mesher
    skipped a face), so exports are watertight. Each loop is filled on its best-fit plane,
    wound against the triangles around it."""
    from collections import defaultdict
    count = defaultdict(int)
    for a, b, c in tris:
        for x, y in ((a, b), (b, c), (c, a)):
            count[(x, y)] += 1
    nxt = {}
    for (x, y), k in count.items():
        if count.get((y, x), 0) == 0:          # a boundary edge, seen in one direction only
            nxt.setdefault(y, []).append(x)    # walk the hole the other way round
    out = []
    while nxt:
        start = next(iter(nxt))
        loop, cur = [start], start
        while True:
            options = nxt.get(cur)
            if not options:
                break
            n2 = options.pop()
            if not options:
                del nxt[cur]
            if n2 == start:
                break
            loop.append(n2)
            cur = n2
            if len(loop) > max_loop:
                break
        if len(loop) < 3 or len(loop) > max_loop:
            continue
        pts = verts[loop]
        c = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - c)
        ux, uy = vt[0], vt[1]
        p2 = [((q - c) @ ux, (q - c) @ uy) for q in pts]
        local = _ear_clip_keep_order(p2)
        out += [(loop[i], loop[j], loop[k]) for i, j, k in local]
    return out


def _ear_clip_keep_order(p):
    """Triangulate a 2D polygon keeping its winding (the loop's order sets the facing)."""
    n = len(p)
    area = sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1] for i in range(n))
    if area >= 0:
        return _ear_clip(p)
    flipped = [(x, -y) for x, y in p]          # mirror, clip counterclockwise, keep index order
    return [(i, j, k) for i, j, k in _ear_clip(flipped)]


def edge_polyline(edge, tol=VIEW_TOL):
    c = BRepAdaptor_Curve(edge)
    d = GCPnts_TangentialDeflection(c, 0.15, tol)
    return [topo.to_np(d.Value(i)) for i in range(1, d.NbPoints() + 1)]


# ---------------------------------------------------------------- file exports
FLAT_KINDS = ("profile", "construction")       # sketches, text, profiles, planes, axes: nothing to print


def printable(bodies) -> list[Body]:
    return [b for b in bodies if b.role == "solid" and b.kind not in FLAT_KINDS]


def export_stl(bodies, path, tol=PRINT_TOL):
    tris_all = []
    for b in printable(bodies):
        v, t = body_mesh(b.shape, tol)
        tris_all.append(v[t])
    tri = np.concatenate(tris_all) if tris_all else np.zeros((0, 3, 3))
    with open(path, "wb") as fh:
        fh.write(b"OpenFusion binary STL".ljust(80, b" "))
        fh.write(struct.pack("<I", len(tri)))
        for t in tri:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            ln = np.linalg.norm(n)
            n = n / ln if ln > 0 else n
            fh.write(struct.pack("<12fH", *n, *t[0], *t[1], *t[2], 0))
    return len(tri)


def export_3mf(bodies, path, tol=PRINT_TOL):
    """3MF with one object per body (names preserved), units in millimeters."""
    objs, items = [], []
    for i, b in enumerate(printable(bodies), start=1):
        v, t = body_mesh(b.shape, tol)
        vs = "".join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in v)
        ts = "".join(f'<triangle v1="{a}" v2="{c}" v3="{d}"/>' for a, c, d in t)
        objs.append(f'<object id="{i}" name="{escape(b.id)}" type="model"><mesh><vertices>{vs}</vertices>'
                    f'<triangles>{ts}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Application">OpenFusion</metadata>'
             f'<resources>{"".join(objs)}</resources><build>{"".join(items)}</build></model>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
              '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    return len(objs)


def export_step(bodies, path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    w = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    bs = printable(bodies)
    for b in bs:
        w.Transfer(b.shape, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP export to {path} failed")
    return len(bs)


def export(bodies, path, tol=PRINT_TOL, only_solid=True) -> int:
    """Write `bodies` to one file. By default only solid bodies are written; pass
    only_solid=False to export exactly the bodies given (e.g. a reference body
    the user picked on purpose)."""
    bodies = printable(bodies) if only_solid else [b for b in bodies if b.role != "removed" and b.kind not in FLAT_KINDS]
    p = str(path).lower()
    if p.endswith(".stl"):
        return export_stl(_as_solid(bodies), path, tol)
    if p.endswith(".3mf"):
        return export_3mf(_as_solid(bodies), path, tol)
    if p.endswith((".step", ".stp")):
        return export_step(_as_solid(bodies), path)
    raise ValueError("export format must be .stl, .3mf or .step")


def _as_solid(bodies):
    """The writers filter on role; bodies chosen explicitly are written whatever their role."""
    import dataclasses
    return [b if b.role == "solid" else dataclasses.replace(b, role="solid") for b in bodies]


# ---------------------------------------------------------------- per-body export
QUALITY = {"draft": 0.05, "standard": PRINT_TOL, "fine": 0.002}
FORMATS = ("3mf", "stl", "step")
_TRI_CACHE: "dict[tuple, tuple]" = {}


def triangle_count(b, tol) -> int:
    key = (id(b), tol)
    hit = _TRI_CACHE.get(key)
    if hit is None or hit[0] is not b:
        _, t = body_mesh(b.shape, tol)
        hit = (b, len(t))
        if len(_TRI_CACHE) > 400:
            _TRI_CACHE.clear()
        _TRI_CACHE[key] = hit
    return hit[1]


def body_report(b, tol=PRINT_TOL, problem: str | None = None) -> dict:
    """What the export dialog shows for one body: size, volume, mesh size and anything
    that would make it a poor print (several pieces, not a closed solid, out of date)."""
    lo, hi = topo.bbox(b.shape)
    notes = []
    n_solids = len(topo.solids(b.shape))
    if n_solids == 0:
        notes.append("not a closed solid; slicers may reject it")
    elif n_solids > 1:
        notes.append(f"{n_solids} separate pieces in one body")
    if not topo.is_valid(b.shape):
        notes.append("geometry check failed; the mesh may have gaps")
    tris = triangle_count(b, tol) if problem is None else 0
    return {
        "id": b.id, "role": b.role, "mirrored": b.mirrored,
        "size": [round(float(x), 3) for x in hi - lo], "min": [round(float(x), 3) for x in lo],
        "volume": round(topo.volume(b.shape), 3),
        "triangles": tris, "stl_bytes": 84 + 50 * tris,
        "notes": notes, "problem": problem,
    }


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s) or "body"


def export_bundle(bodies, fmt: str, stem: str, each=False, tol=PRINT_TOL) -> tuple[str, bytes, int]:
    """Export the given bodies (exactly these, whatever their role) and return
    (file name, bytes, count). `each` gives one file per body, zipped when there
    are several."""
    import tempfile
    from pathlib import Path
    if fmt not in FORMATS:
        raise ValueError("format must be 3mf, stl or step")
    bodies = [b for b in bodies if b.role != "removed" and b.kind not in FLAT_KINDS]
    if not bodies:
        raise ValueError("no bodies to export (cross-section profiles are not printable)")
    stem = safe_name(stem)
    with tempfile.TemporaryDirectory() as d:
        if each and len(bodies) > 1:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for b in bodies:
                    out = Path(d) / f"{safe_name(b.id)}.{fmt}"
                    export([b], out, tol, only_solid=False)
                    z.write(out, f"{stem}-{safe_name(b.id)}.{fmt}")
            return f"{stem}-{fmt}.zip", buf.getvalue(), len(bodies)
        if len(bodies) == 1:
            name = f"{stem}-{safe_name(bodies[0].id)}.{fmt}"
        else:
            name = f"{stem}.{fmt}"
        out = Path(d) / name
        export(bodies, out, tol, only_solid=False)
        return name, out.read_bytes(), len(bodies)


# ---------------------------------------------------------------- viewer scene
def _r(a, nd=4):
    return [round(float(x), nd) for x in np.asarray(a).ravel()]


_BODY_CACHE: "dict[int, tuple]" = {}


def scene(result, path: str | None = None) -> dict:
    """Everything the viewer needs, as JSON-ready data. Bodies are immutable, so
    the per-body scene data is cached by identity: unchanged bodies are not re-meshed."""
    out = {"file": path, "params": {k: v for k, v in result.params.items()},
           "timeline": [r.as_dict() for r in result.results], "summary": result.summary(),
           "script_error": result.script_error, "script_error_line": result.script_error_line,
           "bodies": []}
    live = set()
    for b in result.bodies.values():
        hit = _BODY_CACHE.get(id(b))
        if hit is None or hit[0] is not b:
            hit = (b, body_scene(b))
            _BODY_CACHE[id(b)] = hit
        live.add(id(b))
        out["bodies"].append(hit[1])
    if len(_BODY_CACHE) > 400:
        for k in [k for k in _BODY_CACHE if k not in live]:
            del _BODY_CACHE[k]
    return out


def _snap(ref, kind, fr, o=None):
    o = fr.origin if o is None else o
    return {"ref": ref, "kind": kind, "o": _r(o), "x": _r(fr.x), "z": _r(fr.z)}


def body_scene(b) -> dict:
    """Mesh, edges, anchors and snap points of one body. Snap points carry the exact
    reference text the script will use, so the GUI never invents names."""
    from .geom import Frame
    from .refs import face_anchor_frame, face_points
    q = json.dumps
    efmap = topo.edge_face_map(b.shape)
    anchor_names = set(b.anchor_names())
    faces, curved, holes = [], [], {}
    for f, pos, nrm, tris in mesh_faces(b.shape):
        label = b.label_of(f) or "?"
        kind = topo.surface_kind(f)
        entry = {"label": label, "kind": kind, "p": _r(pos), "n": _r(nrm, 3),
                 "i": [int(x) for x in tris.ravel()], "snaps": []}
        faces.append(entry)
        if b.role == "removed":
            continue
        fref = f"{b.id}.face({q(label)})"
        origin, base, _ = b.parse_face(label)
        own = origin == b.id
        try:
            if kind == "plane":
                fr = face_anchor_frame(b, [f], base if own else None)
                if own and base in anchor_names:
                    da = b.anchor(base)
                    entry["snaps"].append(_snap(f"{b.id}.anchor({q(base)})", "center", da))
                else:
                    entry["snaps"].append(_snap(f"{fref}.anchor()", "center", fr))
                pts = face_points(b, f, fr)
                for k in ("hole", "boss", "corner", "mid", "arc"):
                    for i, pt in enumerate(pts[k]):
                        s = _snap(f"{fref}.{k}({i})", k, fr, pt)
                        entry["snaps"].append(s)
                        if k in ("hole", "boss"):
                            holes[tuple(np.round(pt, 5))] = s
                entry["center"] = _r(fr.origin)
                entry["points"] = {k: [_r(x) for x in v] for k, v in pts.items() if k != "simple" and v}
            else:
                if own and base in anchor_names:
                    entry["snaps"].append(_snap(f"{b.id}.anchor({q(base)})", "center", b.anchor(base)))
                else:
                    pt, n = topo.face_point_normal_mid(f)
                    entry["snaps"].append(_snap(f"{fref}.anchor()", "center", Frame.make(pt, np.cross(b.frame.z, n), n)))
                curved.append((entry, f))
        except Exception:
            pass
    # a hole's wall (or a boss's side) offers the center from the face around it
    for entry, f in curved:
        for e in topo.edges(f):
            if topo.edge_kind(e) != "circle":
                continue
            c = tuple(np.round(topo.circle_center_radius(e)[0], 5))
            if c in holes and holes[c] not in entry["snaps"]:
                entry["snaps"].insert(0, holes[c])
    if True:
        edges = []
        for e, fs in efmap.items():
            edges.append({"label": b.edge_label(e, efmap), "p": _r(np.array(edge_polyline(e)))})
        if not efmap:                     # an open profile (a path): a wire with no faces
            for i, e in enumerate(topo.edges(b.shape)):
                edges.append({"label": f"seg{i}", "p": _r(np.array(edge_polyline(e)))})
        anchors = []
        for name in b.anchor_names():
            try:
                fr = b.anchor(name)
            except Exception:
                continue
            anchors.append({"name": name, **{k: _r(v) for k, v in fr.as_dict().items()}})
        return {
            "id": b.id, "role": b.role, "kind": b.kind, "mirrored": b.mirrored,
            "volume": round(topo.volume(b.shape), 3), "history": list(b.history),
            "faces": faces, "edges": edges, "anchors": anchors,
        }

