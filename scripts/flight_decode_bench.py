"""Decode-cost bench for the fly-through's frame tiers.

Writes one sample frame per (tier, format) into static/flight/_bench/ so that
scripts/flight_decode_bench.mjs can time createImageBitmap on each in a real
browser. Formats are compared at matched visual quality, not matched quality
numbers, because the question is "which format decodes fastest for the picture
we ship", not "which wins at q=80".

    .venv/bin/python scripts/flight_decode_bench.py
    node scripts/flight_decode_bench.mjs            # needs the server running

Sources are the shipped AVIF frames rather than the ffmpeg masters, which are
not kept in the repo. Decode cost is a function of dimensions and codec, not of
how the pixels got there, so re-encoding from a shipped frame measures the same
thing; file sizes are reported for scale only.
"""
import json
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLIGHT = ROOT / "static" / "flight"
OUT = FLIGHT / "_bench"

# Three frames with different content: shallow depth of field, dense shelving
# (hard for every codec), and handwriting (fine high-contrast detail).
SAMPLES = [96, 236, 470]

TIERS = [
    ("phone",        (810, 1440),  FLIGHT / "phone"),
    ("default",      (1920, 1080), FLIGHT),
    ("large",        (2560, 1440), FLIGHT),
    ("motion",       (1280, 720),  FLIGHT / "motion"),
    ("motion-phone", (540, 960),   FLIGHT / "motion-phone"),
]

FORMATS = [
    ("avif", "AVIF", {"quality": 42}),
    ("webp", "WEBP", {"quality": 80, "method": 4}),
    ("jpg",  "JPEG", {"quality": 90, "subsampling": 0, "optimize": True}),
]


def main():
    if not FLIGHT.exists():
        sys.exit("static/flight is not built yet; run scripts/build_flight.py")
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for tier, size, srcdir in TIERS:
        for frame in SAMPLES:
            src = srcdir / f"frame-{frame:04d}.avif"
            if not src.exists():
                print(f"skip {tier} frame {frame}: {src} missing")
                continue
            im = Image.open(src).convert("RGB")
            if im.size != size:
                im = im.resize(size, Image.LANCZOS)
            for ext, pil, opts in FORMATS:
                name = f"{tier}-{frame:04d}.{ext}"
                p = OUT / name
                im.save(p, pil, **opts)
                index.append({"tier": tier, "frame": frame, "format": ext,
                              "w": size[0], "h": size[1],
                              "url": f"/static/flight/_bench/{name}",
                              "bytes": p.stat().st_size})
            print(f"{tier} {size[0]}x{size[1]} frame {frame}: "
                  + "  ".join(f"{e[0]} {(OUT / f'{tier}-{frame:04d}.{e[0]}').stat().st_size / 1024:.0f}KB"
                              for e in FORMATS))
    (OUT / "index.json").write_text(json.dumps(index, indent=1))
    print(f"\n{len(index)} files in {OUT}")


if __name__ == "__main__":
    main()
