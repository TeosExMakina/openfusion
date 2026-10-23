"""Bodies: a solid shape plus everything needed to name it.

A Body carries
  * its B-rep shape, in world coordinates
  * its local frame (right-handed, in world coordinates)
  * a name for every face (FaceName: which body named it, the name, an optional split index)
  * its design anchors: a Design object that computes anchor frames from the
    creating feature's parameters, in local coordinates
  * a chain of local operations (mirror, scale) applied after the design formulas
  * custom anchors set with set_anchor
  * a role: solid, reference or removed

Bodies are immutable: every operation returns a new Body. That makes the
per-feature cache trivially safe.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

import numpy as np

from . import topo
from .topo import _face
from OCP.TopAbs import TopAbs_FACE
from .geom import Frame

SIGN_FACE = re.compile(r"^[+-][XYZ]$")
SIGN_PAIR = re.compile(r"^([+-][XYZ])([+-][XYZ])$")
SIGN_TRIPLE = re.compile(r"^[+-]{3}$")
AXIS_ORDER = {"X": 0, "Y": 1, "Z": 2}

ROLES = ("solid", "reference", "removed")


class NameError_(LookupError):
    """A name could not be resolved on a body."""


@dataclass(frozen=True)
class FaceName:
    origin: str          # id of the body (or feature) that named the face
    name: str            # e.g. "+Z", "side", "fil1(+X+Z)"
    piece: int | None = None

    def label(self, owner: str) -> str:
        base = self.name if self.origin == owner else f"{self.origin}.{self.name}"
        return base if self.piece is None else f"{base}#{self.piece}"


# ---------------------------------------------------------------- designs
class Design:
    """Design anchors for a body, in its local frame. Subclassed per primitive."""

    kind = "generic"

    def names(self) -> list[str]:
        return ["origin"]

    def get(self, name: str, params: dict) -> Frame:
        if name == "origin":
            return Frame.world()
        raise NameError_(f"no design anchor '{name}'")

    # Mirror support: a mirrored body is its source reflected in local Y
    # (see Body.mirrored_copy). Names that mention Y, or count around Z,
    # change; everything else keeps its name.
    def mirror_name(self, name: str) -> str:
        return generic_mirror_name(name)

    def mirror_params(self, name: str, params: dict) -> dict:
        p = dict(params)
        for k in ("angle", "theta", "lon"):
            if k in p:
                p[k] = -p[k]
        return p


def generic_mirror_name(name: str) -> str:
    if SIGN_TRIPLE.match(name):
        return name[0] + ("-" if name[1] == "+" else "+") + name[2]
    return re.sub(r"([+-])Y", lambda m: ("-" if m.group(1) == "+" else "+") + "Y", name)


@dataclass(frozen=True)
class LocalOp:
    """An affine map in local coordinates applied after the design formulas."""
    matrix: np.ndarray
    mirror: bool = False


# ---------------------------------------------------------------- body
@dataclass(frozen=True)
class Body:
    id: str
    shape: object
    frame: Frame
    face_names: tuple            # tuple of (TopoDS_Face, FaceName)
    design: Design
    kind: str = "generic"
    role: str = "solid"
    local_ops: tuple = ()
    custom: tuple = ()           # tuple of (name, Frame local)
    mirrored: bool = False
    params: tuple = ()           # creation parameters, for display
    history: tuple = ()          # feature names that touched this body
    # Consecutive fillets on this body: (body before the first one, the shape they produced,
    # ((edge label, radius, feature), ...)). Lets a later fillet that OCCT cannot add on top
    # be rebuilt as one combined fillet. Valid only while `shape` is still that shape.
    rounds: object = None

    # -- copies ------------------------------------------------------------
    def evolve(self, **kw) -> "Body":
        if "history" not in kw and "touched_by" in kw:
            kw["history"] = self.history + (kw.pop("touched_by"),)
        kw.pop("touched_by", None)
        return replace(self, **kw)

    # -- faces -------------------------------------------------------------
    def label_of(self, face) -> str | None:
        for f, n in self.face_names:
            if f.IsSame(face):
                return n.label(self.id)
        return None

    def parse_face(self, text: str) -> tuple[str, str, int | None]:
        piece = None
        m = re.match(r"^(.*)#(\d+)$", text)
        if m:
            text, piece = m.group(1), int(m.group(2))
        origins = {n.origin for _, n in self.face_names}
        if "." in text:
            head, tail = text.split(".", 1)
            if head in origins and head != self.id:
                return head, tail, piece
        return self.id, text, piece

    def find_faces(self, origin: str, name: str, piece: int | None = None) -> list:
        out = []
        for f, n in self.face_names:
            if n.origin == origin and n.name == name and (piece is None or n.piece == piece):
                out.append(f)
        return out

    def faces_named(self, text: str) -> list:
        return self.find_faces(*self.parse_face(text))

    def face_labels(self) -> list[str]:
        return [n.label(self.id) for _, n in self.face_names]

    # -- edges -------------------------------------------------------------
    def curves(self) -> list:
        """A sketch's curves, whole: [(name, edge, construction)] (empty for other bodies)."""
        return list(dict(self.params).get("curves", ())) if self.kind == "profile" else []

    def edge_label(self, edge, efmap=None) -> str:
        if self.kind == "profile":
            # a sketch's edges are named after its curves (also the pieces of a curve that
            # bound a region)
            for n, e, _ in self.curves():
                if edge.IsSame(e):
                    return n
            for e, n in dict(self.params).get("edges", ()):
                if edge.IsSame(e):
                    return n
        efmap = efmap or topo.edge_face_map(self.shape)
        fs = efmap.get(edge, [])
        labels = [self.label_of(f) or "?" for f in fs]
        return edge_label_from_faces(labels)

    def edges_between(self, faces_a: list, faces_b: list) -> list:
        efmap = topo.edge_face_map(self.shape)
        out = []
        for e, fs in efmap.items():
            in_a = any(any(f.IsSame(a) for a in faces_a) for f in fs)
            in_b = any(any(f.IsSame(b) for b in faces_b) for f in fs)
            if in_a and in_b:
                same = faces_a is faces_b or (len(fs) == 1)
                if not same or len(fs) == 1:
                    out.append(e)
        return out

    def edges_named(self, text: str) -> list:
        """'+X+Z', 'rim+Z', 'a|b' -> edges shared by the two named faces; on a sketch, a curve's name."""
        for n, e, _ in self.curves():
            if n == text:
                return [e]
        a, b = split_edge_name(text)
        fa, fb = self.faces_named(a), self.faces_named(b)
        if not fa or not fb:
            return []
        return self.edges_between(fa, fb)

    def vertex_named(self, text: str):
        """'+++' style vertex name -> world point (boxes)."""
        if not SIGN_TRIPLE.match(text):
            return None
        names = [s + ax for s, ax in zip(text, "XYZ")]
        sets = [self.faces_named(n) for n in names]
        if not all(sets):
            return None
        for v in topo.vertices(self.shape):
            ok = True
            for fs in sets:
                if not any(any(v.IsSame(w) for w in topo.vertices(f)) for f in fs):
                    ok = False
                    break
            if ok:
                from OCP.BRep import BRep_Tool
                return topo.to_np(BRep_Tool.Pnt_s(v))
        return None

    # -- anchors -----------------------------------------------------------
    def anchor_local(self, name: str, params: dict | None = None) -> Frame:
        params = dict(params or {})
        for n, fr in reversed(self.custom):
            if n == name and not params:
                return fr
        # walk local ops backwards to find the design-time name
        query, qparams = name, params
        for op in reversed(self.local_ops):
            if op.mirror:
                query = self.design.mirror_name(query)
                qparams = self.design.mirror_params(query, qparams)
        fr = self.design.get(query, qparams)
        for op in self.local_ops:
            fr = fr.transformed(op.matrix)
        return fr

    def anchor(self, name: str, **params) -> Frame:
        return self.frame.frame_to_world(self.anchor_local(name, params))

    def anchor_names(self) -> list[str]:
        names = list(self.design.names())
        out = []
        for n in names:
            m = n
            for op in self.local_ops:
                if op.mirror:
                    m = self.design.mirror_name(m)
            out.append(m)
        for n, _ in self.custom:
            if n not in out:
                out.append(n)
        return out

    def has_anchor(self, name: str) -> bool:
        try:
            self.anchor_local(name)
            return True
        except NameError_:
            return False

    def with_custom_anchor(self, name: str, world: Frame, feature: str) -> "Body":
        local = self.frame.frame_to_local(world)
        return self.evolve(custom=self.custom + ((name, local),), touched_by=feature)

    # -- transforms ----------------------------------------------------------
    def moved(self, m: np.ndarray, feature: str) -> "Body":
        """Rigid motion in world coordinates."""
        shape, builder = topo.transform_shape(self.shape, m)
        return self.evolve(shape=shape, frame=self.frame.transformed(m),
                           face_names=remap_faces(self.face_names, builder, shape),
                           touched_by=feature)

    def scaled_local(self, a_local: np.ndarray, feature: str) -> "Body":
        """Affine in local coordinates (scale). Frame stays put."""
        f = self.frame.matrix()
        world = f @ a_local @ np.linalg.inv(f)
        shape, builder = topo.transform_shape(self.shape, world)
        custom = tuple((n, fr.transformed(a_local)) for n, fr in self.custom)
        return self.evolve(shape=shape, face_names=remap_faces(self.face_names, builder, shape),
                           local_ops=self.local_ops + (LocalOp(a_local),), custom=custom,
                           touched_by=feature)

    def mirrored_copy(self, new_id: str, plane: Frame, feature: str) -> "Body":
        from .geom import reflection
        m = reflection(plane)
        shape, builder = topo.transform_shape(self.shape, m)
        r = m[:3, :3]
        new_frame = Frame.make(m[:3, :3] @ self.frame.origin + m[:3, 3], r @ self.frame.x, r @ self.frame.z)
        flip = np.diag([1.0, -1.0, 1.0, 1.0])
        names = []
        for f, n in remap_faces(self.face_names, builder, shape):
            if n.origin == self.id:
                nn = FaceName(new_id, self.design.mirror_name(n.name), n.piece)
            else:
                nn = FaceName(new_id, generic_mirror_name(f"{n.origin}.{n.name}"), n.piece)
            names.append((f, nn))
        custom = tuple((n, fr.transformed(flip)) for n, fr in self.custom)
        return Body(id=new_id, shape=shape, frame=new_frame, face_names=tuple(names),
                    design=self.design, kind=self.kind, role=self.role,
                    local_ops=self.local_ops + (LocalOp(flip, mirror=True),), custom=custom,
                    mirrored=not self.mirrored, params=self.params, history=(feature,))

    def renamed_copy(self, new_id: str, feature: str) -> "Body":
        names = []
        for f, n in self.face_names:
            if n.origin == self.id:
                names.append((f, FaceName(new_id, n.name, n.piece)))
            else:
                names.append((f, FaceName(new_id, f"{n.origin}.{n.name}", n.piece)))
        return replace(self, id=new_id, face_names=tuple(names), history=(feature,))


# ---------------------------------------------------------------- naming helpers
def edge_label_from_faces(labels: list[str]) -> str:
    if len(labels) == 1:
        return f"seam({labels[0]})"
    if len(labels) != 2:
        return "|".join(sorted(labels))
    a, b = labels
    if SIGN_FACE.match(a) and SIGN_FACE.match(b):
        a, b = sorted([a, b], key=lambda s: AXIS_ORDER[s[1]])
        return a + b
    s = {a, b}
    for z in ("+Z", "-Z"):
        if s == {"side", z}:
            return "rim" + z
    return "|".join(sorted(labels))


def split_edge_name(text: str) -> tuple[str, str]:
    m = SIGN_PAIR.match(text)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^rim([+-]Z)$", text)
    if m:
        return "side", m.group(1)
    if "|" in text:
        a, b = text.split("|", 1)
        return a, b
    raise NameError_(f"cannot parse edge name '{text}' (use '+X+Z', 'rim+Z' or 'faceA|faceB')")


def remap_faces(face_names, builder, new_shape) -> tuple:
    """Carry names through a transform builder (one face -> one face). Faces are
    taken from the new shape itself so their orientation is the one in context."""
    present = topo.ShapeDict()
    for f in topo.faces(new_shape):
        present[f] = True
    out = []
    for f, n in face_names:
        img = None
        try:
            img = builder.ModifiedShape(f)
        except Exception:
            lst = builder.Modified(f)
            if lst.Size() > 0:
                img = lst.First()
        if img is not None and not img.IsNull():
            c = present.canonical(img)
            if c is not None:
                out.append((c, n))
    return tuple(out)


def sort_key_local(frame: Frame, point) -> tuple:
    p = frame.to_local(point)
    return tuple(round(float(c), 6) for c in p)


def propagate_names(algo, inputs: list[tuple], result_shape, frame: Frame, owner: str,
                    generated: list[tuple] | None = None) -> tuple:
    """Assign names to the faces of `result_shape` from an OCCT algorithm's history.

    inputs:    list of (face, FaceName) from all input bodies
    generated: list of (input_subshape, origin, name) whose generated faces get that name
    Splits become pieces ordered by position along local X, then Y, then Z.
    Unnamed faces get '<owner>.new#i'.
    """
    result_faces = topo.faces(result_shape)
    present = topo.ShapeDict()
    for f in result_faces:
        present[f] = None
    groups: dict[FaceName, list] = {}

    def claim(face, fname: FaceName):
        face = present.canonical(face)
        if face is not None and present.get(face) is None:
            present[face] = fname
            groups.setdefault(fname, []).append(face)

    for f, n in inputs:
        if algo.IsDeleted(f):
            continue
        mods = algo.Modified(f)
        if mods.Size() == 0:
            claim(f, n)
        else:
            for img in list(_iter_list(mods)):
                if img.ShapeType() == TopAbs_FACE:
                    claim(_face(img), n)
    for sub, origin, name in (generated or []):
        try:
            gen = algo.Generated(sub)
        except Exception:
            continue
        for img in _iter_list(gen):
            if img.ShapeType() == TopAbs_FACE:
                claim(_face(img), FaceName(origin, name))

    # A face that comes through whole keeps its name, piece number included, so a reference
    # to "+Z#1" keeps meaning the same face however later steps change the others. A face
    # split in two or more gets pieces in order along local X, then Y, then Z: the first keeps
    # its number (if it had one), the rest take the next numbers free for that name.
    out = []
    taken: dict[tuple, set] = {}
    split = []
    for fname, fs in groups.items():
        if len(fs) == 1:
            out.append((fs[0], fname))
            if fname.piece is not None:
                taken.setdefault((fname.origin, fname.name), set()).add(fname.piece)
        else:
            split.append((fname, fs))
    for fname, fs in split:
        fs = sorted(fs, key=lambda f: sort_key_local(frame, topo.face_area_centroid(f)[1]))
        used = taken.setdefault((fname.origin, fname.name), set())
        if fname.piece is not None and fname.piece not in used:
            used.add(fname.piece)
            out.append((fs[0], fname))
            fs = fs[1:]
        k = 0
        for f in fs:
            while k in used:
                k += 1
            used.add(k)
            out.append((f, FaceName(fname.origin, fname.name, k)))
    leftovers = [f for f, v in present.items() if v is None]
    leftovers.sort(key=lambda f: sort_key_local(frame, topo.face_area_centroid(f)[1]))
    for i, f in enumerate(leftovers):
        out.append((f, FaceName(owner, "new", i)))
    return tuple(out)


def _iter_list(lst):
    it = lst.cbegin() if hasattr(lst, "cbegin") else None
    try:
        for x in lst:
            yield x
        return
    except TypeError:
        pass
    from OCP.TopTools import TopTools_ListIteratorOfListOfShape  # pragma: no cover
    it = TopTools_ListIteratorOfListOfShape(lst)
    while it.More():
        yield it.Value()
        it.Next()


class _HistoryAdapter:
    """Give BRepTools_History the same interface as the boolean builders."""

    def __init__(self, hist):
        self.h = hist

    def IsDeleted(self, s):
        return self.h.IsRemoved(s)

    def Modified(self, s):
        return self.h.Modified(s)

    def Generated(self, s):
        return self.h.Generated(s)


# Faces narrower than this are slivers: what is left where two roundings meet exactly
# (a 1 mm wall rounded r=0.5 on both edges). Downstream steps (shell, a crossing fillet,
# lofts that start on such a face) choke on them, so they are removed and their
# neighbours joined, the way Fusion's tolerant modelling would never create them.
SLIVER = 1e-4


def heal_slivers(shape, names, frame: Frame, owner: str):
    """Remove sliver faces and the tiny edges around them, carrying names through."""
    if not any(topo.edge_length(e) < SLIVER for e in topo.edges(shape)):
        return shape, names
    from OCP.ShapeFix import ShapeFix_FixSmallFace
    try:
        fx = ShapeFix_FixSmallFace()
        fx.Init(shape)
        fx.SetPrecision(SLIVER)
        fx.SetMaxTolerance(10 * SLIVER)
        fx.Perform()
        new = fx.FixShape()
        sols = topo.solids(new)
        if len(sols) != 1 or not topo.is_valid(sols[0]):
            return shape, names
        new = sols[0]
        v0, v1 = topo.volume(shape), topo.volume(new)
        if abs(v1 - v0) > max(1e-4 * abs(v0), 1e-3) or len(topo.faces(new)) >= len(topo.faces(shape)):
            return shape, names
        hist = _HistoryAdapter(fx.Context().History())
    except Exception:
        return shape, names
    return new, propagate_names(hist, list(names), new, frame, owner)


def unify_faces(shape, names, frame: Frame, owner: str):
    """Merge coplanar / same-surface neighbours (as Fusion does after a join),
    carrying names through. When two named faces merge, the one listed first
    (the target body's own faces come first) keeps its name."""
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    # the kernel's merge sometimes gives up on arcs meeting lines ("curves not joined"): then
    # merge faces only, and if that fails too keep the faces as they are (still a valid solid)
    for edges_too in (True, False):
        try:
            u = ShapeUpgrade_UnifySameDomain(shape, edges_too, True, False)
            u.Build()
            new = u.Shape()
            break
        except Exception:
            continue
    else:
        return shape, tuple(names)
    sols = topo.solids(new)
    if len(sols) == 1:
        new = sols[0]
    return new, propagate_names(_HistoryAdapter(u.History()), list(names), new, frame, owner)
