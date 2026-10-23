"""Constrained sketches: named points and curves on a plane, held in place by constraints and
driving dimensions, solved numerically; every closed region becomes a face to extrude,
revolve, loft or sweep.

    s = sketch(at=plate.face("+Z"),
        points={"a": (0, 0), "b": (40, 0), "c": (40, 25), "d": (0, 25), "o": (20, 12)},
        curves={"bottom": ("line", "a", "b"), "right": ("line", "b", "c"),
                "top": ("line", "c", "d"), "left": ("line", "d", "a"),
                "hole": ("circle", "o", 4)},
        constraints=[("horizontal", "bottom"), ("vertical", "right"), ("horizontal", "top"),
                     ("vertical", "left"), ("fix", "a"), ("length", "bottom", P.length),
                     ("length", "right", 25), ("radius", "hole", 4),
                     ("distance", "o", "bottom", 12), ("distance", "o", "left", 20)])
    extrude(s, 10)                       # the outline with the hole

Coordinates are in mm on the plane of `at` (its X and Y). They are where the solver starts:
it moves geometry only as far as the constraints require, so an under-constrained sketch
stays as drawn. The app rewrites them with the solved positions.

Curves (a trailing "construction" makes a curve guide-only: solved, never part of a region):
    ("line", p, q)                   straight from p to q
    ("arc", centre, start, end)      counterclockwise (about the sketch's Z) from start to end
    ("circle", centre, radius)       the radius is where solving starts; dimension it to fix it
    ("spline", p1, p2, ..., pn)      smooth curve through the points ("closed" to close it)
    ("project", edge)                an edge of another body, flattened onto the sketch plane:
                                     fixed; its ends are "<name>.start" / "<name>.end"
    ("ellipse", centre, rx, ry)      half-widths along X and Y; ("ellipse", c, rx, ry, angle) turns it
    ("parametric", "x(t)", "y(t)", t0, t1)
                                     the curve x(t), y(t) for t from t0 to t1, e.g.
                                     ("parametric", "20*cos(t)", "10*sin(2*t)", 0, 2*pi): math with
                                     sin cos tan sqrt exp log pi...; a parameter goes in with an
                                     f-string, f"{P.r}*cos(t)"; fixed; its ends are "<name>.start"
                                     / "<name>.end" (closed when they meet)
    ("gear", centre, module, teeth)  an involute spur gear's outline (teeth outside), tooth 0 along
                                     X; options in a dict last: {"pressure_angle": 20, "angle": 0,
                                     "backlash": 0} (backlash: play at the pitch circle, mm)
    ("ring_gear", centre, module, teeth)  the hole of a ring gear (teeth inside); same options
    ("rack", p, q, module)           a straight rack along the pitch line p -> q, teeth on the left
                                     (whole teeth every pi*module; options pressure_angle, backlash)
Points are (x, y) or an anchor of another body (fixed where it projects onto the plane).
A curve's points can also be named through it: "top.start", "top.end", "hole.center".

Constraints:   coincident p q | on p curve | midpoint p line | horizontal line (or p q) |
    vertical line (or p q) | parallel l1 l2 | perpendicular l1 l2 | collinear l1 l2 |
    tangent c1 c2 | equal c1 c2 (lengths or radii) | concentric c1 c2 | symmetric p q line |
    fix p (or a curve)
Dimensions (last item a number or a parameter):  length line v | distance p q v |
    distance p line v | distance line line v (parallel) | hdistance p q v | vdistance p q v |
    radius c v | diameter c v | angle l1 l2 v (degrees, 0-180) | x p v | y p v

Faces: each closed region is named after the curves around it, sorted and joined by '+'
("bottom+left+right+top" for an outline, "hole" for the inside of the hole circle); the
regions an outline-with-holes reading fills (alternately in and out, like a font) are also
named 'profile', which is what extrude, revolve, loft and sweep take when given the sketch
itself. Anchors: every point by name, 'origin' and 'center'.
"""
from __future__ import annotations

import math

import numpy as np

KINDS = ("line", "arc", "circle", "spline", "project", "ellipse", "parametric", "gear", "ring_gear", "rack")
GEAR_OPTIONS = {"pressure_angle", "angle", "backlash"}
TOL = 1e-9               # mm: a constraint this close is met
ALIAS = {"line": ("start", "end"), "arc": ("center", "start", "end"), "circle": ("center",),
         "project": ("start", "end"), "ellipse": ("center",), "parametric": ("start", "end"),
         "gear": ("center",), "ring_gear": ("center",), "rack": ("start", "end")}


class SketchError(ValueError):
    pass


# ===================================================================== model
class Curve:
    def __init__(self, name, kind, pts, radius=None, construction=False, fixed=False, closed=False):
        self.name, self.kind, self.pts = name, kind, list(pts)
        self.radius, self.construction, self.fixed, self.closed = radius, construction, fixed, closed
        self.shape_kind = kind       # a projected edge: line, circle, arc or spline once projected
        self.centre, self.samples = None, None


class Model:
    """Points (name -> [x, y], fixed or not), curves and constraints, checked."""

    def __init__(self, points, curves, constraints):
        self.points: dict[str, list] = {}
        self.fixed: set = set()
        for n, p in (points or {}).items():
            self._name(n, "point")
            if not (isinstance(p, (tuple, list)) and len(p) == 2):
                raise SketchError(f"point {n!r}: give (x, y)")
            self.points[n] = [float(p[0]), float(p[1])]
        self.curves: dict[str, Curve] = {}
        for n, c in (curves or {}).items():
            self._name(n, "curve")
            if n in self.points:
                raise SketchError(f"{n!r} names both a point and a curve")
            self.curves[n] = self._curve(n, c)
        self.cons = [self._constraint(c) for c in (constraints or [])]

    @staticmethod
    def _name(n, what):
        if not isinstance(n, str) or not n or any(ch in n for ch in ".+# \t\n"):
            raise SketchError(f"{what} name {n!r}: use a word without spaces, '.', '+' or '#'")

    def _curve(self, n, c):
        if not isinstance(c, (tuple, list)) or not c or c[0] not in KINDS:
            raise SketchError(f"curve {n!r}: start with one of {', '.join(KINDS)}")
        kind, rest = c[0], list(c[1:])
        construction = bool(rest) and rest[-1] == "construction"
        if construction:
            rest = rest[:-1]
        closed = kind == "spline" and bool(rest) and rest[-1] == "closed"
        if closed:
            rest = rest[:-1]
        if kind == "project":
            if len(rest) != 1:
                raise SketchError(f"curve {n!r}: ('project', body.edge(...))")
            return Curve(n, kind, [], construction=construction, fixed=True)
        if kind == "ellipse":
            if len(rest) not in (3, 4) or not all(isinstance(v, (int, float)) for v in rest[1:]) or \
                    not (rest[1] > 0 and rest[2] > 0):
                raise SketchError(f"curve {n!r}: ('ellipse', centre, rx, ry) or ('ellipse', centre, rx, ry, angle)")
            c = Curve(n, kind, [self.point(rest[0])], construction=construction)
            c.rx, c.ry, c.angle = float(rest[1]), float(rest[2]), float(rest[3]) if len(rest) == 4 else 0.0
            return c
        if kind == "parametric":
            if len(rest) != 4 or not all(isinstance(v, str) for v in rest[:2]) or \
                    not all(isinstance(v, (int, float)) for v in rest[2:]) or not rest[3] > rest[2]:
                raise SketchError(f"curve {n!r}: ('parametric', \"x(t)\", \"y(t)\", t0, t1) with t1 > t0")
            c = Curve(n, kind, [], construction=construction, fixed=True)
            c.expr, c.span = (rest[0], rest[1]), (float(rest[2]), float(rest[3]))
            return c
        if kind in ("gear", "ring_gear", "rack"):
            opts = {}
            if rest and isinstance(rest[-1], dict):
                opts = rest.pop()
                bad = set(opts) - GEAR_OPTIONS - ({"angle"} if kind != "rack" else set())
                if bad or (kind == "rack" and "angle" in opts):
                    raise SketchError(f"curve {n!r}: options are {', '.join(sorted(GEAR_OPTIONS - ({'angle'} if kind == 'rack' else set())))}")
            if kind == "rack":
                if len(rest) != 3 or not isinstance(rest[2], (int, float)) or not rest[2] > 0:
                    raise SketchError(f"curve {n!r}: ('rack', start, end, module) along the pitch line")
                c = Curve(n, kind, [self.point(rest[0]), self.point(rest[1])], construction=construction)
                c.module, c.teeth = float(rest[2]), None
            else:
                if len(rest) != 3 or not isinstance(rest[1], (int, float)) or not isinstance(rest[2], (int, float)) \
                        or not rest[1] > 0 or rest[2] != int(rest[2]) or rest[2] < 3:
                    raise SketchError(f"curve {n!r}: ('{kind}', centre, module, teeth): a module above 0 and 3 or more teeth")
                c = Curve(n, kind, [self.point(rest[0])], construction=construction)
                c.module, c.teeth = float(rest[1]), int(rest[2])
            c.pa = float(opts.get("pressure_angle", 20.0))
            c.angle = float(opts.get("angle", 0.0))
            c.backlash = float(opts.get("backlash", 0.0))
            return c
        if kind == "circle":
            if len(rest) != 2 or not isinstance(rest[1], (int, float)) or not rest[1] > 0:
                raise SketchError(f"curve {n!r}: ('circle', centre, radius)")
            return Curve(n, kind, [self.point(rest[0])], radius=float(rest[1]), construction=construction)
        need = {"line": 2, "arc": 3}.get(kind)
        if need is not None and len(rest) != need:
            raise SketchError(f"curve {n!r}: {kind} takes {need} points")
        if kind == "spline" and len(rest) < 2:
            raise SketchError(f"curve {n!r}: a spline goes through at least 2 points")
        pts = [self.point(p) for p in rest]
        if len(set(pts)) < len(pts) and not (kind == "spline"):
            raise SketchError(f"curve {n!r}: uses the same point twice")
        return Curve(n, kind, pts, construction=construction, closed=closed)

    def point(self, ref):
        """A point name, or '<curve>.start|end|center', as the point's own name."""
        if not isinstance(ref, str):
            raise SketchError(f"{ref!r}: name a point")
        if ref in self.points:
            return ref
        if "." in ref:
            cn, part = ref.split(".", 1)
            c = self.curves.get(cn)
            if c is not None and part in ALIAS.get(c.kind, ()):
                i = ALIAS[c.kind].index(part)
                if i < len(c.pts):
                    return c.pts[i]
            if c is not None and c.kind in ("project", "parametric"):
                return ref                   # filled in when the edge is projected / the curve evaluated
        raise SketchError(f"no point {ref!r}")

    def curve(self, ref, kinds=None):
        c = self.curves.get(ref)
        if c is None:
            raise SketchError(f"no curve {ref!r}")
        if kinds and c.kind not in kinds and not (c.kind == "project" and c.shape_kind in kinds):
            raise SketchError(f"{ref!r} is a {c.kind}; this needs a {' or '.join(kinds)}")
        return c

    def _constraint(self, c):
        if not isinstance(c, (tuple, list)) or not c or not isinstance(c[0], str):
            raise SketchError(f"constraint {c!r}: start with its kind, e.g. ('horizontal', 'top')")
        kind = c[0]
        spec = CONSTRAINTS.get(kind)
        if spec is None:
            raise SketchError(f"unknown constraint {kind!r} (known: {', '.join(sorted(CONSTRAINTS))})")
        args = list(c[1:])
        value = None
        if spec["dim"]:
            if not args or not isinstance(args[-1], (int, float)):
                raise SketchError(f"{kind}: give the value last, e.g. ('{kind}', ..., 10)")
            value = float(args.pop())
        return (kind, args, value)


# ===================================================================== solving
class Solver:
    """Gauss-Newton with minimum-norm steps: an under-constrained sketch moves as little as it
    must. Variables: every point's x and y, every circle's radius (projected geometry fixed)."""

    def __init__(self, model: Model, extra_fixed=()):
        self.m = model
        names = [n for n in model.points if n not in model.fixed and n not in extra_fixed]
        self.idx = {n: 2 * i for i, n in enumerate(names)}
        k = 2 * len(names)
        self.ridx = {}
        for c in model.curves.values():
            if c.kind == "circle":
                self.ridx[c.name] = k
                k += 1
        self.n = k
        x = np.zeros(k)
        for n, i in self.idx.items():
            x[i:i + 2] = model.points[n]
        for n, i in self.ridx.items():
            x[i] = model.curves[n].radius
        self.x0 = x
        self.terms = []                  # (constraint index, function(x) -> residuals)
        for ci, (kind, args, value) in enumerate(model.cons):
            for f in CONSTRAINTS[kind]["make"](self, args, value):
                self.terms.append((ci, f))
        for c in model.curves.values():   # an arc's ends stay on its circle
            if c.kind == "arc":
                cc, s, e = c.pts
                self.terms.append((None, lambda x, cc=cc, s=s, e=e:
                                   _norm(self.P(x, s) - self.P(x, cc)) - _norm(self.P(x, e) - self.P(x, cc))))

    # -- access
    def P(self, x, name):
        i = self.idx.get(name)
        return x[i:i + 2] if i is not None else np.asarray(self.m.points[name], float)

    def R(self, x, curve):
        c = self.m.curves[curve]
        if c.kind == "circle":
            return x[self.ridx[c.name]] if c.name in self.ridx else c.radius
        if c.kind == "arc":
            return _norm(self.P(x, c.pts[1]) - self.P(x, c.pts[0]))
        raise SketchError(f"{curve!r} has no radius")

    def L0(self, curve):
        """A line's length as drawn: a fixed scale for its direction constraints, so a line
        shrinking to nothing (which has every direction) never looks like a way to meet them."""
        a, b = self.ends(self.x0, curve)
        return max(_norm(b - a), 1e-6)

    def d(self, x, curve):
        a, b = self.ends(x, curve)
        return (b - a) / self.L0(curve)

    def ends(self, x, curve):
        c = self.m.curves[curve]
        if c.kind not in ("line", "project", "rack") or len(c.pts) != 2:
            raise SketchError(f"{curve!r} is not a straight line")
        return self.P(x, c.pts[0]), self.P(x, c.pts[1])

    # -- solving
    def residuals(self, x):
        out = []
        for _, f in self.terms:
            r = f(x)
            out.extend(np.atleast_1d(r).tolist())
        return np.array(out, float)

    def jacobian(self, x, r0):
        J = np.zeros((len(r0), self.n))
        for j in range(self.n):
            h = 1e-7 * max(1.0, abs(x[j]))
            xp = x.copy()
            xp[j] += h
            xm = x.copy()
            xm[j] -= h
            J[:, j] = (self.residuals(xp) - self.residuals(xm)) / (2 * h)
        return J

    def solve(self, iters=100):
        x = self.x0.copy()
        if not self.terms or self.n == 0:
            return x, self._status(x)
        r = self.residuals(x)
        for _ in range(iters):
            if np.max(np.abs(r)) < TOL:
                break
            J = self.jacobian(x, r)
            dx = np.linalg.lstsq(J, -r, rcond=None)[0]
            f0, step = float(r @ r), 1.0
            while step > 1e-6:
                xn = x + step * dx
                rn = self.residuals(xn)
                if float(rn @ rn) < f0:
                    x, r = xn, rn
                    break
                step *= 0.5
            else:
                break
        return x, self._status(x)

    def _status(self, x):
        r = self.residuals(x) if self.terms else np.zeros(0)
        bad = sorted({ci for (ci, f) in self.terms if ci is not None and np.max(np.abs(np.atleast_1d(f(x)))) > 1e-6})
        arc_bad = any(ci is None and abs(float(f(x))) > 1e-6 for ci, f in self.terms)
        if self.terms and self.n:
            J = self.jacobian(x, r)
            sv = np.linalg.svd(J, compute_uv=False) if J.size else np.zeros(0)
            rank = int(np.sum(sv > 1e-8 * max(1.0, sv[0] if len(sv) else 1.0)))
        else:
            rank = 0
        return {"dof": self.n - rank, "solved": not bad and not arc_bad, "unmet": bad}


def _norm(v):
    return float(np.hypot(v[0], v[1]))


def _cross(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _unit_dir(a, b):
    d = b - a
    n = _norm(d)
    return d / n if n > 1e-12 else d


# ===================================================================== constraints
def _args(kind, args, n, what):
    if len(args) != n:
        raise SketchError(f"{kind} takes {what}")


def _coincident(S, args, v):
    _args("coincident", args, 2, "two points")
    p, q = (S.m.point(a) for a in args)
    return [lambda x: S.P(x, p) - S.P(x, q)]


def _on(S, args, v):
    _args("on", args, 2, "a point and a curve")
    p = S.m.point(args[0])
    c = S.m.curve(args[1])
    if c.kind in ("line",) or (c.kind == "project" and len(c.pts) == 2 and c.shape_kind == "line"):
        return [lambda x: _cross(S.d(x, c.name), S.P(x, p) - S.ends(x, c.name)[0])]
    if c.kind in ("circle", "arc") or (c.kind == "project" and c.shape_kind in ("circle", "arc")):
        return [lambda x: _norm(S.P(x, p) - _centre(S, x, c)) - _radius(S, x, c)]
    raise SketchError(f"on: {args[1]!r} must be a line, arc or circle")


def _centre(S, x, c):
    return S.P(x, c.pts[0]) if c.kind in ("circle", "arc", "gear", "ring_gear", "ellipse") else np.asarray(c.centre, float)


def _radius(S, x, c):
    return S.R(x, c.name) if c.kind in ("circle", "arc") else c.radius


def _line(S, name):
    c = S.m.curve(name)
    if c.kind in ("line", "rack") or (c.kind == "project" and getattr(c, "shape_kind", None) == "line"):
        return c
    raise SketchError(f"{name!r} is not a straight line")


def _dir_args(S, kind, args):
    """A line's two points, or two points given directly."""
    if len(args) == 1:
        c = _line(S, args[0])
        return c.pts[0], c.pts[1]
    if len(args) == 2:
        return S.m.point(args[0]), S.m.point(args[1])
    raise SketchError(f"{kind} takes a line, or two points")


def _horizontal(S, args, v):
    a, b = _dir_args(S, "horizontal", args)
    return [lambda x: S.P(x, b)[1] - S.P(x, a)[1]]


def _vertical(S, args, v):
    a, b = _dir_args(S, "vertical", args)
    return [lambda x: S.P(x, b)[0] - S.P(x, a)[0]]


def _two_lines(S, kind, args):
    _args(kind, args, 2, "two lines")
    return _line(S, args[0]), _line(S, args[1])


def _parallel(S, args, v):
    l1, l2 = _two_lines(S, "parallel", args)
    return [lambda x: _cross(S.d(x, l1.name), S.d(x, l2.name))]


def _perpendicular(S, args, v):
    l1, l2 = _two_lines(S, "perpendicular", args)
    return [lambda x: float(S.d(x, l1.name) @ S.d(x, l2.name))]


def _collinear(S, args, v):
    l1, l2 = _two_lines(S, "collinear", args)

    def f(x):
        a, _ = S.ends(x, l1.name)
        c, d = S.ends(x, l2.name)
        u = S.d(x, l1.name)
        return np.array([_cross(u, c - a), _cross(u, d - a)])
    return [f]


def _tangent(S, args, v):
    _args("tangent", args, 2, "two curves")
    c1, c2 = (S.m.curve(a) for a in args)
    lin = lambda c: c.kind == "line" or (c.kind == "project" and c.shape_kind == "line")        # noqa: E731
    rnd = lambda c: c.kind in ("circle", "arc") or (c.kind == "project" and c.shape_kind in ("circle", "arc"))  # noqa: E731
    if lin(c1) and rnd(c2):
        c1, c2 = c2, c1
    if rnd(c1) and lin(c2):
        def f(x, c=c1, l=c2):
            a, b = S.ends(x, l.name)
            return abs(_cross(_unit_dir(a, b), _centre(S, x, c) - a)) - _radius(S, x, c)
        return [f]
    if rnd(c1) and rnd(c2):
        x0 = S.x0
        d0 = _norm(_centre(S, x0, c1) - _centre(S, x0, c2))
        r1, r2 = _radius(S, x0, c1), _radius(S, x0, c2)
        inner = abs(d0 - abs(r1 - r2)) < abs(d0 - (r1 + r2))

        def g(x):
            d = _norm(_centre(S, x, c1) - _centre(S, x, c2))
            a, b = _radius(S, x, c1), _radius(S, x, c2)
            return d - abs(a - b) if inner else d - (a + b)
        return [g]
    raise SketchError("tangent: give a line and an arc/circle, or two arcs/circles")


def _equal(S, args, v):
    _args("equal", args, 2, "two curves")
    c1, c2 = (S.m.curve(a) for a in args)
    if c1.kind == "line" and c2.kind == "line":
        return [lambda x: _norm(S.P(x, c1.pts[1]) - S.P(x, c1.pts[0])) - _norm(S.P(x, c2.pts[1]) - S.P(x, c2.pts[0]))]
    if c1.kind in ("circle", "arc") and c2.kind in ("circle", "arc"):
        return [lambda x: S.R(x, c1.name) - S.R(x, c2.name)]
    raise SketchError("equal: two lines (same length) or two arcs/circles (same radius)")


def _concentric(S, args, v):
    _args("concentric", args, 2, "two arcs/circles")
    c1, c2 = (S.m.curve(a) for a in args)
    if not all(c.kind in ("circle", "arc", "gear", "ring_gear", "ellipse") or (c.kind == "project" and c.shape_kind in ("circle", "arc"))
               for c in (c1, c2)):
        raise SketchError("concentric: two arcs/circles (or gears, ellipses)")
    return [lambda x: _centre(S, x, c1) - _centre(S, x, c2)]


def _midpoint(S, args, v):
    _args("midpoint", args, 2, "a point and a line")
    p = S.m.point(args[0])
    l = _line(S, args[1])
    return [lambda x: S.P(x, p) - (S.ends(x, l.name)[0] + S.ends(x, l.name)[1]) / 2]


def _symmetric(S, args, v):
    _args("symmetric", args, 3, "two points and a line")
    p, q = S.m.point(args[0]), S.m.point(args[1])
    l = _line(S, args[2])

    def f(x):
        a, _ = S.ends(x, l.name)
        u = S.d(x, l.name)
        m = (S.P(x, p) + S.P(x, q)) / 2
        return np.array([_cross(u, m - a), float(u @ (S.P(x, q) - S.P(x, p)))])
    return [f]


def _fix(S, args, v):
    _args("fix", args, 1, "a point or a curve")
    name = args[0]
    pts = S.m.curves[name].pts if name in S.m.curves else [S.m.point(name)]
    out = []
    for p in pts:
        at = np.array(S.m.points[p], float)
        out.append(lambda x, p=p, at=at: S.P(x, p) - at)
    if name in S.m.curves and S.m.curves[name].kind == "circle":
        r0 = S.m.curves[name].radius
        out.append(lambda x, n=name, r0=r0: S.R(x, n) - r0)
    return out


def _length(S, args, v):
    _args("length", args, 1, "a line and a value")
    l = _line(S, args[0])
    return [lambda x: _norm(S.ends(x, l.name)[1] - S.ends(x, l.name)[0]) - v]


def _point_line_dist(S, x, p, l):
    a, b = S.ends(x, l)
    return abs(_cross(_unit_dir(a, b), S.P(x, p) - a))


def _distance(S, args, v):
    _args("distance", args, 2, "two items and a value")
    a, b = args
    if a in S.m.curves and b in S.m.curves:
        l1, l2 = _line(S, a), _line(S, b)
        return [lambda x: _point_line_dist(S, x, l2.pts[0], l1.name) - v,
                lambda x: _cross(_unit_dir(*S.ends(x, l1.name)), _unit_dir(*S.ends(x, l2.name)))]
    if b in S.m.curves and S.m.curves[b].kind in ("line", "project"):
        p = S.m.point(a)
        return [lambda x: _point_line_dist(S, x, p, b) - v]
    if a in S.m.curves and S.m.curves[a].kind in ("line", "project"):
        p = S.m.point(b)
        return [lambda x: _point_line_dist(S, x, p, a) - v]
    p, q = S.m.point(a), S.m.point(b)
    return [lambda x: _norm(S.P(x, q) - S.P(x, p)) - v]


def _hdistance(S, args, v):
    _args("hdistance", args, 2, "two points and a value")
    p, q = (S.m.point(a) for a in args)
    return [lambda x: (S.P(x, q)[0] - S.P(x, p)[0]) - v]


def _vdistance(S, args, v):
    _args("vdistance", args, 2, "two points and a value")
    p, q = (S.m.point(a) for a in args)
    return [lambda x: (S.P(x, q)[1] - S.P(x, p)[1]) - v]


def _radius_c(S, args, v):
    _args("radius", args, 1, "an arc/circle and a value")
    c = S.m.curve(args[0], ("circle", "arc"))
    return [lambda x: S.R(x, c.name) - v]


def _diameter(S, args, v):
    _args("diameter", args, 1, "an arc/circle and a value")
    c = S.m.curve(args[0], ("circle", "arc"))
    return [lambda x: 2 * S.R(x, c.name) - v]


def _angle(S, args, v):
    l1, l2 = _two_lines(S, "angle", args)
    t = math.radians(v)

    def f(x):
        u, w = _unit_dir(*S.ends(x, l1.name)), _unit_dir(*S.ends(x, l2.name))
        return abs(math.atan2(_cross(u, w), float(u @ w))) - t
    return [f]


def _xcoord(S, args, v):
    _args("x", args, 1, "a point and a value")
    p = S.m.point(args[0])
    return [lambda x: S.P(x, p)[0] - v]


def _ycoord(S, args, v):
    _args("y", args, 1, "a point and a value")
    p = S.m.point(args[0])
    return [lambda x: S.P(x, p)[1] - v]


CONSTRAINTS = {
    "coincident": {"dim": False, "make": _coincident}, "on": {"dim": False, "make": _on},
    "midpoint": {"dim": False, "make": _midpoint}, "horizontal": {"dim": False, "make": _horizontal},
    "vertical": {"dim": False, "make": _vertical}, "parallel": {"dim": False, "make": _parallel},
    "perpendicular": {"dim": False, "make": _perpendicular}, "collinear": {"dim": False, "make": _collinear},
    "tangent": {"dim": False, "make": _tangent}, "equal": {"dim": False, "make": _equal},
    "concentric": {"dim": False, "make": _concentric}, "symmetric": {"dim": False, "make": _symmetric},
    "fix": {"dim": False, "make": _fix},
    "length": {"dim": True, "make": _length}, "distance": {"dim": True, "make": _distance},
    "hdistance": {"dim": True, "make": _hdistance}, "vdistance": {"dim": True, "make": _vdistance},
    "radius": {"dim": True, "make": _radius_c}, "diameter": {"dim": True, "make": _diameter},
    "angle": {"dim": True, "make": _angle}, "x": {"dim": True, "make": _xcoord}, "y": {"dim": True, "make": _ycoord},
}


def describe(kind, args, value):
    """A constraint as a person would say it, for messages."""
    v = "" if value is None else f" {value:g}"
    return f"{kind} {' '.join(str(a) for a in args)}{v}".strip()


def solve(model: Model):
    """Solve in place: returns the status; model.points and circle radii hold the solution."""
    S = Solver(model)
    x, st = S.solve()
    for n, i in S.idx.items():
        model.points[n] = [float(x[i]), float(x[i + 1])]
    for n, i in S.ridx.items():
        model.curves[n].radius = float(x[i])
    st["unmet"] = [describe(*model.cons[i]) for i in st["unmet"]]
    return st


# ===================================================================== geometry
def build(model: Model, fr):
    """([(region face, name, piece, in profile)], [(edge, curve name, construction)])."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
    w = lambda p: fr.origin + float(p[0]) * fr.x + float(p[1]) * fr.y          # noqa: E731
    P3 = lambda p: gp_Pnt(*w(p))                                                # noqa: E731
    pt = lambda n: model.points[n]                                              # noqa: E731
    edges = []                        # (edge, curve name, construction)
    for c in model.curves.values():
        try:
            if c.kind in ("line",) or (c.kind == "project" and c.shape_kind == "line"):
                e = BRepBuilderAPI_MakeEdge(P3(pt(c.pts[0])), P3(pt(c.pts[1]))).Edge()
            elif c.kind in ("circle", "arc") or (c.kind == "project" and c.shape_kind in ("circle", "arc")):
                ctr = pt(c.pts[0]) if c.kind in ("circle", "arc") else c.centre
                r = c.radius if c.kind in ("circle", "project") else _norm(np.subtract(pt(c.pts[1]), pt(c.pts[0])))
                circ = gp_Circ(gp_Ax2(P3(ctr), gp_Dir(*fr.z), gp_Dir(*fr.x)), r)
                if c.kind == "circle" or (c.kind == "project" and c.shape_kind == "circle"):
                    e = BRepBuilderAPI_MakeEdge(circ).Edge()
                else:
                    s, t = (c.pts[1], c.pts[2]) if c.kind == "arc" else (c.pts[0], c.pts[1])
                    e = BRepBuilderAPI_MakeEdge(circ, P3(pt(s)), P3(pt(t))).Edge()
            elif c.kind == "ellipse":
                from OCP.gp import gp_Elips
                ctr = pt(c.pts[0])
                big, small, ang = (c.rx, c.ry, c.angle) if c.rx >= c.ry else (c.ry, c.rx, c.angle + 90.0)
                a = math.radians(ang)
                xd = np.cos(a) * np.asarray(fr.x) + np.sin(a) * np.asarray(fr.y)
                e = BRepBuilderAPI_MakeEdge(gp_Elips(gp_Ax2(P3(ctr), gp_Dir(*fr.z), gp_Dir(*xd)), big, small)).Edge()
                ts = np.linspace(0, 2 * math.pi, 97)
                ca, sa = math.cos(math.radians(c.angle)), math.sin(math.radians(c.angle))
                c.samples = [[ctr[0] + c.rx * math.cos(t) * ca - c.ry * math.sin(t) * sa,
                              ctr[1] + c.rx * math.cos(t) * sa + c.ry * math.sin(t) * ca] for t in ts]
            elif c.kind in ("gear", "ring_gear", "rack"):
                from . import gears
                try:
                    if c.kind == "rack":
                        p0, p1 = np.asarray(pt(c.pts[0]), float), np.asarray(pt(c.pts[1]), float)
                        L = _norm(p1 - p0)
                        u = (p1 - p0) / (L or 1.0)
                        nrm = np.array([-u[1], u[0]])
                        segs = gears.rack(c.module, L, c.pa, c.backlash)
                        loc = lambda x, y, p0=p0, u=u, nrm=nrm: p0 + x * u + y * nrm                 # noqa: E731
                    else:
                        segs = (gears.spur if c.kind == "gear" else gears.ring)(c.module, c.teeth, c.pa, c.backlash, c.angle)
                        ctr = np.asarray(pt(c.pts[0]), float)
                        loc = lambda x, y, ctr=ctr: ctr + np.array([x, y])                          # noqa: E731
                except ValueError as err:
                    raise SketchError(f"curve {c.name!r}: {err}")
                for e in gears.edges(segs, lambda x, y, loc=loc: w(loc(x, y))):
                    edges.append((e, c.name, c.construction))
                c.samples = [list(map(float, loc(x, y))) for x, y in gears.sample(segs)]
                continue
            else:                     # spline, or a projected free-form edge
                from OCP.GeomAPI import GeomAPI_Interpolate
                from OCP.collections import HArray1_gp_Pnt
                ps = [pt(n) for n in c.pts] if c.kind == "spline" else c.samples
                if c.closed and c.kind != "spline" and np.linalg.norm(np.subtract(ps[0], ps[-1])) < 1e-9:
                    ps = ps[:-1]              # a closed curve's samples end where they began
                arr = HArray1_gp_Pnt(1, len(ps))
                for i, p in enumerate(ps):
                    arr.SetValue(i + 1, P3(p))
                it = GeomAPI_Interpolate(arr, bool(c.closed), 1e-9)
                it.Perform()
                if not it.IsDone():
                    raise SketchError(f"curve {c.name!r}: cannot run a smooth curve through its points")
                e = BRepBuilderAPI_MakeEdge(it.Curve()).Edge()
        except SketchError:
            raise
        except Exception:
            raise SketchError(f"curve {c.name!r} has collapsed (zero length or radius)")
        edges.append((e, c.name, c.construction))
    solid = [(e, n) for e, n, k in edges if not k]
    faces, owner = _regions(solid, fr) if solid else ([], [])
    return faces, edges, owner


def _regions(edges, fr):
    """Split the sketch plane by the curves: every bounded cell is a region, named after the
    curves around it; cells an even-odd reading fills (crossing any curve flips in/out) are
    marked as part of the 'profile'. Returns [(face, name, in_profile)]."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.collections import List_TopoDS_Shape
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
    from . import topo
    pts = []
    for e, _ in edges:
        lo, hi = topo.bbox(e)
        pts += [lo, hi]
    pts = np.array(pts)
    size = float(np.max(np.abs(pts - fr.origin))) * 4 + 10
    pln = gp_Pln(gp_Ax3(gp_Pnt(*fr.origin), gp_Dir(*fr.z), gp_Dir(*fr.x)))
    big = BRepBuilderAPI_MakeFace(pln, -size, size, -size, size).Face()
    sp = BRepAlgoAPI_Splitter()
    a, t = List_TopoDS_Shape(), List_TopoDS_Shape()
    a.Append(big)
    for e, _ in edges:
        t.Append(e)
    sp.SetArguments(a)
    sp.SetTools(t)
    sp.SetFuzzyValue(1e-7)
    sp.SetNonDestructive(True)
    sp.Build()
    if not sp.IsDone():
        raise SketchError("the curves could not be split into regions")
    owner = topo.ShapeDict()          # split edge -> curve name
    for e, n in edges:
        imgs = list(sp.Modified(e)) if sp.Modified(e).Size() else [e]
        for x in imgs:
            owner[x] = n
    cells = topo.faces(sp.Shape())
    outer = max(cells, key=lambda f: topo.face_area_centroid(f)[0]) if cells else None
    # neighbours across curve edges; an edge with the same cell on both sides (a dangling
    # line) separates nothing
    efmap = topo.edge_face_map(sp.Shape())
    index = {i: f for i, f in enumerate(cells)}
    cell_of = topo.ShapeDict()
    for i, f in index.items():
        cell_of[f] = i
    adj = {i: set() for i in index}
    for e, fs in efmap.items():
        ids = {cell_of[f] for f in fs if f in cell_of}
        if len(ids) == 2:
            i, j = ids
            adj[i].add(j)
            adj[j].add(i)
    o = next(i for i, f in index.items() if f.IsSame(outer))
    parity, todo = {o: 0}, [o]
    while todo:
        i = todo.pop()
        for j in adj[i]:
            if j not in parity:
                parity[j] = 1 - parity[i]
                todo.append(j)
    final = []
    for i, f in index.items():
        if i == o:
            continue
        f = _drop_dangling(f)
        names = {owner.get(e) for e in topo.edges(f)} - {None}
        final.append([f, "+".join(sorted(names)) or "region", None, parity.get(i, 0) == 1])
    # repeated names get pieces #1, #2... largest first
    groups = {}
    for item in final:
        groups.setdefault(item[1], []).append(item)
    for items in groups.values():
        if len(items) > 1:
            def order(it):               # largest first; equal sizes by position, left to right
                a, c = topo.face_area_centroid(it[0])
                return (-round(a, 6), round(float(c @ fr.x), 6), round(float(c @ fr.y), 6))
            items.sort(key=order)
            for k, it in enumerate(items):
                it[2] = k + 1
    # which curve each edge of the regions came from (for naming what's built from them)
    names_of = []
    for f, *_ in final:
        for e in topo.edges(f):
            n = owner.get(e)
            if n is not None:
                names_of.append((e, n))
    return [tuple(it) for it in final], names_of


def _drop_dangling(f):
    """A cell with a dangling curve end inside keeps that curve as an internal edge; rebuild
    the face from its closed wires only."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_INTERNAL
    from . import topo
    if not any(e.Orientation() == TopAbs_INTERNAL for e in topo.edges(f)):
        return f
    from OCP.BRepTools import BRepTools
    from OCP.ShapeFix import ShapeFix_Face
    outer = BRepTools.OuterWire_s(f)
    mk = BRepBuilderAPI_MakeFace(BRep_Tool.Surface_s(f), outer, True)
    for w in topo.wires(f):
        if not w.IsSame(outer) and w.Closed() and not any(e.Orientation() == TopAbs_INTERNAL for e in topo.edges(w)):
            mk.Add(w)
    fx = ShapeFix_Face(mk.Face())
    fx.Perform()
    return fx.Face()


# ===================================================================== the feature
def _project(ctx, model, name, ref, fr):
    """Flatten an edge of another body onto the sketch plane as fixed geometry."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from . import topo
    if not hasattr(ref, "resolve"):
        raise SketchError(f"curve {name!r}: project an edge, e.g. ('project', plate.edge('+X+Z'))")
    got = ref.resolve(ctx)
    edges = got[1] if isinstance(got, tuple) else got
    if len(edges) != 1:
        raise SketchError(f"curve {name!r}: project one edge at a time ({len(edges)} given)")
    ad = BRepAdaptor_Curve(edges[0])
    to2 = lambda p: [float((p - fr.origin) @ fr.x), float((p - fr.origin) @ fr.y)]       # noqa: E731
    u0, u1 = ad.FirstParameter(), ad.LastParameter()
    a, b = topo.to_np(ad.Value(u0)), topo.to_np(ad.Value(u1))
    c = model.curves[name]
    c.fixed = True
    flat = lambda d: abs(abs(float(np.asarray(d) @ fr.z)) - 1) < 1e-9                     # noqa: E731
    if ad.GetType() == GeomAbs_Line:
        c.shape_kind = "line"
    elif ad.GetType() == GeomAbs_Circle and flat(topo.to_np(ad.Circle().Axis().Direction())):
        circ = ad.Circle()
        c.centre = to2(topo.to_np(circ.Location()))
        c.radius = float(circ.Radius())
        c.shape_kind = "circle" if np.linalg.norm(a - b) < 1e-7 else "arc"
        if c.shape_kind == "arc" and float(topo.to_np(circ.Axis().Direction()) @ fr.z) < 0:
            a, b = b, a                       # arcs run counterclockwise about the sketch's Z
    else:
        c.shape_kind = "spline"
        c.samples = [to2(topo.to_np(ad.Value(u0 + (u1 - u0) * k / 63))) for k in range(64)]
    c.pts = []
    if c.shape_kind != "circle":
        for part, p in (("start", a), ("end", b)):
            model.points[f"{name}.{part}"] = to2(p)
            model.fixed.add(f"{name}.{part}")
            c.pts.append(f"{name}.{part}")


def _sketch_impl(ctx, at=None, points=None, curves=None, constraints=None, spin=0.0):
    """A constrained sketch on the XY plane of `at` (default: the world XY plane). See the
    sketch module's documentation for points, curves, constraints and dimensions.
    The sketch is a reference body: its closed regions are faces for extrude, revolve, loft
    and sweep ('profile': the outline with its holes; or a region by the curves around it);
    every point is an anchor."""
    from .body import Body, FaceName
    from .features import ProfileDesign
    from .geom import Frame
    from .ops import _creation_frame
    from .refs import FaceRef, FrameRef, Handle
    from . import topo
    if isinstance(at, FaceRef):
        at = at.anchor()                 # on a face: its anchor (centre, normal out)
    fr = _creation_frame(ctx, at, spin)
    pts, fixed = {}, set()
    for n, p in (points or {}).items():
        if isinstance(p, (FrameRef, Handle)):
            f = ctx.frame(p)
            q = np.asarray(f.origin) - fr.origin
            pts[n] = (float(q @ fr.x), float(q @ fr.y))
            fixed.add(n)
        else:
            pts[n] = p
    curves = dict(curves or {})
    m = Model(pts, curves, constraints)
    m.fixed |= fixed
    for n, c in curves.items():
        if c[0] == "project":
            _project(ctx, m, n, c[1], fr)
        elif c[0] == "parametric":
            _parametric(m, n)
    st = solve(m)
    if not st["solved"]:
        raise RuntimeError("sketch: these constraints can't all be met: " + "; ".join(st["unmet"] or ["(an arc's ends)"]))
    regions, edges, edge_names = build(m, fr)
    names = []
    for f, name, piece, prof in regions:
        names.append((f, FaceName(ctx.fid, name, piece)))
        if prof:
            names.append((f, FaceName(ctx.fid, "profile")))
    shape = topo.compound([f for f, *_ in regions] + [e for e, _, k in edges if not k])
    w = lambda p: fr.origin + float(p[0]) * fr.x + float(p[1]) * fr.y                     # noqa: E731
    frames = {n: fr.frame_to_local(Frame.make(w(p), fr.x, fr.z)) for n, p in m.points.items()}
    prof = [f for f, *_r, pr in regions if pr]
    if prof:
        c = np.mean([topo.face_area_centroid(f)[1] for f in prof], axis=0)
    else:
        c = np.mean([w(p) for p in m.points.values()], axis=0) if m.points else fr.origin
    frames["center"] = fr.frame_to_local(Frame.make(c, fr.x, fr.z))
    owner = None
    try:
        own = at.owner() if hasattr(at, "owner") else None
        owner = own.body_id if own is not None else None
    except Exception:
        owner = None
    ctx.info["sketch"] = {
        "frame": {"o": list(map(float, fr.origin)), "x": list(map(float, fr.x)), "y": list(map(float, fr.y)), "z": list(map(float, fr.z))},
        "points": {n: [round(v, 9) for v in p] for n, p in m.points.items()},
        "fixed": sorted(m.fixed),
        "curves": {n: _curve_info(c) for n, c in m.curves.items()},
        "dof": st["dof"],
        "regions": [_region_info(f, n, piece, pr, fr) for f, n, piece, pr in regions]}
    params = (("sketch", True), ("owner", owner),
              ("edges", tuple(edge_names)), ("dof", st["dof"]),
              ("curves", tuple((n, e, k) for e, n, k in edges)))     # every curve whole, by name (paths, s.edge("arc"))
    ctx.put(Body(id=ctx.fid, shape=shape, frame=fr, face_names=tuple(names), design=ProfileDesign(frames),
                 kind="profile", role="reference", params=params, history=(ctx.fid,)))


# ----------------------------------------------------------------- parametric curves
_MATH = {k: getattr(math, k) for k in ("sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh",
                                         "tanh", "sqrt", "exp", "log", "log10", "radians", "degrees", "floor", "ceil",
                                         "hypot")}
_MATH.update(pi=math.pi, e=math.e, tau=math.tau, abs=abs, min=min, max=max)


def math_expr(text, names):
    """Compile a math expression (numbers, + - * / ** %, the math functions above, and the
    given names) to a function of those names' values. Nothing else is allowed: no
    attributes, no imports, no strings."""
    import ast
    tree = ast.parse(text, mode="eval")
    ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant, ast.operator,
          ast.unaryop, ast.IfExp, ast.Compare, ast.cmpop)
    for node in ast.walk(tree):
        if not isinstance(node, ok):
            raise SketchError(f"{text!r}: only arithmetic and math functions are allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise SketchError(f"{text!r}: only numbers are allowed")
        if isinstance(node, ast.Name) and node.id not in _MATH and node.id not in names:
            raise SketchError(f"{text!r}: unknown name {node.id!r} (use t and the math functions; put a parameter "
                              f"in with an f-string: f\"{{P.r}}*cos(t)\")")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _MATH):
            raise SketchError(f"{text!r}: only the math functions can be called")
    code = compile(tree, "<curve>", "eval")
    return lambda **kw: float(eval(code, {"__builtins__": {}}, {**_MATH, **kw}))      # noqa: S307 (checked above)


def _parametric(model, name):
    """Sample a parametric curve x(t), y(t) finely; it becomes a smooth curve through the samples."""
    c = model.curves[name]
    vals = {}
    fx, fy = (math_expr(e, {"t"}) for e in c.expr)
    t0, t1 = c.span
    n = 200
    pts = []
    for k in range(n + 1):
        t = t0 + (t1 - t0) * k / n
        try:
            pts.append([fx(t=t, **vals), fy(t=t, **vals)])
        except (ValueError, ZeroDivisionError, OverflowError) as err:
            raise SketchError(f"curve {name!r}: can't evaluate at t={t:g} ({err})")
    if not all(math.isfinite(v) for p in pts for v in p):
        raise SketchError(f"curve {name!r}: the expressions don't give finite numbers over t={t0:g}..{t1:g}")
    span = max(np.ptp(np.array(pts), axis=0))
    if span < 1e-6:
        raise SketchError(f"curve {name!r} has no size")
    c.samples = pts
    c.shape_kind = "parametric"
    c.closed = bool(np.linalg.norm(np.subtract(pts[0], pts[-1])) < 1e-7 * max(1.0, span))
    c.pts = []
    if not c.closed:
        for part, p in (("start", pts[0]), ("end", pts[-1])):
            model.points[f"{name}.{part}"] = list(p)
            model.fixed.add(f"{name}.{part}")
            c.pts.append(f"{name}.{part}")


def _curve_info(c):
    """How the app draws a curve: its kind as built, its points, radius and so on."""
    out = {"kind": c.shape_kind, "pts": list(c.pts), "construction": c.construction, "fixed": c.fixed}
    if c.kind == "circle" or c.shape_kind in ("circle", "arc") and c.kind == "project":
        out["radius"] = c.radius
    if c.centre is not None:
        out["centre"] = list(c.centre)
    if c.samples is not None:
        out["samples"] = c.samples
    if c.kind == "spline":
        out["closed"] = c.closed
    return out


def _region_info(f, name, piece, prof, fr):
    """A region as flat triangles in sketch coordinates, for shading it while drawing."""
    from .export import mesh_faces
    label = name if piece is None else f"{name}#{piece}"
    tris = []
    for _, pos, _n, tri in mesh_faces(f, 0.05):
        q = [[float((p - fr.origin) @ fr.x), float((p - fr.origin) @ fr.y)] for p in pos]
        for i, j, k in tri:
            tris += [q[i], q[j], q[k]]
    return {"name": label, "profile": prof, "tris": [[round(a, 5), round(b, 5)] for a, b in tris]}


def _make_api():
    from .ops import _record
    _sketch_impl.__name__ = "_sketch"
    return _record("create")(_sketch_impl)


sketch = _make_api()
