"""
Fastest-possible preview of the "Inside the evidence" compromise cut: no
zoompan, no crossfades, no burned-in text -- just the four compromise clips,
trimmed with `-ss`/`-t` before `-i` (fast keyframe seek, no full decode),
downscaled to 720p with `-preset ultrafast`, and hard-cut together with the
concat demuxer. This is for judging the footage only.

Hard 10-minute budget: if the video build hasn't finished by then, it stops
and instead writes a 6-frame contact sheet plus one 100% crop per stage.

    .venv/bin/python scripts/build_preview.py

Writes media/preview.mp4 (fast path) OR, on timeout, contact sheets and
crops under media/_candidates/checkpoint/.
"""
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "media" / "preview.mp4"
CROPDIR = ROOT / "media" / "_candidates" / "checkpoint"
TMPDIR = CROPDIR / "tmp_fast"

W, H = 1280, 720
FPS = 25
BUDGET_S = 480
CALL_TIMEOUT = 90

CLAIM = ROOT / "media/_candidates/stage1-claim/pixabay-169445.mp4"
ARCHIVE = ROOT / "media/_candidates/stage2-archive/pexels-854417-video-inside-library.mp4"
LAB_A = ROOT / "media/_candidates/stage3-lab-v2/pexels-31575747.mp4"
LAB_B = ROOT / "media/_candidates/stage3-lab/pixabay-216231.mp4"
# Round 5: new closing beat -- writing up the findings, then the paper going
# public -- replacing the chemical-mixing shot as the sequence's last note.
WRITE = ROOT / "media/_candidates/stage3b-publish/pexels-8534605.mp4"
PUBLISH = ROOT / "media/_candidates/stage3b-publish/pexels-38496194.mp4"

CLAIM_TRIM = (3.0, 6.5)
ARCHIVE_TRIM = (2.0, 6.5)
LAB_A_TRIM = (1.0, 3.25)
LAB_B_TRIM = (2.0, 3.25)
LAB_A_CROP = (700, 400, 3100, 1750)
WRITE_TRIM = (1.0, 3.0)
PUBLISH_TRIM = (8.0, 3.5)

STAGES = {
    "claim": (CLAIM, CLAIM_TRIM, None),
    "archive": (ARCHIVE, ARCHIVE_TRIM, None),
    "lab": (LAB_A, LAB_A_TRIM, LAB_A_CROP),
    "write": (WRITE, WRITE_TRIM, None),
    "publish": (PUBLISH, PUBLISH_TRIM, None),
}

START = time.time()


class BudgetExceeded(Exception):
    pass


def elapsed() -> float:
    return time.time() - START


def check_budget() -> None:
    if elapsed() > BUDGET_S:
        raise BudgetExceeded(f"exceeded {BUDGET_S}s budget at {elapsed():.0f}s")


def run(cmd: list[str]) -> None:
    check_budget()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BudgetExceeded(f"single call exceeded {CALL_TIMEOUT}s: {' '.join(cmd[:6])}")
    if r.returncode:
        raise BudgetExceeded(f"ffmpeg failed: {' '.join(cmd[:6])} ...\n{r.stderr[-2000:]}")


def fast_video() -> None:
    TMPDIR.mkdir(parents=True, exist_ok=True)
    segs = []

    def render_seg(src: Path, start: float, dur: float, crop, out: Path) -> None:
        vf = (f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},scale={W}:{H}:flags=fast_bilinear"
              if crop else f"scale={W}:{H}:flags=fast_bilinear")
        run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur), "-i", str(src),
             "-vf", vf, "-r", str(FPS), "-an",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
             "-pix_fmt", "yuv420p", str(out)])
        segs.append(out)

    a, d = CLAIM_TRIM
    render_seg(CLAIM, a, d, None, TMPDIR / "stage1.mp4")
    a, d = ARCHIVE_TRIM
    render_seg(ARCHIVE, a, d, None, TMPDIR / "stage2.mp4")
    a, d = LAB_A_TRIM
    render_seg(LAB_A, a, d, LAB_A_CROP, TMPDIR / "stage3a.mp4")
    a, d = LAB_B_TRIM
    render_seg(LAB_B, a, d, None, TMPDIR / "stage3b.mp4")
    a, d = WRITE_TRIM
    render_seg(WRITE, a, d, None, TMPDIR / "stage4-write.mp4")
    a, d = PUBLISH_TRIM
    render_seg(PUBLISH, a, d, None, TMPDIR / "stage5-publish.mp4")

    listfile = TMPDIR / "concat.txt"
    listfile.write_text("".join(f"file '{s}'\n" for s in segs))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(OUT)])
    print(f"preview: {OUT} ({OUT.stat().st_size / 1e6:.1f}MB), {elapsed():.0f}s elapsed")


def save_still(src: Path, t: float, crop, out: Path) -> None:
    full = out.with_name(out.stem + "_full.png")
    run(["ffmpeg", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1", str(full)])
    with Image.open(full) as im:
        w, h = im.size
        if crop:
            x, y, cw, ch = crop
        else:
            cw = ch = 900
            x, y = (w - cw) // 2, (h - ch) // 2
        x, y = min(max(x, 0), w - min(cw, w)), min(max(y, 0), h - min(ch, h))
        cw, ch = min(cw, w - x), min(ch, h - y)
        im.crop((x, y, x + cw, y + ch)).save(out)
    full.unlink()


def crop_stills() -> None:
    for name, (src, trim, crop) in STAGES.items():
        t = trim[0] + trim[1] / 2
        save_still(src, t, crop, CROPDIR / f"preview-crop-{name}.png")
        print(f"  100% crop -> preview-crop-{name}.png")


def contact_sheet(name: str, src: Path, trim: tuple, crop) -> Path:
    a, d = trim
    times = [a + d * f for f in (0.05, 0.24, 0.43, 0.62, 0.81, 0.98)]
    thumbs = []
    for i, t in enumerate(times):
        frame = TMPDIR / f"{name}_f{i}.png"
        run(["ffmpeg", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1", str(frame)])
        with Image.open(frame) as im:
            im2 = im.crop((crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3])) if crop else im
            w = 480
            h = int(im2.height * w / im2.width)
            thumbs.append(im2.resize((w, h)))
    cols, rows = 3, 2
    tw, th = thumbs[0].size
    pad = 8
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * pad, rows * th + (rows + 1) * pad), (30, 30, 30))
    for i, th_im in enumerate(thumbs):
        cx, cy = i % cols, i // cols
        sheet.paste(th_im, (pad + cx * (tw + pad), pad + cy * (th + pad)))
    out = CROPDIR / f"preview-contactsheet-{name}.png"
    sheet.save(out)
    return out


def fallback() -> None:
    print(f"BUDGET/ERROR at {elapsed():.0f}s -- switching to contact sheets + crops only", file=sys.stderr)
    global START
    for name, (src, trim, crop) in STAGES.items():
        try:
            contact_sheet(name, src, trim, crop)
            print(f"  contact sheet -> preview-contactsheet-{name}.png")
        except Exception as e:
            print(f"  contact sheet FAILED for {name}: {e}", file=sys.stderr)
        try:
            t = trim[0] + trim[1] / 2
            save_still(src, t, crop, CROPDIR / f"preview-crop-{name}.png")
            print(f"  100% crop -> preview-crop-{name}.png")
        except Exception as e:
            print(f"  crop FAILED for {name}: {e}", file=sys.stderr)


def main() -> None:
    CROPDIR.mkdir(parents=True, exist_ok=True)
    try:
        fast_video()
        crop_stills()
    except BudgetExceeded as e:
        print(str(e), file=sys.stderr)
        fallback()


if __name__ == "__main__":
    main()
