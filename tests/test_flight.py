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
  'url.pathname.startsWith("/static/flight/")' in sw)

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
    # closing still as a header image, roughly 14KB where the sequence is 23MB.
    last_still = _app._FLIGHT_STILLS[-1]["file"]
    t("a shared result link carries the closing still as a header image",
      b'class="result-still"' in r.data and last_still.encode() in r.data,
      last_still)
    t("the header still is decorative, so a screen reader skips it",
      b'alt=""' in r.data.split(b'class="result-still"')[1][:300])
    t("the header still is small enough to be a header image",
      (FLIGHT / last_still).stat().st_size < 60_000,
      (FLIGHT / last_still).stat().st_size)
else:
    print("SKIP check.json / /flight route checks: static/flight/check.json "
          "not exported yet (run scripts/export_flight_check.py)")

# ---- the footage keeps running through checking and the result ---------------
# Part 3: a check does not cut away to a paper screen. The clip stays on, the
# live panel takes the replay panel's place, and the verdict lands on a solid
# sheet over the last lit frame. These tests hold the rules that make that
# honest (every label comes from a stream event) and legible (a solid panel,
# no blur, no colour-coded verdict).

import re  # noqa: E402

app_js = (ROOT / "static" / "app.js").read_text()
flight_css = (ROOT / "static" / "flight.css").read_text()
style_css = (ROOT / "static" / "style.css").read_text()

_m = re.search(r"var CINEMA_REST_SECOND = ([\d.]+);", flight_js)
_rest_second = float(_m.group(1)) if _m else None
beats_master_seconds = (
    json.loads(beats_path.read_text())["masterSeconds"] if beats_path.exists() else 23.0)

t("the live check has its own panel on the stage",
  'id="cinema-chip"' in flight_tpl and 'id="cinema-claim"' in flight_tpl
  and 'id="cinema-steps"' in flight_tpl)
t("arriving steps are announced to a screen reader",
  'id="cinema-steps"' in flight_tpl
  and 'aria-live="polite"' in flight_tpl.split('id="cinema-steps"')[1][:120])
t("the live panel carries 'Not medical advice', because the replay's own "
  "note is faded out while a check runs",
  "Not medical advice." in flight_tpl.split('class="cinema-note"')[1][:200])
t("the live panel is shown by a data attribute, never by the hidden "
  "attribute, so it cross-fades instead of popping",
  'id="cinema-chip" data-on="0"' in flight_tpl
  or 'data-on="0"' in flight_tpl.split('id="cinema-chip"')[1][:60])

t("the page can drive the footage without a scroll",
  "window.EvidentFlight" in flight_js
  and all(k in flight_js.split("window.EvidentFlight")[1][:900]
          for k in ("enter:", "stage:", "leave:")))
t("the verdict rests on a frame the beats actually cover, not on the fade "
  "to black at the end of the sequence",
  "function cinemaRestPx(" in flight_js and "var CINEMA_REST_SECOND = " in flight_js
  and _rest_second is not None and _rest_second < beats_master_seconds)
t("the rest frame is the brightest still frame of the clip, measured",
  _rest_second is not None and abs(_rest_second - 243 / 24) < 1 / 24)
t("the verdict is a cut, so the footage never rewinds under it and a cached "
  "verdict never swoops through work that never ran",
  "cinema.pos = cinema.target;" in flight_js.split('name === "end"')[1][:900])

# Every footage step is an event off the stream. A timer would be a progress
# bar that lies: the one thing this screen must not be.
stream_stages = {m for m in ("search", "query", "found", "weigh", "done")}
_map = re.search(r"const CINEMA_STAGES = \{(.*?)\n\};", app_js, re.S)
t("the footage-stage map exists", bool(_map))
if _map:
    pairs = re.findall(r"(\w+):\s*\"(\w+)\"", _map.group(1))
    keys, vals = {k for k, _ in pairs}, {v for _, v in pairs}
    t("every step the footage follows is a real stream stage, plus the "
      "synthetic 'start' fired when the claim is submitted",
      keys <= stream_stages | {"start"}, keys - stream_stages - {"start"})
    t("every stage the footage is sent to is a real beat, or the end",
      vals <= {"claim", "transition", "archive", "weighing", "write",
               "publish", "horizon", "end"}, vals)
_cinema_obj = app_js.split("const cinema = {")[1].split("\n};")[0]
t("nothing in the cinema controller runs on a timer",
  "setTimeout" not in _cinema_obj and "setInterval" not in _cinema_obj)
t("the live panel is revealed synchronously, so a fast check cannot leave "
  "the checking panel sitting on top of the verdict",
  "requestAnimationFrame" not in _cinema_obj)
_stage_fn = app_js.split("function handleStage(")[1].split("\n}")[0]
t("step labels are written from the stream event, never from a clock",
  "setTimeout" not in _stage_fn and "setInterval" not in _stage_fn
  and "cinema.at(ev.stage)" in _stage_fn)
t("a long step holds or loops the clip rather than running past the work",
  "CINEMA_LOOP_SECONDS" in flight_js and "Math.cos" in flight_js)

# Legibility. The panel is solid: footage stays at full brightness around it,
# but nothing readable sits on moving pictures.
t("no backdrop blur anywhere in the shipped stylesheets",
  "backdrop-filter:" not in flight_css and "backdrop-filter:" not in style_css)
t("the verdict sheet is opaque paper, not a tint over the footage",
  "background: var(--paper)" in
  flight_css.split('[data-cinema="result"] #result,')[1][:600])
t("the verdict is never colour-coded, in cinema as anywhere else",
  "[data-verdict" not in flight_css)

# Motion. Only transform and opacity, so a transition cannot shift layout.
_props = set()
for decl in re.findall(r"transition:\s*([^;]+);", flight_css, re.S):
    # cubic-bezier(.22,.61,.36,1) has commas of its own, and they are not
    # property separators.
    decl = re.sub(r"\([^)]*\)", "", decl)
    for part in decl.split(","):
        word = part.strip().split()[0] if part.strip() else ""
        if word and not word[0].isdigit():
            _props.add(word)
t("transitions animate opacity and transform only",
  _props <= {"opacity", "transform", "visibility", "none"}, sorted(_props))
_sheet = re.search(r"@keyframes sheet-in \{(.*?)\n\}", flight_css, re.S)
t("the sheet arrives on opacity and transform, with no layout property",
  bool(_sheet) and set(re.findall(r"(\w[\w-]*):", _sheet.group(1)))
  <= {"opacity", "transform"},
  _sheet.group(1) if _sheet else "missing")

# Reduced motion: the same panel, the same copy, the same flow, nothing plays.
_rm = flight_css.split("@media (prefers-reduced-motion: reduce)")[1]
t("reduced motion stills the live panel and the replay overlay",
  ".cinema-chip" in _rm and ".flight-overlay" in _rm)
t("reduced motion lands the verdict sheet without an entrance",
  'animation: none' in _rm and '[data-cinema="result"]' in _rm)
t("reduced motion keeps a picture behind the panel rather than a blank "
  "stage: the stage still paints its poster",
  "--poster" in flight_css.split("[data-cinema] .flight-stage")[1][:400])

t("the skip link stays through checking and steps aside for the verdict, "
  "which carries its own way out",
  '[data-cinema="result"] .skip-checker' in flight_css
  and '[data-cinema="error"] .skip-checker' in flight_css)
t("an error lands on the same sheet as a verdict, with the same way out",
  '[data-cinema="error"] #error' in flight_css)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
