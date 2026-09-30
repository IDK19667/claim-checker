"""
Laplacian-variance sharpness score for a still frame, no OpenCV required
(the project venv doesn't carry it). A 3x3 discrete Laplacian convolved
over a grayscale image; the variance of the result is a standard, cheap
proxy for focus/sharpness, higher is sharper. Used to screen fly-through
footage candidates: a soft or upscaled source scores low here even when
its metadata claims 4K.

    .venv/bin/python scripts/frame_sharpness.py path/to/frame.png [more.png ...]
    .venv/bin/python scripts/frame_sharpness.py path/to/frame.png --crop 960,540,480,480

--crop x,y,w,h scores a 100% pixel crop instead of the whole (possibly
already-downscaled-by-ffmpeg) frame, which is the honest way to judge
whether a "4K" source is really that sharp at native resolution.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

KERNEL = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)


def laplacian_variance(gray: np.ndarray) -> float:
    h, w = gray.shape
    out = np.zeros((h - 2, w - 2), dtype=np.float64)
    for dy in range(3):
        for dx in range(3):
            k = KERNEL[dy, dx]
            if k:
                out += k * gray[dy:dy + h - 2, dx:dx + w - 2]
    return float(out.var())


def score(path: Path, crop: tuple[int, int, int, int] | None) -> float:
    with Image.open(path) as im:
        if crop:
            x, y, w, h = crop
            im = im.crop((x, y, x + w, y + h))
        gray = np.asarray(im.convert("L"), dtype=np.float64)
    return laplacian_variance(gray)


def main() -> None:
    args = sys.argv[1:]
    crop = None
    if "--crop" in args:
        i = args.index("--crop")
        x, y, w, h = (int(v) for v in args[i + 1].split(","))
        crop = (x, y, w, h)
        del args[i:i + 2]
    if not args:
        sys.exit("usage: frame_sharpness.py frame.png [...] [--crop x,y,w,h]")
    for p in args:
        s = score(Path(p), crop)
        print(f"{s:9.1f}  {p}")


if __name__ == "__main__":
    main()
