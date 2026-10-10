#!/usr/bin/env node
/**
 * Fast-scroll stress test for the fly-through.
 *
 *   node scripts/flight_stress.mjs [baseUrl] [--out DIR] [--headed]
 *
 * Flings the whole page top to bottom and back, five times, at three
 * durations, at a desktop and a phone viewport, then repeats the set with the
 * CPU throttled 4x through CDP. Reports, per run, exactly the four numbers the
 * fast-scroll requirement is written in:
 *
 *   maxHoldMs         longest a single frame stayed on screen while the scroll
 *                     position was moving (must be under 100ms)
 *   longTasks         main-thread tasks over 50ms (must be 0)
 *   loresAfterWindow  low-res draws after the first second (must be 0)
 *   offTarget         draws further than 2 frames from the scroll position
 *
 * Launches headed by default: on macOS that is the real GPU, and these numbers
 * are meaningless under Playwright's software rasteriser.
 */
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5000";
const outArg = process.argv.indexOf("--out");
const OUT = outArg > -1 ? process.argv[outArg + 1] : "/tmp/flight-stress";
const headless = process.argv.includes("--headless");

mkdirSync(OUT, { recursive: true });

const VIEWS = [
  { name: "desktop", width: 1440, height: 900, dpr: 2 },
  { name: "phone", width: 390, height: 844, dpr: 2, mobile: true },
];
const DURATIONS = [300, 500, 1000];
const ROUND_TRIPS = 5;

/** Drives window.scrollTo from 0 to the bottom and back, `trips` times, each
 *  leg taking `ms`. Runs inside one rAF chain so each leg is a real fling the
 *  page's own loop sees through scroll events, not a jump.
 *
 *  Guarded by a wall-clock timer: a headed window that loses focus has its rAF
 *  throttled to a crawl, and without the guard the chain simply never resolves
 *  and the whole run hangs (which is exactly what the first attempt did). The
 *  guard resolves with a flag so a starved run is reported, never silently
 *  counted as a pass. */
const FLING = ([ms, trips]) => new Promise((resolve) => {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const legs = trips * 2;
  let leg = 0;
  let t0 = null;
  let done = false;
  const finish = (starved) => { if (!done) { done = true; resolve({ starved, legs: leg }); } };
  const guard = setTimeout(() => finish(true), ms * legs + 3000);
  function step(now) {
    if (done) return;
    if (t0 === null) t0 = now;
    const p = Math.min(1, (now - t0) / ms);
    const down = leg % 2 === 0;
    window.scrollTo(0, Math.round((down ? p : 1 - p) * max));
    if (p >= 1) {
      leg++;
      t0 = null;
      if (leg >= legs) { clearTimeout(guard); finish(false); return; }
    }
    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
});

function fmt(rows) {
  const cols = Object.keys(rows[0]);
  const w = cols.map((c) => Math.max(c.length, ...rows.map((r) => String(r[c]).length)));
  const line = (cells) => "| " + cells.map((v, i) => String(v).padEnd(w[i])).join(" | ") + " |";
  return [line(cols), "|" + w.map((n) => "-".repeat(n + 2)).join("|") + "|",
    ...rows.map((r) => line(cols.map((c) => r[c])))].join("\n");
}

const browser = await chromium.launch({ headless });
const rows = [];

for (const throttle of [1, 4]) {
  for (const view of VIEWS) {
    const context = await browser.newContext({
      viewport: { width: view.width, height: view.height },
      deviceScaleFactor: view.dpr,
      isMobile: !!view.mobile,
      hasTouch: !!view.mobile,
    });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(`${base}/flight?debug=1`, { waitUntil: "load" });

    // Wait for the state the requirement is about: the page has gone idle and
    // the motion tier has pre-decoded as far as it is going to. Reported, not
    // assumed, so a run that never got there is visible in the table.
    await page.waitForFunction(() => window.__flightStats && window.__flightStats.frameCount > 0,
      null, { timeout: 20000 });
    // A touch phone holds its footage until the reader first scrolls (the
    // phone budget in flight.js), so move the page once, the way a reader would.
    await page.evaluate(() => window.scrollTo(0, 1));
    await page.waitForTimeout(100);
    await page.evaluate(() => window.scrollTo(0, 0));
    // Wait for the state the requirement is about: the motion tier pre-decoded
    // as far as it is going to go. "Unchanged between two reads" is not enough
    // on its own, because pre-decode only starts once the page goes idle and
    // residency sits at 1 until then. Hold out for the full count, and settle
    // for a genuine plateau (unchanged across four reads, two seconds) only
    // when the device cannot hold the whole sequence. Reported either way, so a
    // run that never warmed up is visible in the table rather than mistaken for
    // a fast-scroll failure.
    const wantResident = await page.evaluate(() =>
      Math.min(window.__flightStats.frameCount || 0, window.__flightStats.motionCap || Infinity));
    let resident = 0, flat = 0;
    const settleStart = Date.now();
    while (Date.now() - settleStart < 90000) {
      await page.waitForTimeout(500);
      const next = await page.evaluate(() => window.__flightStats.residentMotion);
      if (wantResident && next >= wantResident) { resident = next; break; }
      flat = next === resident ? flat + 1 : 0;
      resident = next;
      if (flat >= 4 && resident > 0 && Date.now() - settleStart > 8000) break;
    }
    const settleMs = Date.now() - settleStart;
    console.log(`  ${throttle}x ${view.name}: settled in ${(settleMs / 1000).toFixed(1)}s, resident ${resident}`);

    if (throttle > 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: throttle });

    for (const ms of DURATIONS) {
      await page.evaluate(() => { window.scrollTo(0, 0); window.__flightStats.reset(); });
      await page.waitForTimeout(400);
      const fling = await page.evaluate(FLING, [ms, ROUND_TRIPS])
        .catch((e) => ({ starved: true, legs: -1, error: String(e).slice(0, 80) }));
      await page.waitForTimeout(120);
      const s = await page.evaluate(() => {
        const s = window.__flightStats;
        return {
          maxHoldMs: Math.round(s.maxHoldMs), draws: s.draws, distinct: s.distinct,
          longTasks: s.longTasks, longestTaskMs: Math.round(s.longestTaskMs),
          loresAfterWindow: s.loresAfterWindow, blendAtSpeed: s.blendDrawsAtSpeed,
          offTarget: s.offTarget, maxOffTarget: s.maxOffTarget,
          motion: s.motionDraws, hires: s.hiresDraws,
          peak: Math.round(s.peakVelocity), resident: s.residentMotion,
          decodes: s.decodes, fails: s.decodeFails, workers: s.workers,
        };
      });
      rows.push({
        cpu: throttle === 1 ? "1x" : "4x", view: view.name, fling: ms + "ms",
        maxHold: s.maxHoldMs + "ms", longTasks: s.longTasks, loresAfter1s: s.loresAfterWindow,
        offTarget: s.offTarget, worstOff: s.maxOffTarget, distinct: s.distinct,
        draws: s.draws, blendAtSpeed: s.blendAtSpeed, motion: s.motion, hires: s.hires,
        peakFps: s.peak, resident: s.resident, decodes: s.decodes,
        starved: fling.starved ? "YES" : "no",
      });
      console.log(`${throttle}x ${view.name} ${ms}ms -> hold ${s.maxHoldMs}ms, longTasks ${s.longTasks}, lores ${s.loresAfterWindow}, offTarget ${s.offTarget}/${s.maxOffTarget}, distinct ${s.distinct}, peak ${s.peak}f/s, resident ${s.resident}, workers ${s.workers}${fling.starved ? `  [STARVED after ${fling.legs} legs]` : ""}`);

      // Mid-fling capture: start a single fling, let it reach the middle, hold
      // there and shoot. The canvas holds whatever the last refresh drew, so
      // this is the real frame, not a settled one.
      if (ms === 300) {
        await page.evaluate(() => { window.scrollTo(0, 0); });
        await page.waitForTimeout(200);
        await page.evaluate(() => new Promise((res) => {
          const max = document.documentElement.scrollHeight - window.innerHeight;
          let t0 = null, done = false;
          const fin = () => { if (!done) { done = true; res(); } };
          setTimeout(fin, 3000);
          function step(now) {
            if (done) return;
            if (t0 === null) t0 = now;
            const p = (now - t0) / 300;
            window.scrollTo(0, Math.round(Math.min(0.5, p) * max));
            if (p >= 0.5) { fin(); return; }
            requestAnimationFrame(step);
          }
          requestAnimationFrame(step);
        }));
        const tag = `${throttle}x-${view.name}`;
        await page.screenshot({ path: join(OUT, `midfling-${tag}.png`) });
        const png = await page.evaluate(() =>
          document.getElementById("flight-canvas").toDataURL("image/png").split(",")[1]);
        writeFileSync(join(OUT, `midfling-canvas-${tag}.png`), Buffer.from(png, "base64"));
      }
    }
    if (errors.length) console.log(`  page errors (${view.name}): ${errors.slice(0, 3).join(" | ")}`);
    console.log(`  (settled in ${(settleMs / 1000).toFixed(1)}s, resident ${resident})`);
    await context.close();
  }
}

await browser.close();
console.log("\n" + fmt(rows));
writeFileSync(join(OUT, "stress.json"), JSON.stringify(rows, null, 1));
console.log(`\nartefacts in ${OUT}`);
