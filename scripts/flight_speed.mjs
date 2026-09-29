// Page-speed measurement: the front page (should be unchanged from main's
// recorded baseline of 71ms / CLS 0) and /flight (new, no baseline to
// compare against, so this establishes one). Uses the CDP Performance
// domain rather than a synthetic Lighthouse run, since the number that
// matters here is "did adding the flight route change anything on the
// pages that already existed" and a real load in a real browser answers
// that directly.
import { chromium } from "playwright";

const BASE = "http://localhost:5000";

async function measure(path) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const client = await page.context().newCDPSession(page);
  await client.send("Performance.enable");

  const t0 = Date.now();
  await page.goto(`${BASE}${path}`, { waitUntil: "load" });
  const loadMs = Date.now() - t0;

  const cls = await page.evaluate(() => new Promise((resolve) => {
    let total = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) total += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
    setTimeout(() => resolve(total), 500);
  }));

  const nav = await page.evaluate(() => {
    const n = performance.getEntriesByType("navigation")[0];
    return n ? { transferSize: n.transferSize, domContentLoaded: Math.round(n.domContentLoadedEventEnd) } : null;
  });

  const lcp = await page.evaluate(() => new Promise((resolve) => {
    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      if (entries.length) resolve(Math.round(entries[entries.length - 1].startTime));
    }).observe({ type: "largest-contentful-paint", buffered: true });
    setTimeout(() => resolve(null), 800);
  }));

  await browser.close();
  return { path, loadMs, cls: Number(cls.toFixed(4)), lcp, nav };
}

for (const path of ["/", "/flight"]) {
  const r = await measure(path);
  console.log(
    `${r.path.padEnd(16)} load ${String(r.loadMs).padStart(4)}ms  ` +
    `LCP ${String(r.lcp ?? "n/a").padStart(4)}ms  CLS ${r.cls}  ` +
    `DOMContentLoaded ${r.nav?.domContentLoaded}ms  transfer ${r.nav?.transferSize}B`
  );
}
