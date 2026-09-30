"""
Build the scroll fly-through: three stock clips in, one frame sequence out.

Run from the project root with the venv python, because the WebP encoding
uses Pillow (already a dependency for the link-preview cards) rather than
an ffmpeg built with libwebp, which most Homebrew builds are not:

    .venv/bin/python scripts/build_flight.py

Inputs are the three approved clips in media/clips/ (see media/PRODUCTION.md
for source and licence). Outputs are media/master.mp4, the QC contact sheet
beside it, and static/flight/frame-NNNN.webp plus manifest.json.

Two deliberate choices worth knowing:

  * The seam grade. Stage 1 is a cold blue night and stage 2 is warm
    lamplight, which is the largest colour jump in the sequence. Rather
    than change clips, the phone is warmed and the archive cooled by a
    small, fixed amount so the half second where they cross has less
    distance to travel.

  * 12 fps, 1280 wide. The sequence is scrubbed by scroll position, not
    played, so the eye reads it as continuous well below video frame
    rates, and every frame saved is bytes a phone does not download.
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
FPS_FRAMES = 12
FRAME_WIDTH = 1280
WEBP_QUALITY = 72
LORES_WIDTH = 240      # the always-available fallback tier: tiny, held fully decoded
LORES_QUALITY = 40
XFADE = 0.5          # seconds of crossfade between stages
TAIL_FADE = 1.5      # seconds of fade into DEEP at the end

# (file, trim start, trim end, per-clip grade). Trims come from looking at
# the contact sheets: the archive tilt ends on blown windows, so it is cut
# before that; the other two are consistent throughout.
STAGES = [
    # Lifting red in the mids and highlights warms the night without
    # cancelling its blue, which a symmetric red-up/blue-down shift does:
    # that turns the whole stage teal instead of moving it toward the
    # archive. Same logic in reverse for the archive.
    ("01-phone-pixabay-169445.mp4", 2.0, 12.0,
     "colorbalance=rm=0.06:rh=0.05"),                 # warm the cold night
    ("02-archive-pexels-18969594.mp4", 0.5, 10.5,
     "colorbalance=bm=0.05:bh=0.04"),                 # cool the warm lamps
    ("03-lab-pixabay-76395.mp4", 1.0, 9.0,
     "eq=saturation=0.92"),                           # ease the green cast
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
    """Normalise, grade, crossfade and fade to the deep field."""
    chains, inputs = [], []
    for i, (name, a, b, grade) in enumerate(STAGES):
        inputs += ["-i", str(CLIPS / name)]
        chains.append(
            f"[{i}:v]trim={a}:{b},setpts=PTS-STARTPTS,"
            f"scale=1920:1080:flags=lanczos,fps={FPS_MASTER},{grade},"
            f"format=yuv420p,setsar=1[v{i}]"
        )

    # Each crossfade eats XFADE seconds of total length.
    lengths = [b - a for _, a, b, _ in STAGES]
    off1 = lengths[0] - XFADE
    off2 = off1 + lengths[1] - XFADE
    total = off2 + lengths[2]

    chains.append(f"[v0][v1]xfade=transition=fade:duration={XFADE}:offset={off1}[x1]")
    chains.append(f"[x1][v2]xfade=transition=fade:duration={XFADE}:offset={off2}[x2]")
    chains.append(f"[x2]fade=t=out:st={total - TAIL_FADE}:d={TAIL_FADE}:color={DEEP}[out]")

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
