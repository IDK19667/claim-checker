#!/usr/bin/env node
/**
 * Makes the still picture of the desk that text mode shows (no WebGL,
 * Save-Data, 2G, the 3D library refused, scripts off): our own scene, drawn
 * by this tree's 3D desk in headless Chromium at the first-screen camera,
 * the page's words hidden, then encoded to WebP with Pillow
 * (scripts/make_still.py). Run it again whenever the scene changes.
 *
 *   node scripts/make_still.mjs [baseUrl]     (a fixture server must be running there)
 *
 * Two crops of the same scene, chosen by the page with <picture media>:
 *   static/desk/still-phone.webp  the phone's band (390 wide, the band at its
 *                                 tallest), at 1.5x, at most 2,048 bytes: text
 *                                 mode's first load (about 197 KB as sent) must
 *                                 stay under 200 KB
 *   static/desk/still-wide.webp   the lamp and its lit wood from the desktop
 *                                 view (1280x800), at 2x, at most 20,480 bytes,
 *                                 with room above and below so desk.css can fade
 *                                 it to nothing on every side (no straight edge)
 * Nothing leaves 127.0.0.1.
 */
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://127.0.0.1:5652";
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const py = process.env.PY || path.join(root, ".venv/bin/python");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "still-"));
const b = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });

async function shoot(w, h, dpr, clipFn, file) {
  const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: dpr, serviceWorkers: "block" });
  const page = await ctx.newPage();
  await page.goto(base + "/", { waitUntil: "load" });
  await page.evaluate(() => Promise.race([window.__evident.ready(), new Promise((r) => setTimeout(r, 30000))]));
  if ((await page.evaluate(() => window.__evident.stats().mode)) !== "3d") throw new Error("the desk did not draw in 3D");
  const clip = await page.evaluate(clipFn);
  await page.addStyleTag({ content: "body > *:not(#stage) { visibility: hidden !important; }" });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: file, clip, type: "png" });
  await ctx.close();
  return clip;
}
// the phone: the band the 3D page keeps above the headline, at its tallest (390x844)
const phone = await shoot(390, 844, 1.5, () => {
  const s1 = document.getElementById("s1"), top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--strip"));
  const h = Math.round(parseFloat(getComputedStyle(s1).paddingTop) - top);
  return { x: 0, y: top, width: innerWidth, height: h };
}, path.join(tmp, "phone.png"));
// the wide screen: the lamp and the wood it lights, from the desktop's first view
const wide = await shoot(1280, 800, 2, () => ({ x: 90, y: 0, width: 520, height: 660 }), path.join(tmp, "wide.png"));
await b.close();
execFileSync(py, [path.join(root, "scripts/make_still.py"),
  path.join(tmp, "phone.png"), path.join(root, "static/desk/still-phone.webp"), "2048",
  path.join(tmp, "wide.png"), path.join(root, "static/desk/still-wide.webp"), "20480", "room"], { stdio: "inherit" });
fs.rmSync(tmp, { recursive: true, force: true });
console.log("phone band", JSON.stringify(phone), "wide crop", JSON.stringify(wide));
