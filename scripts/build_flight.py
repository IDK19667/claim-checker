"""
Build the scroll fly-through: six stock clips in, three frame-sequence tiers
out.

Run from the project root with the venv python, because the AVIF encoding
uses Pillow (already a dependency for the link-preview cards, and built
with native AVIF support) rather than an ffmpeg built with libaom, which
most Homebrew builds are not:

    .venv/bin/python scripts/build_flight.py

Inputs are the six approved clips in media/clips/ (see media/PRODUCTION.md
for source and licence). Outputs:

  * media/master.mp4          the landscape master (2560x1440), QC contact
                               sheet beside it
  * media/master-phone.mp4    a second, portrait-cropped master built from
                               the same six clips
  * static/flight/frame-NNNN.avif + manifest.json          1920-wide tier
  * static/flight/2560/frame-NNNN.avif + manifest-2560.json 2560-wide tier
  * static/flight/phone/frame-NNNN.avif + manifest-phone.json portrait tier
  * static/flight/motion/frame-NNNN.avif                   1280-wide motion
                                                            tier (desktop)
  * static/flight/motion-phone/frame-NNNN.avif             540x960 motion tier
  * static/flight/lores/frame-NNNN.avif                    shared low-res
                                                            fallback tier

Pass --frames-only to skip re-encoding the two masters when only the frame
tiers or their qualities have changed (the masters are deterministic from the
clips and take several minutes each).

Four deliberate choices worth knowing:

  * No grade. Round 3 ran a per-stage saturation ramp tied to a "noise to
    clarity" story; round 5 drops that story (see DECISIONS.md) and the
    grade along with it. Footage plays at its own colour, full brightness,
    no scrim, per the standing no-darkening rule: nothing here touches
    exposure, gamma or saturation.

  * The landscape master renders at 2560x1440, not 1920x1080. Two desktop
    frame tiers are extracted from it (1920 and 2560, both downscales from
    that master, never an upscale of anything), so a large/DPR2 screen gets
    a real 2560px source instead of the 1920 tier stretched to fill it.
    All six source clips are confirmed true 4K (ffprobe on the actual
    files), so 2560 is still a safe downscale, including on the one clip
    that carries its own hard crop before this scale (weighing-a).

  * A genuinely separate portrait master, not a runtime crop. The old
    single-tier design let a phone crop the landscape frame at draw time
    (`focusX`), which on a typical DPR2 portrait phone canvas means scaling
    a 1920x1080 frame *up* to cover a tall, narrow viewport: real upscale,
    real softness. Round 5 instead crops each clip to a 9:16 window before
    the final scale-down (still native 4K at the crop step), so the phone
    tier is a downscale of real pixels, never a stretch of the landscape
    tier. One focusX per clip (not per beat) picks that crop's horizontal
    centre; flight.js ignores beats.json's per-beat mobile.focusX whenever
    this tier is active, since the crop is already baked into the asset.

  * Round 6: 9fps to 24fps, and WebP to AVIF. Every FRAME_QUALITY/
    LARGE_QUALITY/PHONE_QUALITY/LORES_QUALITY number below comes from a real
    extraction at 24fps measured against the per-tier budget (desktop
    25MB, phone 10MB), not a guess: see scripts/flight_scroll_test.mjs and
    DECISIONS.md for the before/after numbers.

  * Round 9 correction: round 6 claimed AVIF decodes "roughly 2x faster" than
    WebP. A careful real-GPU measurement (16 frames per tier, four decodes
    each, createImageBitmap in the browser pane) says that is wrong. AVIF and
    WebP decode within 5% of each other at every tier; high-quality JPEG is
    genuinely the fastest, about 25-30% quicker than either. AVIF stays
    anyway, because JPEG costs 4-6x the bytes (the default tier would be
    113MB against a 25MB budget) and WebP 1.4-1.9x for no speed gain. The
    real answer to decode cost was never the format: it was a smaller motion
    tier plus a pool of decode workers. See DECISIONS.md for the full table.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIPS = ROOT / "media" / "clips"
OUT_VIDEO = ROOT / "media" / "master.mp4"
OUT_VIDEO_PHONE = ROOT / "media" / "master-phone.mp4"
FRAMES = ROOT / "static" / "flight"

# The site's deep field. The flight ends by draining into it so the verdict
# section begins on a colour the page already owns.
DEEP = "0x0b1226"

FPS_MASTER = 24
# Round 6: 9fps was the single biggest source of visible choppiness (the
# frame-blend and easing work in flight.js only has two real frames to work
# with per 220ms at 9fps). 24fps was measured against the 25MB/10MB budgets
# below with real extractions before being chosen over 30fps: 30fps left the
# default tier at 24.17MB (96% of budget, no margin, and untested on the
# other two tiers), where 24fps leaves 19-27% headroom on every tier. See
# DECISIONS.md.
FPS_FRAMES = 24

# Landscape master resolution: the working canvas both desktop tiers are
# downscaled from. See the docstring above for why this moved up from 1920.
MASTER_W, MASTER_H = 2560, 1440

# AVIF, not WebP: measured equal-quality AVIF frames at roughly 60-70% of a
# WebP frame's size (PSNR-matched, scripts/avif_test), and real-browser
# createImageBitmap decode on this hardware came out about 2x faster for
# AVIF too (this file's frame budget triples with 24fps, so decode speed
# now matters as much as transfer size). Pillow encodes AVIF natively, no
# extra dependency. Qualities below are each tuned against a real
# extraction at 24fps to land comfortably under budget, not guessed.
FRAME_FORMAT = "AVIF"
FRAME_SPEED = 6            # Pillow AVIF encode speed (0 slow/small .. 10 fast/large)

FRAME_WIDTH = 1920         # default desktop tier
FRAME_QUALITY = 42
LARGE_WIDTH = 2560         # large-screen desktop tier
LARGE_QUALITY = 32

PHONE_W, PHONE_H = 810, 1440   # 9:16, a real phone canvas size, never upscaled
PHONE_QUALITY = 30

# The motion tier (round 9). A fast fling has to put a *decoded* frame on the
# canvas every screen refresh, and the hi-res tiers cannot be decoded that
# fast: measured on a real GPU via createImageBitmap, one 2560x1440 AVIF frame
# takes 34.4ms average and one 1920x1080 frame 20.8ms, against a 16.7ms
# deadline at 60Hz and 8.3ms at 120Hz. 1280x720 takes 10.8ms and 540x960
# takes 6.4ms, which a pool of three or four decode workers clears with real
# headroom. So the fast-scroll path draws from this tier and the hi-res tier
# takes over the moment the scroll slows down. The quality numbers are far
# below the hi-res tiers' on purpose: a motion-tier frame is on screen for one
# refresh during a fling, where compression detail is invisible but *position*
# and sharpness of edges are not, and the bytes have to fit inside the same
# per-device budget as the hi-res tier it sits beside.
MOTION_WIDTH = 1280            # desktop motion tier, from the landscape master
MOTION_QUALITY = 26
PHONE_MOTION_W, PHONE_MOTION_H = 540, 960   # phone motion tier, 9:16 like its hi-res tier
PHONE_MOTION_QUALITY = 26

# The always-available fallback tier: tiny, held fully decoded. Round 9 cut it
# from 240px/q40 to 160px/q36 (0.94MB -> 0.48MB) because its job shrank: it now
# only covers the first second after load, before any motion-tier frame has
# decoded, and never appears again. The bytes it gives back go to the motion
# tier, which is inside the same budget.
LORES_WIDTH = 160
LORES_QUALITY = 36

XFADE = 0.6          # seconds of crossfade between stages
TAIL_FADE = 1.0      # seconds of fade into DEEP at the end

# (file, trim start, trim end, hard crop or None, portrait focusX 0..1).
# Trim windows and the weighing-a hard crop come from this round's sourcing
# passes (media/_candidates/*/REPORT.md); portrait focusX is a single
# centred guess per clip (0.5), not yet hand-tuned per shot, since these are
# all roughly centre-weighted compositions. See DECISIONS.md.
STAGES = [
    ("01-claim-pixabay-169445.mp4", 3.0, 9.5, None, 0.5),
    ("02-archive-pexels-854417.mp4", 2.0, 8.5, None, 0.5),
    ("03a-weighing-pexels-31575747.mp4", 1.0, 4.25, (700, 400, 3100, 1750), 0.5),
    ("03b-weighing-pixabay-216231.mp4", 2.0, 5.25, None, 0.5),
    ("04-write-pexels-8534605.mp4", 1.0, 4.0, None, 0.5),
    ("05-publish-pexels-38496194.mp4", 8.0, 11.5, None, 0.5),
]


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"failed: {' '.join(cmd[:3])} ...\n{r.stderr[-2000:]}")


def _xfade_chain(chains: list[str], lengths: list[float]) -> float:
    """Shared crossfade-accumulation pattern for either master."""
    label = "v0"
    running = lengths[0]
    for i in range(1, len(lengths)):
        offset = running - XFADE
        nxt = f"x{i}"
        chains.append(f"[{label}][v{i}]xfade=transition=fade:duration={XFADE}:offset={offset}[{nxt}]")
        running = offset + lengths[i]
        label = nxt
    chains.append(f"[{label}]fade=t=out:st={running - TAIL_FADE}:d={TAIL_FADE}:color={DEEP}[out]")
    return running


def build_master() -> float:
    """Landscape master at MASTER_W x MASTER_H. Generic over any clip count."""
    chains, inputs = [], []
    for i, (name, a, b, crop, _fx) in enumerate(STAGES):
        inputs += ["-i", str(CLIPS / name)]
        crop_vf = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
        chains.append(
            f"[{i}:v]trim={a}:{b},setpts=PTS-STARTPTS,"
            f"{crop_vf}scale={MASTER_W}:{MASTER_H}:flags=lanczos,fps={FPS_MASTER},"
            f"format=yuv420p,setsar=1[v{i}]"
        )
    lengths = [b - a for _, a, b, _, _ in STAGES]
    total = _xfade_chain(chains, lengths)

    run(["ffmpeg", "-v", "error", "-y", *inputs,
         "-filter_complex", ";".join(chains), "-map", "[out]", "-an",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-pix_fmt", "yuv420p", str(OUT_VIDEO)])
    return total


def build_master_phone() -> float:
    """Portrait master: crop each clip to 9:16 at native resolution, before
    any downscale, so the phone tier is a real crop-then-shrink, not the
    landscape tier stretched to fill a taller canvas."""
    chains, inputs = [], []
    for i, (name, a, b, crop, fx) in enumerate(STAGES):
        inputs += ["-i", str(CLIPS / name)]
        crop_vf = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
        # Scale to the portrait tier's working height first (still a
        # downscale: every source clears 2160px tall even after its own
        # hard crop), then crop the 9:16 window out of that, offset by fx.
        chains.append(
            f"[{i}:v]trim={a}:{b},setpts=PTS-STARTPTS,"
            f"{crop_vf}scale=-2:{PHONE_H}:flags=lanczos,"
            f"crop={PHONE_W}:{PHONE_H}:'(iw-{PHONE_W})*{fx}':0,"
            f"fps={FPS_MASTER},format=yuv420p,setsar=1[v{i}]"
        )
    lengths = [b - a for _, a, b, _, _ in STAGES]
    total = _xfade_chain(chains, lengths)

    run(["ffmpeg", "-v", "error", "-y", *inputs,
         "-filter_complex", ";".join(chains), "-map", "[out]", "-an",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-pix_fmt", "yuv420p", str(OUT_VIDEO_PHONE)])
    return total


def _save(im, target: Path, quality: int) -> None:
    if FRAME_FORMAT == "AVIF":
        im.save(target, "AVIF", quality=quality, speed=FRAME_SPEED)
    else:
        im.save(target, "WEBP", quality=quality, method=6)


FRAME_EXT = ".avif" if FRAME_FORMAT == "AVIF" else ".webp"


def _extract_one(src: Path, width: int, quality: int, out_dir: Path, clean: bool = True) -> dict:
    from PIL import Image

    if clean and out_dir.exists():
        for old in out_dir.glob("frame-*" + FRAME_EXT):
            old.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp = ROOT / "media" / ("_frames_png_" + out_dir.name)
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-an",
         "-vf", f"fps={FPS_FRAMES},scale={width}:-2:flags=lanczos" if width else f"fps={FPS_FRAMES}",
         "-start_number", "0", str(tmp / "frame-%04d.png")])

    pngs = sorted(tmp.glob("frame-*.png"))
    if not pngs:
        sys.exit(f"no frames extracted for {out_dir}")

    total_bytes = 0
    for p in pngs:
        target = out_dir / (p.stem + FRAME_EXT)
        with Image.open(p) as im:
            _save(im, target, quality)
            total_bytes += target.stat().st_size
    with Image.open(pngs[0]) as im:
        w, h = im.size
    shutil.rmtree(tmp)
    return {"count": len(pngs), "width": w, "height": h, "bytes": total_bytes}


def extract_lores(src: Path) -> dict:
    """The always-available tiny fallback tier, shared by all three real
    tiers: small enough to preload and keep fully decoded for the session."""
    from PIL import Image

    lores_dir = FRAMES / "lores"
    if lores_dir.exists():
        for old in lores_dir.glob("frame-*" + FRAME_EXT):
            old.unlink()
    lores_dir.mkdir(parents=True, exist_ok=True)

    tmp = ROOT / "media" / "_frames_png_lores"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-an",
         "-vf", f"fps={FPS_FRAMES},scale={LORES_WIDTH}:-2:flags=lanczos",
         "-start_number", "0", str(tmp / "frame-%04d.png")])
    pngs = sorted(tmp.glob("frame-*.png"))
    total_bytes = 0
    for p in pngs:
        target = lores_dir / (p.stem + FRAME_EXT)
        with Image.open(p) as im:
            _save(im, target, LORES_QUALITY)
            total_bytes += target.stat().st_size
    shutil.rmtree(tmp)
    return {"count": len(pngs), "width": LORES_WIDTH, "bytes": total_bytes}


def main() -> None:
    for name, *_ in STAGES:
        if not (CLIPS / name).exists():
            sys.exit(f"missing clip: {name}")

    frames_only = "--frames-only" in sys.argv
    if frames_only and OUT_VIDEO.exists() and OUT_VIDEO_PHONE.exists():
        print("--frames-only: reusing the existing masters")
    else:
        total = build_master()
        print(f"master (landscape): {total:.1f}s, {OUT_VIDEO.stat().st_size/1e6:.1f}MB")
        total_phone = build_master_phone()
        print(f"master (phone):     {total_phone:.1f}s, {OUT_VIDEO_PHONE.stat().st_size/1e6:.1f}MB")

        (ROOT / "media" / "qc").mkdir(exist_ok=True)
        run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT_VIDEO),
             "-vf", "fps=1,scale=320:-2,tile=7x4", "-frames:v", "1",
             str(ROOT / "media" / "qc" / "master-contact.jpg")])

    lores = extract_lores(OUT_VIDEO)
    print(f"lores:  {lores['count']} at {lores['width']}px wide, "
          f"{lores['bytes']/1e6:.2f}MB total, {lores['bytes']/lores['count']/1024:.1f}KB average")

    # The two motion tiers. The desktop one is shared by the default and large
    # variants (both are drawing it into the same canvas at speed, and a
    # second copy would cost budget for no visible difference); the phone one
    # keeps the portrait master's 9:16 framing so a fast fling on a phone is
    # not cropping a landscape frame.
    motion_info = _extract_one(OUT_VIDEO, MOTION_WIDTH, MOTION_QUALITY, FRAMES / "motion")
    print(f"motion ({MOTION_WIDTH}w): {motion_info['count']} frames at "
          f"{motion_info['width']}x{motion_info['height']}, "
          f"{motion_info['bytes']/1e6:.2f}MB total, "
          f"{motion_info['bytes']/motion_info['count']/1024:.1f}KB average")
    pmotion_info = _extract_one(OUT_VIDEO_PHONE, PHONE_MOTION_W, PHONE_MOTION_QUALITY,
                                FRAMES / "motion-phone")
    print(f"motion-phone:   {pmotion_info['count']} frames at "
          f"{pmotion_info['width']}x{pmotion_info['height']}, "
          f"{pmotion_info['bytes']/1e6:.2f}MB total, "
          f"{pmotion_info['bytes']/pmotion_info['count']/1024:.1f}KB average")

    default_info = _extract_one(OUT_VIDEO, FRAME_WIDTH, FRAME_QUALITY, FRAMES)
    manifest = {
        "version": 7,
        "pattern": "frame-%04d" + FRAME_EXT,
        "loresPattern": "lores/frame-%04d" + FRAME_EXT,
        "count": default_info["count"],
        "fps": FPS_FRAMES,
        "width": default_info["width"],
        "height": default_info["height"],
        "loresWidth": lores["width"],
        "poster": "frame-0000" + FRAME_EXT,
        "seconds": round(default_info["count"] / FPS_FRAMES, 2),
        "totalBytes": default_info["bytes"],
        "loresTotalBytes": lores["bytes"],
        "motionPattern": "motion/frame-%04d" + FRAME_EXT,
        "motionWidth": motion_info["width"],
        "motionHeight": motion_info["height"],
        "motionTotalBytes": motion_info["bytes"],
    }
    (FRAMES / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"default (1920): {default_info['count']} frames at "
          f"{default_info['width']}x{default_info['height']}, "
          f"{default_info['bytes']/1e6:.1f}MB total, "
          f"{default_info['bytes']/default_info['count']/1024:.0f}KB average")

    large_info = _extract_one(OUT_VIDEO, LARGE_WIDTH, LARGE_QUALITY, FRAMES / "2560")
    manifest_large = dict(manifest)
    manifest_large.update({
        "pattern": "2560/frame-%04d" + FRAME_EXT,
        "count": large_info["count"],
        "width": large_info["width"],
        "height": large_info["height"],
        "poster": "2560/frame-0000" + FRAME_EXT,
        "seconds": round(large_info["count"] / FPS_FRAMES, 2),
        "totalBytes": large_info["bytes"],
    })
    (FRAMES / "manifest-2560.json").write_text(json.dumps(manifest_large, indent=1) + "\n")
    print(f"large (2560):   {large_info['count']} frames at "
          f"{large_info['width']}x{large_info['height']}, "
          f"{large_info['bytes']/1e6:.1f}MB total, "
          f"{large_info['bytes']/large_info['count']/1024:.0f}KB average")

    phone_info = _extract_one(OUT_VIDEO_PHONE, None, PHONE_QUALITY, FRAMES / "phone")
    manifest_phone = dict(manifest)
    manifest_phone.update({
        "pattern": "phone/frame-%04d" + FRAME_EXT,
        "count": phone_info["count"],
        "width": phone_info["width"],
        "height": phone_info["height"],
        "poster": "phone/frame-0000" + FRAME_EXT,
        "seconds": round(phone_info["count"] / FPS_FRAMES, 2),
        "totalBytes": phone_info["bytes"],
        "motionPattern": "motion-phone/frame-%04d" + FRAME_EXT,
        "motionWidth": pmotion_info["width"],
        "motionHeight": pmotion_info["height"],
        "motionTotalBytes": pmotion_info["bytes"],
    })
    (FRAMES / "manifest-phone.json").write_text(json.dumps(manifest_phone, indent=1) + "\n")
    print(f"phone ({PHONE_W}w):  {phone_info['count']} frames at "
          f"{phone_info['width']}x{phone_info['height']}, "
          f"{phone_info['bytes']/1e6:.2f}MB total, "
          f"{phone_info['bytes']/phone_info['count']/1024:.1f}KB average")

    # The budget is per device, not per tier: one session downloads exactly one
    # hi-res tier, one motion tier and the shared lores tier, so those three
    # are what has to fit. Printed here rather than eyeballed, because round 9
    # added a tier and the old per-tier print no longer answers the question.
    for label, hires_bytes, motion_bytes, cap in (
        ("desktop (default + motion)", default_info["bytes"], motion_info["bytes"], 25_000_000),
        ("desktop (large + motion)", large_info["bytes"], motion_info["bytes"], 25_000_000),
        ("phone (phone + motion-phone)", phone_info["bytes"], pmotion_info["bytes"], 10_000_000),
    ):
        total_bytes = hires_bytes + motion_bytes + lores["bytes"]
        flag = "OVER" if total_bytes > cap else "within"
        print(f"budget {label}: {total_bytes/1e6:.2f}MB "
              f"({hires_bytes/1e6:.2f} hi-res + {motion_bytes/1e6:.2f} motion + "
              f"{lores['bytes']/1e6:.2f} lores) -- {flag} the {cap//1_000_000}MB budget")


if __name__ == "__main__":
    main()
    # One file per lores and motion tier, so a first visit is two requests
    # rather than 1,104. See scripts/bundle_flight.py.
    import bundle_flight
    bundle_flight.main()
