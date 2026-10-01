/* The scroll fly-through.
 *
 * A pinned canvas scrubbed by scroll position, with the checker's own work
 * animated over it. No framework, no build step, same as the rest of the site.
 *
 * The hard requirement this file is built around (round 9): someone flinging
 * the page top to bottom and back as hard as they can must see a sharp frame
 * for where they actually are on *every* screen refresh. Never blurry, never
 * stuck, never behind. Everything below follows from that.
 *
 *  1. Every refresh is a deadline. `tick` reads the scroll position and draws
 *     the frame for *that* position, full stop. There is no easing, no
 *     catch-up queue and no "work through the frames we skipped": if a fling
 *     crosses thirty frames between two refreshes, twenty-nine of them are
 *     simply never drawn and never decoded. Falling behind is the bug.
 *
 *  2. Decoding happens off the main thread. createImageBitmap on one AVIF
 *     frame costs about 21ms at 1920x1080 and 34ms at 2560x1440 on a fast
 *     laptop, measured on a real GPU, against a 16.7ms budget at 60Hz. A pool
 *     of two to four workers (static/flight-decoder.js) turns that latency
 *     into throughput; where Worker is unavailable, a capped main-thread path
 *     stands in.
 *
 *  3. Three tiers, each with one job.
 *     * motion  (1280x720, or 540x960 on a phone) decodes in 10.8ms / 6.4ms
 *       and is what a fling draws from. On a machine with memory to spare the
 *       whole tier is decoded during idle time and then simply stays
 *       resident, which is what makes "sharp on every refresh at any speed"
 *       achievable rather than merely likely.
 *     * hi-res  (1920, 2560 or 810x1440) is what the reader looks at whenever
 *       the scroll slows below the velocity threshold, and always at rest.
 *     * lores   (160px, every frame, decoded up front and held) exists only
 *       to cover the first second after load. After LORES_WINDOW_MS it is
 *       never drawn again, at any speed, for any reason.
 *
 *  4. Velocity predicts. The decode request issued each refresh is for where
 *     the scroll is *going* to be on the next one or two refreshes, not where
 *     it is now, because a decode started now finishes after the deadline it
 *     was wanted for. Requests for frames the scroll has already passed are
 *     dropped from the queue rather than completed.
 *
 *  5. No freezing. If the exact frame is not ready, the nearest ready frame
 *     within SUB_RADIUS of the target is drawn instead. If the frame on screen
 *     has not changed for FREEZE_LIMIT_MS while the scroll position is moving,
 *     the search widens until something moves, and the widening is counted
 *     (`offTarget`) rather than hidden.
 *
 * Two things that have not changed. The page works with the animation off: if
 * the sequence cannot load, or the reader asked for reduced motion, we add
 * .no-flight and the stages become stills in normal page flow, with the claim
 * input present either way. And nothing here calls preventDefault or moves the
 * scroll position; the reader stays in charge, forwards and backwards.
 */

(function () {
  "use strict";

  var BYTES_CONCURRENCY = 6;      // parallel fetches per tier
  var LORES_CONCURRENCY = 10;
  var LORES_WINDOW_MS = 1000;     // after this, lores is never drawn again
  var FREEZE_LIMIT_MS = 100;      // the frame on screen must change inside this
  var SUB_RADIUS = 2;             // nearest ready frame may stand in this far away
  var PREDICT_MS = 24;            // how far ahead of now a decode is aimed
  // Requests allowed to wait at once. Generous, because pump() dispatches in
  // priority order rather than arrival order: a queue full of idle pre-decode
  // work costs one sort and never delays the frame being drawn.
  var QUEUE_MAX = 32;

  // Above this many footage-frames per second the motion tier is what gets
  // drawn; below the lower number, hi-res. Two numbers, not one, so a scroll
  // hovering at the boundary does not flicker between a 1280px frame and a
  // 1920/2560px one. 18 frames/s is three quarters of a second of footage per
  // second of real time: comfortably more than a reading scroll, comfortably
  // less than a flick.
  var MOTION_ENTER_VELOCITY = 18;
  var MOTION_LEAVE_VELOCITY = 10;
  // Crossfading two frames only reads as smoothness while the scroll is slow
  // enough that adjacent frames are nearly the same picture. Faster than this
  // it reads as a double exposure, so draw() snaps to one sharp frame.
  var CROSSFADE_MAX_VELOCITY = 12;
  var VELOCITY_SMOOTHING = 0.5;

  var FRAME_WIDTH_DEFAULT = 1920; // must match scripts/build_flight.py FRAME_WIDTH
  var variant = "default";

  // Three hi-res tiers, chosen once at load. "large" gives a genuinely wide
  // desktop viewport a real 2560px source instead of the 1920 tier stretched
  // to fill it; "phone" gives a narrow portrait viewport a sequence cropped to
  // 9:16 at build time (native pixels, never an upscale). The breakpoint
  // compares *physical* pixels (CSS width times DPR, capped at 2 to match
  // sizeCanvas) against the default tier's own width, so an ordinary DPR2
  // laptop gets the 2560 tier rather than a 50% upscale of the 1920 one.
  function pickVariant() {
    var w = window.innerWidth, h = window.innerHeight;
    if (w < 700 && h > w) return "phone";
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (w * dpr > FRAME_WIDTH_DEFAULT) return "large";
    return "default";
  }

  var root = document.documentElement;
  var section = document.getElementById("flight");
  if (!section) return;

  var canvas = document.getElementById("flight-canvas");
  var overlay = document.getElementById("flight-overlay");
  var progressEl = document.getElementById("flight-progress");

  function manifestUrl() {
    if (variant === "large") return section.dataset.frames + "/manifest-2560.json";
    if (variant === "phone") return section.dataset.frames + "/manifest-phone.json";
    return section.dataset.frames + "/manifest.json";
  }

  function giveUp(why) {
    root.classList.add("no-flight");
    if (why) root.setAttribute("data-flight-off", why);
  }

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var conn = navigator.connection || {};
  var saveData = !!conn.saveData;
  if (reduced || saveData || !canvas) {
    giveUp(reduced ? "reduced-motion" : saveData ? "save-data" : "no-canvas");
    return;
  }

  // A connection that reports 2g or 3g gets the motion tier and nothing else:
  // 6MB instead of 23MB, still sharp enough to read at any scroll speed, and
  // the hi-res tier that a stopped reader would otherwise get is simply not
  // worth 17MB on a connection like that.
  var slowNetwork = /^(slow-2g|2g|3g)$/.test(conn.effectiveType || "");

  var ctx = canvas.getContext("2d", { alpha: false });
  // Footage plays at full brightness in its own right (no scrim), so sharpness
  // rests on the source frame and this setting. Measured on a real GPU: "low"
  // and "high" look the same here and "low" is measurably cheaper per draw, so
  // "low" stays.
  if ("imageSmoothingQuality" in ctx) ctx.imageSmoothingQuality = "low";

  var manifest = null;
  var beats = null;
  var startedAt = performance.now();
  var pad4 = function (i) {
    var n = String(i);
    while (n.length < 4) n = "0" + n;
    return n;
  };

  /* ---- what the stress test reads --------------------------------------- */

  // Cheap, always-on counters: a handful of integer writes per tick. The
  // fast-scroll claims in DECISIONS.md are measured off these, not guessed
  // from a screenshot.
  var stats = {
    draws: 0, seen: {}, distinct: 0,
    hiresDraws: 0, motionDraws: 0, loresDraws: 0, loresAfterWindow: 0,
    blendDraws: 0, blendDrawsAtSpeed: 0,
    offTarget: 0, maxOffTarget: 0,   // drawn index further than SUB_RADIUS from target
    holdMs: 0, maxHoldMs: 0,          // longest a single frame stayed up while moving
    freezeBreaks: 0,                  // times the search widened to break a hold
    longTasks: 0, longestTaskMs: 0,
    decodes: 0, decodeFails: 0, queueDepth: 0,
    workers: 0, residentMotion: 0, residentHires: 0,
    velocity: 0, peakVelocity: 0, tier: "-"
  };
  stats.reset = function () {
    stats.draws = 0; stats.seen = {}; stats.distinct = 0;
    stats.hiresDraws = 0; stats.motionDraws = 0; stats.loresDraws = 0;
    stats.loresAfterWindow = 0; stats.blendDraws = 0; stats.blendDrawsAtSpeed = 0;
    stats.offTarget = 0; stats.maxOffTarget = 0;
    stats.maxHoldMs = 0; stats.freezeBreaks = 0;
    stats.longTasks = 0; stats.longestTaskMs = 0;
    stats.decodes = 0; stats.decodeFails = 0; stats.peakVelocity = 0;
    // Otherwise the first tick after a reset reports however long the page sat
    // idle beforehand as a frame hold.
    drawnAt = performance.now();
    heldWhileMoving = false;
  };
  window.__flightStats = stats;

  // Long tasks are the other half of "never laggy": a 60ms decode or layout on
  // the main thread shows up as a skipped refresh no matter how ready the
  // frames are. Observed rather than inferred.
  if (typeof PerformanceObserver === "function") {
    try {
      new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (e) {
          if (e.duration > 50) {
            stats.longTasks++;
            if (e.duration > stats.longestTaskMs) stats.longestTaskMs = e.duration;
          }
        });
      }).observe({ type: "longtask", buffered: true });
    } catch (err) { /* not supported: the counter stays 0 and says so */ }
  }

  var DEBUG = /(?:^|[?&])debug=1(?:&|$)/.test(location.search);
  var debugEl = null;

  function updateDebugOverlay(f0, f1, t, target) {
    if (!DEBUG) return;
    if (!debugEl) {
      debugEl = document.createElement("div");
      debugEl.id = "flight-debug";
      debugEl.style.cssText = "position:fixed;top:8px;left:8px;z-index:9999;" +
        "background:rgba(0,0,0,.78);color:#5f5;font:11px/1.5 ui-monospace," +
        "Menlo,monospace;padding:6px 9px;border-radius:4px;pointer-events:none;" +
        "white-space:pre;max-width:72vw;";
      document.body.appendChild(debugEl);
    }
    debugEl.textContent =
      "want " + target + "  drawn " + f0.index + " [" + f0.tier + "]" +
      (f1 ? "  blend-> " + f1.index + " t=" + t.toFixed(2) : "") +
      "\nvelocity " + stats.velocity.toFixed(0) + " f/s  tier " + stats.tier +
      "  hold " + stats.holdMs.toFixed(0) + "ms (max " + stats.maxHoldMs.toFixed(0) + ")" +
      "\ndraws " + stats.draws + "  distinct " + stats.distinct +
      "  hires " + stats.hiresDraws + "  motion " + stats.motionDraws +
      "  lores " + stats.loresDraws + " (after 1s " + stats.loresAfterWindow + ")" +
      "\nblend " + stats.blendDraws + " (at speed " + stats.blendDrawsAtSpeed + ")" +
      "  offTarget " + stats.offTarget + " (worst " + stats.maxOffTarget + ")" +
      "  breaks " + stats.freezeBreaks +
      "\nlongTasks>50ms " + stats.longTasks + " (worst " + stats.longestTaskMs.toFixed(0) + "ms)" +
      "  queue " + stats.queueDepth + "  workers " + stats.workers +
      "\nresident motion " + stats.residentMotion + "/" + (manifest ? manifest.count : "?") +
      "  hires " + stats.residentHires + "/" + hiresStore.cap;
  }

  /* ---- the decode pool --------------------------------------------------- */

  var pool = [];          // { w, busy }
  var queue = [];         // { store, index, prio }
  var mainDecodes = 0;    // in-flight main-thread decodes (fallback path)
  var MAIN_DECODE_MAX = 2;

  function buildPool() {
    if (typeof Worker !== "function") return;
    var n = Math.max(2, Math.min(4, (navigator.hardwareConcurrency || 4) - 1));
    for (var i = 0; i < n; i++) {
      try {
        var slot = { w: new Worker(section.dataset.decoder), busy: false };
        slot.w.onmessage = function (e) { onDecoded(slot, e.data); };
        slot.w.onerror = function () { slot.busy = false; };
        pool.push(slot);
      } catch (err) { break; }
    }
    stats.workers = pool.length;
  }

  function onDecoded(slot, d) {
    slot.busy = false;
    var store = d.tier === "motion" ? motionStore : hiresStore;
    store.inFlight.delete(d.index);
    if (d.kind === "decoded") {
      stats.decodes++;
      store.put(d.index, d.bmp);
    } else {
      stats.decodeFails++;
      store.broken[d.index] = 1;
    }
    pump();
  }

  function pump() {
    if (!queue.length) { stats.queueDepth = 0; return; }
    queue.sort(function (a, b) { return a.prio - b.prio; });
    for (var i = 0; i < pool.length && queue.length; i++) {
      if (pool[i].busy) continue;
      var job = queue.shift();
      if (job.store.bitmaps.has(job.index)) { i--; continue; }
      pool[i].busy = true;
      job.store.inFlight.add(job.index);
      pool[i].w.postMessage({
        kind: "decode", tier: job.store.name, index: job.index,
        blob: job.store.blobs[job.index]
      });
    }
    // No workers (or none left free and none exist): decode on the main thread,
    // strictly capped, so an old browser still gets a moving picture.
    while (!pool.length && queue.length && mainDecodes < MAIN_DECODE_MAX) {
      (function (job) {
        mainDecodes++;
        job.store.inFlight.add(job.index);
        createImageBitmap(job.store.blobs[job.index]).then(function (bmp) {
          mainDecodes--;
          job.store.inFlight.delete(job.index);
          stats.decodes++;
          job.store.put(job.index, bmp);
          pump();
        }).catch(function () {
          mainDecodes--;
          job.store.inFlight.delete(job.index);
          stats.decodeFails++;
          job.store.broken[job.index] = 1;
          pump();
        });
      })(queue.shift());
    }
    stats.queueDepth = queue.length;
  }

  /* ---- a tier of frames -------------------------------------------------- */

  function TierStore(name, pattern) {
    this.name = name;
    this.pattern = pattern;
    this.blobs = [];
    this.bitmaps = new Map();
    this.inFlight = new Set();
    this.broken = {};
    this.bytesLoaded = 0;
    this.countLoaded = 0;
    this.cap = 0;
    this.full = false;       // every frame resident: nothing left to decode
  }

  TierStore.prototype.url = function (i) {
    return section.dataset.frames + "/" + this.pattern.replace("%04d", pad4(i));
  };

  TierStore.prototype.put = function (i, bmp) {
    this.bitmaps.set(i, bmp);
    this.evict();
    if (this.bitmaps.size >= manifest.count) this.full = true;
  };

  // Distance-based, not insertion order: the idle pre-decode fills this map
  // outward from wherever the reader is, and FIFO eviction would throw away
  // whichever frame happened to decode first rather than whichever is
  // furthest from the scroll position. Never evicts the frame on screen.
  TierStore.prototype.evict = function () {
    if (this.bitmaps.size <= this.cap) return;
    var keep = this.cap, cur = Math.round(wantedFrame), self = this;
    var idxs = [];
    this.bitmaps.forEach(function (_, idx) { idxs.push(idx); });
    idxs.sort(function (a, b) { return Math.abs(b - cur) - Math.abs(a - cur); });
    for (var i = 0; i < idxs.length && self.bitmaps.size > keep; i++) {
      if (idxs[i] === drawnIndex || idxs[i] === cur) continue;
      var b = self.bitmaps.get(idxs[i]);
      if (b && b.close) b.close();
      self.bitmaps.delete(idxs[i]);
      self.full = false;
    }
  };

  TierStore.prototype.readyNear = function (i, radius) {
    var b = this.bitmaps.get(i);
    if (b) return { bmp: b, index: i };
    for (var d = 1; d <= radius; d++) {
      b = this.bitmaps.get(i - d);
      if (b) return { bmp: b, index: i - d };
      b = this.bitmaps.get(i + d);
      if (b) return { bmp: b, index: i + d };
    }
    return null;
  };

  TierStore.prototype.nearestReady = function (i) {
    var best = null, bestD = Infinity;
    this.bitmaps.forEach(function (bmp, idx) {
      var d = Math.abs(idx - i);
      if (d < bestD) { bestD = d; best = { bmp: bmp, index: idx }; }
    });
    return best;
  };

  TierStore.prototype.preloadBytes = function () {
    var self = this, next = 0, inFlight = 0;
    return new Promise(function (resolve) {
      function step() {
        while (inFlight < BYTES_CONCURRENCY && next < manifest.count) {
          (function (idx) {
            inFlight++;
            fetch(self.url(idx)).then(function (r) { return r.blob(); })
              .then(function (blob) { self.blobs[idx] = blob; self.bytesLoaded += blob.size; })
              .catch(function () { self.broken[idx] = 1; })
              .then(function () {
                inFlight--;
                self.countLoaded++;
                updateProgress();
                if (self.countLoaded >= manifest.count) resolve();
                else step();
              });
          })(next);
          next++;
        }
      }
      step();
    });
  };

  var motionStore = new TierStore("motion", "motion/frame-%04d.avif");
  var hiresStore = new TierStore("hires", "frame-%04d.avif");

  // No "pre-warm the GPU upload" step here, deliberately. An earlier version of
  // this round drew each freshly decoded bitmap into a 1x1 scratch context on
  // the theory that the first drawImage pays a texture upload that a fling
  // cannot afford. Measured on the real on-screen canvas, that theory is wrong
  // twice over: the first draw of a never-drawn bitmap and the second draw of
  // the same bitmap cost the same (median 0ms, mean 3-4ms, with identical
  // multi-tens-of-ms outliers on both), so there is no upload to front-load;
  // and the 1x1 target is CPU-backed, so "warming" into it forced a full
  // read-back that showed up as 31 long tasks of about 65ms each across the
  // load. Removing it removed all of them.

  function requestDecode(store, index, prio) {
    if (index < 0 || index >= manifest.count) return;
    if (store.bitmaps.has(index) || store.inFlight.has(index)) return;
    if (!store.blobs[index] || store.broken[index]) return;
    for (var i = 0; i < queue.length; i++) {
      if (queue[i].store === store && queue[i].index === index) {
        if (prio < queue[i].prio) queue[i].prio = prio;
        return;
      }
    }
    queue.push({ store: store, index: index, prio: prio });
  }

  /* ---- the low-res tier: first second only ------------------------------- */

  var lores = [];
  var loresLoaded = 0;

  function loadLores() {
    var next = 0, inFlight = 0;
    function step() {
      while (inFlight < LORES_CONCURRENCY && next < manifest.count) {
        (function (idx) {
          inFlight++;
          fetch(section.dataset.frames + "/" + manifest.loresPattern.replace("%04d", pad4(idx)))
            .then(function (r) { return r.blob(); })
            .then(createImageBitmap)
            .then(function (bmp) { lores[idx] = bmp; })
            .catch(function () { /* a missing low-res frame just leaves that slot empty */ })
            .then(function () { inFlight--; loresLoaded++; updateProgress(); step(); });
        })(next);
        next++;
      }
    }
    step();
  }

  // Once the lores window has closed these bitmaps can never be drawn again,
  // so they are closed outright rather than left holding memory the motion
  // tier could be using.
  var loresRetired = false;

  function retireLores() {
    if (loresRetired) return;
    loresRetired = true;
    for (var i = 0; i < lores.length; i++) {
      if (lores[i] && lores[i].close) lores[i].close();
      lores[i] = null;
    }
  }

  /* ---- progress indicator ------------------------------------------------ */

  var progressScheduled = false;

  function updateProgress() {
    if (!progressEl || progressScheduled) return;
    progressScheduled = true;
    requestAnimationFrame(function () {
      progressScheduled = false;
      var frac = (loresLoaded + motionStore.countLoaded + hiresStore.countLoaded) /
        (manifest.count * 3);
      if (frac >= 0.999) { progressEl.hidden = true; return; }
      progressEl.hidden = false;
      progressEl.textContent = "Loading footage … " + Math.round(frac * 100) + "%";
    });
  }

  /* ---- resolving a target index to something drawable -------------------- */

  var drawnIndex = -1;          // index actually on screen
  var drawnAt = startedAt;      // when it went up
  var lastDrawn = { b0: null, b1: null, t: -1 };
  var hasDrawn = false;
  var fastMode = false;         // drawing from the motion tier
  // Whether the scroll has been moving at any point during the current hold.
  // The 100ms limit is about frames held while the reader is scrolling; a frame
  // held for ten seconds because nobody touched the page is not a freeze.
  var heldWhileMoving = false;

  // Picks what to draw for `target`. Preference order depends on speed: at
  // speed the motion tier first (it is the tier that can actually be ready,
  // and a consistent 1280px source beats alternating between two sharpnesses),
  // below the threshold hi-res first. `widen` is set by the freeze limit: it
  // allows a stand-in further than SUB_RADIUS away, because a frame that is a
  // few frames off but moving beats a correct frame that is frozen.
  function resolveFrame(target, widen) {
    var now = performance.now();
    var loresAllowed = !loresRetired && now - startedAt < LORES_WINDOW_MS;
    var first = fastMode ? motionStore : hiresStore;
    var second = fastMode ? hiresStore : motionStore;

    var r = first.readyNear(target, SUB_RADIUS);
    if (r) return { bmp: r.bmp, index: r.index, tier: first.name };
    r = second.readyNear(target, SUB_RADIUS);
    if (r) return { bmp: r.bmp, index: r.index, tier: second.name };

    if (loresAllowed && lores[target]) {
      return { bmp: lores[target], index: target, tier: "lores" };
    }

    if (widen) {
      var a = first.nearestReady(target), b = second.nearestReady(target);
      var pick = null, name = "";
      if (a && (!b || Math.abs(a.index - target) <= Math.abs(b.index - target))) { pick = a; name = first.name; }
      else if (b) { pick = b; name = second.name; }
      if (pick) {
        var off = Math.abs(pick.index - target);
        stats.offTarget++;
        if (off > stats.maxOffTarget) stats.maxOffTarget = off;
        return { bmp: pick.bmp, index: pick.index, tier: name };
      }
    }

    if (loresAllowed) {
      for (var d = 1; d < manifest.count; d++) {
        if (lores[target - d]) return { bmp: lores[target - d], index: target - d, tier: "lores" };
        if (lores[target + d]) return { bmp: lores[target + d], index: target + d, tier: "lores" };
      }
    }
    return null;
  }

  function currentFocusX() {
    // The phone tier's crop is baked into the asset at build time, so applying
    // beats.json's per-beat mobile.focusX on top would crop twice.
    if (variant === "phone") return 0.5;
    var row = activeRow;
    if (!row || !isMobile) return 0.5;
    var m = row.b.mobile || {};
    return typeof m.focusX === "number" ? m.focusX : 0.5;
  }

  function drawOne(bmp, alpha) {
    var cw = canvas.width, ch = canvas.height;
    var scale = Math.max(cw / bmp.width, ch / bmp.height);
    var dw = bmp.width * scale, dh = bmp.height * scale;
    var fx = currentFocusX();
    ctx.globalAlpha = alpha;
    ctx.drawImage(bmp, (cw - dw) * fx, (ch - dh) / 2, dw, dh);
    ctx.globalAlpha = 1;
  }

  // Draws the continuous scroll position `pos`, not an index. Below the
  // crossfade velocity a position landing between two resident frames draws
  // both, so slow scroll reads as motion instead of a visible step; at speed
  // it snaps to the nearest single frame.
  function draw(pos, force, allowBlend, widen) {
    var last = manifest.count - 1;
    var i0 = allowBlend
      ? Math.max(0, Math.min(last, Math.floor(pos)))
      : Math.max(0, Math.min(last, Math.round(pos)));
    var i1 = Math.min(last, i0 + 1);
    var t = (allowBlend && i1 !== i0) ? pos - i0 : 0;

    var f0 = resolveFrame(i0, widen);
    if (!f0) return false;
    var f1 = (allowBlend && t > 0.004) ? resolveFrame(i1, false) : null;
    // Only blend two frames from the same tier: mixing a 1280px frame with a
    // 1920px one halfway through a crossfade is a visible sharpness pulse.
    if (f1 && (f1.tier !== f0.tier || f1.index === f0.index)) { f1 = null; t = 0; }

    if (!force && hasDrawn && f0.bmp === lastDrawn.b0 &&
        (f1 ? f1.bmp : null) === lastDrawn.b1 && Math.abs(t - lastDrawn.t) < 0.004) {
      updateDebugOverlay(f0, f1, t, i0);
      return true;
    }

    lastDrawn.b0 = f0.bmp;
    lastDrawn.b1 = f1 ? f1.bmp : null;
    lastDrawn.t = t;
    hasDrawn = true;

    stats.draws++;
    if (!stats.seen[f0.index]) { stats.seen[f0.index] = 1; stats.distinct++; }
    if (f0.tier === "lores") {
      stats.loresDraws++;
      if (performance.now() - startedAt >= LORES_WINDOW_MS) stats.loresAfterWindow++;
    } else if (f0.tier === "motion") stats.motionDraws++;
    else stats.hiresDraws++;
    if (f1) {
      stats.blendDraws++;
      if (stats.velocity >= CROSSFADE_MAX_VELOCITY) stats.blendDrawsAtSpeed++;
    }

    drawOne(f0.bmp, 1);
    if (f1) drawOne(f1.bmp, t);

    if (f0.index !== drawnIndex) {
      var nowMs = performance.now();
      // Close out the hold that just ended, so a frame that was up for 140ms
      // is counted even though the frame after it arrived.
      if (drawnIndex >= 0 && heldWhileMoving && nowMs - drawnAt > stats.maxHoldMs) {
        stats.maxHoldMs = nowMs - drawnAt;
      }
      drawnIndex = f0.index;
      drawnAt = nowMs;
    }
    updateDebugOverlay(f0, f1, t, i0);
    return true;
  }

  function sizeCanvas() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var cw = canvas.clientWidth, ch = canvas.clientHeight;
    // Never allocate a backing store larger than the frames can fill. Frames are
    // drawn cover-cropped, so a canvas wider than the frame (or taller) is pure
    // upscale: more pixels to rasterise every refresh for detail that does not
    // exist in the source. At 1440x900 DPR2 the naive 2880x1800 store is 36%
    // more pixels than the 2560x1440 frame holds, and under a throttled CPU that
    // surplus is what pushes a refresh past its deadline. Tier choice still uses
    // the uncapped size, so this only ever shrinks the store, never the tier.
    //
    // Sized once, from the hi-res tier: see the note in tick() for why following
    // the motion tier while moving measures worse, not better.
    if (manifest && manifest.width && manifest.height && cw > 0 && ch > 0) {
      dpr = Math.min(dpr, manifest.width / cw, manifest.height / ch);
    }
    var w = Math.max(1, Math.round(cw * dpr));
    var h = Math.max(1, Math.round(ch * dpr));
    if (canvas.width === w && canvas.height === h) return;
    canvas.width = w;
    canvas.height = h;
    if (hasDrawn) draw(wantedFrame, true, false, true);
  }

  /* ---- the timeline ------------------------------------------------------ */

  var plan = [];
  var stageSpans = {};
  var totalPx = 0;
  var travelPx = 1;
  var isMobile = false;
  var sectionTop = 0;

  function beatVh(b) {
    var m = b.mobile || {};
    return isMobile && typeof m.vh === "number" ? m.vh : b.vh;
  }

  function layout() {
    isMobile = window.innerWidth < 700;
    var vh = window.innerHeight / 100;

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
  }

  function stageProgress(name, scrollPx) {
    var span = stageSpans[name];
    if (!span) return 0;
    return clamp01((scrollPx - span.start) / Math.max(1, span.end - span.start));
  }

  var activeRow = null;

  function frameFor(row, p) {
    if (typeof row.b.hold === "number") {
      return row.b.hold < 0 ? manifest.count - 1 : row.b.hold;
    }
    var secs = row.b.from + (row.b.to - row.b.from) * p;
    return Math.max(0, Math.min(manifest.count - 1, secs * manifest.fps));
  }

  /* ---- idle pre-decode of the motion tier -------------------------------- */

  var ric = typeof window.requestIdleCallback === "function"
    ? window.requestIdleCallback.bind(window)
    : function (cb) {
        return setTimeout(function () {
          cb({ didTimeout: true, timeRemaining: function () { return 0; } });
        }, 60);
      };

  // "After the page is interactive": the load event has fired and the browser
  // has reported an idle moment. ric's own 1500ms timeout is the backstop, so a
  // page that never goes idle still gets its footage.
  function whenInteractive(fn) {
    var go = function () {
      if (typeof window.requestIdleCallback === "function") {
        window.requestIdleCallback(fn, { timeout: 1500 });
      } else {
        setTimeout(fn, 200);
      }
    };
    if (document.readyState === "complete") go();
    else window.addEventListener("load", go, { once: true });
  }

  var idleScheduled = false;

  // "Pre-decoded into memory after the page goes idle, as much as memory
  // safely allows." The whole motion tier if it fits (measured: 600 bitmaps at
  // 1280x720 held on an 8GB machine with decode time staying flat and the JS
  // heap untouched, because ImageBitmaps do not live on it), otherwise a
  // window around wherever the reader is. Frames nearest the reader go first,
  // so an interrupted preload is still the useful half.
  function scheduleIdlePredecode() {
    if (idleScheduled || !motionStore.blobs.length) return;
    idleScheduled = true;
    ric(function () {
      idleScheduled = false;
      var cur = Math.round(wantedFrame);
      var room = motionStore.cap - motionStore.bitmaps.size;
      if (room <= 0) { setTimeout(scheduleIdlePredecode, 500); return; }
      // Already as much queued as the workers can usefully hold: come back
      // when some of it has landed, rather than spinning.
      if (queue.length >= QUEUE_MAX) { setTimeout(scheduleIdlePredecode, 100); return; }

      // Nearest first, so an interrupted preload is still the useful half, and
      // so eviction (which is also distance-based) is not fighting it. Only
      // frames inside the cap's reach are worth asking for.
      var want = [], i;
      for (i = 0; i < manifest.count; i++) {
        if (motionStore.bitmaps.has(i) || motionStore.inFlight.has(i)) continue;
        if (!motionStore.blobs[i] || motionStore.broken[i]) continue;
        want.push(i);
      }
      // Nothing left to decode. Keep a slow heartbeat anyway: eviction during
      // a long session frees slots, and this is what refills them.
      if (!want.length) { motionStore.full = true; setTimeout(scheduleIdlePredecode, 1000); return; }
      want.sort(function (a, b) { return Math.abs(a - cur) - Math.abs(b - cur); });

      // These all queue at priority 1000+, so every one of them yields to the
      // frame the reader is actually looking at. Four per slice, not eight:
      // adopting a transferred ImageBitmap costs the main thread real work, and
      // eight landing together was measurably one long task rather than four
      // short ones.
      var budget = Math.min(room, 4);
      for (i = 0; i < want.length && budget > 0; i++) {
        requestDecode(motionStore, want[i], 1000 + Math.abs(want[i] - cur));
        budget--;
      }
      pump();
      setTimeout(scheduleIdlePredecode, 0);
    }, { timeout: 400 });
  }

  /* ---- the animation over the footage ------------------------------------ */

  var el = {};
  var cards = [];
  var sortedIndex = [];
  var originalTop = [];
  var targetTop = [];
  var STACK_GAP = 8;

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

  /* A panel that is faded out is also made invisible, so its field cannot be
     tabbed into and a screen reader does not read three panels at once.
     `visibility` keeps the box, so this costs no reflow. */
  function setLayer(name, op) {
    var node = el.layers && el.layers[name];
    if (!node) return;
    var o = clamp01(op);
    node.style.opacity = String(o);
    node.style.visibility = o > 0.02 ? "visible" : "hidden";
  }

  function textOpacity(row, p) {
    if (!row.b.chapter) return 0;
    var f = row.b.fade;
    if (!f) return 1;
    if (p < f.in) return f.in <= 0 ? 1 : clamp01(p / f.in);
    if (p > f.out) return clamp01((1 - p) / Math.max(1e-6, 1 - f.out));
    return 1;
  }

  function paint(row, p, scrollPx) {
    var stage = row.b.stage;
    var tOp = textOpacity(row, p);

    for (var id in el.chapters) setOn(el.chapters[id], id === row.b.chapter && tOp > 0.02);
    if (el.chapterZone) el.chapterZone.style.opacity = String(tOp);

    el.work.setAttribute("data-stage", stage);

    var weighP = stageProgress("weighing", scrollPx);
    var horizonP = stageProgress("horizon", scrollPx);

    /* Three panels, cross-faded, never reflowed. Nothing here may add or
       remove a row: a row that leaves the flow moves every row that is still
       on screen, and that is layout shift a reader can see and a Core Web
       Vital can measure. Opacity and visibility only. */
    // A panel belongs to a stage, not to a beat's text fade: the chapter
    // headline fades with its beat, but the panel carrying the claim field
    // must not blink out halfway through the stage that owns it. The only
    // moments with no panel are the four crossfades, which carry no text at
    // all, and the gap between the work panel leaving and the verdict
    // arriving.
    var workOn = stage === "archive" || stage === "weighing"
      || stage === "write" || stage === "publish";
    setLayer("claim", stage === "claim" ? 1 : 0);
    setLayer("work", workOn ? 1
      : (stage === "horizon" ? 1 - clamp01(horizonP / 0.22) : 0));
    setLayer("verdict", stage === "horizon" ? clamp01((horizonP - 0.3) / 0.2) : 0);

    var stackOn = stage === "weighing" || stage === "write"
      || stage === "publish" || stage === "horizon";

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
      var hide = stage === "horizon" ? clamp01(horizonP * 2.2) : 0;
      // Arrival has to be carried here rather than left to the data-in rule:
      // this inline opacity wins over the stylesheet, so without the factor
      // every study is on screen from the first frame of the stage while the
      // counter underneath still says two of eight. A study that has not
      // arrived yet is a blank slot rather than nothing at all: the stack
      // holds all eight rows from the start, so the panel never changes
      // height, and the empty ones read as a form filling in.
      var inOp = i < arrived ? 1 : 0.26;
      cards[i].style.transform = "translateY(" + shift.toFixed(1)
        + "px) scaleY(" + (1 - hide) + ")";
      cards[i].style.opacity = String((1 - hide) * midFade * inOp);
    }

    setOn(el.bar, (stage === "weighing" && weighP > 0.75) || stage === "write" || stage === "publish" || stage === "horizon");

    setOn(el.verdict, stage === "horizon" && horizonP > 0.3);
    setOn(el.open, stage === "horizon" && horizonP > 0.55);
    setOn(el.again, stage === "horizon" && horizonP > 0.75);
  }

  /* ---- scroll + the deadline loop ----------------------------------------
   * The scroll listener does nothing but set a flag: no DOM read, no DOM
   * write. Once per animation frame `tick` reads scrollY, recomputes the
   * overlay, and draws the frame for exactly that position. */

  var wantedFrame = 0;
  var lastWantedFrame = 0;
  var signedVelocity = 0;   // footage frames per second, signed
  var scrollDirty = true;
  var lastTickTime = null;
  var firstTick = true;

  function onScroll() { scrollDirty = true; }

  function updateFromScroll() {
    var local = Math.max(0, Math.min(totalPx, window.scrollY - sectionTop));

    var row = plan[0];
    for (var i = 0; i < plan.length; i++) {
      if (local >= plan[i].start && local < plan[i].end) { row = plan[i]; break; }
      row = plan[i];
    }
    activeRow = row;
    var p = clamp01((local - row.start) / Math.max(1, row.end - row.start));

    wantedFrame = frameFor(row, p);
    paint(row, p, local);
  }

  function tick(now) {
    if (scrollDirty) { scrollDirty = false; updateFromScroll(); }
    if (firstTick) { firstTick = false; lastWantedFrame = wantedFrame; }

    var dt = lastTickTime == null ? 16 : Math.max(1, Math.min(200, now - lastTickTime));
    lastTickTime = now;

    var raw = (wantedFrame - lastWantedFrame) / (dt / 1000);
    var moving = Math.abs(wantedFrame - lastWantedFrame) > 0.01;
    lastWantedFrame = wantedFrame;
    signedVelocity += (raw - signedVelocity) * VELOCITY_SMOOTHING;
    var speed = Math.abs(signedVelocity);
    stats.velocity = speed;
    if (speed > stats.peakVelocity) stats.peakVelocity = speed;

    // Hysteresis, so a scroll sitting near the threshold does not alternate
    // tiers from one refresh to the next.
    if (fastMode && speed < MOTION_LEAVE_VELOCITY) fastMode = false;
    else if (!fastMode && speed > MOTION_ENTER_VELOCITY) fastMode = true;
    stats.tier = fastMode ? "motion" : "hires";
    // Follow the tier with the backing store. The velocity hysteresis above is
    // what keeps this to a couple of reallocations per fling rather than one per
    // refresh, and sizeCanvas returns untouched when the size already matches.
    // The backing store deliberately does NOT follow the tier. Sizing it to the
    // motion frame while moving looks like free work saved, and measured the
    // other way: 4x throttled desktop, 500ms and 1000ms full-page flings,
    // worst hold 138ms / 175ms and 24 / 39 long tasks when the store is resized
    // at every velocity threshold crossing, against 93ms / 120ms and 9 / 9 when
    // it is allocated once. Reallocating a backing store costs more than the
    // interpolated pixels it saves, and a fling crosses the threshold twice per
    // leg.

    if (!loresRetired && now - startedAt >= LORES_WINDOW_MS) retireLores();

    var target = Math.round(wantedFrame);

    // Requests, in priority order, before the draw: the frame wanted now,
    // then where the scroll will be one and two refreshes from now. At a fling
    // speed of 1800 frames/s a refresh is thirty frames of travel, so "now" is
    // already history by the time a decode lands; the predicted band is what
    // actually gets drawn next.
    var ahead = signedVelocity * (PREDICT_MS / 1000);
    var p1 = Math.round(wantedFrame + ahead);
    var p2 = Math.round(wantedFrame + ahead * 2);
    var tiers = fastMode ? [motionStore, hiresStore] : [hiresStore, motionStore];

    // Anything queued that the scroll has already gone past is dropped rather
    // than decoded: finishing it would spend a worker on a frame nobody will
    // ever see, which is how the old build fell behind. Only while actually
    // moving, and only the hot-path requests: the idle pre-decode (priority
    // 1000 and up) is deliberately filling in frames behind the reader as well
    // as ahead, and must not be pruned for doing its job.
    if (speed > MOTION_LEAVE_VELOCITY) {
      var dir = signedVelocity >= 0 ? 1 : -1;
      queue = queue.filter(function (job) {
        if (job.prio >= 1000) return true;
        return (job.index - target) * dir >= -SUB_RADIUS;
      });
    }

    var primary = tiers[0];
    if (fastMode) {
      // The frame wanted *now* is already history by the time a decode of it
      // lands, so the predicted band outranks it.
      requestDecode(primary, p1, 0);
      requestDecode(primary, target, 8);
    } else {
      requestDecode(primary, target, 0);
      requestDecode(primary, p1, 5);
    }
    for (var k = 1; k <= SUB_RADIUS; k++) {
      requestDecode(primary, p1 + k, 10 + k);
      requestDecode(primary, p1 - k, 10 + k);
    }
    if (speed > MOTION_LEAVE_VELOCITY) requestDecode(primary, p2, 20);
    // At rest the second tier is worth filling too: it is the hi-res frame the
    // reader is about to stop on, or the motion frame that covers the next
    // flick out of a standstill.
    if (!moving || speed < MOTION_LEAVE_VELOCITY) {
      requestDecode(tiers[1], target, 40);
      requestDecode(tiers[1], target + 1, 45);
      requestDecode(tiers[1], target - 1, 45);
    }
    if (queue.length > QUEUE_MAX) {
      queue.sort(function (a, b) { return a.prio - b.prio; });
      queue.length = QUEUE_MAX;
    }
    pump();

    // The freeze limit. While the scroll position is moving, the picture has
    // to change inside FREEZE_LIMIT_MS; if it has not, allow a stand-in from
    // outside SUB_RADIUS rather than hold.
    var positionMoving = moving || Math.abs(target - drawnIndex) > SUB_RADIUS;
    // At rest the frame on screen is the correct frame, so it is not "held": it
    // is simply right, and there is nothing to redraw. Keep the clock fresh
    // while still, or the first tick of a fling charges the whole preceding
    // idle period as one enormous hold and the number means nothing.
    if (!positionMoving) drawnAt = now;
    if (positionMoving) heldWhileMoving = true;
    stats.holdMs = positionMoving ? now - drawnAt : 0;
    var widen = positionMoving && (now - drawnAt) > FREEZE_LIMIT_MS;
    if (widen) stats.freezeBreaks++;

    // A hold still in progress counts too: a fling that ends frozen would
    // otherwise never be recorded, because the frame after it never arrives.
    if (positionMoving && now - drawnAt > stats.maxHoldMs) stats.maxHoldMs = now - drawnAt;

    draw(wantedFrame, false, speed < CROSSFADE_MAX_VELOCITY, widen);
    if (!positionMoving && drawnIndex === target) heldWhileMoving = false;

    stats.residentMotion = motionStore.bitmaps.size;
    stats.residentHires = hiresStore.bitmaps.size;

    requestAnimationFrame(tick);
  }

  /* ---- build the overlay from the real check ------------------------------ */

  function buildOverlay() {
    el.work = overlay.querySelector(".flight-work");
    el.chapterZone = overlay.querySelector(".chapter-zone");
    el.counter = overlay.querySelector(".counter");
    el.stackWrap = overlay.querySelector(".stack");
    el.bar = overlay.querySelector(".evidence-bar");
    el.verdict = overlay.querySelector(".verdict-word");
    el.open = overlay.querySelector(".still-open");
    el.again = overlay.querySelector(".again");
    el.layers = {};
    Array.prototype.forEach.call(overlay.querySelectorAll(".work-layer"), function (n) {
      el.layers[n.dataset.layer] = n;
    });
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

  /* ---- how much stays decoded -------------------------------------------- */

  // An ImageBitmap costs width*height*4 bytes of (mostly GPU-side) memory.
  // The motion tier gets the larger share because it is the tier a fling draws
  // from and because holding all of it resident is the difference between
  // "decode might keep up" and "there is nothing left to decode"; hi-res only
  // has to cover a window around a slow or stopped reader.
  function residentCaps() {
    var gb = typeof navigator.deviceMemory === "number" && navigator.deviceMemory > 0
      ? navigator.deviceMemory : 4;
    var GB = 1073741824;
    var motionBytes = (manifest.motionWidth || 1280) * (manifest.motionHeight || 720) * 4;
    var hiresBytes = manifest.width * manifest.height * 4;

    // All of the tier, or a working window well clear of it: never 90-odd per
    // cent. Measured on this machine, 4x throttled, a 1000ms full-page fling,
    // desktop (552 frames in the tier):
    //
    //   resident   long tasks   worst hold
    //   552/552        9          106ms
    //   512/552       33          192ms     <- the trap
    //   320/552        6          177ms
    //   240/552        2          186ms
    //
    // Sitting just short of the whole tier is the worst of both: every leg of a
    // fling evicts frames the next leg needs straight back, so it pays constant
    // decode and eviction and gets nothing for it. At 1x the same shape is
    // starker still: 552 resident holds the worst frame 33ms, 240 holds it
    // 116ms, which is over the 100ms rule on its own. So full residency is not
    // an optimisation here, it is what makes the rule reachable, and the budget
    // rounds up to the whole tier when it is within reach.
    var whole = manifest.count * motionBytes;
    var motionShare = gb * 0.26 * GB;
    motionStore.cap = whole <= motionShare
      ? manifest.count
      : Math.min(Math.round(manifest.count * 0.55),
                 Math.max(90, Math.floor(motionShare / motionBytes)));
    hiresStore.cap = Math.min(manifest.count,
      Math.max(24, Math.floor(gb * 0.10 * GB / hiresBytes)));
    // Debug-only override, so the residency budget can be swept and measured
    // rather than argued about: /flight?debug=1&mcap=240
    var m = /[?&]mcap=(\d+)/.exec(location.search);
    if (DEBUG && m) motionStore.cap = Math.min(manifest.count, parseInt(m[1], 10));
    stats.motionCap = motionStore.cap;
  }

  /* ---- start -------------------------------------------------------------- */

  variant = pickVariant();
  Promise.all([
    fetch(manifestUrl()).then(function (r) { return r.json(); }),
    fetch(section.dataset.beats).then(function (r) { return r.json(); })
  ]).then(function (both) {
    manifest = both[0];
    beats = both[1];
    if (!manifest.count) throw new Error("empty manifest");
    if (!manifest.motionPattern) throw new Error("manifest has no motion tier");
    // The stress harness needs to know how many frames full residency means
    // before it starts flinging, so it waits for a warm page rather than
    // measuring the warm-up and calling it a fast-scroll failure.
    stats.frameCount = manifest.count;

    lores = new Array(manifest.count);
    motionStore.pattern = manifest.motionPattern;
    hiresStore.pattern = manifest.pattern;
    motionStore.blobs = new Array(manifest.count);
    hiresStore.blobs = new Array(manifest.count);
    residentCaps();
    buildPool();

    buildOverlay();
    computeTops();
    layout();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", function () { computeTops(); layout(); onScroll(); });
    window.addEventListener("orientationchange", function () { computeTops(); layout(); onScroll(); });

    // Order matters. Lores is a few hundred KB and gives every index something
    // to show immediately. The motion tier comes next because it is what makes
    // a fast scroll possible at all, and it is a third the bytes of the hi-res
    // tier. Hi-res bytes load last, in parallel, for the reader who stops.
    onScroll();
    requestAnimationFrame(tick);

    // Frames start downloading only once the page is interactive. The claim
    // field is the reason anyone is here, and 23MB of footage queued ahead of
    // it would make the one control that matters wait on the one thing that
    // does not. The poster is already painted by then (17KB, preloaded in the
    // head), so there is something on screen throughout.
    whenInteractive(function () {
      loadLores();
      motionStore.preloadBytes().then(function () {
        scheduleIdlePredecode();
        if (!slowNetwork) return hiresStore.preloadBytes();
      });
    });
  }).catch(function () {
    giveUp("manifest-failed");
  });
})();
