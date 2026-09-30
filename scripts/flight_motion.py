"""
Measure real camera motion in media/master.mp4, second by second, so the
beats timeline is built from what the footage actually does rather than an
assumption that every clip contains a sweeping pan.

Global motion (the camera moving) is told apart from local motion (a hand,
a pipette, a liquid moving while the camera holds still) by comparing a
downsampled, heavily blurred version of each frame pair: blurring erases
small moving subjects but not a frame-wide shift, so the residual after
blur is a decent proxy for camera movement specifically.

    .venv/bin/python scripts/flight_motion.py

Prints one motion score per second across the whole master, and marks the
top few seconds per stage as candidate text-free "camera move" beats.
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "media" / "master.mp4"
FPS = 4  # coarse enough to be fast, fine enough to find sub-second peaks

# Stage boundaries in the current beats.json (seconds into the master).
STAGES = [("phone", 0.0, 9.6), ("archive", 9.6, 19.2), ("lab", 19.2, 25.4)]


def extract(tmp: Path) -> list[Path]:
    tmp.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(MASTER),
                    "-vf", f"fps={FPS},scale=240:-2", str(tmp / "f-%04d.png")],
                   check=True)
    return sorted(tmp.glob("f-*.png"))


def motion_score(a: Image.Image, b: Image.Image) -> float:
    a = a.convert("L").filter(ImageFilter.GaussianBlur(4))
    b = b.convert("L").filter(ImageFilter.GaussianBlur(4))
    return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())


def main() -> None:
    if not MASTER.exists():
        sys.exit(f"missing {MASTER}; run scripts/build_flight.py first")
    tmp = ROOT / "media" / "_motion_frames"
    frames = extract(tmp)
    imgs = [Image.open(f) for f in frames]
    scores = [0.0] + [motion_score(imgs[i - 1], imgs[i]) for i in range(1, len(imgs))]

    print(f"{len(frames)} frames at {FPS}fps ({len(frames)/FPS:.1f}s)\n")
    print(f"{'t (s)':>6}  {'score':>6}  stage")
    for i, s in enumerate(scores):
        t = i / FPS
        stage = next((name for name, a, b in STAGES if a <= t < b), "verdict")
        bar = "#" * int(s / 2)
        print(f"{t:6.2f}  {s:6.2f}  {stage:8} {bar}")

    print("\nTop motion windows per stage (candidates for a text-free beat):")
    for name, a, b in STAGES:
        idx = [i for i, _ in enumerate(scores) if a <= i / FPS < b]
        if not idx:
            continue
        ranked = sorted(idx, key=lambda i: -scores[i])[:6]
        ranked.sort()
        # Group adjacent high-motion frames into windows.
        windows, cur = [], [ranked[0]]
        for i in ranked[1:]:
            if i - cur[-1] <= 2:
                cur.append(i)
            else:
                windows.append(cur)
                cur = [i]
        windows.append(cur)
        for w in windows:
            t0, t1 = w[0] / FPS, (w[-1] + 1) / FPS
            peak = max(scores[i] for i in w)
            print(f"  {name:8} {t0:5.2f}s - {t1:5.2f}s  peak score {peak:.2f}")

    for im in imgs:
        im.close()
    import shutil
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
