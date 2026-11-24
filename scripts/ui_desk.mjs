#!/usr/bin/env node
/**
 * The desk front's promises that only a real browser can prove, against the
 * fixture server (scripts/ui_fixture_server.py). Exits non-zero if any check
 * fails. No model call and no network: the fixture answers every check.
 *
 *   node scripts/ui_desk.mjs [baseUrl]
 *
 * 4. When the browser drops the drawing context, the page goes to text mode on
 *    the same stop and stays there, every word of the card readable.
 * 8. The phone keyboard and the address bar change only the window's height:
 *    the claim box stays on screen, Enter sends, the 3D picture is not
 *    reallocated or reframed, and closing the keyboard returns to the place.
 *    On a desktop a shorter window is only that: the picture follows, the bar stays.
 * 12. While a check runs there is one Cancel: it keeps focus while lines print,
 *    sits outside the live log, and pressing it ends the check.
 * 13. Focus after a check: a keyboard check lands on the verdict, Tab goes on
 *    from the answer and not back to the claim box, Enter does not send again;
 *    an error lands on its title; Escape in the place menu returns to its button.
 *    Words changed in the claim box during a check keep focus there when it lands.
 * 14. Every control Tab reaches on a result is fully clear of the strip and the
 *    bar, at 390 and 1440, in 3D and text mode.
 * 15. On a result the first Tab reaches "Skip to the answer", drawn only then;
 *    Enter takes the reader to the Answer stop with focus on the verdict, in 3D,
 *    in text mode and with scripts off. Every screen has one h1, on both paths.
 * 16. The strip is the banner, the site links a Site navigation, the last stop's
 *    closing lines the footer, and Limitations a heading and a region of that name.
 * 18. The service worker: a returning visit to a held server (10 s) paints the
 *    kept home page within about 3 s and the late answer refreshes the copy; with
 *    a fast server the fresh page arrives; a first visit waits for the network;
 *    offline, the home page paints from the copy with its stylesheet and scripts;
 *    with a copy kept, a home answer of 503 or 429 shows the copy and is never
 *    stored, while a first visit gets the server's answer as before.
 * 19. The warm-up: on a saved answer in 3D the scene's programs and textures are
 *    sent to the GPU ahead of the press, after the answer is readable; the first
 *    Next then compiles no program and sends no texture it already had; a press
 *    before the warm-up is done still lands on the next stop; the home page has
 *    no warm-up.
 * 20. The update path: a browser holding the live site's worker (static/sw.js at
 *    f10ef54, cache "evident-v118", served by the fixture from git) visits this
 *    build: no console error, no failed request, the new worker takes over, the
 *    old cache is gone, every shell file is in the new cache under its stamped
 *    address, and offline the home page paints; a kept copy and a server taking
 *    4 s: the copy paints at about 2.5 s and the fresh page is stored.
 * 17. The result screens (q10-nav): every study shows Read abstract and Open
 *    PubMed at 44px on every width; three ways to move (scrolling, one section
 *    picker, View evidence) and no cue, Back or Next; scrolling stays where the
 *    reader leaves it; no screen looks finished when it is not; the answer and the
 *    section headings come before the scenery; the four section names.
 * 21. Snappy: no picture drawn while the home page scrolls or on a settled screen
 *    for 2 s; on the phone profile (CPU slowed 4 times, the Mac's own GPU) the first
 *    scroll from the Answer to Studies after a fresh answer moves the camera within
 *    300 ms and glides, and a fresh answer is on screen within 2.5 s of arriving;
 *    once warmed that first scroll sends no card
 *    face; a sheet is three draw calls; the claim box is usable as the scripts are in.
 * 22. The joined release (q10-rel), at five sizes in 3D and text mode: the home screen
 *    holds the claim box and a check lands on the Answer; no colour q10-look removed on
 *    any result section; both study actions, one picker and one View evidence at 44px;
 *    the first section move after an answer starts within 300 ms; on a phone the camera
 *    does not jump at the start of a check; the press of "Check the research" paints
 *    within 100 ms.
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
const SAVED = ["Creatine improves cognitive performance", "Fish oil lowers blood pressure", "Green tea helps weight loss", "Magnesium improves sleep"];
const LIVE = "Vitamin D supplements prevent colds";
const SLOW = "Slow claim for cancel test"; // the fixture server holds this check open until it is cancelled
const UNSEEN = "Zinc lozenges shorten colds"; // never checked by any test here: offline, a claim this browser has checked before shows its saved copy, not the error
const browser = await chromium.launch({ args: ARGS });
const results = [];
const must = (c, m) => { if (!c) throw new Error(m); };

async function test(name, fn, { w = 390, h = 844, reduce = false, touch = false, sw = false } = {}) {
  if (process.env.UI_ONLY && !name.startsWith(process.env.UI_ONLY)) return;  // UI_ONLY=18: run only the checks whose name starts so
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: reduce ? "reduce" : "no-preference", serviceWorkers: sw ? "allow" : "block", hasTouch: touch });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/net::ERR_INTERNET_DISCONNECTED|Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => (window.__csp = window.__csp || []).push(e.violatedDirective)));
  try {
    await fetch(base + "/__fixture/reset");
    await fn(page, ctx);
    const csp = await page.evaluate(() => window.__csp || []);
    must(!errors.length && !csp.length, "page errors: " + errors.concat(csp).join(" | "));
    results.push(true); console.log(`PASS ${name}`);
  } catch (e) {
    results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally {
    await ctx.close();
  }
}
const ready = (page) => page.evaluate(() => Promise.race([window.__evident.ready(), new Promise((r) => setTimeout(r, 20000))]));
const send = async (page, claim) => { await page.fill("#claim-input", claim); await page.press("#claim-input", "Enter"); };

// ---- 4. The browser drops the drawing context ----------------------------------------
// Lost on purpose (WEBGL_lose_context) at the Answer and at Weighed: the page is in text
// mode at once, on the same stop, the desk is gone, the verdict and the short answer read
// at 4.5:1 or better on what is behind them, and two seconds later it is still text mode.
const lum = (c) => { const m = c.match(/[\d.]+/g).map(Number); const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]); };
const ratio = ([a, b]) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
for (const [w, h] of [[390, 844], [1440, 900]]) {
  for (const [k, name] of [[2, "Answer"], [4, "Weighed"]]) {
    await test(`a lost drawing context leaves the page in text mode on ${name}, every word readable, ${w}x${h}`, async (page) => {
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
      must((await page.evaluate(() => window.__evident.stats().mode)) === "3d", "the desk did not start in 3D");
      await page.evaluate((n) => window.__evident.goto(n), k); await page.waitForTimeout(1200);
      await page.evaluate(() => { const c = document.getElementById("gl"); (c.getContext("webgl2") || c.getContext("webgl")).getExtension("WEBGL_lose_context").loseContext(); });
      await page.waitForTimeout(600);
      const m = await page.evaluate(() => {
        const bg = (e) => { for (let x = e; x; x = x.parentElement) { const c = getComputedStyle(x).backgroundColor; if (!/rgba\(0, 0, 0, 0\)|transparent/.test(c)) return c; } return getComputedStyle(document.body).backgroundColor; };
        const pair = (s) => { const e = document.querySelector(s); return [getComputedStyle(e).color, bg(e)]; };
        const s = window.__evident.stats();
        return { mode: s.mode, stop: s.stop, stage: getComputedStyle(document.getElementById("stage")).display, v: pair(".vcard .v"), t: pair(".vcard .t"), q: pair(".vcard .q") };
      });
      must(m.mode === "text" && m.stop === k, `mode ${m.mode} on stop ${m.stop}, wanted text on ${k}`);
      must(m.stage === "none", "the desk's canvas is still shown");
      for (const [what, p] of [["verdict", m.v], ["short answer", m.t], ["claim", m.q]]) must(ratio(p) >= 4.5, `the ${what} reads at ${ratio(p).toFixed(2)}:1 (${p.join(" on ")})`);
      await page.waitForTimeout(2000);
      const later = await page.evaluate(() => window.__evident.stats().mode);
      must(later === "text", `two seconds later the page is in mode ${later}`);
    }, { w, h });
  }
}

// ---- 8. The phone keyboard and the address bar ------------------------------------------
// A touch phone, the claim box focused, the window's height dropped as the keyboard does
// (interactive-widget=resizes-content): the box and what is typed stay on screen, Enter
// sends, the 3D picture keeps its drawing buffer (no reallocation, no reframing), and when
// the keyboard closes the page is back where it was. The address bar sliding (a 74 px
// change, four times) reallocates nothing and leaves no strip uncovered. A turn still
// draws afresh.
async function phone(url, fn) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: "block" });
  const page = await ctx.newPage(); const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => { window.__allocs = 0; document.addEventListener("DOMContentLoaded", () => { const c = document.getElementById("gl"); if (c) new MutationObserver((m) => { window.__allocs += m.length; }).observe(c, { attributes: true, attributeFilter: ["width", "height"] }); }); });
  try { await fetch(base + "/__fixture/reset"); await page.goto(base + url, { waitUntil: "load" }); await ready(page); await page.waitForTimeout(600); await fn(page); must(!errors.length, "page errors: " + errors.join(" | ")); }
  finally { await ctx.close(); }
}
// the box is on screen when it is below the top strip and above the bar at the foot (when the bar shows)
const kb = (page) => page.evaluate(() => { const r = (s) => document.querySelector(s).getBoundingClientRect(); const c = document.getElementById("gl"), b = r(".claim-box");
  const bar = document.getElementById("bar"), bs = getComputedStyle(bar), foot = bs.display !== "none" && bs.visibility !== "hidden" ? r("#bar").top : innerHeight;
  return { y: Math.round(scrollY), allocs: window.__allocs, buf: c ? c.width + "x" + c.height : "", canvasBottom: c && getComputedStyle(document.getElementById("stage")).display !== "none" ? Math.round(c.getBoundingClientRect().bottom) : innerHeight,
    boxOn: b.top >= r("#strip").bottom - 1 && b.bottom <= foot + 0.5, box: `${Math.round(b.top)}..${Math.round(b.bottom)} between ${Math.round(r("#strip").bottom)} and ${Math.round(foot)}`, value: document.getElementById("claim-input").value, mode: window.__evident.stats().mode }; });
for (const path of ["/", "/?nogl=1", "/?q=" + encodeURIComponent(SAVED[0])]) {
  await test(`the keyboard keeps the claim box and the picture, and closing it returns to the place, ${path}`, async () => {
    await phone(path, async (page) => {
      if (path.includes("q=")) { await page.evaluate(() => window.__evident.goto(1)); await page.waitForTimeout(1200); }
      await page.tap("#claim-input"); await page.fill("#claim-input", ""); await page.waitForTimeout(300);
      const a = await kb(page);
      for (const h of [500, 420, 340]) {
        await page.setViewportSize({ width: 390, height: h }); await page.waitForTimeout(500);
        if (h === 500) { await page.keyboard.type("Does walking lower blood sugar"); await page.waitForTimeout(200); }
        const s = await kb(page);
        must(s.boxOn && s.value === "Does walking lower blood sugar", `at ${h} high the box (${s.box}) is ${s.boxOn ? "on" : "off"} screen and holds "${s.value}"`);
        must(s.allocs === a.allocs && s.buf === a.buf, `at ${h} high the drawing buffer went ${a.buf} to ${s.buf} (${s.allocs - a.allocs} reallocations)`);
        must(s.canvasBottom >= h, `at ${h} high the picture ends at ${s.canvasBottom}`);
      }
      await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(700);
      const z = await kb(page);
      must(z.y === a.y, `the page was at ${a.y} and is at ${z.y} after the keyboard closed`);
      must(z.allocs === a.allocs, `${z.allocs - a.allocs} reallocations over the whole keyboard`);
      await page.setViewportSize({ width: 390, height: 340 }); await page.waitForTimeout(400);
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => !document.getElementById("reading").hidden || document.documentElement.classList.contains("checked") && location.search.includes("walking"), null, { timeout: 15000 });
    });
  });
}
await test("the address bar sliding reallocates nothing and uncovers nothing; a turn draws afresh", async () => {
  await phone("/?q=" + encodeURIComponent(SAVED[0]), async (page) => {
    await page.evaluate(() => window.__evident.goto(4)); await page.waitForTimeout(1200);
    const a = await kb(page); must(a.mode === "3d", "not in 3D: " + a.mode);
    for (let i = 0; i < 4; i++) for (const h of [770, 844]) {
      await page.setViewportSize({ width: 390, height: h }); await page.waitForTimeout(250);
      const s = await kb(page); must(s.canvasBottom >= h, `at ${h} high the picture ends at ${s.canvasBottom}`);
    }
    const z = await kb(page); must(z.allocs === a.allocs, `${z.allocs - a.allocs} reallocations over eight slides`);
    await page.setViewportSize({ width: 844, height: 390 }); await page.waitForTimeout(800);
    const t = await kb(page); must(t.allocs > z.allocs && t.buf !== z.buf, `a turn kept the buffer at ${t.buf}`);
  });
});

// J2: a desktop has no phone keyboard. With the claim box focused on a result, a window dragged shorter
// (1440x900 to 1440x600) and back keeps the bar at the foot, never sets the keyboard state,
// and the picture follows the window: the stage is not pinned and the buffer is drawn afresh.
await test("a desktop window dragged shorter keeps the section picker (the rail), never sets the keyboard state, and the picture follows the window", async (page) => {
  await page.addInitScript(() => { window.__kbdSeen = 0; window.__allocs = 0; document.addEventListener("DOMContentLoaded", () => {
    new MutationObserver(() => { if (document.documentElement.classList.contains("kbd")) window.__kbdSeen++; }).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    const c = document.getElementById("gl"); if (c) new MutationObserver((m) => { window.__allocs += m.length; }).observe(c, { attributes: true, attributeFilter: ["width", "height"] }); }); });
  // a saved result at its first stop: the claim box with the bar at the foot (home has no bar)
  await page.goto(base + "/?q=" + encodeURIComponent(SAVED[0]), { waitUntil: "load" }); await ready(page);
  await page.evaluate(() => window.__evident.goto(1)); await page.waitForTimeout(1500);
  const touch = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
  must(!touch, "this desktop context reports a touch screen");
  await page.click("#claim-input"); await page.keyboard.type("Does walking lower blood sugar"); await page.waitForTimeout(300);
  const look = () => page.evaluate(() => { const c = document.getElementById("gl"), bar = document.getElementById("bar"), bs = getComputedStyle(bar), st = document.getElementById("stage");
    return { mode: window.__evident.stats().mode, f: document.activeElement && document.activeElement.id, kbd: document.documentElement.classList.contains("kbd"), seen: window.__kbdSeen,
      bar: (bs.display !== "none" && bs.visibility !== "hidden" && bar.getBoundingClientRect().height > 0) || (getComputedStyle(document.getElementById("rail")).display !== "none" && document.getElementById("rail").getBoundingClientRect().height > 0), pinned: st.style.height,
      buf: c.width + "x" + c.height, allocs: window.__allocs, stageH: Math.round(st.getBoundingClientRect().height), bottom: Math.round(c.getBoundingClientRect().bottom), vh: innerHeight }; });
  const a = await look();
  must(a.mode === "3d" && a.f === "claim-input" && a.bar, `at the start: mode ${a.mode}, focus #${a.f}, bar shown ${a.bar}`);
  for (const h of [600, 900]) {
    await page.setViewportSize({ width: 1440, height: h }); await page.waitForTimeout(2500);
    const s = await look();
    must(s.f === "claim-input", `at ${h} high focus moved to #${s.f}`);
    must(s.bar && !s.kbd && s.seen === 0, `at ${h} high the bar is ${s.bar ? "shown" : "hidden"} and the keyboard state was set ${s.seen} time(s)`);
    must(!s.pinned && s.bottom >= h && s.stageH <= h, `at ${h} high the stage is pinned at "${s.pinned}", ${s.stageH} px tall, the picture ends at ${s.bottom}`);
    must(h === 600 ? s.buf !== a.buf && s.allocs > a.allocs : s.buf === a.buf, `at ${h} high the drawing buffer went ${a.buf} to ${s.buf}`);
  }
}, { w: 1440, h: 900 });

// ---- 12. One Cancel that stays ---------------------------------------------------------------
await test("while a check prints its lines, the focused Cancel stays the same node with focus, outside the live log, and ends the check", async (page) => {
  await page.goto(base + "/?nogl=1", { waitUntil: "load" }); await ready(page);
  await send(page, LIVE);
  await page.waitForFunction(() => !document.getElementById("cancel-row").hidden, null, { timeout: 15000 });
  await page.evaluate(() => { window.__cancel = document.getElementById("cancel-live"); window.__cancel.focus(); window.__lines0 = document.querySelectorAll("#reading li").length; });
  await page.waitForFunction(() => document.querySelectorAll("#reading li").length > window.__lines0 || document.documentElement.classList.contains("checked"), null, { timeout: 15000 });
  const s = await page.evaluate(() => ({ same: document.getElementById("cancel-live") === window.__cancel, focused: document.activeElement === window.__cancel || document.documentElement.classList.contains("checked"),
    inLog: !!document.querySelector("#reading #cancel-live"), lines: document.querySelectorAll("#reading li").length, l0: window.__lines0 }));
  must(s.same && s.focused && !s.inLog, `same node ${s.same}, focus kept ${s.focused}, inside the live log ${s.inLog} (lines ${s.l0} to ${s.lines})`);
});
await test("Cancel ends a running check and is put away", async (page) => {
  await page.goto(base + "/?nogl=1", { waitUntil: "load" }); await ready(page);
  await send(page, SLOW);
  await page.waitForFunction(() => !document.getElementById("cancel-row").hidden, null, { timeout: 15000 });
  await page.click("#cancel-live");
  await page.waitForFunction(() => !document.getElementById("submit-btn").disabled, null, { timeout: 10000 });
  const hid = await page.evaluate(() => document.getElementById("cancel-row").hidden);
  must(hid, "the Cancel row stayed up after the check ended");
});

// ---- 13. Focus after a check ---------------------------------------------------------------
for (const path of ["/", "/?nogl=1"]) {
  await test(`a keyboard check lands focus on the verdict, Tab goes forward from it, Enter does not send again, ${path === "/" ? "3D" : "text mode"}`, async (page) => {
    let checks = 0; page.on("request", (r) => { if (r.url().includes("/api/check")) checks++; });
    await page.goto(base + path, { waitUntil: "load" }); await ready(page);
    await page.click("#claim-input"); await page.keyboard.type(LIVE); await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 });
    await page.waitForTimeout(400);
    const f = await page.evaluate(() => document.activeElement && document.activeElement.id);
    must(f === "stamp", `focus is on #${f}`);
    const n = checks;
    await page.keyboard.press("Enter"); await page.waitForTimeout(800);
    must(checks === n, `Enter on the answer sent ${checks - n} more check(s)`);
    await page.keyboard.press("Tab");
    const t = await page.evaluate(() => { const a = document.activeElement; return { id: a.id, cls: a.className, inAsk: !!a.closest("#ask"), inChecker: !!a.closest("form.ask") }; });
    must(!t.inAsk && !t.inChecker, `the first Tab went back to the claim box area (${t.id || t.cls})`);
  });
}
// J1: a reader changing the words in the claim box while a check runs keeps focus, caret and
// words when the answer lands, and the view stays where it is: no jump to the answer and no
// glide (a reader typing is moving); the answer is still drawn and announced. This holds for a
// fresh answer and for a cached one. Words left as sent (focus still in the box) land on the
// verdict and glide to the answer as before. The gated claim holds a fresh check open; a cached
// one is held by delaying the request in the browser.
const GATED = "Gated claim for stage test";
const release = async (...gates) => { for (const g of gates) await fetch(`${base}/__fixture/release/${g}`); };
const J1_NAME = { typed: "words typed during a check keep focus, caret and place when the answer lands",
  sent: "words left as sent land focus on the verdict and glide to the answer",
  cached: "words typed while a cached answer comes back keep focus, caret and place" };
for (const [path, kind] of [["/", "typed"], ["/?nogl=1", "typed"], ["/", "sent"], ["/?nogl=1", "sent"], ["/?nogl=1", "cached"]]) {
  await test(`${J1_NAME[kind]}, ${path === "/" ? "3D" : "text mode"}`, async (page) => {
    const typed = kind !== "sent";
    let hold = null, bodies = [];
    if (kind === "cached") {
      // run the claim once to the end, so the fixture has it cached, then open the page afresh
      await release("query", "found", "done");
      await page.goto(base + path, { waitUntil: "load" }); await ready(page);
      await send(page, GATED);
      await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 60000 });
      let open; hold = new Promise((r) => { open = r; }); hold.open = open;
      await page.route((u) => u.pathname.startsWith("/api/check"), async (route) => { await hold; await route.continue(); });
      page.on("response", (r) => { if (new URL(r.url()).pathname.startsWith("/api/check")) bodies.push(r.text().catch(() => "")); });
    }
    await page.goto(base + path, { waitUntil: "load" }); await ready(page); await page.waitForTimeout(600);
    await send(page, GATED);
    await page.waitForFunction(() => document.getElementById("submit-btn").disabled, null, { timeout: 15000 });
    if (kind !== "cached") await release("query");
    await page.focus("#claim-input");
    if (typed) { await page.press("#claim-input", "End"); await page.keyboard.type(" today"); }
    const y0 = await page.evaluate(() => scrollY);
    if (kind === "cached") hold.open(); else await release("found", "done");
    await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 60000 });
    if (typed) await page.waitForTimeout(path === "/" ? 8000 : 3000);
    else await page.waitForFunction(() => window.__evident.stats().stop === 2, null, { timeout: 45000 }).catch(() => {});
    const look = () => page.evaluate(() => { const a = document.activeElement, i = document.getElementById("claim-input"), r = i.getBoundingClientRect();
      return { f: a && a.id, value: i.value, caret: i.selectionStart, stop: window.__evident.stats().stop, y: Math.round(scrollY),
        top: Math.round(r.top), bottom: Math.round(r.bottom), vh: innerHeight, stamp: document.getElementById("stamp").textContent.trim(),
        echo: document.getElementById("claim-echo").textContent, said: document.getElementById("announce").textContent }; });
    const s = await look();
    must(s.stamp && s.echo.includes(GATED) && /^Verdict: /.test(s.said), `the answer was not drawn and announced (verdict "${s.stamp}", claim "${s.echo}", said "${s.said}")`);
    if (kind === "cached") { const t = (await Promise.all(bodies)).join(""); must(/"cached":\s*true/.test(t), "the second check did not come back cached"); }
    if (!typed) { must(s.f === "stamp" && s.stop === 2, `focus is on #${s.f} at stop ${s.stop}`); return; }
    must(s.f === "claim-input" && s.value === GATED + " today" && s.caret === s.value.length, `focus on #${s.f}, the box holds "${s.value}" with the caret at ${s.caret}`);
    must(s.stop === 1 && s.y === Math.round(y0) && s.top >= 0 && s.bottom <= s.vh, `the view moved: stop ${s.stop}, scrollY ${Math.round(y0)} to ${s.y}, the box at ${s.top}..${s.bottom} of ${s.vh}`);
    await page.keyboard.type(" now"); await page.waitForTimeout(path === "/" ? 3000 : 800);
    const z = await look();
    must(z.f === "claim-input" && z.value === GATED + " today now" && z.stop === 1, `after one more word: focus on #${z.f}, the box holds "${z.value}", stop ${z.stop}`);
  });
}
// J1 follow-ups (review of batch 2): the reader is changing the claim if any input reached the box
// after the check started, even a single space (M1) or dictation (L3); the page stays where they
// are, even scrolled (L1); and words kept that way survive Back then Forward, and Edit claim (L2).
// Text mode, where nothing moves the page on its own.
const j1Start = async (page, { dictate = false } = {}) => {
  if (dictate) await page.addInitScript(() => { window.SpeechRecognition = class { start() { window.__rec = this; } stop() { if (this.onend) this.onend(); } }; });
  await page.goto(base + "/?nogl=1", { waitUntil: "load" }); await ready(page); await page.waitForTimeout(400);
  await send(page, GATED);
  await page.waitForFunction(() => document.getElementById("submit-btn").disabled, null, { timeout: 15000 });
  await release("query");
};
const j1Land = async (page) => { await release("found", "done"); await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 60000 }); await page.waitForTimeout(2500); };
const j1Look = (page) => page.evaluate(() => ({ f: document.activeElement && document.activeElement.id, value: document.getElementById("claim-input").value,
  stop: window.__evident.stats().stop, y: Math.round(scrollY), stamp: document.getElementById("stamp").textContent.trim() }));
await test("M1: a single space typed during a check counts as changing the claim: focus, words and place stay", async (page) => {
  await j1Start(page);
  await page.focus("#claim-input"); await page.press("#claim-input", "End"); await page.keyboard.type(" ");
  await j1Land(page);
  const s = await j1Look(page);
  must(s.stamp && s.f === "claim-input" && s.value === GATED + " " && s.stop === 1, `focus on #${s.f}, the box holds "${s.value}", stop ${s.stop}`);
});
await test("L1: an answer that lands while the reader types leaves a scrolled page where it is", async (page) => {
  await j1Start(page);
  await page.focus("#claim-input"); await page.press("#claim-input", "End"); await page.keyboard.type(" today");
  await page.evaluate(() => window.scrollTo({ top: 40, behavior: "instant" })); await page.waitForTimeout(300);
  const y0 = await page.evaluate(() => Math.round(scrollY));
  await j1Land(page);
  const s = await j1Look(page);
  must(y0 > 0 && s.f === "claim-input" && s.y === y0, `scrollY ${y0} became ${s.y} (focus on #${s.f})`);
}, { w: 390, h: 480 });
await test("L2: words kept from a check survive Back then Forward, and Edit claim", async (page) => {
  await j1Start(page);
  await page.focus("#claim-input"); await page.press("#claim-input", "End"); await page.keyboard.type(" today");
  await j1Land(page);
  await page.goBack(); await page.waitForFunction(() => !document.documentElement.classList.contains("checked"), null, { timeout: 10000 });
  await page.goForward(); await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 10000 }); await page.waitForTimeout(800);
  const a = await j1Look(page);
  must(a.value === GATED + " today", `after Back and Forward the box holds "${a.value}"`);
  await page.evaluate(() => document.getElementById("edit-claim").click()); await page.waitForTimeout(800);
  const b = await j1Look(page);
  must(b.value === GATED + " today" && b.f === "claim-input", `after Edit claim the box holds "${b.value}", focus on #${b.f}`);
});
await test("L3: words dictated during a check count as changing the claim and stay when the answer lands", async (page) => {
  await j1Start(page, { dictate: true });
  await page.click("#mic-btn");
  await page.evaluate((t) => window.__rec.onresult({ results: [[{ transcript: t }]] }), GATED + " today");
  await j1Land(page);
  const s = await j1Look(page);
  must(s.stamp && s.value === GATED + " today" && s.stop === 1, `while dictating the box holds "${s.value}", stop ${s.stop}, focus on #${s.f}`);
});
await test("an error lands focus on its title", async (page) => {
  await page.goto(base + "/?nogl=1", { waitUntil: "load" }); await ready(page);
  await page.context().setOffline(true);
  await page.click("#claim-input"); await page.keyboard.type(LIVE); await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.getElementById("error").hidden, null, { timeout: 10000 });
  const f = await page.evaluate(() => document.activeElement && document.activeElement.id);
  await page.context().setOffline(false);
  must(f === "error-title", `focus is on #${f}`);
});
await test("Escape in the place menu returns focus to the place button", async (page) => {
  await page.goto(base + "/?nogl=1&q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page);
  await page.evaluate(() => window.__evident.goto(3)); await page.waitForTimeout(800);
  await page.focus("#where"); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
  const open = await page.evaluate(() => !document.getElementById("menu").hidden);
  await page.keyboard.press("Escape"); await page.waitForTimeout(200);
  const s = await page.evaluate(() => ({ hidden: document.getElementById("menu").hidden, f: document.activeElement && document.activeElement.id }));
  must(open && s.hidden && s.f === "where", `menu opened ${open}, closed ${s.hidden}, focus on #${s.f}`);
});

// ---- 14. Focus never hides under the bar or the strip ---------------------------------------
for (const [w, h] of [[390, 844], [1440, 900]]) for (const path of ["/", "/?nogl=1"]) {
  await test(`every control Tab reaches on a result is clear of the strip and the bar, ${w}x${h} ${path === "/" ? "3D" : "text mode"}`, async (page) => {
    await page.goto(base + (path === "/" ? "/?" : "/?nogl=1&") + "q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page);
    await page.waitForTimeout(1500);
    const hidden = []; let seen = 0;
    for (let i = 0; i < 70; i++) {
      await page.keyboard.press("Tab"); await page.waitForTimeout(120);
      const r = await page.evaluate(() => {
        const a = document.activeElement; if (!a || a === document.body) return null;
        // the skip link is drawn over the strip on purpose, above it, while it has focus
        if (a.closest(".strip, .bar, #menu, dialog, .skip")) return { skip: true };
        const b = a.getBoundingClientRect(); if (!b.width || !b.height) return { skip: true };
        const strip = document.querySelector(".strip").getBoundingClientRect().bottom;
        const bar = document.querySelector(".bar"); const barTop = bar && getComputedStyle(bar).visibility !== "hidden" && bar.getBoundingClientRect().height ? bar.getBoundingClientRect().top : innerHeight;
        return { id: a.id || a.className || a.tagName, top: Math.round(b.top), bottom: Math.round(b.bottom), strip: Math.round(strip), barTop: Math.round(barTop) };
      });
      if (!r || r.skip) continue;
      seen++;
      if (r.top < r.strip - 0.5 || r.bottom > r.barTop + 0.5) hidden.push(`${r.id} ${r.top}..${r.bottom} (strip ${r.strip}, bar ${r.barTop})`);
    }
    must(seen > 5 && !hidden.length, `${hidden.length} of ${seen} focused controls under the strip or the bar: ${hidden.slice(0, 4).join("; ")}`);
  }, { w, h });
}

// ---- 15. Skip to the answer, one h1 ------------------------------------------------------------
const focusInfo = (page) => page.evaluate(() => { const a = document.activeElement, r = a.getBoundingClientRect();
  return { id: a.id, w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), bottom: Math.round(r.bottom), vh: innerHeight }; });
for (const path of ["/?", "/?nogl=1&"]) {
  await test(`on a result the first Tab is "Skip to the answer", shown then, and Enter lands on the verdict at the Answer stop, ${path === "/?" ? "3D" : "text mode"}`, async (page) => {
    await page.goto(base + path + "q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page);
    const before = await page.evaluate(() => { const r = document.getElementById("skip").getBoundingClientRect(); return r.width * r.height; });
    must(before <= 1, `the skip link is drawn before it has focus (${before} px²)`);
    await page.evaluate(() => window.__evident.goto(4)); await page.waitForTimeout(1500);
    await page.keyboard.press("Tab");
    const s = await focusInfo(page);
    must(s.id === "skip" && s.w > 60 && s.h >= 44 && s.top >= 0, `first Tab: #${s.id} ${s.w}x${s.h} at ${s.top}`);
    await page.keyboard.press("Enter"); await page.waitForTimeout(2500);
    const v = await focusInfo(page), stop = await page.evaluate(() => window.__evident.stats().stop);
    must(v.id === "stamp" && stop === 2 && v.top >= 0 && v.bottom <= v.vh, `after Enter: focus #${v.id}, stop ${stop}, verdict at ${v.top}..${v.bottom} of ${v.vh}`);
  });
}
{
  // scripts off: the link's own #stamp moves focus to the verdict
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, javaScriptEnabled: false, serviceWorkers: "block" });
  const page = await ctx.newPage(); const name = "with scripts off the skip link still moves focus to the verdict";
  try {
    await fetch(base + "/__fixture/reset");
    await page.goto(base + "/?q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" });
    await page.keyboard.press("Tab");
    const a = await page.evaluate(() => document.activeElement.id);
    await page.keyboard.press("Enter"); await page.waitForTimeout(400);
    const b = await page.evaluate(() => document.activeElement.id);
    must(a === "skip" && b === "stamp", `first Tab #${a}, after Enter #${b}`);
    results.push(true); console.log(`PASS ${name}`);
  } catch (e) { results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`); }
  await ctx.close();
}
const h1s = (page) => page.evaluate(() => [...document.querySelectorAll("h1")].map((e) => e.id));
await test("every screen has one h1 on both paths: home, a saved, fresh and no-studies result, an error, home again", async (page) => {
  const seen = [];
  const want = async (where, id) => { const h = await h1s(page); seen.push(`${where}: ${h.join(",")}`); must(h.length === 1 && h[0] === id, seen.join(" | ")); };
  await page.goto(base + "/", { waitUntil: "load" }); await ready(page); await want("home", "ask-heading");
  await send(page, LIVE); await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 });
  await want("fresh", "claim-echo");
  must(!(await page.evaluate(() => document.getElementById("skip").hidden)), "a fresh result has no skip link");
  await page.goBack(); await page.waitForFunction(() => !document.documentElement.classList.contains("checked"), null, { timeout: 10000 });
  await want("home again", "ask-heading");
  must(await page.evaluate(() => document.getElementById("skip").hidden), "home shows the skip link");
  await send(page, "Copper bracelets ease arthritis pain"); await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 });
  await want("no studies", "claim-echo");
  await page.goto(base + "/?q=" + encodeURIComponent(SAVED[0]), { waitUntil: "load" }); await ready(page); await want("saved", "claim-echo");
  await page.goto(base + "/", { waitUntil: "load" }); await ready(page);
  await page.context().setOffline(true);
  await send(page, UNSEEN); await page.waitForFunction(() => !document.getElementById("error").hidden, null, { timeout: 10000 });
  await page.context().setOffline(false);
  await want("error", "error-title");
});
// ---- 16. The landmarks -------------------------------------------------------------------------
for (const way of ["saved", "fresh"]) {
  await test(`the landmarks on a ${way} result: one banner with the notice, Site navigation, one footer, Limitations as heading and region`, async (page) => {
    if (way === "saved") { await page.goto(base + "/?q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page); }
    else { await page.goto(base + "/", { waitUntil: "load" }); await ready(page); await send(page, LIVE);
      await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 }); await page.waitForTimeout(500); }
    const banner = page.getByRole("banner");
    const n = { banner: await banner.count(), nav: await page.getByRole("navigation", { name: "Site", exact: true }).count(),
      foot: await page.getByRole("contentinfo").count(), region: await page.getByRole("region", { name: "Limitations", exact: true }).count(),
      heading: await page.getByRole("heading", { name: "Limitations", exact: true }).count() };
    const notice = n.banner === 1 && /Not medical advice/.test(await banner.innerText());
    must(notice && n.nav >= 1 && n.foot === 1 && n.region === 1 && n.heading === 1, JSON.stringify({ ...n, notice }));
  });
}

// ---- 17. q10-nav: the result screens. Source buttons, three ways to move, content before scenery, plain names ----
// Replaces the cue row checks (cue in view and as wide, Next drawn as Back beside it, the chevron's dip, a wheel or a
// drag from the Answer landing on Read, the text-mode cue landing on the next stop):
//   N2a  no cue, no Back, no Next, no "Keep reading", no "Back to the short answer"; the phone bar is one row with the
//        picker alone, 56 px at most; on a wide screen no bar and the rail is the picker, the four alone (Check another claim is
//        at the end of the page, check 23a)   (was: the cue row and Next)
//   N2b  nothing animates on its own after an answer lands                                   (was: the chevron dips)
//   N2c  a wheel or a touch drag leaves the page where the reader put it, in 3D and text    (was: lands on Read)
//   N2d  the picker names the section in view, lists the four and Check another claim, works by keyboard (was: the cue click)
const NAV_SIZES = [[375, 812], [375, 667], [320, 568], [1280, 800]];
const SECTIONS = ["Answer", "Studies", "Explanation", "Limitations"];
const openSaved = async (page, w, mode, claim = SAVED[0]) => {
  await page.goto(`${base}/?${mode === "text" ? "nogl=1&" : ""}q=${encodeURIComponent(claim)}`, { waitUntil: "load" }); await ready(page);
  await page.waitForTimeout(800);
};
// the y each section starts at (the first stop it holds), and a jump there through the picker's own path
const secTop = (page, name) => page.evaluate((name) => {
  const map = { Answer: "s2", Studies: "s3", Explanation: "s5", Limitations: "s6" }, s = document.getElementById(map[name]);
  return s ? Math.round(s.getBoundingClientRect().top + scrollY) : null;
}, name);
const pick = async (page, w, name) => {
  if (w > 760) { await page.click(`#rail button[aria-label="Go to ${name}"]`); }
  else { await page.click("#where"); await page.click(`#menu button:text-is("${name}")`); }
  await page.waitForTimeout(2600);
};
// N1: every study's two actions, on every width, in 3D and text mode
for (const [w, h] of [[320, 568], [375, 812], [1280, 800]]) for (const mode of ["3d", "text"]) {
  await test(`N1: every study shows Read abstract and Open PubMed, 44px or more, and the abstract opens in place in one press, ${w}x${h} ${mode}`, async (page) => {
    await openSaved(page, w, mode);
    await page.evaluate(() => document.querySelectorAll(".grp-btn[aria-expanded=false]").forEach((b) => b.click()));
    await page.waitForTimeout(300);
    const rows = await page.evaluate(() => [...document.querySelectorAll("#rows .row")].map((r) => {
      const a = r.querySelector(".abs > summary"), no = r.querySelector(".noabs"), p = r.querySelector("a.pm"), box = (e) => e && e.getBoundingClientRect();
      const vis = (e) => !!e && e.checkVisibility() && box(e).height > 0;
      return { n: r.dataset.n, abs: vis(a) ? box(a).height : 0, absText: a ? a.textContent : "", no: vis(no), pm: vis(p) ? box(p).height : 0, pmText: p ? p.textContent : "",
        target: p && p.target, rel: p && p.rel, details: !!r.querySelector(".row-more:not(.study-go)") };
    }));
    must(rows.length >= 10, `only ${rows.length} rows`);
    const bad = rows.filter((r) => !((r.abs >= 44 && /^Read abstract/.test(r.absText)) || r.no) || r.pm < 44 || r.target !== "_blank" || !/noopener/.test(r.rel) || !/opens in a new tab/.test(r.pmText) || r.details);
    must(!bad.length, `rows without both actions at 44px: ${JSON.stringify(bad.slice(0, 2))}`);
    must(await page.evaluate(() => !/\bDetails\b/.test(document.body.innerText) && !/lift its sheet/i.test(document.body.innerText)), "a visible Details or lift instruction");
    // one press opens the abstract inside the row; the summary then says Hide abstract; a second press closes it
    await page.evaluate(() => document.querySelector("#abs-1 > summary").scrollIntoView({ block: "center" })); await page.waitForTimeout(400);
    await page.click("#abs-1 > summary"); await page.waitForTimeout(300);
    const o = await page.evaluate(() => { const d = document.getElementById("abs-1"), t = d.querySelector(".abs-text"), g = d.querySelector(".study-go");
      return { open: d.open, said: d.querySelector("summary").innerText.trim(), text: t.checkVisibility() && t.getBoundingClientRect().height > 20, inRow: d.closest(".row").id, go: g.checkVisibility() && g.getBoundingClientRect().height >= 20, sheet: !!document.querySelector("dialog.sheet[open]") }; });
    must(o.open && o.said.startsWith("Hide abstract") && o.text && o.inRow === "row-1" && o.go && !o.sheet, `after one press: ${JSON.stringify(o)}`);
    await page.click("#abs-1 > summary"); await page.waitForTimeout(200);
    must(await page.evaluate(() => !document.getElementById("abs-1").open), "a second press did not close the abstract");
  }, { w, h });
}
// N2a and N2b: the controls left on a result, and nothing moving on its own
for (const [w, h] of NAV_SIZES) for (const mode of ["3d", "text"]) {
  await test(`N2a: three ways to move and no others: no cue, Back or Next, one picker, ${w}x${h} ${mode}`, async (page) => {
    await page.addInitScript(() => { window.__anims = 0; document.addEventListener("animationstart", () => window.__anims++, true); });
    await openSaved(page, w, mode);
    const s = await page.evaluate(() => {
      const shown = (e) => !!e && getComputedStyle(e).display !== "none" && e.getBoundingClientRect().height > 0;
      const bar = document.getElementById("bar"), r = bar.getBoundingClientRect(), txt = document.body.innerText;
      return { gone: ["cue", "next", "prev"].filter((id) => document.getElementById(id)), words: ["Scroll down", "Keep reading", "Back to the short answer", "See how we got this answer"].filter((x) => txt.includes(x)),
        bar: shown(bar), barH: Math.round(r.height), barKids: [...bar.children].filter(shown).map((e) => e.id), rail: shown(document.getElementById("rail")),
        railNames: [...document.querySelectorAll("#rail button span")].map((e) => e.textContent), answerBtns: [...document.querySelectorAll("#s2 button")].filter(shown).map((b) => b.id || b.textContent.trim()),
        again: [...document.querySelectorAll("[data-again]")].filter(shown).length };
    });
    must(!s.gone.length && !s.words.length, `still there: ${s.gone.concat(s.words).join(", ")}`);
    if (w <= 760) must(s.bar && s.barH <= 56 && s.barKids.join() === "where" && !s.rail, `the phone bar: shown ${s.bar}, ${s.barH} px, holds ${s.barKids.join()}, rail ${s.rail}`);
    else must(!s.bar && s.rail && s.railNames.join() === SECTIONS.join(), `wide: bar ${s.bar}, rail ${s.rail}: ${s.railNames.join(", ")}`);
    must(s.answerBtns.filter((x) => x !== "meter").join() === "how-answer" && s.again === 1, `on the Answer: ${s.answerBtns.join(", ")}; Check another claim shown ${s.again} times`);
    must(await page.evaluate(() => document.getElementById("how-answer").textContent.trim() === "View evidence"), "the Answer's button is not View evidence");
    await page.waitForTimeout(4000);
    must((await page.evaluate(() => window.__anims)) === 0, "something animated on its own after the answer landed");
  }, { w, h });
}
// N2c: scrolling is native and stays where the reader leaves it
for (const [w, h] of [[375, 812], [1280, 800]]) for (const mode of ["3d", "text"]) for (const how of ["wheel", "touch"]) {
  await test(`N2c: a ${how} from the Answer moves the page and it stays there, no snap to a stop, ${w}x${h} ${mode}`, async (page, ctx) => {
    await openSaved(page, w, mode);
    const y0 = await page.evaluate(() => scrollY);
    if (how === "wheel") { await page.mouse.move(w / 2, h / 2); await page.mouse.wheel(0, 140); }
    else { const c = await ctx.newCDPSession(page); await c.send("Input.synthesizeScrollGesture", { x: Math.round(w / 2), y: Math.round(h / 2), yDistance: -140, gestureSourceType: "touch", speed: 800 }); }
    await page.waitForTimeout(700); const y1 = await page.evaluate(() => scrollY);
    await page.waitForTimeout(2500); const y2 = await page.evaluate(() => scrollY);
    must(y1 - y0 > 60 && y1 - y0 < 260 && Math.abs(y2 - y1) < 1, `scrollY ${y0} then ${y1} then ${y2}`);
  }, { w, h, touch: how === "touch" });
}
// N2d: the picker names where the reader is, lists the four sections, and moves by keyboard
for (const [w, h] of [[375, 812], [1280, 800]]) {
  await test(`N2d: the section picker names the section in view and goes to each one, ${w}x${h}`, async (page) => {
    await openSaved(page, w, "3d");
    const named = () => page.evaluate((wide) => wide ? (document.querySelector("#rail button[aria-current] span") || {}).textContent : document.getElementById("where-name").textContent, w > 760);
    must((await named()) === "Answer", `at the start the picker says ${await named()}`);
    if (w <= 760) {
      must(/^Sections\. You are in Answer/.test(await page.getAttribute("#where", "aria-label")), "the picker does not name itself");
      await page.focus("#where"); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
      const list = await page.evaluate(() => [...document.querySelectorAll("#menu > button")].map((b) => b.textContent));
      must(list.join() === SECTIONS.concat("Check another claim").join(), `the list: ${list.join(", ")}`);
      must(await page.evaluate(() => document.activeElement.textContent === "Answer"), "focus is not on the current section");
      await page.keyboard.press("ArrowDown").catch(() => {}); await page.keyboard.press("Tab"); await page.keyboard.press("Enter"); await page.waitForTimeout(2600);
      must((await named()) === "Studies", `Tab and Enter in the list went to ${await named()}`);
    }
    for (const name of ["Explanation", "Limitations", "Studies", "Answer"]) {
      await pick(page, w, name);
      const y = await page.evaluate(() => Math.round(scrollY)), top = await secTop(page, name), max = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
      must(Math.abs(y - Math.min(top, max)) <= 2 && (await named()) === name, `picked ${name}: scrollY ${y}, its top ${top}, picker says ${await named()}`);
    }
  }, { w, h });
}
// N2e: View evidence goes to Studies
await test("N2e: View evidence on the Answer goes to Studies", async (page) => {
  await openSaved(page, 375, "3d");
  await page.click("#how-answer"); await page.waitForTimeout(2600);
  // (brief N3: the landing may sit a little past the stop's top so the foot crosses words; the heading stays whole under the strip)
  const y = await page.evaluate(() => Math.round(scrollY)), top = await secTop(page, "Studies");
  const hd = await page.evaluate(() => { const q = document.getElementById("e-read").getBoundingClientRect(); return { t: q.top, b: q.bottom, s: document.getElementById("strip").getBoundingClientRect().bottom, vh: innerHeight }; });
  must(Math.abs(y - top) <= hd.vh / 3 && hd.t >= hd.s - 1 && hd.b <= hd.vh && (await page.textContent("#where-name")) === "Studies", `scrollY ${y}, Studies at ${top}, heading ${Math.round(hd.t)} under the strip at ${Math.round(hd.s)}`);
}, { w: 375, h: 812 });
// N2f and N3: at every place the picker or View evidence lands, the next words are cut by the foot of the screen (or
// the bar), or the end of the page is in view; and the words come before the scenery
// (brief N3: eight sizes, and each section is reached through the picker or the rail itself, so the check sees where a
// jump really ends, landShift in desk.js included; the Studies heading stays whole under the strip)
const NAV_GRID = [[320, 568], [360, 640], [375, 667], [375, 812], [390, 844], [412, 915], [1280, 800], [1440, 900]];
const landVia = async (page, w, name) => {
  if (w > 760) await page.click(`#rail button[aria-label="Go to ${name}"]`);
  else { await page.click("#where"); await page.click(`#menu button:text-is("${name}")`); }
  let last = -1, same = 0;
  for (let i = 0; i < 60 && same < 4; i++) { await page.waitForTimeout(150); const y = await page.evaluate(() => Math.round(scrollY)); same = y === last ? same + 1 : 0; last = y; }
  await page.waitForTimeout(300);
};
for (const [w, h] of NAV_GRID) for (const mode of ["3d", "text"]) {
  await test(`N2f/N3: no screen looks finished when it is not, and the words come first, ${w}x${h} ${mode}`, async (page) => {
    await openSaved(page, w, mode);
    const look = () => page.evaluate(() => {
      const bar = document.getElementById("bar"), bt = getComputedStyle(bar).display !== "none" && bar.getBoundingClientRect().height ? bar.getBoundingClientRect().top : innerHeight;
      const end = scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
      // a line of text that starts above the foot and runs past it, or starts inside the last 40% and has more below
      let cut = false, below = false;
      const w = document.createTreeWalker(document.getElementById("track"), NodeFilter.SHOW_TEXT); let t;
      const rg = document.createRange();
      while ((t = w.nextNode())) {
        if (!t.data.trim()) continue; const el = t.parentElement; if (!el || !el.checkVisibility({ visibilityProperty: true }) || el.closest("#vcard,#short-label,details:not([open]) > :not(summary)")) continue;
        // a line the foot crosses, or one run of words with lines both above the foot and below it (the foot in the space between two of its lines)
        rg.selectNodeContents(t); let above = false, under = false;
        for (const q of rg.getClientRects()) { if (q.height < 2) continue; if (q.top < bt - 4 && q.bottom > bt) cut = true; if (q.top < bt - 4) above = true; if (q.bottom > bt) under = true; if (q.top >= bt) below = true; }
        if (above && under) cut = true;
      }
      // a control the foot crosses is cut too (brief N2, step 2: "a line of text or a control")
      for (const c of document.querySelectorAll("#track button, #track a")) { const q = c.getBoundingClientRect(); if (c.checkVisibility() && q.height && q.top < bt - 1 && q.bottom > bt + 1) cut = true; }
      // and so is a drawn panel (3D: a dark block over the desk) whose box runs on past the foot
      for (const c of document.querySelectorAll("#track .stop .inner")) { const q = c.getBoundingClientRect(), bg = getComputedStyle(c).backgroundColor; if (c.checkVisibility() && q.top < bt - 24 && q.bottom > bt + 1 && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") cut = true; }
      // on the Answer the Studies heading wholly above the foot also says there is more (brief N2, step 2)
      const st = document.getElementById("e-read").getBoundingClientRect(), studies = st.height > 0 && st.top >= 0 && st.bottom <= bt;
      return { end, cut, below, studies, bt: Math.round(bt), y: Math.round(scrollY) };
    });
    const bad = [];
    for (const name of SECTIONS) {
      const top = await secTop(page, name); if (top === null) continue;
      await landVia(page, w, name);
      const s = await look();
      // anything still below must show itself at the foot: a line cut by it
      if (!s.end && !s.cut && !(name === "Answer" && s.studies)) bad.push(`${name} at ${s.y}: nothing cut at the foot (${s.bt})`);
      // and the section's own heading is whole under the strip
      const hd = await page.evaluate((n) => { const m = { Answer: null, Studies: "#s3 .eyebrow", Explanation: "#s5 .eyebrow", Limitations: "#h-open" }[n]; const e = m && document.querySelector(m); if (!e) return null;
        const q = e.getBoundingClientRect(); return { t: Math.round(q.top), b: Math.round(q.bottom), s: Math.round(document.getElementById("strip").getBoundingClientRect().bottom) }; }, name);
      if (hd && (hd.t < hd.s - 1 || hd.b > s.bt)) bad.push(`${name}: heading at ${hd.t}..${hd.b}, strip ${hd.s}, foot ${s.bt}`);
    }
    must(!bad.length, bad.join("; "));
    // N3 keeps the four sizes it was written for (NAV_SIZES), and 1440x900 since q10-rel; the grid above is N2f's
    if (!NAV_SIZES.some(([a, b]) => a === w && b === h) && !(w === 1440 && h === 900)) return;
    // N3: the desktop answer starts near the top with how sure fully in view; on a phone each later section's heading
    // starts in the top 28% of the screen with six lines of words under it on that screen
    await page.evaluate((y) => window.scrollTo({ top: y, behavior: "instant" }), await secTop(page, "Answer")); await page.waitForTimeout(mode === "3d" ? 3500 : 900);
    if (w > 760) {
      const a = await page.evaluate(() => { const c = document.getElementById(document.documentElement.classList.contains("has-gl") ? "vcard" : "vcard").getBoundingClientRect(), s = document.getElementById("confidence").getBoundingClientRect(); return { card: Math.round(c.top), st: Math.round(s.top), sb: Math.round(s.bottom), vh: innerHeight }; });
      must(a.card <= 120 && a.st >= 0 && a.sb <= a.vh, `the answer card starts at ${a.card}, how sure at ${a.st}..${a.sb}`);
    } else {
      const far = [];
      for (const [name, sel] of [["Studies", "#s3 .eyebrow"], ["Explanation", "#s5 .eyebrow"], ["Limitations", "#h-open"]]) {
        const top = await secTop(page, name); if (top === null) continue;
        await page.evaluate((y) => window.scrollTo({ top: y, behavior: "instant" }), top); await page.waitForTimeout(900);
        const r = await page.evaluate((sel) => { const e = document.querySelector(sel), q = e.getBoundingClientRect(), bar = document.getElementById("bar").getBoundingClientRect().top;
          // Studies runs over two stops (what was read, then the list), so the lines are counted on the screen, not in one stop
          const rg = document.createRange(), lines = new Set(); const tw = document.createTreeWalker(document.getElementById("track"), NodeFilter.SHOW_TEXT); let t;
          while ((t = tw.nextNode())) { if (!t.data.trim() || !t.parentElement.checkVisibility()) continue; rg.selectNodeContents(t); for (const b of rg.getClientRects()) if (b.top > q.bottom && b.bottom < bar && b.height > 8) lines.add(Math.round(b.top)); }
          return { top: Math.round(q.top), lines: lines.size, vh: innerHeight }; }, sel);
        if (r.top > r.vh * 0.28 || r.lines < 6) far.push(`${name}: heading at ${r.top} of ${r.vh}, ${r.lines} lines under it`);
      }
      must(!far.length, far.join("; "));
    }
  }, { w, h });
}
// N5 (the review of the drafts): (a) on the Answer's first screen the Studies heading is readable above the footer or
// the fold, so the Answer never looks finished; (b) on a wide screen no rail label crosses a text panel at any section,
// and the how-sure panel starts level with the card; (c) with an abstract open, Open PubMed stays beside Hide abstract
// and the abstract opens below them.
// N5a (brief N2, step 2; replaces "the Studies heading shows on the Answer's first screen" at 375x812, 1280 and 1440):
// at rest on the Answer, every line of the card's words is whole (nothing covers or clips it); the screen visibly goes
// on (the Studies heading whole above the foot, or the foot's top edge crossing a line or a control of the Answer);
// and View evidence is in view where both fit. Eight sizes, both modes, a short claim and a 140-character one.
const LONG = "Drinking two cups of green tea every morning lowers blood pressure and protects the heart in adults over sixty who also have type 2 diabetes";
const answerAtRest = () => {
  const R = (e) => e.getBoundingClientRect(), bar = document.getElementById("bar");
  const foot = getComputedStyle(bar).display !== "none" && R(bar).height ? R(bar).top : innerHeight;
  const stripB = R(document.getElementById("strip")).bottom, panel = R(document.querySelector("#cap-answer .inner")), card = document.getElementById("vcard"), cb = R(card);
  const lines = (n) => { const o = [], w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT), rg = document.createRange(); let t, cur = null;
    while ((t = w.nextNode())) { const re = /\S+/g; let m; while ((m = re.exec(t.data))) { rg.setStart(t, m.index); rg.setEnd(t, m.index + m[0].length); const q = rg.getClientRects()[0]; if (!q || !q.width) continue;
      if (!cur || Math.abs(q.top - cur.top) > 2) { cur = { top: q.top, bottom: q.bottom, left: q.left, right: q.right, el: t.parentElement }; o.push(cur); } else { cur.right = Math.max(cur.right, q.right); cur.bottom = Math.max(cur.bottom, q.bottom); } } }
    return o; };
  const hit = (a, b) => a.left < b.right - 0.5 && a.right > b.left + 0.5 && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5, bad = [];
  for (const sel of [".q", ".v", ".t"]) {
    const el = card.querySelector(sel), eb = R(el), clamped = parseInt(getComputedStyle(el).webkitLineClamp, 10) > 0;
    for (const L of lines(el)) {
      if (clamped && L.top >= eb.bottom - 1) continue;   // past a clamp's ellipsis: not drawn
      const why = [];
      if (L.top < stripB - 0.5) why.push("under the strip"); if (L.bottom > foot + 0.5) why.push("past the foot");
      if (L.top < cb.top - 0.5 || L.bottom > cb.bottom + 0.5 || L.left < cb.left - 0.5 || L.right > cb.right + 0.5) why.push("outside the card");
      if (clamped && L.bottom > eb.bottom + 0.5) why.push("cut by its clamp"); if (hit(L, panel)) why.push("under how sure");
      if (why.length) bad.push(`${sel} line at ${Math.round(L.top)}: ${why.join(", ")}`);
    }
  }
  if (document.documentElement.classList.contains("has-gl") && +getComputedStyle(card).opacity < 0.99) bad.push("the card's words are not shown");
  const crossed = [];
  for (const el of document.querySelectorAll("#s2 button, #s2 a, #s2 p, #s2 h1, #s2 h2")) {
    if (!el.checkVisibility() || card.contains(el)) continue;
    if (el.matches("button, a")) { const b = R(el); if (b.top < foot - 1 && b.bottom > foot + 1) crossed.push(el.textContent.trim().slice(0, 24)); continue; }
    for (const L of lines(el)) if (L.el === el && L.top < foot - 1 && L.bottom > foot + 1) crossed.push(el.textContent.trim().slice(0, 24));
  }
  const st = R(document.getElementById("e-read")), ve = R(document.getElementById("how-answer"));
  return { bad, crossed, studies: st.height > 0 && st.top >= stripB - 0.5 && st.bottom <= foot + 0.5, view: ve.height > 0 && ve.top >= stripB - 0.5 && ve.bottom <= foot + 0.5, foot: Math.round(foot) };
};
for (const [w, h] of [[320, 568], [360, 640], [375, 667], [375, 812], [390, 844], [412, 915], [1280, 800], [1440, 900]]) for (const mode of ["3d", "text"]) for (const long of [false, true]) {
  await test(`N5a: at rest on the Answer the card's words are whole, the screen goes on, View evidence where it fits, ${w}x${h} ${mode}${long ? " long claim" : ""}`, async (page) => {
    await openSaved(page, w, mode, long ? LONG : SAVED[0]); await page.waitForTimeout(mode === "3d" ? 2500 : 600);
    const r = await page.evaluate(answerAtRest);
    must(!r.bad.length, r.bad.join("; "));
    must(r.studies || r.crossed.length, `the screen looks finished: the Studies heading is not whole above ${r.foot} and the foot crosses no line or control`);
    // View evidence: always in 3D and on the taller screens; on short phones in text mode the card itself is taller, so (a) and (b) win
    if (mode === "3d" || (h >= 800 && !(w === 375 && long))) must(r.view, "View evidence is not wholly on the first screen");
  }, { w, h });
}
for (const [w, h] of [[1280, 800], [1440, 900]]) for (const mode of ["3d", "text"]) {
  await test(`N5b: no rail label crosses a text panel, and how sure starts level with the card, ${w}x${h} ${mode}`, async (page) => {
    await openSaved(page, w, mode);
    const hits = [];
    for (const k of [2, 3, 4, 5, 6]) {
      await page.evaluate((k) => window.__evident.goto(k), k); await page.waitForTimeout(mode === "3d" ? 1500 : 700);
      hits.push(...await page.evaluate((k) => {
        const rails = [...document.querySelectorAll("#rail button span")].filter((e) => e.checkVisibility()).map((e) => [e.textContent, e.getBoundingClientRect()]);
        const boxes = [...document.querySelectorAll(".stop .inner, .stop .panel, #vcard")].filter((e) => e.checkVisibility()).map((e) => [e.id || e.className, e.getBoundingClientRect()]);
        const o = []; for (const [t, r] of rails) for (const [n, b] of boxes) if (r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top) o.push(`stop ${k}: ${t} over ${n}`); return o;
      }, k));
    }
    must(!hits.length, [...new Set(hits)].join("; "));
    await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(mode === "3d" ? 2500 : 1200);
    const a = await page.evaluate(() => ({ card: document.getElementById("vcard").getBoundingClientRect().top, panel: document.querySelector("#cap-answer .inner").getBoundingClientRect().top }));
    if (mode === "3d") must(a.card <= 120 && a.panel <= 120, `the card starts at ${Math.round(a.card)}, how sure at ${Math.round(a.panel)}`);
  }, { w, h });
}
for (const [w, h] of [[320, 568], [375, 812], [1280, 800]]) for (const mode of ["3d", "text"]) {
  await test(`N5c: with an abstract open, Open PubMed stays beside Hide abstract and the abstract opens below them, ${w}x${h} ${mode}`, async (page) => {
    await openSaved(page, w, mode);
    const look = () => page.evaluate(() => { const d = document.getElementById("abs-1"), s = d.querySelector("summary").getBoundingClientRect(), p = d.closest(".acts").querySelector(".pm").getBoundingClientRect(), row = d.closest(".row").getBoundingClientRect();
      const b = d.querySelector(".abs-body").getBoundingClientRect(); return { side: Math.abs(s.top - p.top) <= 2 && p.left >= s.right, below: !d.open || b.top >= Math.max(s.bottom, p.bottom) - 1, inRow: p.right <= row.right + 0.5, words: d.querySelector("summary").innerText.split("\n")[0].trim() }; });   // the first line: "of study n" is for screen readers
    const shut = await look();
    await page.evaluate(() => document.getElementById("row-1").scrollIntoView({ block: "center" }));
    await page.click("#abs-1 > summary"); await page.waitForTimeout(200);
    const open = await look();
    must(shut.side && shut.inRow && shut.words === "Read abstract", `closed: ${JSON.stringify(shut)}`);
    must(open.side && open.below && open.inRow && open.words === "Hide abstract", `open: ${JSON.stringify(open)}`);
  }, { w, h });
}
// N4: four names wherever a name shows, and one labelled line per study on what it contributes, the same from the
// server's HTML and from app.js (a check run from the claim box), in 3D and text mode   (was: numbered eyebrows)
for (const [w, h] of [[375, 812], [1280, 800]]) for (const mode of ["3d", "text"]) {
  await test(`N4: Answer, Studies, Explanation, Limitations, and What this study contributes on every study, both renderers, ${w}x${h} ${mode}`, async (page) => {
    const read = () => page.evaluate(() => ({
      eyebrows: [...document.querySelectorAll(".stop .eyebrow")].filter((e) => e.checkVisibility()).map((e) => e.textContent.trim()),
      rail: [...document.querySelectorAll("#rail button[aria-label^='Go to ']")].map((b) => b.textContent.trim()),
      lines: [...document.querySelectorAll(".row")].map((r) => [...r.querySelectorAll(".contrib, .caution, .indirect")].map((p) => p.textContent.trim()).join(" | ")),
      label: document.querySelector("#short-label").textContent.trim() }));
    await openSaved(page, w, mode);
    const srv = await read();
    must(srv.eyebrows.join() === "Studies,Explanation,Limitations" && srv.label === "Answer", `names: ${srv.label}; ${srv.eyebrows.join(", ")}`);
    must(srv.rail.join() === SECTIONS.join(), `the rail: ${srv.rail.join(", ")}`);
    must(srv.lines.length >= 3 && srv.lines.every((l) => /^What this study contributes \S/.test(l)), `a study without its line: ${srv.lines.find((l) => !/^What this study contributes \S/.test(l))}`);
    // no indirect label in this release (the engine sends indirect false for every study); the fixture's caution prints
    must(!srv.lines.some((l) => /Indirect/.test(l)) && srv.lines.some((l) => / \| [^|]*small trial/.test(l)), "the fixture's caution did not print, or an Indirect label did");
    await page.goto(`${base}/${mode === "text" ? "?nogl=1" : ""}`, { waitUntil: "load" }); await ready(page);
    await page.click("#claim-input"); await page.keyboard.type(SAVED[0]); await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 }); await page.waitForTimeout(600);
    const js = await read();
    must(js.eyebrows.join() === srv.eyebrows.join() && js.label === srv.label, `app.js names: ${js.label}; ${js.eyebrows.join(", ")}`);
    must(JSON.stringify(js.lines) === JSON.stringify(srv.lines), `app.js lines differ: ${js.lines.find((l, i) => l !== srv.lines[i])}`);
  }, { w, h });
}
// on a short phone in 3D the Answer panel is reordered; "What does this mean?" stays right under the
// sentence it explains (#confidence), never floated to the top of the panel
for (const [w, h] of [[390, 664], [320, 568]]) {
  await test(`on a short phone "What does this mean?" sits right under how sure, ${w}x${h}`, async (page) => {
    await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
    must((await page.evaluate(() => window.__evident.stats().mode)) === "3d", "not in 3D");
    await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(3500);
    const r = await page.evaluate(() => {
      const inner = document.querySelector("#cap-answer .inner"), items = [];
      const walk = (p) => { for (const c of p.children) { const o = getComputedStyle(c); if (o.display === "contents") walk(c); else if (o.display !== "none" && !c.hidden && c.getBoundingClientRect().height > 0) items.push(c); } };
      walk(inner);
      const conf = document.getElementById("confidence").getBoundingClientRect(), more = document.getElementById("meter").closest(".sure-more"), m = document.getElementById("meter").getBoundingClientRect();
      const between = items.filter((c) => c !== more && c.id !== "confidence" && (() => { const q = c.getBoundingClientRect(); return q.top >= conf.bottom - 0.5 && q.bottom <= m.top + 0.5; })()).map((c) => c.id || c.className);
      return { cb: conf.bottom, mt: m.top, between };
    });
    must(r.mt >= r.cb - 0.5, `the top of #meter (${Math.round(r.mt)}) is above the bottom of #confidence (${Math.round(r.cb)})`);
    must(!r.between.length, `between how sure and its link: ${r.between.join(", ")}`);
  }, { w, h });
}
// ---- 18. the service worker's shell: a time limit on the home page, and offline first paint ----
// waitForFunction takes an async predicate's promise as truthy at once: poll from here instead
const until = async (page, fn, ms = 30000) => {
  for (const t0 = Date.now(); Date.now() - t0 < ms; await page.waitForTimeout(250)) if (await page.evaluate(fn).catch(() => false)) return true;
  return false;
};
const swReady = async (page) => {
  await page.evaluate(() => navigator.serviceWorker.ready);
  await until(page, async () => !!(await caches.keys()).length && !!(await (await caches.open((await caches.keys())[0])).match("/")), 20000);
  await page.waitForTimeout(500);
};
const painted = (page) => page.evaluate(() => {
  const el = document.getElementById("claim-input"); if (!el) return false;
  const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
});
await test("18: a returning visit to a server held 10 s paints the kept home page within about 3 s, and the late answer refreshes the copy", async (page) => {
  await page.goto(base + "/"); await swReady(page);
  await page.reload(); await page.waitForTimeout(500);  // now controlled
  must(await page.evaluate(() => !!navigator.serviceWorker.controller), "the page is not controlled by the service worker");
  const keptNonce = () => page.evaluate(async () => { const c = await caches.open((await caches.keys())[0]); const r = await c.match("/"); return r ? ((await r.text()).match(/nonce="([^"]+)"/) || [])[1] : null; });
  const mark = await keptNonce();
  must((await fetch(base + "/__fixture/hold/10")).ok, "the fixture server cannot hold");
  // timed from the navigation's own start (unloading the 3D page before it is not the server's wait)
  await page.addInitScript(() => { const look = () => { const el = document.getElementById("claim-input");
    if (el && el.getBoundingClientRect().height > 0) window.__boxAt = performance.now(); else requestAnimationFrame(look); }; requestAnimationFrame(look); });
  await page.goto(base + "/", { waitUntil: "commit", timeout: 20000 });
  await page.waitForFunction(() => window.__boxAt, null, { timeout: 8000 });
  const ms = Math.round(await page.evaluate(() => window.__boxAt));
  const shown = await page.evaluate(() => document.querySelector("script[nonce]").nonce);
  must(ms < 3500, `the kept page painted ${ms} ms after the navigation started`);
  must(shown === mark, "the page shown is not the kept copy (was the server held?)");
  await page.waitForTimeout(11000);
  await fetch(base + "/__fixture/hold/0");
  const fresh = await keptNonce();
  must(mark && fresh && fresh !== mark, `the kept copy was not refreshed by the late answer (${mark} then ${fresh})`);
}, { sw: true });
await test("18: with a fast server a returning visit gets the fresh page from the network", async (page) => {
  const seen = [];
  page.on("response", (r) => { if (r.request().isNavigationRequest()) seen.push(r.headers()["content-security-policy"] || ""); });
  await page.goto(base + "/"); await swReady(page);
  await page.reload(); await page.waitForTimeout(500);
  await page.reload();
  must(await painted(page), "the claim box is not painted");
  // each fresh page carries a new CSP nonce, so three different nonces mean three answers from the server
  const nonces = new Set(seen.map((h) => (h.match(/nonce-([^']+)/) || [])[1]).filter(Boolean));
  must(nonces.size === 3, `navigations answered: ${seen.length}, distinct nonces: ${nonces.size}`);
}, { sw: true });
await test("18: a first visit with nothing kept waits for a held server, as before", async (page) => {
  must((await fetch(base + "/__fixture/hold/4")).ok, "the fixture server cannot hold");
  const t0 = Date.now();
  await page.goto(base + "/", { timeout: 20000 });
  const ms = Date.now() - t0;
  await fetch(base + "/__fixture/hold/0");
  must(ms >= 3900 && await painted(page), `a first visit took ${ms} ms`);
}, { sw: true });
await test("18: offline, the home page paints from the copy with its stylesheet, scripts and faces", async (page, ctx) => {
  await page.goto(base + "/"); await swReady(page);
  await ctx.setOffline(true);
  await page.reload({ waitUntil: "load" });
  const look = await page.evaluate(async () => {
    await document.fonts.ready;
    return { box: !!document.getElementById("claim-input"), css: [...document.styleSheets].some((s) => /desk\.css/.test(s.href || "")),
             ran: !!window.__evident, faces: [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family) };
  });
  await ctx.setOffline(false);
  must(look.box && look.css && look.ran, `offline: ${JSON.stringify(look)}`);
  must(look.faces.some((f) => /Instrument/.test(f)), `offline faces loaded: ${look.faces.join(", ")}`);
}, { sw: true });
await test("18: with a kept copy, a home answer of 503 or 429 shows the copy, and the bad answer is never stored", async (page) => {
  await page.goto(base + "/"); await swReady(page);
  await page.reload({ waitUntil: "load" });
  must(await page.evaluate(() => !!navigator.serviceWorker.controller), "the page is not controlled by the service worker");
  const keptNonce = () => page.evaluate(async () => { const c = await caches.open((await caches.keys())[0]); const r = await c.match("/"); return r ? ((await r.text()).match(/nonce="([^"]+)"/) || [])[1] : null; });
  const mark = await keptNonce();
  for (const code of [503, 429]) {
    must((await fetch(base + "/__fixture/status/" + code)).ok, "the fixture server cannot answer " + code);
    const r = await page.reload({ waitUntil: "load" });
    const shown = await page.evaluate(() => { const s = document.querySelector("script[nonce]"); return s ? s.nonce : null; });
    must(r.status() === 200 && await painted(page) && shown === mark, `${code}: status ${r.status()}, the kept copy shown: ${shown === mark}`);
    must((await keptNonce()) === mark, `${code}: the copy was replaced`);
  }
  await fetch(base + "/__fixture/status/0");
}, { sw: true });
await test("18: with a kept copy, a home answer of 403 (a challenge) reaches the reader as sent and is never stored", async (page, ctx) => {
  await page.goto(base + "/"); await swReady(page);
  await page.reload({ waitUntil: "load" });
  must(await page.evaluate(() => !!navigator.serviceWorker.controller), "the page is not controlled by the service worker");
  const keptNonce = () => page.evaluate(async () => { const c = await caches.open((await caches.keys())[0]); const r = await c.match("/"); return r ? ((await r.text()).match(/nonce="([^"]+)"/) || [])[1] : null; });
  const mark = await keptNonce();
  must((await fetch(base + "/__fixture/status/403")).ok, "the fixture server cannot answer 403");
  const r = await page.reload({ waitUntil: "load" });
  const text = await page.evaluate(() => document.body ? document.body.innerText : "");
  await fetch(base + "/__fixture/status/0");
  must(r.status() === 403 && /Challenge page/.test(text) && !(await painted(page)), `403: status ${r.status()}, on screen: ${JSON.stringify(text.slice(0, 80))}`);
  must((await keptNonce()) === mark, "403: the challenge replaced the kept copy");
  await page.reload({ waitUntil: "load" });
  const fresh = await page.evaluate(() => { const s = document.querySelector("script[nonce]"); return s ? s.nonce : null; });
  must(await painted(page) && fresh && fresh !== mark, `healthy again: the fresh page was not shown (${mark} then ${fresh})`);
  const good = await keptNonce();
  await ctx.setOffline(true);
  await page.reload({ waitUntil: "load" });
  const off = await page.evaluate(() => { const s = document.querySelector("script[nonce]"); return s ? s.nonce : null; });
  await ctx.setOffline(false);
  must(await painted(page) && off === good, `offline: the kept copy was not shown (${good} then ${off})`);
}, { sw: true });
await test("18: a first visit with nothing kept still gets the server's answer, as before", async (page) => {
  must((await fetch(base + "/__fixture/status/503")).ok, "the fixture server cannot answer 503");
  const r = await page.goto(base + "/");
  await fetch(base + "/__fixture/status/0");
  must(r.status() === 503, `a first visit got ${r.status()}`);
}, { sw: true });

await test("18: offline, /checks with a claim and /flight are sent on to the home page under its own address", async (page, ctx) => {
  await page.goto(base + "/"); await swReady(page);
  await page.reload({ waitUntil: "load" });
  await ctx.setOffline(true);
  const seen = [];
  for (const [from, to] of [["/checks?q=zinc", "/?q=zinc"], ["/flight", "/"]]) {
    await page.goto(base + from, { waitUntil: "load" }).catch(() => {});
    // the claim's address then behaves as /?q= does offline (the page reads it); the page and its notice are there
    seen.push([from, page.url().replace(base, ""), await page.evaluate(() => /Not medical advice/.test(document.body.innerText)).catch(() => false)]);
  }
  await ctx.setOffline(false);
  must(seen[0][1] === "/?q=zinc" && seen[1][1] === "/" && seen.every((x) => x[2]), JSON.stringify(seen));
}, { sw: true });

// ---- 19. the warm-up: the first Next pays for no compile and no upload ----
const warmed = (page) => page.waitForFunction(() => window.__evd && window.__evd.warmed && window.__evd.warmed(), null, { timeout: 30000, polling: 100 }).then(() => true, () => false);
await test("19: on a saved answer the warm-up finishes after the answer is readable, and the first press of View evidence compiles and sends nothing new", async (page) => {
  await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  must(await warmed(page), "the warm-up never finished");
  const before = await page.evaluate(() => { const r = window.__evd.renderer; return { p: r.info.programs.length, t: r.info.memory.textures, stop: window.__evident.stats().stop }; });
  await page.evaluate(() => { const r = window.__evd.renderer, gl = r.getContext(); window.__up = 0; const o = gl.texImage2D.bind(gl); gl.texImage2D = function (...a) { window.__up++; return o(...a); }; });
  await page.click("#how-answer"); await page.waitForTimeout(2500);
  const after = await page.evaluate(() => ({ p: window.__evd.renderer.info.programs.length, up: window.__up, stop: window.__evident.stats().stop }));
  must(after.stop === before.stop + 1, `stop ${before.stop} to ${after.stop}`);
  must(after.p === before.p, `programs ${before.p} to ${after.p}`);
  // the lifted top sheet's large face is painted on the way (hiRes): at most that one is new
  must(after.up <= 1, `textures sent after the press: ${after.up}`);
});
await test("19: a press before the warm-up is done still lands on the next stop", async (page) => {
  await page.goto(`${base}/?q=${encodeURIComponent(SAVED[1])}`, { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  const s0 = await page.evaluate(() => window.__evident.stats().stop);
  await page.click("#how-answer");
  await page.waitForFunction((s) => window.__evident.stats().stop === s + 1 && !window.__evident.stats().looping, s0, { timeout: 15000 });
  must(await warmed(page), "the warm-up did not finish after the press");
});
await test("19: the home page has no warm-up", async (page) => {
  await page.goto(base + "/", { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  await page.waitForTimeout(1500);
  must(await page.evaluate(() => !window.__evd.warmed()), "the home page warmed up");
});

// ---- 20. the update path: from the live site's worker (f10ef54, cache evident-v118) to this build ----
await test("20: a browser holding the live worker and its cache moves to this build: no error, no failed request, old cache gone, shell stamped, offline paints", async (page, ctx) => {
  const bad = [];
  page.on("requestfailed", (r) => bad.push("failed " + r.url().replace(base, "")));
  page.on("response", (r) => { if (r.status() >= 400) bad.push(r.status() + " " + r.url().replace(base, "")); });
  must((await fetch(base + "/__fixture/oldsw/1")).ok, "the fixture server cannot serve the old worker");
  await page.goto(base + "/", { waitUntil: "load" });
  await page.evaluate(() => navigator.serviceWorker.ready);
  must(await until(page, async () => (await caches.keys()).join() === "evident-v118" && (await (await caches.open("evident-v118")).keys()).length >= 20), "the old worker did not fill evident-v118");
  await page.reload({ waitUntil: "load" });
  must(await until(page, () => !!navigator.serviceWorker.controller, 10000), "the old worker does not control the page");
  // the new build: /sw.js now answers this build's worker; the page registers it on load
  await fetch(base + "/__fixture/oldsw/0");
  bad.length = 0;
  await page.reload({ waitUntil: "load" });
  must(await painted(page), "the new build did not paint under the old worker");
  await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r && r.update())).catch(() => {});
  await until(page, async () => { const r = await navigator.serviceWorker.getRegistration(); return (await caches.keys()).join() === "evident-v130" && r && r.active && r.active.state === "activated"; });
  const st = await page.evaluate(async () => {
    const keys = await caches.keys();
    const src = await (await fetch("/sw.js", { cache: "no-store" })).text();
    const shell = JSON.parse(src.match(/const SHELL = (\[[\s\S]*?\]);/)[1].replace(/,\s*\]/, "]"));
    const c = await caches.open(keys[0]);
    const missing = [];
    for (const u of shell) if (!(await c.match(u))) missing.push(u);
    const stamped = shell.filter((u) => u.startsWith("/static/") && !u.startsWith("/static/fonts/")).every((u) => /\?v=[0-9a-f]{10}$/.test(u));
    const reg = await navigator.serviceWorker.getRegistration();
    return { keys, missing, stamped, n: shell.length, active: reg && reg.active ? reg.active.state : null, waiting: !!(reg && reg.waiting) };
  });
  must(st.keys.join() === "evident-v130", `caches: ${st.keys.join()}`);
  must(!st.missing.length && st.stamped && st.n === 8, `shell missing ${st.missing.join(" ")}, stamped ${st.stamped}, ${st.n} entries`);
  must(st.active === "activated" && !st.waiting, `worker ${st.active}, waiting ${st.waiting}`);
  must(!bad.length, `requests: ${bad.join(" | ")}`);
  // later, with the network off, the home page paints from the new cache
  await page.reload({ waitUntil: "load" });
  await ctx.setOffline(true);
  await page.reload({ waitUntil: "load" });
  const ok = await painted(page);
  await ctx.setOffline(false);
  must(ok, "offline, the home page did not paint");
}, { sw: true });
await test("20: a kept copy and a server taking 4 s: the copy paints at about 2.5 s and the fresh page is stored for next time", async (page) => {
  await page.goto(base + "/"); await swReady(page);
  await page.reload(); await page.waitForTimeout(500);
  const keptNonce = () => page.evaluate(async () => { const c = await caches.open((await caches.keys())[0]); const r = await c.match("/"); return r ? ((await r.text()).match(/nonce="([^"]+)"/) || [])[1] : null; });
  const mark = await keptNonce();
  must((await fetch(base + "/__fixture/hold/4")).ok, "the fixture server cannot hold");
  await page.addInitScript(() => { const look = () => { const el = document.getElementById("claim-input");
    if (el && el.getBoundingClientRect().height > 0) window.__boxAt = performance.now(); else requestAnimationFrame(look); }; requestAnimationFrame(look); });
  await page.goto(base + "/", { waitUntil: "commit", timeout: 20000 });
  await page.waitForFunction(() => window.__boxAt, null, { timeout: 8000 });
  const ms = Math.round(await page.evaluate(() => window.__boxAt));
  const shown = await page.evaluate(() => document.querySelector("script[nonce]").nonce);
  must(ms >= 2300 && ms < 3500 && shown === mark, `the kept copy painted at ${ms} ms (kept copy: ${shown === mark})`);
  await page.waitForTimeout(3000);
  await fetch(base + "/__fixture/hold/0");
  const fresh = await keptNonce();
  must(fresh && fresh !== mark, `the fresh page was not stored (${mark} then ${fresh})`);
}, { sw: true });

// ---- The share image takes the site's colours (q10-card 2) ----------------------------------
// The square the browser draws for sharing (app.js CARD) kept the mint ring and the green-cast
// greys after q10-look. Drawn for two verdicts with every kind of study, no pixel is near a colour
// q10-look removed (a pixel of a kept cloth aside), and outside the shelf (whose four cloths mean the kind of study) no pixel has
// chroma 0.02 or more outside hue 40 to 100. The grounds are --deep and --paper, the size 1080.
for (const verdict of ["false", "complicated"]) {
  await test(`share image colours (${verdict}): no mint, no green cast, the site's grounds, 1080 square`, async (page) => {
    await page.goto(base + "/?q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page);
    const out = await page.evaluate(async (verdict) => {
      const kinds = ["strong", "human", "lab", "weak", "retracted"];
      const studies = kinds.flatMap((k, i) => [true, false].map((used) => ({ title: `Study ${k}`, kind: k, tier: k === "strong" ? "strong" : k === "weak" || k === "retracted" ? "weak" : "moderate", cited_in_verdict: used, pmid: String(100 + i) })));
      const r = { claim: "Cold showers boost the immune system", verdict, tldr: "Small trials found fewer sick days reported, not fewer colds.", still_open: "Whether the effect lasts past three months.", studies, share_url: location.origin + "/?q=x" };
      const blob = await renderShareCard(r);
      const bmp = await createImageBitmap(blob);
      const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height;
      const x = c.getContext("2d"); x.drawImage(bmp, 0, 0);
      const d = x.getImageData(0, 0, c.width, c.height).data;
      const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      const lch = (R, G, B) => { const r = lin(R), g = lin(G), b = lin(B);
        const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b), s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
        const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s, Bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
        return [Math.hypot(A, Bb), ((Math.atan2(Bb, A) * 180 / Math.PI) + 360) % 360]; };
      const removed = ["9fc3b0", "4f6a5d", "d4e4da", "2c5848", "9fb3ad", "8fa6b4", "1d3a2e"].map((h) => [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)));
      // the kept cloths (DESIGN.md c-*): c-strong sits close to the removed --green, so a pixel of a cloth is not counted
      const cloths = ["35594c", "3a4e6b", "5a4760", "7e4f3d"].map((h) => [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)));
      const near12 = (i, q) => (d[i] - q[0]) ** 2 + (d[i + 1] - q[1]) ** 2 + (d[i + 2] - q[2]) ** 2 <= 144;
      const shelfTop = c.height - 112 - 60, shelfBottom = c.height - 112 + 6;
      let near = 0, cast = 0; const seen = new Map();
      for (let i = 0; i < d.length; i += 4) {
        const key = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2], y = Math.floor(i / 4 / c.width);
        if (removed.some((q) => near12(i, q)) && !cloths.some((q) => near12(i, q))) near++;
        if (y < shelfTop || y > shelfBottom) {
          let v = seen.get(key); if (v === undefined) { const [C, H] = lch(d[i], d[i + 1], d[i + 2]); v = C >= 0.02 && !(H >= 40 && H <= 100); seen.set(key, v); }
          if (v) cast++;
        }
      }
      const px = (X, Y) => { const j = (Y * c.width + X) * 4; return [d[j], d[j + 1], d[j + 2]].map((v) => v.toString(16).padStart(2, "0")).join(""); };
      return { w: c.width, h: c.height, near, cast, night: px(4, 4), day: px(4, c.height - 4) };
    }, verdict);
    must(out.w === 1080 && out.h === 1080, `the share image is ${out.w} by ${out.h}`);
    must(out.near === 0, `${out.near} pixels are near a colour q10-look removed`);
    must(out.cast === 0, `${out.cast} pixels outside the shelf have a green, mint, blue or purple cast`);
    must(out.night === "12110f" && out.day === "f4f2ef", `the grounds are #${out.night} and #${out.day}, not --deep and --paper`);
  });
}

// ---- L2. The lamp on a phone's first screen ----------------------------------------------------
// Held upright, before a check, in 3D: the lamp (base, stem, shade) and the lit wood under it are
// inside the picture below the strip, no word of stop 1 lies on them, and at 375x667 a band of
// 160 px or more of the picture is free of text. The claim box is on the first screen at every
// phone size, and "Check the research" too from 375x667 up. The same with reduced motion (the
// still picture). The lamp's box is the projection of the same points desk3d.js fits (LAMP_PTS).
const L2_PTS = [[-0.74, 0, -0.38], [-0.54, 0, -0.2], [-0.66, 0.46, -0.36], [-0.27, 0.71, 0], [-0.37, 0.6, 0], [-0.17, 0.6, 0], [-0.27, 0.6, -0.1], [-0.27, 0.6, 0.1], [-0.27, 0.67, -0.07], [-0.27, 0.67, 0.07], [0.12, 0, 0.12], [-0.3, 0, 0.32], [-0.62, 0, 0.18]];
const l2Look = (page) => page.evaluate((pts) => {
  const ps = pts.map((p) => window.__evd.proj(p[0], p[1], p[2])), xs = ps.map((p) => p[0]), ys = ps.map((p) => p[1]);
  const lamp = { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
  const strip = document.getElementById("strip").getBoundingClientRect().bottom;
  // every line of words in stop 1 a reader can see, as boxes
  const words = [], walk = document.createTreeWalker(document.getElementById("s1"), NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (!n.textContent.trim()) continue;
    const el = n.parentElement, cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || el.closest("[hidden], .sr-only")) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    for (const r of rg.getClientRects()) if (r.width > 1 && r.height > 1) words.push({ x0: r.left, x1: r.right, y0: r.top, y1: r.bottom, t: n.textContent.trim().slice(0, 24) });
  }
  const r = (s) => document.querySelector(s).getBoundingClientRect();
  const firstWord = Math.min(...words.map((w) => w.y0));
  return { mode: window.__evident.stats().mode, lamp, strip, W: innerWidth, H: innerHeight, firstWord,
    hit: words.find((w) => w.x0 < lamp.x1 && w.x1 > lamp.x0 && w.y0 < lamp.y1 && w.y1 > lamp.y0) || null,
    box: r(".claim-box").bottom, btn: r("#submit-btn").bottom };
}, L2_PTS);
for (const [w, h] of [[375, 667], [375, 812], [320, 568], [360, 740], [390, 844], [414, 896]]) for (const reduce of [false, true]) {
  await test(`Phone lamp: the lamp shows on a ${w}x${h} phone's first screen, clear of every word${reduce ? ", reduced motion" : ""}`, async (page) => {
    await page.goto(base + "/", { waitUntil: "load" }); await ready(page); await page.waitForTimeout(800);
    const s = await l2Look(page), L = s.lamp, f = (b) => `${Math.round(b.x0)},${Math.round(b.y0)} to ${Math.round(b.x1)},${Math.round(b.y1)}`;
    must(s.mode === "3d", "not in 3D: " + s.mode);
    must(L.x0 >= 0 && L.x1 <= s.W && L.y0 >= s.strip && L.y1 <= s.H, `the lamp (${f(L)}) is not inside the picture below the strip (${s.strip}) on ${s.W}x${s.H}`);
    // the shade keeps 12 px of clear air under the strip: it never reads as cropped
    must(L.y0 >= s.strip + 12, `the top of the shade (${Math.round(L.y0)}) is only ${Math.round(L.y0 - s.strip)} px under the strip`);
    must(L.y1 - L.y0 >= 75, `the lamp is only ${Math.round(L.y1 - L.y0)} px tall`);
    must(!s.hit, s.hit && `"${s.hit.t}" (${f(s.hit)}) lies on the lamp (${f(L)})`);
    // the words' scrim starts 14 px above the first line: the lamp ends above it, so nothing dims it
    must(L.y1 <= s.firstWord - 14, `the lamp ends at ${Math.round(L.y1)}, inside the scrim above the first words (${Math.round(s.firstWord)})`);
    must(s.box <= s.H, `the claim box ends at ${Math.round(s.box)}, below the first screen (${s.H})`);
    if (h >= 667) must(s.btn <= s.H, `"Check the research" ends at ${Math.round(s.btn)}, below the first screen (${s.H})`);
    if (w === 375 && h === 667) must(s.firstWord - s.strip >= 160, `only ${Math.round(s.firstWord - s.strip)} px of the picture is free of words`);
  }, { w, h, reduce, touch: true });
}

// ---- 21. snappy: the screen answers at once, the picture draws only when it changes ----
// Timings run on a second browser drawing on this machine's own GPU (ANGLE Metal on a Mac): SwiftShader runs the GPU
// on the CPU and makes every frame seconds long, so where only SwiftShader is there the timing checks say so and pass.
// The phone profile is scripts/measure_load.mjs's: 390x844 at 3x, touch, CPU slowed 4 times.
const gpuBrowser = await chromium.launch({ args: ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu"] });
const realGpu = await (async () => { const p = await gpuBrowser.newPage(); const r = await p.evaluate(() => { const g = document.createElement("canvas").getContext("webgl"), e = g && g.getExtension("WEBGL_debug_renderer_info"); return g && e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : ""; }); await p.close(); return !!r && !/SwiftShader/i.test(r); })();
async function snappy(name, fn, { phone = true, gpu = true } = {}) {
  if (process.env.UI_ONLY && !name.startsWith(process.env.UI_ONLY)) return;
  if (gpu && !realGpu) { results.push(true); console.log(`PASS ${name} (skipped: no GPU but SwiftShader here)`); return; }
  const ctx = await (gpu ? gpuBrowser : browser).newContext(phone ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, serviceWorkers: "block" } : { viewport: { width: 1440, height: 900 }, serviceWorkers: "block" });
  await ctx.addInitScript(() => {
    // every picture drawn, and the picture and page as one string (any change is movement)
    const hook = () => { const d = window.__evd; if (!d || !d.renderer) { setTimeout(hook, 20); return; } if (!d.gl) return; const r = d.renderer, o = r.render.bind(r); window.__draws = 0; r.render = (a, b) => { window.__draws++; o(a, b); }; };
    hook();
    window.__sig = () => { const c = window.__evd && window.__evd.camera; return [Math.round(scrollY), c ? c.position.toArray().map((v) => v.toFixed(4)).join() : "", c ? c.fov.toFixed(3) : ""].join("|"); };
    // the camera alone (q10-rel: with native scrolling the page moves at once; the camera's first change is the measure)
    window.__csig = () => { const c = window.__evd && window.__evd.camera; return c ? c.position.toArray().map((v) => v.toFixed(4)).join() + "|" + c.fov.toFixed(3) : ""; };
    // after a press: the first movement and the last, from the press's own time stamp
    window.__watch = (ms, cam) => new Promise((res) => {
      const sig = cam ? window.__csig : window.__sig;
      let press = null, first = null, lastAt = null, prev = sig(), moves = 0; const t0 = performance.now();
      const pd = (e) => { if (press === null) press = e.timeStamp; };
      addEventListener("pointerdown", pd, true); addEventListener("touchstart", pd, true);
      const loop = () => { const n = performance.now(), s = sig(); if (s !== prev) { if (press !== null && first === null) first = n; lastAt = n; prev = s; moves++; }
        if (n - t0 > ms || (lastAt !== null && n - lastAt > 900)) { removeEventListener("pointerdown", pd, true); removeEventListener("touchstart", pd, true); res({ first: first === null ? null : Math.round(first - press), settled: lastAt === null ? null : Math.round(lastAt - press), moves }); } else requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const cdp = await ctx.newCDPSession(page);
  if (phone) await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  try {
    await fetch(base + "/__fixture/reset");
    await fn(page, cdp);
    must(!errors.length, "page errors: " + errors.join(" | "));
    results.push(true); console.log(`PASS ${name}`);
  } catch (e) {
    results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}
const still3d = (page, ms = 900) => page.evaluate((ms) => new Promise((res) => { let prev = window.__sig(), at = performance.now(); const t0 = at; const loop = () => { const s = window.__sig(), n = performance.now(); if (s !== prev) { prev = s; at = n; } if (n - at > ms || n - t0 > 30000) res(); else requestAnimationFrame(loop); }; loop(); }), ms);
const fresh3d = async (page) => {
  await page.goto(base + "/", { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return false;
  await page.fill("#claim-input", LIVE); await page.tap("#submit-btn").catch(() => page.click("#submit-btn"));
  await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 60000 });
  await still3d(page); return true;
};

await snappy("21: the home page draws no picture while it scrolls, and none on a settled screen for 2 s", async (page, cdp) => {
  await page.goto(base + "/", { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  await page.waitForTimeout(1500);
  const d0 = await page.evaluate(() => window.__draws);
  await cdp.send("Input.synthesizeScrollGesture", { x: 720, y: 500, yDistance: -800, speed: 1200, gestureSourceType: "mouse" });
  await page.waitForTimeout(400);
  const d1 = await page.evaluate(() => window.__draws);
  must(d1 === d0, `the home page drew ${d1 - d0} pictures while it scrolled`);
  await page.evaluate(() => scrollTo(0, 0)); await page.waitForTimeout(600);
  const d2 = await page.evaluate(() => window.__draws); await page.waitForTimeout(2000);
  must((await page.evaluate(() => window.__draws)) === d2, "the settled home page drew");
  await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page); await still3d(page);
  await page.waitForTimeout(500);
  const r0 = await page.evaluate(() => window.__draws); await page.waitForTimeout(2000);
  must((await page.evaluate(() => window.__draws)) === r0, "the settled result drew");
}, { phone: false, gpu: false });

// q10-rel: q10-nav removed Next; the section move is now a real scroll from the Answer to Studies (q10-snappy's
// hand-back), timed to the camera's first change, after one move held up mid-way by 2.2 s of other work.
const toStudies = (page) => page.evaluate(() => Math.round(document.getElementById("s3").getBoundingClientRect().top));
await snappy("21: on the phone the first scroll from the Answer to Studies after a fresh answer moves the camera within 300 ms, and glides, after one held-up frame too", async (page, cdp) => {
  if (!(await fresh3d(page))) return;
  // one frame held up longer than the whole glide by other work (as the answer arriving does on a slow phone): later moves still glide
  const y0 = await page.evaluate(() => scrollY);
  await page.evaluate(() => setTimeout(() => { const t = performance.now(); while (performance.now() - t < 2200); }, 120));
  await cdp.send("Input.synthesizeScrollGesture", { x: 195, y: 600, yDistance: -(await toStudies(page)), speed: 1200, gestureSourceType: "touch" });
  await still3d(page); await page.evaluate((y) => window.scrollTo({ top: y, behavior: "instant" }), y0); await still3d(page);
  await page.waitForTimeout(1000);
  const w = page.evaluate(() => window.__watch(8000, true)); await page.waitForTimeout(50);
  await cdp.send("Input.synthesizeScrollGesture", { x: 195, y: 600, yDistance: -(await toStudies(page)), speed: 1200, gestureSourceType: "touch" });
  const r = await w;
  must(r.first !== null && r.first <= 300, `first camera movement ${r.first} ms after the touch`);
  // a glide is many pictures; a cut (the slow-machine rule) is one or two
  must(r.moves >= 4, `the move was a cut (${r.moves} changes)`);
});

await snappy("21: on the phone a fresh answer is on screen within 2.5 s of arriving, before the desk paints its sheets", async (page) => {
  await page.addInitScript(() => {
    // every canvas the page makes, when (each sheet face and card face is one)
    window.__cv = []; const ce = Document.prototype.createElement; Document.prototype.createElement = function (t, ...a) { if (String(t).toLowerCase() === "canvas") window.__cv.push(performance.now()); return ce.call(this, t, ...a); };
    const rd = ReadableStreamDefaultReader.prototype.read, dec = new TextDecoder();
    ReadableStreamDefaultReader.prototype.read = function () { return rd.call(this).then((r) => { if (r && r.value && !window.__arrived) { try { if (/"stage":\s*"done"/.test(dec.decode(r.value, { stream: true }))) { window.__arrived = performance.now(); requestAnimationFrame(look); } } catch (e) {} } return r; }); };
    // the test's own look at the verdict, in the page and on the page's clock (not the product's signal, and not the
    // runner's round trip): the first frame the stamp is on screen, stamped at the frame after it, once painted
    const look = () => { const s = document.getElementById("stamp"), b = s && s.getBoundingClientRect();
      if (document.documentElement.classList.contains("checked") && s.textContent.trim() && b.height > 0 && b.bottom > 0 && b.top < innerHeight) requestAnimationFrame(() => { window.__shownAt = performance.now(); });
      else requestAnimationFrame(look); };
  });
  await page.goto(base + "/", { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  await page.fill("#claim-input", LIVE); await page.tap("#submit-btn");
  await page.waitForFunction(() => window.__arrived > 0, null, { timeout: 60000, polling: 20 });
  await page.waitForFunction(() => window.__shownAt > 0, null, { timeout: 30000, polling: 50 });
  const shown = await page.evaluate(() => window.__shownAt - window.__arrived);
  must(shown <= 2500, `the answer reached the screen ${Math.round(shown)} ms after it arrived`);
  // the new result's faces are painted after it is on screen, a few at a time, not in one task as it lands
  const r = await page.evaluate((sh) => ({ before: window.__cv.filter((t) => t > window.__arrived && t <= window.__arrived + sh).length, sheets: (() => { let n = 0; window.__evd.scene.traverse((o) => { if (o.isMesh && o.userData.face) n++; }); return n; })() }), shown);
  // the card's two printed faces, and at most the first lazy step's sheets (on 05ec98e: every sheet, the top one again
  // at full size, and both standing faces)
  must(r.before <= 4 && r.before < r.sheets + 4, `${r.before} canvases painted before the answer was on screen (${r.sheets} sheets)`);
});

await snappy("21: once warmed, the first scroll from the Answer to Studies after a fresh answer sends no card face to the GPU", async (page, cdp) => {
  if (!(await fresh3d(page))) return;
  must(await warmed(page), "the warm-up never finished");
  await page.evaluate(() => { const gl = window.__evd.renderer.getContext(); window.__big = []; const o = gl.texImage2D.bind(gl); gl.texImage2D = function (...a) { const s = a[a.length - 1]; if (s && s.width >= 1600) window.__big.push(s.width + "x" + s.height); return o(...a); }; });
  await cdp.send("Input.synthesizeScrollGesture", { x: 720, y: 500, yDistance: -(await toStudies(page)), speed: 1200, gestureSourceType: "mouse" });
  await page.waitForTimeout(2500);
  const big = await page.evaluate(() => window.__big);
  must(!big.length, `card faces sent on that first move: ${big.join(" ")}`);
}, { phone: false, gpu: false });

await snappy("21: a sheet is three draw calls, not six", async (page) => {
  await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  const g = await page.evaluate(() => { const out = []; window.__evd.scene.traverse((o) => { if (o.isMesh && o.userData.face) out.push(o.geometry.groups.length); }); return out; });
  must(g.length && g.every((n) => n === 3), `groups per sheet: ${g.join()}`);
}, { phone: false, gpu: false });

await snappy("21: on the phone at slow 4G the claim box is usable as the page's own scripts are in, not later", async (page, cdp) => {
  await cdp.send("Network.enable"); await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 });
  await page.addInitScript(() => {
    const look = () => { const el = document.getElementById("claim-input"), r = el && el.getBoundingClientRect();
      if (r && r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden" && r.top < innerHeight && !el.disabled) { window.__boxAt = performance.now(); return; } requestAnimationFrame(look); };
    requestAnimationFrame(look);
  });
  await page.goto(base + "/", { waitUntil: "load", timeout: 120000 });
  await page.waitForFunction(() => window.__boxAt, null, { timeout: 120000, polling: 50 });
  const t = await page.evaluate(() => ({ box: window.__boxAt, dcl: performance.getEntriesByType("navigation")[0].domContentLoadedEventEnd }));
  // the box is on screen and typeable no later than 300 ms after the page's scripts are wired (before: about 5 ms)
  must(t.box - t.dcl <= 300, `the claim box was usable ${Math.round(t.box - t.dcl)} ms after the scripts were in (${Math.round(t.box)} ms)`);
});

// ---- 22. The joined release (q10-rel): checks no single part could make --------------------------------------
// The result screens (q10-nav), the colours (q10-look, q10-card), the lamp on a phone (q10-phone) and the speed work
// (q10-snappy), together. At 375x812, 375x667, 320x568, 1280x800 and 1440x900, in 3D and in text mode:
// (a) the home screen shows the claim box (and on a phone in 3D the lamp band) on the first screen, and a check run
//     from it lands on the Answer as q10-nav built it;
// (b) on every result section no element's computed colour, background, border or outline is one q10-look removed;
// (c) Read abstract and Open PubMed on every study, the one section picker and the one View evidence, 44 px or more;
// (d) the first section move after an answer arrives (View evidence) starts within 300 ms.
// Then, on the phone profile: (e) the camera at the start of a fresh check never jumps; (f) "Check the research"
// paints its feedback within 100 ms, phone and desktop.
const J_SIZES = [[375, 812], [375, 667], [320, 568], [1280, 800], [1440, 900]];
// colours.md (05ec98e): the mint and the green, blue and teal it named, matched within 12 of 255 per channel...
const J_NEAR = ["9fc3b0", "4f6a5d", "d4e4da", "2c5848", "9fb3ad", "8fa6b4", "1d3a2e"];
// ...and every green-cast grey token it replaced, matched exactly (the new warm greys sit close by on purpose)
const J_EXACT = ["0b0d0c", "0f1211", "181c1a", "2a302d", "3a423e", "3c4440", "59625d", "4a534e", "eceee9", "a9b0ab", "8f9792", "f1f2ee",
  "121514", "434a46", "3a403c", "222624", "0f1110", "0c0e0d", "9ea6a1", "7d8580", "5d6560", "d9dcd5", "b9bfb7", "fafaf8"];
const jHome = (page, mode) => page.goto(`${base}/${mode === "text" ? "?nogl=1" : ""}`, { waitUntil: "load" }).then(() => ready(page));
for (const [w, h] of J_SIZES) for (const mode of ["3d", "text"]) {
  await test(`22a: the home screen holds the claim box on the first screen and a check from it lands on the Answer, ${w}x${h} ${mode}`, async (page) => {
    await jHome(page, mode); await page.waitForTimeout(600);
    const s = await page.evaluate(() => {
      const R = (q) => document.querySelector(q).getBoundingClientRect(), cs = getComputedStyle(document.documentElement);
      return { mode: window.__evident.stats().mode, box: R(".claim-box").bottom, btn: R("#submit-btn").bottom, H: innerHeight,
        band: cs.getPropertyValue("--lamp-band").trim(), mark: getComputedStyle(document.querySelector("#s1 .wordmark")).display };
    });
    must(s.mode === (mode === "3d" ? "3d" : "text"), `mode ${s.mode}`);
    must(s.box <= s.H, `the claim box ends at ${Math.round(s.box)}, below the first screen (${s.H})`);
    if (h >= 667) must(s.btn <= s.H, `"Check the research" ends at ${Math.round(s.btn)}, below the first screen (${s.H})`);
    // the lamp band is the phone's in 3D only; text mode and a wide screen keep their own first screen
    if (w <= 760 && mode === "3d") must(s.band && s.mark === "none", `no lamp band on the phone (band "${s.band}", wordmark ${s.mark})`);
    else must(!s.band || s.mark !== "none", `a lamp band where there is none to show (band "${s.band}", wordmark ${s.mark})`);
    await page.fill("#claim-input", LIVE); await page.click("#submit-btn");
    await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 60000 });
    await page.waitForTimeout(mode === "3d" ? 3500 : 900);
    const a = await page.evaluate(answerAtRest);
    const at = await page.evaluate(() => ({ y: Math.round(scrollY), top: Math.round(document.getElementById("s2").getBoundingClientRect().top + scrollY),
      named: innerWidth > 760 ? (document.querySelector("#rail button[aria-current] span") || {}).textContent : document.getElementById("where-name").textContent }));
    // within 8 px of the Answer's top: q10-nav's landing may shift by a few px (landShift), the picker must say Answer
    must(Math.abs(at.y - at.top) <= 8 && at.named === "Answer", `landed at ${at.y} (the Answer starts at ${at.top}), the picker says ${at.named}`);
    must(!a.bad.length, a.bad.join("; "));
    must(a.studies || a.crossed.length, `the Answer looks finished: no Studies heading above ${a.foot} and nothing crossed`);
  }, { w, h, touch: w <= 760 });
}
for (const [w, h] of J_SIZES) for (const mode of ["3d", "text"]) {
  await test(`22b: no colour q10-look removed on any result section, the picker and an open abstract included, ${w}x${h} ${mode}`, async (page) => {
    await openSaved(page, w, mode);
    await page.evaluate(() => { document.querySelectorAll(".grp-btn[aria-expanded=false]").forEach((b) => b.click()); const a = document.querySelector(".row .abs"); if (a) a.open = true; });
    if (w <= 760) await page.click("#where");
    await page.waitForTimeout(300);
    const out = await page.evaluate(([near, exact]) => {
      const rgb = (h) => [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)), N = near.map(rgb), E = new Set(exact);
      const hex = (c) => { const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/); if (!m || (m[4] !== undefined && +m[4] === 0)) return null; return [m[1], m[2], m[3]].map((v) => (+v).toString(16).padStart(2, "0")).join(""); };
      const bad = new Map(); let n = 0;
      for (const el of document.querySelectorAll("#strip, #strip *, #track, #track *, #bar, #bar *, #rail, #rail *, #menu, #menu *")) {
        if (!el.checkVisibility()) continue; n++;
        const cs = getComputedStyle(el), props = ["color", "backgroundColor", "borderTopColor", "borderBottomColor", "borderLeftColor", "borderRightColor", "outlineColor", "textDecorationColor"];
        for (const p of props) {
          if (p.startsWith("border") && parseFloat(cs[p.replace("Color", "Width")]) === 0) continue;
          const x = hex(cs[p]); if (!x) continue; const q = rgb(x);
          if (E.has(x) || N.some((r) => (q[0] - r[0]) ** 2 + (q[1] - r[1]) ** 2 + (q[2] - r[2]) ** 2 <= 144)) {
            const k = `${el.id ? "#" + el.id : el.tagName.toLowerCase() + "." + [...el.classList].join(".")} ${p} #${x}`; bad.set(k, 1);
          }
        }
      }
      return { n, bad: [...bad.keys()] };
    }, [J_NEAR, J_EXACT]);
    must(out.n > 200, `only ${out.n} visible elements were read`);
    must(!out.bad.length, `${out.bad.length} removed colours: ${out.bad.slice(0, 4).join("; ")}`);
  }, { w, h });
}
for (const [w, h] of J_SIZES) for (const mode of ["3d", "text"]) {
  await test(`22c: Read abstract and Open PubMed on every study, one section picker, one View evidence, all 44 px or more, ${w}x${h} ${mode}`, async (page) => {
    await openSaved(page, w, mode);
    await page.evaluate(() => document.querySelectorAll(".grp-btn[aria-expanded=false]").forEach((b) => b.click()));
    await page.waitForTimeout(300);
    const s = await page.evaluate(() => {
      const vis = (e) => !!e && e.checkVisibility() && e.getBoundingClientRect().height > 0, H = (e) => Math.round(e.getBoundingClientRect().height);
      const rows = [...document.querySelectorAll("#rows .row")].map((r) => { const a = r.querySelector(".abs > summary"), no = r.querySelector(".noabs"), p = r.querySelector("a.pm");
        return { n: r.dataset.n, abs: vis(a) ? H(a) : vis(no) ? 44 : 0, pm: vis(p) ? H(p) : 0 }; });
      const where = [...document.querySelectorAll("#where")].filter(vis), rail = [...document.querySelectorAll("#rail button:not(.rail-again)")].filter(vis);
      const ve = [...document.querySelectorAll("button, a")].filter((b) => vis(b) && b.textContent.trim() === "View evidence");
      return { rows, where: where.map(H), rail: rail.map(H), railShown: vis(document.getElementById("rail")), ve: ve.map(H) };
    });
    must(s.rows.length >= 10, `only ${s.rows.length} rows`);
    const bad = s.rows.filter((r) => r.abs < 44 || r.pm < 44);
    must(!bad.length, `rows without both actions at 44 px: ${JSON.stringify(bad.slice(0, 3))}`);
    if (w <= 760) must(s.where.length === 1 && s.where[0] >= 44 && !s.railShown, `the phone picker: ${s.where.join()} px, rail shown ${s.railShown}`);
    else must(!s.where.length && s.railShown && s.rail.length === 4 && s.rail.every((x) => x >= 44), `the wide picker: bar ${s.where.join()}, rail ${s.rail.join()}`);
    must(s.ve.length === 1 && s.ve[0] >= 44, `View evidence: ${s.ve.length} shown, ${s.ve.join()} px`);
  }, { w, h, touch: w <= 760 });
}
// (d) timed on the GPU browser of block 21 in 3D (SwiftShader draws a wide frame in most of a second, which is the
// machine, not the page), at each size without CPU slowing; text mode on the usual browser.
for (const [w, h] of J_SIZES) for (const mode of ["3d", "text"]) {
  const name = `22d: the first section move after an answer arrives starts within 300 ms, ${w}x${h} ${mode}`;
  const fn = async (page) => {
    if (mode === "3d") await page.setViewportSize({ width: w, height: h });
    await jHome(page, mode);
    await page.fill("#claim-input", LIVE); await page.click("#submit-btn");
    await page.waitForFunction(() => { const b = document.getElementById("how-answer"); return document.documentElement.classList.contains("checked") && b && b.checkVisibility() && b.getBoundingClientRect().height > 0; }, null, { timeout: 60000, polling: 20 });
    const t = page.evaluate(() => new Promise((res) => {
      const sig = () => { const c = window.__evd && window.__evd.camera; return Math.round(scrollY) + "|" + (c ? c.position.toArray().map((v) => v.toFixed(4)).join() : ""); };
      let press = null; addEventListener("pointerdown", (e) => { if (press === null) press = e.timeStamp; }, { capture: true, once: true });
      let prev = null; const t0 = performance.now();
      const loop = () => { const s = sig(), n = performance.now();
        if (press !== null && prev !== null && s !== prev) { res(Math.round(n - press)); return; }
        if (press !== null && prev === null) prev = s;
        if (n - t0 > 6000) { res(null); return; } requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }));
    await page.waitForTimeout(30);
    await page.click("#how-answer");
    const ms = await t;
    console.log(`     22d ${w}x${h} ${mode}: first movement ${ms} ms after the press`);
    must(ms !== null && ms <= 300, `the first movement came ${ms} ms after the press`);
  };
  if (mode === "3d") await snappy(name, fn, { phone: false, gpu: true }); else await test(name, fn, { w, h, touch: w <= 760 });
}
// (e) On a phone in 3D the home screen's camera is the lamp band's (q10-phone); a fresh check glides from it to the Answer.
// Every frame from the press to 600 ms after it, and the first 600 ms after the camera starts to move (the glide starts
// when the answer lands, after the reading log): the camera is at its start, or on the way (never farther from the
// answer's camera than the start was, never back up the path, and no step longer than a third of the whole way).
// The camera is its position, where it looks (a point 1 unit ahead) and its field of view (a degree counts 1/20).
for (const [w, h] of [[375, 812], [375, 667], [320, 568]]) {
  // on the GPU browser, phone profile (touch, CPU slowed 4 times): SwiftShader draws a frame in about 170 ms, too few to see a jump
  await snappy(`22e: on a ${w}x${h} phone the camera does not jump at the start of a fresh check`, async (page) => {
    await page.setViewportSize({ width: w, height: h });
    await jHome(page, "3d"); if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
    await page.waitForTimeout(800);
    await page.fill("#claim-input", LIVE);
    const rec = page.evaluate(() => new Promise((res) => {
      const cam = () => { const c = window.__evd.camera, d = c.getWorldDirection(new c.position.constructor()); return c.position.toArray().concat(d.toArray(), c.fov); };
      const f = []; let press = null, movedAt = null;
      addEventListener("pointerdown", () => { press = performance.now(); f.push([0].concat(cam())); }, { capture: true, once: true });
      const same = (a, b) => a.every((v, i) => i === 0 || Math.abs(v - b[i]) < 1e-5);
      const loop = () => { if (press !== null) { const n = performance.now(), x = [Math.round(n - press)].concat(cam());
          if (movedAt === null && !same(x, f[0])) movedAt = n;
          if (n - press <= 600 || movedAt !== null) f.push(x);
          if ((movedAt !== null && n - movedAt > 600) || n - press > 60000) { res({ f, movedAt: movedAt === null ? null : Math.round(movedAt - press) }); return; } }
        requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }));
    await page.waitForTimeout(30); await page.tap("#submit-btn");
    const { f, movedAt } = await rec;
    await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 60000 }); await page.waitForTimeout(3500);
    const end = await page.evaluate(() => { const c = window.__evd.camera, d = c.getWorldDirection(new c.position.constructor()); return [0].concat(c.position.toArray(), d.toArray(), c.fov); });
    const dd = (a, b) => Math.hypot(a[1] - b[1], a[2] - b[2], a[3] - b[3], a[4] - b[4], a[5] - b[5], a[6] - b[6], (a[7] - b[7]) / 20);
    const total = dd(f[0], end), bad = [], steps = [];
    for (let i = 1; i < f.length; i++) {
      const far = dd(f[i], end), was = dd(f[i - 1], end), step = dd(f[i], f[i - 1]); steps.push(step);
      if (far > total + 1e-3) bad.push(`${f[i][0]} ms: ${far.toFixed(3)} from the answer, the start was ${total.toFixed(3)}`);
      else if (far > was + 1e-3) bad.push(`${f[i][0]} ms: back up the path (${was.toFixed(3)} to ${far.toFixed(3)})`);
      else if (total > 0.01 && step > total / 3) bad.push(`${f[i][0]} ms: a step of ${step.toFixed(3)} of ${total.toFixed(3)}`);
    }
    console.log(`     22e ${w}x${h}: ${f.length} frames, the camera first moves ${movedAt === null ? "never" : movedAt + " ms after the press"}, whole way ${total.toFixed(3)}, largest step ${Math.max(0, ...steps).toFixed(3)}, first moving frames ${f.filter((x) => dd(x, f[0]) > 1e-4).slice(0, 4).map((x) => x[0] + " ms " + dd(x, end).toFixed(3)).join(", ")}`);
    must(f.length >= 20 && movedAt !== null, `${f.length} frames sampled, the camera moved ${movedAt}`);
    must(!bad.length, `${bad.length} frames off the way: ${bad.slice(0, 3).join("; ")}`);
  }, { phone: true, gpu: true });
}
// (f) the press of "Check the research" paints its first feedback (pressed state or the first progress paint) within
// 100 ms: the Event Timing API's input-to-next-paint for that press. Phone: 375x812 at 3x, touch, CPU slowed 4 times;
// desktop 1280x800. Both on the GPU browser, as block 21.
for (const [w, h, phone] of [[375, 812, true], [1280, 800, false]]) {
  await snappy(`22f: "Check the research" shows its feedback within 100 ms of the press, ${w}x${h}`, async (page) => {
    await page.setViewportSize({ width: w, height: h });
    await page.goto(base + "/", { waitUntil: "load" }); await ready(page); await page.waitForTimeout(1500);
    await page.evaluate(() => { window.__ev = []; new PerformanceObserver((l) => { for (const e of l.getEntries()) if (e.interactionId) window.__ev.push([e.interactionId, e.name, e.startTime, e.duration]); }).observe({ type: "event", durationThreshold: 16, buffered: false }); });
    await page.fill("#claim-input", LIVE); await page.waitForTimeout(400);
    await page.evaluate(() => { window.__ev = []; });
    if (phone) await page.tap("#submit-btn"); else await page.click("#submit-btn");
    await page.waitForTimeout(1500);
    const ev = await page.evaluate(() => window.__ev.filter((e) => /pointer|click|touch|mouse/.test(e[1])));
    const ms = ev.length ? Math.max(...ev.map((e) => e[3])) : 0;
    console.log(`     22f ${w}x${h}: press to next paint ${ms} ms (${ev.map((e) => e[1] + " " + Math.round(e[3])).join(", ")})`);
    must(ms <= 100, `the press painted ${ms} ms after it`);
  }, { phone, gpu: true });
}
// (g) the pressed state 22f paints: Space and Enter on a primary button dim it to --desk-on-2, the words and the focus
// ring as they were, and the key coming up ends it.
await test("22g: a primary button looks pressed under Space and Enter, words and focus ring kept", async (page) => {
  await page.goto(base + "/?nogl=1", { waitUntil: "load" }); await ready(page);
  const look = () => page.evaluate(() => { const b = document.getElementById("submit-btn"), c = getComputedStyle(b);
    return { bg: c.backgroundColor, ink: c.color, ring: [c.outlineStyle, c.outlineColor, c.outlineWidth, c.outlineOffset].join(" "), cls: b.className }; });
  // Enter first, as synthetic key events (their default would submit); then a real Space held down (its key up
  // would click, so the press is ended by blurring, which ends a press as focus leaving does)
  const key = (type, k) => page.evaluate(([type, k]) => document.getElementById("submit-btn").dispatchEvent(new KeyboardEvent(type, { key: k, bubbles: true })), [type, k]);
  await page.focus("#submit-btn"); const a = await look();
  await key("keydown", "Enter"); const en = await look(); await key("keyup", "Enter"); const off = await look();
  await page.keyboard.down(" "); const sp = await look();
  await page.evaluate(() => document.getElementById("submit-btn").blur()); const up = await look(); await page.keyboard.up(" ");
  const P = "rgb(177, 174, 170)";
  must(a.bg !== P && sp.bg === P && en.bg === P, `background at rest ${a.bg}, under Space ${sp.bg}, under Enter ${en.bg}`);
  must(sp.ink === a.ink && en.ink === a.ink && sp.ring === a.ring && en.ring === a.ring, `words ${a.ink} to ${sp.ink}/${en.ink}, ring "${a.ring}" to "${sp.ring}"/"${en.ring}"`);
  must(off.bg === a.bg && !/pressed/.test(off.cls) && !/pressed/.test(up.cls), `after Enter comes up: ${off.bg}, class "${off.cls}"; after Space and blur, class "${up.cls}"`);
});

// ---- 23. After the review of the joined release (q10-rel C2) ------------------------------------------------------
// (a) a result shows exactly one "Check another claim" at every width and in every mode: the one at the end of the page
const againCount = (page) => page.evaluate(() => [...document.querySelectorAll("button, a")]
  .filter((e) => e.textContent.trim() === "Check another claim" && e.checkVisibility() && e.getBoundingClientRect().height > 0).length);
for (const [w, h] of [[375, 812], [1280, 800]]) {
  for (const mode of ["3D", "text mode"]) {
    await test(`23a: a result shows exactly one "Check another claim", ${w}x${h} ${mode}`, async (page) => {
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}${mode === "3D" ? "" : "&nogl=1"}`, { waitUntil: "load" }); await ready(page);
      await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" })); await page.waitForTimeout(800);
      const n = await againCount(page);
      must(n === 1, `${n} displayed`);
    }, { w, h });
  }
  const name = `23a: a result with scripts off shows exactly one "Check another claim", ${w}x${h}`;
  if (!process.env.UI_ONLY || name.startsWith(process.env.UI_ONLY)) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, javaScriptEnabled: false, serviceWorkers: "block" });
    const page = await ctx.newPage();
    try {
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" });
      const n = await againCount(page), href = await page.evaluate(() => { const a = document.querySelector(".nojs-again a"); return a && a.getAttribute("href"); });
      must(n === 1 && href === "/", `${n} displayed, the link goes to ${href}`);
      results.push(true); console.log(`PASS ${name}`);
    } catch (e) { results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`); } finally { await ctx.close(); }
  }
}
// (b) every study row: Open PubMed, and either the Read abstract button or the plain words "No abstract on PubMed"
for (const [w, h] of [[375, 812], [1280, 800]]) {
  await test(`23b: every study row has Open PubMed and Read abstract or "No abstract on PubMed" in its place, ${w}x${h}`, async (page) => {
    await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
    const s = await page.evaluate(() => {
      const vis = (e) => !!e && e.checkVisibility() && e.getBoundingClientRect().height > 0;
      const rows = [...document.querySelectorAll("#rows .row")].map((r) => { const a = r.querySelector(".abs > summary"), no = r.querySelector(".noabs"), pm = r.querySelector("a.pm");
        return { a: !!a, no: !!no, pm: !!pm, noFocus: !no || (no.tabIndex < 0 && !no.closest("button, a, summary")), noText: no ? no.textContent.trim() : "", h: no ? Math.round(no.getBoundingClientRect().height) : 0 }; });
      return { rows, intro: document.querySelector("#list-panel .intro").textContent.trim() };
    });
    const bad = s.rows.filter((r) => r.a === r.no || !r.pm || !r.noFocus || (r.no && r.noText !== "No abstract on PubMed"));
    must(s.rows.length && !bad.length && s.rows.some((r) => r.a) && s.rows.some((r) => r.no), `${s.rows.length} rows, ${bad.length} wrong: ${JSON.stringify(bad[0] || {})}`);
    must(s.intro === "Each study has its PubMed page, and its abstract where PubMed has one.", `intro: "${s.intro}"`);
  }, { w, h });
}

// ---- 24. Evidence handling joined (q10-rel step 2): the recorded vinegar answer, saved by the fixture server ------
// The two statements under How sure, the effect-size sentence printed once, a flagged review's caution once in its
// row, no Indirect label and no empty label, and no bare strength word as a label in the page, the ClaimReview
// markup or the shared text; from the server's HTML with scripts off, and after renderResult (a check from the box).
const VIN = "Apple cider vinegar cure diabetes", VIN_S = "Fasting blood sugar fell by about 22 mg per dL";
const step2Read = (page) => page.evaluate((S) => {
  const words = []; const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) if (!n.parentElement.closest("script, style, template")) words.push(n.nodeValue);
  const text = words.join(" ").replace(/\s+/g, " ");
  const ev = [...document.querySelectorAll("p.evline")], st1 = ev.find((e) => !e.classList.contains("effect-line")), st2 = document.querySelector("p.effect-line");
  const cautions = [...document.querySelectorAll("#rows .row")].map((r) => r.querySelectorAll(".caution").length);
  const flagged = [...document.querySelectorAll("#rows .row .caution")].map((e) => e.textContent.trim());
  const emptyLabels = [...document.querySelectorAll(".lbl")].filter((b) => !b.parentElement.textContent.replace(b.textContent, "").trim()).length;
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map((e) => e.textContent).join(" ");
  return { count: text.split(S).length - 1, st1: st1 ? st1.textContent.trim() : "", st2: st2 ? st2.textContent.trim() : "",
    shown: !!st1 && !!st2 && st1.checkVisibility() && st2.checkVisibility(), cautions, flagged, emptyLabels,
    indirect: /Indirect/.test(text) || !!document.querySelector(".indirect"), bare: (text.match(/\b(Strong|Moderate|Limited|Weak) evidence\b/g) || []).join(),
    ldBare: (ld.match(/\b(Strong|Moderate|Limited|Weak) evidence\b/g) || []).join(), ld: ld.length };
}, VIN_S);
const step2Must = (r, where) => {
  must(r.count === 1, `${where}: the effect-size sentence is on the page ${r.count} times`);
  must(r.shown && r.st1 && r.st2.startsWith("What the studies did find: " + VIN_S), `${where}: the two statements: "${r.st1}" / "${r.st2.slice(0, 80)}"`);
  must(r.cautions.length && r.cautions.every((n) => n <= 1) && r.flagged.filter((t) => t.startsWith("This review cites a study that was later retracted")).length === 1,
    `${where}: cautions per row ${r.cautions.join()}: ${r.flagged.join(" | ").slice(0, 120)}`);
  must(!r.indirect && !r.emptyLabels, `${where}: an Indirect label ${r.indirect}, empty labels ${r.emptyLabels}`);
  must(!r.bare && !r.ldBare && (r.ld > 0 || where !== "scripts off"), `${where}: bare strength words: page "${r.bare}", ClaimReview "${r.ldBare}" (${r.ld} chars of markup)`);
};
for (const [w, h] of [[375, 812], [1280, 800]]) {
  const name = `24a: the vinegar answer with scripts off: two statements, the effect size once, one caution, no bare strength word, ${w}x${h}`;
  if (!process.env.UI_ONLY || name.startsWith(process.env.UI_ONLY)) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, javaScriptEnabled: false, serviceWorkers: "block" });
    const page = await ctx.newPage();
    try {
      await page.goto(`${base}/?q=${encodeURIComponent(VIN)}`, { waitUntil: "load" });
      step2Must(await step2Read(page), "scripts off");
      results.push(true); console.log(`PASS ${name}`);
    } catch (e) { results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`); } finally { await ctx.close(); }
  }
  await test(`24b: the vinegar answer after renderResult, the same, and the shared text has no bare strength word, ${w}x${h}`, async (page) => {
    await page.addInitScript(() => { window.__shared = []; Object.defineProperty(navigator, "share", { value: (d) => { window.__shared.push(d.text || ""); return Promise.resolve(); }, configurable: true }); Object.defineProperty(navigator, "canShare", { value: () => false, configurable: true }); });
    await page.goto(`${base}/?nogl=1`, { waitUntil: "load" }); await ready(page);
    await page.click("#claim-input"); await page.keyboard.type(VIN); await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 }); await page.waitForTimeout(600);
    step2Must(await step2Read(page), "after renderResult");
    await page.evaluate(() => document.getElementById("share-btn").click());
    await page.waitForFunction(() => window.__shared.length > 0, null, { timeout: 5000 });
    const shared = await page.evaluate(() => window.__shared.join(" "));
    must(shared.includes(VIN) && !/\b(Strong|Moderate|Limited|Weak) evidence\b/.test(shared), `the shared text: ${shared.slice(0, 160)}`);
  }, { w, h });
}

await gpuBrowser.close();
await browser.close();
const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} passed`);
process.exit(passed === results.length ? 0 : 1);
