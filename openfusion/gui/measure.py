"""Selection readout for the app: what Fusion shows at the bottom right for a selection."""
from __future__ import annotations

import math

import numpy as np

from .. import topo


def _edge_for(body, label, point):
    es = body.edges_named(label) if "|" in label or not label.startswith("seg") else []
    if not es:
        es = topo.edges(body.shape)
    if point is None or len(es) == 1:
        return es[0] if es else None
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
    return min(es, key=lambda e: BRepExtrema_DistShapeShape(v, e).Value())


def _shape_of(res, item):
    body = res.bodies.get(item.get("body"))
    if body is None:
        return None, None
    kind = item.get("kind")
    if kind == "body":
        return body.shape, body
    if kind == "face":
        fs = body.faces_named(item.get("label", ""))
        if not fs:
            return None, body
        return (fs[0] if len(fs) == 1 else topo.compound(fs)), body
    if kind == "edge":
        return _edge_for(body, item.get("label", ""), item.get("point")), body
    return None, body


def _edge_info(e):
    kind = topo.edge_kind(e)
    out = {"what": "Edge", "rows": []}
    L = topo.edge_length(e)
    if kind == "line":
        a, b = topo.edge_endpoints(e)
        out["what"] = "Straight edge"
        out["rows"] = [["Length", L, "mm"]]
        out["dir"] = list(map(float, (b - a) / (np.linalg.norm(b - a) or 1)))
    elif kind == "circle":
        c, r, _ = topo.circle_center_radius(e)
        full = abs(L - 2 * math.pi * r) < 1e-6 * max(1.0, r)
        out["what"] = "Circle" if full else "Arc"
        out["rows"] = [["Radius", r, "mm"], ["Diameter", 2 * r, "mm"]]
        if not full:
            out["rows"] += [["Arc length", L, "mm"], ["Angle", math.degrees(L / r), "°"]]
        out["center"] = list(map(float, c))
    else:
        out["rows"] = [["Length", L, "mm"]]
    return out


def _face_info(f):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    kind = topo.surface_kind(f) if not isinstance(f, type(topo.compound([]))) else "pieces"
    area = sum(topo.face_area_centroid(x)[0] for x in topo.faces(f))
    out = {"what": "Face", "rows": [["Area", area, "mm²"]]}
    if kind == "plane":
        out["what"] = "Flat face"
        out["normal"] = list(map(float, topo.plane_normal(f)))
    elif kind in ("cylinder", "cone", "sphere", "torus"):
        s = BRepAdaptor_Surface(f)
        out["what"] = {"cylinder": "Cylindrical face", "cone": "Conical face", "sphere": "Spherical face", "torus": "Toroidal face"}[kind]
        if kind == "cylinder":
            out["rows"].append(["Radius", s.Cylinder().Radius(), "mm"])
        elif kind == "sphere":
            out["rows"].append(["Radius", s.Sphere().Radius(), "mm"])
        elif kind == "cone":
            out["rows"].append(["Half angle", abs(math.degrees(s.Cone().SemiAngle())), "°"])
        else:
            out["rows"] += [["Major radius", s.Torus().MajorRadius(), "mm"], ["Minor radius", s.Torus().MinorRadius(), "mm"]]
    elif kind == "bspline":
        out["what"] = "Free-form face"
    elif kind == "pieces":
        out["what"] = f"Face ({len(topo.faces(f))} pieces)"
    return out


def _body_info(b):
    lo, hi = topo.bbox(b.shape)
    size = hi - lo
    area = sum(topo.face_area_centroid(f)[0] for f in topo.faces(b.shape))
    return {"what": "Body", "rows": [["Volume", topo.volume(b.shape), "mm³"], ["Surface area", area, "mm²"],
                                     ["Size", " × ".join(f"{v:.2f}" for v in size), "mm"]]}


def describe_selection(res, items):
    out, shapes = [], []
    for it in items[:12]:
        shape, body = _shape_of(res, it)
        if shape is None:
            out.append({"what": "?", "rows": []})
            shapes.append((None, None))
            continue
        if it.get("kind") == "body":
            info = _body_info(body)
        elif it.get("kind") == "face":
            info = _face_info(shape)
        else:
            info = _edge_info(shape)
        info["name"] = it.get("name") or it.get("label") or it.get("body")
        out.append(info)
        shapes.append((shape, info))
    pair = None
    if len(shapes) == 2 and all(s is not None for s, _ in shapes):
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        (a, ia), (b, ib) = shapes
        d = BRepExtrema_DistShapeShape(a, b)
        rows = []
        if d.IsDone():
            rows.append(["Minimum distance", d.Value(), "mm"])
            p, q = d.PointOnShape1(1), d.PointOnShape2(1)
            dv = np.array([q.X() - p.X(), q.Y() - p.Y(), q.Z() - p.Z()])
            if d.Value() > 1e-9:                         # touching: the components say nothing
                rows.append(["ΔX, ΔY, ΔZ", ", ".join(f"{v:.3f}" for v in dv), "mm"])
        va, vb = ia.get("normal") or ia.get("dir"), ib.get("normal") or ib.get("dir")
        if va is not None and vb is not None:
            c = abs(float(np.dot(va, vb)))
            ang = math.degrees(math.acos(min(1.0, c)))
            if ("normal" in ia) != ("normal" in ib):      # a line against a plane: angle to the plane
                ang = 90 - ang
            rows.append(["Angle", ang, "°"])
        if "center" in ia and "center" in ib:
            rows.append(["Center to center", float(np.linalg.norm(np.array(ia["center"]) - np.array(ib["center"]))), "mm"])
        pair = {"rows": rows}
    elif len(shapes) > 2:
        edges = [i for (s, i) in shapes if i and i.get("what") in ("Straight edge", "Circle", "Arc", "Edge")]
        faces = [i for (s, i) in shapes if i and "face" in i.get("what", "").lower()]
        rows = []
        if len(edges) == len(shapes):
            tot = 0.0
            for (s, i) in shapes:
                tot += topo.edge_length(s)
            rows.append(["Total length", tot, "mm"])
        if len(faces) == len(shapes):
            rows.append(["Total area", sum(i["rows"][0][1] for i in faces), "mm²"])
        pair = {"rows": rows} if rows else None
    return {"items": out, "pair": pair}
