#!/usr/bin/env node
/**
 * Design QA for the lamp-lit desk front and the pages beside it. Loads the
 * running site at phone and desktop sizes in both colour schemes, in 3D and in
 * text mode, and checks the things that quietly rot between passes: contrast,
 * tap targets, text size, horizontal overflow, console errors and CSP
 * violations, one visible h1 at the size DESIGN.md gives, the ground each page
 * stands on, the first screen on short laptop screens, and whether the CSS
 * still uses only colours written down in DESIGN.md.
 *
 *   node scripts/qa.mjs [baseUrl] [--claim "..."]
 *
 * It never runs a check (no POST to /api/check): the result it audits is a
 * saved one, opened by its address. Exits non-zero if anything fails.
 * Screenshots go to $QA_OUT (default /tmp) as qa-*.png.
 * Needs Playwright's chromium: npx playwright install chromium
 */
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5000";
const claimArg = process.argv.indexOf("--claim");
const claim = claimArg > -1 ? process.argv[claimArg + 1] : "Apple cider vinegar cures diabetes";
const OUT = (process.env.QA_OUT || "/tmp").replace(/\/$/, "");
const shotName = (s) => `${OUT}/qa-${s.replace(/[^a-z0-9]+/gi, "-").replace(/-$/, "")}.png`;

const VIEWS = [
  { name: "phone light", width: 390, height: 844, scheme: "light" },
  { name: "phone dark-os", width: 390, height: 844, scheme: "dark" },
  { name: "desktop light", width: 1440, height: 900, scheme: "light" },
  { name: "desktop dark-os", width: 1440, height: 900, scheme: "dark" },
];
// The front (index.html, desk.css) stands on the night; every other page keeps style.css and stands on paper.
const PAGES = [
  ["home", "/", "desk"], ["home text mode", "/?nogl=1", "desk"], ["result", "/?q=" + encodeURIComponent(claim), "desk"],
  ["about", "/about", "paper"], ["method", "/method", "paper"], ["corrections", "/corrections", "paper"],
  ["privacy", "/privacy", "paper"], ["404", "/no-such-page", "paper"],
];

const fails = [];
const note = (view, rule, detail) => fails.push({ view, rule, detail });

// ---- DESIGN.md is the written system; the two stylesheets are the shipped one ----
function tokenDrift() {
  const design = readFileSync("DESIGN.md", "utf8");
  const fm = design.slice(design.indexOf("---") + 3, design.indexOf("\n---", 3));
  const declared = new Set();
  for (const m of fm.matchAll(/^\s{2}([a-z0-9-]+):\s*"(#[0-9a-fA-F]{3,8}|rgba?\([^"]+\))"/gm)) declared.add(m[2].toLowerCase().replace(/\s/g, ""));
  const out = [];
  for (const file of ["static/desk.css", "static/style.css"]) {
    const css = readFileSync(file, "utf8");
    const rootStart = css.indexOf(":root {");
    const root = css.slice(rootStart, css.indexOf("\n}", rootStart));
    for (const m of root.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
      const value = m[2].trim().toLowerCase();
      if (!/^#|^rgba?\(/.test(value)) continue;
      if (!declared.has(value.replace(/\s/g, ""))) out.push(`--${m[1]}: ${value} is in ${file} but not in DESIGN.md`);
    }
  }
  return out;
}

// ---- in-page audits --------------------------------------------------------
const AUDIT = () => {
  const lum = (rgb) => { const [r, g, b] = rgb.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  // Painted through a canvas rather than read as numbers: a computed colour can
  // come back as oklab() (from color-mix) as well as rgb().
  const cv = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const parse = (c) => { if (!c) return null; cv.clearRect(0, 0, 1, 1); cv.fillStyle = "#000"; cv.fillStyle = c; cv.fillRect(0, 0, 1, 1); const [r, g, b, a] = cv.getImageData(0, 0, 1, 1).data; return { rgb: [r, g, b], a: a / 255 }; };
  // In 3D the answer card is painted on the paper on the desk and its words are laid over it as real text: their
  // ground is the card stock, not the night under the scene.
  const card = getComputedStyle(document.documentElement).getPropertyValue("--desk-card").trim();
  const bgOf = (el) => { if (card && document.documentElement.classList.contains("has-gl") && el.closest(".vcard")) return parse(card).rgb; let e = el; while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.9) return c.rgb; e = e.parentElement; } return parse(getComputedStyle(document.body).backgroundColor).rgb; };
  const ratio = (f, b) => { const l1 = lum(f), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };

  const contrast = [], targets = [], overflow = [], smallText = [];
  const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(" ");
    if (own) {
      const f = parse(cs.color);
      // words the desk paints into the 3D scene are laid over it as transparent text for selection and screen readers
      if (f && f.a > 0.1) {
        const r = ratio(f.rgb, bgOf(el));
        const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
        const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
        const key = el.className + "|" + cs.color;
        if (r < need && !seen.has(key)) { seen.add(key); contrast.push(`${el.tagName}.${el.className} "${own.slice(0, 22)}" ${r.toFixed(2)}:1 (needs ${need})`); }
      }
    }
    // Every control a thumb has to hit: 44px or more, hit-tested outward from
    // its centre, so a grown hit area passes. A plain link inside a sentence
    // of running text is exempt (WCAG 2.5.8 inline exception).
    if (/^(BUTTON|A|SUMMARY)$/.test(el.tagName)) {
      el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
      const r = el.getBoundingClientRect();
      const owns = (x, y) => { const hit = document.elementFromPoint(x, y); return hit && (hit === el || el.contains(hit)); };
      const around = (el.parentElement ? el.parentElement.textContent : "").replace(el.textContent, "").trim().length;
      const inProse = cs.display === "inline" && around >= 40 && !el.classList.contains("ref") && !el.closest(".foot, .src, .query, .fine");
      if (r.width > 0 && r.height > 0 && !inProse && !el.closest(".sr-only")) {
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const reach = (dx, dy) => { let n = 0; for (; n <= 30; n += 1) { const x = cx + dx * (n + 0.5 + (dx ? r.width / 2 : 0)), y = cy + dy * (n + 0.5 + (dy ? r.height / 2 : 0)); if (x < 0 || y < 0 || x > innerWidth || y > innerHeight || !owns(x, y)) break; } return n; };
        const h = reach(0, -1) + reach(0, 1) + r.height, w = reach(-1, 0) + reach(1, 0) + r.width;
        if (owns(cx, cy) && (h < 43.5 || w < 43.5)) targets.push(`${el.tagName}.${String(el.className).slice(0, 24)} "${el.textContent.trim().slice(0, 16)}" effective ${Math.round(w)}x${Math.round(h)}`);
      }
    }
    // Nothing a reader needs is under 13px.
    if (own && parseFloat(cs.fontSize) < 13 && !el.closest(".sr-only")) smallText.push(`${el.tagName}.${String(el.className).slice(0, 24)} ${cs.fontSize}`);
    const r = el.getBoundingClientRect();
    if (r.right > innerWidth + 1 && cs.overflowX !== "auto" && cs.position !== "fixed" && !el.closest("#stage")) overflow.push(`${el.tagName}.${String(el.className).slice(0, 24)} extends to ${Math.round(r.right)}px`);
  }
  window.scrollTo({ top: 0, behavior: "instant" });
  const h1s = [...document.querySelectorAll("h1")].filter((h) => h.checkVisibility());
  const tok = (name) => { const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); if (!v) return ""; const p = document.createElement("span"); p.style.color = v; document.body.appendChild(p); const rgb = getComputedStyle(p).color; p.remove(); return rgb; };
  return {
    contrast, smallText: [...new Set(smallText)], targets: [...new Set(targets)], overflow: [...new Set(overflow)].slice(0, 5),
    scrollW: document.documentElement.scrollWidth, innerW: innerWidth, title: document.title,
    h1: h1s.length, h1px: h1s.length ? parseFloat(getComputedStyle(h1s[0]).fontSize) : 0, h1id: h1s.length ? h1s[0].id : "",
    imgsNoAlt: [...document.images].filter((i) => !i.hasAttribute("alt")).length,
    bodyBg: getComputedStyle(document.body).backgroundColor, night: tok("--night"), paper: tok("--paper"),
    nma: [...document.querySelectorAll("body *")].some((e) => e.checkVisibility() && /Not medical advice/i.test(e.textContent) && e.children.length < 4),
    csp: window.__csp || [],
  };
};

const ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
const browser = await chromium.launch({ args: ARGS });
async function newPage(opts) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  const errors = [];
  // the 404 page answers with status 404 on purpose; the browser logs that as an error for the page itself
  page.on("console", (m) => { if (m.type() === "error" && !(/status of 404/.test(m.text()) && /\/no-such-page$/.test(m.location().url))) errors.push(`${m.text()} (${m.location().url})`); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => (window.__csp = window.__csp || []).push(e.violatedDirective + " " + e.blockedURI)));
  return { ctx, page, errors };
}
// the desk reports when its scene (or its text mode) is ready
const deskReady = (page) => page.evaluate(() => window.__evident ? Promise.race([window.__evident.ready(), new Promise((r) => setTimeout(r, 15000))]) : null);

for (const v of VIEWS) {
  const { ctx, page, errors } = await newPage({ viewport: { width: v.width, height: v.height }, colorScheme: v.scheme, deviceScaleFactor: 2, serviceWorkers: "block" });
  for (const [label, path, ground] of PAGES) {
    await page.goto(base + path, { waitUntil: "load" });
    if (ground === "desk") await deskReady(page);
    await page.waitForTimeout(700);
    const a = await page.evaluate(AUDIT);
    const where = `${v.name} ${label}`;
    a.contrast.forEach((d) => note(where, "contrast", d));
    a.targets.forEach((d) => note(where, "tap target < 44px", d));
    a.smallText.forEach((d) => note(where, "text under 13px", d));
    a.overflow.forEach((d) => note(where, "overflows viewport", d));
    a.csp.forEach((d) => note(where, "CSP violation", d));
    if (a.scrollW > a.innerW + 1) note(where, "horizontal scroll", `${a.scrollW} > ${a.innerW}`);
    if (a.h1 !== 1) note(where, "headings", `${a.h1} visible <h1> elements`);
    if (!a.nma) note(where, "disclaimer", "Not medical advice is not visible");
    // Main heading (DESIGN.md): on the desk's home 42 to 46px on a phone and up to 104px on a desktop, sized by the
    // screen's height as well; on the other pages 34 to 38px on a phone, 48 to 56px on a desktop. On a result the
    // h1 is the claim, set as a quotation, so no hero size applies.
    const [lo, hi] = ground === "desk" ? (v.width <= 430 ? [42, 46] : [46, 104]) : (v.width <= 430 ? [34, 38] : [48, 56]);
    if (label !== "result" && (a.h1px < lo || a.h1px > hi)) note(where, "main heading size", `${a.h1px}px, wanted ${lo} to ${hi}`);
    if (a.imgsNoAlt) note(where, "images", `${a.imgsNoAlt} without alt`);
    if (!a.title) note(where, "title", "empty <title>");
    // There is no dark stock: a dark system preference changes neither ground.
    const want = ground === "desk" ? a.night : a.paper;
    if (a.bodyBg.replace(/\s/g, "") !== want.replace(/\s/g, "")) note(where, "ground", `${a.bodyBg}, wanted --${ground === "desk" ? "night" : "paper"} ${want}`);
    await page.screenshot({ path: shotName(`${v.name}-${label}`), fullPage: ground !== "desk" });

    // The type-ahead only exists while someone is typing; open it on purpose and audit it too.
    if (label === "home") {
      await page.locator("#claim-input").click();
      await page.locator("#claim-input").fill("turmaric inflamation");
      await page.dispatchEvent("#claim-input", "input");
      await page.waitForTimeout(700);
      if (!(await page.locator(".suggest-row").count())) note(where, "type-ahead", "no suggestions for a known misspelling");
      else {
        const sa = await page.evaluate(AUDIT);
        sa.contrast.forEach((d) => note(`${where} type-ahead`, "contrast", d));
        sa.targets.forEach((d) => note(`${where} type-ahead`, "tap target < 44px", d));
        if (sa.scrollW > sa.innerW + 1) note(`${where} type-ahead`, "horizontal scroll", `${sa.scrollW} > ${sa.innerW}`);
        const aria = await page.getAttribute("#claim-input", "aria-controls");
        if (aria !== "suggest-list") note(`${where} type-ahead`, "aria", `aria-controls is ${aria}`);
        await page.screenshot({ path: shotName(`${v.name}-suggest`) });
      }
      await page.locator("#claim-input").fill("");
      await page.dispatchEvent("#claim-input", "input");
    }
  }
  errors.forEach((e) => note(v.name, "console error", e.slice(0, 120)));
  await ctx.close();
}

// ---- the first screen on short laptop screens and phones ------------------------
// The headline, the claim box and the button are wholly on the first screen with no scrolling, in 3D and in text
// mode (DESIGN.md, "The first screen").
for (const [w, h] of [[1366, 650], [1280, 720], [1024, 768], [1440, 900], [1920, 1080], [390, 844], [375, 667]]) {
  for (const path of ["/", "/?nogl=1"]) {
    const where = `first screen ${w}x${h} ${path === "/" ? "3D" : "text mode"}`;
    const { ctx, page, errors } = await newPage({ viewport: { width: w, height: h }, serviceWorkers: "block" });
    await page.goto(base + path, { waitUntil: "load" }); await deskReady(page); await page.waitForTimeout(600);
    const m = await page.evaluate(() => {
      const strip = document.getElementById("strip").getBoundingClientRect().bottom;
      return { sy: scrollY, items: ["#ask-heading", ".claim-box", "#submit-btn"].map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return [s, Math.round(r.top), Math.round(r.bottom)]; }).map(([s, t, b]) => ({ s, t, b, ok: t >= strip - 1 && b <= innerHeight })) };
    });
    if (m.sy !== 0) note(where, "first screen", `the page opened scrolled to ${m.sy}`);
    m.items.filter((i) => !i.ok).forEach((i) => note(where, "first screen", `${i.s} runs from ${i.t} to ${i.b}, the screen is ${h} high`));
    errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
    await ctx.close();
  }
}
console.log("first screen: headline, claim box and button checked at seven sizes in 3D and text mode");

// ---- the desk's library blocked ---------------------------------------------------
// With three.js and the wood refused, as a slow or filtering network might, the page is the text mode: the claim
// box works, the first screen holds it, and nothing says it is loading forever.
for (const view of [
  { name: "desktop", opts: { viewport: { width: 1366, height: 650 }, deviceScaleFactor: 1 } },
  { name: "touch phone", opts: { viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
]) {
  const where = `desk library blocked, ${view.name}`;
  const { ctx, page, errors } = await newPage({ ...view.opts, serviceWorkers: "block" });
  await ctx.route(/\/static\/(vendor|desk)\//, (r) => r.abort());
  await page.goto(`${base}/`, { waitUntil: "load" });
  await deskReady(page); await page.waitForTimeout(800);
  const v = await page.evaluate(() => {
    const r = document.getElementById("claim-input").getBoundingClientRect();
    const s = document.getElementById("submit-btn").getBoundingClientRect();
    return { mode: window.__evident ? window.__evident.stats().mode : "", onFirstScreen: r.top >= 0 && s.bottom <= innerHeight, scrollW: document.documentElement.scrollWidth, innerW: innerWidth };
  });
  if (v.mode !== "text") note(where, "text mode", `the page is in mode ${v.mode}`);
  if (!v.onFirstScreen) note(where, "claim box", "field and button are not both on the first screen");
  if (v.scrollW > v.innerW + 1) note(where, "horizontal scroll", `${v.scrollW} > ${v.innerW}`);
  if (view.opts.hasTouch) await page.tap("#claim-input"); else await page.click("#claim-input");
  await page.keyboard.type("Vitamin C prevents colds");
  if ((await page.inputValue("#claim-input")) !== "Vitamin C prevents colds") note(where, "claim box", "typing did not reach the box");
  await page.screenshot({ path: shotName(`library-blocked-${view.name}`) });
  errors.filter((e) => !/Failed to load resource|ERR_FAILED/.test(e)).forEach((e) => note(where, "console error", e.slice(0, 120)));
  await ctx.close();
}
console.log("desk library blocked: text mode, claim box and console checked on a short laptop and a touch phone");

await browser.close();

tokenDrift().forEach((d) => note("tokens", "design system drift", d));

if (!fails.length) {
  console.log(`QA passed: contrast, tap targets, text size, overflow, console, CSP, headings, grounds, first screen, tokens. Screenshots in ${OUT}/qa-*.png`);
  process.exit(0);
}
console.log(`QA found ${fails.length} problem(s):\n`);
for (const f of fails) console.log(`  [${f.view}] ${f.rule}: ${f.detail}`);
process.exit(1);
