#!/usr/bin/env node
/**
 * Design QA. Loads the running site at phone and desktop widths in both
 * colour schemes and checks the things that quietly rot between passes:
 * contrast, tap targets, horizontal overflow, console errors, and whether
 * the CSS still matches the tokens written down in DESIGN.md.
 *
 *   node scripts/qa.mjs [baseUrl] [--claim "..."]
 *
 * Exits non-zero if anything fails, so it can gate a change.
 * Needs Playwright's chromium: npx playwright install chromium
 */
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5000";
const claimArg = process.argv.indexOf("--claim");
const claim = claimArg > -1 ? process.argv[claimArg + 1] : "Apple cider vinegar cures diabetes";
const q = (extra) => (extra ? `?${extra}` : "");

const VIEWS = [
  { name: "phone light", width: 390, height: 844, scheme: "light" },
  { name: "phone dark-os", width: 390, height: 844, scheme: "dark" },
  { name: "desktop light", width: 1280, height: 900, scheme: "light" },
  { name: "desktop dark-os", width: 1280, height: 900, scheme: "dark" },
];

const fails = [];
const note = (view, rule, detail) => fails.push({ view, rule, detail });

// ---- DESIGN.md is the written system; the CSS is the shipped one ----------
function tokenDrift() {
  const design = readFileSync("DESIGN.md", "utf8");
  const css = readFileSync("static/style.css", "utf8");
  const fm = design.slice(design.indexOf("---") + 3, design.indexOf("\n---", 3));
  const declared = new Map();
  for (const m of fm.matchAll(/^\s{2}([a-z0-9-]+):\s*"(#[0-9a-fA-F]{3,8}|rgba?\([^"]+\))"/gm)) {
    declared.set(m[1], m[2].toLowerCase());
  }
  const rootStart = css.indexOf(":root {");
  const root = css.slice(rootStart, css.indexOf("\n}", rootStart));
  const live = new Map();
  for (const m of root.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) live.set(m[1], m[2].trim().toLowerCase());
  const out = [];
  for (const [name, value] of live) {
    if (!/^#|^rgba?\(/.test(value)) continue;
    if (![...declared.values()].includes(value)) {
      out.push(`--${name}: ${value} is in style.css but not in DESIGN.md`);
    }
  }
  return out;
}

// ---- in-page audits --------------------------------------------------------
const AUDIT = () => {
  const lum = (rgb) => { const [r, g, b] = rgb.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const parse = (c) => { const m = c.match(/[\d.]+/g); if (!m) return null; return { rgb: m.slice(0, 3).map(Number), a: m.length > 3 ? parseFloat(m[3]) : 1 }; };
  const bgOf = (el) => { let e = el; while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.9) return c.rgb; e = e.parentElement; } return parse(getComputedStyle(document.body).backgroundColor).rgb; };
  const ratio = (f, b) => { const l1 = lum(f), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };

  const contrast = [], targets = [], overflow = [];
  const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    if (!el.offsetParent && cs.position !== "fixed") continue;
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(" ");
    if (own) {
      const f = parse(cs.color);
      if (f) {
        const r = ratio(f.rgb, bgOf(el));
        const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
        const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
        const key = el.className + "|" + cs.color;
        if (r < need && !seen.has(key)) { seen.add(key); contrast.push(`${el.tagName}.${el.className} "${own.slice(0, 22)}" ${r.toFixed(2)}:1 (needs ${need})`); }
      }
    }
    // Every control a thumb has to hit. Measured by hit-testing outward from
    // the centre, not from the box: a small mark with a grown hit area (an
    // inline reference, say) is genuinely tappable and should pass.
    if (/^(BUTTON|A|SUMMARY)$/.test(el.tagName) && innerWidth < 720) {
      const r = el.getBoundingClientRect();
      const owns = (x, y) => { const hit = document.elementFromPoint(x, y); return hit && (hit === el || el.contains(hit) || hit.contains(el)); };
      if (r.width > 0 && r.height > 0 && !el.closest(".src, .query, .foot, .fine")) {
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const reach = (dx, dy) => { let n = 0; for (; n <= 24; n += 2) { const x = cx + dx * (n + 2), y = cy + dy * (n + 2); if (x < 0 || y < 0 || x > innerWidth || y > innerHeight || !owns(x, y)) break; } return n; };
        const h = reach(0, -1) + reach(0, 1) + r.height, w = reach(-1, 0) + reach(1, 0) + r.width;
        if (owns(cx, cy) && (h < 31 || w < 31)) {
          targets.push(`${el.tagName}.${el.className} effective ${Math.round(w)}x${Math.round(h)}`);
        }
      }
    }
    const r = el.getBoundingClientRect();
    if (r.right > innerWidth + 1 && cs.overflowX !== "auto" && cs.position !== "fixed") {
      overflow.push(`${el.tagName}.${String(el.className).slice(0, 24)} extends to ${Math.round(r.right)}px`);
    }
  }
  return {
    contrast, targets: [...new Set(targets)], overflow: [...new Set(overflow)].slice(0, 5),
    scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
    title: document.title, h1: document.querySelectorAll("h1").length,
    // A missing alt is the fault. alt="" is the correct markup for a
    // decorative image (the result page's film still), and a screen reader
    // should skip it rather than be read a description of scenery.
    imgsNoAlt: [...document.images].filter((i) => !i.hasAttribute("alt")).length,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    paperToken: (() => {
      const v = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim();
      const probe = document.createElement("span");
      probe.style.color = v; document.body.appendChild(probe);
      const rgb = getComputedStyle(probe).color; probe.remove();
      return rgb;
    })(),
  };
};

const browser = await chromium.launch();
for (const v of VIEWS) {
  const ctx = await browser.newContext({ viewport: { width: v.width, height: v.height }, colorScheme: v.scheme, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  for (const [label, url] of [["home", `${base}/${q()}`], ["result", `${base}/${q("q=" + encodeURIComponent(claim))}`]]) {
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    const a = await page.evaluate(AUDIT);
    const where = `${v.name} ${label}`;
    a.contrast.forEach((d) => note(where, "contrast", d));
    a.targets.forEach((d) => note(where, "tap target < 32px", d));
    a.overflow.forEach((d) => note(where, "overflows viewport", d));
    if (a.scrollW > a.innerW + 1) note(where, "horizontal scroll", `${a.scrollW} > ${a.innerW}`);
    if (a.h1 !== 1) note(where, "headings", `${a.h1} <h1> elements`);
    if (a.imgsNoAlt) note(where, "images", `${a.imgsNoAlt} without alt`);
    if (!a.title) note(where, "title", "empty <title>");
    // There is no dark stock: a dark system preference must not darken the
    // page. Compared against the declared --paper token, not a literal, so a
    // palette change cannot leave this check quietly testing the wrong colour.
    if (a.bodyBg.replace(/\s/g, "") !== a.paperToken.replace(/\s/g, "")) {
      note(where, "background is not paper", `${a.bodyBg} vs --paper ${a.paperToken}`);
    }
    await page.screenshot({ path: `/tmp/qa-${v.name.replace(/\s/g, "-")}-${label}.png`, fullPage: label === "result" });

    // The type-ahead only exists while someone is typing, so a page load
    // never sees it. Open it deliberately and audit it like any other
    // surface, or it rots unwatched.
    if (label === "home") {
      await page.locator("#claim-input").click();
      await page.locator("#claim-input").fill("turmaric inflamation");
      await page.dispatchEvent("#claim-input", "input");
      await page.waitForTimeout(600);
      const open = await page.locator(".suggest-row").count();
      if (!open) {
        note(where, "type-ahead", "no suggestions for a known misspelling");
      } else {
        const sa = await page.evaluate(AUDIT);
        sa.contrast.forEach((d) => note(`${where} type-ahead`, "contrast", d));
        sa.targets.forEach((d) => note(`${where} type-ahead`, "tap target < 32px", d));
        if (sa.scrollW > sa.innerW + 1) note(`${where} type-ahead`, "horizontal scroll", `${sa.scrollW} > ${sa.innerW}`);
        const aria = await page.getAttribute("#claim-input", "aria-expanded");
        if (aria !== "true") note(`${where} type-ahead`, "aria", `aria-expanded is ${aria}`);
        await page.screenshot({ path: `/tmp/qa-${v.name.replace(/\s/g, "-")}-suggest.png` });
      }
      await page.locator("#claim-input").fill("");
      await page.dispatchEvent("#claim-input", "input");
    }
  }
  errors.forEach((e) => note(v.name, "console error", e.slice(0, 120)));
  await ctx.close();
}
// ---- the fly-through ---------------------------------------------------------
// Its own pass, on a desktop that gets the whole thing. Every check here is
// about behaviour over time, which a single page load never sees.
{
  const where = "fly-through";
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  // Counts every animation frame the page asks for, from anything on it.
  await ctx.addInitScript(() => {
    window.__raf = 0;
    const raf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => { window.__raf++; return raf(cb); };
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${base}/`, { waitUntil: "load" });
  await page.waitForFunction(() => window.__flightStats && window.__flightStats.frameCount > 0, null, { timeout: 20000 });
  const idle = [];
  await page.waitForFunction(() => { const p = document.getElementById("flight-progress"); return p && p.hidden; },
    null, { timeout: 90000 }).catch(() => note(where, "footage", "still loading after 90s"));

  // A page sitting still asks for no animation frames: not at rest inside the
  // fly-through, not scrolled past it, not parked behind a check. Momentum and
  // a few quiet refreshes are allowed to die away first.
  const still = async (label) => {
    await page.waitForTimeout(1200);
    const a = await page.evaluate(() => window.__raf);
    await page.waitForTimeout(2000);
    const n = (await page.evaluate(() => window.__raf)) - a;
    idle.push(`${label} ${n}`);
    if (n > 2) note(where, "loop never rests", `${label}: ${n} animation frames in 2s of stillness`);
  };
  await still("at the top");
  await page.mouse.wheel(0, 1500);
  await still("stopped inside it");
  // Still asleep is fine; asleep through a scroll is not. And where the
  // scroll stops, the frame on screen is the frame for that position.
  const ticks = await page.evaluate(() => window.__flightStats.ticks);
  await page.mouse.wheel(0, 400);
  await page.waitForTimeout(1500);
  const s = await page.evaluate(() => ({ ticks: window.__flightStats.ticks, wanted: window.__flightStats.wanted, drawn: window.__flightStats.drawn }));
  if (s.ticks === ticks) note(where, "loop does not wake", "a scroll ran no refreshes");
  if (Math.abs(s.wanted - s.drawn) > 2) note(where, "settled on the wrong frame", `drawn ${s.drawn}, wanted ${s.wanted}`);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await still("scrolled past it");
  await page.evaluate(() => { window.scrollTo(0, 1500); window.EvidentFlight.park(); });
  await page.mouse.wheel(0, 200);
  await still("parked");
  await page.evaluate(() => window.EvidentFlight.resume());
  console.log(`fly-through: animation frames in 2s of stillness: ${idle.join(", ")}`);

  errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
  await ctx.close();
}

// ---- the still version's live gates ----------------------------------------
// Reduced motion and a phone on its side are followed during the visit, in
// both directions, and a rotation never leaves the stage blank.
{
  // What a reader would see: the canvas lit with a frame or not, the stills,
  // the claim box, and anything paint() left inline that would fight the CSS.
  const look = (page) => page.evaluate(() => {
    const shown = (e) => {
      if (!e) return false;
      for (let n = e; n && n !== document.body; n = n.parentElement) {
        const cs = getComputedStyle(n);
        if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) < 0.99) return false;
      }
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const c = document.getElementById("flight-canvas");
    // An opaque canvas with nothing drawn is pure black: lit means some pixel
    // on a 3x3 grid across it is not.
    let lit = false;
    if (shown(c) && c.width) {
      const g = c.getContext("2d");
      for (let i = 1; i <= 3 && !lit; i++) for (let j = 1; j <= 3 && !lit; j++) {
        const d = g.getImageData(Math.floor(c.width * i / 4), Math.floor(c.height * j / 4), 1, 1).data;
        if (d[0] + d[1] + d[2] > 12) lit = true;
      }
    }
    const s = window.__flightStats;
    return {
      armed: !!(s && s.armed()), off: document.documentElement.getAttribute("data-flight-off"),
      canvas: shown(c), lit, width: Math.round(c.getBoundingClientRect().width),
      stills: shown(document.querySelector(".stills")), input: shown(document.getElementById("claim-input")),
      // Declarations, not the attribute: Chromium can leave an empty style="".
      inline: [...document.querySelectorAll(".work-layer, .chapter-zone, .counter")].filter((n) => n.style.length).length,
      gap: s ? Math.abs(s.wanted - s.drawn) : 0, ticks: s ? s.ticks : 0,
    };
  });
  const stillsShown = (where, v, why) => {
    if (v.armed || v.off !== why) note(where, "gate", `expected the stills for ${why}, got armed ${v.armed}, off ${v.off}`);
    if (v.canvas) note(where, "gate", "the canvas still shows over the stills");
    if (!v.stills) note(where, "gate", "the stills are not shown");
    if (!v.input) note(where, "gate", "the claim box is not visible on the stills");
    if (v.inline) note(where, "gate", `${v.inline} panel(s) kept inline styles from the footage`);
  };
  const footageShown = (where, v, width) => {
    if (!v.armed || v.off) note(where, "gate", `expected the footage, got armed ${v.armed}, off ${v.off}`);
    if (!v.canvas || !v.lit) note(where, "blank stage", `canvas shown ${v.canvas}, a frame on it ${v.lit}`);
    if (v.stills) note(where, "gate", "the stills show under the footage");
    if (width && Math.abs(v.width - width) > 2) note(where, "stage size", `canvas ${v.width}px wide in a ${width}px window`);
    if (v.gap > 2) note(where, "settled on the wrong frame", `${v.gap} frames from the scroll position`);
  };
  const open = async (opts) => {
    const ctx = await browser.newContext({ deviceScaleFactor: 1, ...opts });
    const page = await ctx.newPage();
    const errors = [];
    const asked = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("request", (r) => asked.push(r.url()));
    await page.goto(`${base}/`, { waitUntil: "load" });
    return { ctx, page, errors, asked };
  };
  const drawn = (page, where) => page.waitForFunction(() =>
    document.getElementById("flight-canvas").hasAttribute("data-drawn"), null, { timeout: 30000 })
    .catch(() => note(where, "footage", "no frame drawn within 30s"));

  // Reduced motion switched on mid-visit, then off again.
  {
    const where = "reduced motion, mid-visit";
    const { ctx, page, errors } = await open({ viewport: { width: 1280, height: 900 } });
    await drawn(page, where);
    await page.evaluate(() => window.scrollTo(0, 1200));
    await page.waitForTimeout(1200);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.waitForTimeout(500);
    const on = await look(page);
    stillsShown(where + ", switched on", on, "reduced-motion");
    await page.waitForTimeout(1500);
    if ((await look(page)).ticks !== on.ticks) note(where, "loop never rests", "the loop kept running behind the stills");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.waitForTimeout(300);
    await page.evaluate(() => window.scrollBy(0, 300));
    await page.waitForTimeout(1500);
    footageShown(where + ", switched off", await look(page), 1280);
    errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
    await ctx.close();
  }

  // A tablet turned on its side and back: the stage changes shape and is
  // redrawn at the new size, never left blank.
  {
    const where = "tablet rotation";
    const { ctx, page, errors } = await open({ viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true });
    await drawn(page, where);
    await page.evaluate(() => window.scrollTo(0, 900));
    await page.waitForTimeout(1500);
    footageShown(where + ", upright", await look(page), 820);
    await page.setViewportSize({ width: 1180, height: 820 });
    await page.waitForTimeout(1200);
    footageShown(where + ", on its side", await look(page), 1180);
    await page.setViewportSize({ width: 820, height: 1180 });
    await page.waitForTimeout(1200);
    footageShown(where + ", upright again", await look(page), 820);
    errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
    await ctx.close();
  }

  // A phone opened on its side gets the stills and fetches no footage; turned
  // upright, it gets the fly-through (its own phone cut, after a scroll).
  {
    const where = "phone on its side";
    const { ctx, page, errors, asked } = await open({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    await page.waitForTimeout(800);
    stillsShown(where, await look(page), "landscape-phone");
    if (asked.some((u) => /manifest[^/]*\.json/.test(u))) note(where, "footage", "fetched the fly-through while showing the stills");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    await page.evaluate(() => window.scrollTo(0, 1));
    await drawn(page, where + ", turned upright");
    await page.waitForTimeout(800);
    footageShown(where + ", turned upright", await look(page), 390);
    if (!asked.some((u) => /manifest-phone\.json/.test(u))) note(where, "footage", "upright, it did not load the phone cut");
    errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
    await ctx.close();
  }
  console.log("live gates: reduced motion on and off, tablet rotation, phone on its side and upright: ran");
}

// ---- pausing what nobody can see --------------------------------------------
// The band's clip plays only while someone can see it, and a hidden tab
// freezes every CSS animation. Driven through the page's own controllers, so
// it needs no check (and no model) to run.
{
  const where = "pause when unseen";
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${base}/`, { waitUntil: "load" });
  const setHidden = (h) => page.evaluate((h) => {
    Object.defineProperty(document, "hidden", { value: h, configurable: true });
    Object.defineProperty(document, "visibilityState", { value: h ? "hidden" : "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  }, h);

  // The looping status dot, on the home page: running in view, held out of it.
  // After the fly-through has sized itself, which moves everything below it.
  await page.waitForFunction(() => window.__flightStats && window.__flightStats.frameCount > 0, null, { timeout: 20000 });
  await page.waitForTimeout(300);
  const dot = () => page.evaluate(() => getComputedStyle(document.getElementById("status"), "::before").animationPlayState);
  await page.evaluate(() => {
    const st = document.getElementById("status");
    st.classList.add("working");
    st.scrollIntoView({ block: "center" });
  });
  await page.waitForTimeout(400);
  if (await dot() !== "running") note(where, "loop", "the working dot does not run in view");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  if (await dot() !== "paused") note(where, "loop", "the working dot kept looping out of view");
  await page.evaluate(() => { window.scrollTo(0, 0); document.getElementById("status").classList.remove("working"); });

  // The band, on a check screen with no check behind it, made tall enough to
  // scroll the band away.
  await page.evaluate(() => { screenState.enter(); document.querySelector("main.wrap").style.minHeight = "3000px"; });
  const playing = () => page.evaluate(() => {
    const v = document.querySelector('#film video.film-shot[data-on="1"]');
    return v ? !v.paused : null;
  });
  const started = await page.waitForFunction(() => {
    const v = document.querySelector('#film video.film-shot[data-on="1"]');
    return v && !v.paused && v.currentTime > 0;
  }, null, { timeout: 15000 }).then(() => true, () => false);
  if (!started) note(where, "band", "the clip never started");
  else {
    await setHidden(true);
    const hid = await page.evaluate(() => ({
      body: document.body.classList.contains("paused"),
      clip: document.querySelector('#film video.film-shot[data-on="1"]').paused,
      sheet: getComputedStyle(document.querySelector("main.wrap")).animationPlayState,
    }));
    if (!hid.body) note(where, "tab hidden", "body.paused was not set");
    if (!hid.clip) note(where, "tab hidden", "the clip kept playing");
    if (hid.sheet !== "paused") note(where, "tab hidden", `the sheet's animation is ${hid.sheet}`);
    await setHidden(false);
    await page.waitForTimeout(300);
    if (!(await playing())) note(where, "tab shown", "the clip did not resume");
    await page.evaluate(() => window.scrollTo(0, 2000));
    await page.waitForTimeout(400);
    if (await playing()) note(where, "scrolled away", "the clip kept playing out of view");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(400);
    if (!(await playing())) note(where, "scrolled back", "the clip did not resume");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.waitForTimeout(300);
    if (await playing()) note(where, "reduced motion on", "the clip kept playing");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.waitForTimeout(400);
    if (!(await playing())) note(where, "reduced motion off", "the clip did not come back");
  }
  errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
  await ctx.close();
  console.log("pause when unseen: hidden tab, band scrolled away, looping dot out of view, reduced motion: ran");
}

// ---- captions under flicks, and text over the footage ------------------------
// Walks the fly-through in flicks of 120, 240 and 360px, on a desktop and on
// a touch phone. Every caption has to reach full strength at every flick size,
// and at 120px (one wheel notch) stay there for at least five flicks. At each
// 120px stop, every piece of text drawn over the footage is measured against
// the frame actually under it, and the worst stop has to clear 3.5:1.
const OVER_FOOTAGE = () => {
  const c = document.getElementById("flight-canvas");
  const cr = c.getBoundingClientRect();
  const g = c.getContext("2d");
  const parse = (s) => { const m = s.match(/[\d.]+/g) || ["0", "0", "0", "0"]; return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1]; };
  const lum = (p) => { const [r, gg, b] = p.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * gg + 0.0722 * b; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (top, under) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3]));
  const mix = (a, b, o) => [0, 1, 2].map((i) => a[i] * o + b[i] * (1 - o));
  const out = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!el.closest(".flight-stage") && getComputedStyle(el).position !== "fixed" && !el.closest(".top")) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || getComputedStyle(el).visibility !== "visible") continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const stack = document.elementsFromPoint(cx, cy);
    if (!stack.length || !el.contains(stack[0]) || !stack.includes(c)) continue;
    // The layers between the frame and the glyph: every box from the text up
    // to the first one that also holds the canvas, each with its own
    // background and its own opacity.
    const chain = [];
    for (let n = el; n && !n.contains(c); n = n.parentElement) chain.unshift(n);
    const styles = chain.map((n) => {
      const cs = getComputedStyle(n), b = n.getBoundingClientRect();
      const covers = cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
      return { o: parseFloat(cs.opacity), bg: covers ? parse(cs.backgroundColor) : [0, 0, 0, 0], color: parse(cs.color) };
    });
    const strength = styles.reduce((s, x) => s * x.o, 1);
    if (strength < 0.99) continue;
    const render = (P, k, text) => {
      const s = styles[k];
      const back = over(s.bg, P);
      const inner = k === styles.length - 1 ? (text ? over(s.color, back) : back) : render(back, k + 1, text);
      return mix(inner, P, s.o);
    };
    let worst = Infinity;
    const sx = c.width / cr.width, sy = c.height / cr.height;
    for (let i = 0; i < 6; i++) for (let j = 0; j < 3; j++) {
      const x = Math.max(cr.left, Math.min(cr.right - 1, r.left + r.width * (i + 0.5) / 6));
      const y = Math.max(cr.top, Math.min(cr.bottom - 1, r.top + r.height * (j + 0.5) / 3));
      const d = g.getImageData(Math.floor((x - cr.left) * sx), Math.floor((y - cr.top) * sy), 1, 1).data;
      const P = [d[0], d[1], d[2]];
      worst = Math.min(worst, ratio(render(P, 0, true), render(P, 0, false)));
    }
    out.push({ text: el.textContent.trim().replace(/\s+/g, " ").slice(0, 30), ratio: worst });
  }
  return out;
};
const settle = (page) => page.evaluate(() => new Promise((done) => {
  // Three refreshes: the scroll event, the tick it wakes, and the paint.
  requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(done)));
}));
for (const view of [
  { name: "desktop", opts: { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 } },
  { name: "touch phone", opts: { viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
]) {
  const where = `flicks, ${view.name}`;
  const ctx = await browser.newContext(view.opts);
  const page = await ctx.newPage();
  const errors = [];
  const frames = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("request", (r) => { if (/\.(avif|bundle)(\?|$)/.test(r.url())) frames.push(r.url()); });
  await page.goto(`${base}/`, { waitUntil: "load" });
  // The claim box is usable before any footage: a phone has asked for no
  // frame but the one still poster under the box, and a tap puts the cursor
  // in the box.
  if (view.opts.hasTouch) {
    await page.waitForTimeout(800);
    const poster = await page.evaluate(() => {
      const m = (document.querySelector(".flight-stage")?.getAttribute("style") || "").match(/url\('([^']+)'\)/);
      return m ? new URL(m[1], location.href).href : "";
    });
    const early = frames.filter((u) => u !== poster);
    if (early.length) note(where, "phone budget", `${early.length} footage request(s) before the first scroll, e.g. ${early[0]}`);
    await page.tap("#claim-input");
    if (!(await page.evaluate(() => document.activeElement && document.activeElement.id === "claim-input"))) {
      note(where, "touch", "tapping the claim box did not focus it");
    }
    await page.evaluate(() => document.activeElement.blur());
  }
  await page.evaluate(() => window.scrollTo(0, 1));
  await page.waitForFunction(() => document.getElementById("flight-canvas").hasAttribute("data-drawn"), null, { timeout: 30000 })
    .catch(() => note(where, "footage", "no frame drawn within 30s"));
  await page.waitForFunction(() => { const p = document.getElementById("flight-progress"); return p && p.hidden; },
    null, { timeout: 90000 }).catch(() => note(where, "footage", "still loading after 90s"));
  const span = await page.evaluate(() => {
    const f = document.getElementById("flight");
    return { top: f.offsetTop, end: f.offsetTop + f.offsetHeight - innerHeight, ids: [...f.querySelectorAll(".chapter")].map((c) => c.id) };
  });
  const held = {};
  let worst = { ratio: Infinity, text: "", y: 0 };
  for (const step of [120, 240, 360]) {
    const reached = new Set();
    const run = {};
    let last = null, streak = 0;
    for (let y = span.top; y <= span.end + step; y += step) {
      await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y);
      await settle(page);
      const s = await page.evaluate(() => {
        const zone = document.querySelector(".chapter-zone");
        const on = document.querySelector(".chapter[data-on='1']");
        return { id: on ? on.id : null, op: on && zone && zone.style.opacity !== "" ? parseFloat(zone.style.opacity) : 0 };
      });
      const full = s.id && s.op >= 0.99 ? s.id : null;
      streak = full && full === last ? streak + 1 : full ? 1 : 0;
      last = full;
      if (full) {
        run[full] = Math.max(run[full] || 0, streak);
        // The first time a caption is at full strength, let its fade finish
        // and confirm the page really draws it that way.
        if (step === 120 && !reached.has(full)) {
          await page.waitForTimeout(450);
          const shown = await page.evaluate((id) => {
            const zone = document.querySelector(".chapter-zone");
            return parseFloat(getComputedStyle(zone).opacity) * parseFloat(getComputedStyle(document.getElementById(id)).opacity);
          }, full);
          if (shown < 0.99) note(where, "caption", `${full} is meant to be at full strength but draws at ${shown.toFixed(2)}`);
        }
        reached.add(full);
      }
      if (step === 120) {
        await page.waitForFunction(() => { const st = window.__flightStats; return Math.abs(st.wanted - st.drawn) <= 2; },
          null, { timeout: 2000 }).catch(() => {});
        for (const t of await page.evaluate(OVER_FOOTAGE)) {
          if (t.ratio < worst.ratio) worst = { ...t, y };
        }
      }
    }
    for (const id of span.ids) {
      if (!reached.has(id)) note(where, "caption skipped", `${id} never reaches full strength in ${step}px flicks`);
      if (step === 120) {
        held[id] = run[id] || 0;
        if ((run[id] || 0) < 5) note(where, "caption too short", `${id} holds for ${run[id] || 0} flick(s) of 120px, needs 5`);
      }
    }
  }
  if (worst.ratio < 3.5) note(where, "text over footage", `"${worst.text}" falls to ${worst.ratio.toFixed(2)}:1 at ${worst.y}px`);
  console.log(`flicks, ${view.name}: 120px flicks each caption holds: ${span.ids.map((id) => `${id.replace("ch-", "")} ${held[id]}`).join(", ")}; ` +
    `section ${span.end - span.top + 0}px of scroll; worst text over footage ${worst.ratio === Infinity ? "none found" : `${worst.ratio.toFixed(2)}:1 ("${worst.text}")`}`);
  errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
  await ctx.close();
}

// ---- the page with no footage at all ------------------------------------------
// Every footage URL refused, as a slow or filtering network might. The page
// is still whole: the claim box works, the words are all there, nothing says
// it is loading forever, and the only errors are the refused requests.
for (const view of [
  { name: "desktop", opts: { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 } },
  { name: "touch phone", opts: { viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
]) {
  const where = `footage blocked, ${view.name}`;
  const ctx = await browser.newContext(view.opts);
  await ctx.route(/\/(footage|static\/footage|static\/flight)\//, (r) => r.abort());
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${base}/`, { waitUntil: "load" });
  await page.evaluate(() => window.scrollTo(0, 1));
  await page.waitForTimeout(2500);
  const v = await page.evaluate(() => {
    const shown = (e) => {
      if (!e) return false;
      for (let n = e; n && n !== document.body; n = n.parentElement) {
        const cs = getComputedStyle(n);
        if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) < 0.5) return false;
      }
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const p = document.getElementById("flight-progress");
    return {
      input: shown(document.getElementById("claim-input")),
      trust: shown(document.getElementById("trust")),
      loading: shown(p),
      chapters: [...document.querySelectorAll(".chapter p")].filter((n) => n.textContent.trim()).length,
      scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
    };
  });
  if (!v.input) note(where, "claim box", "not visible");
  if (!v.trust) note(where, "trust section", "not visible");
  if (v.chapters < 5) note(where, "captions", `${v.chapters} of 5 captions in the page`);
  if (v.scrollW > v.innerW + 1) note(where, "horizontal scroll", `${v.scrollW} > ${v.innerW}`);
  // Scroll to the box the way a reader would, then type into it.
  await page.locator("#claim-input").scrollIntoViewIfNeeded();
  if (view.opts.hasTouch) await page.tap("#claim-input"); else await page.click("#claim-input");
  await page.keyboard.type("Vitamin C prevents colds");
  if ((await page.inputValue("#claim-input")) !== "Vitamin C prevents colds") note(where, "claim box", "typing did not reach the box");
  if (v.loading) note(where, "stuck loading", "the loading note is still up with no footage coming");
  await page.screenshot({ path: `/tmp/qa-footage-blocked-${view.name.replace(/\s/g, "-")}.png` });
  errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
  await ctx.close();
}
console.log("footage blocked: claim box, captions, trust section and console checked on desktop and a touch phone");

await browser.close();

tokenDrift().forEach((d) => note("tokens", "design system drift", d));

if (!fails.length) {
  console.log("QA passed: contrast, tap targets, overflow, console, tokens. Screenshots in /tmp/qa-*.png");
  process.exit(0);
}
console.log(`QA found ${fails.length} problem(s):\n`);
for (const f of fails) console.log(`  [${f.view}] ${f.rule}: ${f.detail}`);
process.exit(1);
