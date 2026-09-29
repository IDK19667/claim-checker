"""
Find candidate footage for the fly-through, using the Pexels API.

Why this exists as a script rather than a handful of ad-hoc commands: the
choice of footage is a production record. Anyone should be able to re-run
the same searches, see the same candidate pool, and check that the clips
that shipped were picked from it rather than found by luck.

The Pexels website is unreachable from this machine (Cloudflare challenges
every request), but `api.pexels.com` and the file CDN both answer, so the
API is the only honest way to search the catalogue.

Usage, with PEXELS_API_KEY in .env:

    .venv/bin/python scripts/pexels_scout.py search  --stage phone
    .venv/bin/python scripts/pexels_scout.py frames  --stage phone --top 5

`search` writes the filtered candidate pool to media/scout/<stage>.json and
prints it. `frames` downloads the top N and builds a labelled strip of three
frames each, which is the only thing that actually settles whether a clip
breaks the imagery rules. No key is ever printed, and nothing here writes to
static/.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "media" / "scout"
API = "https://api.pexels.com/videos/search"

# Anything narrower than this cannot fill a 1920 canvas without upscaling.
MIN_WIDTH = 1920
# Shorter than this is not worth a beat; longer wastes bytes we then trim.
MIN_SECONDS, MAX_SECONDS = 5, 40

# Searches per stage. Several phrasings each, because one query returns one
# photographer's idea of the subject and we want the spread.
QUERIES = {
    "phone": [
        "phone screen glow dark room",
        "hand holding smartphone night",
        "scrolling phone in the dark",
        "smartphone blank screen close up",
        "phone light in dark bedroom",
    ],
    "archive": [
        "old library interior",
        "library bookshelves",
        "archive shelves documents",
        "historic library reading room",
        "rows of old books",
    ],
    "lab": [
        "laboratory equipment close up",
        "laboratory glassware",
        "science laboratory machine",
        "microscope close up",
        "research laboratory interior",
    ],
}


def api_key() -> str:
    """Read the key from the environment or .env. Never print it."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("PEXELS_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("No PEXELS_API_KEY. Put it in .env (which is gitignored).")


def search_one(query: str, key: str, per_page: int = 40) -> list[dict]:
    params = urllib.parse.urlencode({
        "query": query,
        "orientation": "landscape",
        "size": "large",          # Pexels' own word for 4K
        "per_page": per_page,
    })
    req = urllib.request.Request(f"{API}?{params}", headers={"Authorization": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("videos", [])
    except urllib.error.HTTPError as e:
        # The key must never reach stdout, so say what happened, not what was sent.
        sys.exit(f"Pexels API refused the request: HTTP {e.code} {e.reason}")


def best_file(video: dict) -> dict | None:
    """The largest progressive MP4 rendition of this clip."""
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("width")]
    return max(files, key=lambda f: f["width"]) if files else None


def usable(video: dict) -> bool:
    f = best_file(video)
    if not f or f["width"] < MIN_WIDTH or f["width"] < f.get("height", 0):
        return False
    return MIN_SECONDS <= video.get("duration", 0) <= MAX_SECONDS


def collect(stage: str, key: str) -> list[dict]:
    seen, rows = set(), []
    for q in QUERIES[stage]:
        for v in search_one(q, key):
            if v["id"] in seen or not usable(v):
                continue
            seen.add(v["id"])
            f = best_file(v)
            rows.append({
                "id": v["id"],
                "url": v["url"],
                "author": (v.get("user") or {}).get("name", ""),
                "author_url": (v.get("user") or {}).get("url", ""),
                "duration": v.get("duration"),
                "width": f["width"],
                "height": f["height"],
                "fps": round(f.get("fps") or 0, 2),
                "link": f["link"],
                "found_by": q,
            })
    rows.sort(key=lambda r: (-r["width"], -r["duration"]))
    return rows


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def ffmpeg() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


def frames(stage: str, top: int) -> None:
    """Download the top N candidates and build one labelled strip each."""
    rows = json.loads((OUT / f"{stage}.json").read_text())[:top]
    clips, strips = OUT / "clips", OUT / "strips"
    clips.mkdir(parents=True, exist_ok=True)
    strips.mkdir(parents=True, exist_ok=True)
    font = ROOT / "static" / "fonts" / "LibreFranklin.ttf"

    for i, r in enumerate(rows, 1):
        mp4 = clips / f"{r['id']}.mp4"
        if not mp4.exists():
            urllib.request.urlretrieve(r["link"], mp4)
        dur = r["duration"]
        shots = []
        for n, frac in enumerate((0.12, 0.5, 0.88), 1):
            png = strips / f"{r['id']}_{n}.png"
            run([ffmpeg(), "-v", "error", "-y", "-ss", f"{dur * frac:.2f}",
                 "-i", str(mp4), "-frames:v", "1", "-vf", "scale=620:-2", str(png)])
            shots.append(str(png))
        label = (f"{stage[0].upper()}{i}  pexels {r['id']}  {r['width']}x{r['height']}"
                 f"  {r['fps']:g}fps  {dur}s  {r['author']}")
        run([ffmpeg(), "-v", "error", "-y", "-i", shots[0], "-i", shots[1],
             "-i", shots[2], "-filter_complex",
             f"hstack=inputs=3,pad=iw:ih+56:0:56:color=0x0b1226,"
             f"drawtext=fontfile='{font}':text='{label}':fontcolor=0xfafbfc:"
             f"fontsize=30:x=18:y=13",
             str(strips / f"{stage}_{i:02d}_{r['id']}.jpg")])
        print(f"{label}  {r['url']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("search", "frames"))
    p.add_argument("--stage", required=True, choices=sorted(QUERIES))
    p.add_argument("--top", type=int, default=5)
    a = p.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if a.command == "search":
        rows = collect(a.stage, api_key())
        (OUT / f"{a.stage}.json").write_text(json.dumps(rows, indent=1))
        print(f"{len(rows)} candidates for {a.stage}")
        for r in rows[:25]:
            print(f"  {r['id']:>9}  {r['width']}x{r['height']} {r['fps']:g}fps "
                  f"{r['duration']:>3}s  {r['author'][:22]:22}  {r['found_by']}")
    else:
        frames(a.stage, a.top)


if __name__ == "__main__":
    main()
