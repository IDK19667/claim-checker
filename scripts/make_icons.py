"""Draw Evident's icon: a short shelf of books on the night ground, in the
cloths of the desk's own books, the relied-on one marked with a paper dot. Pure
geometry, so the SVG and the PNGs are the same drawing and no font is
needed at 16px."""
import pathlib, sys
from PIL import Image, ImageDraw

OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent / "static" / "icons"
# No mint, blue or purple: the scene's cloths (desk3d.js books) and paper white for the mark.
DEEP, ON_DEEP, MARK = "#12110f", "#f0ede9", "#f4f2ef"
CLOTH = {"strong": "#cfc6b2", "human": "#3a2f26", "lab": "#2a2d29", "weak": "#7e4f3d"}
FOIL = (244, 242, 239, 115)  # paper at 0.45

# On a 512 canvas. Books 72 wide, 14 apart, standing on a light rule.
W, GAP, BASE_Y, BASE_H = 72, 14, 420, 16
BOOKS = [("human", 216), ("strong", 300), ("weak", 132), ("lab", 216)]
X0 = (512 - (len(BOOKS) * W + (len(BOOKS) - 1) * GAP)) // 2
DOT = 1  # the relied-on book


def shapes():
    """Yield (kind, args) in drawing order, in 512 units."""
    yield "rect", (X0 - 20, BASE_Y, 512 - X0 + 20, BASE_Y + BASE_H, ON_DEEP)
    for i, (kind, h) in enumerate(BOOKS):
        x = X0 + i * (W + GAP)
        top = BASE_Y - h
        yield "rect", (x, top, x + W, BASE_Y, CLOTH[kind])
        for dy in (18, 30):
            yield "foil", (x + 10, top + dy, x + W - 10, top + dy + 3)
        if i == DOT:
            yield "dot", (x + W / 2, top - 34, 12)


def svg():
    parts = [f'<rect width="512" height="512" fill="{DEEP}"/>']
    for kind, a in shapes():
        if kind == "rect":
            x1, y1, x2, y2, c = a
            parts.append(f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" fill="{c}"/>')
        elif kind == "foil":
            x1, y1, x2, y2 = a
            parts.append(f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" fill="#f4f2ef" fill-opacity="0.45"/>')
        else:
            cx, cy, r = a
            parts.append(f'<circle cx="{cx:g}" cy="{cy:g}" r="{r}" fill="{MARK}"/>')
    body = "\n  ".join(parts)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">\n'
            '  <!-- A short shelf on the night ground: each book\'s cloth is a kind of\n'
            '       study, and the paper dot marks the one a verdict relied on. -->\n'
            f'  {body}\n</svg>\n')


def png(size, scale=1.0):
    """Draw at 4x and downsample. scale < 1 shrinks the shelf about the
    centre, for the maskable icon's safe zone."""
    S = size * 4
    k = S / 512
    im = Image.new("RGBA", (S, S), DEEP)
    over = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    o = ImageDraw.Draw(over)

    def tx(v, axis_c=256):
        return ((v - axis_c) * scale + axis_c) * k

    for kind, a in shapes():
        if kind == "rect":
            x1, y1, x2, y2, c = a
            d.rectangle([tx(x1), tx(y1), tx(x2) - 1, tx(y2) - 1], fill=c)
        elif kind == "foil":
            x1, y1, x2, y2 = a
            o.rectangle([tx(x1), tx(y1), tx(x2) - 1, tx(y2) - 1], fill=FOIL)
        else:
            cx, cy, r = a
            rr = r * scale * k
            d.ellipse([tx(cx) - rr, tx(cy) - rr, tx(cx) + rr, tx(cy) + rr], fill=MARK)
    im = Image.alpha_composite(im, over).convert("RGB")
    return im.resize((size, size), Image.LANCZOS)


(OUT / "icon.svg").write_text(svg())
png(512).save(OUT / "icon-512.png", optimize=True)
png(192).save(OUT / "icon-192.png", optimize=True)
png(180).save(OUT / "apple-touch-icon.png", optimize=True)
# The maskable icon may be cut to a circle 80% of its width: the shelf is
# drawn at 72% so the whole of it survives the tightest mask.
png(512, scale=0.72).save(OUT / "icon-512-maskable.png", optimize=True)
print("ok")
