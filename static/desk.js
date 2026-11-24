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
  // the four sections a reader moves between, by what they hold. The page keeps its six stops for the camera;
  // the picker, the rail and every name a reader sees use these
  var SEC = { Claim: "Answer", Answer: "Answer", Read: "Studies", Weighed: "Studies", Research: "Explanation", Open: "Limitations" };
  var SECS = [];   // [{ name, stop }] the sections this result has, each with the first stop it starts on
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
  // a study picked from the prose or its title opens its abstract in place (the row's Read abstract disclosure)
  function setMore(n, open) { var d = byId("abs-" + n); if (d && open) d.open = true; }
  var track = byId("track");
  track.addEventListener("click", function (e) {
    var t = e.target;
    var g = t.closest(".grp-btn");
    if (g) { setGroup(g.getAttribute("aria-controls").replace(/^grp-/, ""), g.getAttribute("aria-expanded") !== "true"); layoutChanged(); return; }
    var h = t.closest(".row-head");
    if (h) { var n = +h.closest(".row").dataset.n; if (openN === n) clearStudy(); else selectStudy(n, { from: "row" }); return; }
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

  // ---- wayfinding: three ways to move. Normal scrolling, one section picker (the rail on a wide screen, the one
  // button in the foot bar on a phone), and View evidence on the Answer. No Back, no Next, no cue
  var rail = byId("rail"), menu = byId("menu"), where = byId("where"), whereName = byId("where-name");
  var railBtns = [], menuBtns = [], ro = null, curSec = -1;
  function again() { var b = document.querySelector("#s6 [data-again]"); if (b) b.click(); }
  function readStops() {
    checked = root.classList.contains("checked");
    sections = [].slice.call(document.querySelectorAll(".stop[data-stop]")).filter(function (s) { return checked || s.id === "s1"; });
    STOPS = sections.map(function (s) { return { name: s.dataset.stop, cam: CAM[s.dataset.stop] }; });
    NS = STOPS.length; LAST = NS - 1;
    WEIGH = STOPS.map(function (s) { return s.name; }).indexOf("Weighed");
    if (curStop > LAST) curStop = LAST;
    SECS = [];
    STOPS.forEach(function (s, i) {
      if (s.name === "Claim") return;
      var nm = SEC[s.name]; if (!SECS.length || SECS[SECS.length - 1].name !== nm) SECS.push({ name: nm, stop: i });
    });
    rail.textContent = ""; menu.textContent = ""; railBtns = []; menuBtns = []; curSec = -1;
    SECS.forEach(function (c, i) {
      var b = el("button"); b.type = "button"; b.appendChild(el("span", "", c.name)); b.setAttribute("aria-label", "Go to " + c.name);
      b.addEventListener("click", function () { jump(c.stop); }); rail.appendChild(b); railBtns.push(b);
      var m = el("button", "", c.name); m.type = "button";
      m.addEventListener("click", function () { closeMenu(); jump(c.stop); where.focus({ preventScroll: true }); }); menu.appendChild(m); menuBtns.push(m);
    });
    // Check another claim, from anywhere: the same press as the one at the end of the page
    if (checked) {
      var ra = el("button", "rail-again"); ra.type = "button"; ra.appendChild(el("span", "", "Check another claim"));
      ra.addEventListener("click", again); rail.appendChild(ra);
      var ma = el("button", "menu-again", "Check another claim"); ma.type = "button";
      ma.addEventListener("click", function () { closeMenu(); again(); }); menu.appendChild(ma);
    }
    // the site's own pages, from every section
    var links = el("p", "menu-links");
    [["How it works", "/method"], ["About", "/about"], ["Privacy", "/privacy"]].forEach(function (l) { var a = el("a", "", l[0]); a.href = l[1]; links.appendChild(a); });
    menu.appendChild(links);
    if (ro) { ro.disconnect(); sections.forEach(function (s) { ro.observe(s); }); ro.observe(track); }
  }
  function closeMenu() { menu.hidden = true; where.setAttribute("aria-expanded", "false"); }
  where.addEventListener("click", function () { var o = menu.hidden; menu.hidden = !o; where.setAttribute("aria-expanded", String(o)); if (o && menuBtns[curSec]) menuBtns[curSec].focus(); });
  // a press anywhere outside the open list closes it
  addEventListener("pointerdown", function (e) { if (!menu.hidden && !menu.contains(e.target) && !where.contains(e.target)) closeMenu(); }, { passive: true });
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
    if (detail.fresh && !typing) glideToAnswer();
  });
  // during a check: at "found N", N blank sheets stand in the stack (3D only; text mode has no sheets)
  var onStage = function () {};
  document.addEventListener("desk:stage", function (e) { var d = e.detail || {}; if (d.stage === "found") onStage(Math.max(0, Math.min(40, +d.count || 0))); });
  document.addEventListener("desk:home", function () {
    endTween(); clearStudy(); closeMenu();
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
      // on a result nothing is pushed below the fold any more: with no Next to say "keep reading", a block cut by the
      // screen's foot is what tells the reader there is more, so the words run on as they are
      if (checked) return;
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
  // the section in view: the picker names it, the rail and the list mark it
  function updateWay() {
    var P = rawProgress();
    var cur = navTo >= 0 ? navTo : Math.max(0, Math.min(LAST, Math.round(P)));
    var sec = 0; for (var i = 0; i < SECS.length; i++) if (SECS[i].stop <= cur) sec = i;
    if (cur !== curStop && cur !== WEIGH && !tween) clearStudy();
    curStop = cur;
    if (sec !== curSec && SECS.length) {
      curSec = sec;
      railBtns.forEach(function (b, i) { if (i === sec) b.setAttribute("aria-current", "location"); else b.removeAttribute("aria-current"); });
      menuBtns.forEach(function (b, i) { if (i === sec) b.setAttribute("aria-current", "location"); else b.removeAttribute("aria-current"); });
      whereName.textContent = SECS[sec].name;
      where.setAttribute("aria-label", "Sections. You are in " + SECS[sec].name + ". Choose a section");
    }
    if (!tween) armWords();
  }
  // ---- what a reader can see, for keeping their place when the page changes height ----------------
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
  // kept for the scene, which calls it after a cut: there are no Next words any more, only the place to note
  function nextWords() {}
  function armWords() { clearTimeout(wordsT); wordsT = setTimeout(restWords, 150); }
  function restWords() {
    wordsT = 0;
    if (tween || performance.now() - lastScrollAt < 140) { if (!tween) armWords(); return; }
    noteRest();
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

  // ---- scrolling is the reader's own: the page never settles itself onto a stop ----------------
  // 160 ms after the scroll stops the place is noted, nothing more: no snap, no finishing a glide the reader cut short.
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
  function settleStop() { settleT = 0; glideLeft = null; restY = window.scrollY; runLen = 0; }
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
  // the scene's own code (static/desk3d.js) comes with the library, never with the first screen
  var SCENE = (glEl && glEl.dataset.scene) || "/static/desk3d.js", sceneP = null;
  function loadScene() {
    return new Promise(function (res) {
      var sc = document.createElement("script");
      sc.onload = function () { res(typeof window.__evidentDesk3d === "function"); };
      sc.onerror = function () { res(false); };
      sc.async = true; sc.src = SCENE; document.body.appendChild(sc);
    });
  }
  function loadLib(tries) {
    var sc = document.createElement("script"), done = false;
    var timer = setTimeout(function () { fail(); }, 8000);
    function fail() { if (done) return; done = true; clearTimeout(timer); sc.onload = sc.onerror = null; if (tries > 0) loadLib(tries - 1); else fallback(); }
    sc.onload = function () { if (done) return; done = true; clearTimeout(timer); if (typeof THREE === "undefined") { fallback(); return; } sceneP.then(function (ok) { if (ok) waitFonts(); else fallback(); }); };
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
  function begin() { woodImg = new Image(); woodImg.decoding = "async"; woodImg.src = WOOD; sceneP = loadScene(); loadLib(1); }
  if (document.readyState === "complete") begin(); else addEventListener("load", begin);

  // the 3D desk (static/desk3d.js) reaches these through one accessor each
  var O3 = {
    get CHECK() { return CHECK; }, set CHECK(v) { CHECK = v; },
    get LAST() { return LAST; }, set LAST(v) { LAST = v; },
    get NS() { return NS; }, set NS(v) { NS = v; },
    get STOPS() { return STOPS; }, set STOPS(v) { STOPS = v; },
    get WEIGH() { return WEIGH; }, set WEIGH(v) { WEIGH = v; },
    get anchors() { return anchors; }, set anchors(v) { anchors = v; },
    get byId() { return byId; }, set byId(v) { byId = v; },
    get check() { return check; }, set check(v) { check = v; },
    get checked() { return checked; }, set checked(v) { checked = v; },
    get clearStudy() { return clearStudy; }, set clearStudy(v) { clearStudy = v; },
    get coarse() { return coarse; }, set coarse(v) { coarse = v; },
    get curStop() { return curStop; }, set curStop(v) { curStop = v; },
    get drawImpl() { return drawImpl; }, set drawImpl(v) { drawImpl = v; },
    get endTween() { return endTween; }, set endTween(v) { endTween = v; },
    get fallback() { return fallback; }, set fallback(v) { fallback = v; },
    get gotoImpl() { return gotoImpl; }, set gotoImpl(v) { gotoImpl = v; },
    get handT() { return handT; }, set handT(v) { handT = v; },
    get holds() { return holds; }, set holds(v) { holds = v; },
    get hoverN() { return hoverN; }, set hoverN(v) { hoverN = v; },
    get inGroup() { return inGroup; }, set inGroup(v) { inGroup = v; },
    get isField() { return isField; }, set isField(v) { isField = v; },
    get landY() { return landY; }, set landY(v) { landY = v; },
    get lastScrollAt() { return lastScrollAt; }, set lastScrollAt(v) { lastScrollAt = v; },
    get layout() { return layout; }, set layout(v) { layout = v; },
    get layoutHook() { return layoutHook; }, set layoutHook(v) { layoutHook = v; },
    get nextWords() { return nextWords; }, set nextWords(v) { nextWords = v; },
    get noteRest() { return noteRest; }, set noteRest(v) { noteRest = v; },
    get onCheck() { return onCheck; }, set onCheck(v) { onCheck = v; },
    get onHome() { return onHome; }, set onHome(v) { onHome = v; },
    get onLoad() { return onLoad; }, set onLoad(v) { onLoad = v; },
    get onStage() { return onStage; }, set onStage(v) { onStage = v; },
    get openN() { return openN; }, set openN(v) { openN = v; },
    get params() { return params; }, set params(v) { params = v; },
    get pressing() { return pressing; }, set pressing(v) { pressing = v; },
    get prevY() { return prevY; }, set prevY(v) { prevY = v; },
    get progY() { return progY; }, set progY(v) { progY = v; },
    get rawProgress() { return rawProgress; }, set rawProgress(v) { rawProgress = v; },
    get readyResolve() { return readyResolve; }, set readyResolve(v) { readyResolve = v; },
    get reduce() { return reduce; }, set reduce(v) { reduce = v; },
    get restY() { return restY; }, set restY(v) { restY = v; },
    get root() { return root; }, set root(v) { root = v; },
    get runLen() { return runLen; }, set runLen(v) { runLen = v; },
    get scene3d() { return scene3d; }, set scene3d(v) { scene3d = v; },
    get sections() { return sections; }, set sections(v) { sections = v; },
    get selectStudy() { return selectStudy; }, set selectStudy(v) { selectStudy = v; },
    get sheetPointImpl() { return sheetPointImpl; }, set sheetPointImpl(v) { sheetPointImpl = v; },
    get slowGl() { return slowGl; }, set slowGl(v) { slowGl = v; },
    get statsImpl() { return statsImpl; }, set statsImpl(v) { statsImpl = v; },
    get stripH() { return stripH; }, set stripH(v) { stripH = v; },
    get touching() { return touching; }, set touching(v) { touching = v; },
    get tween() { return tween; }, set tween(v) { tween = v; },
    get txt() { return txt; }, set txt(v) { txt = v; },
    get updateWay() { return updateWay; }, set updateWay(v) { updateWay = v; },
    get wake() { return wake; }, set wake(v) { wake = v; },
    get woodImg() { return woodImg; }, set woodImg(v) { woodImg = v; }
  };
  function start() {
    if (typeof window.__evidentDesk3d !== "function") { fallback(); return; }
    window.__evidentDesk3d(O3);
  }
})();
