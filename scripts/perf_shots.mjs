#!/usr/bin/env node
/**
 * Screenshots of every settled screen (home, and each stop of a saved answer),
 * at 390x844 and 1440x900, in 3D, to compare a build before and after a
 * performance change. Against the fixture server.
 *
 *   node scripts/perf_shots.mjs <outDir> [baseUrl]
 */
import { chromium } from "playwright";
import fs from "node:fs";

const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:5674";
const ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
const SAVED = "Creatine improves cognitive performance";
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ args: ARGS });
const still = (page) => page.waitForFunction(() => !window.__evident.stats().looping, null, { timeout: 20000, polling: 100 }).catch(() => {});
for (const [name, vp] of [["phone", { width: 390, height: 844 }], ["desktop", { width: 1440, height: 900 }]]) {
  const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: name === "phone" ? 3 : 1, isMobile: name === "phone", hasTouch: name === "phone", serviceWorkers: "block" });
  const page = await ctx.newPage();
  await page.goto(base + "/?shots", { waitUntil: "load" });
  await page.evaluate(() => window.__evident.ready());
  await page.waitForTimeout(1500); await still(page);
  await page.screenshot({ path: `${out}/${name}-home.png` });
  await page.goto(`${base}/?q=${encodeURIComponent(SAVED)}&shots`, { waitUntil: "load" });
  await page.evaluate(() => window.__evident.ready());
  const n = await page.evaluate(() => window.__evident.stats().stops);
  for (let k = 1; k <= n; k++) {
    await page.evaluate((k) => window.__evident.goto(k), k);
    await page.waitForTimeout(1200); await still(page);
    await page.screenshot({ path: `${out}/${name}-stop${k}.png` });
  }
  await ctx.close();
}
await browser.close();
console.log("shots in " + out);
