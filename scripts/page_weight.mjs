#!/usr/bin/env node
/**
 * First-load weight of the home page. Loads "/" cold, waits without
 * scrolling, and counts every request the page, its workers and its service
 * worker make, with the bytes that crossed the wire.
 *
 *   node scripts/page_weight.mjs [baseUrl] [--input]
 *
 * Prints one row per viewport: requests, MB, frame-ish requests, and the time
 * to the load event at Fast 3G (1.6 Mbps down, 150 ms RTT). With --input it
 * also presses a key and reports what that pulled in. Needs Playwright.
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5000";
const withInput = process.argv.includes("--input");
const VIEWS = [
  { name: "375x812 phone", width: 375, height: 812, mobile: true },
  { name: "375x812 plain viewport (mouse)", width: 375, height: 812, mobile: false },
  { name: "1440x900 desktop", width: 1440, height: 900, mobile: false },
];
const FRAMEISH = /\/footage\/[0-9a-f]{10}\/|\/static\/flight\//;

async function run(browser, v, throttle) {
  const ctx = await browser.newContext({
    viewport: { width: v.width, height: v.height },
    deviceScaleFactor: v.mobile ? 3 : 1,
    isMobile: v.mobile,
    hasTouch: v.mobile,
    serviceWorkers: "allow",
  });
  const rows = [];
  const pending = [];
  ctx.on("requestfinished", (r) => {
    pending.push(
      r.sizes().then(
        (s) => rows.push({ url: r.url(), bytes: s.responseBodySize + s.responseHeadersSize }),
        () => {}
      )
    );
  });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  if (throttle) {
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 150,
      downloadThroughput: (1.6 * 1024 * 1024) / 8,
      uploadThroughput: (750 * 1024) / 8,
    });
  }
  const t0 = Date.now();
  await page.goto(base + "/", { waitUntil: "load", timeout: 120000 });
  const loadMs = Date.now() - t0;
  await page.waitForTimeout(10000);
  await Promise.all(pending);
  const first = rows.slice();
  let afterInput = null;
  if (withInput) {
    await page.keyboard.press("PageDown");
    await page.mouse.wheel(0, 400);
    await page.waitForTimeout(5000);
    await Promise.all(pending);
    afterInput = rows.length - first.length;
  }
  await ctx.close();
  const mb = first.reduce((a, r) => a + r.bytes, 0) / 1048576;
  return {
    requests: first.length,
    mb,
    frames: first.filter((r) => FRAMEISH.test(r.url) && !/frame-0000|still-/.test(r.url)).length,
    loadMs,
    afterInput,
  };
}

// Chromium does not throttle localhost, so a host name that resolves to it is
// used when the base URL is wp.test (http://wp.test:5000).
const browser = await chromium.launch({ args: ["--host-resolver-rules=MAP wp.test 127.0.0.1"] });
console.log(
  "| viewport | requests | MB | frame/bundle requests | load @ Fast 3G |" +
    (withInput ? " requests after first input |" : "")
);
console.log("|---|---|---|---|---|" + (withInput ? "---|" : ""));
for (const v of VIEWS) {
  const open = await run(browser, v, false);
  const slow = await run(browser, v, true);
  console.log(
    `| ${v.name} | ${open.requests} | ${open.mb.toFixed(2)} | ${open.frames} | ${(slow.loadMs / 1000).toFixed(1)} s |` +
      (withInput ? ` ${open.afterInput} |` : "")
  );
}
await browser.close();
