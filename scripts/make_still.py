"""Encode the desk stills for text mode (see scripts/make_still.mjs).

    python scripts/make_still.py <in.png> <out.webp> <max bytes> [room] [<in.png> <out.webp> <max bytes> [room] ...]

WebP through Pillow, the best quality that fits the byte budget. With
"room" the picture gets space above and below (its own top and bottom rows
carried on), so the lamp's shade and the lit wood sit well inside the
ellipse the page fades it with (desk.css masks it to nothing at every edge;
a baked alpha would cost twice the bytes). No new colour is added.
"""
import io
import sys

from PIL import Image

PAD_TOP, PAD_BOTTOM = 0.23, 0.11   # of the height


def room(im):
    w, h = im.size
    top, bottom = round(h * PAD_TOP), round(h * PAD_BOTTOM)
    out = Image.new("RGB", (w, h + top + bottom))
    out.paste(im.crop((0, 0, w, 1)).resize((w, top)), (0, 0))
    out.paste(im, (0, top))
    out.paste(im.crop((0, h - 1, w, h)).resize((w, bottom)), (0, top + h))
    return out


def encode(src, dst, budget, pad):
    im = Image.open(src).convert("RGB")
    if pad:
        im = room(im)
    for q in range(80, 9, -2):
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=q, method=6)
        if buf.tell() <= budget:
            open(dst, "wb").write(buf.getvalue())
            print(f"{dst}: {im.width}x{im.height}, quality {q}, {buf.tell()} bytes")
            return
    sys.exit(f"{src} does not fit {budget} bytes")


if __name__ == "__main__":
    a, jobs = sys.argv[1:], []
    while a:
        if len(a) < 3:
            sys.exit(__doc__)
        pad = len(a) > 3 and a[3] == "room"
        jobs.append((a[0], a[1], int(a[2]), pad))
        a = a[4 if pad else 3:]
    if not jobs:
        sys.exit(__doc__)
    for j in jobs:
        encode(*j)
