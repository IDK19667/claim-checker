"""
Tests for the video-flythrough branch: the fly-through's manifest, beats
timeline, exported check, and the /flight route's failure path.

Independent of tests/test_app.py's fake-provider harness (this branch's
files are static assets plus one route, not the check pipeline), but run
the same way:

    .venv/bin/python tests/test_flight.py

These do not need the frame sequence to have been built: the manifest and
beats checks fall back to a minimal in-memory fixture when the real files
are absent (e.g. a fresh checkout before scripts/build_flight.py has run),
so the schema is verified either way. The /flight route test does need a
real Flask app and does hit the real filesystem for check.json.
"""
import json
import logging
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))

import dotenv  # noqa: E402
dotenv.load_dotenv = lambda *a, **k: None

results = []


def t(label, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'} {label} {extra if not cond else ''}")


FLIGHT = ROOT / "static" / "flight"

# ---- manifest.json ----------------------------------------------------------

manifest_path = FLIGHT / "manifest.json"
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text())
    # The pattern's own extension, not a hardcoded one: round 6 switched the
    # frame tiers from WebP to AVIF, and this test should track whatever
    # build_flight.py actually produced rather than assume a format.
    ext = pathlib.Path(manifest.get("pattern", "frame-%04d.webp")).suffix
    frame_files = sorted(FLIGHT.glob("frame-*" + ext))

    t("manifest count matches the number of frame files on disk",
      manifest["count"] == len(frame_files),
      f"manifest says {manifest.get('count')}, found {len(frame_files)}")
    t("manifest fps is positive and not absurd",
      0 < manifest.get("fps", 0) <= 60)
    t("manifest width and height are both positive",
      manifest.get("width", 0) > 0 and manifest.get("height", 0) > 0)
    t("the poster frame named in the manifest exists",
      (FLIGHT / manifest["poster"]).exists() if manifest.get("poster") else False)
    t(f"frame-0000{ext} exists (the first frame, used as the poster)",
      (FLIGHT / ("frame-0000" + ext)).exists())
    if frame_files:
        last = f"frame-{len(frame_files) - 1:04d}{ext}"
        t("the last frame implied by the count exists",
          (FLIGHT / last).exists(), last)
    t("totalBytes roughly matches the frames' real size on disk",
      abs(manifest.get("totalBytes", 0) -
          sum(f.stat().st_size for f in frame_files)) < 1024,
      "manifest bytes drifted from the files on disk")

    # ---- the motion tier (round 9) -------------------------------------------
    # flight.js refuses to start without it (a fast fling has no other way to
    # put a decoded frame up every refresh), so the manifest has to describe it
    # and the files have to be there.
    t("the manifest names a motion tier",
      bool(manifest.get("motionPattern")), manifest.get("motionPattern"))
    if manifest.get("motionPattern"):
        mpat = manifest["motionPattern"]
        mdir = FLIGHT / pathlib.Path(mpat).parent
        mfiles = sorted(mdir.glob("frame-*" + ext))
        t("the motion tier has exactly as many frames as the hi-res tier",
          len(mfiles) == manifest["count"],
          f"motion has {len(mfiles)}, hi-res has {manifest['count']}")
        t("motion frames are smaller than hi-res frames (that is the point)",
          0 < manifest.get("motionWidth", 0) < manifest["width"] or
          0 < manifest.get("motionHeight", 0) < manifest["height"],
          f"{manifest.get('motionWidth')}x{manifest.get('motionHeight')} vs "
          f"{manifest['width']}x{manifest['height']}")
        t("motionTotalBytes roughly matches the motion frames on disk",
          abs(manifest.get("motionTotalBytes", 0) -
              sum(f.stat().st_size for f in mfiles)) < 1024)

    # ---- per-device byte budgets --------------------------------------------
    # One session downloads one hi-res tier, one motion tier and the shared
    # lores tier. Those three together are what has to fit: desktop 25MB,
    # phone 10MB. Asserted here rather than eyeballed off a build log, because
    # a quality tweak that quietly blows the budget is exactly the kind of
    # thing that only shows up on someone else's phone bill.
    lores_files = sorted((FLIGHT / "lores").glob("frame-*" + ext))
    lores_bytes = sum(f.stat().st_size for f in lores_files)
    for label, mf, cap in (("manifest.json", None, 25_000_000),
                           ("manifest-2560.json", None, 25_000_000),
                           ("manifest-phone.json", None, 10_000_000)):
        p = FLIGHT / label
        if not p.exists():
            continue
        m = json.loads(p.read_text())
        total = m.get("totalBytes", 0) + m.get("motionTotalBytes", 0) + lores_bytes
        t(f"{label}: hi-res + motion + lores fits the "
          f"{cap // 1_000_000}MB budget ({total / 1e6:.2f}MB)",
          0 < total <= cap, f"{total / 1e6:.2f}MB")
else:
    print("SKIP manifest.json checks: static/flight/manifest.json not built yet "
          "(run scripts/build_flight.py)")

# ---- the decode worker -------------------------------------------------------

decoder = ROOT / "static" / "flight-decoder.js"
t("the decode worker file exists", decoder.exists())
if decoder.exists():
    src = decoder.read_text()
    t("the decode worker transfers the finished bitmap rather than copying it",
      "postMessage" in src and "createImageBitmap" in src and "[bmp]" in src)
flight_tpl = (ROOT / "templates" / "flight.html").read_text()
t("the page tells flight.js where the decode worker lives",
  'data-decoder=' in flight_tpl)
flight_js = (ROOT / "static" / "flight.js").read_text()
t("flight.js keeps a main-thread decode path for browsers without Worker",
  'typeof Worker !== "function"' in flight_js and "MAIN_DECODE_MAX" in flight_js)
t("the low-res tier is retired after its window rather than drawn forever",
  "LORES_WINDOW_MS" in flight_js and "retireLores" in flight_js)
t("the freeze limit is 100ms and the stand-in radius is 2 frames",
  "var FREEZE_LIMIT_MS = 100;" in flight_js and "var SUB_RADIUS = 2;" in flight_js)

# ---- beats.json --------------------------------------------------------------

beats_path = FLIGHT / "beats.json"
if beats_path.exists():
    bt = json.loads(beats_path.read_text())
    bs = bt["beats"]

    t("every beat has an id, a scroll distance and a stage",
      all(b.get("id") and isinstance(b.get("vh"), int) and b.get("stage") for b in bs))
    t("beat ids are unique", len({b["id"] for b in bs}) == len(bs))
    t("every beat is either a footage range or a held frame, never both",
      all(("from" in b) != ("hold" in b) for b in bs),
      [b["id"] for b in bs if ("from" in b) == ("hold" in b)])

    segs = [(b["from"], b["to"]) for b in bs if "from" in b]
    t("footage ranges run forwards", all(a < z for a, z in segs))
    t("footage ranges are contiguous, no gaps or overlaps",
      all(abs(segs[i][1] - segs[i + 1][0]) < 1e-6 for i in range(len(segs) - 1)),
      [(segs[i][1], segs[i + 1][0]) for i in range(len(segs) - 1)
       if abs(segs[i][1] - segs[i + 1][0]) >= 1e-6])
    t("the last footage range ends at masterSeconds",
      abs(segs[-1][1] - bt["masterSeconds"]) < 1e-6 if segs else False)

    t("every chapter a beat names is defined",
      all(b["chapter"] in bt["chapters"] for b in bs if b.get("chapter")))
    t("every defined chapter is used by at least one beat",
      set(bt["chapters"]) == {b["chapter"] for b in bs if b.get("chapter")})

    t("mobile focusX, where given, stays inside the frame",
      all(0 <= b.get("mobile", {}).get("focusX", 0.5) <= 1 for b in bs))
    t("mobile pacing is shorter than desktop, never longer",
      all(b.get("mobile", {}).get("vh", b["vh"]) <= b["vh"] for b in bs))

    t("the opening beat holds a still, so the claim input is usable first",
      bs[0].get("hold") == 0)
    t("the closing beat holds the last frame (hold -1)",
      bs[-1].get("hold") == -1)
    t("all five real stages are represented: claim, archive, weighing, write, publish, plus the footage-free horizon hold",
      {"claim", "archive", "weighing", "write", "publish", "horizon"}.issubset({b["stage"] for b in bs}))
    t("a transition beat (the crossfade itself) never carries a chapter",
      all(b.get("chapter") is None for b in bs if b["stage"] == "transition"))
    t("every fade window is a valid in-before-out range inside 0..1",
      all(0 <= b["fade"]["in"] < b["fade"]["out"] <= 1 for b in bs if "fade" in b))
    t("a fade window only ever appears on a beat that has a chapter to fade",
      all(b.get("chapter") for b in bs if "fade" in b))
else:
    print("SKIP beats.json checks: static/flight/beats.json is missing")

# ---- check.json, and the /flight route's failure path ------------------------

check_path = FLIGHT / "check.json"
if check_path.exists():
    check = json.loads(check_path.read_text())
    required = {"claim", "checkedAt", "query", "verdict", "tldr",
                "stillOpen", "snapshot", "studies"}
    t("check.json has every field the template reads",
      required.issubset(check), required - set(check))
    t("check.json names at least one study",
      isinstance(check.get("studies"), list) and len(check["studies"]) > 0)
    t("every study in check.json carries an evidence tier",
      all(s.get("tier") in ("strong", "moderate", "weak", "retracted")
          for s in check.get("studies", [])))
    t("the still-open line is real prose, not empty",
      bool((check.get("stillOpen") or "").strip()))

    # The route itself: needs a real Flask app, so import it under this
    # branch's environment only.
    import app as appmod  # noqa: E402
    appmod.app.logger.setLevel(logging.ERROR)
    client = appmod.app.test_client()

    r = client.get("/flight")
    t("/flight responds 200 when check.json and beats.json are present",
      r.status_code == 200, r.status_code)
    body = r.data.decode()
    t("the real claim text appears in the rendered page",
      check["claim"] in body)
    t("the real still-open line appears in the rendered page",
      check["stillOpen"] in body)
    t("the page is titled for its own function, not the branch's working name",
      "<title>How a check works" in body)

    # The failure path: if check.json is temporarily unreadable, the route
    # must 404, never fabricate a placeholder verdict.
    tmp = check_path.with_suffix(".json.bak")
    check_path.rename(tmp)
    try:
        r = client.get("/flight")
        t("/flight 404s rather than inventing a check when check.json is missing",
          r.status_code == 404, r.status_code)
    finally:
        tmp.rename(check_path)

    # The front page's own hero is untouched; /flight is reached only
    # through a quiet link, not by replacing anything on "/".
    front = client.get("/").data.decode()
    t("the front page still renders its own hero heading",
      'id="ask-heading"' in front)
    t("the front page links to /flight rather than embedding it",
      'href="/flight"' in front)
else:
    print("SKIP check.json / /flight route checks: static/flight/check.json "
          "not exported yet (run scripts/export_flight_check.py)")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
