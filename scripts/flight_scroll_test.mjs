// Fast-scroll frame-drop test for /flight.
//
// Scrolls the whole pinned section top to bottom in ~1 second (60 steps via
// requestAnimationFrame, matching how a real fast swipe actually drives the
// page), on desktop and on a throttled "mid-range phone" CDP profile (4x CPU
// slowdown, a Fast 3G-ish network), then reads window.__flightStats
// (a counter flight.js maintains: every scroll tick where the wanted frame
// was not yet decoded is a "miss" — the canvas held the previous frame
// instead, which is the stale-frame problem the fix targets).
//
//   node scripts/flight_scroll_test.mjs before   # label only, for the printout
//   node scripts/flight_scroll_test.mjs after
//
// The server must be running the code being tested.

import { chromium } from "playwright";

const BASE = "http://localhost:5000";
const label = process.argv[2] || "run";

async function runOne(name, width, height, throttle) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
  const client = await page.context().newCDPSession(page);

  if (throttle) {
    await client.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await client.send("Network.emulateNetworkConditions", {
      offline: false, latency: 150,
      downloadThroughput: (1.6 * 1024 * 1024) / 8,
      uploadThroughput: (750 * 1024) / 8,
    });
  }

  // Not "networkidle": the whole high-res sequence is deliberately still
  // downloading in the background on a slow connection (that is the point
  // of the two-tier preload), so the network never truly goes idle within
  // a reasonable wait. A real visitor does not wait for that either.
  await page.goto(`${BASE}/flight`, { waitUntil: "load", timeout: 60000 });
  await page.waitForTimeout(600);

  const travel = await page.evaluate(() => {
    const s = document.getElementById("flight");
    return parseFloat(getComputedStyle(s).height) - window.innerHeight;
  });

  await page.evaluate(() => { window.__flightStats = { draws: 0, blanks: 0, seen: {} }; });

  // A real fast swipe: ~60 steps targeting ~1000ms. Each step is a real
  // round trip (evaluate + the page's own scroll handler), and that round
  // trip itself takes real time — under CPU throttling especially, it can
  // take longer than the per-step budget, which would silently stretch the
  // whole sweep and hand the fix extra wall-clock time it did not earn.
  // Tracked explicitly instead of assumed: `elapsed` is what actually
  // happened, reported alongside the miss count rather than in place of it.
  const STEPS = 60, DURATION_MS = 1000;
  const t0 = Date.now();
  for (let i = 1; i <= STEPS; i++) {
    await page.evaluate((y) => window.scrollTo(0, y), Math.round(travel * (i / STEPS)));
    const target = t0 + (DURATION_MS * i) / STEPS;
    const wait = target - Date.now();
    if (wait > 0) await page.waitForTimeout(wait);
  }
  const elapsed = Date.now() - t0;
  const overrun = elapsed - DURATION_MS;
  await page.waitForTimeout(300); // let any in-flight fetch resolve before reading stats

  const stats = await page.evaluate(() => window.__flightStats);
  const distinct = Object.keys(stats.seen || {}).length;

  console.log(
    `[${label}] ${name}${throttle ? " (throttled 4x CPU, slow network)" : ""}: ` +
    `swept in ${elapsed}ms (target ${DURATION_MS}ms, ${overrun >= 0 ? "+" : ""}${overrun}ms overrun), ` +
    `${stats.draws} draws, ${distinct} distinct frames shown, ` +
    `${stats.blanks} misses (scroll ticks where the wanted frame wasn't ready yet)`
  );

  await page.close();
  await browser.close();
  return { name, throttle, elapsed, overrun, draws: stats.draws, distinct, blanks: stats.blanks };
}

// This browser launch renders canvas via whatever GL backend is available
// in this environment. A software (non-GPU) backend composites a large
// canvas noticeably slower than real hardware does, which would read as a
// scroll-performance problem that has nothing to do with the frame-loading
// fix under test. Printed once so the numbers below carry that context.
{
  const b = await chromium.launch();
  const p = await b.newPage();
  const renderer = await p.evaluate(() => {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl");
    if (!gl) return "no webgl";
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    return dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  });
  console.log(`[${label}] canvas renderer in this environment: ${renderer}`);
  await b.close();
}

const results = [];
results.push(await runOne("desktop", 1280, 800, false));
results.push(await runOne("phone (throttled)", 390, 844, true));

console.log(`\n[${label}] summary: ` + results.map(r =>
  `${r.name}=${r.blanks} misses`).join(", "));
