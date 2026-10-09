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
FOOTAGE = ROOT / "static" / "footage"

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
flight_tpl = (ROOT / "templates" / "_flythrough.html").read_text()
t("the page tells flight.js where the decode worker lives",
  'data-decoder=' in flight_tpl)
t("the fly-through's own claim fields are real forms, so they work without JS",
  flight_tpl.count('class="claim-form flight-ask') == 2
  and flight_tpl.count('action="/" method="get"') == 2)
t("the poster is set on the stage, so the first screen has a picture "
  "before any frame is decoded",
  "--poster:" in flight_tpl)
flight_js = (ROOT / "static" / "flight.js").read_text()
t("frames are not downloaded until the page is interactive",
  "whenInteractive" in flight_js)
t("a 2g or 3g connection gets the motion tier only",
  "slowNetwork" in flight_js and "slow-2g" in flight_js)
t("flight.js keeps a main-thread decode path for browsers without Worker",
  'typeof Worker !== "function"' in flight_js and "MAIN_DECODE_MAX" in flight_js)
t("the low-res tier is retired after its window rather than drawn forever",
  "LORES_WINDOW_MS" in flight_js and "retireLores" in flight_js)
t("the freeze limit is 100ms and the stand-in radius is 2 frames",
  "var FREEZE_LIMIT_MS = 100;" in flight_js and "var SUB_RADIUS = 2;" in flight_js)

# ---- the overlay shifts nothing ----------------------------------------------
# Measured CLS on a phone while scrubbing was 0.3364 when a stage change added
# and removed rows inside one growing panel. Three cross-faded panels of fixed
# geometry bring it to 0, so nothing here may go back to toggling layout.

t("the work panel is three cross-faded layers, not one growing panel",
  flight_tpl.count('class="work-layer flight-chip"') == 3
  and 'class="work-deck"' in flight_tpl)
t("a stage change is opacity and visibility, never a row leaving the flow",
  "function setLayer(" in flight_js
  and ".hidden = !(" not in flight_js
  and ".hidden = !stackOn" not in flight_js)
t("nothing in the work panel is hidden with the hidden attribute",
  "still-open\" data-on" in flight_tpl
  and 'class="claim-form flight-ask again" data-on' in flight_tpl)
t("the skip link rides the pinned stage rather than floating over the "
  "whole page",
  flight_tpl.index('class="skip-checker"') > flight_tpl.index('class="flight-stage"')
  and "position: absolute" in (ROOT / "static" / "flight.css").read_text()
      .split(".skip-checker {")[1].split("}")[0])

# ---- the fallback stills exist -----------------------------------------------
# Without these files the reduced-motion and Save-Data path is five broken
# images, which is the one path a reader cannot work around.

import app as _app  # noqa: E402
for _s in _app._FLIGHT_STILLS:
    t(f"the fallback still {_s['file']} is built", (FLIGHT / _s["file"]).exists())

sw = (ROOT / "static" / "sw.js").read_text()
t("the fly-through's stylesheet and script are shell files now that it is "
  "the home page",
  '"/static/flight.css"' in sw and '"/static/flight.js"' in sw)
t("the frames themselves are never cached by the service worker",
  'url.pathname.startsWith("/static/flight/")' in sw
  and 'url.pathname.startsWith("/footage/")' in sw)

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

    r = client.get("/")
    t("the home page responds 200 with the fly-through on it",
      r.status_code == 200, r.status_code)
    body = r.data.decode()
    t("the real claim text appears in the rendered page",
      check["claim"] in body)
    t("the real still-open line appears in the rendered page",
      check["stillOpen"] in body)
    t("the fly-through's canvas is on the home page",
      'id="flight-canvas"' in body)

    # The checker is not something you scroll to find: it is on the same page,
    # with its own field, and reachable from any scroll position.
    t("the home page still carries the checker's own hero and field",
      'id="ask-heading"' in body and 'id="claim-input"' in body)
    t('"Skip to the checker" is on the page', "Skip to the checker" in body)
    t("the home page carries the ledger and the latest checks below the footage",
      'class="ledger' in body or 'id="ask-more"' in body)

    # /flight moved to "/" and the old link has to keep working.
    r = client.get("/flight")
    t("/flight redirects permanently to the home page",
      r.status_code == 301 and r.headers.get("Location", "").endswith("/"),
      f"{r.status_code} {r.headers.get('Location')}")

    # The checker on its own, for anyone who wants the tool and not the film.
    r = client.get("/checks")
    checks_body = r.data.decode()
    t("/checks responds 200", r.status_code == 200, r.status_code)
    t("/checks renders the checker without the frame sequence",
      'id="ask-heading"' in checks_body and 'id="flight-canvas"' not in checks_body)

    t("sitemap.xml lists /checks", b"/checks" in client.get("/sitemap.xml").data)
    t("llms.txt names both the home page and /checks",
      b"/checks" in client.get("/llms.txt").data)

    # The failure path: if check.json is temporarily unreadable, the home page
    # must still serve the checker rather than 500 or invent a check.
    tmp = check_path.with_suffix(".json.bak")
    check_path.rename(tmp)
    try:
        r = client.get("/")
        t("the home page still works when the fly-through is not built",
          r.status_code == 200 and b'id="claim-input"' in r.data
          and b'id="flight-canvas"' not in r.data, r.status_code)
    finally:
        tmp.rename(check_path)

    # A shared result link is a result page, not a 23MB film. Needs a verdict
    # actually in the cache: without one, "/?q=..." is just the home page, and
    # asserting against that would be testing nothing.
    import db as dbmod  # noqa: E402
    dbmod.init_db()
    dbmod.put_cached_verdict(
        "test claim for the flight route", "test AND claim", "complicated",
        "An explanation.", ["1"],
        [{"pmid": "1", "title": "T", "journal": "J", "year": 2020,
          "publication_types": ["Randomized Controlled Trial"], "abstract": "A.",
          "url": "https://pubmed.ncbi.nlm.nih.gov/1/"}],
        tldr="A takeaway.", still_open="What is still open.")
    r = client.get("/?q=test+claim+for+the+flight+route")
    t("a shared result link renders the verdict", b"A takeaway." in r.data)
    t("a shared result link does not load the frame sequence",
      b'id="flight-canvas"' not in r.data)
    # A link opened cold is a page about one claim, not a film. It gets the
    # verdict shot's own still at the head of the band, and no video at all.
    t("a shared result link carries one still at the head of the band",
      b'class="film-shot" data-shot="verdict"' in r.data
      and b'footage/' + _app.BAND_STILL.encode() in r.data,
      _app.BAND_STILL)
    # ".webmanifest" is not a clip: match the extension at the end of a name.
    t("a shared result link loads no video",
      b".mp4" not in r.data and b".webm\"" not in r.data
      and b".webm'" not in r.data)
    t("the band is decorative, so a screen reader skips the whole of it",
      b'class="film" id="film" aria-hidden="true"' in r.data
      and b'alt=""' in r.data.split(b'class="film-shot"')[1][:300])
    t("the band still is small enough to head a page that must stay fast",
      (FOOTAGE / _app.BAND_STILL).stat().st_size <= 100_000,
      (FOOTAGE / _app.BAND_STILL).stat().st_size)
    t("the server names the screen on the html element, so a shared link "
      "never paints the home layout first",
      b'data-screen="result"' in r.data)
else:
    print("SKIP check.json / /flight route checks: static/flight/check.json "
          "not exported yet (run scripts/export_flight_check.py)")

# ---- the band and the sheet -------------------------------------------------
# Part 3: checking, a verdict, an error and a check that found nothing are one
# screen. A strip of footage across the top, and the report on a sheet below
# it. These tests hold the rules that make that honest (every stage change
# comes from a stream event) and legible (nothing readable on the picture, no
# blur, no colour-coded verdict, no layout animated).

import re  # noqa: E402

app_js = (ROOT / "static" / "app.js").read_text()
flight_css = (ROOT / "static" / "flight.css").read_text()
style_css = (ROOT / "static" / "style.css").read_text()
index_tpl = (ROOT / "templates" / "index.html").read_text()

t("the band lives on the page itself, not on the fly-through, so a cold "
  "shared link and /checks get it too",
  'class="film" id="film"' in index_tpl and "film" not in flight_tpl)
t("the band is decorative throughout",
  'aria-hidden="true"' in index_tpl.split('class="film" id="film"')[1][:120])
t("the fly-through no longer carries a checking panel of its own",
  "cinema-chip" not in flight_tpl and "cinema-steps" not in flight_tpl
  and "cinema" not in flight_css)
t("the claim and the work print on the sheet, inside the report",
  'id="claim-echo"' in index_tpl and 'id="reading"' in index_tpl)
t("arriving steps are announced to a screen reader",
  'aria-live="polite"' in index_tpl.split('id="reading"')[1][:160]
  or 'aria-live="polite"' in index_tpl.split('id="reading"')[0][-160:])

# The fly-through is five screens of scroll belonging to a page the reader has
# left. While a check owns the screen it stands down rather than decoding
# frames for a canvas nobody can see.
t("the fly-through exposes only stand down and stand up",
  "window.EvidentFlight" in flight_js
  and set(re.findall(r"(\w+): function",
                     flight_js.split("window.EvidentFlight")[1][:400]))
  == {"park", "resume"})
t("a parked fly-through decodes nothing",
  "if (parked)" in flight_js.split("function tick(")[1][:400])
t("a page that opens on a check never starts the fly-through at all",
  "document.documentElement.dataset.screen" in flight_js)
t("nothing cinema-shaped survives in the fly-through engine",
  "CINEMA" not in flight_js and "cinemaRestPx" not in flight_js)

# Every clip change is an event off the stream. A timer would be a progress
# bar that lies: the one thing this screen must not be.
stream_stages = {"search", "query", "found", "weigh", "done"}
_map = re.search(r"const FILM_STAGES = \{(.*?)\n\};", app_js, re.S)
t("the stage-to-clip map exists", bool(_map))
if _map:
    pairs = re.findall(r"(\w+):\s*\"(\w+)\"", _map.group(1))
    keys, vals = {k for k, _ in pairs}, {v for _, v in pairs}
    t("every step the band follows is a real stream stage, plus the "
      "synthetic 'start' fired when the claim is submitted",
      keys <= stream_stages | {"start"}, keys - stream_stages - {"start"})
    t("every clip the band is sent to is one that was built",
      vals <= {"searching", "weighing", "verdict"}, vals)
    t("searching plays while the search is built and run, weighing once "
      "studies have come back",
      dict(pairs).get("start") == "searching"
      and dict(pairs).get("query") == "searching"
      and dict(pairs).get("found") == "weighing"
      and dict(pairs).get("done") == "verdict", pairs)
_film_obj = app_js.split("const film = {")[1].split("\n};")[0]
t("nothing in the band controller runs on a timer",
  "setTimeout" not in _film_obj and "setInterval" not in _film_obj)
_screen_obj = app_js.split("const screenState = {")[1].split("\n};")[0]
t("nothing in the screen controller runs on a timer",
  "setTimeout" not in _screen_obj and "setInterval" not in _screen_obj)
_stage_fn = app_js.split("function handleStage(")[1].split("\n}")[0]
t("step labels and clip changes are written from the stream event, never "
  "from a clock",
  "setTimeout" not in _stage_fn and "setInterval" not in _stage_fn
  and "screenState.at(ev.stage)" in _stage_fn)
t("the clips are only fetched once the reader has asked for a check, or "
  "once the home page has gone quiet",
  'el.preload = "none"' in _film_obj
  and "requestIdleCallback" in app_js and 'film.warm("searching")' in app_js)
t("reduced motion and Save-Data get a still instead of a clip",
  "prefers-reduced-motion" in _film_obj and "saveData" in _film_obj)

# Every clip the map can ask for has actually been built, in both codecs,
# with a still beside it.
for _shot in ("searching", "weighing", "verdict"):
    _files = [FOOTAGE / f"{_shot}.{ext}" for ext in ("mp4", "webm", "webp")]
    t(f"the {_shot} clip ships as mp4, webm and a still",
      all(f.exists() for f in _files),
      [f.name for f in _files if not f.exists()])
    if all(f.exists() for f in _files):
        t(f"the {_shot} clip is under 3MB in both codecs",
          all(f.stat().st_size < 3_000_000 for f in _files[:2]),
          [f"{f.name} {f.stat().st_size // 1024}KB" for f in _files[:2]])

# Legibility. Nothing readable sits on the footage, so no panel has to buy
# its contrast back.
t("no backdrop blur anywhere in the shipped stylesheets",
  "backdrop-filter:" not in flight_css and "backdrop-filter:" not in style_css)
t("the sheet is opaque paper over the foot of the band, not a tint on it",
  "background: var(--paper)" in
  style_css.split("[data-screen] main.wrap {")[1][:400])
t("the sheet overlaps the band rather than butting against it",
  "margin-top: -" in style_css.split("[data-screen] main.wrap {")[1][:400])
t("the verdict is never colour-coded, on the band screens as anywhere else",
  "[data-verdict" not in style_css and "[data-verdict" not in flight_css)
t("the dark field and the panel under it are the same width, and so share "
  "a left edge",
  "margin: -14px calc(-1 * var(--gutter)) 0" in
  style_css.split(".verdict-block {")[1][:200])

# Motion. Only transform and opacity, so a transition cannot shift layout.
_props = set()
for _css in (flight_css, style_css):
    for decl in re.findall(r"transition:\s*([^;]+);", _css, re.S):
        # cubic-bezier(.22,.61,.36,1) has commas of its own, and they are
        # not property separators.
        decl = re.sub(r"\([^)]*\)", "", decl)
        for part in decl.split(","):
            word = part.strip().split()[0] if part.strip() else ""
            if word and not word[0].isdigit():
                _props.add(word)
t("transitions animate opacity and transform only",
  _props <= {"opacity", "transform", "visibility", "none", "overlay",
             "display", "color", "background", "border-color"},
  sorted(_props))
for _name in ("band-in", "sheet-rise"):
    _kf = re.search(r"@keyframes " + _name + r" \{(.*?)\}\s*\}", style_css, re.S)
    t(f"the {_name} entrance moves opacity and transform, nothing that lays out",
      bool(_kf) and set(re.findall(r"(\w[\w-]*):", _kf.group(1)))
      <= {"opacity", "transform"},
      _kf.group(1) if _kf else "missing")
t("the entrance belongs to a live check, so a cold shared link does not "
  "fade in a page the reader navigated to",
  "body.live-check main.wrap" in style_css
  and 'classList.add("live-check")' in app_js
  and 'classList.remove("live-check")' in app_js)

# Reduced motion: the same band, the same sheet, the same copy, nothing plays.
_rm = style_css.split("@media (prefers-reduced-motion: reduce)")[1]
t("reduced motion cuts between clips rather than cross-fading",
  ".film-shot { transition: none; }" in _rm)
t("reduced motion lands the band and the sheet without an entrance",
  "body.live-check .film, body.live-check main.wrap { animation: none; }" in _rm)
t("the fly-through's own reduced-motion rules survived the trim",
  "@media (prefers-reduced-motion: reduce)" in flight_css
  and ".flight-overlay" in
  flight_css.split("@media (prefers-reduced-motion: reduce)")[1][:400])

# ---- the footage is kept, not asked for again on every visit ------------------

if (FLIGHT / "manifest.json").exists():
    _c = _app.app.test_client()
    _home = _c.get("/").get_data(as_text=True)
    _base = f"/footage/{_app._footage_version()}"
    t("the home page asks for the footage under its fingerprinted path",
      f'data-frames="{_base}"' in _home and f'href="{_base}/frame-0000.avif"' in _home
      and "/static/flight/frame-" not in _home, _base)
    _r = _c.get(f"{_base}/lores/frame-0001.avif")
    t("  and a frame there is kept for a year, unchanged",
      _r.status_code == 200 and _r.data == (FLIGHT / "lores" / "frame-0001.avif").read_bytes()
      and "immutable" in _r.headers.get("Cache-Control", "")
      and "max-age=31536000" in _r.headers.get("Cache-Control", ""),
      _r.headers.get("Cache-Control"))
    t("  and a path outside the footage is refused",
      _c.get(f"{_base}/../../app.py").status_code == 404)
    _old = _app._footage_version()
    _app._footage_version_memo.clear()
    t("  and the fingerprint is stable across a restart", _app._footage_version() == _old)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
