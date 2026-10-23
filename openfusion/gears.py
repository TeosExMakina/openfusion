"""Gear tooth profiles: involute spur gears (outside teeth), ring gears (inside teeth) and racks.

Sizes follow the usual metric conventions:
  module m        tooth size: pitch diameter = m * teeth (a 20-tooth module-2 gear is 40 mm across
                  at its pitch circle). Gears mesh when they have the same module and pressure
                  angle; their centres are then m * (z1 + z2) / 2 apart (m * (z_ring - z) / 2 for
                  a gear inside a ring).
  pressure angle  20 degrees (the common standard; 14.5 for old gears, 25 for strong ones).
  addendum m, dedendum 1.25 m (0.25 m clearance at the root).
  backlash        play between meshing teeth, as a length along the pitch circle (0 = exact);
                  for printed gears 0.1 to 0.2 mm on each gear is a good start.

A profile is a list of segments in counterclockwise order, closing on itself (a rack's is open):
  ("line", p, q)              p, q: (x, y)
  ("arc", r, a0, a1)          a circle of radius r about the centre, from angle a0 to a1 (radians, CCW)
  ("curve", [p0, p1, ...])    an involute flank, as points on it (a smooth curve through them)
Consecutive segments share their end points exactly.
"""
from __future__ import annotations

import math

ADDENDUM, CLEARANCE = 1.0, 0.25
FLANK_POINTS = 12


def inv(a):
    return math.tan(a) - a


def _check(m, z, pa, backlash):
    if not (m > 0):
        raise ValueError(f"module must be positive (got {m})")
    if z != int(z) or z < 3:
        raise ValueError(f"teeth must be a whole number, at least 3 (got {z})")
    if not (0 < pa < 45):
        raise ValueError(f"pressure angle must be between 0 and 45 degrees (got {pa})")
    if backlash < 0:
        raise ValueError("backlash can't be negative")


def _teeth(z, ra, rf, rb, psi, angle0, alpha, what):
    """The closed outline of z bumps between radius rf and ra whose sides are involutes of the
    circle rb; psi is a bump's half-angle at the pitch circle (angle alpha there)."""
    def phi(rho):
        return math.acos(min(1.0, rb / rho))

    def theta(rho):                      # half-angle of the bump at radius rho
        return psi + inv(alpha) - inv(phi(rho))
    rs = max(rb, rf)                    # the involute starts at the base circle (or the root)
    if rs >= ra:
        raise ValueError(f"{what}: the teeth have no flank (the base circle is outside the tips); use more teeth")
    if theta(ra) <= 1e-6:
        raise ValueError(f"{what}: the teeth come to a point before their tips; use more teeth or a smaller pressure angle")
    gap = 2 * math.pi / z - 2 * theta(rs)
    if gap <= 1e-6:
        raise ValueError(f"{what}: the teeth overlap at their roots; use more teeth")
    pol = lambda r, a: (r * math.cos(a), r * math.sin(a))          # noqa: E731
    radii = [rs + (ra - rs) * (k / (FLANK_POINTS - 1)) ** 1.5 for k in range(FLANK_POINTS)]  # finer near the base
    segs = []
    for i in range(int(z)):
        c = angle0 + 2 * math.pi * i / z
        right = [pol(r, c - theta(r)) for r in radii]                # going out
        left = [pol(r, c + theta(r)) for r in reversed(radii)]       # coming back in
        if rf < rs - 1e-9:
            segs.append(("line", pol(rf, c - theta(rs)), right[0]))
        segs.append(("curve", right))
        segs.append(("arc", ra, c - theta(ra), c + theta(ra)))
        segs.append(("curve", left))
        if rf < rs - 1e-9:
            segs.append(("line", left[-1], pol(rf, c + theta(rs))))
        nxt = c + 2 * math.pi / z
        segs.append(("arc", rf, c + theta(rs), nxt - theta(rs)))
    return segs


def spur(m, z, pressure_angle=20.0, backlash=0.0, angle=0.0):
    """Outline of a spur gear (teeth outside), centred on (0, 0); tooth 0 points along +X
    (turned by `angle` degrees)."""
    _check(m, z, pressure_angle, backlash)
    a = math.radians(pressure_angle)
    r = m * z / 2
    psi = math.pi / (2 * z) - backlash / (2 * r)     # backlash thins each tooth by that much at the pitch circle
    return _teeth(z, r + ADDENDUM * m, r - (ADDENDUM + CLEARANCE) * m, r * math.cos(a), psi,
                  math.radians(angle), a, "gear")


def ring(m, z, pressure_angle=20.0, backlash=0.0, angle=0.0):
    """Outline of the hole of a ring gear (teeth inside): the tooth spaces are shaped like a
    spur gear's teeth, reaching out to its root circle; tooth space 0 is along +X."""
    _check(m, z, pressure_angle, backlash)
    a = math.radians(pressure_angle)
    r = m * z / 2
    psi = math.pi / (2 * z) + backlash / (2 * r)     # the spaces get wider by the backlash
    return _teeth(z, r + (ADDENDUM + CLEARANCE) * m, r - ADDENDUM * m, r * math.cos(a), psi,
                  math.radians(angle), a, "ring gear")


def rack(m, length, pressure_angle=20.0, backlash=0.0):
    """A straight rack's tooth line along +X from (0, 0) on the pitch line, `length` long:
    whole teeth (one every pi*m), then the pitch line to the end. Teeth stand up (+Y) to m
    above the pitch line, spaces go down 1.25 m."""
    _check(m, 3, pressure_angle, backlash)
    if not length > 0:
        raise ValueError("a rack needs a length")
    p = math.pi * m
    ta = math.tan(math.radians(pressure_angle))
    a, d, h, f = ADDENDUM * m * ta, (ADDENDUM + CLEARANCE) * m * ta, ADDENDUM * m, (ADDENDUM + CLEARANCE) * m
    b = backlash / 2
    pts = [(0.0, 0.0)]
    n = int(length / p + 1e-9)
    for k in range(n):
        x = k * p
        pts += [(x + a + b / 2, h), (x + p / 2 - a - b / 2, h),       # a tooth (thinner by the backlash)
                (x + p / 2 + d - b / 2, -f), (x + p - d + b / 2, -f), (x + p, 0.0)]
    if n == 0:
        raise ValueError(f"a rack of module {m:g} needs at least {p:.3f} mm (one tooth)")
    if length - n * p > 1e-9:
        pts.append((length, 0.0))
    return [("line", pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def sample(segs, per_arc_deg=6.0):
    """The profile as a polyline (for drawing it in the app)."""
    out = []
    for s in segs:
        if s[0] == "line":
            pts = [s[1], s[2]]
        elif s[0] == "curve":
            pts = list(s[1])
        else:
            r, a0, a1 = s[1:]
            n = max(2, int(abs(a1 - a0) / math.radians(per_arc_deg)) + 1)
            pts = [(r * math.cos(a0 + (a1 - a0) * k / (n - 1)), r * math.sin(a0 + (a1 - a0) * k / (n - 1))) for k in range(n)]
        out += pts if not out else pts[1:]
    return out


def edges(segs, place):
    """The profile as kernel edges; place(x, y) -> 3D point (numpy array)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt
    from OCP.gp import gp_Pnt
    P = lambda q: gp_Pnt(*map(float, place(*q)))          # noqa: E731
    out = []
    for s in segs:
        if s[0] == "line":
            out.append(BRepBuilderAPI_MakeEdge(P(s[1]), P(s[2])).Edge())
        elif s[0] == "curve":
            arr = HArray1_gp_Pnt(1, len(s[1]))
            for i, q in enumerate(s[1]):
                arr.SetValue(i + 1, P(q))
            it = GeomAPI_Interpolate(arr, False, 1e-9)
            it.Perform()
            out.append(BRepBuilderAPI_MakeEdge(it.Curve()).Edge())
        else:
            from OCP.GC import GC_MakeArcOfCircle
            r, a0, a1 = s[1:]
            am = (a0 + a1) / 2
            arc = GC_MakeArcOfCircle(P((r * math.cos(a0), r * math.sin(a0))), P((r * math.cos(am), r * math.sin(am))),
                                     P((r * math.cos(a1), r * math.sin(a1)))).Value()
            out.append(BRepBuilderAPI_MakeEdge(arc).Edge())
    return out


def wire(segs, place):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    mk = BRepBuilderAPI_MakeWire()
    for e in edges(segs, place):
        mk.Add(e)
    if not mk.IsDone():
        raise RuntimeError("the gear outline doesn't close")
    return mk.Wire()
