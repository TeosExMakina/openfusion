"""Operations. Each public function records a Feature; the matching `_x`
function runs it against a Ctx during evaluation."""
from __future__ import annotations

import functools

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.collections import List_TopoDS_Shape
from OCP.gp import gp_Vec

from . import topo
from .body import (Body, Design, FaceName, NameError_, ROLES, heal_slivers, propagate_names, sort_key_local,
                   unify_faces)
from .document import Feature, caller_line, current_doc
from .geom import Frame, rotation_about, translation, unit
from .primitives import BUILDERS
from .refs import (FaceRef, FrameRef, GroupHandle, Handle, RefError, WorldRef)


def _record(kind: str):
    """Turn `_impl(ctx, ...)` into a public API function that records a Feature."""

    def deco(impl):
        name = impl.__name__.lstrip("_")

        @functools.wraps(impl)
        def api(*args, **kwargs):
            doc = current_doc()
            feat = doc.add(Feature(name, impl, args, kwargs, kind, caller_line()))
            if kind == "create":
                return Handle(feat)
            if kind == "group":
                return GroupHandle(feat, args[0] if args else kwargs["body"])
            target = kwargs.get("body") or (args[0] if args else kwargs.get("target") or kwargs.get("at"))
            if isinstance(target, (list, tuple)) and target:
                target = target[0]
            if isinstance(target, FrameRef) and target.owner() is not None:
                target = target.owner()
            if isinstance(target, FaceRef):
                target = target.handle
            if isinstance(target, Handle):
                return Handle(feat, target)
            return Handle(feat)

        api.__doc__ = impl.__doc__
        api.__name__ = name
        return api

    return deco


def _role(role):
    if role not in ("solid", "reference"):
        raise ValueError("role must be 'solid' or 'reference' at creation")
    return role


def _creation_frame(ctx, at, spin, height=None, shift_y=0.0):
    """The frame a primitive is built in. A negative height turns the frame over
    (180 degrees about its X axis), so the primitive grows the other way, e.g. into
    the face it sits on. shift_y keeps a corner-based footprint where it was drawn."""
    fr = ctx.frame(at)
    fr = fr.spin(spin) if spin else fr
    if height is not None and height < 0:
        fr = Frame(fr.origin + shift_y * fr.y, fr.x, -fr.z)
    return fr


# ===================================================================== primitives
@_record("create")
def _box(ctx, length, width, height, origin="corner", at=None, spin=0.0, role="solid"):
    """Box in the frame `at` (default world origin). length = local X, width = Y, height = Z.
    origin: 'corner' (default), 'center' or 'center_xy'.
    A negative height builds the box the other way (its frame turned over), keeping the footprint."""
    shift = width if origin == "corner" else 0.0
    ctx.put(BUILDERS["box"](ctx.fid, _creation_frame(ctx, at, spin, height, shift), _role(role), length, width,
                            abs(height), origin))


@_record("create")
def _cylinder(ctx, r, h, at=None, spin=0.0, role="solid"):
    """Cylinder on the frame `at`; axis = its Z, angle 0 = its X (rotate with spin=).
    A negative h builds it the other way (its frame turned over), e.g. into a face to cut a hole."""
    ctx.put(BUILDERS["cylinder"](ctx.fid, _creation_frame(ctx, at, spin, h), _role(role), r, abs(h)))


@_record("create")
def _cone(ctx, r1, r2, h, at=None, spin=0.0, role="solid"):
    """Cone: r1 at the base (z=0), r2 at the top (z=h)."""
    ctx.put(BUILDERS["cone"](ctx.fid, _creation_frame(ctx, at, spin, h), _role(role), r1, r2, abs(h)))


@_record("create")
def _sphere(ctx, r, at=None, spin=0.0, role="solid"):
    """Sphere centered on `at`. Longitude 0 = its X, poles on its Z."""
    ctx.put(BUILDERS["sphere"](ctx.fid, _creation_frame(ctx, at, spin), _role(role), r))


@_record("create")
def _torus(ctx, R, r, at=None, spin=0.0, role="solid"):
    """Torus around the Z of `at`. theta = angle about Z from X; phi = angle around the tube."""
    ctx.put(BUILDERS["torus"](ctx.fid, _creation_frame(ctx, at, spin), _role(role), R, r))


@_record("create")
def _coil(ctx, R, turns, pitch, size, section="circle", hand="right", at=None, spin=0.0, role="solid"):
    """Helical coil rising along Z, starting at angle 0. size = section diameter or side."""
    ctx.put(BUILDERS["coil"](ctx.fid, _creation_frame(ctx, at, spin, pitch), _role(role), R, turns, abs(pitch), size,
                             section, hand))


@_record("create")
def _polygon(ctx, n, h, r=None, a=None, at=None, spin=0.0, role="solid"):
    """Regular n-sided prism. r = circumradius (v0 at angle 0) or a = apothem (s0 midpoint at angle 0)."""
    ctx.put(BUILDERS["polygon"](ctx.fid, _creation_frame(ctx, at, spin, h), _role(role), n, abs(h), r, a))


# ===================================================================== placement
def _owner_body(ctx, ref):
    if isinstance(ref, Handle):
        return ctx.body(ref)
    owner = ref.owner() if isinstance(ref, FrameRef) else None
    if owner is None:
        raise ValueError(f"{ref!r} does not belong to a body, so nothing can be moved")
    return ctx.body(owner)


@_record("modify")
def _place(ctx, source, onto=None, mode="mate", gap=0.0, angle=0.0, offset=(0.0, 0.0)):
    """Move the body owning `source` so that anchor lands on `onto`.

    mode 'mate'  : normals opposed (stack a part on a face)  [default]
    mode 'flush' : normals the same (tops level, tool sunk into a part)
    mode 'axis'  : same as flush; reads better for shafts in holes
    gap moves along the target normal, angle spins about it, offset shifts in its X/Y.
    Bodies passed instead of anchors use source '-Z' onto target '+Z'.
    """
    if isinstance(source, Handle):
        source = source.anchor("-Z")
    if isinstance(onto, Handle):
        onto = onto.anchor("+Z")
    body = _owner_body(ctx, source)
    if isinstance(onto, FrameRef) and onto.owner() is not None and onto.owner().body_id == body.id:
        raise ValueError("cannot place a body onto itself")
    if mode not in ("mate", "flush", "axis"):
        raise ValueError("mode must be 'mate', 'flush' or 'axis'")
    t = ctx.frame(onto, WorldRef("bed"))
    ox, oy = (list(offset) + [0.0, 0.0])[:2]
    t = t.offset(ox, oy, gap)
    if angle:
        t = t.spin(angle)
    want = Frame(t.origin, t.x, -t.z) if mode == "mate" else t
    have = ctx.frame(source)
    m = want.matrix() @ np.linalg.inv(have.matrix())
    ctx.put(body.moved(m, ctx.fid))


@_record("modify")
def _place_on_bed(ctx, body, face="-Z"):
    """Print orientation: put the named face's design anchor down on the bed at the origin."""
    b = ctx.body(body)
    src = b.anchor(face)
    t = WorldRef("bed").frame(ctx)
    want = Frame(t.origin, t.x, -t.z)
    m = want.matrix() @ np.linalg.inv(src.matrix())
    ctx.put(b.moved(m, ctx.fid))


@_record("modify")
def _move(ctx, body, dx=0.0, dy=0.0, dz=0.0, rx=0.0, ry=0.0, rz=0.0, frame="local", along=None, pivot=None,
          to=None, point=None):
    """Move a body: rotate, then translate (like Fusion's Move).
    Axes: frame='local' (default) uses the body's own axes, or the anchor `along`;
    frame='world' uses world X/Y/Z.
    rx, ry, rz: degrees about those axes through `pivot` (an anchor; default the body origin),
    applied X, then Y, then Z. dx, dy, dz: distance along those axes.
    Point to point: to= an anchor, snap point or (x, y, z); the body moves so `point`
    (an anchor; default the body origin) lands there. dx/dy/dz then add an offset."""
    b = ctx.body(body)
    if frame not in ("local", "world"):
        raise ValueError("frame must be 'local' or 'world'")
    f = (ctx.frame(along) if along is not None else b.frame) if frame == "local" else Frame.world()
    c = ctx.frame(pivot).origin if pivot is not None else b.frame.origin
    m = np.eye(4)
    for ang, ax in ((rx, f.x), (ry, f.y), (rz, f.z)):
        if ang:
            m = rotation_about(c, ax, float(ang)) @ m
    v = float(dx) * f.x + float(dy) * f.y + float(dz) * f.z
    if to is not None:
        target = np.asarray(to, float) if isinstance(to, (tuple, list)) else ctx.frame(to).origin
        src = ctx.frame(point).origin if point is not None else b.frame.origin
        src = (m @ np.append(src, 1.0))[:3]
        v = v + (target - src)
    ctx.put(b.moved(translation(v) @ m, ctx.fid))


@_record("modify")
def _rotate(ctx, body, angle, about=None, axis="Z", frame="local"):
    """Rotate a body by `angle` degrees (right-hand rule).
    about: an anchor; the rotation axis is its Z through its origin.
    Otherwise axis 'X'/'Y'/'Z' of the body frame (local, default) or world."""
    b = ctx.body(body)
    if about is not None:
        f = ctx.frame(about)
        m = rotation_about(f.origin, f.z, angle)
    else:
        f = b.frame if frame == "local" else Frame.world()
        d = {"X": f.x, "Y": f.y, "Z": f.z}[axis.upper()]
        m = rotation_about(f.origin, d, angle)
    ctx.put(b.moved(m, ctx.fid))


@_record("create")
def _copy(ctx, body):
    """Independent copy with the same names and anchors."""
    ctx.put(ctx.body(body).renamed_copy(ctx.fid, ctx.fid))


# ===================================================================== patterns / mirror / scale
def _count_step(spec, axis):
    if spec is None:
        return 1, 0.0
    if not isinstance(spec, (tuple, list)) or len(spec) != 2:
        raise ValueError(f"{axis} must be (count, spacing)")
    n, d = spec
    if int(n) != n or n < 1:
        raise ValueError(f"{axis} count must be a positive integer")
    return int(n), float(d)


def _is_line_ref(ref) -> bool:
    from .refs import EdgeBetweenRef, EdgeRef
    return isinstance(ref, (EdgeRef, EdgeBetweenRef, FaceRef))


def _sense(d, frame):
    """Give a direction its right-handed sense: it points the positive way of whichever
    axis of the owning body it most nearly follows (an edge parallel to the block's X
    points +X). Negative spacings and angles then flip it."""
    d = unit(np.asarray(d, float))
    dots = [float(np.dot(d, a)) for a in (frame.x, frame.y, frame.z)]
    k = max(range(3), key=lambda i: (round(abs(dots[i]), 6), -i))
    return d if dots[k] >= 0 else -d


def ref_line(ctx, ref, what="direction", normals=None):
    """(point, unit direction) from a straight edge, a circular edge (its axis) or a
    round face (its axis), with the sense given by _sense on the edge's own body.
    If `normals` is a list, the outward normals of the flat faces along the edge are added to it."""
    if isinstance(ref, FaceRef):
        body, faces = ref.resolve(ctx)
        ax = topo.surface_axis(faces[0])
        if ax is None:
            raise RefError(f"{what}: {ref!r} is flat; pick an edge, or a round face for its axis")
        pt, d = ax
    else:
        body, edges = ref.resolve(ctx)
        e = edges[0]
        kind = topo.edge_kind(e)
        if kind == "line":
            a, b = topo.edge_endpoints(e)
            pt, d = a, b - a
        elif kind == "circle":
            pt, _, d = topo.circle_center_radius(e)
        else:
            raise RefError(f"{what}: {ref!r} is curved; pick a straight edge or a circle")
        if normals is not None:
            for f in topo.edge_face_map(body.shape).get(e) or []:
                if topo.surface_kind(f) == "plane":
                    normals.append(topo.plane_normal(f))
    return np.asarray(pt, float), _sense(d, body.frame)


@_record("group")
def _pattern_rect(ctx, body, x=None, y=None, z=None, frame="local", along=None, y_along=None, z_along=None):
    """Rectangular pattern of a body. x/y/z = (count, spacing); negative spacing goes the other way.
    Directions follow the body's axes (default), the world (frame='world'), an anchor
    (along=anchor: its X/Y/Z), or edges of another body:
      along=edge     x runs along the edge; y is the right-hand perpendicular on the face the
                     edge borders that the seed sits on (y = face normal x edge), z = x cross y
      y_along=edge   y runs along this edge instead (need not be perpendicular)
      z_along=edge   z runs along this edge
    A straight edge points the positive way of the axis of its body it most nearly
    follows; a circular edge or round face gives its axis.
    Returns a group: pattern[0] is the seed, pattern[1..] the copies."""
    b = ctx.body(body)
    if frame not in ("local", "world"):
        raise ValueError("frame must be 'local' or 'world'")
    if along is not None and _is_line_ref(along):
        normals: list = []
        _, dx = ref_line(ctx, along, "along", normals)
        # "up" is the outward normal of the face the edge borders that the seed stands on
        # (the one most parallel to the seed's Z, whichever way the seed itself points)
        up = max(normals, key=lambda n: abs(float(np.dot(n, b.frame.z)))) if normals else b.frame.z
        up = up - np.dot(up, dx) * dx
        if np.linalg.norm(up) < 1e-6:          # runs along the edge: fall back to the seed's axes
            up = b.frame.z - np.dot(b.frame.z, dx) * dx
            if np.linalg.norm(up) < 1e-6:
                up = b.frame.x - np.dot(b.frame.x, dx) * dx
        dz = unit(up)
        dy = np.cross(dz, dx)
    else:
        f = ctx.frame(along) if along is not None else (b.frame if frame == "local" else Frame.world())
        dx, dy, dz = f.x, f.y, f.z
    if y_along is not None:
        _, dy = ref_line(ctx, y_along, "y_along")
        if np.linalg.norm(np.cross(dx, dy)) < 1e-6:
            raise ValueError("y_along runs parallel to the x direction; pick an edge that crosses it")
        if z_along is None:
            dz = unit(np.cross(dx, dy))
    if z_along is not None:
        _, dz = ref_line(ctx, z_along, "z_along")
    (nx, sx), (ny, sy), (nz, sz) = _count_step(x, "x"), _count_step(y, "y"), _count_step(z, "z")
    ctx.info = {"kind": "rect", "seed": b.id, "origin": [float(c) for c in b.frame.origin],
                "dirs": [{"d": [float(c) for c in d], "n": n, "s": sp}
                         for d, n, sp in ((dx, nx, sx), (dy, ny, sy), (dz, nz, sz))]}
    ids, k = [b.id], 0
    for i in range(nx):
        for j in range(ny):
            for l in range(nz):
                if i == j == l == 0:
                    continue
                k += 1
                v = i * sx * dx + j * sy * dy + l * sz * dz
                nb = b.renamed_copy(f"{ctx.fid}_{k}", ctx.fid).moved(translation(v), ctx.fid)
                ctx.put(nb)
                ids.append(nb.id)
    ctx.groups[ctx.fid] = ids


@_record("group")
def _pattern_circ(ctx, body, count, axis=None, angle=360.0):
    """Circular pattern. axis: an anchor (about its Z through its origin; default world Z),
    a straight edge (about that line), or a circular edge / round face (about its axis).
    Turns follow the right-hand rule about the axis; a negative angle turns the other way.
    angle=360 spreads count copies evenly; otherwise first to last span `angle`."""
    b = ctx.body(body)
    if int(count) != count or count < 2:
        raise ValueError("count must be an integer >= 2")
    count = int(count)
    if axis is not None and _is_line_ref(axis):
        origin, d = ref_line(ctx, axis, "axis")
    else:
        f = ctx.frame(axis, WorldRef("origin"))
        origin, d = f.origin, f.z
    full = abs(abs(angle) - 360.0) < 1e-9
    step = angle / count if full else angle / (count - 1)
    ctx.info = {"kind": "circ", "seed": b.id, "origin": [float(c) for c in origin], "axis": [float(c) for c in d],
                "angle": float(angle), "step": float(step), "seed_origin": [float(c) for c in b.frame.origin]}
    ids = [b.id]
    for k in range(1, count):
        m = rotation_about(origin, d, step * k)
        nb = b.renamed_copy(f"{ctx.fid}_{k}", ctx.fid).moved(m, ctx.fid)
        ctx.put(nb)
        ids.append(nb.id)
    ctx.groups[ctx.fid] = ids


@_record("create")
def _mirror(ctx, body, plane="YZ"):
    """Mirrored copy. plane: an anchor (its XY plane) or 'XY'/'XZ'/'YZ' world planes.
    The copy is named as if built from scratch: right-handed, X and Z names match the original."""
    b = ctx.body(body)
    p = ctx.frame(plane)
    ctx.put(b.mirrored_copy(ctx.fid, p, ctx.fid))


@_record("modify")
def _scale(ctx, body, factor, about=None):
    """Scale a body. factor: number, or (sx, sy, sz) along the body's own axes.
    about: anchor to scale around (default the body origin). Non-uniform scaling turns faces into B-splines."""
    b = ctx.body(body)
    s = np.array(factor if isinstance(factor, (tuple, list)) else (factor,) * 3, float)
    if np.any(s <= 0):
        raise ValueError("scale factors must be positive")
    c = b.frame.to_local(ctx.frame(about).origin) if about is not None else np.zeros(3)
    a = translation(c) @ np.diag([s[0], s[1], s[2], 1.0]) @ translation(-c)
    ctx.put(b.scaled_local(a, ctx.fid))


# ===================================================================== booleans
def _single_solid(shape):
    sols = topo.solids(shape)
    if len(sols) == 1:
        return sols[0], 1
    return shape, len(sols)


# Booleans treat geometry closer than this as touching (Fusion's modeller is similarly tolerant).
# It absorbs the tiny differences left by smoothed loft sections and healed fillet slivers;
# 0.0002 mm is far below anything a printer or a STEP consumer cares about.
FUZZY = 2e-4


def _boolean(op, target, tools, fid):
    algo = {"join": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}[op]()
    args, tls = List_TopoDS_Shape(), List_TopoDS_Shape()
    args.Append(target.shape)
    for t in tools:
        tls.Append(t.shape)
    algo.SetArguments(args)
    algo.SetTools(tls)
    algo.SetRunParallel(True)
    algo.SetFuzzyValue(FUZZY)
    # leave the inputs alone: by default the kernel edits them in place, which changes bodies
    # the engine has cached for other steps, and can blow a tolerance up to tens of millimetres
    algo.SetNonDestructive(True)
    algo.Build()
    if not algo.IsDone():
        raise RuntimeError(f"{op} failed in the geometry kernel")
    return algo


@_record("modify")
def _combine(ctx, target, tools, op="join", keep_tools=False):
    """Join, cut or intersect `tools` into `target`. The target keeps its identity and names.
    keep_tools: False (default) = tools are Removed (their design anchors stay usable),
    True = tools stay as solids, 'reference' = tools become reference bodies."""
    if op not in ("join", "cut", "intersect"):
        raise ValueError("op must be 'join', 'cut' or 'intersect'")
    t = ctx.body(target)
    tl = [x for x in ctx.bodies(tools) if x.id != t.id]
    if not tl:
        raise ValueError("no tool bodies")
    algo = _boolean(op, t, tl, ctx.fid)
    shape, nsol = _single_solid(algo.Shape())
    if nsol == 0:
        raise RuntimeError(f"{op} produced no solid (do the bodies overlap?)")
    if nsol > 1:
        ctx.warn(f"result has {nsol} separate solids")
    v0, v1 = topo.volume(t.shape), topo.volume(shape)
    if op == "cut" and abs(v0 - v1) < 1e-9 * max(1.0, v0):
        ctx.warn("the tools do not overlap the target, nothing was removed "
                 "(face anchors point outward: use .flipped() to grow a cutter into a face)")
    if op == "join" and abs(v1 - v0 - sum(topo.volume(x.shape) for x in tl)) < 1e-9 * max(1.0, v1) and nsol > 1:
        ctx.warn("the tools do not touch the target")
    inputs = list(t.face_names) + [fn for x in tl for fn in x.face_names]
    names = propagate_names(algo, inputs, shape, t.frame, t.id)
    if nsol == 1:
        shape, names = unify_faces(shape, names, t.frame, t.id)
    ctx.put(t.evolve(shape=shape, face_names=names, touched_by=ctx.fid))
    for x in tl:
        if keep_tools is True:
            continue
        role = "reference" if keep_tools == "reference" else "removed"
        ctx.put(x.evolve(role=role, touched_by=ctx.fid))


def _boolean_op(op):
    def impl(ctx, target, tools, keep_tools=False):
        _combine.__wrapped__(ctx, target, tools, op=op, keep_tools=keep_tools)
    impl.__name__ = "_" + op
    impl.__doc__ = {"cut": "Subtract tools from target (see combine).",
                    "intersect": "Keep only the volume shared with the tools (see combine).",
                    "join": "Add tools to target (see combine)."}[op]
    return _record("modify")(impl)


cut, intersect, join = _boolean_op("cut"), _boolean_op("intersect"), _boolean_op("join")
combine = _combine


# ===================================================================== fillet / chamfer / extrude
def _edges_tolerant(ctx, b, items):
    """Resolve edges; an edge whose two faces still exist but no longer meet was already
    rounded by an earlier fillet chain: report it and skip it instead of failing."""
    from .body import split_edge_name
    from .refs import EdgeRef
    if not isinstance(items, (list, tuple)):
        items = [items]
    out, skipped = [], []
    for it in items:
        name = it if isinstance(it, str) else (it.name if isinstance(it, EdgeRef) else None)
        try:
            es = ctx.edges(b, [it])
        except RefError:
            if name is None:
                raise
            try:
                fa, fb = split_edge_name(name)
            except NameError_:
                raise RefError(f'{b.id}.edge("{name}") not found')
            if b.faces_named(fa) and b.faces_named(fb):
                skipped.append(name)
                continue
            raise
        out += [e for e in es if not any(e.IsSame(x) for x in out)]
    return out, skipped


# When two roundings meet exactly (a 1 mm wall with r=0.5 on both edges), the face between them
# shrinks to zero height, which a B-rep cannot hold. Retrying a hair smaller leaves an invisible
# sliver instead: 0.00001 mm, far below any printer's resolution.
EXACT_FIT = 1e-5


def _shrink(r):
    return float(r) - min(EXACT_FIT, float(r) * 1e-3)


def _make_fillet(shape, pairs):
    for attempt in (0, 1):
        mk = BRepFilletAPI_MakeFillet(shape)
        for e, r in pairs:
            mk.Add(float(r) if attempt == 0 else _shrink(r), e)
        try:
            mk.Build()
            if mk.IsDone() and topo.is_valid(mk.Shape()):
                return mk
        except Exception:
            pass
    return None


@_record("modify")
def _fillet(ctx, body, edges, r):
    """Round edges with radius r. edges: edge refs or names such as '+X+Z', 'rim+Z',
    or body.edges() for every edge. Smoothly continuing edges are rounded with them.
    Fillets can be added in any order: if OCCT cannot add this one on top of earlier
    fillets on the same body, they are rebuilt together as one combined fillet."""
    if not r > 0:
        raise ValueError("fillet radius must be positive")
    b = ctx.body(body)
    es, skipped = _edges_tolerant(ctx, b, edges)
    for s in skipped:
        ctx.warn(f'edge "{s}" was already rounded by an earlier fillet (fillets follow smooth chains); skipped')
    if not es:
        ctx.warn("nothing left to round")
        return
    efmap = topo.edge_face_map(b.shape)
    labels = [b.edge_label(e, efmap) for e in es]
    prior = b.rounds if (b.rounds is not None and b.rounds[1] is b.shape) else None
    base, items = (prior[0], prior[2]) if prior else (b, ())
    record = tuple(items) + tuple((lbl, r, ctx.fid) for lbl in labels)
    mk = _make_fillet(b.shape, [(e, r) for e in es])
    if mk is not None:
        gen = [(e, b.id, f"{ctx.fid}({lbl})") for e, lbl in zip(es, labels)]
        shape, _ = _single_solid(mk.Shape())
        names = propagate_names(mk, list(b.face_names), shape, b.frame, b.id, gen)
    else:
        # rebuild every consecutive fillet on this body as one operation on the body before them
        allitems = list(items) + [(lbl, r, ctx.fid) for lbl in labels]
        pairs, gen, ok = [], [], prior is not None
        for lbl, rad, fid in allitems if ok else []:
            be = base.edges_named(lbl) if "(" not in lbl else []
            if not be:
                ok = False
                break
            for e in be:
                pairs.append((e, rad))
                gen.append((e, base.id, f"{fid}({lbl})"))
        mk = _make_fillet(base.shape, pairs) if ok else None
        if mk is None:
            shown = ", ".join(labels[:6]) + ("…" if len(labels) > 6 else "")
            raise RuntimeError(f"fillet r={r} failed on {shown}: the radius is probably too large for an adjacent "
                               "face, or these edges meet a rounded corner that was made in a different way "
                               "(then try rounding all the edges in one fillet step).")
        shape, _ = _single_solid(mk.Shape())
        names = propagate_names(mk, list(base.face_names), shape, base.frame, base.id, gen)
    if not topo.is_valid(shape):
        raise RuntimeError(f"fillet r={r} produced a broken solid: the radius is too large for these edges "
                           "(it must be smaller than the faces it rounds)")
    shape, names = heal_slivers(shape, names, b.frame, b.id)
    ctx.put(b.evolve(shape=shape, face_names=names, rounds=(base, shape, record), touched_by=ctx.fid))


@_record("modify")
def _chamfer(ctx, body, edges, d, d2=None):
    """Bevel edges by distance d (or d along the first face and d2 along the second)."""
    b = ctx.body(body)
    es, skipped = _edges_tolerant(ctx, b, edges)
    for s in skipped:
        ctx.warn(f'edge "{s}" was already bevelled or rounded by an earlier step; skipped')
    if not es:
        ctx.warn("nothing left to chamfer")
        return
    efmap = topo.edge_face_map(b.shape)
    mk, ok = None, False
    for attempt in (0, 1):
        dd = float(d) if attempt == 0 else _shrink(d)
        dd2 = None if d2 is None else (float(d2) if attempt == 0 else _shrink(d2))
        mk = BRepFilletAPI_MakeChamfer(b.shape)
        for e in es:
            if dd2 is None:
                mk.Add(dd, e)
            else:
                mk.Add(dd, dd2, e, efmap[e][0])
        try:
            mk.Build()
            ok = mk.IsDone() and topo.is_valid(mk.Shape())
        except Exception:
            ok = False
        if ok:
            break
    if not ok:
        raise RuntimeError(f"chamfer d={d} failed; the distance may be too large")
    gen = [(e, b.id, f"{ctx.fid}({b.edge_label(e, efmap)})") for e in es]
    shape, _ = _single_solid(mk.Shape())
    if not topo.is_valid(shape):
        raise RuntimeError(f"chamfer d={d} produced a broken solid: the distance is too large for these edges")
    names = propagate_names(mk, list(b.face_names), shape, b.frame, b.id, gen)
    shape, names = heal_slivers(shape, names, b.frame, b.id)
    ctx.put(b.evolve(shape=shape, face_names=names, touched_by=ctx.fid))


class FixedDesign(Design):
    kind = "extrude"

    def __init__(self, frames: dict):
        self.frames = frames

    def names(self):
        return ["origin"] + list(self.frames)

    def get(self, name, params):
        if name == "origin":
            return Frame.world()
        if name in self.frames:
            return self.frames[name]
        raise NameError_(f"no design anchor '{name}'")


@_record("modify")
def _extrude(ctx, face, distance, op="join", target=None, symmetric=False):
    """Extrude a face along its outward normal. Negative distance goes inward.
    op 'join' (default) adds to the body, 'cut' removes, 'new' makes a separate body named after this feature.
    A sketch or text (or one region, sketch1.face("...")) extrudes along its Z into `target`
    (default: the body it was drawn on; with none, a new body); a negative distance pushes it
    into that body and cuts (engraves); the sketch itself means its 'profile' (the outline with
    its holes). symmetric=True goes distance/2 each way.
    New faces: '<feature>.end' (cap), '<feature>.start', '<feature>.side(<edge or sketch curve>)'."""
    from .refs import Handle
    if op not in ("join", "cut", "new"):
        raise ValueError("op must be 'join', 'cut' or 'new'")
    if distance == 0:
        raise ValueError("distance must not be zero")
    if isinstance(face, Handle):
        b = ctx.body(face, allow_removed=True)
        if b.kind != "profile":
            raise TypeError("extrude needs a face, e.g. plate.face('+Z'), or a sketch")
        faces = b.faces_named("profile") or topo.faces(b.shape)
        if not faces:
            raise ValueError(f"{b.id} has no closed region to extrude")
    elif isinstance(face, FaceRef):
        b, faces = face.resolve(ctx)
    else:
        raise TypeError("extrude needs a face, e.g. plate.face('+Z'), or a sketch")
    sheet = b.kind == "profile"
    curve_of = topo.ShapeDict()
    if sheet:
        for e, n in dict(b.params).get("edges", ()):
            curve_of[e] = n
        tgt = target if target is not None else dict(b.params).get("owner")
        if op == "join" and distance < 0 and tgt is not None:
            op = "cut"                   # pushed into the body under it: engrave, like a face pushed in
        if tgt is None and op != "new":
            if op == "cut":
                raise ValueError("extrude op='cut' from a sketch needs target=<body> (the sketch isn't on a body)")
            op = "new"
    efmap = topo.edge_face_map(b.shape)
    prisms = []
    d = float(distance)
    for f in faces:
        if sheet:
            n = np.asarray(b.frame.z, float)
        else:
            n = topo.plane_normal(f) if topo.surface_kind(f) == "plane" else None
        if n is None:
            raise ValueError("extrude currently needs a planar face")
        base = f
        if symmetric:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCP.gp import gp_Trsf
            tr = gp_Trsf()
            tr.SetTranslation(gp_Vec(*(-n * d / 2)))
            base = BRepBuilderAPI_Transform(f, tr, True).Shape()
        mk = BRepPrimAPI_MakePrism(base, gp_Vec(*(n * d)))
        mk.Build()
        p = mk.Shape()
        nm = []
        last, first = mk.LastShape(), mk.FirstShape()
        for pf in topo.faces(p):
            if pf.IsSame(last):
                nm.append((pf, FaceName(ctx.fid, "end")))
            elif pf.IsSame(first):
                nm.append((pf, FaceName(ctx.fid, "start")))
        src_edges = topo.edges(base)
        for e0, e in zip(topo.edges(f), src_edges):
            lbl = curve_of.get(e0) if sheet else b.edge_label(e0, efmap)
            for g in mk.Generated(e):
                for pf in topo.faces(p):
                    if pf.IsSame(g):
                        nm.append((pf, FaceName(ctx.fid, f"side({lbl or 'edge'})")))
        prisms.append((p, tuple(nm), f))
    if sheet:
        if op == "new":
            tgt_body = None
        else:
            tgt_body = ctx.body(tgt)
        fr0 = topo.face_area_centroid(faces[0])
        if op == "new":
            shape = prisms[0][0] if len(prisms) == 1 else _union_all([p for p, _, _ in prisms])
            names = [x for _, nm, _ in prisms for x in nm]
            start = Frame.make(fr0[1] - (n * d / 2 if symmetric else 0), b.frame.x, b.frame.z)
            design = FixedDesign({"start": Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1)),
                                  "end": Frame.make((0, 0, d))})
            ctx.put(Body(id=ctx.fid, shape=shape, frame=start, face_names=tuple(names), design=design,
                         kind="extrude", history=(ctx.fid,)))
            return
        tools = [Body(id=f"{ctx.fid}#tool{i}", shape=p, frame=b.frame, face_names=nm, design=Design())
                 for i, (p, nm, _) in enumerate(prisms)]
        algo = _boolean(op, tgt_body, tools, ctx.fid)
        shape, nsol = _single_solid(algo.Shape())
        if nsol == 0:
            raise RuntimeError(f"extrude {op} left no solid")
        if nsol > 1:
            ctx.warn(f"result has {nsol} separate solids")
        inputs = list(tgt_body.face_names) + [fn for t in tools for fn in t.face_names]
        names = propagate_names(algo, inputs, shape, tgt_body.frame, tgt_body.id)
        if nsol == 1:
            shape, names = unify_faces(shape, names, tgt_body.frame, tgt_body.id)
        ctx.put(tgt_body.evolve(shape=shape, face_names=names, touched_by=ctx.fid))
        return
    fr0 = topo.face_area_centroid(faces[0])
    n0 = topo.plane_normal(faces[0])
    if op == "new":
        start = Frame.make(fr0[1], b.anchor("origin").x, n0)
        shape = prisms[0][0] if len(prisms) == 1 else topo.compound([p for p, _, _ in prisms])
        names = [x for _, nm, _ in prisms for x in nm]
        design = FixedDesign({"start": Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1)) if distance > 0 else Frame.world(),
                              "end": Frame.make((0, 0, distance)) if distance > 0 else Frame.make((0, 0, distance), (1, 0, 0), (0, 0, -1))})
        nb = Body(id=ctx.fid, shape=shape, frame=start, face_names=tuple(names), design=design, kind="extrude",
                  history=(ctx.fid,))
        ctx.put(nb)
        return
    tools = [Body(id=f"{ctx.fid}#tool{i}", shape=p, frame=b.frame, face_names=nm, design=Design())
             for i, (p, nm, _) in enumerate(prisms)]
    eff = "cut" if (op == "cut" or distance < 0) else "join"
    algo = _boolean(eff, b, tools, ctx.fid)
    shape, nsol = _single_solid(algo.Shape())
    if nsol == 0:
        raise RuntimeError("extrude removed the whole body")
    inputs = list(b.face_names) + [fn for t in tools for fn in t.face_names]
    names = propagate_names(algo, inputs, shape, b.frame, b.id)
    if nsol == 1:
        shape, names = unify_faces(shape, names, b.frame, b.id)
    ctx.put(b.evolve(shape=shape, face_names=names, touched_by=ctx.fid))


def _union_all(shapes):
    if len(shapes) == 1:
        return shapes[0]
    fu = BRepAlgoAPI_Fuse()
    a, t = List_TopoDS_Shape(), List_TopoDS_Shape()
    a.Append(shapes[0])
    for x in shapes[1:]:
        t.Append(x)
    fu.SetArguments(a)
    fu.SetTools(t)
    fu.SetFuzzyValue(FUZZY)
    fu.SetNonDestructive(True)
    fu.Build()
    return _single_solid(fu.Shape())[0] if fu.IsDone() else topo.compound(shapes)


@_record("create")
def _split(ctx, body, plane="XY"):
    """Split a body with a plane (an anchor's XY plane, or 'XY'/'XZ'/'YZ').
    The body keeps the material behind the plane (opposite its normal); the material in
    front becomes a new body named after this feature. Cut faces are named '<feature>.cut'."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
    b = ctx.body(body)
    p = ctx.frame(plane)
    lo, hi = topo.bbox(b.shape)
    size = float(np.linalg.norm(hi - lo)) * 2 + 10
    pln = gp_Pln(gp_Ax3(gp_Pnt(*p.origin), gp_Dir(*p.z), gp_Dir(*p.x)))
    tool = BRepBuilderAPI_MakeFace(pln, -size, size, -size, size).Face()
    sp = BRepAlgoAPI_Splitter()
    args, tls = List_TopoDS_Shape(), List_TopoDS_Shape()
    args.Append(b.shape)
    tls.Append(tool)
    sp.SetArguments(args)
    sp.SetTools(tls)
    sp.SetNonDestructive(True)          # the body may be cached for other steps
    sp.Build()
    if not sp.IsDone():
        raise RuntimeError("split failed in the geometry kernel")
    res = sp.Shape()
    sols = topo.solids(res)
    back, front = [], []
    for s in sols:
        c = topo.center_of_mass(s)
        (front if float((c - p.origin) @ p.z) > 0 else back).append(s)
    if not back or not front:
        raise ValueError("the plane does not cut through the body")
    names = propagate_names(sp, list(b.face_names) + [(tool, FaceName(ctx.fid, "cut"))], res, b.frame, b.id)

    def side(solids, owner):
        shape = solids[0] if len(solids) == 1 else topo.compound(solids)
        fs = topo.ShapeSet()
        for s in solids:
            for f in topo.faces(s):
                fs.add(f)
        mine = [(f, n) for f, n in names if f in fs]
        groups = {}
        for f, n in mine:
            groups.setdefault((n.origin, n.name), []).append(f)
        out = []
        for (origin, nm), faces_ in groups.items():
            if len(faces_) == 1:
                out.append((faces_[0], FaceName(origin, nm)))
            else:
                faces_.sort(key=lambda f: sort_key_local(b.frame, topo.face_area_centroid(f)[1]))
                out += [(f, FaceName(origin, nm, i)) for i, f in enumerate(faces_)]
        return shape, tuple(out)

    shape_b, names_b = side(back, b.id)
    shape_f, names_f = side(front, ctx.fid)
    ctx.put(b.evolve(shape=shape_b, face_names=names_b, touched_by=ctx.fid))
    ctx.put(Body(id=ctx.fid, shape=shape_f, frame=b.frame, face_names=names_f, design=b.design, kind=b.kind,
                 role=b.role, local_ops=b.local_ops, custom=b.custom, mirrored=b.mirrored, params=b.params,
                 history=(ctx.fid,)))


# ===================================================================== anchors / roles
@_record("modify")
def _set_anchor(ctx, body, name, at):
    """Define or redefine a named anchor on `body` from this step on.
    `at` is any anchor or point; use .offset(), .spin(), .flipped() on it to adjust."""
    if not isinstance(name, str) or not name:
        raise ValueError("anchor name must be a non-empty string")
    b = ctx.body(body)
    ctx.put(b.with_custom_anchor(name, ctx.frame(at), ctx.fid))


@_record("modify")
def _set_role(ctx, body, role):
    """'solid', 'reference' (ghost: snappable, never in booleans or exports unless named) or 'removed'."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    b = ctx.body(body, allow_removed=True)
    ctx.put(b.evolve(role=role, touched_by=ctx.fid))


@_record("modify")
def _remove(ctx, body):
    """The body ends here. Earlier features are unaffected; its design anchors stay usable."""
    b = ctx.body(body)
    ctx.put(b.evolve(role="removed", touched_by=ctx.fid))


# public names
box, cylinder, cone, sphere, torus, coil, polygon = _box, _cylinder, _cone, _sphere, _torus, _coil, _polygon
place, place_on_bed, move, rotate, copy = _place, _place_on_bed, _move, _rotate, _copy
pattern_rect, pattern_circ, mirror, scale = _pattern_rect, _pattern_circ, _mirror, _scale
fillet, chamfer, extrude, split = _fillet, _chamfer, _extrude, _split
set_anchor, set_role, remove = _set_anchor, _set_role, _remove

# profile-based features live in features.py (they build on the helpers above)
from .features import draft, loft, profile, revolve, shell, sweep  # noqa: E402,F401  (public names)

from .sketch import sketch  # noqa: E402,F401  (constrained sketches)

from .holes import hole, thread  # noqa: E402,F401  (holes and threads)
from .text import text  # noqa: E402,F401  (lettering)
from .construct import axis, plane  # noqa: E402,F401  (construction planes and axes)
from .importer import import_file  # noqa: E402,F401  (STEP, STL, 3MF)
