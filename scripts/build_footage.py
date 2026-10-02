#!/usr/bin/env python3
"""Build the three looping clips behind the checking, result and error screens.

These are not the scroll fly-through. The fly-through is a frame sequence
because scroll scrubs it; these three never scrub, so they ship as ordinary
looping video (H.264 plus VP9) and cost a fraction of the bytes.

    .venv/bin/python scripts/build_footage.py

Inputs are three clips in media/clips/ (gitignored, re-fetchable from the
URLs in media/PRODUCTION.md). Outputs, all 1920x600:

  * static/footage/searching.mp4 / .webm / .webp
  * static/footage/weighing.mp4  / .webm / .webp
  * static/footage/verdict.mp4   / .webm / .webp

The .webp beside each pair is a still from the loop's own first frame. It
is what a reader sees under prefers-reduced-motion, and the verdict still
is also what a cold shared link renders instead of loading any video.

Five choices worth knowing.

  * 3.2:1, not 16:9 and not 2.5:1. These play in a band that is 42vh of a
    full-width page: 3.4:1 on a 1440x900 laptop, 4.3:1 on a 1920x1080
    desktop, and about 1.5:1 on a phone. The first cut of these clips was
    2.5:1 and `object-fit: cover` then threw away a third of the height on
    every desktop, which turned the book shot into an abstract and the
    microscope into a detail. At 3.2:1 a desktop keeps nearly the whole
    frame and a phone crops the sides of a shot that is composed across the
    width anyway.

  * Every clip keeps the full source width. Cropping inward magnifies
    whatever print is in frame: page type that is 6 CSS px tall across a
    full-width 4096px source is 12 CSS px tall if you keep only the middle
    two thirds. Keeping the width is half of what makes print unreadable,
    and it is why the searching clip is a book shot with the type already
    out of focus rather than the sharper desk shot that has a typewriter
    parked in the corner (see media/PRODUCTION.md, round 10). What is
    chosen per clip is the row the crop opens on, and the first cut got two
    of the three wrong: the book shot opened 880 rows down and lost the
    hand, leaving a page edge and a cover, and the highlighter shot opened
    at row 0 and lost the marker, leaving a fingernail.

  * The weighing clip carries a defocus, and nothing else does. Its lens
    holds the whole page sharp, and the body type reads at band size: a
    column about office leasing, which is both legible and about the wrong
    subject. The mask takes the picture soft below the marker's own line
    and to the right of the hand, which between them is everywhere the page
    is, and leaves the hand and the marker sharp. It is the shallower depth
    of field the shot was one stop away from, and it is the only way this
    clip meets the "no readable text" rule that does not also blur away the
    sharpness it was chosen for.

  * One grade for all three, near monochrome, and one colour pulled after
    it. The site has no accent colour and never colour-codes a verdict, so
    the footage must not smuggle one in, and two of these three arrive with
    one: a saturated orange book cover and a magenta highlighter. A mild
    desaturation plus a per-clip pull of the offending range was tried
    first and does not survive contact with skin, which is made of the same
    reds as the cover. Taking every clip to about a fifth of its saturation
    settles it: the cover becomes a warm grey-brown, skin stays skin, and
    the verdict clip, near monochrome already, barely changes. Only the
    highlighter survives that, so only the highlighter is pulled. Two
    ranges are left alone deliberately: the whites, which turn the weighing
    clip's paper acid green, and the yellows, which do the same to the
    searching clip's cover.

  * Every loop closes with a crossfade, not a cut and not a ping-pong. The
    tail of the chosen stretch is dissolved over its own head, so the last
    frame already is the first frame. A ping-pong would double the frames
    and make the verdict clip's slow push breathe in and out.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CLIPS = ROOT / "media" / "clips"
OUT = ROOT / "static" / "footage"

WIDTH, HEIGHT = 1920, 600

# No accent colour in the footage. See the module docstring.
BASE_GRADE = "hue=s=0.22,eq=contrast=1.06:brightness=0.008"
PULL = {"weighing": "selectivecolor=magentas=0.35 -1.0 0.60 0"}  # the highlighter

# name, source, crop (w:h:x:y), loop start, loop length, crossfade, defocus.
#
# Loop starts and lengths were picked off a per-frame motion trace: the
# searching and weighing stretches are the steadiest runs of real movement in
# each clip, and the verdict stretch is the steadiest part of a push that
# accelerates slightly as it goes. The weighing loop opens on the frame the
# poster and the reduced-motion still are taken from, which is why it starts
# where the marker's tip is already down on the page.
#
# The defocus is (first row, last row, first column, last column, sigma) in
# output pixels, or None. Sharp above the first row and left of the first
# column, soft past the last of either, linear between.
SHOTS = [
    ("searching", "check-searching-6651088.mp4", "4096:1280:0:620",
     2.52, 5.0, 0.8, None),
    ("weighing", "check-weighing-7710592.mp4", "4096:1280:0:300",
     9.0, 5.0, 0.9, (250, 340, 1150, 1380, 11)),
    ("verdict", "check-verdict-8325856.mp4", "3840:1200:0:330",
     0.0, 4.0, 0.8, None),
]

# The verdict still also serves cold shared links, where it is the only
# image on the page, so it is held to a tighter budget than the other two.
STILL_BUDGET_KB = {"verdict": 100}


def run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(args[:6])} ...")


def fps(src: Path) -> str:
    """The source's frame rate, as the fraction ffprobe reports it."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def chain(name: str, crop: str) -> str:
    pull = PULL.get(name)
    grade = f"{BASE_GRADE},{pull}" if pull else BASE_GRADE
    return f"crop={crop},{grade},scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1"


def defocus(ramp: tuple[int, int, int, int, int], rate: str, length: float) -> str:
    """Sharp in the top left corner the ramps describe, soft outside it.

    maskedmerge takes the base where the mask is black and the blurred copy
    where it is white, so the mask is the larger of two linear gradients, one
    down the frame and one across it. The mask source is given the clip's own
    rate and length: an endless one would keep the output running after the
    picture had ended.
    """
    top, bottom, left, right, sigma = ramp
    g = (f"clip(max((Y-{top})*255/{bottom - top},"
         f"(X-{left})*255/{right - left}),0,255)")
    return (
        f",format=gbrp,split[s][b];"
        f"[b]gblur=sigma={sigma}[bl];"
        f"color=c=black:s={WIDTH}x{HEIGHT}:r={rate}:d={length},format=gbrp,"
        f"geq=r='{g}':g='{g}':b='{g}'[m];"
        f"[s][bl][m]maskedmerge"
    )


def loop_filter(name: str, crop: str, start: float, length: float, fade: float,
                ramp, rate: str) -> str:
    """A clip of `length` seconds whose last frame dissolves into its first.

    The head runs `start` to `start + length`; the tail is the `fade`
    seconds that follow it. xfade plays the tail, dissolves into the head
    over `fade`, then runs the head out, so the output is exactly `length`
    seconds and wraps without a seam.
    """
    c = chain(name, crop)
    graph = (
        f"[0:v]trim=start={start}:end={start + length},setpts=PTS-STARTPTS,{c}[head];"
        f"[0:v]trim=start={start + length}:end={start + length + fade},"
        f"setpts=PTS-STARTPTS,{c}[tail];"
        f"[tail][head]xfade=transition=fade:duration={fade}:offset=0"
    )
    if ramp:
        graph += defocus(ramp, rate, length)
    return graph + ",format=yuv420p[v]"


def build(name: str, clip: str, crop: str, start: float, length: float,
          fade: float, ramp) -> None:
    src = CLIPS / clip
    if not src.exists():
        raise SystemExit(
            f"missing {src}. See media/PRODUCTION.md round 10 for the source URL.")
    graph = loop_filter(name, crop, start, length, fade, ramp, fps(src))

    mp4 = OUT / f"{name}.mp4"
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
         "-filter_complex", graph, "-map", "[v]", "-an",
         "-c:v", "libx264", "-preset", "slow", "-crf", "25",
         "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", "-g", "48", str(mp4)])

    webm = OUT / f"{name}.webm"
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
         "-filter_complex", graph, "-map", "[v]", "-an",
         "-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0",
         "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
         "-pix_fmt", "yuv420p", str(webm)])

    # The still is the loop's own first frame, so the poster and the first
    # painted video frame are the same picture and nothing jumps when the
    # video takes over. WebP goes through Pillow, which is already a
    # dependency and is how the fly-through's stills are written; this
    # ffmpeg has no libwebp.
    still = OUT / f"{name}.webp"
    raw = OUT / f"{name}.still.png"
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
         "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", str(raw)])
    quality = 78
    with Image.open(raw) as im:
        im = im.convert("RGB")
        while True:
            im.save(still, "WEBP", quality=quality, method=6)
            kb = still.stat().st_size / 1024
            budget = STILL_BUDGET_KB.get(name)
            if budget is None or kb <= budget or quality <= 40:
                break
            quality -= 6
    raw.unlink()

    for path in (mp4, webm, still):
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1024:.0f}KB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        print(f"{shot[0]}  ({shot[4]:.1f}s loop, {shot[5]:.1f}s crossfade)")
        build(*shot)


if __name__ == "__main__":
    main()
