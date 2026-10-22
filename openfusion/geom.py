"""Frames and small vector helpers.

A Frame is a right-handed coordinate frame: an origin plus X and Z axes.
Y is always derived as Z x X, so a Frame can never be left-handed.
Every anchor in OpenFusion is a Frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EPS = 1e-9


def vec(x, y=None, z=None) -> np.ndarray:
    if y is None:
        return np.asarray(x, dtype=float).reshape(3)
    return np.array([x, y, z], dtype=float)


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < EPS:
        raise ValueError("zero-length direction")
    return v / n


def any_perpendicular(z) -> np.ndarray:
    z = unit(z)
    trial = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    return unit(trial - np.dot(trial, z) * z)


@dataclass(frozen=True)
class Frame:
    origin: np.ndarray
    x: np.ndarray
    z: np.ndarray

    # -- construction -----------------------------------------------------
    @staticmethod
    def make(origin=(0, 0, 0), x=(1, 0, 0), z=(0, 0, 1)) -> "Frame":
        z = unit(z)
        x = np.asarray(x, dtype=float)
        x = x - np.dot(x, z) * z
        if np.linalg.norm(x) < 1e-7:
            x = any_perpendicular(z)
        return Frame(vec(origin), unit(x), z)

    @staticmethod
    def world() -> "Frame":
        return Frame.make()

    # -- properties -------------------------------------------------------
    @property
    def y(self) -> np.ndarray:
        return np.cross(self.z, self.x)

    def matrix(self) -> np.ndarray:
        """4x4 matrix mapping local coordinates to the parent space."""
        m = np.eye(4)
        m[:3, 0] = self.x
        m[:3, 1] = self.y
        m[:3, 2] = self.z
        m[:3, 3] = self.origin
        return m

    # -- conversions ------------------------------------------------------
    def to_world(self, p) -> np.ndarray:
        p = vec(p)
        return self.origin + p[0] * self.x + p[1] * self.y + p[2] * self.z

    def dir_to_world(self, d) -> np.ndarray:
        d = vec(d)
        return d[0] * self.x + d[1] * self.y + d[2] * self.z

    def to_local(self, p) -> np.ndarray:
        d = vec(p) - self.origin
        return np.array([d @ self.x, d @ self.y, d @ self.z])

    def frame_to_world(self, local: "Frame") -> "Frame":
        """Interpret `local` as expressed in this frame; return it in the parent space."""
        return Frame.make(self.to_world(local.origin), self.dir_to_world(local.x), self.dir_to_world(local.z))

    def frame_to_local(self, world: "Frame") -> "Frame":
        r = np.vstack([self.x, self.y, self.z])
        return Frame.make(self.to_local(world.origin), r @ world.x, r @ world.z)

    # -- modifiers (all return new frames, all stay right-handed) -----------
    def offset(self, x=0.0, y=0.0, z=0.0) -> "Frame":
        """Shift along this frame's own axes."""
        return Frame(self.origin + x * self.x + y * self.y + z * self.z, self.x, self.z)

    def spin(self, angle_deg: float) -> "Frame":
        """Rotate X about this frame's Z (right-hand rule)."""
        a = math.radians(angle_deg)
        x = math.cos(a) * self.x + math.sin(a) * self.y
        return Frame(self.origin, unit(x), self.z)

    def flipped(self) -> "Frame":
        """Reverse Z, keep X (Y flips too, so the frame stays right-handed)."""
        return Frame(self.origin, self.x, -self.z)

    def transformed(self, m: np.ndarray) -> "Frame":
        """Apply a 4x4 affine. Normals use the inverse transpose so non-uniform
        scales and reflections keep Z perpendicular to surfaces; the result is
        re-orthonormalised and right-handed."""
        a = m[:3, :3]
        o = a @ self.origin + m[:3, 3]
        x = a @ self.x
        try:
            z = np.linalg.inv(a).T @ self.z
        except np.linalg.LinAlgError:
            z = a @ self.z
        return Frame.make(o, x, z)

    def __repr__(self) -> str:
        f = lambda v: "(" + ", ".join(f"{c:.4g}" for c in v) + ")"
        return f"Frame(o={f(self.origin)}, x={f(self.x)}, z={f(self.z)})"

    def as_dict(self) -> dict:
        return {"o": [float(c) for c in self.origin], "x": [float(c) for c in self.x],
                "y": [float(c) for c in self.y], "z": [float(c) for c in self.z]}


def rotation_about(axis_point, axis_dir, angle_deg) -> np.ndarray:
    """4x4 rotation about an arbitrary axis (right-hand rule)."""
    k = unit(axis_dir)
    a = math.radians(angle_deg)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    r = np.eye(3) + math.sin(a) * kx + (1 - math.cos(a)) * (kx @ kx)
    m = np.eye(4)
    m[:3, :3] = r
    p = vec(axis_point)
    m[:3, 3] = p - r @ p
    return m


def translation(v) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = vec(v)
    return m


def reflection(plane: Frame) -> np.ndarray:
    """4x4 reflection across the plane through plane.origin with normal plane.z."""
    n = unit(plane.z)
    r = np.eye(3) - 2 * np.outer(n, n)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = plane.origin - r @ plane.origin
    return m


