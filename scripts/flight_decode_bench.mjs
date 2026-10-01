#!/usr/bin/env node
/**
 * Times createImageBitmap on the bench files written by
 * scripts/flight_decode_bench.py, in a real browser with a real GPU.
 *
 *   .venv/bin/python scripts/flight_decode_bench.py
 *   node scripts/flight_decode_bench.mjs [baseUrl]
 *
 * Headed on purpose: Playwright's headless default rasterises through
 * SwiftShader, and decode numbers taken there do not describe any reader's
 * machine. Each file is decoded REPS times from an already-fetched Blob, so the
 * number is decode cost alone, with no network or disk in it. The bitmap is
 * closed immediately, so the run does not turn into a memory test.
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5000";
const REPS = 12;

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 2 })).newPage();
await page.goto(`${base}/flight`, { waitUntil: "domcontentloaded" });

const rows = await page.evaluate(async (reps) => {
  const index = await (await fetch("/static/flight/_bench/index.json")).json();
  const out = [];
  for (const f of index) {
    const blob = await (await fetch(f.url)).blob();
    // One warm-up decode: the first use of a codec in the process pays for
    // setting it up, and that cost is paid once per session, not per frame.
    (await createImageBitmap(blob)).close();
    const times = [];
    for (let i = 0; i < reps; i++) {
      const t = performance.now();
      const bmp = await createImageBitmap(blob);
      times.push(performance.now() - t);
      bmp.close();
    }
    times.sort((a, b) => a - b);
    out.push({
      tier: f.tier, format: f.format, frame: f.frame, w: f.w, h: f.h, bytes: f.bytes,
      avg: times.reduce((a, b) => a + b, 0) / times.length,
      p50: times[Math.floor(times.length / 2)],
      worst: times[times.length - 1],
    });
  }
  return out;
}, REPS);

await browser.close();

// Aggregate the three sample frames per (tier, format): average of averages,
// worst of worsts, since the worst case is what misses a refresh deadline.
const agg = new Map();
for (const r of rows) {
  const k = `${r.tier}|${r.format}`;
  const a = agg.get(k) || { tier: r.tier, format: r.format, px: `${r.w}x${r.h}`, n: 0, avg: 0, worst: 0, kb: 0 };
  a.n++; a.avg += r.avg; a.kb += r.bytes / 1024;
  if (r.worst > a.worst) a.worst = r.worst;
  agg.set(k, a);
}
const ORDER = ["phone", "default", "large", "motion", "motion-phone"];
const list = [...agg.values()].sort((a, b) =>
  ORDER.indexOf(a.tier) - ORDER.indexOf(b.tier) || a.format.localeCompare(b.format));

const cols = ["tier", "pixels", "format", "avg ms", "worst ms", "avg KB/frame"];
const data = list.map((a) => [a.tier, a.px, a.format,
  (a.avg / a.n).toFixed(1), a.worst.toFixed(1), (a.kb / a.n).toFixed(0)]);
const w = cols.map((c, i) => Math.max(c.length, ...data.map((r) => r[i].length)));
const line = (c) => "| " + c.map((v, i) => String(v).padEnd(w[i])).join(" | ") + " |";
console.log(line(cols));
console.log("|" + w.map((n) => "-".repeat(n + 2)).join("|") + "|");
for (const r of data) console.log(line(r));
console.log(`\n${REPS} decodes per file, 3 frames per tier, real GPU, 60Hz budget 16.7ms.`);
