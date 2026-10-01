/* The scroll fly-through.
 *
 * A pinned canvas scrubbed by scroll position, with the checker's own work
 * animated over it. No framework, no build step, same as the rest of the site.
 *
 * Five things this file is careful about:
 *
 *  1. The page works with the animation off. If the sequence cannot load, or
 *     the reader asked for reduced motion, or Save-Data is on, we add
 *     .no-flight and the four stages become four stills in normal page flow.
 *     The claim input is in the markup either way.
 *  2. Native scroll only. Nothing here calls preventDefault or moves the
 *     scroll position; the reader stays in charge, forwards and backwards.
 *  3. Two frame tiers. A tiny low-res copy of every frame is fetched and
 *     decoded up front and held for the whole session (small enough that
 *     this costs nothing): it is what "never blank" actually means, since
 *     the exact wanted frame is always available at low quality even before
 *     its high-res copy has arrived. High-res bytes for the whole sequence
 *     are also fetched up front (a small progress indicator tracks this),
 *     but only a window of them stays decoded to an ImageBitmap at once —
 *     decoding all 334 at 1440x810 simultaneously is well over a gigabyte
 *     of bitmap memory, which a phone will not tolerate.
 *  4. The displayed frame eases toward the scroll-implied one every
 *     animation frame rather than jumping straight to it, so a fast flick
 *     glides across frames instead of visibly skipping between them.
 *  5. Card arrival, grading and sorting progress continuously across a
 *     named *stage* (every beat that shares a stage name), never reset by
 *     a beat boundary; only text visibility (the headline, the small
 *     supporting labels) is scoped to one beat's own fade window. That is
 *     what lets a "no text" beat exist without the underlying state
 *     jumping when text next appears.
 */

(function () {
  "use strict";

  var HIRES_CONCURRENCY = 6;
  // Decoded ImageBitmaps held at once; the rest redecode from cached bytes
  // on demand (fast: no network, just createImageBitmap on an already-
  // downloaded blob). Set once the manifest's real width/height/fps are
  // known (see computeHiresKeep below): the raw bitmap size varies by
  // tier (phone frames are a third the pixels of the 2560 tier), and the
  // frame count per second of footage now varies by fps too, so a single
  // constant tuned for the old 9fps/1920px baseline would either waste
  // memory on a small tier or starve a large one.
  var HIRES_KEEP = 90;

  // Three frame tiers, chosen once at load. "large" gives a genuinely wide
  // desktop viewport a real 2560px source instead of the 1920 tier
  // stretched to fill it; "phone" gives a narrow portrait viewport a frame
  // sequence that was cropped to 9:16 at build time (native pixels, never
  // an upscale) instead of the landscape tier cropped at draw time (which
  // on a DPR2 phone canvas means scaling *up*). The breakpoint is a CSS
  // viewport width, not multiplied by DPR: an ordinary 1280-1440px laptop
  // at DPR2 already has the 1920 tier as its accepted compromise (see this
  // file's build script), so "large" is reserved for a viewport wide
  // enough that 1920 would visibly upscale regardless of DPR. Chosen once,
  // not re-picked on resize/orientation change: a deliberate scope limit,
  // see DECISIONS.md.
  var LARGE_BREAKPOINT = 1600; // CSS px, viewport width alone
  var variant = "default";

  function pickVariant() {
    var w = window.innerWidth, h = window.innerHeight;
    if (w < 700 && h > w) return "phone";
    if (w >= LARGE_BREAKPOINT) return "large";
    return "default";
  }

  function manifestUrl() {
    if (variant === "large") return section.dataset.frames + "/manifest-2560.json";
    if (variant === "phone") return section.dataset.frames + "/manifest-phone.json";
    return section.dataset.frames + "/manifest.json";
  }

  var root = document.documentElement;
  var section = document.getElementById("flight");
  if (!section) return;

  var canvas = document.getElementById("flight-canvas");
  var overlay = document.getElementById("flight-overlay");
  var progressEl = document.getElementById("flight-progress");

  function giveUp(why) {
    root.classList.add("no-flight");
    if (why) root.setAttribute("data-flight-off", why);
  }

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var saveData = navigator.connection && navigator.connection.saveData;
  if (reduced || saveData || !canvas) {
    giveUp(reduced ? "reduced-motion" : saveData ? "save-data" : "no-canvas");
    return;
  }

  var ctx = canvas.getContext("2d", { alpha: false });
  // Footage now plays at full brightness in its own right (no scrim), so
  // sharpness rests entirely on the source frame and this setting. At a
  // device pixel ratio of 2 the backing store is several megapixels, and
  // resampling a frame up to fill it at "high" quality measurably slowed
  // drawImage enough to reintroduce dropped frames during a fast scroll
  // (confirmed: 0 misses at DPR 1 in the same sweep, 29 at DPR 2, before
  // this line existed). Re-checked this round on a real GPU (not the
  // software-rendered SwiftShader path Playwright uses in this sandbox,
  // which blurs noticeably worse than either setting and should not be
  // trusted for judging sharpness): "low" and "high" looked the same, so
  // "low" stays for the scroll-performance win it measurably buys.
  if ("imageSmoothingQuality" in ctx) ctx.imageSmoothingQuality = "low";
  var manifest = null;
  var beats = null;

  // Cheap, always-on counters so a fast-scroll test can measure dropped and
  // stale frames instead of guessing from a screenshot. A handful of integer
  // increments per tick; read from outside as window.__flightStats.
  window.__flightStats = { draws: 0, misses: 0, seen: {} };

  /* ---- low-res tier: fetched and decoded fully, kept forever ------------ */

  var lores = new Array(0); // index -> ImageBitmap, always present once loaded
  var loresReady = false;

  function loresUrl(i) {
    return section.dataset.frames + "/" + manifest.loresPattern.replace("%04d", pad(i));
  }

  function pad(i) {
    var n = String(i);
    while (n.length < 4) n = "0" + n;
    return n;
  }

  var LORES_CONCURRENCY = 10;

  // Concurrency-limited like the hi-res loader below. Firing all 334 tiny
  // fetches (and, worse, 334 simultaneous createImageBitmap decodes) at
  // once congests the main thread for a couple of seconds right when the
  // page has just loaded and a reader is most likely to start scrolling:
  // measured with scripts/flight_scroll_test.mjs, an unthrottled version of
  // this function made even a plain scroll sweep run 1.7s behind real time.
  var loresLoadedCount = 0;

  function loadLores() {
    var next = 0, inFlight = 0;
    return new Promise(function (resolve) {
      function pump() {
        while (inFlight < LORES_CONCURRENCY && next < manifest.count) {
          (function (idx) {
            inFlight++;
            fetch(loresUrl(idx)).then(function (r) { return r.blob(); })
              .then(createImageBitmap).then(function (bmp) { lores[idx] = bmp; })
              .catch(function () { /* a missing low-res frame just leaves that slot empty */ })
              .then(function () {
                inFlight--;
                loresLoadedCount++;
                updateProgress();
                if (loresLoadedCount >= manifest.count) { loresReady = true; resolve(); }
                else pump();
              });
          })(next);
          next++;
        }
      }
      pump();
    });
  }

  /* ---- high-res tier: bytes preloaded for all frames, decoded on a window */

  var hiresBlobs = new Array(0);   // index -> Blob, once downloaded, kept forever (~10MB total)
  var hiresBitmaps = new Map();    // index -> ImageBitmap, bounded by HIRES_KEEP
  var hiresLoaded = 0;
  var hiresTotal = 0;

  // One frame's raw decoded size (RGBA), used both to size HIRES_KEEP and
  // to pick a sane idle-decode budget. Set once the manifest is read.
  function computeHiresKeep(width, height, fps) {
    var sizeRatio = (width * height) / (1920 * 1080);
    var fpsRatio = (fps || 9) / 9;
    var cap = variant === "phone" ? 140 : 220;
    return Math.max(60, Math.min(cap, Math.round(90 * Math.sqrt(fpsRatio / Math.max(0.1, sizeRatio)))));
  }

  function hiresUrl(i) {
    return section.dataset.frames + "/" + manifest.pattern.replace("%04d", pad(i));
  }

  function preloadHiresBytes() {
    hiresTotal = manifest.count;
    var next = 0;
    var inFlight = 0;
    return new Promise(function (resolve) {
      function pump() {
        while (inFlight < HIRES_CONCURRENCY && next < manifest.count) {
          (function (idx) {
            inFlight++;
            fetch(hiresUrl(idx)).then(function (r) { return r.blob(); })
              .then(function (blob) {
                hiresBlobs[idx] = blob;
              })
              .catch(function () { /* the low-res tier still covers this frame */ })
              .then(function () {
                inFlight--;
                hiresLoaded++;
                updateProgress();
                if (hiresLoaded >= manifest.count) resolve();
                else pump();
              });
          })(next);
          next++;
        }
      }
      pump();
    });
  }

  // Distance-based, not insertion-order: a stage-ahead idle decode can fill
  // the map with frames far from where the reader actually is, and FIFO
  // eviction would throw away whichever of those happened to be decoded
  // first rather than whichever is actually farthest from the scroll
  // position. Never evicts the frame currently on screen.
  function evictHiresIfNeeded() {
    if (hiresBitmaps.size <= HIRES_KEEP) return;
    var cur = Math.round(displayedFrame);
    var farthest = -1, farthestDist = -1;
    hiresBitmaps.forEach(function (_, idx) {
      if (idx === cur) return;
      var d = Math.abs(idx - cur);
      if (d > farthestDist) { farthestDist = d; farthest = idx; }
    });
    if (farthest < 0) return;
    var b = hiresBitmaps.get(farthest);
    if (b && b.close) b.close();
    hiresBitmaps.delete(farthest);
  }

  var hiresDecoding = new Set(); // indices with a decode already in flight
  var decodesThisTick = 0;

  // Decoding from an already-downloaded Blob is local and fast (no network
  // round trip), so this can happen the moment a frame is wanted rather
  // than needing to be anticipated far in advance. The persistent easing
  // loop calls this every animation frame while a frame is still missing,
  // so without the in-flight guard the same index gets re-decoded on every
  // tick until the first decode resolves — the other real source of the
  // main-thread congestion the scroll test caught.
  function ensureHiresDecoded(i) {
    if (hiresBitmaps.has(i) || hiresDecoding.has(i) || !hiresBlobs[i]) return;
    hiresDecoding.add(i);
    createImageBitmap(hiresBlobs[i]).then(function (bmp) {
      hiresDecoding.delete(i);
      hiresBitmaps.set(i, bmp);
      evictHiresIfNeeded();
    }).catch(function () { hiresDecoding.delete(i); });
  }

  // Caps how many decodes a single hot-path tick can kick off (point 4: at
  // most ~2 per frame). Idle-time stage-ahead decoding (scheduleIdleDecode,
  // below) uses its own separate budget and doesn't count against this.
  function kickDecode(i) {
    if (decodesThisTick >= 2) return;
    if (hiresBitmaps.has(i) || hiresDecoding.has(i) || !hiresBlobs[i]) return;
    decodesThisTick++;
    ensureHiresDecoded(i);
  }

  /* ---- progress indicator ------------------------------------------------ */

  // Called up to twice per downloaded frame (roughly 650 times across both
  // tiers). A per-call DOM write forces a style/layout recalc each time,
  // which is real, measured main-thread cost for a number nobody reads at
  // that resolution; rAF-coalescing collapses any calls landing in the same
  // frame into one write. The counter is exact either way.
  var progressScheduled = false;

  function updateProgress() {
    if (!progressEl || progressScheduled) return;
    progressScheduled = true;
    requestAnimationFrame(function () {
      progressScheduled = false;
      var frac = (hiresLoaded + loresLoadedCount) / (manifest.count * 2);
      if (frac >= 0.999) {
        progressEl.hidden = true;
        return;
      }
      progressEl.hidden = false;
      progressEl.textContent = "Loading footage … " + Math.round(frac * 100) + "%";
    });
  }

  /* ---- drawing: never blank, never stale ---------------------------------
   * Preference order for the wanted index: its decoded hi-res bitmap, else
   * its low-res bitmap (the "always available once loaded" tier), else the
   * nearest index in either direction that has *something* decoded, so the
   * canvas is never simply left showing an old, unrelated frame while the
   * reader has scrolled somewhere else. */

  // How far to widen the search for a sharp stand-in before giving up and
  // showing the blurry lores copy. +/-2 (point 4's wording) is a floor, not
  // a ceiling: it guarantees lores never stands in once a hi-res frame is
  // that close, but during a fast flick the exact frame can be dozens of
  // indices from whatever is already decoded. A sharp neighbour from the
  // same clip beats a blurry exact frame, so keep searching out to about a
  // second of footage before conceding to lores.
  var HIRES_FALLBACK_RADIUS = 24;

  function bitmapFor(i) {
    var hi = hiresBitmaps.get(i);
    if (hi) return hi;
    kickDecode(i); // kick off a decode for next time, budget permitting
    var best = null, bestDist = Infinity;
    hiresBitmaps.forEach(function (bmp, idx) {
      var d = Math.abs(idx - i);
      if (d <= HIRES_FALLBACK_RADIUS && d < bestDist) { bestDist = d; best = bmp; }
    });
    if (best) return best;
    if (lores[i]) return lores[i];
    return null;
  }

  function nearestAvailable(i) {
    var direct = bitmapFor(i);
    if (direct) return { bmp: direct, index: i };
    for (var d = 1; d < manifest.count; d++) {
      var lo = i - d, hi = i + d;
      if (lo >= 0) {
        var b = bitmapFor(lo);
        if (b) return { bmp: b, index: lo };
      }
      if (hi < manifest.count) {
        var b2 = bitmapFor(hi);
        if (b2) return { bmp: b2, index: hi };
      }
    }
    return null;
  }

  var lastB0 = null, lastB1 = null, lastT = -1;
  var hasDrawn = false;

  // focusX lets a mobile crop follow the subject instead of always
  // centring, which is what loses the phone at narrow widths.
  function drawOne(bmp, alpha) {
    var cw = canvas.width, ch = canvas.height;
    var scale = Math.max(cw / bmp.width, ch / bmp.height);
    var dw = bmp.width * scale, dh = bmp.height * scale;
    var fx = currentFocusX();
    ctx.globalAlpha = alpha;
    ctx.drawImage(bmp, (cw - dw) * fx, (ch - dh) / 2, dw, dh);
    ctx.globalAlpha = 1;
  }

  // Draws the continuous scroll position `pos`, not a single frame index:
  // when pos lands between two decoded frames, both are drawn (the lower
  // at full opacity, the upper crossfaded in at the fractional part) so
  // in-between positions look like motion instead of a visible step.
  function draw(pos, force) {
    var i0 = Math.max(0, Math.min(manifest.count - 1, Math.floor(pos)));
    var i1 = Math.min(manifest.count - 1, i0 + 1);
    var t = i1 === i0 ? 0 : pos - i0;

    var f0 = nearestAvailable(i0);
    if (!f0) { window.__flightStats.misses++; return; }
    var f1 = t > 0.004 ? nearestAvailable(i1) : null;

    if (!force && hasDrawn && f0.bmp === lastB0 && (f1 ? f1.bmp : null) === lastB1 &&
        Math.abs(t - lastT) < 0.004) return;

    if (f0.index !== i0) window.__flightStats.misses++;
    if (f1 && f1.index !== i1) window.__flightStats.misses++;

    lastB0 = f0.bmp;
    lastB1 = f1 ? f1.bmp : null;
    lastT = t;
    hasDrawn = true;
    window.__flightStats.draws++;
    window.__flightStats.seen[f0.index] = 1;
    if (f1) window.__flightStats.seen[f1.index] = 1;

    drawOne(f0.bmp, 1);
    if (f1) drawOne(f1.bmp, t);
  }

  function sizeCanvas() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    if (hasDrawn) draw(displayedFrame, true);
  }

  /* ---- the timeline ------------------------------------------------------ */

  var plan = [];       // {b, start, end} in scroll pixels
  var stageSpans = {};  // stage name -> {start, end} in scroll pixels, for continuous state
  var totalPx = 0;
  var travelPx = 1;
  var isMobile = false;
  // Read once per layout, not once per tick: section.offsetTop forces a
  // layout recalc, and the easing loop reads scroll position every frame.
  var sectionTop = 0;

  function beatVh(b) {
    var m = b.mobile || {};
    return isMobile && typeof m.vh === "number" ? m.vh : b.vh;
  }

  function layout() {
    isMobile = window.innerWidth < 700;
    var vh = window.innerHeight / 100;

    // The section is as tall as the beats ask for, but a sticky stage is
    // only pinned for (height - one viewport). The timeline has to be laid
    // out across that travel, or the last beats sit in the stretch where the
    // stage has already scrolled away and are never seen.
    var heightPx = beats.beats.reduce(function (sum, b) { return sum + beatVh(b) * vh; }, 0);
    travelPx = Math.max(1, heightPx - window.innerHeight);

    var at = 0;
    plan = beats.beats.map(function (b) {
      var share = (beatVh(b) * vh) / heightPx;
      var px = share * travelPx;
      var row = { b: b, start: at, end: at + px };
      at += px;
      return row;
    });
    totalPx = at;

    stageSpans = {};
    plan.forEach(function (row) {
      var s = stageSpans[row.b.stage] || { start: row.start, end: row.end };
      s.start = Math.min(s.start, row.start);
      s.end = Math.max(s.end, row.end);
      stageSpans[row.b.stage] = s;
    });

    section.style.setProperty("--flight-height", heightPx + "px");
    sectionTop = section.offsetTop;
    sizeCanvas();
    buildStageGroups();
  }

  function stageProgress(name, scrollPx) {
    var span = stageSpans[name];
    if (!span) return 0;
    return clamp01((scrollPx - span.start) / Math.max(1, span.end - span.start));
  }

  function currentFocusX() {
    // The phone tier's crop is already baked into the asset at build time
    // (see scripts/build_flight.py); applying beats.json's per-beat
    // mobile.focusX on top of that would crop an already-cropped frame.
    if (variant === "phone") return 0.5;
    var row = activeRow;
    if (!row || !isMobile) return 0.5;
    var m = row.b.mobile || {};
    return typeof m.focusX === "number" ? m.focusX : 0.5;
  }

  var activeRow = null;

  function frameFor(row, p) {
    if (typeof row.b.hold === "number") {
      return row.b.hold < 0 ? manifest.count - 1 : row.b.hold;
    }
    var secs = row.b.from + (row.b.to - row.b.from) * p;
    return Math.max(0, Math.min(manifest.count - 1, Math.round(secs * manifest.fps)));
  }

  function beatFrameRange(row) {
    if (typeof row.b.hold === "number") {
      var f = row.b.hold < 0 ? manifest.count - 1 : row.b.hold;
      return [f, f];
    }
    var f0 = frameFor(row, 0), f1 = frameFor(row, 1);
    return f0 < f1 ? [f0, f1] : [f1, f0];
  }

  // Groups consecutive beats sharing a stage name into one {minF, maxF,
  // startPx, endPx} span each, so idle decoding (point 4) can target "every
  // frame this stage and the next one touch" instead of guessing from the
  // single current frame.
  var stageGroups = [];

  function buildStageGroups() {
    stageGroups = [];
    var cur = null;
    plan.forEach(function (row) {
      var range = beatFrameRange(row);
      if (cur && cur.stage === row.b.stage) {
        cur.minF = Math.min(cur.minF, range[0]);
        cur.maxF = Math.max(cur.maxF, range[1]);
        cur.endPx = row.end;
      } else {
        cur = { stage: row.b.stage, minF: range[0], maxF: range[1], startPx: row.start, endPx: row.end };
        stageGroups.push(cur);
      }
    });
    aheadQueued = null; // force refreshAheadQueue to recompute on next call
  }

  // requestIdleCallback, or a setTimeout stand-in where it's missing (older
  // Safari). Either way idle decoding never competes with the hot path.
  var ric = typeof window.requestIdleCallback === "function"
    ? window.requestIdleCallback.bind(window)
    : function (cb) {
        return setTimeout(function () {
          cb({ didTimeout: true, timeRemaining: function () { return 0; } });
        }, 60);
      };

  var aheadQueue = [];
  var aheadQueued = null; // last stage-group index the queue was built for
  var idleScheduled = false;

  // Point 4: "keep the current stage's frames plus the next stage's decoded
  // ahead of time." Rebuilds the wanted set only when the reader crosses
  // into a new stage group (cheap to check every scroll update, since most
  // calls land in the same group as last time and bail immediately).
  function refreshAheadQueue(scrollPx) {
    if (!stageGroups.length) return;
    var idx = 0;
    for (var i = 0; i < stageGroups.length; i++) {
      idx = i;
      if (scrollPx < stageGroups[i].endPx) break;
    }
    if (idx === aheadQueued) return;
    aheadQueued = idx;

    var want = [];
    function addRange(g) { for (var f = g.minF; f <= g.maxF; f++) want.push(f); }
    addRange(stageGroups[idx]);
    if (stageGroups[idx + 1]) addRange(stageGroups[idx + 1]);

    var cur = Math.round(displayedFrame);
    want.sort(function (a, b) { return Math.abs(a - cur) - Math.abs(b - cur); });
    aheadQueue = want.filter(function (f) { return !hiresBitmaps.has(f) && !hiresDecoding.has(f); });
    scheduleIdleDecode();
  }

  function scheduleIdleDecode() {
    if (idleScheduled || !aheadQueue.length) return;
    idleScheduled = true;
    ric(function (deadline) {
      idleScheduled = false;
      var budget = 6; // this slice's own cap, separate from the hot-path per-tick cap
      while (aheadQueue.length && budget > 0 &&
             (deadline.didTimeout || deadline.timeRemaining() > 0)) {
        var f = aheadQueue.shift();
        if (!hiresBitmaps.has(f) && !hiresDecoding.has(f) && hiresBlobs[f]) {
          ensureHiresDecoded(f);
          budget--;
        }
      }
      if (aheadQueue.length) scheduleIdleDecode();
    }, { timeout: 500 });
  }

  // Fade-in/hold/fade-out, computed from a beat's own local progress and its
  // own {in, out} window. A beat with no `fade` (a hold, or a transition/
  // move beat with chapter:null) is simply on or off, never fading.
  function textOpacity(row, p) {
    if (!row.b.chapter) return 0;
    var f = row.b.fade;
    if (!f) return 1; // a hold beat: steady while it is the active beat
    if (p < f.in) return f.in <= 0 ? 1 : clamp01(p / f.in);
    if (p > f.out) return clamp01((1 - p) / Math.max(1e-6, 1 - f.out));
    return 1;
  }

  /* ---- the animation over the footage ------------------------------------ */

  var el = {};
  var cards = [];
  var sortedIndex = [];
  var originalTop = [];
  var targetTop = [];
  var STACK_GAP = 8; // matches the gap in .stack in flight.css

  function computeTops() {
    if (!cards.length) return;
    var heights = cards.map(function (c) { return c.getBoundingClientRect().height; });
    var top = 0;
    originalTop = heights.map(function (h) { var t = top; top += h + STACK_GAP; return t; });
    var order = cards.map(function (_, i) { return i; });
    order.sort(function (a, b) { return sortedIndex[a] - sortedIndex[b]; });
    targetTop = new Array(cards.length);
    top = 0;
    order.forEach(function (i) { targetTop[i] = top; top += heights[i] + STACK_GAP; });
  }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function setOn(node, on) { if (node) node.setAttribute("data-on", on ? "1" : "0"); }

  function paint(row, p, scrollPx) {
    var stage = row.b.stage;
    var tOp = textOpacity(row, p);

    for (var id in el.chapters) setOn(el.chapters[id], id === row.b.chapter && tOp > 0.02);
    if (el.chapterZone) el.chapterZone.style.opacity = String(tOp);

    el.work.setAttribute("data-stage", stage);

    // The claim panel: visible only during the claim stage (the landing,
    // the input), opacity tied to this beat's own text window rather than
    // a stage-wide fade, so it can vanish before that clip's quiet,
    // text-free tail.
    if (el.claim) {
      el.claim.style.opacity = String(stage === "claim" ? tOp : 0);
      el.claim.hidden = !(stage === "claim" && tOp > 0.02);
    }
    if (el.form) el.form.hidden = stage !== "claim";

    // The query: archive is where the real PubMed query appears (stepping
    // back before any study has arrived), and it stays in view through
    // weighing, write and publish since the search that produced these
    // studies is still the relevant context for everything that follows.
    if (el.query) {
      var queryOn = stage === "archive" || stage === "weighing" || stage === "write" || stage === "publish";
      el.query.style.opacity = String(stage === "archive" ? tOp : (queryOn ? 1 : 0));
      el.query.hidden = !queryOn;
    }

    // Weighing is where the studies arrive, get graded and sort into the
    // evidence bar; write, publish and horizon hold that result while the
    // sequence moves on to the findings being written up, made public, and
    // finally the verdict, still-open line and next input reveal.
    // Continuous progress through each named stage, not any one beat's
    // local p, so nothing resets at a beat boundary inside weighing (its
    // text beat and its text-free tail share one climbing count) or at any
    // later stage seam.
    var stackOn = stage === "weighing" || stage === "write" || stage === "publish" || stage === "horizon";
    if (el.stackWrap) el.stackWrap.hidden = !stackOn;

    var weighP = stageProgress("weighing", scrollPx);
    var horizonP = stageProgress("horizon", scrollPx);

    var arrived = 0, graded = 0, sorting = 0;
    if (stage === "weighing") {
      arrived = Math.round(clamp01(weighP / 0.5) * cards.length);
      graded = Math.round(clamp01((weighP - 0.15) / 0.45) * cards.length);
      sorting = clamp01((weighP - 0.55) / 0.35);
    } else if (stage === "write" || stage === "publish" || stage === "horizon") {
      arrived = cards.length;
      graded = cards.length;
      sorting = 1;
    }

    for (var i = 0; i < cards.length; i++) cards[i].setAttribute("data-in", i < arrived ? "1" : "0");
    if (el.counter) {
      el.counter.style.opacity = String(stackOn ? 1 : 0);
      el.counter.textContent = arrived + " of " + cards.length + " studies read";
    }

    var midFade = 1 - 0.45 * Math.sin(Math.min(1, Math.max(0, sorting)) * Math.PI);
    for (i = 0; i < cards.length; i++) {
      cards[i].setAttribute("data-graded", i < graded ? "1" : "0");
      var shift = ((targetTop[i] || 0) - (originalTop[i] || 0)) * sorting;
      // The cards shrink away over the course of horizon so the panel can
      // simplify down to the bar, the verdict and what is still open by
      // the time the reader has sat with it a moment.
      var hide = stage === "horizon" ? clamp01(horizonP * 2.2) : 0;
      cards[i].style.transform = "translateY(" + shift.toFixed(1) + "px) scaleY(" + (1 - hide) + ")";
      cards[i].style.opacity = String((1 - hide) * midFade);
    }

    // The bar forms once weighing's sort has mostly landed and then stays
    // up through write, publish and horizon: it is the evidence the
    // verdict rests on, not a thing that belongs only to one stage.
    setOn(el.bar, (stage === "weighing" && weighP > 0.75) || stage === "write" || stage === "publish" || stage === "horizon");

    // Horizon: the verdict, what is still open, then the input again, each
    // a little further into the stage than the last. 0.3 sits just past
    // where the headline's own fade-out finishes, so the verdict never
    // overlaps the chapter sentence above it.
    setOn(el.verdict, stage === "horizon" && horizonP > 0.3);
    if (el.open) el.open.hidden = !(stage === "horizon" && horizonP > 0.55);
    if (el.again) el.again.hidden = !(stage === "horizon" && horizonP > 0.75);
  }

  /* ---- scroll + a persistent easing loop ---------------------------------
   * The native scroll listener does nothing but set a flag: no DOM read, no
   * DOM write (point 5). Once per animation frame, `tick` reads scrollY,
   * recomputes everything that should feel instant (wantedFrame, the text
   * panels, the cards, the verdict) in `updateFromScroll`, then eases
   * `displayedFrame` toward `wantedFrame` by a fraction based on real
   * elapsed time, not a fixed per-tick factor, so motion reads the same at
   * 60Hz and 120Hz and across frames dropped by other work. */

  var lastY = window.scrollY;
  var direction = 1;
  var wantedFrame = 0;
  var displayedFrame = 0;
  var scrollDirty = true; // run once at startup even before any scroll event
  var EASE_TAU_MS = 100; // displayed catches up to ~63% of the gap every tau
  var MAX_FRAME_STEP = 8; // never skip ahead more than a few frames in one tick
  var lastTickTime = null;
  var firstTick = true; // skip the catch-up sweep if the page loads mid-scroll

  function onScroll() {
    scrollDirty = true;
  }

  function updateFromScroll() {
    var y = window.scrollY;
    direction = y >= lastY ? 1 : -1;
    lastY = y;

    var local = Math.max(0, Math.min(totalPx, y - sectionTop));

    var row = plan[0];
    for (var i = 0; i < plan.length; i++) {
      if (local >= plan[i].start && local < plan[i].end) { row = plan[i]; break; }
      row = plan[i];
    }
    activeRow = row;
    var p = clamp01((local - row.start) / Math.max(1, row.end - row.start));

    wantedFrame = frameFor(row, p);
    paint(row, p, local);
    refreshAheadQueue(local);
  }

  function tick(now) {
    if (scrollDirty) { scrollDirty = false; updateFromScroll(); }

    if (firstTick) { firstTick = false; displayedFrame = wantedFrame; }

    var dt = lastTickTime == null ? 16 : Math.max(0, Math.min(200, now - lastTickTime));
    lastTickTime = now;

    var delta = wantedFrame - displayedFrame;
    if (Math.abs(delta) < 0.02) {
      displayedFrame = wantedFrame;
    } else {
      var alpha = 1 - Math.exp(-dt / EASE_TAU_MS);
      var step = delta * alpha;
      if (step > MAX_FRAME_STEP) step = MAX_FRAME_STEP;
      else if (step < -MAX_FRAME_STEP) step = -MAX_FRAME_STEP;
      displayedFrame += step;
    }

    decodesThisTick = 0;
    var cur = Math.round(displayedFrame);

    // draw() goes first and gets first claim on this tick's decode budget:
    // it needs the exact frame(s) on screen right now. A fast flick can
    // move displayedFrame through dozens of indices per second, and if the
    // speculative ahead-decode below ran first and spent the whole budget
    // on frames the reader hasn't reached yet, the frame actually being
    // shown would never get its own decode kicked off and would sit on the
    // blurry lores stand-in indefinitely — exactly the "blurry when I go
    // fast" symptom this ordering fixes.
    draw(displayedFrame);

    // Whatever budget draw() didn't use goes to a small window ahead in
    // the scroll direction (and a little behind) so frames are already
    // sharp by the time the reader arrives, capped at ~2 decode kickoffs
    // per tick total (point 4).
    var dir = delta >= 0 ? 1 : -1;
    for (var k = 1; k <= 6; k++) {
      var ahead = cur + dir * k;
      if (ahead >= 0 && ahead < manifest.count) kickDecode(ahead);
    }
    if (cur - dir >= 0 && cur - dir < manifest.count) kickDecode(cur - dir);

    requestAnimationFrame(tick);
  }

  /* ---- build the overlay from the real check ------------------------------ */

  function buildOverlay() {
    el.work = overlay.querySelector(".flight-work");
    el.chapterZone = overlay.querySelector(".chapter-zone");
    el.claim = overlay.querySelector(".claim-card");
    el.form = overlay.querySelector(".claim-form");
    el.query = overlay.querySelector(".query-line");
    el.counter = overlay.querySelector(".counter");
    el.stackWrap = overlay.querySelector(".stack");
    el.bar = overlay.querySelector(".evidence-bar");
    el.verdict = overlay.querySelector(".verdict-word");
    el.open = overlay.querySelector(".still-open");
    el.again = overlay.querySelector(".again");
    el.chapters = {};
    Array.prototype.forEach.call(overlay.querySelectorAll(".chapter"), function (c) {
      el.chapters[c.id] = c;
    });

    cards = Array.prototype.slice.call(el.stackWrap.querySelectorAll("li"));
    var rank = { weak: 0, moderate: 1, strong: 2, retracted: -1 };
    var withIdx = cards.map(function (c, i) { return { i: i, r: rank[c.dataset.tier] || 0 }; });
    var target = withIdx.slice().sort(function (a, b) { return a.r - b.r || a.i - b.i; });
    sortedIndex = new Array(cards.length);
    target.forEach(function (row, pos) { sortedIndex[row.i] = pos; });
  }

  /* ---- start --------------------------------------------------------------- */

  variant = pickVariant();
  Promise.all([
    fetch(manifestUrl()).then(function (r) { return r.json(); }),
    fetch(section.dataset.beats).then(function (r) { return r.json(); })
  ]).then(function (both) {
    manifest = both[0];
    beats = both[1];
    if (!manifest.count) throw new Error("empty manifest");
    lores = new Array(manifest.count);
    hiresBlobs = new Array(manifest.count);
    HIRES_KEEP = computeHiresKeep(manifest.width, manifest.height, manifest.fps);

    buildOverlay();
    computeTops();
    layout();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", function () { computeTops(); layout(); onScroll(); });
    window.addEventListener("orientationchange", function () { computeTops(); layout(); onScroll(); });

    // The low-res pass is small (a few hundred KB total) and finishes fast:
    // once it does, every frame index has *something* correct to show.
    // High-res bytes for the whole sequence preload alongside it in the
    // background; the progress indicator tracks both together.
    loadLores();
    preloadHiresBytes();

    onScroll();
    requestAnimationFrame(tick);
  }).catch(function () {
    giveUp("manifest-failed");
  });
})();
