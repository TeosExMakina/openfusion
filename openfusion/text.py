"""Text: lettering as flat faces (a profile), ready to emboss or engrave with extrude.

    label = text("v2", size=6, at=lid.face("+Z"))
    extrude(label, 0.6)                  # raised letters, joined into the lid
    extrude(label, -0.6)                 # or engraved: pushed into the lid, cut

The default font is Liberation Sans (bundled, SIL Open Font License), so a script builds the
same letters on every computer. `font=` also takes a .ttf/.otf path or an installed font's
name (those depend on the computer)."""
from __future__ import annotations

import os

import numpy as np

from . import topo

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
BUNDLED = {False: "LiberationSans-Regular.ttf", True: "LiberationSans-Bold.ttf"}
HALIGN = ("left", "center", "right")
VALIGN = ("baseline", "top", "center", "bottom")


def _font(font, bold, size):
    """An OCCT font at `size` points (1 point = 1 mm here)."""
    from OCP.NCollection import NCollection_String
    from OCP.StdPrs import StdPrs_BRepFont
    if font in (None, "sans"):
        path = os.path.join(FONT_DIR, BUNDLED[bool(bold)])
    elif os.path.splitext(str(font))[1].lower() in (".ttf", ".otf", ".ttc"):
        path = os.path.expanduser(str(font))
        if not os.path.exists(path):
            raise ValueError(f"font file {font!r} not found")
    else:
        from OCP.Font import Font_FA_Bold, Font_FA_Regular, Font_StrictLevel_Strict
        from OCP.TCollection import TCollection_AsciiString
        f = StdPrs_BRepFont()
        aspect = Font_FA_Bold if bold else Font_FA_Regular
        if not f.FindAndInit(TCollection_AsciiString(str(font)), aspect, float(size), Font_StrictLevel_Strict):
            raise ValueError(f"no installed font named {font!r} (or give a .ttf path; 'sans' is built in)")
        return f
    f = StdPrs_BRepFont()
    if not f.Init(NCollection_String(path), float(size), 0):
        raise ValueError(f"can't read the font {path}")
    return f


def _line(font, s, halign):
    """One line of text, its baseline on y=0, aligned by its advance width."""
    from OCP import Graphic3d
    from OCP.gp import gp_Ax3
    from OCP.NCollection import NCollection_String
    from OCP.StdPrs import StdPrs_BRepTextBuilder
    h = {"left": Graphic3d.Graphic3d_HTA_LEFT, "center": Graphic3d.Graphic3d_HTA_CENTER,
         "right": Graphic3d.Graphic3d_HTA_RIGHT}[halign]
    shape = StdPrs_BRepTextBuilder().Perform(font, NCollection_String(s), gp_Ax3(), h, Graphic3d.Graphic3d_VTA_BOTTOM)
    return topo.faces(shape)


def cap_height(font_arg=None, bold=False):
    """Height of a capital H for 1 mm of font size (about 0.7 for most fonts)."""
    f = _font(font_arg, bold, 100.0)
    faces = _line(f, "H", "left")
    lo, hi = topo.bbox(topo.compound(faces))
    return float(hi[1] - lo[1]) / 100.0


def layout(s, size, font=None, bold=False, halign="left", valign="baseline", spacing=1.0):
    """The letters of `s` as faces in the XY plane: capitals `size` tall, lines `spacing` x the
    font's line spacing apart. Returns (faces, block box (x0, y0, x1, y1), first baseline y)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    if halign not in HALIGN:
        raise ValueError(f"halign must be one of {', '.join(HALIGN)}")
    if valign not in VALIGN:
        raise ValueError(f"valign must be one of {', '.join(VALIGN)}")
    if not isinstance(s, str) or not s.strip():
        raise ValueError("text needs some characters")
    if not size > 0:
        raise ValueError("size must be positive")
    if not spacing > 0:
        raise ValueError("spacing must be positive")
    points = size / cap_height(font, bold)
    f = _font(font, bold, points)
    step = f.LineSpacing() * spacing
    lines = s.split("\n")
    faces = []
    for k, line in enumerate(lines):
        if not line.strip():
            continue
        fs = _line(f, line, halign)
        if k:
            tr = gp_Trsf()
            tr.SetTranslation(gp_Vec(0.0, -k * step, 0.0))
            fs = [topo.faces(BRepBuilderAPI_Transform(x, tr, True).Shape())[0] for x in fs]
        faces += fs
    if not faces:
        raise ValueError(f"the font has no outlines for {s!r}")
    top, bottom = size, -(len(lines) - 1) * step
    dy = {"baseline": 0.0, "top": -top, "center": -(top + bottom) / 2, "bottom": -bottom}[valign]
    if dy:
        tr = gp_Trsf()
        tr.SetTranslation(gp_Vec(0.0, dy, 0.0))
        faces = [topo.faces(BRepBuilderAPI_Transform(x, tr, True).Shape())[0] for x in faces]
    lo, hi = topo.bbox(topo.compound(faces))
    return faces, (float(lo[0]), bottom + dy, float(hi[0]), top + dy), dy


def _text(ctx, text, size=5.0, at=None, font="sans", bold=False, halign="center", valign="center",
          spacing=1.0, spin=0.0):
    """Lettering on the XY plane of `at` (a face: its centre; an anchor; default the world XY):
    a profile to extrude, positive to emboss, negative to engrave (into the body it sits on).
    size: the height of capital letters in mm. halign 'left' / 'center' / 'right' and valign
    'baseline' / 'top' / 'center' / 'bottom' place the block on the anchor. '\\n' starts a new
    line (spacing scales the gap). font: 'sans' (Liberation Sans, built in), a .ttf/.otf path,
    or an installed font's name. Anchors: 'center' (of the block), 'baseline' (the first
    line's start), 'origin'."""
    from .body import Body, FaceName
    from .features import ProfileDesign
    from .geom import Frame
    from .ops import _creation_frame
    from .refs import FaceRef
    if isinstance(at, FaceRef):
        at = at.anchor()
    fr = _creation_frame(ctx, at, spin)
    faces, (x0, y0, x1, y1), dy = layout(text, float(size), font, bold, halign, valign, spacing)
    shape = topo.transform_shape(topo.compound(faces), fr.matrix())[0]
    placed = topo.faces(shape)
    names = [(f, FaceName(ctx.fid, "profile")) for f in placed]
    edges = tuple((e, "text") for e in topo.edges(shape))
    w = lambda x, y: fr.origin + x * fr.x + y * fr.y                              # noqa: E731
    frames = {"center": fr.frame_to_local(Frame.make(w((x0 + x1) / 2, (y0 + y1) / 2), fr.x, fr.z)),
              "baseline": fr.frame_to_local(Frame.make(w(x0, dy), fr.x, fr.z))}
    owner = None
    try:
        own = at.owner() if hasattr(at, "owner") else None
        owner = own.body_id if own is not None else None
    except Exception:
        owner = None
    params = (("owner", owner), ("edges", edges), ("text", text))
    ctx.put(Body(id=ctx.fid, shape=shape, frame=fr, face_names=tuple(names), design=ProfileDesign(frames),
                 kind="profile", role="reference", params=params, history=(ctx.fid,)))
    ctx.info["text"] = {"width": x1 - x0, "height": y1 - y0, "faces": len(placed)}


def _make_api():
    from .ops import _record
    return _record("create")(_text)


text = _make_api()
