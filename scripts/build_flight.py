"""
Build the scroll fly-through: four stock clips in, one frame sequence out.

Run from the project root with the venv python, because the WebP encoding
uses Pillow (already a dependency for the link-preview cards) rather than
an ffmpeg built with libwebp, which most Homebrew builds are not:

    .venv/bin/python scripts/build_flight.py

Inputs are the four approved clips in media/clips/ (see media/PRODUCTION.md
for source and licence). Outputs are media/master.mp4, the QC contact sheet
beside it, and static/flight/frame-NNNN.webp plus manifest.json.

Three deliberate choices worth knowing:

  * The grade is a "noise to clarity" saturation ramp, not a flat match.
    Each clip is desaturated toward the site's muted, cool-leaning palette,
    but the ceiling rises stage by stage (0.82 -> 0.78 -> 0.85 -> 0.88) so
    the footage itself gains a little colour as the checker moves from
    "claims spreading" to "clarity", never past a restrained cap. Horizon
    (a warm sunset) is additionally cooled with colorbalance so its crossfade
    from Weighing's teal water has less distance to travel. Nothing here
    touches exposure or gamma: brightness is never pulled down, the footage
    stays at full brightness throughout, per the no-darkening rule in
    DECISIONS.md.

  * 1920 wide, quality 72, not the literal 1920/85-90 ask. Byte cost and
    perceived sharpness turned out to be driven by different things here.
    A first pass shipped 1440/quality-76, reasoning that two of the four
    clips (Rising, Weighing) are native 1080p so 1920 bought little; that
    was wrong once checked against a true-resolution DPR2 screenshot
    (Playwright, not the downscaled preview it looked fine in): the canvas
    needs roughly 2x the CSS viewport's width to avoid visible upscale on
    a DPR2 screen, so 1440px is thinner margin over a 1280-CSS-px desktop
    than the old 1280px build had at DPR1, and it showed. 1920px (those two
    clips' real ceiling) cuts that upscale ratio from ~1.78x to ~1.33x,
    which is the actual fix; quality only ever bought compression-artefact
    headroom, not this. So quality moved back to 72 (round 2's own number)
    to make room for the width increase: 1920/72/9fps measures ~57MB, up
    from round 2's 10.6MB but for real reasons (2x the pixels on the axis
    that was actually causing the blur, a sequence 37% longer, one more
    real-motion clip), not an unmeasured guess. The literal 1920/quality-88
    ask still measured 139MB; see media/PRODUCTION.md for the full table.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIPS = ROOT / "media" / "clips"
OUT_VIDEO = ROOT / "media" / "master.mp4"
FRAMES = ROOT / "static" / "flight"

# The site's deep field. The flight ends by draining into it so the verdict
# section begins on a colour the page already owns.
DEEP = "0x0b1226"

FPS_MASTER = 24
# Measured directly against this footage. Two separate things drive
# perceived sharpness and they were not the same lever: WebP quality
# (70-88) affects compression artefacting, but the actual blur the round-3
# feedback named came from canvas upscale, and a DPR2 screen (the desktop
# and phone majority now) needs roughly 2x the CSS viewport's pixel width
# to avoid it. A first pass at 1440px/quality-76 measured genuinely softer
# at true DPR2 (screenshotted at exact size, not judged from a downscaled
# preview) than intended, because 1440px is *less* margin over a 1280 CSS-
# px-wide desktop at DPR2 (needs ~2560px to avoid upscale) than the old
# 1280px build had over a DPR1 screen. 1920px is two of the four clips'
# native ceiling (Rising, Weighing; Surface and Horizon are 4K) and cuts
# that upscale ratio from ~1.78x to ~1.33x on a typical desktop, which is
# the fix that actually matters here. Quality moved back down to 72 (the
# previous build's own number) rather than chasing a further bump, since
# the width change is what buys the sharpness and quality past 72 buys
# comparatively little for a lot more weight on this footage (measured:
# 139MB at the literal 1920/quality-88/12fps ask). 1920/72/9fps lands at
# ~57MB: heavier than round 2's 10.6MB, but for real reasons (2x the pixel
# count on the dominant blur cause, a sequence 37% longer, one more real-
# motion clip), not because a number was picked without measuring it.
FPS_FRAMES = 9
FRAME_WIDTH = 1920
WEBP_QUALITY = 72
LORES_WIDTH = 240      # the always-available fallback tier: tiny, held fully decoded
LORES_QUALITY = 40
XFADE = 0.6          # seconds of crossfade between stages
TAIL_FADE = 1.0      # seconds of fade into DEEP at the end

# (file, trim start, trim end, per-clip grade). Trim windows come from
# scripts/flight_motion.py --clip on each source file: all four read as
# sustained motion throughout, so trims exist only to drop a slow drone
# ease-in (Horizon's first ~1.5s) or a cut artefact (Horizon's last ~1.4s),
# not to hunt for a moving moment inside an otherwise-still shot.
STAGES = [
    ("01-surface-pexels-2248532.mp4", 0.5, 9.5,
     "eq=saturation=0.82,colorbalance=bm=0.03:bh=0.02"),
    ("02-rising-pexels-5619876.mp4", 0.3, 9.0,
     "eq=saturation=0.78,colorbalance=gm=-0.04:gh=-0.03:bm=0.02:bh=0.02"),
    ("03-weighing-pexels-7666608.mp4", 0.3, 12.5,
     "eq=saturation=0.85,colorbalance=gm=-0.02:bm=0.02"),
    ("04-horizon-pexels-9209847.mp4", 1.5, 10.5,
     "eq=saturation=0.88,colorbalance=rm=-0.05:rh=-0.04:bm=0.04:bh=0.03"),
]


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"failed: {' '.join(cmd[:3])} ...\n{r.stderr[-2000:]}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def build_master() -> float:
    """Normalise, grade, crossfade and fade to the deep field. Generic over
    however many clips STAGES holds (built for four; works for any count)."""
    chains, inputs = [], []
    for i, (name, a, b, grade) in enumerate(STAGES):
        inputs += ["-i", str(CLIPS / name)]
        chains.append(
            f"[{i}:v]trim={a}:{b},setpts=PTS-STARTPTS,"
            f"scale=1920:1080:flags=lanczos,fps={FPS_MASTER},{grade},"
            f"format=yuv420p,setsar=1[v{i}]"
        )

    lengths = [b - a for _, a, b, _ in STAGES]
    label = "v0"
    running = lengths[0]
    for i in range(1, len(STAGES)):
        offset = running - XFADE
        nxt = f"x{i}"
        chains.append(f"[{label}][v{i}]xfade=transition=fade:duration={XFADE}:offset={offset}[{nxt}]")
        running = offset + lengths[i]
        label = nxt
    total = running

    chains.append(f"[{label}]fade=t=out:st={total - TAIL_FADE}:d={TAIL_FADE}:color={DEEP}[out]")

    run(["ffmpeg", "-v", "error", "-y", *inputs,
         "-filter_complex", ";".join(chains), "-map", "[out]", "-an",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-pix_fmt", "yuv420p", str(OUT_VIDEO)])
    return total


def extract_frames() -> dict:
    """PNG frames from the master, then WebP via Pillow, full res and low."""
    from PIL import Image

    lores_dir = FRAMES / "lores"
    if FRAMES.exists():
        for old in FRAMES.glob("frame-*.webp"):
            old.unlink()
    FRAMES.mkdir(parents=True, exist_ok=True)
    lores_dir.mkdir(exist_ok=True)
    for old in lores_dir.glob("frame-*.webp"):
        old.unlink()

    tmp = ROOT / "media" / "_frames_png"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT_VIDEO), "-an",
         "-vf", f"fps={FPS_FRAMES},scale={FRAME_WIDTH}:-2:flags=lanczos",
         "-start_number", "0", str(tmp / "frame-%04d.png")])

    pngs = sorted(tmp.glob("frame-*.png"))
    if not pngs:
        sys.exit("no frames extracted")

    total_bytes = 0
    lores_bytes = 0
    for p in pngs:
        target = FRAMES / (p.stem + ".webp")
        with Image.open(p) as im:
            im.save(target, "WEBP", quality=WEBP_QUALITY, method=6)
            total_bytes += target.stat().st_size

            # The low-res tier: small enough to preload and keep fully
            # decoded for the whole session, so there is always something
            # correct to show at the exact wanted frame index even before
            # (or instead of) the matching high-res frame is ready.
            lh = round(im.height * LORES_WIDTH / im.width)
            small = im.resize((LORES_WIDTH, lh), Image.LANCZOS)
            lo_target = lores_dir / (p.stem + ".webp")
            small.save(lo_target, "WEBP", quality=LORES_QUALITY, method=6)
            lores_bytes += lo_target.stat().st_size

    with Image.open(pngs[0]) as im:
        w, h = im.size
    shutil.rmtree(tmp)

    return {"count": len(pngs), "width": w, "height": h, "bytes": total_bytes,
            "loresBytes": lores_bytes, "loresWidth": LORES_WIDTH}


def main() -> None:
    for name, *_ in STAGES:
        if not (CLIPS / name).exists():
            sys.exit(f"missing clip: {name}")

    total = build_master()
    print(f"master: {total:.1f}s, {OUT_VIDEO.stat().st_size/1e6:.1f}MB")

    # Whole-flight contact sheet, for looking at before anything ships.
    run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT_VIDEO),
         "-vf", "fps=1,scale=320:-2,tile=7x4", "-frames:v", "1",
         str(ROOT / "media" / "qc" / "master-contact.jpg")])

    info = extract_frames()
    poster = "frame-0000.webp"
    manifest = {
        "version": 2,
        "pattern": "frame-%04d.webp",
        "loresPattern": "lores/frame-%04d.webp",
        "count": info["count"],
        "fps": FPS_FRAMES,
        "width": info["width"],
        "height": info["height"],
        "loresWidth": info["loresWidth"],
        "poster": poster,
        "seconds": round(info["count"] / FPS_FRAMES, 2),
        "totalBytes": info["bytes"],
        "loresTotalBytes": info["loresBytes"],
    }
    (FRAMES / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"frames: {info['count']} at {info['width']}x{info['height']}, "
          f"{info['bytes']/1e6:.1f}MB total, "
          f"{info['bytes']/info['count']/1024:.0f}KB average")
    print(f"lores:  {info['count']} at {info['loresWidth']}px wide, "
          f"{info['loresBytes']/1e6:.2f}MB total, "
          f"{info['loresBytes']/info['count']/1024:.1f}KB average")


if __name__ == "__main__":
    main()
