#!/usr/bin/env node
/**
 * Measures the home screen before a UI rebuild and leaves screenshots of each
 * state, so "after" can be compared with "before" by number, not by eye.
 *
 *   node scripts/ui_measure.mjs [baseUrl] [outDir]
 *
 * Needs the fixture server (no key, no network, replayed stages):
 *   PORT=5050 .venv/bin/python scripts/ui_fixture_server.py [cache-copy.db]
 *
 * Per width and motion setting it records the claim input's top (page
 * coordinates), whether the input and the submit button are fully inside the
 * first viewport with no scrolling, the page height, and horizontal overflow.
 * Screenshots are PNG; any over ~400 KB is saved as JPEG quality 70 instead.
 */
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const out = process.argv[3] && !process.argv[3].startsWith("http") ? process.argv[3] : "research/ui_before";
mkdirSync(out, { recursive: true });

const WIDTHS = [
  { w: 1440, h: 900 },
  { w: 768, h: 1024 },
  { w: 390, h: 844 },
  { w: 375, h: 812 },
];
const GATED = "Gated claim for stage test";
const DOWN = "Provider down claim";
const SHOT_WIDTHS = new Set([390, 1440]); // the flow screenshots (disk is tight)
const MAX_PNG = 400 * 1024;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const files = [];

async function shot(page, name, opts = {}) {
  let buf = await page.screenshot({ ...opts, type: "png" });
  let ext = "png";
  if (buf.length > MAX_PNG) { buf = await page.screenshot({ ...opts, type: "jpeg", quality: 70 }); ext = "jpg"; }
  const file = `${name}.${ext}`;
  writeFileSync(`${out}/${file}`, buf);
  files.push(`${file} (${Math.round(buf.length / 1024)} KB)`);
}

async function settle(page) {
  await page.waitForLoadState("load");
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await sleep(2500); // the footage poster, fonts and the first frame
}

async function measureHome(browser, v, reduced) {
  const ctx = await browser.newContext({
    viewport: { width: v.w, height: v.h },
    reducedMotion: reduced ? "reduce" : "no-preference",
  });
  const page = await ctx.newPage();
  await page.goto(base + "/");
  await settle(page);
  const m = await page.evaluate(() => {
    const vh = innerHeight;
    const box = (id) => {
      const el = document.getElementById(id);
      const r = el.getBoundingClientRect();
      return { top: r.top + scrollY, bottom: r.bottom + scrollY, left: r.left, right: r.right, height: r.height };
    };
    const i = box("claim-input"), s = box("submit-btn");
    const inside = (b) => b.top >= 0 && b.bottom <= vh && b.left >= 0 && b.right <= innerWidth;
    const de = document.documentElement;
    return {
      scrollY: scrollY,
      inputTop: Math.round(i.top),
      inputBottom: Math.round(i.bottom),
      submitTop: Math.round(s.top),
      submitBottom: Math.round(s.bottom),
      inputInFirstViewport: inside(i),
      submitInFirstViewport: inside(s),
      bothInFirstViewport: inside(i) && inside(s),
      pageHeight: Math.max(de.scrollHeight, document.body.scrollHeight),
      scrollWidth: de.scrollWidth,
      clientWidth: de.clientWidth,
      horizontalOverflow: de.scrollWidth > de.clientWidth + 1,
    };
  });
  const rec = { width: v.w, height: v.h, motion: reduced ? "reduce" : "on", ...m };
  const tag = `${v.w}${reduced ? "-reduced" : ""}`;
  if (!reduced || v.w === 390) await shot(page, `home-first-${tag}`);
  if (!reduced && SHOT_WIDTHS.has(v.w)) await shot(page, `home-full-${tag}`, { fullPage: true });
  await ctx.close();
  return rec;
}

async function flows(browser, v) {
  const ctx = await browser.newContext({ viewport: { width: v.w, height: v.h } });
  const page = await ctx.newPage();
  const tag = String(v.w);
  await fetch(base + "/__fixture/reset");

  // Error: submit with nothing typed.
  await page.goto(base + "/");
  await settle(page);
  await page.click("#submit-btn");
  await sleep(400);
  await shot(page, `error-empty-submit-${tag}`);

  // Loading: a claim whose stages wait for the test, held after "query".
  await page.fill("#claim-input", GATED);
  await page.click("#submit-btn");
  await fetch(base + "/__fixture/release/query");
  await page.waitForFunction(() => /Searching PubMed/.test(document.getElementById("reading")?.textContent || ""), null, { timeout: 15000 });
  await sleep(1200);
  await shot(page, `loading-${tag}`);

  // Result.
  await fetch(base + "/__fixture/release/found");
  await fetch(base + "/__fixture/release/done");
  await page.waitForSelector("#stamp", { state: "visible", timeout: 20000 });
  await page.waitForFunction(() => document.querySelector("#reading")?.hidden, null, { timeout: 20000 });
  await sleep(2500); // the nudge scroll and the reveal
  await page.evaluate(() => scrollTo(0, 0));
  await sleep(600);
  await shot(page, `result-${tag}`);
  await shot(page, `result-full-${tag}`, { fullPage: true });

  // Source dialog.
  const study = page.locator("#sources .study").first();
  await study.scrollIntoViewIfNeeded();
  await study.click();
  await page.waitForSelector("dialog#study-sheet[open]", { timeout: 5000 });
  await sleep(1200);
  await shot(page, `source-dialog-${tag}`);
  await page.keyboard.press("Escape");

  // Error: a failed network request.
  await page.goto(base + "/");
  await settle(page);
  await page.route("**/api/check**", (r) => r.abort("failed"));
  await page.fill("#claim-input", "Cold showers cure the flu");
  await page.click("#submit-btn");
  await page.waitForSelector("#error:not([hidden])", { timeout: 15000 });
  await sleep(800);
  await shot(page, `error-network-${tag}`);
  await page.unroute("**/api/check**");

  // Error: the provider is down (a server error screen).
  await page.goto(base + "/");
  await settle(page);
  await page.fill("#claim-input", DOWN);
  await page.click("#submit-btn");
  await page.waitForSelector("#error:not([hidden])", { timeout: 15000 });
  await sleep(800);
  await shot(page, `error-provider-${tag}`);
  await ctx.close();
}

const browser = await chromium.launch();
const rows = [];
for (const v of WIDTHS) for (const reduced of [false, true]) rows.push(await measureHome(browser, v, reduced));
for (const v of WIDTHS.filter((v) => SHOT_WIDTHS.has(v.w))) await flows(browser, v);
await browser.close();

writeFileSync(`${out}/measure.json`, JSON.stringify({ base, measuredAt: new Date().toISOString(), rows, screenshots: files }, null, 2));

const yn = (b) => (b ? "yes" : "no");
const table = [
  "| Width x height | Motion | Input top (px) | Submit bottom (px) | Input + submit on first screen | Page height (px) | Horizontal overflow |",
  "|---|---|---|---|---|---|---|",
  ...rows.map((r) => `| ${r.width}x${r.height} | ${r.motion} | ${r.inputTop} | ${r.submitBottom} | ${yn(r.bothInFirstViewport)} (input ${yn(r.inputInFirstViewport)}, submit ${yn(r.submitInFirstViewport)}) | ${r.pageHeight} | ${r.horizontalOverflow ? `yes (${r.scrollWidth} > ${r.clientWidth})` : "no"} |`),
].join("\n");
console.log(table);
writeFileSync(`${out}/table.md`, table + "\n");
console.log("\n" + files.join("\n"));
