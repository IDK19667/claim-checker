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
 *    closing lines the footer, and Still open a heading and a region of that name.
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
 * 17. The cue row on top of the bar row: in view, as wide, sizes and contrast, its
 *    words from Next's state, a click moves as Next does, gone at the end; the
 *    chevron dips once and never under reduced motion; in 3D a wheel or a drag from
 *    the Answer lands on Read, and text mode scrolls freely with the words following.
 * 21. Snappy: no picture drawn while the home page scrolls or on a settled screen
 *    for 2 s; on the phone profile (CPU slowed 4 times, the Mac's own GPU) the first
 *    Next after a fresh answer moves within 300 ms and glides, and a fresh answer is
 *    on screen within 2.5 s of arriving; once warmed the first Next sends no card
 *    face; a sheet is three draw calls; the claim box is usable as the scripts are in.
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
await test("a desktop window dragged shorter keeps the bar, never sets the keyboard state, and the picture follows the window", async (page) => {
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
      bar: bs.display !== "none" && bs.visibility !== "hidden" && bar.getBoundingClientRect().height > 0, pinned: st.style.height,
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
  await test(`the landmarks on a ${way} result: one banner with the notice, Site navigation, one footer, Still open as heading and region`, async (page) => {
    if (way === "saved") { await page.goto(base + "/?q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page); }
    else { await page.goto(base + "/", { waitUntil: "load" }); await ready(page); await send(page, LIVE);
      await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 }); await page.waitForTimeout(500); }
    const banner = page.getByRole("banner");
    const n = { banner: await banner.count(), nav: await page.getByRole("navigation", { name: "Site", exact: true }).count(),
      foot: await page.getByRole("contentinfo").count(), region: await page.getByRole("region", { name: "Still open", exact: true }).count(),
      heading: await page.getByRole("heading", { name: "Still open", exact: true }).count() };
    const notice = n.banner === 1 && /Not medical advice/.test(await banner.innerText());
    must(notice && n.nav >= 1 && n.foot === 1 && n.region === 1 && n.heading === 1, JSON.stringify({ ...n, notice }));
  });
}

// ---- 17. The cue row: the bottom block's top row says to scroll ----------------------------------
// On a result the bottom controls are two rows: the cue row (one wide button) on top of the bar row.
// At every size, in 3D and text mode: the cue row is in the viewport, directly on top of the bar row
// and as wide as it; 44 px tall or more (48 on phones), words 16 px or more at 7:1 or more, a focus
// ring; out of the tab order and not read (Next, first in the bar, names the same place). Its words
// come from the same state as Next's: the next stop by name ("Scroll down: what was read"), what is
// left of a long stop with the same count ("Scroll down: 11 more studies"), and at the end of the
// last stop it is gone and the block is one row. Stop 2 still sits above the block wherever it did
// before the cue (the dim "Below:" sentence is gone). A click moves exactly as Next does.
const cueLook = (page) => page.evaluate(() => {
  const r = (id) => document.getElementById(id).getBoundingClientRect(), cue = document.getElementById("cue"), c = r("cue"), p = r("prev"), n = r("next"), b = r("bar");
  const bg = (e) => { for (let x = e; x; x = x.parentElement) { const v = getComputedStyle(x).backgroundColor; if (!/rgba\(0, 0, 0, 0\)|transparent/.test(v)) return v; } return "rgb(0, 0, 0)"; };
  const lab = document.getElementById("cue-label"), cs = getComputedStyle(lab);
  return { hidden: cue.hidden || getComputedStyle(cue).display === "none", words: lab.textContent, next: document.getElementById("next").getAttribute("aria-label") || document.getElementById("next-label").textContent, vis: document.getElementById("next-label").textContent,
    stop: window.__evident.stats().stop, c: { t: c.top, b: c.bottom, l: c.left, r: c.right, h: c.height }, row: { t: Math.min(p.top, n.top), l: p.left, r: n.right },
    bar: { t: b.top, h: b.height, l: b.left, r: b.right }, vh: innerHeight, vw: innerWidth, size: parseFloat(cs.fontSize), fg: cs.color, bg: bg(lab),
    ariaHidden: cue.getAttribute("aria-hidden"), tab: cue.tabIndex, svg: cue.querySelector("svg").getBoundingClientRect().width,
    one: document.documentElement.classList.contains("has-cue") };
});
const cueFor = (next, stop) => {
  const m = next.match(/^Keep reading(?:: (\d+) more)?/);
  if (m) return "Scroll down: " + (m[1] ? m[1] + " more " + (m[1] === "1" ? "study" : "studies") : "more of this part");
  if (stop === 1) return "Scroll down: your answer";
  return next.replace(/^Next: /, "Scroll down: ");
};
const FITS2 = new Set(["390x844 3d", "390x844 text", "768x1024 3d", "768x1024 text", "1440x900 3d", "1440x900 text", "1440x700 3d"]);
for (const [w, h] of [[320, 568], [390, 844], [768, 1024], [1440, 900], [1440, 700]]) {
  for (const mode of ["3d", "text"]) {
    await test(`the cue row sits on the bar row, as wide below 761px and centred at 480px above, says what Next says and moves as Next does, ${w}x${h} ${mode}`, async (page) => {
      const wait = mode === "3d" ? 3500 : 1500;
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}${mode === "text" ? "&nogl=1" : ""}`, { waitUntil: "load" }); await ready(page);
      must((await page.evaluate(() => window.__evident.stats().mode)) === mode, "wrong mode");
      await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(wait);
      const s = await cueLook(page);
      must(!s.hidden && s.one, "no cue row on the Answer stop");
      must(s.c.t >= 0 && s.c.b <= s.vh && s.c.t >= s.bar.t - 0.5, `the cue row is not inside the block in view: ${Math.round(s.c.t)}..${Math.round(s.c.b)}, block top ${Math.round(s.bar.t)}`);
      must(s.c.b <= s.row.t && s.row.t - s.c.b <= 12, `the cue row is not directly on top of the bar row (${Math.round(s.c.b)} vs ${Math.round(s.row.t)})`);
      // at 760 wide and below the cue is as wide as the bar row; above, a button no wider than 480px, centred in the block
      if (w <= 760) must(Math.abs(s.c.l - s.row.l) < 1 && Math.abs(s.c.r - s.row.r) < 1, `the cue row is ${Math.round(s.c.l)}..${Math.round(s.c.r)}, the bar row ${Math.round(s.row.l)}..${Math.round(s.row.r)}`);
      else must(s.c.r - s.c.l <= 480.5 && Math.abs((s.c.l - s.bar.l) - (s.bar.r - s.c.r)) <= 1, `the cue is ${Math.round(s.c.l)}..${Math.round(s.c.r)} (${Math.round(s.c.r - s.c.l)} wide) in a block ${Math.round(s.bar.l)}..${Math.round(s.bar.r)}`);
      must(s.c.h >= (w <= 760 ? 48 : 44) - 0.5 && s.size >= 16 && s.svg >= 20, `height ${s.c.h}, words ${s.size}px, chevron ${s.svg}px`);
      must(ratio([s.fg, s.bg]) >= 7, `the cue's words read at ${ratio([s.fg, s.bg]).toFixed(2)}:1`);
      must(s.ariaHidden === "true" && s.tab === -1, `the cue is read or tabbed to (aria-hidden ${s.ariaHidden}, tabIndex ${s.tab})`);
      const ring = await page.evaluate(() => { const c = document.getElementById("cue"); c.focus({ focusVisible: true }); const o = getComputedStyle(c); const r = [o.outlineStyle, parseFloat(o.outlineWidth)]; c.blur(); return r; });
      must(ring[0] !== "none" && ring[1] >= 2, `no focus ring (${ring.join(" ")})`);
      must(s.words === cueFor(s.next, 2), `on the Answer the cue says "${s.words}", Next says "${s.next}"`);
      must(FITS2.has(`${w}x${h} ${mode}`) ? /^Next: /.test(s.next) : true, `the Answer stop no longer fits above the block: Next says "${s.next}"`);
      must(!(await page.evaluate(() => !!document.getElementById("below"))), "the dim Below: line is still there");
      // a click moves as Next does: where Next keeps reading, further down the Answer; where it names Read, onto Read
      let a = s;
      for (let i = 0; i < 6 && a.stop === 2; i++) {
        const keep = /^Keep reading/.test(a.next), y0 = await page.evaluate(() => scrollY);
        await page.click("#cue"); await page.waitForTimeout(wait + 500);
        const was = a; a = await cueLook(page); const y1 = await page.evaluate(() => scrollY);
        must(keep ? a.stop === 2 && y1 > y0 : a.stop === 3, `a click with Next saying "${was.next}" went to stop ${a.stop} (${y0} to ${y1})`);
        must(a.words === cueFor(a.next, a.stop), `after a click: stop ${a.stop}, cue "${a.words}", Next "${a.next}"`);
      }
      must(a.stop === 3, `the cue never reached the Read stop (stop ${a.stop})`);
      must(await page.evaluate(() => document.activeElement.id !== "cue"), "a click left focus on the cue");
      // Weighed at its top: more of the same stop, with the bar's count
      await page.evaluate(() => window.__evident.goto(4)); await page.waitForTimeout(wait);
      const k = await cueLook(page);
      must(/^Keep reading/.test(k.next) && k.words === cueFor(k.next, 4) && !k.hidden, `on Weighed the cue says "${k.words}", Next says "${k.next}"`);
      const y4 = await page.evaluate(() => scrollY);
      await page.click("#cue"); await page.waitForTimeout(wait + 500);
      const k2 = await cueLook(page), y5 = await page.evaluate(() => scrollY);
      must(k2.stop === 4 && y5 > y4, `the cue on Weighed did not keep reading inside it (stop ${k2.stop}, ${y4} to ${y5})`);
      // the end of the last stop: no cue, one row
      await page.evaluate(() => window.__evident.goto(6)); await page.waitForTimeout(wait);
      await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" })); await page.waitForTimeout(1500);
      const e = await cueLook(page);
      must(e.hidden && !e.one && e.next === "Back to the start" && e.bar.h < 90, `at the end: cue hidden ${e.hidden}, Next "${e.next}", block ${Math.round(e.bar.h)} px`);
    }, { w, h });
  }
}
// Next beside the cue is the quiet partner: drawn as Back (border, ground, words), the bare verb on screen
// ("Next" or "Keep reading") and its full words as its name, so the cue is the one paper-filled control.
// At the end of the last stop, with no cue, Next has the paper fill again
const nextLook = (page) => page.evaluate(() => {
  const st = (id) => { const o = getComputedStyle(document.getElementById(id)); return { bg: o.backgroundColor, bd: o.borderTopColor + " " + o.borderTopWidth + " " + o.borderTopStyle, fg: o.color }; };
  const n = document.getElementById("next");
  return { n: st("next"), p: st("prev"), vis: document.getElementById("next-label").textContent, aria: n.getAttribute("aria-label"),
    words: document.getElementById("cue-label").textContent, stop: window.__evident.stats().stop, cue: !document.getElementById("cue").hidden, paper: getComputedStyle(document.documentElement).getPropertyValue("--paper").trim() };
});
for (const [w, h] of [[390, 844], [1440, 900]]) {
  for (const mode of ["3d", "text"]) {
    await test(`beside the cue Next is drawn as Back with the bare verb and its full name, ${w}x${h} ${mode}`, async (page) => {
      const wait = mode === "3d" ? 3500 : 1500;
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}${mode === "text" ? "&nogl=1" : ""}`, { waitUntil: "load" }); await ready(page);
      for (const k of [2, 4]) {
        await page.evaluate((n) => window.__evident.goto(n), k); await page.waitForTimeout(wait);
        const s = await nextLook(page);
        must(s.cue, `no cue on stop ${k}`);
        must(s.n.bg === s.p.bg && s.n.bd === s.p.bd && s.n.fg === s.p.fg, `on stop ${k} Next is ${JSON.stringify(s.n)}, Back ${JSON.stringify(s.p)}`);
        must(s.vis === "Next" || s.vis === "Keep reading", `on stop ${k} Next shows "${s.vis}"`);
        must(!!s.aria && s.aria.startsWith(s.vis) && s.aria.length > s.vis.length, `on stop ${k} Next's name is "${s.aria}", showing "${s.vis}"`);
        must(s.words === cueFor(s.aria, k), `on stop ${k} the cue says "${s.words}", Next's name "${s.aria}"`);
        const bgOf = await page.evaluate(() => { for (let x = document.getElementById("next"); x; x = x.parentElement) { const v = getComputedStyle(x).backgroundColor; if (!/rgba\(0, 0, 0, 0\)|transparent/.test(v)) return v; } return "rgb(0, 0, 0)"; });
        must(ratio([s.n.fg, bgOf]) >= 4.5, `the quiet Next reads at ${ratio([s.n.fg, bgOf]).toFixed(2)}:1`);
      }
      await page.evaluate(() => window.__evident.goto(6)); await page.waitForTimeout(wait);
      await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" })); await page.waitForTimeout(1500);
      const e = await nextLook(page);
      const paperRgb = await page.evaluate(() => { const d = document.createElement("div"); d.style.background = "var(--paper)"; document.body.appendChild(d); const v = getComputedStyle(d).backgroundColor; d.remove(); return v; });
      must(!e.cue && e.vis === "Back to the start" && e.n.bg === paperRgb && e.n.bg !== e.p.bg, `at the end Next shows "${e.vis}" on ${e.n.bg} (paper ${paperRgb})`);
    }, { w, h });
  }
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
// the dip: once, about 3 seconds after an answer the reader just ran lands and nothing was touched;
// never under reduced motion, never on a saved or shared answer, and any input before it cancels it
const dips = (page) => page.evaluate(() => window.__dips || 0);
const dipInit = (page) => page.addInitScript(() => { window.__dips = 0; document.addEventListener("animationstart", (e) => { if (e.animationName === "dip") window.__dips++; }, true); });
for (const [what, opts] of [["after a check the reader just ran, once", {}], ["never under reduced motion", { reduce: true }], ["never on a shared answer", {}], ["not after a key press", {}]]) {
  for (const path of ["/", "/?nogl=1"]) {
    await test(`the cue's chevron dips ${what}, ${path === "/" ? "3D" : "text mode"}`, async (page) => {
      await dipInit(page);
      const shared = what.includes("shared");
      if (shared) { await page.goto(base + path + (path === "/" ? "?" : "&") + "q=" + encodeURIComponent(SAVED[1]), { waitUntil: "load" }); await ready(page); }
      else {
        await page.goto(base + path, { waitUntil: "load" }); await ready(page); await send(page, LIVE);
        await page.waitForFunction(() => document.documentElement.classList.contains("checked"), null, { timeout: 30000 });
        if (what.includes("key")) { await page.waitForTimeout(1000); await page.keyboard.press("Shift"); }
      }
      await page.waitForTimeout(path === "/" ? 12000 : 5000);
      const n1 = await dips(page); await page.waitForTimeout(3000); const n2 = await dips(page);
      const want = what.startsWith("after") ? 1 : 0;
      must(n1 === want && n2 === want, `the chevron dipped ${n1} then ${n2} times, wanted ${want}`);
    }, opts);
  }
}
// scrolling does what the cue says. In 3D a wheel step or a touch drag from the Answer lands on the
// Read stop. Text mode is a freely scrolling page: there the cue's click lands on the next stop, and
// after a free scroll into the Read stop the bar's label and the cue's words follow
for (const [w, h] of [[390, 844], [1440, 900]]) {
  for (const how of ["wheel", "touch"]) {
    await test(`in 3D a ${how} from the Answer lands on the Read stop, ${w}x${h}`, async (page, ctx) => {
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
      await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(3000);
      if (how === "wheel") { await page.mouse.move(w / 2, h / 2); await page.mouse.wheel(0, 100); }
      else { const c = await ctx.newCDPSession(page); await c.send("Input.synthesizeScrollGesture", { x: Math.round(w / 2), y: Math.round(h / 2), yDistance: -150, gestureSourceType: "touch", speed: 600 }); }
      await page.waitForTimeout(6000);
      const s = await page.evaluate(() => { const t = document.querySelectorAll("main .stop")[2]; return { stop: window.__evident.stats().stop, y: Math.round(scrollY), top: Math.round(t.getBoundingClientRect().top + scrollY) }; });
      must(s.stop === 3 && Math.abs(s.y - s.top) <= 1, `stop ${s.stop}, scrollY ${s.y}, the Read stop's top ${s.top}`);
    }, { w, h, touch: how === "touch" });
  }
  await test(`in text mode the cue lands on the next stop, and a free scroll into it carries the bar and the cue along, ${w}x${h}`, async (page) => {
    await page.goto(`${base}/?nogl=1&q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
    await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(1000);
    await page.click("#cue"); await page.waitForTimeout(1500);
    const a = await page.evaluate(() => { const t = document.querySelectorAll("main .stop")[2]; return { stop: window.__evident.stats().stop, y: Math.round(scrollY), top: Math.round(t.getBoundingClientRect().top + scrollY) }; });
    must(a.stop === 3 && Math.abs(a.y - a.top) <= 1, `after the cue: stop ${a.stop}, scrollY ${a.y}, the Read stop's top ${a.top}`);
    await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(1000);
    const d = await page.evaluate(() => { const t = document.querySelectorAll("main .stop")[2]; return Math.round(t.getBoundingClientRect().top) + 40; });
    await page.mouse.move(w / 2, h / 2);
    for (let left = d; left > 0; left -= 100) { await page.mouse.wheel(0, Math.min(100, left)); await page.waitForTimeout(60); }
    await page.waitForTimeout(1500);
    const b = await cueLook(page), y = await page.evaluate(() => scrollY);
    must(b.stop === 3 && b.words === cueFor(b.next, 3) && /^Next: /.test(b.next), `after a free scroll: stop ${b.stop}, Next "${b.next}", cue "${b.words}"`);
    await page.waitForTimeout(1000);
    must(Math.abs((await page.evaluate(() => scrollY)) - y) < 1, "text mode settled the page after a free scroll");
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
await test("19: on a saved answer the warm-up finishes after the answer is readable, and the first Next compiles and sends nothing new", async (page) => {
  await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") return;
  must(await warmed(page), "the warm-up never finished");
  const before = await page.evaluate(() => { const r = window.__evd.renderer; return { p: r.info.programs.length, t: r.info.memory.textures, stop: window.__evident.stats().stop }; });
  await page.evaluate(() => { const r = window.__evd.renderer, gl = r.getContext(); window.__up = 0; const o = gl.texImage2D.bind(gl); gl.texImage2D = function (...a) { window.__up++; return o(...a); }; });
  await page.click("#next"); await page.waitForTimeout(2500);
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
  await page.click("#next");
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
  await until(page, async () => { const r = await navigator.serviceWorker.getRegistration(); return (await caches.keys()).join() === "evident-v122" && r && r.active && r.active.state === "activated"; });
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
  must(st.keys.join() === "evident-v122", `caches: ${st.keys.join()}`);
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
    // after a press: the first movement and the last, from the press's own time stamp
    window.__watch = (ms) => new Promise((res) => {
      let press = null, first = null, lastAt = null, prev = window.__sig(), moves = 0; const t0 = performance.now();
      const pd = (e) => { if (press === null) press = e.timeStamp; };
      addEventListener("pointerdown", pd, true); addEventListener("touchstart", pd, true);
      const loop = () => { const n = performance.now(), s = window.__sig(); if (s !== prev) { if (press !== null && first === null) first = n; lastAt = n; prev = s; moves++; }
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

await snappy("21: on the phone the first Next after a fresh answer starts moving within 300 ms, and glides, after one held-up frame too", async (page) => {
  if (!(await fresh3d(page))) return;
  // one frame held up longer than the whole glide by other work (as the answer arriving does on a slow phone): later moves still glide
  await page.evaluate(() => window.__evident.goto(1)); await page.waitForTimeout(300);
  await page.evaluate(() => { document.getElementById("next").click(); setTimeout(() => { const t = performance.now(); while (performance.now() - t < 2200); }, 60); });
  await still3d(page); await page.evaluate(() => window.__evident.goto(2)); await still3d(page);
  await page.waitForTimeout(1000);
  const w = page.evaluate(() => window.__watch(8000)); await page.waitForTimeout(50);
  await page.tap("#next"); const r = await w;
  must(r.first !== null && r.first <= 300, `first movement ${r.first} ms after the press`);
  // a glide is many pictures; a cut (the slow-machine rule) is one or two
  must(r.moves >= 4, `the move was a cut (${r.moves} changes)`);
});

await snappy("21: once warmed, the first Next after a fresh answer sends no card face to the GPU", async (page) => {
  if (!(await fresh3d(page))) return;
  must(await warmed(page), "the warm-up never finished");
  await page.evaluate(() => { const gl = window.__evd.renderer.getContext(); window.__big = []; const o = gl.texImage2D.bind(gl); gl.texImage2D = function (...a) { const s = a[a.length - 1]; if (s && s.width >= 1600) window.__big.push(s.width + "x" + s.height); return o(...a); }; });
  await page.click("#next"); await page.waitForTimeout(2500);
  const big = await page.evaluate(() => window.__big);
  must(!big.length, `card faces sent after the press: ${big.join(" ")}`);
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

await gpuBrowser.close();
await browser.close();
const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} passed`);
process.exit(passed === results.length ? 0 : 1);
