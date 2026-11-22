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
  // Painted through a canvas rather than read as numbers: a computed colour can
  // come back as oklab() (from color-mix) as well as rgb(), and its numbers are
  // not 0 to 255.
  const cv = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const parse = (c) => { if (!c) return null; cv.clearRect(0, 0, 1, 1); cv.fillStyle = "#000"; cv.fillStyle = c; cv.fillRect(0, 0, 1, 1); const [r, g, b, a] = cv.getImageData(0, 0, 1, 1).data; return { rgb: [r, g, b], a: a / 255 }; };
  const bgOf = (el) => { let e = el; while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.9) return c.rgb; e = e.parentElement; } return parse(getComputedStyle(document.body).backgroundColor).rgb; };
  const ratio = (f, b) => { const l1 = lum(f), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };

  const contrast = [], targets = [], overflow = [], smallText = [];
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
    // Every control a thumb has to hit: 44px or more, at every width.
    // Measured by hit-testing outward from the centre, not from the box: a
    // small mark with a grown hit area (an inline reference, say) is
    // genuinely tappable and should pass. A plain link that sits inside a
    // sentence of running text is exempt (WCAG 2.5.8 inline exception); a
    // link or button standing on its own line or in its own row is not.
    if (/^(BUTTON|A|SUMMARY)$/.test(el.tagName)) {
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const r = el.getBoundingClientRect();
      const owns = (x, y) => { const hit = document.elementFromPoint(x, y); return hit && (hit === el || el.contains(hit)); };
      const around = (el.parentElement ? el.parentElement.textContent : "").replace(el.textContent, "").trim().length;
      // Shelf columns: eight books cannot each be 44px wide on a phone, so a column
      // shrinks to fit (never under 38px) and takes the full height of the shelf.
      const shelf = el.classList.contains("barcol") && r.width >= 38 && r.height >= 44;
      const inProse = shelf || cs.display === "inline" && around >= 40 && !el.classList.contains("ref") && !el.closest(".foot, .src, .query, .fine");
      if (r.width > 0 && r.height > 0 && !inProse && !el.closest(".sr-only")) {
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const reach = (dx, dy) => { let n = 0; for (; n <= 30; n += 1) { const x = cx + dx * (n + 0.5 + (dx ? r.width / 2 : 0)), y = cy + dy * (n + 0.5 + (dy ? r.height / 2 : 0)); if (x < 0 || y < 0 || x > innerWidth || y > innerHeight || !owns(x, y)) break; } return n; };
        const h = reach(0, -1) + reach(0, 1) + r.height, w = reach(-1, 0) + reach(1, 0) + r.width;
        if (owns(cx, cy) && (h < 43.5 || w < 43.5)) {
          targets.push(`${el.tagName}.${String(el.className).slice(0, 24)} "${el.textContent.trim().slice(0, 16)}" effective ${Math.round(w)}x${Math.round(h)}`);
        }
      }
    }
    // Nothing a reader needs is under 13px. The spine lettering is aria-hidden decoration.
    if (own && parseFloat(cs.fontSize) < 13 && !el.closest(".barcol-spine, .sr-only")) smallText.push(`${el.tagName}.${String(el.className).slice(0, 24)} ${cs.fontSize}`);
    const r = el.getBoundingClientRect();
    if (r.right > innerWidth + 1 && cs.overflowX !== "auto" && cs.position !== "fixed" && !el.closest(".still-row")) {
      overflow.push(`${el.tagName}.${String(el.className).slice(0, 24)} extends to ${Math.round(r.right)}px`);
    }
  }
  return {
    contrast, smallText: [...new Set(smallText)], targets: [...new Set(targets)], overflow: [...new Set(overflow)].slice(0, 5),
    scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
    title: document.title,
    // The page keeps one h1 per view (ask, result, error) and shows one; only the visible ones count.
    h1: [...document.querySelectorAll("h1")].filter((h) => h.getClientRects().length).length,
    h1px: parseFloat(getComputedStyle([...document.querySelectorAll("h1")].find((h) => h.getClientRects().length) || document.body).fontSize),
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

  for (const [label, url] of [["home", `${base}/${q()}`], ["result", `${base}/${q("q=" + encodeURIComponent(claim))}`],
                               ["about", `${base}/about`], ["method", `${base}/method`], ["corrections", `${base}/corrections`], ["privacy", `${base}/privacy`]]) {
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    const a = await page.evaluate(AUDIT);
    const where = `${v.name} ${label}`;
    a.contrast.forEach((d) => note(where, "contrast", d));
    a.targets.forEach((d) => note(where, "tap target < 44px", d));
    a.smallText.forEach((d) => note(where, "text under 13px", d));
    a.overflow.forEach((d) => note(where, "overflows viewport", d));
    if (a.scrollW > a.innerW + 1) note(where, "horizontal scroll", `${a.scrollW} > ${a.innerW}`);
    if (a.h1 !== 1) note(where, "headings", `${a.h1} visible <h1> elements`);
    // Main heading: 34 to 38px on a phone, 48 to 56px at desktop widths.
    const [lo, hi] = v.width <= 430 ? [34, 38] : [48, 56];
    // On a result the h1 is the claim itself, set as a quotation well below the verdict (DESIGN.md), so the hero size does not apply.
    if (!/ result$/.test(where) && (a.h1px < lo || a.h1px > hi)) note(where, "main heading size", `${a.h1px}px, wanted ${lo} to ${hi}`);
    if (a.imgsNoAlt) note(where, "images", `${a.imgsNoAlt} without alt`);
    if (!a.title) note(where, "title", "empty <title>");
    // There is no dark stock: a dark system preference must not darken the
    // page. Compared against the declared --paper token, not a literal, so a
    // palette change cannot leave this check quietly testing the wrong colour.
    if (a.bodyBg.replace(/\s/g, "") !== a.paperToken.replace(/\s/g, "")) {
      note(where, "background is not paper", `${a.bodyBg} vs --paper ${a.paperToken}`);
    }
    await page.screenshot({ path: `/tmp/qa-${v.name.replace(/\s/g, "-")}-${label}.png`, fullPage: label !== "home" });

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
        sa.targets.forEach((d) => note(`${where} type-ahead`, "tap target < 44px", d));
        if (sa.scrollW > sa.innerW + 1) note(`${where} type-ahead`, "horizontal scroll", `${sa.scrollW} > ${sa.innerW}`);
        const aria = await page.getAttribute("#claim-input", "aria-controls");
        if (aria !== "suggest-list") note(`${where} type-ahead`, "aria", `aria-controls is ${aria}`);
        await page.screenshot({ path: `/tmp/qa-${v.name.replace(/\s/g, "-")}-suggest.png` });
      }
      await page.locator("#claim-input").fill("");
      await page.dispatchEvent("#claim-input", "input");
    }
  }
  errors.forEach((e) => note(v.name, "console error", e.slice(0, 120)));
  await ctx.close();
}
// ---- the checker on the first screen -------------------------------------------
// The intro footage is gone from the home page; the checker is what loads. With
// every footage URL refused, as a slow or filtering network might, the page is
// still whole: the claim box works and nothing says it is loading forever.
for (const view of [
  { name: "desktop", opts: { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 } },
  { name: "touch phone", opts: { viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true } },
]) {
  const where = `footage blocked, ${view.name}`;
  const ctx = await browser.newContext(view.opts);
  await ctx.route(/\/(footage|static\/footage)\//, (r) => r.abort());
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${base}/`, { waitUntil: "load" });
  await page.waitForTimeout(1000);
  const v = await page.evaluate(() => {
    const r = document.getElementById("claim-input").getBoundingClientRect();
    const s = document.getElementById("submit-btn").getBoundingClientRect();
    return {
      onFirstScreen: r.top >= 0 && s.bottom <= innerHeight,
      trust: !!document.getElementById("trust") && document.getElementById("trust").getClientRects().length > 0,
      scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
    };
  });
  if (!v.onFirstScreen) note(where, "claim box", "field and submit are not both on the first screen");
  if (!v.trust) note(where, "trust section", "not visible");
  if (v.scrollW > v.innerW + 1) note(where, "horizontal scroll", `${v.scrollW} > ${v.innerW}`);
  if (view.opts.hasTouch) await page.tap("#claim-input"); else await page.click("#claim-input");
  await page.keyboard.type("Vitamin C prevents colds");
  if ((await page.inputValue("#claim-input")) !== "Vitamin C prevents colds") note(where, "claim box", "typing did not reach the box");
  await page.screenshot({ path: `/tmp/qa-footage-blocked-${view.name.replace(/\s/g, "-")}.png` });
  errors.forEach((e) => note(where, "console error", e.slice(0, 120)));
  await ctx.close();
}
console.log("footage blocked: claim box, trust section and console checked on desktop and a touch phone");

await browser.close();

tokenDrift().forEach((d) => note("tokens", "design system drift", d));

if (!fails.length) {
  console.log("QA passed: contrast, tap targets, overflow, console, tokens. Screenshots in /tmp/qa-*.png");
  process.exit(0);
}
console.log(`QA found ${fails.length} problem(s):\n`);
for (const f of fails) console.log(`  [${f.view}] ${f.rule}: ${f.detail}`);
process.exit(1);
