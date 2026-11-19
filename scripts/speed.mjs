#!/usr/bin/env node
/**
 * Speed of the home page and of the first answer, on a throttled phone.
 *
 *   node scripts/speed.mjs [baseUrl] [--runs=3]
 *
 * Run it against the fixture server (scripts/ui_fixture_server.py, default
 * http://localhost:5050). Each run is a cold browser context at 375x812 with
 * a 4x CPU slowdown and "Slow 4G" (1.6 Mbps down, 150 ms round trip). Prints
 * one JSON object: per run and the median of
 *   fcp_ms         first contentful paint of "/"
 *   lcp_ms         largest contentful paint of "/" (final value after 4 s idle)
 *   local_line_ms  Enter to the page's own first line ("Building the search")
 *   first_stage_ms Enter to the first line the server's stream causes ("Searching
 *                  PubMed"), minus the fixture's own pause (stage_pause_ms) so what
 *                  is left is the page and the network.
 * Chromium does not throttle localhost, so the page is opened through the
 * host name wp.test, mapped to 127.0.0.1. Needs Playwright.
 */
import { chromium } from "playwright";

const arg = process.argv.find((a) => a.startsWith("http")) || "http://localhost:5050";
const RUNS = Number((process.argv.find((a) => a.startsWith("--runs=")) || "--runs=3").split("=")[1]);
const u = new URL(arg);
const base = `http://wp.test:${u.port || 80}`;
const STAGE_PAUSE_MS = Number(process.env.UI_FIXTURE_PAUSE || "0.9") * 1000;
const LIVE = "Vitamin D supplements prevent colds";

const median = (a) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; };

async function once(browser) {
  await fetch(`${arg}/__fixture/reset`).catch(() => {});
  const ctx = await browser.newContext({
    viewport: { width: 375, height: 812 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, serviceWorkers: "block",
  });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8,
  });
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.addInitScript(() => {
    window.__m = { fcp: null, lcp: null };
    new PerformanceObserver((l) => { for (const e of l.getEntries()) if (e.name === "first-contentful-paint") window.__m.fcp = e.startTime; })
      .observe({ type: "paint", buffered: true });
    new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__m.lcp = e.startTime; })
      .observe({ type: "largest-contentful-paint", buffered: true });
  });
  await page.goto(base + "/", { waitUntil: "load", timeout: 120000 });
  await page.waitForTimeout(4000);
  const paint = await page.evaluate(() => window.__m);
  // Submit the live claim and time the first line of the reading log.
  await page.evaluate(() => {
    window.__stage = null; window.__local = null;
    const log = document.getElementById("reading");
    new MutationObserver(() => {
      const t = [...log.querySelectorAll("li")].map((li) => li.textContent);
      if (window.__local === null && t.length) window.__local = performance.now();
      if (window.__stage === null && t.some((x) => x.startsWith("Searching PubMed"))) window.__stage = performance.now();
    }).observe(log, { childList: true, subtree: true });
  });
  await page.fill("#claim-input", LIVE);
  const t0 = await page.evaluate(() => { window.__t0 = performance.now(); return window.__t0; });
  await page.press("#claim-input", "Enter");
  await page.waitForFunction(() => window.__stage !== null, null, { timeout: 60000 });
  const t1 = await page.evaluate(() => window.__stage);
  const tl = await page.evaluate(() => window.__local);
  // Let the check finish before the next run resets the fixture, so a late
  // save of the replayed claim cannot turn the next run into a cache hit.
  await page.waitForSelector("#stamp", { state: "visible", timeout: 60000 }).catch(() => {});
  await ctx.close();
  const raw = t1 - t0;
  return {
    fcp_ms: Math.round(paint.fcp), lcp_ms: Math.round(paint.lcp),
    local_line_ms: Math.round(tl - t0), first_stage_raw_ms: Math.round(raw), first_stage_ms: Math.round(raw - STAGE_PAUSE_MS),
  };
}

const browser = await chromium.launch({ args: ["--host-resolver-rules=MAP wp.test 127.0.0.1"] });
const runs = [];
for (let i = 0; i < RUNS; i++) runs.push(await once(browser));
await browser.close();
const med = {};
for (const k of Object.keys(runs[0])) med[k] = median(runs.map((r) => r[k]));
console.log(JSON.stringify({
  profile: "375x812 mobile, 4x CPU, 1.6 Mbps / 150 ms RTT, cold, service worker blocked",
  stage_pause_ms: STAGE_PAUSE_MS, runs, median: med,
}, null, 2));
