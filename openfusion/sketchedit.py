"""Editing tools for sketches: fillet a corner, trim, offset, mirror, and patterns.

Each tool takes the sketch as the app holds it (points, curves, constraints, written the way the
script writes them) plus where everything currently is (the solved positions), and returns the
sketch changed: plain points, curves and constraints again, so the result is an ordinary
sketch(...) statement that keeps working when dimensions change:

  fillet   a corner where two lines meet becomes an arc tangent to both (radius dimensioned);
           the corner stays as a point on both lines when something else holds it
  trim     the piece of a line, arc or circle between the curves that cross it is taken out;
           the new ends stay on the curves they were cut by
  offset   lines, arcs and circles copied at a distance (chains stay joined); copies are held
           parallel at that distance (lines) or on the same centre (arcs, circles)
  mirror   curves copied across a line, every new point held symmetric to its original
  pattern  rectangular (held at the spacings) or circular (copies turned about a point)
"""
from __future__ import annotations

import math

import numpy as np

CONSTRUCTION = "construction"


class EditError(ValueError):
    pass


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _opts(v):
    """A gear's options: a dict, or (as the app holds it) its source text {"expr": "{...}"}."""
    import ast
    if isinstance(v, dict) and set(v) == {"expr"}:
        try:
            v = ast.literal_eval(v["expr"])
        except (ValueError, SyntaxError):
            raise EditError("this gear's options use expressions: change its angle in the script instead") from None
    return dict(v) if isinstance(v, dict) else {}


def _opts_out(d):
    return {"expr": "{" + ", ".join(f"{k!r}: {v!r}".replace("'", '"') for k, v in d.items()) + "}"}


class Sketch:
    def __init__(self, model: dict, solved: dict | None):
        self.points = {n: (list(v) if isinstance(v, list) else v) for n, v in (model.get("points") or {}).items()}
        self.curves = {n: list(c) for n, c in (model.get("curves") or {}).items()}
        self.cons = [list(c) for c in (model.get("cons") or model.get("constraints") or [])]
        solved = solved or {}
        self.spos = {n: np.asarray(p, float) for n, p in (solved.get("points") or {}).items()}
        self.sinfo = solved.get("curves") or {}
        self.new = []                     # keys of what was made ("c:name", "p:name")

    def out(self):
        return {"points": self.points, "curves": self.curves, "cons": self.cons, "select": self.new}

    # -- positions
    def pos(self, n):
        if n in self.spos:
            return self.spos[n]
        v = self.points.get(n)
        if isinstance(v, list):
            return np.asarray(v, float)
        raise EditError(f"don't know where {n!r} is yet")

    def scale(self):
        ps = [p for p in self.spos.values()] or [np.zeros(2)]
        return max(1.0, float(np.ptp(np.array(ps), axis=0).max()))

    def kind(self, n):
        c = self.curves.get(n)
        if c is None:
            raise EditError(f"no curve {n!r}")
        return c[0]

    def constr(self, n):
        return CONSTRUCTION in self.curves[n]

    def radius(self, n):
        c = self.curves[n]
        info = self.sinfo.get(n) or {}
        if c[0] == "circle":
            return float(info.get("radius") or (c[2] if _num(c[2]) else 1.0))
        if c[0] == "arc":
            return float(np.linalg.norm(self.pos(c[2]) - self.pos(c[1])))
        raise EditError(f"{n!r} has no radius")

    def pts_of(self, n):
        """The point names a curve is drawn through (centre first for round ones)."""
        c = self.curves[n]
        k = c[0]
        if k in ("line", "rack"):
            return [c[1], c[2]]
        if k == "arc":
            return [c[1], c[2], c[3]]
        if k in ("circle", "ellipse", "gear", "ring_gear"):
            return [c[1]]
        if k == "spline":
            return [v for v in c[1:] if isinstance(v, str) and v not in (CONSTRUCTION, "closed")]
        return []

    # -- names
    def _name(self, prefix, taken):
        k = 1
        while f"{prefix}{k}" in taken or f"{prefix}{k}" in self.points or f"{prefix}{k}" in self.curves:
            k += 1
        return f"{prefix}{k}"

    def add_point(self, p):
        n = self._name("p", self.points)
        self.points[n] = [round(float(p[0]), 4), round(float(p[1]), 4)]
        self.spos[n] = np.asarray(p, float)
        self.new.append("p:" + n)
        return n

    def add_curve(self, prefix, d):
        n = self._name(prefix, self.curves)
        self.curves[n] = d
        self.new.append("c:" + n)
        return n

    def used(self, p):
        """Is point p drawn through by a curve or named by a constraint?"""
        return any(p in self.pts_of(n) for n in self.curves) or any(p in c[1:] for c in self.cons)

    def drop_point_if_free(self, p):
        if p in self.points and isinstance(self.points[p], list) and not self.used(p):
            del self.points[p]

    def cons_on(self, curve):
        return [c for c in self.cons if curve in c[1:]]

    def find_point(self, p, exclude=()):
        tol = 1e-6 * self.scale()
        for n in list(self.points) + [n for n in self.spos if n not in self.points]:
            if n in exclude:
                continue
            try:
                q = self.pos(n)
            except EditError:
                continue
            if np.linalg.norm(q - p) < tol:
                return n
        return None


# ===================================================================== fillet
def fillet(sk: Sketch, corner: str, r: float):
    if not (_num(r) and r > 0):
        raise EditError("the fillet radius must be a number above 0")
    lines = [n for n, c in sk.curves.items() if c[0] == "line" and corner in c[1:3]]
    if len(lines) != 2:
        raise EditError("pick a corner where exactly two lines meet")
    l1, l2 = lines
    oth = [sk.curves[l][2] if sk.curves[l][1] == corner else sk.curves[l][1] for l in lines]
    V, A, B = sk.pos(corner), sk.pos(oth[0]), sk.pos(oth[1])
    u1, u2 = (A - V) / np.linalg.norm(A - V), (B - V) / np.linalg.norm(B - V)
    th = math.acos(max(-1.0, min(1.0, float(u1 @ u2))))
    if th < 1e-3 or math.pi - th < 1e-3:
        raise EditError("the two lines are in line: there is no corner to round")
    d = r / math.tan(th / 2)
    room = min(np.linalg.norm(A - V), np.linalg.norm(B - V))
    if d >= room - 1e-9:
        rmax = room * math.tan(th / 2)
        raise EditError(f"a radius of {r:g} doesn't fit on these lines (at most {rmax:.3f})")
    T1, T2 = V + u1 * d, V + u2 * d
    bis = (u1 + u2) / np.linalg.norm(u1 + u2)
    C = V + bis * (r / math.sin(th / 2))
    t1, t2, c = sk.add_point(T1), sk.add_point(T2), sk.add_point(C)
    for l, t in ((l1, t1), (l2, t2)):
        d_ = sk.curves[l]
        sk.curves[l] = [d_[0]] + [t if v == corner else v for v in d_[1:]]
    ca, cb = T1 - C, T2 - C
    arc = ["arc", c, t1, t2] if ca[0] * cb[1] - ca[1] * cb[0] > 0 else ["arc", c, t2, t1]
    if sk.constr(l1) and sk.constr(l2):
        arc.append(CONSTRUCTION)
    a = sk.add_curve("a", arc)
    # a length on either line meant up to the corner: keep that meaning (the corner stays put)
    for cn in sk.cons:
        if cn[0] == "length" and cn[1] in (l1, l2):
            other = oth[0] if cn[1] == l1 else oth[1]
            cn[:] = ["distance", corner, other, cn[2]]
    sk.cons += [["tangent", a, l1], ["tangent", a, l2], ["radius", a, r]]
    if sk.used(corner):
        sk.cons += [["on", corner, l1], ["on", corner, l2]]       # a virtual sharp corner, like Fusion's
    else:
        sk.drop_point_if_free(corner)
    return sk


# ===================================================================== intersections
def _seg_seg(a, b, c, d):
    """Params (t on a-b, s on c-d) where the segments meet, or None."""
    r, s_ = b - a, d - c
    den = r[0] * s_[1] - r[1] * s_[0]
    if abs(den) < 1e-14:
        return None
    w = c - a
    t = (w[0] * s_[1] - w[1] * s_[0]) / den
    u = (w[0] * r[1] - w[1] * r[0]) / den
    return t, u


def _line_circle(a, b, ctr, r):
    """Params t on the infinite line a + t (b - a) where it meets the circle."""
    dv = b - a
    f = a - ctr
    A, B, C = dv @ dv, 2 * (f @ dv), f @ f - r * r
    disc = B * B - 4 * A * C
    if disc < -1e-12 or A < 1e-18:
        return []
    disc = math.sqrt(max(0.0, disc))
    return sorted({(-B - disc) / (2 * A), (-B + disc) / (2 * A)})


def _circle_circle(c1, r1, c2, r2):
    d = float(np.linalg.norm(c2 - c1))
    if d < 1e-12 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, r1 * r1 - a * a))
    m = c1 + a * (c2 - c1) / d
    perp = np.array([-(c2 - c1)[1], (c2 - c1)[0]]) / d
    return [m + h * perp, m - h * perp] if h > 1e-12 else [m]


def _ang(p, ctr):
    return math.atan2(p[1] - ctr[1], p[0] - ctr[0])


def _in_arc(theta, a0, a1):
    """Is angle theta on the CCW arc from a0 to a1?"""
    span = (a1 - a0) % (2 * math.pi) or 2 * math.pi
    return (theta - a0) % (2 * math.pi) <= span + 1e-9


def _shape(sk: Sketch, n):
    """A curve as ('seg', a, b) | ('circ', centre, r, None | (a0, a1)) | ('poly', points)."""
    c = sk.curves[n]
    info = sk.sinfo.get(n) or {}
    k = info.get("kind", c[0])
    if c[0] == "line" or (c[0] == "project" and k == "line" and len(info.get("pts", [])) == 2):
        p = sk.pts_of(n) if c[0] == "line" else info["pts"]
        return ("seg", sk.pos(p[0]), sk.pos(p[1]))
    if c[0] == "circle":
        return ("circ", sk.pos(c[1]), sk.radius(n), None)
    if c[0] == "arc":
        ctr = sk.pos(c[1])
        return ("circ", ctr, sk.radius(n), (_ang(sk.pos(c[2]), ctr), _ang(sk.pos(c[3]), ctr)))
    if c[0] == "project" and k in ("circle", "arc") and info.get("centre") is not None:
        ctr = np.asarray(info["centre"], float)
        if k == "circle":
            return ("circ", ctr, float(info["radius"]), None)
        s, e = (sk.pos(q) for q in info["pts"][:2])
        return ("circ", ctr, float(info["radius"]), (_ang(s, ctr), _ang(e, ctr)))
    if info.get("samples"):
        return ("poly", [np.asarray(q, float) for q in info["samples"]])
    if c[0] == "rack":
        return ("seg", sk.pos(c[1]), sk.pos(c[2]))
    return None


def _cuts(sk: Sketch, target):
    """Where other curves cross the target: [(param, point, cutter, exact)]; the param is t
    along a line (0..1) or the angle (radians) on a circle/arc."""
    ts = _shape(sk, target)
    out = []
    for n in sk.curves:
        if n == target:
            continue
        cs = _shape(sk, n)
        if cs is None:
            continue
        exact = cs[0] != "poly" and sk.curves[n][0] in ("line", "arc", "circle", "project")
        segs = []
        if cs[0] == "seg":
            segs = [(cs[1], cs[2], True)]
        elif cs[0] == "poly":
            segs = [(cs[1][i], cs[1][i + 1], True) for i in range(len(cs[1]) - 1)]
        for a, b, _ in segs:
            if ts[0] == "seg":
                r = _seg_seg(ts[1], ts[2], a, b)
                if r and -1e-9 <= r[1] <= 1 + 1e-9:
                    out.append((r[0], ts[1] + r[0] * (ts[2] - ts[1]), n, exact))
            else:
                for t in _line_circle(a, b, ts[1], ts[2]):
                    if -1e-9 <= t <= 1 + 1e-9:
                        p = a + t * (b - a)
                        out.append((_ang(p, ts[1]), p, n, exact))
        if cs[0] == "circ":
            if ts[0] == "seg":
                for t in _line_circle(ts[1], ts[2], cs[1], cs[2]):
                    p = ts[1] + t * (ts[2] - ts[1])
                    if cs[3] is None or _in_arc(_ang(p, cs[1]), *cs[3]):
                        out.append((t, p, n, exact))
            else:
                for p in _circle_circle(ts[1], ts[2], cs[1], cs[2]):
                    if cs[3] is None or _in_arc(_ang(p, cs[1]), *cs[3]):
                        out.append((_ang(p, ts[1]), p, n, exact))
    return out


# ===================================================================== trim
def trim(sk: Sketch, target: str, click):
    k = sk.kind(target)
    if k not in ("line", "arc", "circle"):
        raise EditError("trim works on lines, arcs and circles")
    click = np.asarray(click, float)
    ts = _shape(sk, target)
    cuts = _cuts(sk, target)
    if k == "line":
        a, b = ts[1], ts[2]
        L2 = float((b - a) @ (b - a))
        tc = float((click - a) @ (b - a)) / L2
        cuts = sorted({round(t, 12): (t, p, n, ex) for t, p, n, ex in cuts if 1e-7 < t < 1 - 1e-7}.values())
        return _trim_span(sk, target, cuts, tc, 1.0)
    ctr, r = ts[1], ts[2]
    ac = _ang(click, ctr)
    if k == "arc":
        a0, a1 = ts[3]
        span = (a1 - a0) % (2 * math.pi) or 2 * math.pi
        rel = lambda t: (t - a0) % (2 * math.pi)                  # noqa: E731
        cuts = sorted({round(rel(t), 12): (rel(t), p, n, ex) for t, p, n, ex in cuts if 1e-7 < rel(t) < span - 1e-7}.values())
        return _trim_span(sk, target, cuts, rel(ac), span)
    # a circle: cut at two or more places, the piece clicked goes and the rest is an arc
    cuts = sorted({round(t % (2 * math.pi), 9): (t % (2 * math.pi), p, n, ex) for t, p, n, ex in cuts}.values())
    if len(cuts) < 2:
        return _delete_curve(sk, target)
    a = ac % (2 * math.pi)
    i = max([j for j, c in enumerate(cuts) if c[0] <= a], default=len(cuts) - 1)
    lo, hi = cuts[i], cuts[(i + 1) % len(cuts)]
    plo, phi_ = _cut_point(sk, lo), _cut_point(sk, hi)
    d = sk.curves[target]
    sk.curves[target] = ["arc", d[1], phi_, plo] + ([CONSTRUCTION] if CONSTRUCTION in d else [])
    return sk


def _cut_point(sk, cut):
    _, p, cutter, exact = cut
    n = sk.find_point(p)
    if n is not None:
        return n
    n = sk.add_point(p)
    if exact:
        sk.cons.append(["on", n, cutter])
    return n


def _delete_curve(sk, name):
    pts = sk.pts_of(name)
    del sk.curves[name]
    sk.cons = [c for c in sk.cons if name not in c[1:]]
    for p in pts:
        sk.drop_point_if_free(p)
    return sk


def _trim_span(sk, target, cuts, tc, end):
    """Take out the piece of a line or arc (params 0..end) between the cuts around tc."""
    if not cuts:
        return _delete_curve(sk, target)
    bounds = [None] + cuts + [None]
    i = 0
    while i < len(cuts) and cuts[i][0] < tc:
        i += 1
    lo, hi = bounds[i], bounds[i + 1]
    d = sk.curves[target]
    tail = [CONSTRUCTION] if CONSTRUCTION in d else []
    line = d[0] == "line"
    first, last = (d[1], d[2]) if line else (d[2], d[3])
    if lo is None:                        # the piece at the start goes
        p = _cut_point(sk, hi)
        sk.curves[target] = (["line", p, d[2]] if line else ["arc", d[1], p, d[3]]) + tail
        _drop_changed(sk, target)
        sk.drop_point_if_free(first)
    elif hi is None:                      # the piece at the end goes
        p = _cut_point(sk, lo)
        sk.curves[target] = (["line", d[1], p] if line else ["arc", d[1], d[2], p]) + tail
        _drop_changed(sk, target)
        sk.drop_point_if_free(last)
    else:                                 # a middle piece: two curves are left
        p, q = _cut_point(sk, lo), _cut_point(sk, hi)
        sk.curves[target] = (["line", d[1], p] if line else ["arc", d[1], d[2], p]) + tail
        n2 = sk.add_curve("l" if line else "a", (["line", q, d[2]] if line else ["arc", d[1], q, d[3]]) + tail)
        _drop_changed(sk, target)
        sk.cons.append(["collinear", target, n2] if line else ["equal", target, n2])
    return sk


def _drop_changed(sk, curve):
    """A trimmed curve's length (and what measured along it) no longer holds."""
    sk.cons = [c for c in sk.cons if not (curve in c[1:] and c[0] in ("length", "midpoint", "equal")
                                          and sk.curves.get(curve, ["?"])[0] == "line")]


# ===================================================================== offset
def offset(sk: Sketch, names, dist):
    if not (_num(dist) and dist != 0):
        raise EditError("the offset distance must be a number other than 0 (negative: the other side)")
    names = list(dict.fromkeys(names))
    for n in names:
        if sk.kind(n) not in ("line", "arc", "circle"):
            raise EditError(f"offset works on lines, arcs and circles ({n} is a {sk.kind(n)})")
    for n in [n for n in names if sk.kind(n) == "circle"]:
        r = sk.radius(n) + dist
        if r <= 0:
            raise EditError(f"{n} would shrink to nothing")
        d = sk.curves[n]
        new = sk.add_curve("c", ["circle", d[1], round(r, 4)] + ([CONSTRUCTION] if sk.constr(n) else []))
        sk.cons.append(["radius", new, round(r, 6)])
    for chain, closed in _chains(sk, [n for n in names if sk.kind(n) != "circle"]):
        _offset_chain(sk, chain, closed, dist)
    return sk


def _ends(sk, n):
    d = sk.curves[n]
    return (d[1], d[2]) if d[0] == "line" else (d[2], d[3])


def _chains(sk, names):
    """Group lines and arcs into chains joined end to end: [([(name, forward)], closed)]."""
    left = list(names)
    out = []
    while left:
        chain = [(left.pop(0), True)]
        grown = True
        while grown:
            grown = False
            tail = _ends(sk, chain[-1][0])[1 if chain[-1][1] else 0]
            head = _ends(sk, chain[0][0])[0 if chain[0][1] else 1]
            for n in list(left):
                s, e = _ends(sk, n)
                if s == tail or e == tail:
                    chain.append((n, s == tail)); left.remove(n); grown = True
                    break
                if e == head or s == head:
                    chain.insert(0, (n, e == head)); left.remove(n); grown = True
                    break
        first = _ends(sk, chain[0][0])[0 if chain[0][1] else 1]
        last = _ends(sk, chain[-1][0])[1 if chain[-1][1] else 0]
        out.append((chain, len(chain) > 1 and first == last))
    return out


def _offset_chain(sk, chain, closed, dist):
    # positive: outward for a closed chain, to the left of the first curve's direction for an open one
    side = 1.0
    if closed:
        poly = []
        for n, fwd in chain:
            a, b = (sk.pos(p) for p in _ends(sk, n))
            poly.append(a if fwd else b)
        x, y = np.array(poly).T
        area = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
        side = -1.0 if area > 0 else 1.0           # counterclockwise: outward is to the right
    s = side * dist
    pieces = []                                    # (kind, start, end, extra) in walking order
    for n, fwd in chain:
        d = sk.curves[n]
        if d[0] == "line":
            a, b = (sk.pos(p) for p in (d[1], d[2]))
            if not fwd:
                a, b = b, a
            u = (b - a) / np.linalg.norm(b - a)
            nl = np.array([-u[1], u[0]])
            pieces.append(("line", a + s * nl, b + s * nl, n))
        else:
            ctr, r = sk.pos(d[1]), sk.radius(n)
            nr = r - s if fwd else r + s           # left of a counterclockwise walk is the centre
            if nr <= 1e-9:
                raise EditError(f"{n} would shrink to nothing at this distance")
            st, en = (sk.pos(p) for p in (d[2], d[3]))
            if not fwd:
                st, en = en, st
            pieces.append(("arc", ctr + (st - ctr) * nr / r, ctr + (en - ctr) * nr / r, n, nr, fwd))
    # joins: shared where the offset ends meet (tangent joins), at the crossing of two lines,
    # else a short line across the gap
    m = len(pieces)
    starts, ends = [None] * m, [None] * m
    joins = range(m) if closed else range(m - 1)
    bridges = []
    for i in joins:
        j = (i + 1) % m
        P, Q = pieces[i][2], pieces[j][1]
        if np.linalg.norm(P - Q) < 1e-7 * sk.scale():
            ends[i] = starts[j] = sk.add_point((P + Q) / 2)
        elif pieces[i][0] == "line" and pieces[j][0] == "line":
            r = _seg_seg(pieces[i][1], pieces[i][2], pieces[j][1], pieces[j][2])
            X = pieces[i][1] + r[0] * (pieces[i][2] - pieces[i][1]) if r else (P + Q) / 2
            ends[i] = starts[j] = sk.add_point(X)
        else:
            ends[i], starts[j] = sk.add_point(P), sk.add_point(Q)
            bridges.append((ends[i], starts[j]))
    for i, pc in enumerate(pieces):
        if starts[i] is None:
            starts[i] = sk.add_point(pc[1])
        if ends[i] is None:
            ends[i] = sk.add_point(pc[2])
        orig = pc[3]
        tail = [CONSTRUCTION] if sk.constr(orig) else []
        if pc[0] == "line":
            new = sk.add_curve("l", ["line", starts[i], ends[i]] + tail)
            sk.cons.append(["distance", orig, new, round(abs(dist), 6)])
        else:
            d = sk.curves[orig]
            a, b = (starts[i], ends[i]) if pc[5] else (ends[i], starts[i])
            new = sk.add_curve("a", ["arc", d[1], a, b] + tail)       # the same centre: concentric
            sk.cons.append(["radius", new, round(pc[4], 6)])
    for a, b in bridges:
        sk.add_curve("l", ["line", a, b])


# ===================================================================== mirror
def mirror(sk: Sketch, names, line):
    if sk.kind(line) not in ("line",):
        raise EditError("mirror across a line: pick a line last (a construction line works well)")
    A, B = (sk.pos(p) for p in sk.pts_of(line))
    u = (B - A) / np.linalg.norm(B - A)
    ang = math.degrees(math.atan2(u[1], u[0]))

    def refl(p):
        v = p - A
        return A + 2 * (v @ u) * u - v
    mp = {}

    def m(p):
        if p in mp:
            return mp[p]
        P = sk.pos(p)
        v = P - A
        if abs(v[0] * u[1] - v[1] * u[0]) < 1e-9 * sk.scale():
            mp[p] = p                              # on the mirror line: its own image
        else:
            mp[p] = sk.add_point(refl(P))
            sk.cons.append(["symmetric", p, mp[p], line])
        return mp[p]
    made = 0
    for n in names:
        if n == line:
            continue
        d = sk.curves[n]
        tail = [CONSTRUCTION] if CONSTRUCTION in d else []
        k = d[0]
        if k == "line":
            sk.add_curve("l", ["line", m(d[1]), m(d[2])] + tail)
        elif k == "arc":                           # a mirror turns counterclockwise round
            sk.add_curve("a", ["arc", m(d[1]), m(d[3]), m(d[2])] + tail)
        elif k == "circle":
            new = sk.add_curve("c", ["circle", m(d[1]), d[2]] + tail)
            sk.cons.append(["equal", n, new])
        elif k == "ellipse":
            a0 = d[4] if len(d) > 4 and _num(d[4]) else 0.0
            sk.add_curve("e", ["ellipse", m(d[1]), d[2], d[3], round((2 * ang - a0) % 360, 6)] + tail)
        elif k == "spline":
            pts = [v for v in d[1:] if v not in (CONSTRUCTION, "closed")]
            sk.add_curve("s", ["spline"] + [m(p) for p in pts] + (["closed"] if "closed" in d else []) + tail)
        elif k in ("gear", "ring_gear"):
            opts = _opts(d[4]) if len(d) > 4 and isinstance(d[4], dict) else {}
            opts["angle"] = round((2 * ang - float(opts.get("angle", 0.0))) % 360, 6)
            sk.add_curve("g", [k, m(d[1]), d[2], d[3], _opts_out(opts)] + tail)
        elif k == "rack":
            sk.add_curve("r", ["rack", m(d[2]), m(d[1])] + d[3:])      # reversed: the teeth stay on the outside
        else:
            raise EditError(f"{n} is a {k}: projected and formula curves can't be mirrored")
        made += 1
    if not made:
        raise EditError("select the curves to mirror, then the mirror line")
    return sk


# ===================================================================== patterns
def _copy(sk, names, move, held=None, turn=0.0):
    """Copies of the curves with every point moved by move(p); held(p, q) gives constraints
    tying a copied point to its original."""
    mp = {}

    def m(p):
        if p not in mp:
            mp[p] = sk.add_point(move(sk.pos(p)))
            if held:
                sk.cons.extend(held(p, mp[p]))
        return mp[p]
    for n in names:
        d = sk.curves[n]
        k = d[0]
        tail = [CONSTRUCTION] if CONSTRUCTION in d else []
        if k in ("line", "arc"):
            sk.add_curve(k[0], [k] + [m(p) for p in d[1:] if p != CONSTRUCTION] + tail)
        elif k == "circle":
            new = sk.add_curve("c", ["circle", m(d[1]), d[2]] + tail)
            sk.cons.append(["equal", n, new])
        elif k == "ellipse":
            a0 = d[4] if len(d) > 4 and _num(d[4]) else 0.0
            sk.add_curve("e", ["ellipse", m(d[1]), d[2], d[3], round((a0 + turn) % 360, 6)] + tail)
        elif k == "spline":
            pts = [v for v in d[1:] if v not in (CONSTRUCTION, "closed")]
            sk.add_curve("s", ["spline"] + [m(p) for p in pts] + (["closed"] if "closed" in d else []) + tail)
        elif k in ("gear", "ring_gear"):
            opts = _opts(d[4]) if len(d) > 4 and isinstance(d[4], dict) else {}
            if turn:
                opts["angle"] = round((float(opts.get("angle", 0.0)) + turn) % 360, 6)
            sk.add_curve("g", [k, m(d[1]), d[2], d[3]] + ([_opts_out(opts)] if opts else []) + tail)
        elif k == "rack":
            sk.add_curve("r", ["rack", m(d[1]), m(d[2])] + d[3:])
        else:
            raise EditError(f"{n} is a {k}: projected and formula curves can't be patterned")


def pattern_rect(sk: Sketch, names, nx, dx, ny=1, dy=0.0):
    if not names:
        raise EditError("select the curves to pattern first")
    nx, ny = int(nx), int(ny)
    if nx < 1 or ny < 1 or nx * ny < 2:
        raise EditError("give at least 2 copies (counts include the original)")
    for i in range(nx):
        for j in range(ny):
            if i == 0 and j == 0:
                continue
            off = np.array([i * dx, j * dy], float)
            _copy(sk, names, lambda p, off=off: p + off,
                  lambda p, q, i=i, j=j: [["hdistance", p, q, round(i * dx, 6)], ["vdistance", p, q, round(j * dy, 6)]])
    return sk


def pattern_circ(sk: Sketch, names, centre, count, angle=360.0):
    if not names:
        raise EditError("select the curves to pattern first")
    count = int(count)
    if count < 2:
        raise EditError("give at least 2 copies (the count includes the original)")
    C = sk.pos(centre)
    step = angle / count if abs(angle - 360.0) < 1e-9 else angle / (count - 1)
    for k in range(1, count):
        a = math.radians(step * k)
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])

        def held(p, q, r0=None):
            return [["distance", centre, q, round(float(np.linalg.norm(sk.pos(p) - C)), 6)]]
        _copy(sk, names, lambda p, R=R: C + R @ (p - C), held, turn=step * k)
    return sk


OPS = {"fillet", "trim", "offset", "mirror", "pattern_rect", "pattern_circ"}


def apply(op: str, model: dict, solved: dict, args: dict) -> dict:
    if op not in OPS:
        raise EditError(f"unknown sketch tool {op!r}")
    sk = Sketch(model, solved)
    if op == "fillet":
        fillet(sk, args["corner"], args["radius"])
    elif op == "trim":
        trim(sk, args["curve"], args["at"])
    elif op == "offset":
        offset(sk, args["curves"], args["distance"])
    elif op == "mirror":
        mirror(sk, args["curves"], args["line"])
    elif op == "pattern_rect":
        pattern_rect(sk, args["curves"], args.get("nx", 1), args.get("dx", 0.0), args.get("ny", 1), args.get("dy", 0.0))
    else:
        pattern_circ(sk, args["curves"], args["centre"], args["count"], args.get("angle", 360.0))
    return sk.out()
