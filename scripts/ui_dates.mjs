#!/usr/bin/env node
/**
 * Dates a reader sees (q10-site fix 3), against the fixture server
 * (scripts/ui_fixture_server.py). The server prints each date as the home
 * zone's day (America/Los_Angeles) inside <time datetime>; the script prints
 * the reader's own day from the instant. Exits non-zero if a check fails.
 *
 *   node scripts/ui_dates.mjs [baseUrl]
 *
 * 1. In the home zone the page as served and the page after the script print the same day.
 * 2. In Asia/Kolkata, after load, every <time> and the review line show the Kolkata day of the instant.
 */
import { chromium } from "playwright";

const BASE = process.argv[2] || "http://127.0.0.1:5050";
const CLAIM = "Apple cider vinegar cures diabetes";
const fails = [];
const must = (ok, msg) => { if (!ok) fails.push(msg); console.log(`${ok ? "PASS" : "FAIL"} ${msg}`); };
const dayIn = (iso, tz) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: tz });

const browser = await chromium.launch();
try {
  const served = await (await fetch(`${BASE}/?q=${encodeURIComponent(CLAIM)}`)).text();
  const m = served.match(/id="review-line"[^>]*>Checked <time datetime="([^"]+)">([^<]+)<\/time>/);
  must(!!m, "the served result page wraps the review line's day in <time datetime>");
  const home = await (await fetch(`${BASE}/`)).text();
  const p = home.match(/class="proof-meta">Checked <time datetime="([^"]+)">([^<]+)<\/time>/);
  must(!!p, "the served home page wraps the trust example's day in <time datetime>");
  if (m) must(m[2] === dayIn(m[1], "America/Los_Angeles"), `served day is the home zone's day (${m[2]})`);
  for (const tz of ["America/Los_Angeles", "Asia/Kolkata"]) {
    const ctx = await browser.newContext({ timezoneId: tz, serviceWorkers: "block", viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto(`${BASE}/?q=${encodeURIComponent(CLAIM)}`, { waitUntil: "networkidle" });
    const r = await page.evaluate(() => { const t = document.querySelector("#review-line time"); return t && { iso: t.dateTime, text: t.textContent }; });
    must(!!r && r.text === dayIn(r.iso, tz), `${tz}: the review line shows the reader's day (${r && r.text})`);
    if (tz === "America/Los_Angeles" && m && r) must(r.text === m[2], "home zone: server and script print the same day");
    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    const ts = await page.evaluate(() => [...document.querySelectorAll("time[datetime]")].map((t) => [t.dateTime, t.textContent]));
    must(ts.length > 0 && ts.every(([iso, text]) => text === dayIn(iso, tz)), `${tz}: every <time> on the home page shows the reader's day`);
    await ctx.close();
  }
} finally {
  await browser.close();
}
if (fails.length) { console.log(`${fails.length} failed`); process.exit(1); }
console.log("all passed");
