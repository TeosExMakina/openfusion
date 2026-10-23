"""Primitive solids, built in their own right-handed local frame and named
by the conventions in the design doc.

Shared conventions
  * Local Z is the build direction. Angle 0 is local +X; angles grow
    counterclockwise about +Z (right-hand rule).
  * Planar faces aligned with the local axes are named by outward normal:
    +X -X +Y -Y +Z -Z.
  * Face anchor frames: Z = outward normal. On top/bottom faces X = +X.
    On side faces X points right as seen from outside and Y points up.
  * Dimensions are always positive.
"""
from __future__ import annotations

import math

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeWire
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder,
                             BRepPrimAPI_MakePrism, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus)
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d, gp_Vec

from . import topo
from .body import Body, Design, FaceName, NameError_
from .geom import Frame, unit

UP = np.array([0.0, 0.0, 1.0])
AXES = {"X": np.array([1.0, 0, 0]), "Y": np.array([0, 1.0, 0]), "Z": np.array([0, 0, 1.0])}


def face_frame(point, normal) -> Frame:
    """Anchor frame on a face: Z outward; X = +X on top/bottom faces,
    otherwise X to the right seen from outside so that Y points up."""
    n = unit(normal)
    if abs(n[2]) > 0.999:
        return Frame.make(point, (1, 0, 0), n)
    return Frame.make(point, np.cross(UP, n), n)


def sign_name(normal, tol=1e-6) -> str | None:
    n = unit(normal)
    for ax, v in AXES.items():
        d = float(n @ v)
        if abs(abs(d) - 1) < tol:
            return ("+" if d > 0 else "-") + ax
    return None


def gp_ax2(origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)) -> gp_Ax2:
    return gp_Ax2(gp_Pnt(*map(float, origin)), gp_Dir(*map(float, z)), gp_Dir(*map(float, x)))


def _finish(fid, shape, names, design, kind, frame, role, params) -> Body:
    """Build the body in local coordinates, then move it to its world frame."""
    body = Body(id=fid, shape=shape, frame=Frame.world(), face_names=tuple(names), design=design,
                kind=kind, role=role, params=tuple(sorted(params.items())), history=(fid,))
    m = frame.matrix()
    if np.allclose(m, np.eye(4)):
        return body
    moved = body.moved(m, fid)
    return moved.evolve(history=(fid,))


def _check_positive(**kw):
    for k, v in kw.items():
        if v is None or not (v > 0):
            raise ValueError(f"{k} must be positive (got {v})")


# ===================================================================== box
class BoxDesign(Design):
    kind = "box"

    def __init__(self, lo, size, click=None):
        self.lo = np.asarray(lo, float)
        self.size = np.asarray(size, float)
        self.click = click

    @property
    def hi(self):
        return self.lo + self.size

    def center(self):
        return self.lo + self.size / 2

    def names(self):
        return ["origin", "center", "+X", "-X", "+Y", "-Y", "+Z", "-Z"]

    def _coord(self, sign, i):
        return self.hi[i] if sign == "+" else self.lo[i]

    def get(self, name, params):
        c = self.center()
        if name == "origin":
            return Frame.world()
        if name == "center":
            return Frame.make(c)
        if name == "click" and self.click is not None:
            return Frame.make(self.click)
        if len(name) == 2 and name[0] in "+-" and name[1] in "XYZ":
            i = "XYZ".index(name[1])
            p = c.copy()
            p[i] = self._coord(name[0], i)
            n = np.zeros(3)
            n[i] = 1 if name[0] == "+" else -1
            return face_frame(p, n)
        if len(name) == 4 and name[0] in "+-" and name[2] in "+-":
            i, j = "XYZ".index(name[1]), "XYZ".index(name[3])
            p = c.copy()
            p[i], p[j] = self._coord(name[0], i), self._coord(name[2], j)
            n = np.zeros(3)
            n[i] = 1 if name[0] == "+" else -1
            n[j] = 1 if name[2] == "+" else -1
            k = 3 - i - j
            x = np.zeros(3)
            x[k] = 1
            return Frame.make(p, x, n)
        if len(name) == 3 and all(s in "+-" for s in name):
            p = np.array([self._coord(name[i], i) for i in range(3)])
            n = np.array([1.0 if s == "+" else -1.0 for s in name])
            return Frame.make(p, (1, 0, 0), n)
        raise NameError_(f"box has no anchor '{name}'")


def make_box(fid, frame, role, length, width, height, origin="corner", click=None):
    _check_positive(length=length, width=width, height=height)
    size = np.array([length, width, height], float)
    lo = {"corner": np.zeros(3), "center": -size / 2,
          "center_xy": np.array([-length / 2, -width / 2, 0.0])}.get(origin)
    if lo is None:
        raise ValueError("origin must be 'corner', 'center' or 'center_xy'")
    shape = BRepPrimAPI_MakeBox(gp_Pnt(*lo), float(length), float(width), float(height)).Shape()
    names = [(f, FaceName(fid, sign_name(topo.plane_normal(f)))) for f in topo.faces(shape)]
    design = BoxDesign(lo, size, click)
    return _finish(fid, shape, names, design, "box", frame, role,
                   dict(length=length, width=width, height=height, origin=origin))


# ===================================================================== cylinder / cone
class RevolvedDesign(Design):
    """Cylinder (r1 == r2) or cone. Axis = local Z from z=0 to z=h."""

    def __init__(self, r1, r2, h):
        self.r1, self.r2, self.h = float(r1), float(r2), float(h)
        self.kind = "cylinder" if r1 == r2 else "cone"

    def names(self):
        return ["origin", "axis", "center", "+Z", "-Z", "side"]

    def radius_at(self, z):
        return self.r1 + (self.r2 - self.r1) * (z / self.h)

    def surface(self, angle, z):
        a = math.radians(angle)
        radial = np.array([math.cos(a), math.sin(a), 0.0])
        r = self.radius_at(z)
        p = r * radial + np.array([0, 0, z])
        n = unit(radial + np.array([0, 0, (self.r1 - self.r2) / self.h]))
        tangent = np.array([-math.sin(a), math.cos(a), 0.0])
        return Frame.make(p, tangent, n)

    def get(self, name, params):
        angle = float(params.get("angle", 0.0))
        if name == "origin":
            return Frame.world()
        if name == "axis":
            return Frame.world().spin(angle)
        if name == "center":
            return Frame.make((0, 0, self.h / 2)).spin(angle)
        if name == "+Z":
            return Frame.make((0, 0, self.h)).spin(angle)
        if name == "-Z":
            return Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1)).spin(-angle)
        if name == "side":
            return self.surface(angle, float(params.get("z", self.h / 2)))
        if name == "rim+Z":
            return self.surface(angle, self.h)
        if name == "rim-Z":
            return self.surface(angle, 0.0)
        raise NameError_(f"{self.kind} has no anchor '{name}'")


def _name_revolved(fid, shape):
    names = []
    for f in topo.faces(shape):
        k = topo.surface_kind(f)
        if k == "plane":
            names.append((f, FaceName(fid, sign_name(topo.plane_normal(f)))))
        else:
            names.append((f, FaceName(fid, "side")))
    return names


def make_cylinder(fid, frame, role, r, h):
    _check_positive(r=r, h=h)
    shape = BRepPrimAPI_MakeCylinder(gp_ax2(), float(r), float(h)).Shape()
    return _finish(fid, shape, _name_revolved(fid, shape), RevolvedDesign(r, r, h), "cylinder",
                   frame, role, dict(r=r, h=h))


def make_cone(fid, frame, role, r1, r2, h):
    _check_positive(h=h)
    if r1 < 0 or r2 < 0 or (r1 == 0 and r2 == 0):
        raise ValueError("cone radii must be >= 0 and not both zero")
    shape = BRepPrimAPI_MakeCone(gp_ax2(), float(r1), float(r2), float(h)).Shape()
    return _finish(fid, shape, _name_revolved(fid, shape), RevolvedDesign(r1, r2, h), "cone",
                   frame, role, dict(r1=r1, r2=r2, h=h))


# ===================================================================== sphere
class SphereDesign(Design):
    kind = "sphere"

    def __init__(self, r):
        self.r = float(r)

    def names(self):
        return ["origin", "center", "+Z", "-Z", "surface"]

    def surface(self, lon, lat):
        lo, la = math.radians(lon), math.radians(lat)
        n = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
        east = np.array([-math.sin(lo), math.cos(lo), 0.0])
        return Frame.make(self.r * n, east, n)

    def get(self, name, params):
        if name in ("origin", "center"):
            return Frame.world()
        if name == "+Z":
            return Frame.make((0, 0, self.r))
        if name == "-Z":
            return Frame.make((0, 0, -self.r), (1, 0, 0), (0, 0, -1))
        if name == "surface":
            return self.surface(float(params.get("lon", 0.0)), float(params.get("lat", 0.0)))
        raise NameError_(f"sphere has no anchor '{name}'")


def make_sphere(fid, frame, role, r):
    _check_positive(r=r)
    shape = BRepPrimAPI_MakeSphere(gp_ax2(), float(r)).Shape()
    names = [(f, FaceName(fid, "surface")) for f in topo.faces(shape)]
    return _finish(fid, shape, names, SphereDesign(r), "sphere", frame, role, dict(r=r))


# ===================================================================== torus
class TorusDesign(Design):
    kind = "torus"

    def __init__(self, R, r):
        self.R, self.r = float(R), float(r)

    def names(self):
        return ["origin", "center", "tube", "surface"]

    def get(self, name, params):
        th = math.radians(float(params.get("theta", 0.0)))
        radial = np.array([math.cos(th), math.sin(th), 0.0])
        tangent = np.array([-math.sin(th), math.cos(th), 0.0])
        if name in ("origin", "center", "axis"):
            return Frame.world()
        if name == "tube":
            return Frame.make(self.R * radial, radial, tangent)
        if name == "surface":
            ph = math.radians(float(params.get("phi", 0.0)))
            n = math.cos(ph) * radial + math.sin(ph) * UP
            return Frame.make(self.R * radial + self.r * n, tangent, n)
        raise NameError_(f"torus has no anchor '{name}'")


def make_torus(fid, frame, role, R, r):
    _check_positive(R=R, r=r)
    if r >= R:
        raise ValueError(f"torus tube radius r ({r}) must be smaller than R ({R})")
    shape = BRepPrimAPI_MakeTorus(gp_ax2(), float(R), float(r)).Shape()
    names = [(f, FaceName(fid, "surface")) for f in topo.faces(shape)]
    return _finish(fid, shape, names, TorusDesign(R, r), "torus", frame, role, dict(R=R, r=r))


# ===================================================================== polygon prism
class PolygonDesign(Design):
    kind = "polygon"

    def __init__(self, n, R, h, start_angle):
        self.n, self.R, self.h, self.a0 = n, float(R), float(h), float(start_angle)

    def vertex_angle(self, i):
        return self.a0 + 360.0 * i / self.n

    def names(self):
        return ["origin", "axis", "center", "+Z", "-Z"] + [f"s{i}" for i in range(self.n)]

    def vertex(self, i, z=0.0):
        a = math.radians(self.vertex_angle(i))
        return np.array([self.R * math.cos(a), self.R * math.sin(a), z])

    def get(self, name, params):
        import re
        if name in ("origin", "axis"):
            return Frame.world()
        if name == "center":
            return Frame.make((0, 0, self.h / 2))
        if name == "+Z":
            return Frame.make((0, 0, self.h))
        if name == "-Z":
            return Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1))
        m = re.match(r"^s(\d+)$", name)
        if m:
            i = int(m.group(1)) % self.n
            mid = (self.vertex(i) + self.vertex(i + 1)) / 2 + np.array([0, 0, self.h / 2])
            n = unit(np.array([mid[0], mid[1], 0.0]))
            return face_frame(mid, n)
        m = re.match(r"^v(\d+)(\+Z)?$", name)
        if m:
            i = int(m.group(1)) % self.n
            p = self.vertex(i, self.h if m.group(2) else 0.0)
            radial = unit(np.array([p[0], p[1], 0.0]))
            return Frame.make(p, np.cross(UP, radial), radial)
        raise NameError_(f"polygon has no anchor '{name}'")

    def mirror_name(self, name):
        import re
        m = re.match(r"^s(\d+)$", name)
        if m:
            return f"s{(self.n - 1 - int(m.group(1))) % self.n}"
        m = re.match(r"^v(\d+)(\+Z)?$", name)
        if m:
            return f"v{(self.n - int(m.group(1))) % self.n}{m.group(2) or ''}"
        return super().mirror_name(name)


def make_polygon(fid, frame, role, n, h, r=None, a=None):
    if int(n) != n or n < 3:
        raise ValueError("n must be an integer >= 3")
    n = int(n)
    _check_positive(h=h)
    if (r is None) == (a is None):
        raise ValueError("give exactly one of r (circumradius: vertex v0 at angle 0) "
                         "or a (apothem: midpoint of side s0 at angle 0)")
    if r is not None:
        _check_positive(r=r)
        R, a0 = float(r), 0.0
    else:
        _check_positive(a=a)
        R, a0 = float(a) / math.cos(math.pi / n), -180.0 / n
    design = PolygonDesign(n, R, h, a0)
    poly = BRepBuilderAPI_MakePolygon()
    for i in range(n):
        poly.Add(gp_Pnt(*design.vertex(i)))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    shape = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, float(h))).Shape()
    names = []
    for f in topo.faces(shape):
        nrm = topo.plane_normal(f)
        if abs(nrm[2]) > 0.999:
            names.append((f, FaceName(fid, sign_name(nrm))))
            continue
        ang = math.degrees(math.atan2(nrm[1], nrm[0]))
        best = min(range(n), key=lambda i: abs(((design.vertex_angle(i) + 180.0 / n) - ang + 180) % 360 - 180))
        names.append((f, FaceName(fid, f"s{best}")))
    return _finish(fid, shape, names, design, "polygon", frame, role,
                   dict(n=n, h=h, r=r if r is not None else R, mode="circumradius" if r is not None else "apothem"))


# ===================================================================== coil
class CoilDesign(Design):
    kind = "coil"

    def __init__(self, R, turns, pitch, hand):
        self.R, self.turns, self.pitch = float(R), float(turns), float(pitch)
        self.s = 1.0 if hand == "right" else -1.0

    def names(self):
        return ["origin", "axis", "start", "end", "path"]

    def at(self, t):
        """Point, unit tangent and radial direction at fraction t of the path."""
        ang = self.s * 2 * math.pi * self.turns * t
        radial = np.array([math.cos(ang), math.sin(ang), 0.0])
        p = self.R * radial + np.array([0, 0, self.pitch * self.turns * t])
        d = self.s * np.array([-math.sin(ang), math.cos(ang), 0.0]) * self.R * 2 * math.pi + np.array([0, 0, self.pitch])
        return p, unit(d), radial

    def get(self, name, params):
        if name in ("origin", "axis"):
            return Frame.world()
        if name == "start":
            p, d, radial = self.at(0.0)
            return Frame.make(p, radial, -d)
        if name == "end":
            p, d, radial = self.at(1.0)
            return Frame.make(p, radial, d)
        if name == "path":
            p, d, radial = self.at(float(params.get("t", 0.0)))
            return Frame.make(p, radial, d)
        raise NameError_(f"coil has no anchor '{name}'")

    def mirror_params(self, name, params):
        return dict(params)


# Thread profiles for coil(section=...): (crest half-width, depth, flank angle up, flank angle down)
# for a profile of size P (normally the pitch), angles from the radial direction in degrees.
THREAD_PROFILES = {
    "iso": (1 / 16, 0.5413, 30.0, 30.0),            # ISO metric / unified 60 degree V (basic, 5/8 H deep)
    "acme": (0.3707 / 2, 0.5, 14.5, 14.5),          # Acme 29 degree, half the pitch deep
    "trapezoidal": (0.366 / 2, 0.5, 15.0, 15.0),    # metric trapezoidal (Tr) 30 degree
    "buttress": (0.05, 0.6, 7.0, 45.0),             # 7/45 buttress: the steep flank faces +Z (takes the load)
}
SECTIONS = ("circle", "square") + tuple(THREAD_PROFILES)


def thread_section(name, P):
    """A thread profile's outline in (radial, axial) coordinates, base at 0, crest at +depth, with
    a little extra below the base (2% of P) so it joins into the cylinder it sits on."""
    c, h, up, down = THREAD_PROFILES[name]
    c, h, e = c * P, h * P, 0.02 * P
    tu, td = math.tan(math.radians(up)), math.tan(math.radians(down))
    return [(-e, c + (h + e) * tu), (h, c), (h, -c), (-e, -c - (h + e) * td)]


def make_coil(fid, frame, role, R, turns, pitch, size, section="circle", hand="right", side="outside"):
    _check_positive(R=R, turns=turns, pitch=pitch, size=size)
    if section not in SECTIONS:
        raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
    if hand not in ("right", "left"):
        raise ValueError("hand must be 'right' or 'left'")
    if side not in ("outside", "inside"):
        raise ValueError("side must be 'outside' (a thread on a shaft) or 'inside' (in a hole)")
    if section in THREAD_PROFILES:
        prof2d = thread_section(section, size)
        width = max(z for _, z in prof2d) - min(z for _, z in prof2d)
        depth = max(r for r, _ in prof2d)
        if pitch <= width * 1.0001:
            raise ValueError(f"a {section} thread of size {size:g} is {width:.3f} wide at its base: the pitch must be more "
                             f"(usually size = pitch)")
        if side == "inside" and depth >= R:
            raise ValueError(f"the thread ({depth:.3f} deep) doesn't fit inside radius {R}")
    else:
        if pitch <= size * 1.0001:
            raise ValueError(f"pitch ({pitch}) must be larger than section size ({size}) or the coil self-intersects")
        if size / 2 >= R:
            raise ValueError(f"section size ({size}) must be smaller than 2*R ({2 * R})")
    design = CoilDesign(R, turns, pitch, hand)
    surf = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), float(R))
    s = design.s
    # On the cylinder, u is the angle (radians) and v the height: one turn = (2*pi, pitch).
    line = Geom2d_Line(gp_Pnt2d(0, 0), gp_Dir2d(s * 2 * math.pi, pitch))
    length = turns * math.hypot(2 * math.pi, pitch)
    edge = BRepBuilderAPI_MakeEdge(line, surf, 0.0, length).Edge()
    BRepLib.BuildCurves3d_s(edge)
    spine = BRepBuilderAPI_MakeWire(edge).Wire()
    p0, d0, radial0 = design.at(0.0)
    if section == "circle":
        circ = gp_Circ(gp_ax2(p0, d0, radial0), size / 2)
        prof = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire()
    elif section in THREAD_PROFILES:
        # in the plane through the axis, so the thread has its true shape measured along the axis
        sgn = 1.0 if side == "outside" else -1.0
        poly = BRepBuilderAPI_MakePolygon()
        for dr, dz in prof2d:
            poly.Add(gp_Pnt(*(p0 + sgn * dr * radial0 + dz * np.array([0.0, 0.0, 1.0]))))
        poly.Close()
        prof = poly.Wire()
    else:
        up = unit(np.cross(d0, radial0))
        h = size / 2
        poly = BRepBuilderAPI_MakePolygon()
        for a, b in ((h, h), (-h, h), (-h, -h), (h, -h)):
            poly.Add(gp_Pnt(*(p0 + a * radial0 + b * up)))
        poly.Close()
        prof = poly.Wire()
    pipe = BRepOffsetAPI_MakePipeShell(spine)
    pipe.SetMode(True)  # Frenet: stable along a helix
    pipe.Add(prof)
    pipe.Build()
    if not pipe.MakeSolid():
        raise RuntimeError("coil sweep did not produce a solid")
    shape = pipe.Shape()
    sols = topo.solids(shape)
    if sols:
        shape = sols[0]
    pe = design.at(1.0)[0]
    names = []
    for f in topo.faces(shape):
        area, cen = topo.face_area_centroid(f)
        if topo.surface_kind(f) == "plane" and np.linalg.norm(cen - p0) < size and _is_cap(f, cen, p0):
            names.append((f, FaceName(fid, "start")))
        elif topo.surface_kind(f) == "plane" and np.linalg.norm(cen - pe) < size and _is_cap(f, cen, pe):
            names.append((f, FaceName(fid, "end")))
        elif section == "circle":
            names.append((f, FaceName(fid, "surface")))
        else:
            pt, nrm = topo.face_point_normal_mid(f)
            radial = unit(np.array([pt[0], pt[1], 0.0]))
            dr, dz = float(nrm @ radial), float(nrm[2])
            label = ("outer" if dr > 0 else "inner") if abs(dr) >= abs(dz) else ("top" if dz > 0 else "bottom")
            names.append((f, FaceName(fid, label)))
    params = dict(R=R, turns=turns, pitch=pitch, size=size, section=section, hand=hand)
    if side != "outside":
        params["side"] = side
    return _finish(fid, shape, names, design, "coil", frame, role, params)


def _is_cap(f, cen, p):
    """An end cap of a coil: a flat face through the end point, square-ish to the helix (not a
    flat flank of a thread profile, which also lies near the ends)."""
    n = topo.plane_normal(f)
    radial = np.array([p[0], p[1], 0.0])
    radial = radial / (np.linalg.norm(radial) or 1.0)
    return abs(float(n @ radial)) < 0.5 and abs(float(n[2])) < 0.9


# ===================================================================== gears
class GearDesign(Design):
    """A gear on its axis Z from z=0 to its thickness: anchors at the middle of each end."""
    kind = "gear"

    def __init__(self, thickness, pitch_r):
        self.t, self.pitch_r = float(thickness), float(pitch_r)

    def names(self):
        return ["origin", "center", "+Z", "-Z", "pitch"]

    def get(self, name, params):
        if name == "origin":
            return Frame.world()
        if name == "center":
            return Frame.make((0, 0, self.t / 2))
        if name == "+Z":
            return Frame.make((0, 0, self.t), (1, 0, 0), (0, 0, 1))
        if name == "-Z":
            return Frame.make((0, 0, 0), (1, 0, 0), (0, 0, -1))
        if name == "pitch":                  # on the pitch circle, at tooth 0, mid-thickness
            return Frame.make((self.pitch_r, 0, self.t / 2), (0, 1, 0), (1, 0, 0))
        raise NameError_(f"gear has no anchor '{name}'")


def make_gear(fid, frame, role, module, teeth, thickness, pressure_angle=20.0, backlash=0.0, bore=0.0,
              helix=0.0, herringbone=False, internal=False, rim=None):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from . import gears
    _check_positive(module=module, thickness=thickness)
    if not (-45 < helix < 45):
        raise ValueError("helix angle must be between -45 and 45 degrees")
    r = module * teeth / 2
    profile = gears.ring if internal else gears.spur
    profile(module, teeth, pressure_angle, backlash)                         # checks the numbers
    # a helical gear: the teeth turn by thickness * tan(helix) / r radians from bottom to top
    twist = thickness * math.tan(math.radians(helix)) / r if helix else 0.0
    n = 2 if not twist else max(3, int(abs(math.degrees(twist)) / 6) + 2)
    levels = [(thickness * k / (n - 1), twist * k / (n - 1)) for k in range(n)]
    if herringbone and twist:
        half = [(thickness / 2 * k / (n - 1), twist / 2 * k / (n - 1)) for k in range(n)]
        levels = half + [(thickness - h, a) for h, a in reversed(half[:-1])]
    ts = BRepOffsetAPI_ThruSections(True, len(levels) == 2, 1e-6)
    for h, a in levels:
        segs = profile(module, teeth, pressure_angle, backlash, math.degrees(a))
        ts.AddWire(gears.wire(segs, lambda x, y, h=h: np.array([x, y, h])))
    ts.CheckCompatibility(False)
    ts.Build()
    if not ts.IsDone():
        raise RuntimeError("could not build the gear")
    teeth_solid = ts.Shape()
    if internal:
        outer = rim if rim is not None else 2 * (r + 1.25 * module) + 8 * module
        if outer / 2 <= r + 1.25 * module + 1e-6:
            raise ValueError(f"rim diameter must be more than the tooth roots' {2 * (r + 1.25 * module):.2f} mm")
        cyl = BRepPrimAPI_MakeCylinder(gp_ax2(), outer / 2, thickness).Shape()
        shape = BRepAlgoAPI_Cut(cyl, teeth_solid).Shape()
    else:
        shape = teeth_solid
        if bore:
            if not (0 < bore / 2 < r - 1.25 * module):
                raise ValueError(f"bore must be smaller than the root diameter ({2 * (r - 1.25 * module):.2f} mm)")
            hole = BRepPrimAPI_MakeCylinder(gp_ax2((0, 0, -1)), bore / 2, thickness + 2).Shape()
            shape = BRepAlgoAPI_Cut(shape, hole).Shape()
    sols = topo.solids(shape)
    if len(sols) != 1:
        raise RuntimeError("the gear did not come out as one solid")
    shape = sols[0]
    # ends +Z / -Z, the bore (a gear) or the rim (a ring gear), and the teeth, numbered round from X
    outer_r = ((rim if rim is not None else 2 * (r + 1.25 * module) + 8 * module) / 2) if internal else None
    names = []
    for f in topo.faces(shape):
        kind = topo.surface_kind(f)
        c = topo.face_area_centroid(f)[1]
        if kind == "plane" and abs(abs(topo.plane_normal(f)[2]) - 1) < 1e-6:
            names.append((f, FaceName(fid, "+Z" if c[2] > thickness / 2 else "-Z")))
            continue
        name = "teeth"
        if kind == "cylinder":
            p = topo.face_point_normal_mid(f)[0]
            rho = math.hypot(p[0], p[1])
            if bore and not internal and abs(rho - bore / 2) < 1e-6:
                name = "bore"
            elif internal and abs(rho - outer_r) < 1e-6:
                name = "rim"
        names.append((f, FaceName(fid, name)))
    tooth = [(f, nm) for f, nm in names if nm.name == "teeth"]
    others = [(f, nm) for f, nm in names if nm.name != "teeth"]
    def ang(f):
        c = topo.face_area_centroid(f)[1]
        return (round(math.atan2(c[1], c[0]) % (2 * math.pi), 6), round(float(math.hypot(c[0], c[1])), 6))
    tooth.sort(key=lambda x: ang(x[0]))
    names = others + [(f, FaceName(fid, "teeth", k)) for k, (f, _) in enumerate(tooth)]
    params = dict(module=module, teeth=teeth, thickness=thickness, pressure_angle=pressure_angle)
    if helix:
        params["helix"] = helix
    return _finish(fid, shape, names, GearDesign(thickness, r), "gear", frame, role, params)


BUILDERS = {
    "box": make_box, "cylinder": make_cylinder, "cone": make_cone, "sphere": make_sphere,
    "torus": make_torus, "polygon": make_polygon, "coil": make_coil, "gear": make_gear,
}
