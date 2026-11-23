#!/usr/bin/env node
/**
 * What a first visit to "/" costs, and when the claim box can be used.
 *
 *   node scripts/measure_load.mjs [baseUrl] [--log server.log] [--live https://checkevident.com]
 *
 * For "/" (3D) and "/?nogl" (text mode), each in a cold context: Chromium,
 * 390x844 phone, 150 ms round trip, 1.6 Mbps down, 750 kbps up, CPU slowed
 * 4 times (all set through CDP). Prints every request in the order it
 * started with its size as sent (body as encoded plus headers), the total
 * before the first 3D file starts, the 3D total, and:
 *   painted  the claim box is on screen after first contentful paint (then
 *            it is proven to take focus and accept typing)
 *   wired    the page's scripts have run (DOMContentLoaded), so pressing
 *            "Check the research" runs a check
 * Then a repeat visit in the same context (service worker installed): the
 * requests that reached the server, read from the server's log (--log).
 * Then what pressing the button does before app.js has arrived.
 * With --live, the live site's sizes for the same paths (GET only, sent
 * with Accept-Encoding: br, gzip). Nothing is ever posted.
 *
 * The app gzips text itself (app.py _compress), so the local sizes are as
 * sent. CDP throttling does slow 127.0.0.1 (a 664 KB file takes 3.6 s at
 * these settings), and 127.0.0.1 is a secure context, so the service worker
 * registers. The service worker's own fetches are not slowed by the page's
 * throttling; their sizes are counted all the same.
 */
process.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS = "1";
const { chromium } = await import("playwright");
import { execFileSync } from "node:child_process";
import fs from "node:fs";

const arg = (k) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://127.0.0.1:5652";
const logPath = arg("--log");
const live = arg("--live");
const THREE_D = /\/static\/vendor\/three|\/static\/desk\/wood/;
const kb = (b) => (b / 1024).toFixed(1);

const browser = await chromium.launch();

async function visit(path) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
    serviceWorkers: "allow",
  });
  const rows = [];
  const pending = [];
  let seq = 0;
  ctx.on("request", (r) => { r._seq = seq++; r._t = Date.now(); });
  ctx.on("requestfinished", (r) => {
    pending.push(r.sizes().then(async (s) => {
      const res = await r.response();
      rows.push({ seq: r._seq, t: r._t, url: r.url().replace(base, ""), status: res ? res.status() : 0,
                  bytes: s.responseBodySize + s.responseHeadersSize,
                  viaSW: !!(res && res.fromServiceWorker()), bySW: !!r.serviceWorker() });
    }, () => {}));
  });
  await ctx.addInitScript(() => {
    const look = () => {
      const el = document.getElementById("claim-input");
      const fcp = performance.getEntriesByName("first-contentful-paint").length > 0;
      if (el && fcp) {
        const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
        if (r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && r.top < innerHeight) {
          window.__boxAt = performance.now(); return;
        }
      }
      requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8,
  });
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  const t0 = Date.now();
  await page.goto(base + path, { waitUntil: "commit", timeout: 120000 });
  await page.waitForFunction(() => window.__boxAt, null, { timeout: 120000, polling: 50 });
  await page.focus("#claim-input");
  await page.keyboard.type("Zinc");
  const typed = await page.$eval("#claim-input", (e) => document.activeElement === e && e.value === "Zinc");
  await page.$eval("#claim-input", (e) => { e.value = ""; });
  await page.waitForLoadState("load", { timeout: 120000 });
  const timing = await page.evaluate(() => {
    const n = performance.getEntriesByType("navigation")[0];
    return { box: window.__boxAt, dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd };
  });
  // the desk starts at the load event; give the 3D files and the service worker install time to finish
  await page.waitForTimeout(path.includes("nogl") ? 8000 : 25000);
  await Promise.all(pending);
  rows.sort((a, b) => a.seq - b.seq);
  const first = rows.slice();
  let repeat = null;
  if (logPath && !path.includes("nogl")) {
    await page.waitForTimeout(3000);
    // markers in the server's log, sent from here (not the browser), bracket the repeat visit
    await fetch(base + "/robots.txt?repeat-start");
    await page.goto(base + path, { waitUntil: "load", timeout: 120000 });
    await page.waitForTimeout(8000);
    await fetch(base + "/robots.txt?repeat-end");
    await new Promise((r) => setTimeout(r, 500));
    const log = fs.readFileSync(logPath, "utf8");
    const added = log.slice(log.lastIndexOf("repeat-start"), log.lastIndexOf("repeat-end")).replace(/\x1b\[[0-9;]*m/g, "").split("\n").slice(1, -1);
    repeat = added.filter((l) => /"GET /.test(l)).map((l) => l.replace(/^.*"GET (\S+) [^"]*" \D*(\d{3}).*$/, "$1 $2"));
  }
  await ctx.close();
  return { path, rows: first, timing, typed, wall: Date.now() - t0, repeat };
}

function report(v) {
  console.log(`\n## ${v.path}  (cold, 390x844, 150 ms, 1.6/0.75 Mbps, CPU x4)`);
  console.log("| # | request | status | KB as sent | note |\n|---|---|---|---|---|");
  let before = 0, three = 0, install = 0, seen3d = false, i = 0;
  for (const r of v.rows) {
    const is3d = THREE_D.test(r.url);
    if (is3d) seen3d = true;
    // a response the service worker answered from its own fetch is counted once, at that fetch
    const counted = r.viaSW ? 0 : r.bytes;
    if (is3d) three += counted; else if (r.bySW) install += counted; else if (!seen3d) before += counted;
    const note = [r.bySW ? "service worker fetch" : "", r.viaSW ? "answered by the service worker" : "", is3d ? "3D" : ""].filter(Boolean).join(", ");
    console.log(`| ${++i} | ${r.url.slice(0, 90)} | ${r.status} | ${kb(r.bytes)} | ${note} |`);
  }
  console.log(`\npage requests before the first 3D file: ${kb(before)} KB; 3D: ${kb(three)} KB; service worker fetches (install and after): ${kb(install)} KB`);
  console.log(`claim box painted at ${Math.round(v.timing.box)} ms (focus and typing ${v.typed ? "work" : "FAILED"}); scripts wired (DOMContentLoaded) at ${Math.round(v.timing.dcl)} ms; load event at ${Math.round(v.timing.load)} ms`);
  if (v.repeat) console.log(`repeat visit, requests that reached the server: ${v.repeat.length}${v.repeat.length ? ": " + v.repeat.join(", ") : ""}`);
  return v.rows.map((r) => r.url);
}

async function early() {
  // app.js held back: what does pressing the button do before it arrives?
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  const page = await ctx.newPage();
  await page.route(/\/static\/app\.js/, async (route) => { await new Promise((r) => setTimeout(r, 6000)); route.continue().catch(() => {}); });
  await page.goto(base + "/", { waitUntil: "commit" });
  await page.waitForSelector("#claim-input");
  await page.fill("#claim-input", "Zinc shortens colds");
  const navs = [];
  page.on("framenavigated", (f) => { if (f === page.mainFrame()) navs.push(f.url().replace(base, "")); });
  await page.click("#submit-btn", { noWaitAfter: true }).catch(() => {});
  await page.waitForTimeout(1500);
  const after = await page.evaluate(() => ({ url: location.href, value: (document.getElementById("claim-input") || {}).value }))
    .catch(() => ({ url: "(navigating)", value: "" }));
  await ctx.close();
  console.log(`\n## pressing "Check the research" before app.js arrives`);
  console.log(`navigations: ${navs.length ? navs.join(" ") : "none"}; address after: ${after.url.replace(base, "")}; claim box holds: ${JSON.stringify(after.value)}`);
}

const urls = new Set();
for (const p of ["/", "/?nogl"]) report(await visit(p)).forEach((u) => urls.add(u.split("#")[0]));
await early();
await browser.close();

if (live) {
  console.log(`\n## the live site's sizes for the same paths (GET, Accept-Encoding: br, gzip)`);
  console.log("| path | KB as sent | encoding |\n|---|---|---|");
  for (const u of urls) {
    if (!u.startsWith("/") || u.startsWith("/api/")) continue;
    const path = u.replace(/\?v=[0-9a-f]+$/, "");
    const out = execFileSync("curl", ["-s", "-o", "/dev/null", "-H", "Accept-Encoding: br, gzip", "--max-time", "30",
      "-w", "%{size_download} %{size_header} %header{content-encoding}", live + path]);
    const [body, head, enc] = out.toString().split(" ");
    console.log(`| ${path} | ${kb(Number(body) + Number(head))} | ${enc || "none"} |`);
  }
}
