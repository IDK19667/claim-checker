#!/usr/bin/env python3
"""Write the fly-through's fallback stills out of the frame sequence itself.

The stills are what a reader sees instead of the footage under reduced
motion, under Save-Data, on a connection that never finishes the sequence,
or when the canvas is unavailable. They are not a separate shoot: each one
is a real frame from the middle of the stage it stands for, so the fallback
and the footage cannot drift apart.

Run after scripts/build_flight.py:

    .venv/bin/python scripts/build_flight_stills.py

One file per entry in app._FLIGHT_STILLS, 960px wide WebP, which is what
the <img> in templates/_flythrough.html asks for.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "static" / "flight"
WIDTH = 960
QUALITY = 82

# One still per stage, named the way templates/_flythrough.html expects.
# The fraction says where in the stage to take the frame from: far enough in
# that the shot has settled, short of the crossfade at the end.
STILLS = [
    ("still-1-claim.webp", "claim", 0.55),
    ("still-2-archive.webp", "archive", 0.5),
    # The weighing stage crossfades two clips in its middle, so half way
    # through is a double exposure. A quarter in is the first clip, clean.
    ("still-3-weighing.webp", "weighing", 0.25),
    ("still-4-write.webp", "write", 0.5),
    ("still-5-publish.webp", "publish", 0.45),
]


def stage_span(beats: dict, stage: str) -> tuple[float, float]:
    """Seconds of master footage this stage covers, across all its beats."""
    times = [(b["from"], b["to"]) for b in beats["beats"]
             if b.get("stage") == stage and "from" in b]
    if not times:
        raise SystemExit(f"no timed beat for stage {stage!r} in beats.json")
    return min(t[0] for t in times), max(t[1] for t in times)


def main() -> None:
    manifest = json.loads((FRAMES / "manifest.json").read_text())
    beats = json.loads((FRAMES / "beats.json").read_text())
    fps = manifest["fps"]
    count = manifest["count"]

    for name, stage, frac in STILLS:
        start, end = stage_span(beats, stage)
        index = min(count - 1, max(0, round((start + (end - start) * frac) * fps)))
        src = FRAMES / (manifest["pattern"] % index)
        if not src.exists():
            raise SystemExit(f"missing frame {src}; run scripts/build_flight.py first")
        with Image.open(src) as im:
            im = im.convert("RGB")
            height = round(im.height * WIDTH / im.width)
            im = im.resize((WIDTH, height), Image.LANCZOS)
            im.save(FRAMES / name, "WEBP", quality=QUALITY, method=6)
        kb = (FRAMES / name).stat().st_size / 1024
        print(f"  {name}  frame {index:04d} ({stage})  {WIDTH}x{height}  {kb:.0f}KB")


if __name__ == "__main__":
    main()
