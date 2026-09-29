/* The scroll fly-through.
 *
 * A pinned canvas scrubbed by scroll position, with the checker's own work
 * animated over it. No framework, no build step, same as the rest of the site.
 *
 * Four things this file is careful about:
 *
 *  1. The page works with the animation off. If the sequence cannot load, or
 *     the reader asked for reduced motion, or Save-Data is on, we add
 *     .no-flight and the four stages become four stills in normal page flow.
 *     The claim input is in the markup either way.
 *  2. Native scroll only. Nothing here calls preventDefault or moves the
 *     scroll position; the reader stays in charge, forwards and backwards.
 *  3. Bounded memory and network. At most CONCURRENCY requests are in flight
 *     and at most KEEP decoded bitmaps are held, oldest evicted and closed.
 *  4. The numbers on screen are the real check, loaded from check.json, which
 *     is exported from the database by scripts/export_flight_check.py.
 */

(function () {
  "use strict";

  var CONCURRENCY = 6;
  var KEEP = 60;

  var root = document.documentElement;
  var section = document.getElementById("flight");
  if (!section) return;

  var canvas = document.getElementById("flight-canvas");
  var overlay = document.getElementById("flight-overlay");
  var header = document.querySelector(".hdr");

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
  var manifest = null;
  var beats = null;
  var check = null;

  /* ---- frame store ----------------------------------------------------- */

  var frames = new Map();   // index -> ImageBitmap
  var order = [];           // insertion order, for eviction
  var pending = new Map();  // index -> AbortController
  var failed = new Set();
  var lastDrawn = -1;

  function frameUrl(i) {
    var n = String(i);
    while (n.length < 4) n = "0" + n;
    return section.dataset.frames + "/frame-" + n + ".webp";
  }

  function remember(i, bitmap) {
    frames.set(i, bitmap);
    order.push(i);
    while (order.length > KEEP) {
      var old = order.shift();
      if (old === lastDrawn) { order.push(old); continue; }
      var b = frames.get(old);
      if (b && b.close) b.close();
      frames.delete(old);
    }
  }

  function fetchFrame(i) {
    if (i < 0 || i >= manifest.count) return;
    if (frames.has(i) || pending.has(i) || failed.has(i)) return;
    if (pending.size >= CONCURRENCY) return;

    var ac = new AbortController();
    pending.set(i, ac);
    fetch(frameUrl(i), { signal: ac.signal })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.blob();
      })
      .then(createImageBitmap)
      .then(function (bmp) {
        pending.delete(i);
        remember(i, bmp);
        if (i === wanted) draw(i);
        pump();
      })
      .catch(function (err) {
        pending.delete(i);
        if (err && err.name === "AbortError") return;
        failed.add(i);
        if (failed.size > 8) giveUp("frames-failed");
      });
  }

  var wanted = 0;
  var direction = 1;

  function pump() {
    fetchFrame(wanted);
    for (var d = 1; d <= 10 && pending.size < CONCURRENCY; d++) {
      fetchFrame(wanted + d * direction);
    }
  }

  function abortFar() {
    pending.forEach(function (ac, i) {
      if (Math.abs(i - wanted) > 24) { ac.abort(); pending.delete(i); }
    });
  }

  /* ---- drawing --------------------------------------------------------- */

  function sizeCanvas() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    if (lastDrawn >= 0) draw(lastDrawn, true);
  }

  function draw(i, force) {
    var bmp = frames.get(i);
    if (!bmp) return;
    if (i === lastDrawn && !force) return;
    lastDrawn = i;

    var cw = canvas.width, ch = canvas.height;
    var fw = bmp.width, fh = bmp.height;
    var scale = Math.max(cw / fw, ch / fh);
    var dw = fw * scale, dh = fh * scale;
    // focusX lets a mobile crop follow the subject instead of always
    // centring, which is what loses the phone at narrow widths.
    var fx = currentFocusX();
    ctx.drawImage(bmp, (cw - dw) * fx, (ch - dh) / 2, dw, dh);
  }

  /* ---- the timeline ---------------------------------------------------- */

  var plan = [];     // {beat, startPx, endPx, from, to, hold}
  var totalPx = 0;
  var isMobile = false;

  function beatVh(b) {
    var m = b.mobile || {};
    return isMobile && typeof m.vh === "number" ? m.vh : b.vh;
  }

  var travelPx = 1;

  function layout() {
    isMobile = window.innerWidth < 700;
    var vh = window.innerHeight / 100;

    // The section is as tall as the beats ask for, but a sticky stage is
    // only pinned for (height - one viewport). The timeline has to be laid
    // out across that travel, or the last beats sit in the stretch where the
    // stage has already scrolled away and are never seen.
    var heightPx = beats.beats.reduce(function (sum, b) {
      return sum + beatVh(b) * vh;
    }, 0);
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
    section.style.setProperty("--flight-height", heightPx + "px");
    sizeCanvas();
  }

  function currentFocusX() {
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
    return Math.max(0, Math.min(manifest.count - 1,
      Math.round(secs * manifest.fps)));
  }

  /* ---- the animation over the footage ---------------------------------- */

  var el = {};          // cached overlay nodes
  var cards = [];       // <li> per study, in original order
  var sortedIndex = []; // where each card goes once graded
  var originalTop = []; // each card's real top offset, current order
  var targetTop = [];   // each card's real top offset, once sorted

  // Real offsets, not an assumed uniform row height. A study title wraps to
  // a second line more often on a phone width, and the eight cards are
  // rarely all the same height even on desktop, so a shift computed from
  // one card's height (as an earlier version of this did) drifts out of
  // sync with its neighbours and the stack visibly overlaps mid-sort.
  var STACK_GAP = 6; // matches the gap in .stack in flight.css

  function computeTops() {
    if (!cards.length) return;
    var heights = cards.map(function (c) { return c.getBoundingClientRect().height; });

    var top = 0;
    originalTop = heights.map(function (h) {
      var t = top; top += h + STACK_GAP; return t;
    });

    var order = cards.map(function (_, i) { return i; });
    order.sort(function (a, b) { return sortedIndex[a] - sortedIndex[b]; });
    targetTop = new Array(cards.length);
    top = 0;
    order.forEach(function (i) {
      targetTop[i] = top;
      top += heights[i] + STACK_GAP;
    });
  }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  function setOn(node, on) {
    if (node) node.setAttribute("data-on", on ? "1" : "0");
  }

  function paint(stage, p) {
    // Chapters: one visible at a time, keyed to the beat.
    for (var id in el.chapters) {
      setOn(el.chapters[id], id === activeRow.b.chapter);
    }

    el.work.setAttribute("data-stage", stage);

    // The claim shrinks and fades as the verdict arrives, then leaves the
    // layout entirely. Opacity alone is not enough: an invisible element
    // still holds its row, and the panel is taller than a phone viewport
    // with the claim still in it, which pushes the verdict off the top.
    if (el.claim) {
      var gone = stage === "verdict" ? clamp01(p * 2) : 0;
      el.claim.style.opacity = String(1 - gone);
      el.claim.style.transform = "scale(" + (1 - 0.12 * gone) + ")";
      el.claim.hidden = gone >= 1;
    }
    // Only one claim input is on screen at a time: the opening one during
    // the phone stage, the closing one once the verdict has landed.
    if (el.form) el.form.hidden = stage !== "phone";
    if (el.query) el.query.hidden = stage === "phone" || stage === "verdict";
    if (el.stackWrap) el.stackWrap.hidden = stage === "phone" || stage === "verdict";

    // Stage 2: the counter climbs as the studies arrive.
    var arrived = 0;
    if (stage === "archive") arrived = Math.round(clamp01(p / 0.85) * cards.length);
    else if (stage === "lab" || stage === "verdict") arrived = cards.length;

    for (var i = 0; i < cards.length; i++) {
      cards[i].setAttribute("data-in", i < arrived ? "1" : "0");
    }
    if (el.counter) {
      el.counter.textContent = arrived + " of " + cards.length + " studies read";
    }

    // Stage 3: grades appear one by one, then the stack sorts with the
    // strongest at the bottom.
    var graded = stage === "lab" ? Math.round(clamp01(p / 0.55) * cards.length)
               : stage === "verdict" ? cards.length : 0;
    var sorting = stage === "lab" ? clamp01((p - 0.6) / 0.35)
                : stage === "verdict" ? 1 : 0;

    // Two cards swapping rank cross paths partway through the shuffle, which
    // briefly lands one card's text on top of another's regardless of how
    // exact the offsets are. A shallow dip in opacity right at the midpoint
    // (full strength at the ends, where nothing overlaps) reads as a soft
    // shuffle instead of two headlines colliding.
    var midFade = 1 - 0.45 * Math.sin(Math.min(1, Math.max(0, sorting)) * Math.PI);

    for (i = 0; i < cards.length; i++) {
      cards[i].setAttribute("data-graded", i < graded ? "1" : "0");
      var shift = ((targetTop[i] || 0) - (originalTop[i] || 0)) * sorting;
      var hide = stage === "verdict" ? clamp01(p * 1.6) : 0;
      cards[i].style.transform =
        "translateY(" + shift.toFixed(1) + "px) scaleY(" + (1 - hide) + ")";
      cards[i].style.opacity = String((1 - hide) * midFade);
    }

    // Stage 4: the bar, then the verdict, then what is still open.
    setOn(el.bar, stage === "verdict" && p > 0.25);
    setOn(el.verdict, stage === "verdict" && p > 0.45);
    if (el.tldr) el.tldr.hidden = !(stage === "verdict" && p > 0.55);
    if (el.open) el.open.hidden = !(stage === "verdict" && p > 0.7);
    if (el.again) el.again.hidden = !(stage === "verdict" && p > 0.8);
  }

  /* ---- scroll ---------------------------------------------------------- */

  var lastY = window.scrollY;
  var queued = false;

  function onScroll() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(update);
  }

  function update() {
    queued = false;
    var y = window.scrollY;
    direction = y >= lastY ? 1 : -1;
    lastY = y;

    var top = section.offsetTop;
    var local = Math.max(0, Math.min(totalPx, y - top));

    var row = plan[0];
    for (var i = 0; i < plan.length; i++) {
      if (local >= plan[i].start && local < plan[i].end) { row = plan[i]; break; }
      row = plan[i];
    }
    activeRow = row;
    var p = (local - row.start) / Math.max(1, row.end - row.start);
    p = clamp01(p);

    wanted = frameFor(row, p);
    if (frames.has(wanted)) draw(wanted);
    abortFar();
    pump();
    paint(row.b.stage, p);

    // Glass over the footage, solid once the pinned section has passed the
    // header's own height.
    if (header) {
      var past = y > top + travelPx - window.innerHeight * 0.15;
      header.classList.toggle("solid", past);
    }
  }

  /* ---- build the overlay from the real check --------------------------- */

  function buildOverlay() {
    el.work = overlay.querySelector(".flight-work");
    el.claim = overlay.querySelector(".claim-card");
    el.form = overlay.querySelector(".claim-form");
    el.query = overlay.querySelector(".query-line");
    el.counter = overlay.querySelector(".counter");
    el.stackWrap = overlay.querySelector(".stack");
    el.bar = overlay.querySelector(".evidence-bar");
    el.verdict = overlay.querySelector(".verdict-word");
    el.tldr = overlay.querySelector(".tldr");
    el.open = overlay.querySelector(".still-open");
    el.again = overlay.querySelector(".again");
    el.chapters = {};
    Array.prototype.forEach.call(overlay.querySelectorAll(".chapter"), function (c) {
      el.chapters[c.id] = c;
    });

    cards = Array.prototype.slice.call(el.stackWrap.querySelectorAll("li"));

    // Strongest at the bottom: the order the evidence snapshot argues for.
    var rank = { weak: 0, moderate: 1, strong: 2, retracted: -1 };
    var withIdx = cards.map(function (c, i) {
      return { i: i, r: rank[c.dataset.tier] || 0 };
    });
    var target = withIdx.slice().sort(function (a, b) {
      return a.r - b.r || a.i - b.i;
    });
    sortedIndex = new Array(cards.length);
    target.forEach(function (row, pos) { sortedIndex[row.i] = pos; });
  }

  /* ---- start ----------------------------------------------------------- */

  Promise.all([
    fetch(section.dataset.frames + "/manifest.json").then(function (r) { return r.json(); }),
    fetch(section.dataset.beats).then(function (r) { return r.json(); })
  ]).then(function (both) {
    manifest = both[0];
    beats = both[1];
    if (!manifest.count) throw new Error("empty manifest");
    check = window.__flightCheck || null;

    buildOverlay();
    computeTops();
    layout();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", function () { computeTops(); layout(); update(); });
    window.addEventListener("orientationchange", function () { computeTops(); layout(); update(); });
    update();
  }).catch(function () {
    giveUp("manifest-failed");
  });
})();
