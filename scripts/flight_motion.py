"""
Measure real camera motion in a video, second by second, so footage is
picked and beats are built from what it actually does rather than an
assumption that a clip contains sustained movement.

Global motion (the camera moving) is told apart from local motion (a hand,
a pipette, a liquid moving while the camera holds still) by comparing a
downsampled, heavily blurred version of each frame pair: blurring erases
small moving subjects but not a frame-wide shift, so the residual after
blur is a decent proxy for camera movement specifically. This cannot tell
a genuine continuous dolly/drone move from, say, handheld shake of the
same magnitude; it is a screening tool, not a replacement for looking at
the actual frames, which every candidate still gets before being kept.

Two modes:

    .venv/bin/python scripts/flight_motion.py
        Scores media/master.mp4 against the beats.json stage boundaries
        currently hard-coded below (used when building the final timeline).

    .venv/bin/python scripts/flight_motion.py --clip path/to/candidate.mp4
        Scores a single candidate clip on its own and prints a summary
        verdict: mean score, the worst (lowest-motion) one-second window,
        and the fraction of the clip that clears a "clearly moving"
        threshold. Used to screen candidates before any get downloaded to
        media/clips/ for real: a still shot with one shaky frame should
        not pass just because its mean looks fine.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "media" / "master.mp4"
FPS = 4  # coarse enough to be fast, fine enough to find sub-second peaks
MOVING_THRESHOLD = 2.5  # score below this reads as "essentially still" on inspection

# Stage boundaries in the current beats.json (seconds into the master).
STAGES = [("phone", 0.0, 9.6), ("archive", 9.6, 19.2), ("lab", 19.2, 25.4)]


def extract(path: Path, tmp: Path) -> list[Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("f-*.png"):
        old.unlink()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path),
                    "-vf", f"fps={FPS},scale=240:-2", str(tmp / "f-%04d.png")],
                   check=True)
    return sorted(tmp.glob("f-*.png"))


def motion_score(a: Image.Image, b: Image.Image) -> float:
    a = a.convert("L").filter(ImageFilter.GaussianBlur(4))
    b = b.convert("L").filter(ImageFilter.GaussianBlur(4))
    return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())


def score_clip(path: Path) -> dict:
    """The reusable part: per-frame motion scores for one video file."""
    tmp = ROOT / "media" / "_motion_frames"
    frames = extract(path, tmp)
    imgs = [Image.open(f) for f in frames]
    scores = [0.0] + [motion_score(imgs[i - 1], imgs[i]) for i in range(1, len(imgs))]
    for im in imgs:
        im.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "scores": scores,
        "fps": FPS,
        "duration": len(scores) / FPS,
        "mean": float(np.mean(scores[1:])) if len(scores) > 1 else 0.0,
        "min": float(np.min(scores[1:])) if len(scores) > 1 else 0.0,
        "moving_fraction": float(np.mean([s >= MOVING_THRESHOLD for s in scores[1:]]))
                           if len(scores) > 1 else 0.0,
    }


def score_one_clip(path: Path) -> None:
    r = score_clip(path)
    print(f"{path.name}: {r['duration']:.1f}s at {r['fps']}fps")
    print(f"  mean motion       {r['mean']:.2f}")
    print(f"  worst 1s window   {r['min']:.2f}  (lower = a quieter/stiller moment)")
    print(f"  moving fraction   {r['moving_fraction']*100:.0f}% of frames score "
          f">= {MOVING_THRESHOLD} (a rough 'clearly moving' bar)")
    verdict = ("sustained motion throughout" if r["moving_fraction"] >= 0.8
               else "mixed: some real movement, some quieter stretches" if r["moving_fraction"] >= 0.4
               else "mostly still, motion is brief or marginal")
    print(f"  reads as: {verdict}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clip", type=Path, help="score this file alone instead of media/master.mp4")
    a = p.parse_args()

    if a.clip:
        if not a.clip.exists():
            sys.exit(f"missing {a.clip}")
        score_one_clip(a.clip)
        return

    if not MASTER.exists():
        sys.exit(f"missing {MASTER}; run scripts/build_flight.py first")
    tmp = ROOT / "media" / "_motion_frames"
    frames = extract(MASTER, tmp)
    imgs = [Image.open(f) for f in frames]
    scores = [0.0] + [motion_score(imgs[i - 1], imgs[i]) for i in range(1, len(imgs))]

    print(f"{len(frames)} frames at {FPS}fps ({len(frames)/FPS:.1f}s)\n")
    print(f"{'t (s)':>6}  {'score':>6}  stage")
    for i, s in enumerate(scores):
        t = i / FPS
        stage = next((name for name, a2, b2 in STAGES if a2 <= t < b2), "verdict")
        bar = "#" * int(s / 2)
        print(f"{t:6.2f}  {s:6.2f}  {stage:8} {bar}")

    print("\nTop motion windows per stage (candidates for a text-free beat):")
    for name, a2, b2 in STAGES:
        idx = [i for i, _ in enumerate(scores) if a2 <= i / FPS < b2]
        if not idx:
            continue
        ranked = sorted(idx, key=lambda i: -scores[i])[:6]
        ranked.sort()
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
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
