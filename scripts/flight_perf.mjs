// Mid-tier mobile, 4G: LCP, CLS and "input interactive" for the two front
// pages. "Before" is /checks (the old front page, unchanged markup, no
// footage); "after" is / (the same page with the fly-through on top).
//
// Run with the dev server up:  node scripts/flight_perf.mjs
// Headed, because headless Chromium draws on SwiftShader and every number
// that touches decoding or compositing would be a number about software
// rasterisation rather than about this page.
//
// Mid-tier mobile = 4x CPU throttling on a Pixel 5 profile. 4G = 9Mbps down,
// 1.5Mbps up, 85ms RTT.
//
// Two interactivity numbers, because one would be dishonest:
//   present  the first-screen claim field is in the DOM and has been painted,
//            timed inside the page off a rAF, so no harness cost is counted.
//   typed    a real keystroke driven from the harness lands in that field.
//            Includes CDP round trips under 4x throttling, so read it as an
//            upper bound rather than what a thumb would feel.
import { chromium, devices } from "playwright";

const RUNS = 5;
// Each page's first-screen field: the fly-through's own input on "/", the
// checker's textarea on "/checks".
const PAGES = [["before  /checks", "http://localhost:5000/checks", "#claim-input"],
               ["after   /", "http://localhost:5000/", "#flight-q"]];

const COLLECT = (sel) => `
  window.__m = { lcp: 0, cls: 0, present: null, shifts: [] };
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) window.__m.lcp = e.startTime;
  }).observe({ type: "largest-contentful-paint", buffered: true });
  new PerformanceObserver((l) => {
    for (const e of l.getEntries()) {
      if (e.hadRecentInput) continue;
      window.__m.cls += e.value;
      window.__m.shifts.push(Math.round(e.startTime) + "ms " + e.value.toFixed(4) + " "
        + (e.sources || []).map((s) => (s.node && s.node.nodeName
            ? s.node.nodeName + (s.node.id ? "#" + s.node.id : "") : "?")
          + " " + JSON.stringify(s.previousRect) + "->" + JSON.stringify(s.currentRect)).join(" | "));
    }
  }).observe({ type: "layout-shift", buffered: true });
  (function look() {
    const f = document.querySelector(${JSON.stringify(sel)});
    const r = f && f.getBoundingClientRect();
    if (r && r.width > 0 && r.height > 0) {
      requestAnimationFrame(() => { window.__m.present = performance.now(); });
      return;
    }
    requestAnimationFrame(look);
  })();
`;

function pct(a, p) {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.floor(s.length * p))];
}
const r0 = (n) => (Number.isFinite(n) ? Math.round(n) + "ms" : "n/a");

const browser = await chromium.launch({ headless: false });
const rows = [];

for (const [label, url, sel] of PAGES) {
  const lcps = [], clss = [], present = [], typed = [];
  for (let i = 0; i < RUNS; i++) {
    const ctx = await browser.newContext({ ...devices["Pixel 5"] });
    const page = await ctx.newPage();
    await page.addInitScript(COLLECT(sel));
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false, latency: 85,
      downloadThroughput: (9 * 1024 * 1024) / 8,
      uploadThroughput: (1.5 * 1024 * 1024) / 8,
    });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });

    const t0 = Date.now();
    await page.goto(url, { waitUntil: "commit" });
    await page.waitForSelector(sel, { state: "visible", timeout: 20000 });
    const f = await page.$(sel);
    await f.focus();
    await page.keyboard.press("a");
    await page.waitForFunction(
      ([s]) => document.querySelector(s).value === "a", [sel], { timeout: 10000 });
    typed.push(Date.now() - t0);
    await f.fill("");

    await page.waitForLoadState("load").catch(() => {});
    await page.waitForTimeout(4000);
    const m = await page.evaluate(() => Object.assign({}, window.__m, {
      noFlight: document.documentElement.classList.contains("no-flight")
        || document.body.classList.contains("no-flight"),
    }));
    console.log(`  ${label} run ${i}: lcp ${Math.round(m.lcp)} cls ${m.cls.toFixed(4)}`
      + ` present ${Math.round(m.present)} typed ${typed[typed.length - 1]}`
      + (m.noFlight ? " NO-FLIGHT" : ""));
    for (const s of m.shifts) console.log("      shift " + s);
    lcps.push(m.lcp); clss.push(m.cls);
    present.push(m.present ?? NaN);
    await ctx.close();
  }
  rows.push({ label,
    lcp: pct(lcps, 0.5), lcpWorst: Math.max(...lcps),
    cls: Math.max(...clss),
    present: pct(present, 0.5), presentWorst: Math.max(...present),
    typed: pct(typed, 0.5), typedWorst: Math.max(...typed) });
}

await browser.close();

console.log("\n| page | LCP med | LCP worst | CLS worst | field present med | worst | keystroke lands med | worst |");
console.log("|---|---|---|---|---|---|---|---|");
for (const r of rows) {
  console.log(`| ${r.label} | ${r0(r.lcp)} | ${r0(r.lcpWorst)} | ${r.cls.toFixed(4)} | `
    + `${r0(r.present)} | ${r0(r.presentWorst)} | ${r0(r.typed)} | ${r0(r.typedWorst)} |`);
}
