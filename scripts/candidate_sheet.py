"""
Build one contact sheet per footage candidate for the checkpoint review:
top row = the 3 sampled frames (10/50/90%) scaled down to fit, bottom row
= a 100% pixel crop from the same 3 frames at native resolution, so
sharpness can be judged honestly rather than from a downscaled preview.
Also prints the Laplacian-variance sharpness score of each crop.

    .venv/bin/python scripts/candidate_sheet.py <label> <out.png> <crop_x> <crop_y> <crop_size> <frame1.png> <frame2.png> <frame3.png>
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame_sharpness import laplacian_variance

THUMB_W = 480


def main() -> None:
    label, out, cx, cy, csize, *frame_paths = sys.argv[1:]
    cx, cy, csize = int(cx), int(cy), int(csize)
    frames = [Image.open(p).convert("RGB") for p in frame_paths]

    thumbs = []
    for im in frames:
        w, h = im.size
        th = round(h * THUMB_W / w)
        thumbs.append(im.resize((THUMB_W, th), Image.LANCZOS))

    crops = []
    scores = []
    for im in frames:
        w, h = im.size
        x = min(max(cx, 0), w - csize)
        y = min(max(cy, 0), h - csize)
        crop = im.crop((x, y, x + csize, y + csize))
        crops.append(crop)
        gray = np.asarray(crop.convert("L"), dtype=np.float64)
        scores.append(laplacian_variance(gray))

    top_h = max(t.height for t in thumbs)
    row1_w = THUMB_W * len(thumbs) + 8 * (len(thumbs) - 1)
    row2_w = csize * len(crops) + 8 * (len(crops) - 1)
    total_w = max(row1_w, row2_w)
    label_h = 36
    sheet = Image.new("RGB", (total_w, label_h + top_h + 8 + csize + 20), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((4, 6), f"{label}  (bottom row = 100% crop, Laplacian var shown)", fill="black")

    x = 0
    for t in thumbs:
        sheet.paste(t, (x, label_h))
        x += THUMB_W + 8

    x = 0
    y2 = label_h + top_h + 8
    for c, s in zip(crops, scores):
        sheet.paste(c, (x, y2))
        draw.text((x + 4, y2 + csize - 18), f"{s:.1f}", fill="yellow")
        x += csize + 8

    sheet.save(out)
    print(f"{label}: crop scores {[round(s,1) for s in scores]} -> {out}")


if __name__ == "__main__":
    main()
