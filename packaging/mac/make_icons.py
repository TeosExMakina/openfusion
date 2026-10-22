"""Draws the app and document icons and writes them as .icns (run anywhere: python make_icons.py)."""
import io
import struct

from PIL import Image, ImageDraw, ImageFilter

ENTRIES = [("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
           ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512)]


def render(S):
    k = S / 1024.0
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([100*k, 112*k, 924*k, 936*k], radius=185*k, fill=(0, 0, 0, 90))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18*k)))
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=(int(40 + 18*t), int(46 + 18*t), int(56 + 20*t), 255))
    lines = ImageDraw.Draw(grad)
    for i in range(1, 8):
        x = 100*k + i * 824*k / 8
        for seg in ([(x, 100*k), (x, 924*k)], [(100*k, x), (924*k, x)]):
            lines.line(seg, fill=(78, 88, 102, 255), width=max(1, int(3*k)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([100*k, 100*k, 924*k, 924*k], radius=185*k, fill=255)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    img.alpha_composite(tile)
    d = ImageDraw.Draw(img)
    cx, cy, r = 512*k, 520*k, 300*k
    top = [(cx, cy - r), (cx + r*0.866, cy - r*0.5), (cx, cy), (cx - r*0.866, cy - r*0.5)]
    left = [(cx - r*0.866, cy - r*0.5), (cx, cy), (cx, cy + r), (cx - r*0.866, cy + r*0.5)]
    right = [(cx, cy), (cx + r*0.866, cy - r*0.5), (cx + r*0.866, cy + r*0.5), (cx, cy + r)]
    w = max(2, int(12*k))
    o = (cx, cy + r)
    d.line([o, (o[0] + 150*k, o[1] - 86*k)], fill=(208, 69, 58, 255), width=w)
    d.line([o, (o[0] - 150*k, o[1] - 86*k)], fill=(60, 154, 78, 255), width=w)
    d.polygon(top, fill=(232, 176, 104, 255))
    d.polygon(left, fill=(181, 118, 42, 255))
    d.polygon(right, fill=(139, 86, 26, 255))
    d.line([top[3], top[0], top[1]], fill=(255, 226, 180, 255), width=w, joint="curve")
    return img


def document(S):
    doc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    k = S / 1024.0
    dd = ImageDraw.Draw(doc)
    dd.rounded_rectangle([212*k, 90*k, 812*k, 934*k], radius=40*k, fill=(248, 249, 251, 255), outline=(190, 196, 204, 255), width=max(1, int(8*k)))
    mark = render(S).resize((int(560*k), int(560*k)), Image.LANCZOS)
    doc.alpha_composite(mark, (int(232*k), int(250*k)))
    return doc


def icns(path, draw):
    chunks = b""
    for typ, size in ENTRIES:
        im = draw(size) if size >= 128 else draw(256).resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        data = buf.getvalue()
        chunks += typ.encode() + struct.pack(">I", 8 + len(data)) + data
    with open(path, "wb") as fh:
        fh.write(b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks)


if __name__ == "__main__":
    render(1024).save("icon-1024.png")
    icns("OpenFusion.icns", render)
    icns("OpenFusionPart.icns", document)
    print("wrote OpenFusion.icns, OpenFusionPart.icns, icon-1024.png")
