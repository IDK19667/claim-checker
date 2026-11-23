/* Evident: the desk front. The lamp-lit desk behind the stops, the bar, the rail
   and the place menu, Keep reading, the settle to a stop, and text mode.
   No framework, no build step. The engine is the approved sample's
   (sample3d/desk/EvidentDesk.fx4.html), moved here with three changes: the stops
   are the ones the page prints, the sheets and the card are drawn from the
   result on the page, and the three fixes E1 to E3 (keepDown, nextWords).
   app.js renders every word; this file lays the page out and moves it. It
   listens for two events from app.js: desk:result ({ data, fresh, replay, typing }) and
   desk:home. */
(function () {
  "use strict";
  var root = document.documentElement;
  if (!document.getElementById("track") || !document.getElementById("bar")) return;

  // ---- the stops the page prints, in order -----------------------------------------
  // A stop with nothing real to show is not printed (display.desk_stops), so the bar,
  // the rail and the menu count only what is there.
  var NEXT = {
    Answer: ["Next: the short answer"],
    Read: ["Next: what was read"],
    Weighed: ["Next: how it was weighed", "Next: the weighing"],
    Research: ["Next: what the research says", "Next: the research"],
    Open: ["Next: what is still open", "Next: still open"]
  };
  // each stop's camera framing and lamp aim in the sample's six (KEYS, AIMS, EXPO below)
  var CAM = { Claim: 0, Answer: 1, Read: 2, Weighed: 3, Research: 4, Open: 5 };
  var STOPS = [], sections = [], NS = 1, LAST = 0, WEIGH = -1;
  var checked = root.classList.contains("checked");
  var CHECK = { studies: [] };

  // ---- the test hook: the same names in 3D and in text mode ---------------------------
  var wake = function () {}, readyResolve, readyP = new Promise(function (r) { readyResolve = r; });
  var curStop = 0;
  var statsImpl = function () { return { mode: "loading", checked: checked, stop: curStop + 1, stops: NS, pixelRatio: 0, fullPixelRatio: Math.min(2, window.devicePixelRatio || 1), fog: false, looping: false, frames: 0, cardRect: null, sheets: 0 }; };
  var drawImpl = function () {}, gotoImpl = function (n) { return readyP.then(function () { return gotoImpl(n); }); }, sheetPointImpl = function () { return null; };
  window.__evident = {
    ready: function () { return readyP; },
    stats: function () { return statsImpl(); },
    drawOnce: function () { drawImpl(); },
    goto: function (n) { return gotoImpl(n); },
    sheetPoint: function (n) { return sheetPointImpl(n); }
  };

  // until the 3D card has settled, the answer shows as the text-mode card ("early"); then the desk takes over
  root.classList.add("early");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  var params = new URLSearchParams(location.search);
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function byId(id) { return document.getElementById(id); }
  function txt(n, sel) { var e = n.querySelector(sel); return e ? e.textContent.trim() : ""; }

  // ---- the result the page shows, for the desk: one sheet per study row ---------------
  // Read off the rows app.js or the server printed (number, group, design, title), with
  // the journal, year and PMID from the same record, so a sheet and its row never differ.
  function deskCheck(data) {
    var studies = (data && data.studies) || [], out = [];
    [].forEach.call(document.querySelectorAll("#rows .row"), function (li) {
      var i = +li.dataset.i, s = studies[i] || {}, ul = li.closest("ul.rows");
      out.push({ n: +li.dataset.n, group: ul ? ul.id.replace(/^grp-/, "") : "read", design: txt(li, ".design"),
        title: txt(li, ".ttl") || String(s.title || ""), journal: String(s.journal || ""), year: String(s.year || ""), pmid: String(s.pmid || "") });
    });
    out.sort(function (a, b) { return a.n - b.n; });
    return { studies: out };
  }
  function serverData() {
    var e = byId("server-result"); if (!e) return null;
    try { return JSON.parse(e.textContent); } catch (err) { return null; }
  }
  function inGroup(k) { return CHECK.studies.filter(function (s) { return s.group === k; }); }
  function study(n) { return CHECK.studies.filter(function (s) { return s.n === n; })[0]; }

  // ---- stop 4: the rows, their groups and their details (markup from app.js or Jinja) ----
  function grpBtn(key) { return document.querySelector('.grp-btn[aria-controls="grp-' + key + '"]'); }
  function rowBtn(n) { return document.querySelector("#row-" + n + " .row-head"); }
  function moreBtn(n) { return document.querySelector("#row-" + n + " .row-more:not(.study-go)"); }
  function setGroup(key, open) { var b = grpBtn(key); if (!b) return; b.setAttribute("aria-expanded", String(open)); byId(b.getAttribute("aria-controls")).hidden = !open; }
  function setMore(n, open) { var b = moreBtn(n); if (!b) return; b.setAttribute("aria-expanded", String(open)); b.textContent = open ? "Hide details" : "Details"; byId(b.getAttribute("aria-controls")).hidden = !open; }
  var track = byId("track");
  track.addEventListener("click", function (e) {
    var t = e.target;
    var g = t.closest(".grp-btn");
    if (g) { setGroup(g.getAttribute("aria-controls").replace(/^grp-/, ""), g.getAttribute("aria-expanded") !== "true"); layoutChanged(); return; }
    var h = t.closest(".row-head");
    if (h) { var n = +h.closest(".row").dataset.n; if (openN === n) clearStudy(); else selectStudy(n, { from: "row" }); return; }
    var m = t.closest(".row-more");
    if (m && !m.classList.contains("study-go")) { var k = +m.closest(".row").dataset.n; setMore(k, m.getAttribute("aria-expanded") !== "true"); layoutChanged(); return; }
    var a = t.closest("a.ref");
    if (a) { e.preventDefault(); selectStudy(+a.dataset.n, { from: "ref" }); return; }
    var go = t.closest("[data-go]");
    if (go) { jump(+go.dataset.go); return; }
    if (t.closest("#how-answer")) { var r = STOPS.map(function (s) { return s.name; }).indexOf("Read"); jump(r < 0 ? 1 : r); }
  });
  track.addEventListener("mouseover", function (e) { var h = e.target.closest && e.target.closest(".row-head"); var n = h ? +h.closest(".row").dataset.n : 0; if (n !== hoverN) { hoverN = n; wake(); } });
  track.addEventListener("mouseleave", function () { if (hoverN) { hoverN = 0; wake(); } });

  // ---- stop 5: the longer parts fold away on small screens until the reader opens or closes one ----
  var researchFolds = [];
  function readFolds() {
    researchFolds = [].map.call(document.querySelectorAll("#research details[data-fold]"), function (d) { return { d: d, auto: true, want: d.open }; });
  }
  document.addEventListener("toggle", function (e) {
    var d = e.target; if (!d || d.tagName !== "DETAILS" || !track.contains(d)) return;
    researchFolds.forEach(function (f) { if (f.d === d && d.open !== f.want) f.auto = false; });
    if (d.id === "qwrap") { if (qwrap.open !== qWant) qAuto = false; }
    layoutChanged();
  }, true);

  // ---- wayfinding: rail, place menu, Back and Next ------------------------
  var rail = byId("rail"), menu = byId("menu"), where = byId("where");
  var nextBtn = byId("next"), nextLabel = byId("next-label"), prevBtn = byId("prev");
  // the cue row on top of the bar: one wide button that does what Next does, in words that say to scroll (cueWords)
  var cueBtn = byId("cue"), cueLabel = byId("cue-label");
  var railBtns = [], menuBtns = [], ro = null;
  function readStops() {
    checked = root.classList.contains("checked");
    sections = [].slice.call(document.querySelectorAll(".stop[data-stop]")).filter(function (s) { return checked || s.id === "s1"; });
    STOPS = sections.map(function (s, i) {
      var nx = sections[i + 1], w = nx ? NEXT[nx.dataset.stop] : ["Back to the start"];
      return { name: s.dataset.stop, next: w[0], short: w[1], cam: CAM[s.dataset.stop] };
    });
    NS = STOPS.length; LAST = NS - 1;
    WEIGH = STOPS.map(function (s) { return s.name; }).indexOf("Weighed");
    if (curStop > LAST) curStop = LAST;
    rail.textContent = ""; menu.textContent = ""; railBtns = []; menuBtns = [];
    STOPS.forEach(function (s, i) {
      var b = el("button"); b.type = "button"; b.appendChild(el("span", "", s.name)); b.setAttribute("aria-label", (i + 1) + " of " + NS + ": " + s.name);
      b.addEventListener("click", function () { jump(i); }); rail.appendChild(b); railBtns.push(b);
      var m = el("button", "", (i + 1) + ". " + s.name); m.type = "button";
      m.addEventListener("click", function () { closeMenu(); jump(i); where.focus({ preventScroll: true }); }); menu.appendChild(m); menuBtns.push(m);
    });
    // the site's own pages, from every stop
    var links = el("p", "menu-links");
    [["How it works", "/method"], ["About", "/about"], ["Privacy", "/privacy"]].forEach(function (l) { var a = el("a", "", l[0]); a.href = l[1]; links.appendChild(a); });
    menu.appendChild(links);
    if (ro) { ro.disconnect(); sections.forEach(function (s) { ro.observe(s); }); ro.observe(track); }
    where.textContent = ""; wayKey = "";
  }
  function closeMenu() { menu.hidden = true; where.setAttribute("aria-expanded", "false"); }
  where.addEventListener("click", function () { var o = menu.hidden; menu.hidden = !o; where.setAttribute("aria-expanded", String(o)); if (o && menuBtns[curStop]) menuBtns[curStop].focus(); });
  // a second press of the same button within 400 ms is the same press (a double tap or double click moves one stop)
  // timed from when the finger, button or key went down (the input's own time stamp), not from when a busy page got to the click
  var lastPressBtn = null, lastPressAt = -1e9;
  [nextBtn, prevBtn].forEach(function (b) {
    b.addEventListener("pointerdown", function (e) { b._downAt = e.timeStamp; });
    b.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") b._downAt = e.timeStamp; });
    b.addEventListener("blur", function () { b._downAt = undefined; });
  });
  function onePress(b) {
    var now = performance.now(), t = b._downAt !== undefined && b._downAt <= now ? b._downAt : now;
    b._downAt = undefined;
    var same = b === lastPressBtn && t - lastPressAt < 400; lastPressBtn = b; lastPressAt = t; return !same;
  }
  // curStop is already the stop a glide is headed to, so Next and Back count from where the reader is going
  // on a stop whose words run on below the bar, Next keeps reading one screen down and Back reads one screen up
  nextBtn.addEventListener("click", function () { closeMenu(); if (!onePress(nextBtn)) return; if (keepDown()) return; jump(curStop >= LAST ? 0 : curStop + 1); });
  // the cue is a second, larger target for the same press: a pointer or a finger. It is out of the tab order and hidden
  // from screen readers, which have Next, first in the bar, with the same destination; a press never leaves focus on it
  if (cueBtn) {
    cueBtn.addEventListener("mousedown", function (e) { e.preventDefault(); });
    cueBtn.addEventListener("click", function () { nextBtn.click(); });
  }
  prevBtn.addEventListener("click", function () { closeMenu(); if (!onePress(prevBtn)) return; if (keepUp()) return; if (curStop > 0) jump(curStop - 1); });
  function jump(k) { clearStudy(); go(k); }
  // Skip to the answer (results only): the Answer stop, with focus on the verdict and no second scroll.
  // With scripts off the link's own #stamp does the same.
  var skip = byId("skip");
  if (skip) skip.addEventListener("click", function (e) { e.preventDefault(); closeMenu(); jump(1); byId("stamp").focus({ preventScroll: true }); });

  // ---- after a check the reader just ran: one glide to the answer, once ----------------
  // Never on a saved result (that opens on the answer), never against a reader who is
  // already moving: any wheel, touch, press or key before or during it ends it. A cut
  // under reduced motion. In a hidden tab it waits until the tab is seen.
  var answerGlide = false;
  function glideToAnswer() {
    var stopped = false, evs = ["wheel", "touchstart", "pointerdown", "keydown"];
    function stop() { stopped = true; if (answerGlide && tween) endTween(false); answerGlide = false; off(); }
    function off() { evs.forEach(function (ev) { removeEventListener(ev, stop, true); }); }
    evs.forEach(function (ev) { addEventListener(ev, stop, { capture: true, passive: true }); });
    function run() {
      if (stopped) return;
      answerGlide = true;
      go(1, function () { answerGlide = false; off(); });
      if (!tween) { answerGlide = false; off(); }
    }
    function seen() { if (document.visibilityState === "hidden") return; document.removeEventListener("visibilitychange", seen); requestAnimationFrame(run); }
    if (document.visibilityState === "hidden") document.addEventListener("visibilitychange", seen);
    else requestAnimationFrame(run);
  }

  // ---- a result arrives, or the reader goes back to the claim box -------------------------
  var onLoad = function () {}, onHome = function () {};
  function load(data, fresh, replay) {
    endTween(); clearStudy();
    readStops(); readFolds(); qAuto = true;
    CHECK = deskCheck(data);
    onLoad();
    layout();
    if (fresh) { window.scrollTo({ top: 0, behavior: "instant" }); prevY = progY = restY = 0; updateWay(); }
    else if (replay) adoptScroll(1000);
    else {
      // a saved result opens on the answer, with no glide
      window.scrollTo({ top: anchors[Math.min(1, LAST)], behavior: "instant" }); prevY = progY = restY = window.scrollY;
      landY = window.scrollY; nextWords(Math.min(1, LAST), window.scrollY); updateWay(); noteRest();
    }
    wake();
  }
  // A reader changing the words in the claim box is moving: the answer is laid out where they are, as a replay keeps
  // the place, with no jump to the answer and no glide.
  document.addEventListener("desk:result", function (e) {
    var detail = e.detail || {}, typing = !!detail.typing;
    load(detail.data, !!detail.fresh && !typing, !!detail.replay || typing);
    if (cueBtn) cueBtn.classList.remove("dip");
    if (detail.fresh && !typing) glideToAnswer();
    if (detail.fresh && !typing) armDip(); else if (dipOff) dipOff();
  });
  // during a check: at "found N", N blank sheets stand in the stack (3D only; text mode has no sheets)
  var onStage = function () {};
  document.addEventListener("desk:stage", function (e) { var d = e.detail || {}; if (d.stage === "found") onStage(Math.max(0, Math.min(40, +d.count || 0))); });
  document.addEventListener("desk:home", function () {
    endTween(); clearStudy(); closeMenu(); if (dipOff) dipOff();
    readStops(); onHome(); layout(); updateWay(); wake();
  });
  // ---- the page's shape: stop tops, where the camera holds, and the fold on small screens ----
  var anchors = [], holds = [], stripH = 32;
  function measure() {
    textCache = null;
    stripH = document.getElementById("strip").offsetHeight;
    root.style.setProperty("--strip", stripH + "px");
    var max = Math.max(0, document.documentElement.scrollHeight - innerHeight);
    for (var k = 0; k < NS; k++) {
      var s = sections[k], top = s.getBoundingClientRect().top + window.scrollY;
      anchors[k] = Math.min(max, Math.round(top));
      holds[k] = Math.min(max, Math.round(top + Math.max(0, s.offsetHeight - innerHeight)));
    }
  }
  // a short stop whose words cannot fit one screen keeps the strip under the bar clear: the first block that would
  // reach the bar starts just below the fold instead
  function fold() {
    var vh = innerHeight, bar = parseFloat(getComputedStyle(sections[0]).paddingBottom) || 84;
    sections.forEach(function (s0, k) {
      if (s0.classList.contains("long")) return;
      // home has no bar: stop 1 keeps its headline, claim box and button together on the first screen
      if (k === 0 && !checked) { [].forEach.call(s0.querySelectorAll("[data-pushed]"), function (c) { c.style.marginTop = ""; delete c.dataset.pushed; }); return; }
      var s = sections[k], inner = s.querySelector(".inner"); if (!inner) return;
      // the blocks as they are seen: a display: contents wrapper gives its children, and a reordered column is read top to bottom
      var kids = [];
      // (the site's claim box, its form and the error are wrappers: their own blocks are what is seen)
      (function add(list) { [].forEach.call(list, function (c) { if (getComputedStyle(c).display === "contents" || c.id === "ask" || c.id === "error" || c.id === "claim-form") add(c.children); else kids.push(c); }); })(inner.children);
      [].forEach.call(s.querySelectorAll("[data-pushed]"), function (c) { c.style.marginTop = ""; delete c.dataset.pushed; });
      // a block not shown has no place in the column, so it never takes the fold
      kids = kids.filter(function (c) { return c.getBoundingClientRect().height > 0; });
      kids.sort(function (a, b) { return a.getBoundingClientRect().top - b.getBoundingClientRect().top; });
      if (s.offsetHeight <= vh + 1 || getComputedStyle(s).display === "none") return;
      var top = s.getBoundingClientRect().top, limit = vh - bar + 6;
      for (var i = 0; i < kids.length; i++) {
        var r = kids[i].getBoundingClientRect(); if (!r.height) continue;
        // a tall block that begins with room on the screen is not moved whole: the fold falls between its own blocks
        if (r.bottom - top > limit && r.top - top < limit - 120 && kids[i].children.length > 1) {
          var inside = [].filter.call(kids[i].children, function (c) { return c.getBoundingClientRect().height > 0; });
          inside.sort(function (a, b) { return a.getBoundingClientRect().top - b.getBoundingClientRect().top; });
          if (inside.length > 1) { kids = kids.slice(0, i).concat(inside); i--; continue; }
        }
        if (r.bottom - top > limit) {
          // a label goes with the words it names
          if (i > 1 && /(^| )(sure-label|eyebrow)( |$)/.test(kids[i - 1].className)) { i--; r = kids[i].getBoundingClientRect(); }
          if (i === 0) break;
          var cur = parseFloat(getComputedStyle(kids[i]).marginTop) || 0;
          kids[i].style.marginTop = (cur + Math.max(0, vh + 2 - (r.top - top))) + "px"; kids[i].dataset.pushed = "1";
          break;
        }
      }
    });
  }
  var layoutHook = function () {};
  // the search used shows open when the last stop has room for it, folded on short screens
  var qwrap = document.getElementById("qwrap"), qAuto = true, qWant = true;
  // wide screens with room show everything open; only short or narrow screens fold (until the reader opens or closes one)
  function roomy() { return innerWidth > 760 && innerWidth / innerHeight > 0.85 && innerHeight >= 700; }
  function fitQuery() {
    var wide = roomy();
    researchFolds.forEach(function (f) { if (f.auto) f.d.open = f.want = wide; });
    if (!qAuto || !checked || !qwrap || getComputedStyle(sections[LAST]).display === "none") return;
    qwrap.open = qWant = true;
    // the search folds only where the last stop would otherwise run past one screen (short screens)
    if (sections[LAST].offsetHeight > innerHeight + 1) qwrap.open = qWant = false;
  }
  function layout() { layoutHook(); fitQuery(); fold(); measure(); }
  function layoutChanged() { measure(); wake(); }
  function rawProgress() {
    var y = window.scrollY;
    if (!checked) return 0;
    for (var k = 0; k < LAST; k++) {
      if (y < holds[k]) return k;
      if (y < anchors[k + 1]) return k + (y - holds[k]) / Math.max(1, anchors[k + 1] - holds[k]);
    }
    return LAST;
  }

  // ---- moving: one eased glide, or a cut under reduced motion ------------------
  var tween = null, slowGl = false;
  // navTo: the stop a glide started by a press is headed to. It is the current stop from the first frame of the glide;
  // anything that ends the glide early (a wheel, a touch, a scroll key, focus moving the page) hands the count back to the page
  var navTo = -1, goingTo = -1, landY = -1, readerAt = -1e9;
  // any key, pointer or wheel after a press: a page move that follows is the reader's own, not a reflow
  ["keydown", "pointerdown", "wheel", "touchstart"].forEach(function (ev) { addEventListener(ev, function () { readerAt = performance.now(); }, { capture: true, passive: true }); });
  // early: the glide was cut short before it arrived. It is kept as glideLeft, and the settle finishes it unless the
  // reader's own move since then ran NUDGE px or more. Any landing, a new move or a jump clears it, and the run with it
  var curGlide = null, glideLeft = null;
  function endTween(early) {
    glideLeft = early && curGlide ? curGlide : null; curGlide = null; runLen = 0;
    tween = null; navTo = -1;
  }
  function scrollToY(target, done) {
    // the target is read again on every step, so a page that reflows mid-glide (a late font, a fold) still lands on the true top
    var at = typeof target === "function" ? target : function () { return target; };
    var from = window.scrollY, to = at(), nav = goingTo;
    var settling = settleGlide; settleGlide = false;
    goingTo = -1; glideLeft = null;
    // a cut under reduced motion, and on a machine drawing under three frames a second, where a glide would only stutter
    if (reduce.matches || slowGl || Math.abs(to - from) < 2) { endTween(); window.scrollTo({ top: to, behavior: "instant" }); prevY = progY = window.scrollY; if (nav >= 0) { landY = progY; nextWords(nav, progY); } restY = progY; measure(); noteRest(); updateWay(); if (done) done(); return; }
    navTo = nav; curGlide = { at: at, nav: nav };
    if (nav >= 0) { landY = -1; nextWords(nav, Math.round(to)); updateWay(); }
    // a settle finishing the reader's own move is quicker than a press, so one arrow tap is at rest well inside two seconds
    var dur = settling ? Math.min(800, 350 + Math.abs(to - from) * 0.4) : Math.min(1800, 700 + Math.abs(to - from) * 0.35), t0 = performance.now(), setY = from, offAt = -1e9;
    // leaves at once and settles slowly: the page is seen to answer the press on the first frame
    tween = function (now) {
      // the reader moved the page (Tab bringing a control into view, a key, a wheel, a press): the glide gives way.
      // A page that only reflowed under the glide (a late font, a fold opening, scroll anchoring) jumps once and keeps
      // going to the true top; a page moved again within 250 ms (a scrollbar drag sends no press) also wins
      if (Math.abs(window.scrollY - setY) > 3) {
        if ((readerAt > t0 && now - readerAt < 1500) || now - offAt < 250) { endTween(true); updateWay(); return; }
        offAt = now;
      }
      var u = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - u, 3);
      to = at();
      window.scrollTo({ top: from + (to - from) * e, behavior: "instant" });
      // the page's own steps are not the reader's: a later scroll event measures the reader's move from here
      setY = prevY = progY = window.scrollY;
      if (u >= 1) { if (navTo >= 0) landY = window.scrollY; restY = window.scrollY; endTween(); measure(); noteRest(); updateWay(); if (done) done(); }
    };
    if (!scene3d) { var tick = function (now) { if (tween) { tween(now); requestAnimationFrame(tick); } }; requestAnimationFrame(tick); }
    else wake();
  }
  function go(k, done) {
    k = Math.max(0, Math.min(LAST, k));
    measure();
    goingTo = k;
    scrollToY(function () { return anchors[k]; }, done);
  }
  // app.js owns the checked state (html.checked); the desk only follows it
  function check() {
    if (checked || !root.classList.contains("checked")) return;
    checked = true;
    onCheck();
    layout();
  }
  var onCheck = function () {};
  // the reader taking over the scroll ends a glide: a wheel, a touch, or a key that scrolls (never Enter or Tab, which press and move focus)
  var SCROLL_KEYS = /^(ArrowUp|ArrowDown|PageUp|PageDown|Home|End| )$/;
  ["wheel", "touchstart", "keydown"].forEach(function (ev) { addEventListener(ev, function (e) {
    if (ev === "keydown" && !(SCROLL_KEYS.test(e.key) && !(e.target.closest && e.target.closest("button, a, textarea, input, summary")))) return;
    if (tween) endTween(true); lastInput = performance.now(); }, { passive: true }); });

  // ---- selecting a study: row and sheet stay in step -------------------------
  var openN = 0, hoverN = 0, lastInput = 0, scene3d = false;
  function rowTopY(n) { return byId("row-" + n).getBoundingClientRect().top + window.scrollY - stripH - 14; }
  function selectStudy(n, o) {
    o = o || {};
    var s = study(n); if (!s) return;
    if (!checked) check();
    if (openN && rowBtn(openN)) rowBtn(openN).setAttribute("aria-pressed", "false");
    // its group and its row open first, then the page moves to it
    var gb = grpBtn(s.group); if (gb && gb.getAttribute("aria-expanded") !== "true") setGroup(s.group, true);
    setMore(n, true);
    openN = n; if (rowBtn(n)) rowBtn(n).setAttribute("aria-pressed", "true");
    measure(); wake();
    if (o.from !== "row" && rowBtn(n)) {
      scrollToY(Math.max(0, Math.round(rowTopY(n))));
      rowBtn(n).focus({ preventScroll: true });
    }
  }
  function clearStudy() { if (!openN) return; var b = rowBtn(openN); if (b) b.setAttribute("aria-pressed", "false"); openN = 0; wake(); }
  addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var wasOpen = !menu.hidden;
    clearStudy(); closeMenu();
    if (wasOpen) where.focus({ preventScroll: true });   // Escape in the place menu: back to the button that opened it
  });

  // ---- wayfinding follows the page ----------------------------------------------------
  function updateWay() {
    var P = rawProgress(), now = performance.now();
    var cur = navTo >= 0 ? navTo : Math.max(0, Math.min(LAST, Math.round(P)));
    if (cur !== curStop || !where.textContent) {
      curStop = cur;
      railBtns.forEach(function (b, i) { if (i === cur) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current"); });
      menuBtns.forEach(function (b, i) { if (i === cur) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current"); });
      where.textContent = (innerWidth < 430 ? (cur + 1) + " of " + NS : (cur + 1) + " of " + NS + ": " + STOPS[cur].name);
      where.setAttribute("aria-label", "Stop " + (cur + 1) + " of " + NS + ": " + STOPS[cur].name + ". Choose a stop");
      prevBtn.setAttribute("aria-disabled", String(cur === 0));
      if (cur !== WEIGH && !tween) clearStudy();
    }
    // the Next label: at once for a press (named for where it lands), otherwise once the page is at rest, so it never flickers
    if (tween) { if (navTo >= 0 && curGlide) nextWords(navTo, Math.round(curGlide.at())); }
    else armWords();
  }
  // ---- Next never skips what the reader has not seen ------------------------------------
  // a stop whose last line of text is more than 2 px below the bar's top edge reads "Keep reading": a press moves down
  // inside it (keepDown) and never past the stop's last line; once that line is above the bar, Next names the next
  // stop. Back mirrors it, a screen at a time, up to the stop's top
  var barEl = document.getElementById("bar");
  var wayKey = "", wordsT = 0;
  // what a reader can see: laid out, and not inside a closed fold (Chrome still gives those a box)
  function shown(e) {
    if (e.checkVisibility && !e.checkVisibility({ visibilityProperty: true })) return false;
    var d = e.closest("details:not([open])"); if (!d) return true;
    var sm = e.closest("summary"); return !!sm && sm.parentElement === d;
  }
  // E5: the words laid over the 3D card (position fixed, placed by the camera each frame) are not on the page: they
  // move with every scroll and settle after the glide, so they are never measured; the camera keeps the card in view
  function overlay(e) { var c = e.closest("#vcard, #short-label"); return !!c && getComputedStyle(c).position === "fixed"; }
  // E4: the stop's lines of text, top and bottom on the page, read from its text itself (a box's padding is not a
  // line); a form field counts as one line. Kept until the page is next measured, and never longer than 250 ms
  var textCache = null;
  function textLines(k) {
    var now = performance.now();
    if (textCache && textCache.k === k && now - textCache.t < 250) return textCache.lines;
    var sec = sections[k], sy = window.scrollY, out = [], rg = document.createRange();
    var w = document.createTreeWalker(sec, NodeFilter.SHOW_TEXT), t;
    while ((t = w.nextNode())) {
      if (!t.data.trim()) continue;
      var el = t.parentElement; if (!el || overlay(el) || !shown(el)) continue;
      rg.selectNodeContents(t); var mine = [];
      [].forEach.call(rg.getClientRects(), function (q) {
        if (q.height < 2 || q.width < 1) return;
        var top = q.top + sy, bottom = q.bottom + sy, last = mine[mine.length - 1];
        if (last && top < last.bottom - 2) { last.top = Math.min(last.top, top); last.bottom = Math.max(last.bottom, bottom); } else mine.push({ top: top, bottom: bottom });
      });
      out.push.apply(out, mine);
    }
    [].forEach.call(sec.querySelectorAll("textarea, input:not([type=hidden])"), function (f) {
      var r = f.getBoundingClientRect(); if (r.height > 2 && shown(f) && !overlay(f)) out.push({ top: r.top + sy, bottom: r.bottom + sy });
    });
    textCache = { k: k, t: now, lines: out };
    return out;
  }
  function contentBottom(k) { var ls = textLines(k), b = -1e9; for (var i = 0; i < ls.length; i++) if (ls[i].bottom > b) b = ls[i].bottom; return b; }
  function barTop() { var r = barEl.getBoundingClientRect(); return r.height ? r.top : innerHeight; }
  function pageStep() { return Math.max(120, Math.round(barTop() - stripH - 64)); }
  // E2: any line of the stop still under the bar is more to read; the label never says Next while a line is hidden
  function moreBelow(k, y) { return checked && contentBottom(k) - (y + barTop()) > 2; }
  // E2/E4: the place where the stop's last line of text sits 2 px above the bar
  function readEnd(k) {
    var max = Math.max(0, root.scrollHeight - innerHeight), lim = k < LAST ? anchors[k + 1] - 1 : max;
    return Math.min(max, lim, Math.ceil(contentBottom(k) - barTop() + 2));
  }
  function baseY() { return tween && curGlide ? Math.round(curGlide.at()) : window.scrollY; }
  // E1: L, the last line of text wholly in view between the top strip and the bar, as the page is at y
  function lastLineIn(k, y, bt) {
    var best = null;
    textLines(k).forEach(function (l) { if (l.top >= y + stripH - 0.5 && l.bottom <= bt + 0.5 && (!best || l.bottom > best.bottom)) best = l; });
    return best;
  }
  // a press inside a long stop: E1 before E2. After the press L is still wholly in view below the strip (E1's limit,
  // L just under the strip); within that limit the press goes exactly far enough to bring the stop's last line of text
  // above the bar (E2), and no further. With no whole line in view (one block taller than the screen) it moves a screen
  function keepDown() {
    var k = curStop, y = baseY(); if (!moreBelow(k, y)) return false;
    var bt = y + barTop(), to = readEnd(k), L = lastLineIn(k, y, bt);
    var lim = L ? Math.floor(L.top - stripH - 2) : y + pageStep();
    if (lim - y < 2) lim = y + pageStep();
    to = Math.min(to, lim);
    if (to - y < 2) return false;
    goingTo = k; scrollToY(to); return true;
  }
  function keepUp() {
    var k = curStop, y = baseY(); if (!checked || y - anchors[k] <= 40) return false;
    goingTo = k; scrollToY(Math.max(anchors[k], y - pageStep())); return true;
  }
  // E3: in Weighed, the study rows not yet wholly seen with the page at y (a row in a closed group is not counted)
  function rowsLeft(k, y) {
    if (k !== WEIGH) return 0;
    var bt = y + barTop(), sy = window.scrollY, n = 0;
    [].forEach.call(sections[k].querySelectorAll(".row"), function (r) { var q = r.getBoundingClientRect(); if (q.height > 2 && shown(r) && q.bottom + sy > bt + 1) n++; });
    return n;
  }
  function keepWords(n, short) {
    if (!n) return "Keep reading";
    return "Keep reading: " + n + " more" + (short ? "" : n === 1 ? " study" : " studies");
  }
  // the cue row's words, from the same state as Next's: the next stop by its plain name, or what is left of this one
  // with the same count; null at the end of the last stop, where there is nothing below
  function cueWords(k, keep, left, short) {
    if (keep) return "Scroll down: " + (left ? left + " more " + (left === 1 ? "study" : "studies") : "more of this part");
    if (k >= LAST) return null;
    var nx = STOPS[k + 1].name;
    return "Scroll down: " + (nx === "Answer" ? "your answer" : ((short && STOPS[k].short) || STOPS[k].next).replace(/^Next: /, ""));
  }
  function nextWords(k, y) {
    var keep = moreBelow(k, y), up = checked && y - anchors[k] > 40, short = innerWidth <= 420, left = keep ? rowsLeft(k, y) : 0;
    var key = k + (keep ? "k" + left : "n") + (up ? "u" : "") + (short ? "s" : "") + NS;
    if (key === wayKey) return; wayKey = key;
    // while the cue row shows, Next is its quiet partner: the bare verb on screen, the full words as its name
    var cw = cueBtn && checked ? cueWords(k, keep, left, short) : null;
    if (keep) { nextLabel.textContent = cw ? "Keep reading" : keepWords(left, short); nextBtn.setAttribute("aria-label", left ? keepWords(left, false) : "Keep reading: more of " + STOPS[k].name + " below"); nextBtn.classList.remove("last"); }
    else {
      var nw = (short && STOPS[k].short) || STOPS[k].next;
      nextLabel.textContent = cw ? "Next" : nw;
      if (cw) nextBtn.setAttribute("aria-label", nw); else nextBtn.removeAttribute("aria-label");
      nextBtn.classList.toggle("last", k === LAST);
    }
    prevBtn.setAttribute("aria-label", up ? "Back: up one screen in " + STOPS[k].name : "Back one stop");
    if (cueBtn) {
      if (cw) cueLabel.textContent = cw;
      cueBtn.hidden = !cw; root.classList.toggle("has-cue", !!cw);
    }
  }
  // once, after an answer the reader just ran: if they have done nothing for 3 seconds since it landed, the cue's
  // chevron moves one time only, as two short dips of 600 ms. Any touch, wheel, press or key before then cancels it for good. Never under
  // reduced motion, never on a saved, shared or replayed answer
  var dipT = 0, dipOff = null;
  function armDip() {
    if (dipOff) dipOff();
    if (!cueBtn || reduce.matches) return;
    var evs = ["wheel", "touchstart", "pointerdown", "keydown"];
    function cancel() { clearTimeout(dipT); dipT = 0; off(); }
    function off() { evs.forEach(function (ev) { removeEventListener(ev, cancel, true); }); dipOff = null; }
    evs.forEach(function (ev) { addEventListener(ev, cancel, { capture: true, passive: true }); });
    dipOff = cancel;
    function fire() {
      if (tween || document.visibilityState === "hidden") { dipT = setTimeout(fire, 300); return; }
      off(); dipT = 0;
      if (reduce.matches || cueBtn.hidden || root.classList.contains("kbd")) return;
      cueBtn.classList.remove("dip"); void cueBtn.offsetWidth; cueBtn.classList.add("dip");
    }
    dipT = setTimeout(fire, 3000);
  }
  function armWords() { clearTimeout(wordsT); wordsT = setTimeout(restWords, 150); }
  function restWords() {
    wordsT = 0;
    if (tween || performance.now() - lastScrollAt < 140) { if (!tween) armWords(); return; }
    nextWords(curStop, window.scrollY); noteRest();
  }

  // ---- a page at rest keeps its stop when the page above it changes height ---------------
  // html has overflow-anchor none (the browser's anchoring moved the page under a glide), so the page does it itself:
  // at rest on a stop's top it stays on that top; inside a long stop the first line on screen keeps its place. A cut
  var restRef = null;
  function noteRest() {
    if (!checked || tween) { restRef = null; return; }
    var y = window.scrollY, k = -1, el = null, top = 0;
    for (var i = 0; i < NS; i++) if (Math.abs(y - anchors[i]) < 2) { k = i; break; }
    if (k < 0) { var c = sections[curStop].querySelectorAll(".row, p, h2, h3, summary"); for (var j = 0; j < c.length; j++) { var q = c[j].getBoundingClientRect(); if (q.height && q.top >= stripH && q.top < innerHeight && shown(c[j])) { el = c[j]; top = q.top; break; } } }
    restRef = { y: y, k: k, el: el, top: top };
  }
  function keepRest() {
    if (keyboardOpen()) return;
    // while the browser is still putting the reader back (R5) the place is the browser's, not this
    var r = restRef; if (!r || tween || touching || pressing || window.scrollY !== r.y || performance.now() < adoptUntil) return;
    var to = r.k >= 0 ? anchors[r.k] : (r.el && r.el.isConnected ? window.scrollY + r.el.getBoundingClientRect().top - r.top : r.y);
    to = Math.round(to); if (Math.abs(to - r.y) < 1) return;
    window.scrollTo({ top: to, behavior: "instant" });
    var ny = window.scrollY; prevY = progY = restY = ny; runLen = 0; if (landY === r.y) landY = ny; r.y = ny; updateWay();
  }

  // the reader's own move since the page last rested: its direction and how far it ran that way.
  // Moves the page made itself (a glide, a cut, the handover) are not counted
  var runDir = 0, runLen = 0, prevY = window.scrollY, progY = -1;
  // R5: a place the browser restores (Back, Forward, a reload) is where the reader was, at rest: taken as it is, never
  // counted as the reader's move and never settled away. For ms after the page asks, a scroll is the browser's
  var adoptUntil = 0;
  function adoptScroll(ms) {
    if (ms) adoptUntil = performance.now() + ms;
    var y = window.scrollY; prevY = progY = restY = landY = y; runLen = 0; glideLeft = null; clearTimeout(settleT);
    measure(); updateWay(); nextWords(curStop, y); noteRest();
  }
  addEventListener("scroll", function () {
    if (!tween && performance.now() < adoptUntil) { adoptScroll(0); lastInput = lastScrollAt = performance.now(); wake(); return; }
    var y = window.scrollY, dy = y - prevY; prevY = y;
    if (!tween && Math.abs(y - progY) >= 2 && dy) { var s = dy > 0 ? 1 : -1; if (s === runDir) runLen += Math.abs(dy); else { runDir = s; runLen = Math.abs(dy); } lastMoveAt = performance.now(); }
    lastInput = lastScrollAt = performance.now(); updateWay(); wake(); armSettle();
  }, { passive: true });
  var lastMoveAt = -1e9, lastScrollAt = -1e9, handT = 0;

  // ---- the page comes to rest on a stop, never halfway between two (with the desk) ----------------
  // 160 ms after the scroll stops, with no finger or button down and no glide running:
  // - a glide that was cut short (a touch, a reflow) is finished, unless the reader's own move since ran 12 px or more;
  // - a page left between two stops glides to a stop's top in the direction of the reader's last move once that move
  //   ran 12 px or more (one arrow press, a small deliberate wheel or swipe); a smaller nudge goes back to the stop
  //   the page rested on before it. Weighed and Research scroll freely inside themselves: a page inside a long stop,
  //   or just past its end while its last words clear the bar, stays.
  var settleT = 0, restY = 0, touching = false, pressing = false, NUDGE = 12;
  function armSettle() { clearTimeout(settleT); settleT = setTimeout(settleStop, 160); }
  addEventListener("touchstart", function () { touching = true; clearTimeout(settleT); }, { passive: true });
  ["touchend", "touchcancel"].forEach(function (ev) { addEventListener(ev, function () { touching = false; armSettle(); }, { passive: true }); });
  addEventListener("pointerdown", function (e) { if (e.pointerType === "mouse") pressing = true; }, { passive: true });
  addEventListener("pointerup", function () { if (pressing) { pressing = false; armSettle(); } }, { passive: true });
  // Tab moves the page to bring a control into view: that page stays where the browser put it, the reader is reading
  var tabbedAt = -1e9;
  addEventListener("keydown", function (e) { if (e.key === "Tab") tabbedAt = performance.now(); });
  var settleGlide = false;
  function settleStop() {
    settleT = 0;
    if (!checked || tween || touching || pressing || keyboardOpen()) return;
    if (performance.now() - tabbedAt < 1200) { glideLeft = null; restY = window.scrollY; runLen = 0; return; }
    if (glideLeft) {
      var g = glideLeft; glideLeft = null;
      var gd = g.at() > window.scrollY ? 1 : -1;
      // no move of its own, or a move the same way: the glide finishes; a move of 12 px or more the other way keeps the reader's place
      if ((runLen < NUDGE || runDir === gd) && !offScreenFocus(g.at() - window.scrollY)) { goingTo = g.nav; settleGlide = true; scrollToY(g.at, function () { restY = window.scrollY; }); return; }
    }
    if (!scene3d) return;
    // the page is where a press sent it: the settle never moves it off that stop
    if (landY >= 0 && Math.abs(window.scrollY - landY) < 2) { restY = window.scrollY; runLen = 0; return; }
    measure();
    var y = window.scrollY, k = -1;
    for (var i = 0; i < LAST; i++) if (y > holds[i] + 1 && y < anchors[i + 1] - 1) { k = i; break; }
    if (k >= 0 && sections[k].classList.contains("long") && y - holds[k] < innerHeight * 0.3) {
      // a reader resting just past a long stop's end keeps that place; a tiny nudge from it goes back to it
      if (runLen > 0 && runLen < NUDGE && Math.abs(y - restY) >= 2 && Math.abs(y - restY) < NUDGE) { runLen = 0; settleGlide = true; var back = restY; scrollToY(back, function () { restY = window.scrollY; }); return; }
      restY = y; runLen = 0; return;
    }
    if (k < 0) { restY = y; runLen = 0; return; }
    // onward in the direction the reader last moved, once that move ran 12 px or more (one arrow press, a small
    // deliberate wheel or swipe); a smaller nudge goes back to the stop the page rested on before it
    var up = holds[k], down = anchors[k + 1], to;
    if (runLen >= NUDGE) to = runDir > 0 ? down : up;
    else to = Math.abs(restY - up) <= Math.abs(restY - down) ? up : down;
    if (offScreenFocus(to - y)) { restY = y; runLen = 0; return; }
    runLen = 0; settleGlide = true;
    // the target is read live, so a page that grows above it mid-glide still lands on the true top or end
    var goDown = to === down;
    scrollToY(function () { return goDown ? liveTop(k + 1) : liveHold(k); }, function () { restY = window.scrollY; });
  }
  function liveTop(i) { return Math.min(Math.max(0, root.scrollHeight - innerHeight), Math.round(sections[i].getBoundingClientRect().top + window.scrollY)); }
  function liveHold(i) { return Math.min(Math.max(0, root.scrollHeight - innerHeight), liveTop(i) + Math.max(0, Math.round(sections[i].offsetHeight - innerHeight))); }
  // a control the reader moved to by keyboard is never carried off the screen
  function offScreenFocus(dy) {
    var a = document.activeElement;
    if (!(a && a !== document.body && document.getElementById("track").contains(a) && a.matches(":focus-visible"))) return false;
    var r = a.getBoundingClientRect();
    return r.top - dy < stripH || r.bottom - dy > innerHeight - 70;
  }
  // F1: the phone keyboard. Where the page was when a text field took focus is kept; when the window comes back to
  // that height at the same width (the keyboard closed) the page goes back to that place, as long as nothing was
  // checked or answered in between. The keyboard's own moves are the browser's, never counted as the reader's.
  // While the keyboard is open the bar at the foot is hidden (class "kbd"): it would sit on the box being typed in.
  // Only a touch screen has that keyboard: on a desktop a window dragged shorter is just a shorter window.
  var kbd = null;
  var coarse = window.matchMedia ? matchMedia("(pointer: coarse)") : { matches: false };
  function isField(a) { return !!a && (a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && !/^(button|submit|reset|checkbox|radio|range|file|image|color)$/.test(a.type))); }
  document.addEventListener("focusin", function (e) {
    if (!isField(e.target) || !coarse.matches) return;
    if (kbd && kbd.shrunk) return;
    kbd = { y: window.scrollY, w: innerWidth, h: innerHeight, checked: checked, shrunk: false };
  });
  document.addEventListener("focusout", function () { root.classList.remove("kbd"); });
  // while it is open the page does not settle or keep its rest by itself: the field being typed in owns the place
  function keyboardOpen() { return !!(kbd && kbd.shrunk); }
  // the field kept clear of the top strip and of the keyboard: centred in the room between them when it is not
  function showField() {
    var a = document.activeElement; if (!isField(a)) return;
    var r = (a.closest(".claim-box") || a).getBoundingClientRect(), room = innerHeight - stripH;
    if (r.top >= stripH + 8 && r.bottom <= innerHeight - 8) return;
    window.scrollTo({ top: Math.max(0, Math.round(window.scrollY + r.top - stripH - Math.max(8, (room - r.height) / 2))), behavior: "instant" });
  }
  function keyboardMoved() {
    if (!kbd) return false;
    if (!coarse.matches) { kbd = null; root.classList.remove("kbd"); return false; }
    if (innerWidth !== kbd.w) { kbd = null; root.classList.remove("kbd"); return false; }
    if (innerHeight < kbd.h - 1) { kbd.shrunk = true; root.classList.toggle("kbd", isField(document.activeElement)); showField(); adoptScroll(400); return true; }
    root.classList.remove("kbd");
    if (!kbd.shrunk) return false;
    var back = kbd; kbd = isField(document.activeElement) ? { y: back.y, w: back.w, h: back.h, checked: back.checked, shrunk: false } : null;
    if (back.checked !== checked) return false;
    measure();
    window.scrollTo({ top: back.y, behavior: "instant" }); adoptScroll(400);
    return true;
  }
  addEventListener("resize", function () { layout(); keyboardMoved(); updateWay(); wake(); });
  // any stop changing height (fonts arriving, a group opening) moves the stops below it: measure again
  if (window.ResizeObserver) { ro = new ResizeObserver(function () { measure(); keepRest(); updateWay(); }); sections.forEach(function (s) { ro.observe(s); }); ro.observe(track); }

  // ---- text mode -------------------------------------------------------------------
  var mode = "loading";
  function fallback() {
    if (mode !== "loading") return;
    mode = "text";
    root.classList.add("no-gl"); root.classList.remove("has-gl", "early");
    layout();
    window.__evd = { ready: true, gl: false, open: selectStudy };
    statsImpl = function () { updateWay(); return { mode: "text", checked: checked, stop: curStop + 1, stops: NS, pixelRatio: 0, fullPixelRatio: Math.min(2, window.devicePixelRatio || 1), fog: false, looping: !!tween, frames: 0, cardRect: null, sheets: 0 }; };
    gotoImpl = function (n) {
      n = Math.max(1, Math.min(NS, Math.round(n))); if (n >= 2) check(); endTween(); clearStudy(); layout();
      window.scrollTo({ top: anchors[n - 1], behavior: "instant" }); prevY = progY = window.scrollY; nextWords(n - 1, window.scrollY); noteRest();
      return new Promise(function (r) { requestAnimationFrame(function () { updateWay(); r(); }); });
    };
    updateWay();
    readyResolve();
  }
  // the page as it arrived: a home page, or a saved result app.js has already laid out (it opens on the answer)
  readStops(); readFolds();
  if (checked) CHECK = deskCheck(serverData() || {});
  layout(); updateWay();
  // a saved result opens on the answer; on a reload or Back/Forward into the page the browser puts the reader back
  // where they were, and that place is kept (R5)
  var navEntry = performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
  var restoring = !!navEntry && (navEntry.type === "reload" || navEntry.type === "back_forward") && history.scrollRestoration !== "manual";
  if (checked && restoring) adoptScroll(1500);
  else if (checked) {
    window.scrollTo({ top: anchors[Math.min(1, LAST)], behavior: "instant" }); prevY = progY = restY = landY = window.scrollY;
    nextWords(Math.min(1, LAST), window.scrollY); updateWay(); noteRest();
  }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { layout(); wake(); });

  // ---- the 3D library: loaded without blocking, one retry, then the text mode ----------
  function hasGL() {
    if (params.has("nogl")) return false;
    try { var c = document.createElement("canvas"); return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl"))); } catch (e) { return false; }
  }
  // a phone saving data or on a 2G connection gets text mode: the desk costs about 830 KB
  function lite() { var c = navigator.connection; return !!(c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ""))); }
  if (!hasGL() || lite()) { fallback(); return; }
  var woodImg = null;
  // the page hands over the stamped addresses (app.py static_stamp), so the two files can be kept a year
  var glEl = document.getElementById("gl");
  var LIB = (glEl && glEl.dataset.lib) || "/static/vendor/three-r128.min.js";
  var WOOD = (glEl && glEl.dataset.wood) || "/static/desk/wood-1024x2048.webp";
  function loadLib(tries) {
    var sc = document.createElement("script"), done = false;
    var timer = setTimeout(function () { fail(); }, 8000);
    function fail() { if (done) return; done = true; clearTimeout(timer); sc.onload = sc.onerror = null; if (tries > 0) loadLib(tries - 1); else fallback(); }
    sc.onload = function () { if (done) return; done = true; clearTimeout(timer); if (typeof THREE === "undefined") { fallback(); return; } waitFonts(); };
    sc.onerror = fail;
    sc.async = true; sc.src = LIB + (tries === 0 ? (LIB.indexOf("?") < 0 ? "?" : "&") + "retry=1" : ""); document.body.appendChild(sc);
  }
  // the painted sheets use the page's faces: wait for them a little, never long
  function waitFonts() {
    function fontsNow() {
      return Promise.race([Promise.all([
        document.fonts.load('600 120px "Bricolage Grotesque"'), document.fonts.load('600 40px "Instrument Sans"'),
        document.fonts.load('400 40px "Instrument Sans"'), document.fonts.load('500 20px "IBM Plex Mono"')
      ]), new Promise(function (r) { setTimeout(r, 2500); })]);
    }
    if (!document.fonts || !document.fonts.load) { start(); return; }
    fontsNow().then(start, start);
  }
  // at the load event, so the library and the wood never compete with the page and its fonts (the answer is already readable)
  function begin() { woodImg = new Image(); woodImg.decoding = "async"; woodImg.src = WOOD; loadLib(1); }
  if (document.readyState === "complete") begin(); else addEventListener("load", begin);

  function start() {
    var T = THREE;
    var canvas = document.getElementById("gl"), stage = document.getElementById("stage");
    var renderer;
    try {
      renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, powerPreference: "high-performance", preserveDrawingBuffer: params.has("shots") });
    } catch (e) { fallback(); return; }
    scene3d = true;
    // the page keeps its early layout (the text-mode card) until the first still frame: see handover()
    renderer.outputEncoding = T.sRGBEncoding;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    var EXPOSURE = 0.95;
    renderer.toneMappingExposure = EXPOSURE;
    renderer.physicallyCorrectLights = true;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = T.PCFSoftShadowMap;
    var maxAniso = renderer.capabilities.getMaxAnisotropy();
    var DPR_CAP = Math.min(2, window.devicePixelRatio || 1), dpr = DPR_CAP;
    renderer.shadowMap.autoUpdate = false;

    var scene = new T.Scene();
    scene.background = new T.Color(0x0a0c0b);
    var camera = new T.PerspectiveCamera(36, 1, 0.05, 20);

    // ---- painted textures -----------------------------------------------------
    function cnv(w, h) { var c = document.createElement("canvas"); c.width = w; c.height = h; return c; }
    function hash(x, y) { var s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); }
    var fibre = (function () {
      var c = cnv(256, 256), x = c.getContext("2d"), d = x.createImageData(256, 256);
      for (var i = 0; i < 256 * 256; i++) {
        var px = i % 256, py = (i / 256) | 0, v = hash(px, py);
        var streak = hash((px / 7) | 0, py) > 0.985 ? 0.5 : 0;
        var a = (v * 0.06 + streak * 0.06) * 255;
        d.data[i * 4] = 120; d.data[i * 4 + 1] = 105; d.data[i * 4 + 2] = 80; d.data[i * 4 + 3] = a;
      }
      x.putImageData(d, 0, 0); return c;
    })();
    function paperBase(x, w, h, tone, yellow) {
      x.fillStyle = tone; x.fillRect(0, 0, w, h);
      x.fillStyle = x.createPattern(fibre, "repeat"); x.fillRect(0, 0, w, h);
      var g = x.createRadialGradient(w * 0.5, h * 0.45, Math.min(w, h) * 0.35, w * 0.5, h * 0.5, Math.max(w, h) * 0.75);
      g.addColorStop(0, "rgba(160,125,70,0)"); g.addColorStop(1, "rgba(160,125,70," + yellow + ")");
      x.fillStyle = g; x.fillRect(0, 0, w, h);
    }
    function wrap(x, text, maxW) {
      var words = text.split(" "), lines = [], line = "";
      for (var i = 0; i < words.length; i++) {
        var test = line ? line + " " + words[i] : words[i];
        if (x.measureText(test).width > maxW && line) { lines.push(line); line = words[i]; } else line = test;
      }
      if (line) lines.push(line); return lines;
    }

    function tex(c, flip) {
      var t = new T.CanvasTexture(c); t.encoding = T.sRGBEncoding; t.anisotropy = maxAniso; t.flipY = flip !== false;
      t.minFilter = T.LinearMipmapLinearFilter; return t;
    }
    var SW = 1024, SH = 1448;
    function paintSheet(c, s, seed, scale) {
      var x = c.getContext("2d"), m = 92;
      x.setTransform(scale, 0, 0, scale, 0, 0);
      paperBase(x, SW, SH, "#f2eee4", 0.16 + seed * 0.08);
      x.fillStyle = "#5b605c"; x.font = '500 22px "IBM Plex Mono"'; x.textBaseline = "alphabetic";
      var j = s.journal.toUpperCase(); if (j.length > 44) j = j.slice(0, 43).trim() + ".";
      x.fillText(j, m, 128);
      x.textAlign = "right"; x.fillText(s.year, SW - m, 128); x.textAlign = "left";
      x.fillStyle = "#2b2f2d"; x.fillRect(m, 150, SW - 2 * m, 3);
      var size = 50, lines;
      do { x.font = '600 ' + size + 'px "Instrument Sans"'; lines = wrap(x, s.title, SW - 2 * m); size -= 2; } while (lines.length > 6 && size > 30);
      x.fillStyle = "#151816"; var y = 236, lh = size * 1.22;
      lines.forEach(function (l) { x.fillText(l, m, y); y += lh; });
      y += 14;
      x.font = '500 21px "IBM Plex Mono"'; x.fillStyle = "#5b605c";
      x.fillText((s.design || "Study").toUpperCase() + "   PMID " + s.pmid, m, y); y += 40;
      x.fillStyle = "#cfcabe"; x.fillRect(m, y, SW - 2 * m, 2); y += 52;
      x.font = '500 20px "IBM Plex Mono"'; x.fillStyle = "#6a6e6a"; x.fillText("ABSTRACT", m, y); y += 36;
      var col = (SW - 2 * m - 44) / 2, cx = [m, m + col + 44], ci = 0, top = y, r = seed * 997;
      while (ci < 2) {
        var para = 4 + Math.floor(hash(r, y) * 6);
        for (var k = 0; k < para && y < SH - 130; k++) {
          var wl = (k === para - 1) ? 0.3 + hash(r + k, y) * 0.5 : 0.9 + hash(y, r + k) * 0.1;
          x.fillStyle = "rgba(38,40,38," + (0.22 + hash(k, y) * 0.08) + ")";
          var px = cx[ci], rest = col * wl;
          while (rest > 8) { var w = Math.min(rest, 22 + hash(px, y + r) * 70); x.fillRect(px, y - 9, w - 7, 10); px += w; rest -= w; }
          y += 24;
        }
        y += 18;
        if (y >= SH - 130) { ci++; y = top; }
      }
      if (s.n) { x.font = '500 18px "IBM Plex Mono"'; x.fillStyle = "#7a7e7a"; x.textAlign = "center"; x.fillText(String(s.n), SW / 2, SH - 62); x.textAlign = "left"; }
    }
    var CW = 1600, CH = 960, VERDICT_W = 1420;
    function cardWords() { return { claim: txt(document, "#claim-echo"), verdict: txt(document, "#stamp"), tldr: txt(document, "#tldr") }; }
    var CLAIM_FITS = [{ size: 54, lines: 1 }, { size: 46, lines: 2 }, { size: 40, lines: 2 }, { size: 34, lines: 3 }];
    // count(size) is how many lines the claim takes at that size; the first fit that holds it, else the last, clamped
    function fitClaim(count) { for (var i = 0; i < CLAIM_FITS.length; i++) { var f = CLAIM_FITS[i], n = count(f.size); if (n <= f.lines) return { size: f.size, lines: n, clamp: false }; } return { size: 34, lines: 3, clamp: true }; }
    function claimBase(f) { return Math.min(252, 300 - (f.lines - 1) * f.size * 1.2); }
    var TLDR_FITS = [54, 46, 40];
    function fitTldr(count) {
      for (var i = 0; i < TLDR_FITS.length; i++) { var sz = TLDR_FITS[i], lh = Math.round(sz * 1.3), max = Math.floor((CH - 40 - 618) / lh) + 1, n = count(sz); if (n <= max) return { size: sz, lh: lh, lines: n }; }
      return { size: 40, lh: 52, lines: Math.floor((CH - 40 - 618) / 52) + 1 };
    }
    // at most n lines; a cut last line ends in an ellipsis
    function clampLines(x, lines, n, maxW) {
      if (lines.length <= n) return lines;
      var out = lines.slice(0, n), last = out[n - 1];
      while (last.indexOf(" ") > 0 && x.measureText(last + "\u2026").width > maxW) last = last.slice(0, last.lastIndexOf(" "));
      out[n - 1] = last.replace(/[\s,.;:]+$/, "") + "\u2026"; return out;
    }
    function paintCard(c, withVerdict, scale) {
      var x = c.getContext("2d");
      x.setTransform(scale || 1, 0, 0, scale || 1, 0, 0);
      paperBase(x, CW, CH, "#efe6d1", 0.14);
      x.fillStyle = "rgba(44,88,72,0.42)"; x.fillRect(0, 150, CW, 3);
      x.fillStyle = "rgba(70,96,110,0.16)";
      for (var y = 150 + 66; y < CH - 20; y += 66) x.fillRect(0, y, CW, 2);
      if (!withVerdict) return;
      var m = 96, words = cardWords();
      x.fillStyle = "#4b514d"; x.font = '500 32px "IBM Plex Mono"'; x.fillText("THE CLAIM YOU CHECKED", m, 108);
      // the claim: one line at 54, two at 46 or 40, three at 34 and then an ellipsis (layoutCard sets the HTML card the same way)
      var cf = fitClaim(function (size) { x.font = '400 ' + size + 'px "Instrument Sans"'; return wrap(x, "“" + words.claim + "”", CW - 2 * m).length; });
      x.fillStyle = "#262a28"; x.font = '400 ' + cf.size + 'px "Instrument Sans"';
      var cl = clampLines(x, wrap(x, "“" + words.claim + "”", CW - 2 * m), cf.lines, CW - 2 * m), cy = claimBase(cf);
      cl.forEach(function (l) { x.fillText(l, m, cy); cy += cf.size * 1.2; });
      // the verdict: 200 units, smaller when the words are wider than the card
      x.fillStyle = "#0f1110"; x.font = '700 200px "Bricolage Grotesque"';
      var vw = x.measureText(words.verdict).width, vs = vw > VERDICT_W ? Math.floor(200 * VERDICT_W / vw) : 200;
      x.font = '700 ' + vs + 'px "Bricolage Grotesque"'; x.fillText(words.verdict, m - 10, 490);
      var tf = fitTldr(function (size) { x.font = '400 ' + size + 'px "Instrument Sans"'; return wrap(x, words.tldr, CW - 2 * m).length; });
      x.fillStyle = "#222624"; x.font = '400 ' + tf.size + 'px "Instrument Sans"';
      var ls = clampLines(x, wrap(x, words.tldr, CW - 2 * m), tf.lines, CW - 2 * m), yy = 618;
      ls.forEach(function (l) { x.fillText(l, m, yy); yy += tf.lh; });
    }
    function softRect(w, h, blur, alpha) {
      var c = cnv(w, h), x = c.getContext("2d");
      x.filter = "blur(" + blur + "px)"; x.fillStyle = "rgba(0,0,0," + alpha + ")";
      x.fillRect(blur * 2.2, blur * 2.2, w - blur * 4.4, h - blur * 4.4);
      return new T.CanvasTexture(c);
    }
    function blob(alpha) {
      var c = cnv(256, 256), x = c.getContext("2d"), g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
      g.addColorStop(0, "rgba(0,0,0," + alpha + ")"); g.addColorStop(0.5, "rgba(0,0,0," + alpha * 0.5 + ")"); g.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = g; x.fillRect(0, 0, 256, 256); return new T.CanvasTexture(c);
    }

    // ---- the room ---------------------------------------------------------------
    var DESK_W = 1.9, DESK_D = 0.92, DESK_T = 0.04, WOOD_TILE = 1.0, woodState = "loading";
    var woodMat = new T.MeshPhysicalMaterial({ color: 0x5b4636, roughness: 0.5, clearcoat: 0.35, clearcoatRoughness: 0.32 });
    (function () {
      var fb = cnv(512, 512), x = fb.getContext("2d"); x.fillStyle = "#4a3426"; x.fillRect(0, 0, 512, 512);
      for (var i = 0; i < 260; i++) { x.fillStyle = "rgba(" + (hash(i, 1) > 0.5 ? "20,12,8" : "110,80,58") + "," + (0.05 + hash(i, 2) * 0.12) + ")"; x.fillRect(0, hash(i, 3) * 512, 512, 1 + hash(i, 4) * 3); }
      var fallbackTex = tex(fb); fallbackTex.wrapS = fallbackTex.wrapT = T.RepeatWrapping; fallbackTex.repeat.set(1, 1);
      woodMat.map = fallbackTex; woodMat.color.set(0xb09a8a); woodMat.needsUpdate = true;
      // the photo (1024 wide by 2048 tall, WebP, 190 KB: the grain is in the height, so only the width was halved), grain running along the image's width; the desk's UVs are in metres (see deskGeo), so one tile is WOOD_TILE metres square on every face
      // the photo has been loading since the page began (woodImg), alongside the library
      function woodOk() {
        var t = new T.Texture(woodImg); t.needsUpdate = true;
        t.encoding = T.sRGBEncoding; t.anisotropy = maxAniso; t.wrapS = t.wrapT = T.RepeatWrapping;
        t.minFilter = T.LinearMipmapLinearFilter; t.repeat.set(1 / WOOD_TILE, 1 / WOOD_TILE);
        woodMat.map = t; woodMat.color.set(0x8a7466); woodState = "ok"; wake();
      }
      function woodBad() { woodState = "failed"; woodMat.map = null; woodMat.color.set(0x5b4130); woodMat.needsUpdate = true; wake(); }
      if (woodImg.complete) { if (woodImg.naturalWidth) woodOk(); else woodBad(); }
      else { woodImg.addEventListener("load", woodOk); woodImg.addEventListener("error", woodBad); }
    })();
    // fine grain, closer than the photo can hold: a tileable 256 px pore and fibre map, sampled at two unrelated repeats
    // and multiplied in at mean 1, so it adds texture without changing the wood's colour; the mipmaps average it away at a distance
    var detailTex = (function () {
      var N = 256, c = cnv(N, N), x = c.getContext("2d"), d = x.createImageData(N, N), f = new Float32Array(N * N);
      function layer(cx, cy, amp, seed) {
        // value noise on a wrapped cx by cy lattice, so the tile joins seamlessly
        for (var py = 0; py < N; py++) for (var px = 0; px < N; px++) {
          var gx = px / N * cx, gy = py / N * cy, ix = Math.floor(gx), iy = Math.floor(gy), fx = gx - ix, fy = gy - iy;
          var sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
          var a = hash(ix % cx + seed, iy % cy), b = hash((ix + 1) % cx + seed, iy % cy), cc = hash(ix % cx + seed, (iy + 1) % cy), dd = hash((ix + 1) % cx + seed, (iy + 1) % cy);
          f[py * N + px] += amp * ((a + (b - a) * sx) * (1 - sy) + (cc + (dd - cc) * sx) * sy - 0.5);
        }
      }
      layer(2, 32, 0.5, 11); layer(4, 64, 0.35, 23); layer(32, 128, 0.18, 37);
      var mean = 0; for (var i = 0; i < N * N; i++) mean += f[i]; mean /= N * N;
      for (i = 0; i < N * N; i++) { var v = 128 + (f[i] - mean) * 150; v = v < 0 ? 0 : v > 255 ? 255 : v; d.data[i * 4] = d.data[i * 4 + 1] = d.data[i * 4 + 2] = v; d.data[i * 4 + 3] = 255; }
      x.putImageData(d, 0, 0);
      var t = new T.CanvasTexture(c); t.wrapS = t.wrapT = T.RepeatWrapping; t.anisotropy = maxAniso; t.minFilter = T.LinearMipmapLinearFilter; return t;
    })();
    woodMat.onBeforeCompile = function (sh) {
      sh.uniforms.detailMap = { value: detailTex };
      sh.fragmentShader = "uniform sampler2D detailMap;\n" + sh.fragmentShader.replace("#include <map_fragment>",
        "#include <map_fragment>\n#ifdef USE_MAP\n  float dA = texture2D(detailMap, vUv * vec2(47.4, 47.4)).r, dB = texture2D(detailMap, vUv * vec2(122.6, 117.8) + vec2(0.37, 0.61)).r;\n  diffuseColor.rgb *= (0.75 + 0.5 * dA) * (0.8 + 0.4 * dB);\n#endif");
    };
    // UVs in metres, so the grain keeps its true proportions on the top and on the edges, and always runs along the desk's long side
    function deskGeo() {
      var g = new T.BoxGeometry(DESK_W, DESK_T, DESK_D), pos = g.attributes.position, nor = g.attributes.normal, uv = g.attributes.uv;
      for (var i = 0; i < pos.count; i++) {
        var x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i), nx = Math.abs(nor.getX(i)), ny = Math.abs(nor.getY(i));
        if (ny > 0.5) uv.setXY(i, x, z); else if (nx > 0.5) uv.setXY(i, z, y); else uv.setXY(i, x, y);
      }
      uv.needsUpdate = true; return g;
    }
    var desk = new T.Mesh(deskGeo(), woodMat);
    desk.position.y = -DESK_T / 2; desk.receiveShadow = true; scene.add(desk);
    var darkWood = new T.MeshStandardMaterial({ color: 0x1a130f, roughness: 0.7 });
    [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(function (p) {
      var leg = new T.Mesh(new T.BoxGeometry(0.05, 0.72, 0.05), darkWood);
      leg.position.set(p[0] * (DESK_W / 2 - 0.06), -0.4, p[1] * (DESK_D / 2 - 0.06)); scene.add(leg);
    });
    var apron = new T.Mesh(new T.BoxGeometry(DESK_W - 0.1, 0.08, DESK_D - 0.1), darkWood); apron.position.y = -0.08; scene.add(apron);
    var wall = new T.Mesh(new T.PlaneGeometry(8, 4), new T.MeshStandardMaterial({ color: 0x3a423d, roughness: 0.96 }));
    wall.position.set(0, 0.9, -0.52); wall.receiveShadow = true; scene.add(wall);
    var floor = new T.Mesh(new T.PlaneGeometry(10, 10), new T.MeshStandardMaterial({ color: 0x141210, roughness: 0.9 }));
    floor.rotation.x = -Math.PI / 2; floor.position.y = -0.76; scene.add(floor);

    // ---- the lamp ---------------------------------------------------------------
    var metal = new T.MeshStandardMaterial({ color: 0x141515, roughness: 0.38, metalness: 0.55 });
    var chrome = new T.MeshStandardMaterial({ color: 0x8e918f, roughness: 0.28, metalness: 0.9 });
    var BASE = new T.Vector3(-0.64, 0, -0.3), ELBOW = new T.Vector3(-0.66, 0.44, -0.36), HEAD = new T.Vector3(-0.27, 0.6, 0.0);
    var lampBase = new T.Mesh(new T.CylinderGeometry(0.085, 0.095, 0.022, 64), metal);
    lampBase.position.copy(BASE).add(new T.Vector3(0, 0.011, 0)); lampBase.castShadow = true; lampBase.receiveShadow = true; scene.add(lampBase);
    function rod(a, b, r, mat) {
      var d = new T.Vector3().subVectors(b, a), m = new T.Mesh(new T.CylinderGeometry(r, r, d.length(), 16), mat);
      m.position.copy(a).add(b).multiplyScalar(0.5); m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), d.normalize());
      m.castShadow = true; scene.add(m); return m;
    }
    rod(new T.Vector3(BASE.x, 0.02, BASE.z), ELBOW, 0.0065, chrome);
    rod(ELBOW, HEAD, 0.0065, chrome);
    [ELBOW, new T.Vector3(BASE.x, 0.03, BASE.z)].forEach(function (p) { var j = new T.Mesh(new T.SphereGeometry(0.013, 20, 12), metal); j.position.copy(p); j.castShadow = true; scene.add(j); });
    var head = new T.Group(); head.position.copy(HEAD); scene.add(head);
    var shadeR = 0.1;
    var shadeOut = new T.Mesh(new T.SphereGeometry(shadeR, 64, 24, 0, Math.PI * 2, 0, Math.PI * 0.5), metal);
    shadeOut.scale.y = 0.9; shadeOut.castShadow = true; head.add(shadeOut);
    // inside the shade: a warm reflector, brightest near the bulb, falling off to the rim
    var reflMap = (function () {
      var c = cnv(64, 256), x = c.getContext("2d"), g = x.createLinearGradient(0, 0, 0, 256);
      g.addColorStop(0, "#ffffff"); g.addColorStop(0.45, "#c9b79a"); g.addColorStop(1, "#4a4030");
      x.fillStyle = g; x.fillRect(0, 0, 64, 256); var t = new T.CanvasTexture(c); t.encoding = T.sRGBEncoding; return t;
    })();
    var shadeInMat = new T.MeshStandardMaterial({ color: 0xd9d2c4, roughness: 0.55, side: T.BackSide, emissive: 0xffd2a0, emissiveMap: reflMap, emissiveIntensity: 1.1 });
    var shadeIn = new T.Mesh(new T.SphereGeometry(shadeR * 0.985, 64, 24, 0, Math.PI * 2, 0, Math.PI * 0.5), shadeInMat);
    shadeIn.scale.y = 0.9; head.add(shadeIn);
    var rim = new T.Mesh(new T.TorusGeometry(shadeR, 0.0035, 8, 96), metal); rim.rotation.x = Math.PI / 2; head.add(rim);
    var knuckle = new T.Mesh(new T.SphereGeometry(0.016, 20, 12), metal); knuckle.position.y = shadeR * 0.9; head.add(knuckle);
    var socket = new T.Mesh(new T.CylinderGeometry(0.014, 0.016, 0.03, 24), new T.MeshStandardMaterial({ color: 0x2a2826, roughness: 0.5, metalness: 0.4 }));
    socket.position.y = 0.068; head.add(socket);
    var bulbMat = new T.MeshBasicMaterial({ color: 0xfff3df });
    var bulb = new T.Mesh(new T.SphereGeometry(0.027, 32, 20), bulbMat); bulb.scale.y = 1.15; bulb.position.y = 0.032; head.add(bulb);

    var LEAK_I = 0.3;
    var spot = new T.SpotLight(0xffcf98, 2.7, 0, 0.74, 1, 2);
    scene.add(spot); scene.add(spot.target);
    spot.castShadow = true; spot.shadow.mapSize.set(2048, 2048); spot.shadow.bias = -0.00025; spot.shadow.normalBias = 0.012;
    spot.shadow.camera.near = 0.05; spot.shadow.camera.far = 2.6; spot.shadow.focus = 1;
    var leak = new T.PointLight(0xffc68c, LEAK_I, 0, 2); leak.position.copy(HEAD).add(new T.Vector3(0.03, 0.16, -0.1)); scene.add(leak);
    var hemi = new T.HemisphereLight(0x9fb3ad, 0x1a120c, 0.12); scene.add(hemi);
    var cool = new T.DirectionalLight(0x8fa6b4, 0.06); cool.position.set(2, 1.2, 1.5); scene.add(cool);
    var LAMP_I = spot.intensity, LAMP_ANGLE = spot.angle;
    var key = new T.SpotLight(0xfff1de, 0, 0, 0.32, 1, 2);
    key.position.set(0.12, 0.42, 0.62); key.target.position.set(-0.1, 0.047, 0.13); scene.add(key); scene.add(key.target);
    var aimNow = new T.Vector3(), aimDir = new T.Vector3(), DOWN = new T.Vector3(0, -1, 0);
    function aimLamp(p) {
      aimNow.copy(p); aimDir.subVectors(p, HEAD).normalize();
      head.quaternion.setFromUnitVectors(DOWN, aimDir);
      spot.position.copy(HEAD).addScaledVector(aimDir, 0.04);
      spot.target.position.copy(p);
    }

    // ---- contact shadows ------------------------------------------------------------
    var sheetShadowTex = softRect(256, 340, 14, 0.75), cardShadowTex = softRect(320, 200, 22, 0.8), blobTex = blob(0.85);
    function shadowPlane(tx, w, h, opacity) {
      var m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ map: tx, transparent: true, depthWrite: false, opacity: opacity, color: 0x000000 }));
      m.rotation.x = -Math.PI / 2; m.renderOrder = 1; scene.add(m); return m;
    }
    var baseAO = shadowPlane(blobTex, 0.3, 0.3, 0.9); baseAO.position.set(BASE.x, 0.0004, BASE.z);

    // ---- two closed books in the dim, for depth --------------------------------
    (function () {
      var cloth = new T.MeshStandardMaterial({ color: 0x2a2d29, roughness: 0.85 });
      var cloth2 = new T.MeshStandardMaterial({ color: 0x3a2f26, roughness: 0.8 });
      var pages = new T.MeshStandardMaterial({ color: 0xcfc6b2, roughness: 0.95 });
      function book(w, t, d, mat, x, y, z, yaw) {
        var g = new T.Group();
        var cover = new T.Mesh(new T.BoxGeometry(w, t, d), mat); cover.castShadow = cover.receiveShadow = true; g.add(cover);
        var block = new T.Mesh(new T.BoxGeometry(w - 0.006, t * 0.82, d - 0.012), pages); block.position.x = 0.004; g.add(block);
        g.position.set(x, y + t / 2, z); g.rotation.y = yaw; scene.add(g);
      }
      book(0.17, 0.032, 0.24, cloth, 0.74, 0, -0.32, 0.12);
      book(0.15, 0.024, 0.21, cloth2, 0.75, 0.032, -0.325, -0.08);
      var ao = new T.Mesh(new T.PlaneGeometry(0.26, 0.32), new T.MeshBasicMaterial({ map: blob(0.8), transparent: true, depthWrite: false, color: 0x000000, opacity: 0.7 }));
      ao.rotation.x = -Math.PI / 2; ao.rotation.z = 0.12; ao.position.set(0.74, 0.0003, -0.32); scene.add(ao);
    })();

    // ---- the pencil --------------------------------------------------------------
    var pencil = new T.Group();
    var pLen = 0.165, pR = 0.0037;
    var paint = new T.MeshStandardMaterial({ color: 0x1d3a2e, roughness: 0.42, metalness: 0.05 });
    var body = new T.Mesh(new T.CylinderGeometry(pR, pR, pLen, 6), paint); body.castShadow = true; body.receiveShadow = true; pencil.add(body);
    var cedar = new T.Mesh(new T.CylinderGeometry(0.0011, pR * 0.93, 0.02, 6), new T.MeshStandardMaterial({ color: 0xc9a27a, roughness: 0.8 }));
    cedar.position.y = pLen / 2 + 0.01; cedar.castShadow = true; pencil.add(cedar);
    var lead = new T.Mesh(new T.ConeGeometry(0.0011, 0.004, 8), new T.MeshStandardMaterial({ color: 0x1b1b1b, roughness: 0.35, metalness: 0.4 }));
    lead.position.y = pLen / 2 + 0.022; pencil.add(lead);
    var ferrule = new T.Mesh(new T.CylinderGeometry(pR * 1.04, pR * 1.04, 0.012, 16), chrome); ferrule.position.y = -pLen / 2 - 0.006; pencil.add(ferrule);
    var eraser = new T.Mesh(new T.CylinderGeometry(pR * 0.98, pR * 0.98, 0.008, 16), new T.MeshStandardMaterial({ color: 0x3b3532, roughness: 0.9 })); eraser.position.y = -pLen / 2 - 0.016; pencil.add(eraser);
    pencil.rotation.set(0, 0.42, Math.PI / 2); body.rotation.y = Math.PI / 6;
    pencil.position.set(0.38, pR * 0.9, 0.34); scene.add(pencil);
    var pencilAO = shadowPlane(blobTex, 0.2, 0.03, 0.55); pencilAO.position.set(0.38, 0.0003, 0.345); pencilAO.rotation.z = 0.42;

    // ---- sheets ------------------------------------------------------------------
    var PW = 0.21, PH = 0.297, PT = 0.0021;
    // where the sheets fan out under the lamp: relied on in two rows in the brightest part, read at the light's edge, off topic face down beyond
    var FAN = { ax: -0.54, dx: 0.098, az: -0.02, bx: 0.05, bz: 0.17, rx: 0.2, rz: -0.12, ox: 0.5, oz: 0.12 };
    var edgeMat = new T.MeshStandardMaterial({ color: 0xe8e2d5, roughness: 0.95, emissive: 0x2a2620, emissiveIntensity: 1 });
    var backMat = (function () { var c = cnv(256, 362), x = c.getContext("2d"); paperBase(x, 256, 362, "#ece7dc", 0.2); return new T.MeshStandardMaterial({ map: tex(c), roughness: 0.95 }); })();
    function sheetGeo(seed, i) {
      var g = new T.BoxGeometry(PW, PT, PH, 14, 1, 20), pos = g.attributes.position;
      var cA = 0.0018 + seed * 0.0022, corner = (i % 2 ? 1 : -1);
      for (var v = 0; v < pos.count; v++) {
        var px = pos.getX(v) / (PW / 2), pz = pos.getZ(v) / (PH / 2);
        pos.setY(v, pos.getY(v) + 0.0005 * px * px + cA * Math.pow(Math.max(0, px * corner * 0.5 + pz * 0.5), 4));
      }
      g.computeVertexNormals(); return g;
    }
    var sheets = [], STACK = new T.Vector3(-0.46, 0, 0.0), GAP = 0.0004, sheetGen = 0;
    // one blank paper face for every sheet until its own face is painted, after the first frame
    var blankSheet = (function () { var c = cnv(128, 181), x = c.getContext("2d"); paperBase(x, 128, 181, "#f2eee4", 0.2); return tex(c); })();
    // relied on: rows of up to six in the brightest part, centred under the lamp, split evenly over the rows;
    // read (and background only, after them): rows of four at the light's edge; off topic: face down beyond
    var FAN_CX = FAN.ax + 2.5 * FAN.dx, SIDE_YAW = [0.2, 0.3, 0.14, 0.26];
    function dropSheets() {
      if (hiSlot) dropHi();
      sheets.forEach(function (o) {
        scene.remove(o.mesh); scene.remove(o.sh); o.mesh.geometry.dispose();
        var f = o.mesh.userData.face; if (f.map && f.map !== blankSheet) f.map.dispose(); f.dispose();
        o.sh.geometry.dispose(); o.sh.material.dispose();
      });
      sheets = []; casters = null; sheetGen++;
    }
    function buildSheets() {
      dropSheets();
      var relied = inGroup("relied"), side = inGroup("read").concat(inGroup("back")), offT = inGroup("off");
      var R = relied.length, rows = Math.max(1, Math.ceil(R / 6)), per = Math.ceil(R / rows);
      CHECK.studies.forEach(function (s, i) {
        var seed = hash(i * 3.1, 7.7);
        var fmap = blankSheet; var face = new T.MeshStandardMaterial({ map: fmap, roughness: 0.88, metalness: 0, emissive: 0xffffff, emissiveMap: fmap, emissiveIntensity: 0 });
        var mesh = new T.Mesh(sheetGeo(seed, i), [edgeMat, edgeMat, face, backMat, edgeMat, edgeMat]);
        mesh.castShadow = true; mesh.receiveShadow = true; mesh.userData.n = s.n; mesh.userData.face = face;
        scene.add(mesh);
        var sh = shadowPlane(sheetShadowTex, PW * 1.32, PH * 1.26, 0.0);
        var rest = new T.Vector3(STACK.x + (hash(i, 1) - 0.5) * 0.016, PT / 2 + i * (PT + GAP), STACK.z + (hash(i, 2) - 0.5) * 0.014);
        var restYaw = (hash(i, 3) - 0.5) * 0.09;
        var from = new T.Vector3(rest.x + 0.12 + hash(i, 4) * 0.08, rest.y + 0.34, rest.z + 0.22 + hash(i, 5) * 0.06);
        var fan, fanYaw, k, roll0 = 0, order;
        if (s.group === "relied") {
          k = relied.indexOf(s); order = k;
          var row = Math.floor(k / per), kk = k - row * per, inRow = Math.min(per, R - row * per), c = kk - (inRow - 1) / 2;
          fan = new T.Vector3(FAN_CX + c * FAN.dx + row * FAN.bx, PT / 2 + k * (PT + 0.0008), FAN.az + row * FAN.bz + (hash(k, 9) - 0.5) * 0.02);
          fanYaw = c * 0.035 + (hash(k, 8) - 0.5) * 0.05;
        } else if (s.group === "off") {
          k = offT.indexOf(s); order = R + side.length + k;
          fan = new T.Vector3(FAN.ox + k * 0.008, PT / 2 + 0.0002 + k * (PT + 0.0004), FAN.oz - k * 0.006); fanYaw = -0.3 + k * 0.05; roll0 = Math.PI;
        } else {
          k = side.indexOf(s); order = R + k;
          var sr = Math.floor(k / 4), sk = k % 4;
          fan = new T.Vector3(FAN.rx + sk * 0.07 + sr * 0.03, PT / 2 + k * (PT + 0.0008), FAN.rz + sk * 0.045 + sr * 0.13);
          fanYaw = SIDE_YAW[sk];
        }
        sheets.push({ s: s, mesh: mesh, sh: sh, rest: rest, restYaw: restYaw, from: from, fromYaw: restYaw + (hash(i, 6) - 0.5) * 0.9, fan: fan, fanYaw: fanYaw, fanRoll: roll0, order: order, lift: 0, hover: 0, seed: seed, painted: false });
      });
    }

    // ---- the index card ----------------------------------------------------------
    var CARD_W = 0.127, CARD_H = 0.0762, CARD_T = 0.0009;
    var card = new T.Group();
    var cBlank = cnv(CW / 4, CH / 4); paintCard(cBlank, false, 0.25);
    var cardSide = new T.MeshStandardMaterial({ color: 0xe6dcc6, roughness: 0.9 });
    var cbMap = tex(cBlank), cvMap = cbMap, csMap = cbMap, cardPainted = false;
    // the large faces are painted only when the check runs: the standing face, then the printed verdict
    function paintCardFaces() {
      if (cardPainted) return; cardPainted = true;
      var cStand = cnv(CW * 1.6, CH * 1.6); paintCard(cStand, false, 1.6); csMap = tex(cStand);
      paintVerdictFace();
      vMat.map = csMap; vMat.emissiveMap = csMap; vMat.needsUpdate = true;
    }
    // the printed faces use the page's web fonts; one painted before they arrived is painted again when they do
    var cvFontOk = false;
    function fontsOk() {
      if (!document.fonts || !document.fonts.check) return false;
      return ['700 40px "Bricolage Grotesque"', '600 40px "Bricolage Grotesque"', '400 40px "Instrument Sans"', '600 40px "Instrument Sans"', '500 20px "IBM Plex Mono"']
        .every(function (f) { return document.fonts.check(f); });
    }
    function paintVerdictFace() {
      var c = cnv(CW, CH); paintCard(c, true); var old = cvMap; cvMap = tex(c); cvFontOk = fontsOk();
      if (vMat.map === old) { vMat.map = vMat.emissiveMap = cvMap; vMat.needsUpdate = true; }
      if (old && old !== cbMap) old.dispose();
    }
    function fontsArrived() {
      if (!fontsOk()) return;
      if (cardPainted && !cvFontOk) paintVerdictFace();
      if (csvMap && !csvFontOk) { csvKey = ""; prepareStandFace(); }
      cardKey = ""; wake();
    }
    if (document.fonts) {
      if (document.fonts.addEventListener) document.fonts.addEventListener("loadingdone", function () { fontsArrived(); });
      if (document.fonts.ready) document.fonts.ready.then(fontsArrived);
    }
    var vMat = new T.MeshStandardMaterial({ map: csMap, roughness: 0.82, emissive: 0xffffff, emissiveMap: csMap, emissiveIntensity: 0 });
    var bMat = new T.MeshStandardMaterial({ map: cbMap, roughness: 0.85 });
    var cardBox = new T.Mesh(new T.BoxGeometry(CARD_W, CARD_H, CARD_T), [cardSide, cardSide, cardSide, cardSide, vMat, bMat]);
    cardBox.castShadow = true; cardBox.receiveShadow = true; card.add(cardBox);
    scene.add(card);
    var CARD_REST = new T.Vector3(0.2, CARD_T / 2, 0.3), CARD_YAW = -0.16;
    var CARD_LEAN = 0.12, CARD_STAND = new T.Vector3(-0.1, CARD_H / 2 * Math.cos(CARD_LEAN) + 0.004, 0.13), CARD_DOWN = new T.Vector3(0.13, CARD_T / 2, 0.27);
    var Q_STAND = new T.Quaternion().setFromEuler(new T.Euler(-CARD_LEAN, 0, 0));
    var qDown = new T.Quaternion().setFromEuler(new T.Euler(Math.PI / 2, 0, 0)); // blank face up
    var qUp = new T.Quaternion().setFromEuler(new T.Euler(-Math.PI / 2, 0, 0));  // verdict face up
    var qYaw = new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), CARD_YAW);
    var Q_REST_BLANK = qYaw.clone().multiply(qDown), Q_REST_VERDICT = qYaw.clone().multiply(qUp);
    var cardSh = shadowPlane(cardShadowTex, CARD_W * 1.6, CARD_H * 1.8, 0.8);

    // ---- camera stops (desktop and phone framings) and where the lamp points ------
    function V(x, y, z) { return new T.Vector3(x, y, z); }
    var KEYS = {
      wide: [
        { p: V(0.74, 0.52, 1.32), t: V(-0.24, 0.2, -0.1), fov: 36, fx: 0.36, fy: 0.52 },
        { p: V(-0.1, 0.1, 0.52), t: V(-0.1, 0.047, 0.13), fov: 26, fx: 0.33, fy: 0.5 },
        { p: V(-0.2, 0.46, 0.8), t: V(-0.44, 0.0, -0.12), fov: 34, fx: 0.32, fy: 0.55 },
        { p: V(0.14, 1.53, 1.09), t: V(-0.07, 0.0, -0.05), fov: 50, fx: 0.71, fy: 0.5 },
        { p: V(0.75, 0.78, 1.5), t: V(-0.2, 0.06, -0.08), fov: 36, fx: 0.74, fy: 0.5 },
        { p: V(1.15, 1.4, 2.05), t: V(-0.12, 0.04, -0.08), fov: 34, fx: 0.62, fy: 0.5 }
      ],
      tall: [
        { p: V(0.25, 1.3, 1.6), t: V(-0.18, 0.0, 0.04), fov: 56, fx: 0.5, fy: 0.86 },
        { p: V(-0.1, 0.105, 0.6), t: V(-0.1, 0.047, 0.13), fov: 34, fx: 0.5, fy: 0.34 },
        { p: V(-0.36, 0.86, 0.78), t: V(-0.44, 0.0, -0.12), fov: 48, fx: 0.5, fy: 0.7 },
        { p: V(-0.06, 2.0, 0.92), t: V(-0.12, 0.0, -0.05), fov: 54, fx: 0.5, fy: 0.24 },
        { p: V(0.5, 1.2, 1.9), t: V(-0.15, 0.0, -0.05), fov: 46, fx: 0.5, fy: 0.5 },
        { p: V(0.7, 1.55, 2.0), t: V(-0.12, 0.0, -0.05), fov: 44, fx: 0.5, fy: 0.7 }
      ]
    };
    var AIMS = [V(-0.22, 0, 0), V(-0.1, 0.03, 0.13), V(-0.44, 0, 0.06), V(-0.22, 0, -0.02), V(-0.2, 0, 0), V(-0.2, 0, 0)];
    var EXPO = [1, 1, 1, 0.92, 0.42, 0.72];
    // the framings of the stops the page prints (before a check, all six: the camera rests on the first)
    var K, AK, EK, CL = 5, posCurve, tgtCurve, AIMS6 = AIMS, EXPO6 = EXPO;
    function pickKeys() {
      var tall = (stage.clientWidth / Math.max(1, stage.clientHeight)) < 0.85, all = KEYS[tall ? "tall" : "wide"];
      var cams = checked && NS > 1 ? STOPS.map(function (s) { return s.cam; }) : [0, 1, 2, 3, 4, 5];
      K = cams.map(function (c) { return all[c]; }); AK = cams.map(function (c) { return AIMS6[c]; }); EK = cams.map(function (c) { return EXPO6[c]; });
      CL = K.length - 1;
      posCurve = new T.CatmullRomCurve3(K.map(function (k) { return k.p; }), false, "centripetal");
      tgtCurve = new T.CatmullRomCurve3(K.map(function (k) { return k.t; }), false, "centripetal");
    }

    // ---- helpers -----------------------------------------------------------------
    function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
    function c01(v) { return clamp(v, 0, 1); }
    function ss(v) { v = c01(v); return v * v * (3 - 2 * v); }
    function eio(u) { u = c01(u); return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2; }
    function eo(u) { u = c01(u); return 1 - Math.pow(1 - u, 3); }
    function hold(P) { var s = Math.floor(clamp(P, 0, CL - 0.0001)), u = P - s; return s + eio((u - 0.06) / 0.88); }
    function lerpArr(arr, hp) { var s = Math.floor(clamp(hp, 0, CL - 0.0001)), f = hp - s; return arr[s] + (arr[Math.min(CL, s + 1)] - arr[s]) * f; }
    var tmpV = new T.Vector3(), qb = new T.Quaternion(), mBasis = new T.Matrix4();
    var right = new T.Vector3(), up = new T.Vector3(), fwd = new T.Vector3(), back = new T.Vector3(), zAx = new T.Vector3();

    // ---- sizing and resolution ------------------------------------------------------
    // A still picture is always drawn at the full ratio, min(2, devicePixelRatio). Only while something moves may the
    // ratio drop, and never below 1; the first frame after the motion ends is drawn at the full ratio again.
    var W = 1, H = 1, sized = false, ratioNow = 0, lastRatio = 0, shadowDirty = true;
    function setRatio(r) { if (r !== ratioNow) { ratioNow = r; renderer.setPixelRatio(r); renderer.setSize(W, H, false); } }
    // F1: on a touch screen a change of height alone keeps the picture as it is when it is the phone keyboard (a text
    // field has focus) or the address bar sliding (160 px or less): the stage stays at the height it was drawn for,
    // running under the keyboard or the bar, so no strip is left uncovered and nothing is reallocated or reframed.
    // Anything else (a new width, a new pixel ratio, a taller window, a desktop window dragged shorter, text field or
    // not) draws afresh, once.
    function keepsPicture(w, h, d) {
      if (!sized || w !== W || d !== DPR_CAP || h > H || !coarse.matches) return false;
      return isField(document.activeElement) || H - h <= 160;
    }
    function resize() {
      var d = Math.min(2, window.devicePixelRatio || 1);
      stage.style.height = "";
      var w = stage.clientWidth, h = stage.clientHeight;
      if (keepsPicture(w, h, d)) { stage.style.height = H + "px"; return; }
      DPR_CAP = d; dpr = DPR_CAP;
      W = w; H = h; sized = true; ratioNow = 0; setRatio(dpr);
      camera.aspect = W / H; pickKeys(); layout(); cardKey = ""; shadowDirty = true; wake();
    }
    var frameTimes = [];
    function adaptMoving(ft) {
      // one very slow moving frame is enough to drop to ratio 1 for the rest of the move (the still picture is redrawn at full ratio)
      if (ft > 120 && dpr > 1) { dpr = 1; frameTimes = []; return; }
      frameTimes.push(ft); if (frameTimes.length > 10) frameTimes.shift();
      if (frameTimes.length < 10) return;
      var avg = frameTimes.reduce(function (a, b) { return a + b; }, 0) / frameTimes.length;
      if (avg > 24 && dpr > 1) { dpr = Math.max(1, dpr - 0.25); frameTimes = []; }
    }

    // ---- pointer: hover and pick sheets at the weighing stop -------------------------
    var ray = new T.Raycaster(), ndc = new T.Vector2(), pointer = { x: 0, y: 0, has: false }, Ps = 0;
    function pickAt(cx, cy) {
      var r = canvas.getBoundingClientRect();
      ndc.set(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      var hit = ray.intersectObjects(sheets.map(function (o) { return o.mesh; }), false)[0];
      return hit ? hit.object.userData.n : 0;
    }
    function atWeigh() { return WEIGH >= 0 && Math.abs(Ps - WEIGH) < 0.25; }
    stage.addEventListener("pointermove", function (e) {
      pointer.x = e.clientX / innerWidth - 0.5; pointer.y = e.clientY / innerHeight - 0.5; pointer.has = true; wake();
      if (atWeigh()) { var n = pickAt(e.clientX, e.clientY); hoverN = n; stage.style.cursor = n ? "pointer" : ""; }
      else stage.style.cursor = "";
    });
    stage.addEventListener("click", function (e) {
      if (!atWeigh()) return;
      var n = pickAt(e.clientX, e.clientY);
      if (n) { if (n === openN) clearStudy(); else selectStudy(n, { from: "scene" }); } else clearStudy();
      wake();
    });

    // ---- the frame ---------------------------------------------------------------
    var last = performance.now();
    var raf = 0, frames = 0, looping = false, wasMoving = false, waiters = [], cardKey = "", cardRectNow = null;
    wake = function () { if (!raf) raf = requestAnimationFrame(frame); };
    addEventListener("resize", resize);
    resize();
    Ps = rawProgress();

    function poseSheets(P, dt) {
      var busy = false;
      sheets.forEach(function (o, i) {
        var m = o.mesh, x, y, z, yaw, tilt = 0, roll = 0, landY;
        // the reading beat: a quick drop on the way to the answer
        var td = o.placed ? 1 : reduce.matches ? (P >= 0.3 ? 1 : 0) : c01((P - 0.02 - i * 0.04) / 0.26);
        var vt = c01(td / 0.62), ht = eo(td);
        x = o.from.x + (o.rest.x - o.from.x) * ht;
        z = o.from.z + (o.rest.z - o.from.z) * ht;
        y = o.rest.y + (o.from.y - o.rest.y) * (1 - vt * vt);
        yaw = o.fromYaw + (o.restYaw - o.fromYaw) * eo(td * 1.1);
        tilt = (1 - eo(vt)) * (0.32 + o.seed * 0.2); roll = (1 - eo(vt)) * (o.seed - 0.5) * 0.4;
        var visible = td > 0;
        landY = o.rest.y - PT / 2;
        // the weighing: relied on into the light, the rest to the dim side
        var tsr = reduce.matches ? (P >= 2.5 ? 1 : 0) : eio((P - 2.08 - o.order * 0.022) / 0.45);
        if (tsr > 0) {
          x = o.rest.x + (o.fan.x - o.rest.x) * tsr;
          z = o.rest.z + (o.fan.z - o.rest.z) * tsr;
          y = o.rest.y + (o.fan.y - o.rest.y) * tsr + Math.sin(Math.PI * tsr) * (0.03 + o.order * 0.002);
          yaw = o.restYaw + (o.fanYaw - o.restYaw) * tsr; roll = o.fanRoll * tsr;
          landY = (o.rest.y + (o.fan.y - o.rest.y) * tsr) - PT / 2;
        }
        var hv = hoverN === o.s.n && WEIGH >= 0 && Math.abs(P - WEIGH) < 0.4 ? 1 : 0;
        o.hover += (hv - o.hover) * c01(dt * 10);
        if (Math.abs(hv - o.hover) < 0.002) o.hover = hv; else busy = true;
        y += o.hover * 0.008;
        m.visible = visible;
        m.position.set(x, y, z);
        m.quaternion.setFromEuler(new T.Euler(tilt, yaw, roll, "YXZ"));
        // lifted toward the camera when picked
        var target = openN === o.s.n ? 1 : 0;
        o.lift = reduce.matches ? target : o.lift + (target - o.lift) * c01(dt * 5.5);
        if (Math.abs(target - o.lift) < 0.001) o.lift = target; else busy = true;
        hiRes(o, target);
        if (o.lift > 0.001) {
          var L = eio(o.lift);
          camera.getWorldDirection(fwd); right.setFromMatrixColumn(camera.matrixWorld, 0); up.setFromMatrixColumn(camera.matrixWorld, 1);
          var th = Math.tan(T.MathUtils.degToRad(camera.fov / 2)), wide = camera.aspect > 0.85;
          var d = wide ? Math.max(PH / (0.7 * 2 * th), PW / (0.3 * 2 * th * camera.aspect)) : Math.max(PH / (0.4 * 2 * th), PW / (0.8 * 2 * th * camera.aspect));
          var xN = wide ? 0.05 : 0, yN = wide ? 0 : 0.5;
          tmpV.copy(camera.position).addScaledVector(fwd, d).addScaledVector(right, xN * d * th * camera.aspect).addScaledVector(up, yN * d * th);
          back.copy(fwd).negate(); zAx.crossVectors(right, back);
          mBasis.makeBasis(right, back, zAx); qb.setFromRotationMatrix(mBasis);
          m.position.lerp(tmpV, L); m.quaternion.slerp(qb, L);
        }
        var h = Math.max(0, m.position.y - PT / 2 - landY);
        var shScale = 1 + h * 2.2, shOp = visible ? 0.55 * (1 - c01(h * 7)) : 0;
        o.sh.position.set(m.position.x, landY + 0.0003, m.position.z);
        o.sh.rotation.z = yaw; o.sh.scale.set(shScale, shScale, 1);
        o.sh.material.opacity = shOp * (1 - (o.lift > 0.001 ? eio(o.lift) : 0));
        o.sh.visible = shOp > 0.003;
        m.userData.face.emissiveIntensity = 0.62 * (o.lift > 0.001 ? eio(o.lift) : 0);
      });
      return busy;
    }
    function poseCard(P) {
      // rest (blank up) -> rises and stands facing the answer camera -> laid down, verdict up, beside the pencil
      var a = reduce.matches ? (P >= 0.5 ? 1 : 0) : c01((P - 0.3) / 0.55);
      var b = reduce.matches ? (P >= 1.5 ? 1 : 0) : c01((P - 1.12) / 0.6);
      var cp = new T.Vector3(), cq = new T.Quaternion(), stand = 0;
      if (b <= 0) {
        var la = eio(a), ra = eio((a - 0.1) / 0.9);
        cp.copy(CARD_REST).lerp(CARD_STAND, la); cp.y += Math.sin(Math.PI * la) * 0.06;
        cq.copy(Q_REST_BLANK).slerp(Q_STAND, ra); stand = ra;
      } else {
        var lb = eio(b);
        cp.copy(CARD_STAND).lerp(CARD_DOWN, lb); cp.y += Math.sin(Math.PI * lb) * 0.05;
        cq.copy(Q_STAND).slerp(Q_REST_VERDICT, eio((b - 0.05) / 0.9)); stand = 1 - eio((b - 0.05) / 0.9);
      }
      card.position.copy(cp); card.quaternion.copy(cq);
      // standing, the face is plain paper only while the HTML words are fully shown over it; whenever they are not
      // (fading, scrolled, a panel passing), the card carries its printed face, so one of the two is always readable
      standing = b <= 0.35;
      var fm = standing ? standFace() : cvMap; if (vMat.map !== fm) { vMat.map = fm; vMat.emissiveMap = fm; }
      // standing, it drops its hard cast shadow for a soft contact one
      cardBox.castShadow = stand < 0.3;
      var bottom = cp.y - (CARD_H / 2) * stand - CARD_T / 2 * (1 - stand);
      var h = Math.max(0, bottom);
      cardSh.position.set(cp.x, 0.00025, cp.z + 0.012 * stand);
      var yawNow = b > 0 ? CARD_YAW * eio((b - 0.05) / 0.9) : CARD_YAW * (1 - stand);
      cardSh.rotation.z = yawNow;
      var sc = 1 + h * 3; cardSh.scale.set(sc * (1 + 0.15 * stand), sc * (1 - 0.55 * stand), 1);
      cardSh.material.opacity = 0.8 * (1 - c01(h * 14)) * (1 - 0.15 * stand);
      vMat.emissiveIntensity = 0.1 * stand;
      return stand;
    }
    function frame(now) {
      raf = 0;
      // slow frames take bigger steps, so a slow machine settles in a few frames, not dozens
      var ft = now - last, dt = wasMoving ? Math.min(1, ft / 1000) : 1 / 60; last = now;
      if (tween) tween(now);
      var P = rawProgress();
      if (reduce.matches) Ps = Math.round(P);
      else Ps += (P - Ps) * (1 - Math.exp(-dt * 4.2));
      if (wasMoving) slowGl = ft > 350;
      if (slowGl || Math.abs(P - Ps) < 0.0015) Ps = P;

      var hp = reduce.matches ? Math.round(P) : hold(Ps), u = CL ? hp / CL : 0;
      posCurve.getPoint(u, camera.position); var tg = tgtCurve.getPoint(u);
      var s = Math.floor(clamp(hp, 0, CL - 0.0001)), f = hp - s, A = K[s], B = K[Math.min(CL, s + 1)];
      camera.fov = A.fov + (B.fov - A.fov) * f;
      var fx = A.fx + (B.fx - A.fx) * f, fy = A.fy + (B.fy - A.fy) * f;
      if (!reduce.matches && pointer.has && camera.aspect > 0.85) { camera.position.x += pointer.x * 0.03; camera.position.y -= pointer.y * 0.015; }
      camera.lookAt(tg);
      camera.setViewOffset(W, H, (0.5 - fx) * W, (0.5 - fy) * H, W, H);
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld();

      var busy = poseSheets(Ps, dt);
      var stand = poseCard(Ps);
      var k1 = ss((Ps - 0.35) / 0.5) * (1 - ss((Ps - 1.12) / 0.45));
      key.intensity = 1.5 * k1 * stand;
      tmpV.copy(AK[s]).lerp(AK[Math.min(CL, s + 1)], f); aimLamp(tmpV);
      spot.angle = LAMP_ANGLE - 0.26 * k1;
      renderer.toneMappingExposure = EXPOSURE * lerpArr(EK, hp);
      var moving = !!tween || P !== Ps || busy;
      if (moving && wasMoving) adaptMoving(ft);
      if (!moving) { dpr = DPR_CAP; frameTimes = []; }
      setRatio(dpr);
      // shadows are redrawn only when something that casts one has moved
      // the shadow map is redrawn only when something that casts a shadow has moved, appeared or gone (not for a camera move)
      var sig = casterSig();
      if (shadowDirty || sig !== lastSig) { renderer.shadowMap.needsUpdate = true; lastSig = sig; shadowDirty = false; }
      if (placeCard(stand, moving, ft)) moving = true;
      renderer.render(scene, camera);
      lastRatio = ratioNow; frames++;
      wasMoving = moving; looping = moving;
      if (frames === 1) setTimeout(paintLazy, 0);
      if (moving) wake();
      else {
        if (woodState !== "loading" && !handed) {
          // never under a finger or mid scroll: the swap waits for 300 ms of rest
          var quiet = performance.now() - lastScrollAt;
          if (touching || pressing || quiet < 300) { clearTimeout(handT); handT = setTimeout(wake, touching || pressing ? 120 : 310 - quiet); return; }
          handover(); wake(); return;
        }
        if (woodState !== "loading" && !readyDone) { readyDone = true; requestAnimationFrame(function () { readyResolve(); }); }
        if (waiters.length) { var w = waiters; waiters = []; requestAnimationFrame(function () { w.forEach(function (r) { r(); }); }); }
      }
    }
    var lastShadowPs = -1, readyDone = false, lastSig = "", casters = null, handed = false;
    // the desk takes over from the early text-mode card: the page switches to its desk layout and keeps the reader on the same stop
    function handover() {
      handed = true;
      var y = window.scrollY, P = checked ? rawProgress() : 0, k = Math.floor(P + 1e-6), off = y - anchors[Math.min(LAST, k)];
      // inside Weighed or Research: the first row or paragraph whose top is on screen keeps its place on screen
      var keep = null, keepTop = 0;
      if (checked && P - k < 1e-6 && sections[k].classList.contains("long") && off > 0) {
        var cands = sections[k].querySelectorAll(".row, p, h3, summary");
        for (var i = 0; i < cands.length; i++) { var q = cands[i].getBoundingClientRect(); if (q.height && q.top >= stripH && q.top < innerHeight - 80) { keep = cands[i]; keepTop = q.top; break; } }
      }
      root.classList.remove("early"); root.classList.add("has-gl");
      layout();
      if (checked) {
        var to = P - k < 1e-6 ? Math.min(holds[k], anchors[k] + Math.max(0, off)) : holds[k] + (P - k) * (anchors[k + 1] - holds[k]);
        if (keep) to = Math.max(anchors[k], Math.min(holds[k], window.scrollY + keep.getBoundingClientRect().top - keepTop));
        window.scrollTo({ top: Math.round(to), behavior: "instant" });
        restY = prevY = progY = window.scrollY; runLen = 0;
      }
      Ps = rawProgress(); cardKey = ""; updateWay();
    }
    function casterSig() {
      if (!casters) { casters = []; scene.traverse(function (o) { if (o.castShadow && o.isMesh) casters.push(o); }); }
      scene.updateMatrixWorld();
      var h = 0, h2 = 0;
      for (var i = 0; i < casters.length; i++) {
        var o = casters[i], vis = o.visible; for (var q = o.parent; vis && q; q = q.parent) vis = q.visible;
        if (!vis) { h2 += (i + 1) * 7919; continue; }
        var e = o.matrixWorld.elements; h += (e[0] + e[2] * 3 + e[5] * 5 + e[9] * 7 + e[12] * 11 + e[13] * 13 + e[14] * 17) * (i + 1);
      }
      return h.toFixed(6) + "|" + h2;
    }
    setTimeout(function () { if (woodState === "loading") { woodState = "slow"; wake(); } }, 6000);

    // ---- the answer card: real HTML text laid exactly over the settled 3D card ---------
    var vcard = document.getElementById("vcard"), shortLabel = document.getElementById("short-label");
    var cardCorners = [V(-CARD_W / 2, CARD_H / 2, CARD_T / 2), V(CARD_W / 2, CARD_H / 2, CARD_T / 2), V(CARD_W / 2, -CARD_H / 2, CARD_T / 2), V(-CARD_W / 2, -CARD_H / 2, CARD_T / 2)];
    var cardA = 0, capInner = document.querySelector("#cap-answer .inner");
    function setCardA(a) { cardA = a; vcard.style.opacity = shortLabel.style.opacity = a ? a.toFixed(3) : ""; }
    function placeCard(stand, moving, ft) {
      if (!handed) { setStandFace(); return false; }
      // words appear only on a settled card, and stay until the card has clearly left (no flicker between frames)
      var settled = cardRectNow ? (curStop === 1 && Math.abs(Ps - 1) < 0.02 && stand > 0.98) : (!moving && curStop === 1 && Math.abs(Ps - 1) < 0.003 && stand > 0.995);
      if (!settled && cardA === 0) { if (cardRectNow) cardRectNow = null; vcard.classList.remove("on"); shortLabel.classList.remove("on"); setStandFace(); return false; }
      // while the words fade, in or out, they stay laid over the card
      card.updateMatrixWorld(true);
      var c = cardCorners.map(function (v) { var p = v.clone().applyMatrix4(cardBox.matrixWorld).project(camera); return [(p.x * 0.5 + 0.5) * W, (-p.y * 0.5 + 0.5) * H]; });
      var x0 = Math.ceil(Math.max(c[0][0], c[3][0])), x1 = Math.floor(Math.min(c[1][0], c[2][0]));
      var y0 = Math.ceil(Math.max(c[0][1], c[1][1])), y1 = Math.floor(Math.min(c[2][1], c[3][1]));
      var r = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, k = [r.x, r.y, r.w, r.h].join(",");
      // on a short screen the panel scrolls up over the held card: the words leave before the panel reaches them
      if (settled && capInner && capInner.getBoundingClientRect().top < r.y + r.h + 10 && capInner.getBoundingClientRect().left < r.x + r.w + 10) settled = false;
      if (!settled && cardRectNow) cardRectNow = null;
      if (!settled && cardA === 0) { vcard.classList.remove("on"); shortLabel.classList.remove("on"); setStandFace(); return false; }
      if (k !== cardKey) {
        cardKey = k;
        vcard.style.left = r.x + "px"; vcard.style.top = r.y + "px"; vcard.style.width = Math.max(0, r.w) + "px"; vcard.style.height = Math.max(0, r.h) + "px";
        if (settled) layoutCard(r.w, r.h);
        shortLabel.style.left = r.x + "px"; shortLabel.style.top = Math.max(0, r.y - shortLabel.offsetHeight - 12) + "px";
      }
      if (settled) { cardRectNow = r; vcard.classList.add("on"); shortLabel.classList.add("on"); }
      var step = reduce.matches ? 1 : Math.min(0.15, Math.max(0.004, (ft || 16) / 1000)) / 0.15;
      setCardA(settled ? Math.min(1, cardA + step) : Math.max(0, cardA - step));
      setStandFace();
      if (cardA === 0) { vcard.classList.remove("on"); shortLabel.classList.remove("on"); }
      return settled ? cardA < 1 : cardA > 0;
    }
    var standing = false, csvMap = null, csvKey = "", csvFontOk = false;
    function standFace() { return cardA >= 1 && cardRectNow ? csMap : (csvMap || cvMap); }
    function setStandFace() { if (!standing) return; var fm = standFace(); if (vMat.map !== fm) { vMat.map = fm; vMat.emissiveMap = fm; } }
    // each line of an element's text, as the browser set it: its words, left edge and top
    function textLines(n) {
      var out = [], cur = null, w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT), t, rg = document.createRange(), m, re = /\S+/g;
      while ((t = w.nextNode())) {
        re.lastIndex = 0;
        while ((m = re.exec(t.data))) {
          rg.setStart(t, m.index); rg.setEnd(t, m.index + m[0].length); var q = rg.getClientRects()[0]; if (!q) continue;
          if (!cur || Math.abs(q.top - cur.top) > 2) { cur = { t: m[0], x: q.left, top: q.top }; out.push(cur); } else cur.t += " " + m[0];
        }
      }
      return out;
    }
    function paintStandFace(r) {
      var key = [Math.round(r.w), Math.round(r.h), vcard.className, vcard.querySelector(".v").style.fontSize, vcard.querySelector(".lbl").style.display, fontsOk(), vcard.textContent].join(",");
      if (key === csvKey || r.w < 40) return; csvKey = key;
      var cw = Math.min(2048, Math.max(1024, Math.round(r.w * DPR_CAP * 1.25))), ch = Math.round(cw * CH / CW);
      var c = cnv(cw, ch); paintCard(c, false, cw / CW);
      var x = c.getContext("2d"), vb = vcard.getBoundingClientRect();
      x.setTransform(cw / r.w, 0, 0, ch / r.h, 0, 0); x.textBaseline = "alphabetic";
      [].forEach.call(vcard.children, function (n) {
        var cs = getComputedStyle(n); if (cs.display === "none") return;
        x.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
        if ("letterSpacing" in x) x.letterSpacing = cs.letterSpacing === "normal" ? "0px" : cs.letterSpacing;
        x.fillStyle = cs.color;
        var asc = x.measureText("Hg").fontBoundingBoxAscent, up = cs.textTransform === "uppercase";
        // a clamped block paints only the lines it shows, the last ending in an ellipsis; the claim carries its quotes
        var lines = textLines(n), cap = parseInt(cs.webkitLineClamp, 10), quoted = n.classList.contains("q");
        if (cap > 0 && lines.length > cap) { lines = lines.slice(0, cap); lines[cap - 1] = { t: lines[cap - 1].t.replace(/[\s,.;:]+$/, "") + "\u2026", x: lines[cap - 1].x, top: lines[cap - 1].top }; }
        else if (quoted && lines.length) lines[lines.length - 1] = { t: lines[lines.length - 1].t + "\u201D", x: lines[lines.length - 1].x, top: lines[lines.length - 1].top };
        lines.forEach(function (L, i) {
          var dx = quoted && i === 0 ? x.measureText("\u201C").width : 0;
          x.fillText((dx ? "\u201C" : "") + (up ? L.t.toUpperCase() : L.t), L.x - vb.left - dx, L.top - vb.top + asc);
        });
      });
      var old = csvMap; csvMap = tex(c); csvFontOk = fontsOk(); if (vMat.map === old) { vMat.map = vMat.emissiveMap = csvMap; } if (old) old.dispose();
    }
    // lay the HTML card out for the Answer stop's card and paint the printed face from it
    function prepareStandFace() {
      if (!handed || !checked || !cardPainted) return;
      var r = cardRectAt(1);
      r = { x: Math.ceil(r.x), y: Math.ceil(r.y), w: Math.floor(r.x + r.w) - Math.ceil(r.x), h: Math.floor(r.y + r.h) - Math.ceil(r.y) };
      vcard.style.left = r.x + "px"; vcard.style.top = r.y + "px"; vcard.style.width = r.w + "px"; vcard.style.height = r.h + "px";
      layoutCard(r.w, r.h); paintStandFace(r); cardKey = "";
    }
    // tall screens: the card sits just under its label, and the panel starts at least 12 px below the card
    var capAnswer = document.getElementById("cap-answer");
    function cardRectAt(k) {
      var A = K[k], cam = camera.clone();
      cam.position.copy(A.p); cam.fov = A.fov; cam.aspect = W / H; cam.lookAt(A.t);
      cam.setViewOffset(W, H, (0.5 - A.fx) * W, (0.5 - A.fy) * H, W, H); cam.updateProjectionMatrix(); cam.updateMatrixWorld();
      var m = new T.Matrix4().compose(CARD_STAND, Q_STAND, new T.Vector3(1, 1, 1));
      var c = cardCorners.map(function (v) { var q = v.clone().applyMatrix4(m).project(cam); return [(q.x * 0.5 + 0.5) * W, (-q.y * 0.5 + 0.5) * H]; });
      var y0 = Math.max(c[0][1], c[1][1]), y1 = Math.min(c[2][1], c[3][1]), x0 = Math.max(c[0][0], c[3][0]), x1 = Math.min(c[1][0], c[2][0]);
      return { x: x0, y: y0, h: y1 - y0, w: x1 - x0 };
    }
    layoutHook = function () {
      capAnswer.style.paddingTop = "";
      if (!K) return;
      if (K[1].fx0 === undefined) K[1].fx0 = K[1].fx;
      K[1].fx = K[1].fx0;
      if (camera.aspect >= 0.85) {
        // wide screens: the card keeps 32 px clear of the panel beside it (at 1280 wide the panel would otherwise reach over its right edge)
        var inner = capAnswer.querySelector(".inner"), pl = inner ? inner.getBoundingClientRect().left : W;
        for (var m = 0; m < 3; m++) { var q = cardRectAt(1), over = q.x + q.w - (pl - 32); if (over <= 0) break; K[1].fx -= Math.min(over, Math.max(0, q.x - 24)) / W; }
        cardKey = ""; prepareStandFace(); return;
      }
      var want = stripH + 12 + (shortLabel.offsetHeight || 16) + 10, wantW = Math.min(W - 22, 560);
      if (!K[1].fov0) K[1].fov0 = K[1].fov;
      K[1].fov = K[1].fov0;
      for (var j = 0; j < 3; j++) { var q = cardRectAt(1), th = Math.tan(T.MathUtils.degToRad(K[1].fov / 2)) * q.w / wantW; K[1].fov = T.MathUtils.radToDeg(2 * Math.atan(th)); }
      for (var i = 0; i < 3; i++) { var r = cardRectAt(1); K[1].fy += (want - r.y) / H; }
      r = cardRectAt(1);
      var capTop = capAnswer.getBoundingClientRect().top - byId("s2").getBoundingClientRect().top;
      capAnswer.style.paddingTop = Math.max(0, Math.ceil(r.y + r.h + 14 - capTop)) + "px";
      cardKey = ""; prepareStandFace();
    };
    // where a line's baseline falls inside its box, measured with a zero-size marker
    function baseOff(node) {
      var m = document.createElement("span"); m.style.cssText = "display:inline-block;width:0;height:0;vertical-align:baseline";
      node.insertBefore(m, node.firstChild); var off = m.getBoundingClientRect().top - node.getBoundingClientRect().top; node.removeChild(m); return off;
    }
    // the painted card's own layout (1600 by 960 units): label, claim, verdict, short answer, each at its baseline
    var CARD_LAYOUT = [
      { sel: ".lbl", size: 32, min: 13, left: 96, base: 108 },
      { sel: ".q", size: 54, min: 17, left: 96, base: 252 },
      { sel: ".v", size: 200, min: 40, left: 86, base: 490 },
      { sel: ".t", size: 54, min: 17, left: 96, base: 618, lh: 70, width: 1408 }
    ];
    // the claim, the verdict and the short answer fitted to the card as paintCard fits them
    function linesOf(n) { var lh = parseFloat(getComputedStyle(n).lineHeight) || 1; return Math.max(1, Math.round(n.offsetHeight / lh)); }
    function place(n, base, sc) { n.style.top = "0px"; n.style.top = (base * sc - baseOff(n)).toFixed(2) + "px"; }
    function clampTo(n, k) { n.style.display = "-webkit-box"; n.style.webkitBoxOrient = "vertical"; n.style.webkitLineClamp = String(k); n.style.overflow = "hidden"; }
    function fitCardWords(sc) {
      var q = vcard.querySelector(".q"), v = vcard.querySelector(".v"), t = vcard.querySelector(".t");
      q.style.whiteSpace = "normal"; q.style.width = (1408 * sc).toFixed(2) + "px";
      var cf = fitClaim(function (size) { q.style.fontSize = Math.max(13, size * sc).toFixed(2) + "px"; q.style.lineHeight = "1.2"; return linesOf(q); });
      q.style.fontSize = Math.max(13, cf.size * sc).toFixed(2) + "px";
      if (cf.clamp) clampTo(q, 3);
      place(q, claimBase(cf), sc);
      var vw = v.scrollWidth, room = VERDICT_W * sc;
      if (vw > room) { v.style.fontSize = (parseFloat(v.style.fontSize) * room / vw).toFixed(2) + "px"; place(v, 490, sc); }
      var tf = fitTldr(function (size) { t.style.fontSize = Math.max(13, size * sc).toFixed(2) + "px"; t.style.lineHeight = (Math.round(size * 1.3) * sc).toFixed(2) + "px"; return linesOf(t); });
      t.style.fontSize = Math.max(13, tf.size * sc).toFixed(2) + "px"; t.style.lineHeight = (tf.lh * sc).toFixed(2) + "px";
      if (linesOf(t) > tf.lines) clampTo(t, tf.lines);
      place(t, 618, sc);
    }
    function layoutCard(w, h) {
      var sc = w / 1600, compact = 54 * sc < 17;
      vcard.classList.toggle("compact", compact);
      CARD_LAYOUT.forEach(function (L) {
        var n = vcard.querySelector(L.sel); n.style.cssText = "";
        if (compact) return;
        n.style.fontSize = Math.max(L.min, L.size * sc).toFixed(2) + "px";
        n.style.lineHeight = L.lh ? (L.lh * sc).toFixed(2) + "px" : "1.2";
        n.style.left = (L.left * sc).toFixed(2) + "px";
        if (L.width) n.style.width = (L.width * sc).toFixed(2) + "px";
        n.style.top = "0px";
        n.style.top = (L.base * sc - baseOff(n)).toFixed(2) + "px";
      });
      if (!compact) fitCardWords(sc);
      if (compact) {
        // a phone: the same four lines in flow, at reading sizes, shrinking the verdict until all of it fits on the card
        var v = vcard.querySelector(".v"), vs = Math.min(48, Math.round(w * 0.12));
        v.style.fontSize = vs + "px";
        while (vcard.scrollHeight > h + 0.5 && vs > 32) { vs -= 2; v.style.fontSize = vs + "px"; }
        // the smallest cards drop the label line, which the painted card already carries, before the verdict goes below 28 px
        if (vcard.scrollHeight > h + 0.5) vcard.querySelector(".lbl").style.display = "none";
        while (vcard.scrollHeight > h + 0.5 && vs > 28) { vs -= 2; v.style.fontSize = vs + "px"; }
      }
    }

    // ---- the lifted sheet is repainted at twice the size, one at a time --------------
    var hiSlot = null;
    function hiRes(o, target) {
      if (target === 1 && hiSlot !== o) {
        if (hiSlot) dropHi();
        var c = cnv(SW * 2, SH * 2); paintSheet(c, o.s, o.seed, 2);
        var t = tex(c); o.lowMap = o.mesh.userData.face.map;
        o.mesh.userData.face.map = t; o.mesh.userData.face.emissiveMap = t; hiSlot = o;
      } else if (target === 0 && hiSlot === o && o.lift === 0) dropHi();
    }
    function dropHi() {
      var o = hiSlot, f = o.mesh.userData.face, t = f.map; f.map = o.lowMap; f.emissiveMap = o.lowMap; t.dispose(); hiSlot = null;
    }

    // ---- the browser drops the drawing context (a phone backgrounding the tab, low memory) ----
    // The canvas goes blank, and the card's dark words would sit on the dark page. The page goes to
    // the text mode at once, on the same stop, and stays there: no frame is drawn again.
    var glLost = false;
    canvas.addEventListener("webglcontextlost", function () {
      if (glLost) return; glLost = true;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      looping = false; scene3d = false;
      wake = function () {}; drawImpl = function () {}; layoutHook = function () {};
      onLoad = function () {}; onHome = function () {}; onStage = function () {};
      // the first row or paragraph on screen in the stop the reader is on keeps its place on screen
      var k = curStop, keep = null, keepTop = 0, cands = sections[k] ? sections[k].querySelectorAll(".row, p, h3, summary") : [];
      for (var i = 0; i < cands.length; i++) { var q = cands[i].getBoundingClientRect(); if (q.height && q.top >= stripH && q.top < innerHeight - 80) { keep = cands[i]; keepTop = q.top; break; } }
      endTween();
      // the card and its label leave the desk's layout: no position, size or type set for the painted card stays
      vcard.classList.remove("on", "compact"); shortLabel.classList.remove("on");
      [vcard, shortLabel, capAnswer, stage].concat([].slice.call(vcard.querySelectorAll("[style]"))).forEach(function (n) { n.removeAttribute("style"); });
      fallback();
      var to = keep ? window.scrollY + keep.getBoundingClientRect().top - keepTop : anchors[Math.min(LAST, k)];
      window.scrollTo({ top: Math.max(0, Math.round(to)), behavior: "instant" });
      prevY = progY = restY = landY = window.scrollY; nextWords(Math.min(LAST, k), window.scrollY); noteRest(); updateWay();
    }, false);

    // ---- the test hook ----------------------------------------------------------------
    statsImpl = function () {
      updateWay(); return { mode: "3d", checked: checked, stop: curStop + 1, stops: NS, pixelRatio: lastRatio, fullPixelRatio: DPR_CAP, fog: !!scene.fog, looping: looping, frames: frames,
        cardRect: cardRectNow ? { x: cardRectNow.x, y: cardRectNow.y, w: cardRectNow.w, h: cardRectNow.h } : null, sheets: sheets.length, wood: woodState,
        // true once every printed card face in use was painted after the web fonts were ready
        facesFontsOk: (!cardPainted || cvFontOk) && (!csvMap || csvFontOk) };
    };
    drawImpl = function () { if (raf) { cancelAnimationFrame(raf); raf = 0; } frame(performance.now()); };
    gotoImpl = function (n) {
      n = clamp(Math.round(n), 1, NS); if (n >= 2) check(); endTween(); clearStudy(); layout();
      window.scrollTo({ top: anchors[n - 1], behavior: "instant" }); prevY = progY = window.scrollY; nextWords(n - 1, window.scrollY); noteRest();
      Ps = rawProgress(); updateWay();
      sheets.forEach(function (o) { o.lift = openN === o.s.n ? 1 : 0; });
      return new Promise(function (res) { waiters.push(res); wake(); });
    };
    sheetPointImpl = function (n) {
      var o = sheets.filter(function (q) { return q.s.n === n; })[0]; if (!o || !o.mesh.visible) return null;
      o.mesh.updateMatrixWorld(true); var r = canvas.getBoundingClientRect();
      var cand = [[0, 0], [0.25, 0.25], [-0.25, 0.25], [0.25, -0.25], [-0.25, -0.25], [0, 0.4], [0, -0.4], [0.4, 0], [-0.4, 0], [0.4, 0.4], [-0.4, -0.4], [0.4, -0.4], [-0.4, 0.4]];
      for (var i = 0; i < cand.length; i++) {
        var v = V(cand[i][0] * PW, PT / 2, cand[i][1] * PH).applyMatrix4(o.mesh.matrixWorld).project(camera);
        var x = r.left + (v.x * 0.5 + 0.5) * W, y = r.top + (-v.y * 0.5 + 0.5) * H;
        if (x < 2 || y < 2 || x > innerWidth - 2 || y > innerHeight - 2) continue;
        var top = document.elementFromPoint(x, y);
        if (pickAt(x, y) === n && top && (top === canvas || top === stage)) return { x: Math.round(x), y: Math.round(y) };
      }
      return null;
    };
    window.__evd = {
      ready: true, gl: true, renderer: renderer, scene: scene, camera: camera,
      snap: function () { Ps = rawProgress(); sheets.forEach(function (o) { o.lift = openN === o.s.n ? 1 : 0; }); wake(); },
      dpr: function () { return lastRatio; },
      open: function (n) { selectStudy(n, { from: "scene" }); wake(); },
      KEYS: KEYS, V: V, resize: resize, sheets: sheets, FAN: FAN,
      proj: function (x, y, z) { var v = new T.Vector3(x, y, z).project(camera); return [Math.round((v.x * 0.5 + 0.5) * W), Math.round((-v.y * 0.5 + 0.5) * H)]; }
    };
    // ---- lazy painting: the sheets' own faces after the first frame, a few at a time; the card on check ----
    var lazyQ = null;
    function paintOne(o, big) {
      if (o.painted && !big) return; o.painted = true;
      var k = big ? 1 : 0.5, c = cnv(SW * k, SH * k); paintSheet(c, o.s, o.seed, k);
      var t = tex(c), f = o.mesh.userData.face;
      if (hiSlot === o) o.lowMap = t; else { f.map = t; f.emissiveMap = t; f.needsUpdate = true; }
    }
    function paintLazy() {
      if (lazyQ) return;
      var gen = sheetGen;
      lazyQ = sheets.slice().sort(function (a, b) { return b.rest.y - a.rest.y; });
      (function step() {
        if (gen !== sheetGen) { lazyQ = null; return; }
        var t0 = performance.now();
        var seen = false;
        while (lazyQ.length && performance.now() - t0 < 12) { var o = lazyQ.shift(); paintOne(o); seen = seen || o.mesh.visible; }
        // a sheet out of sight needs no new frame
        if (seen) wake();
        if (lazyQ.length) setTimeout(step, 16); else lazyQ = null;
      })();
    }
    onCheck = function () {
      casters = null; shadowDirty = true;
      paintCardFaces();
      sheets.forEach(function (o) { paintOne(o); });
      // the top of the stack is seen close at the reading: its face at full size
      if (sheets.length) paintOne(sheets[sheets.length - 1], true);
      wake();
    };
    // a new result: its sheets, its card and its framings; the page is laid out again around them
    // a check running: at "found N", N blank sheets stand in the stack, placed, not moved (none under reduced motion).
    // The result's own sheets then take their places in the stack, already there
    var stagedN = 0;
    onStage = function (n) {
      if (reduce.matches || checked) return;
      var list = []; for (var i = 0; i < n; i++) list.push({ n: i + 1, group: "read", design: "", title: "", journal: "", year: "", pmid: "" });
      CHECK = { studies: list }; buildSheets(); sheets.forEach(function (o) { o.placed = true; }); stagedN = n; wake();
    };
    onLoad = function () {
      var staged = stagedN > 0; stagedN = 0;
      buildSheets(); if (staged) sheets.forEach(function (o) { o.placed = true; });
      cardPainted = false; csvKey = ""; cardKey = ""; pickKeys();
      checked = false; check();
      if (frames) setTimeout(paintLazy, 0);
      Ps = rawProgress(); wake();
    };
    // back to the claim box: the camera is at the first stop at once
    onHome = function () { if (stagedN) { stagedN = 0; CHECK = { studies: [] }; dropSheets(); } pickKeys(); Ps = 0; hoverN = 0; wake(); };
    buildSheets(); pickKeys();
    if (checked) { checked = false; check(); }
    layout(); updateWay();
    wake();
  }
})();
