#!/usr/bin/env node
/**
 * The desk front's promises that only a real browser can prove, against the
 * fixture server (scripts/ui_fixture_server.py). Exits non-zero if any check
 * fails. No model call and no network: the fixture answers every check.
 *
 *   node scripts/ui_desk.mjs [baseUrl]
 *
 * 4. When the browser drops the drawing context, the page goes to text mode on
 *    the same stop and stays there, every word of the card readable.
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
const SAVED = ["Creatine improves cognitive performance", "Fish oil lowers blood pressure", "Green tea helps weight loss", "Magnesium improves sleep"];
const LIVE = "Vitamin D supplements prevent colds";
const browser = await chromium.launch({ args: ARGS });
const results = [];
const must = (c, m) => { if (!c) throw new Error(m); };

async function test(name, fn, { w = 390, h = 844, reduce = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: reduce ? "reduce" : "no-preference", serviceWorkers: "block" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/net::ERR_INTERNET_DISCONNECTED|Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => (window.__csp = window.__csp || []).push(e.violatedDirective)));
  try {
    await fetch(base + "/__fixture/reset");
    await fn(page, ctx);
    const csp = await page.evaluate(() => window.__csp || []);
    must(!errors.length && !csp.length, "page errors: " + errors.concat(csp).join(" | "));
    results.push(true); console.log(`PASS ${name}`);
  } catch (e) {
    results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally {
    await ctx.close();
  }
}
const ready = (page) => page.evaluate(() => Promise.race([window.__evident.ready(), new Promise((r) => setTimeout(r, 20000))]));
const send = async (page, claim) => { await page.fill("#claim-input", claim); await page.press("#claim-input", "Enter"); };

// ---- 4. The browser drops the drawing context ----------------------------------------
// Lost on purpose (WEBGL_lose_context) at the Answer and at Weighed: the page is in text
// mode at once, on the same stop, the desk is gone, the verdict and the short answer read
// at 4.5:1 or better on what is behind them, and two seconds later it is still text mode.
const lum = (c) => { const m = c.match(/[\d.]+/g).map(Number); const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]); };
const ratio = ([a, b]) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
for (const [w, h] of [[390, 844], [1440, 900]]) {
  for (const [k, name] of [[2, "Answer"], [4, "Weighed"]]) {
    await test(`a lost drawing context leaves the page in text mode on ${name}, every word readable, ${w}x${h}`, async (page) => {
      await page.goto(`${base}/?q=${encodeURIComponent(SAVED[0])}`, { waitUntil: "load" }); await ready(page);
      must((await page.evaluate(() => window.__evident.stats().mode)) === "3d", "the desk did not start in 3D");
      await page.evaluate((n) => window.__evident.goto(n), k); await page.waitForTimeout(1200);
      await page.evaluate(() => { const c = document.getElementById("gl"); (c.getContext("webgl2") || c.getContext("webgl")).getExtension("WEBGL_lose_context").loseContext(); });
      await page.waitForTimeout(600);
      const m = await page.evaluate(() => {
        const bg = (e) => { for (let x = e; x; x = x.parentElement) { const c = getComputedStyle(x).backgroundColor; if (!/rgba\(0, 0, 0, 0\)|transparent/.test(c)) return c; } return getComputedStyle(document.body).backgroundColor; };
        const pair = (s) => { const e = document.querySelector(s); return [getComputedStyle(e).color, bg(e)]; };
        const s = window.__evident.stats();
        return { mode: s.mode, stop: s.stop, stage: getComputedStyle(document.getElementById("stage")).display, v: pair(".vcard .v"), t: pair(".vcard .t"), q: pair(".vcard .q") };
      });
      must(m.mode === "text" && m.stop === k, `mode ${m.mode} on stop ${m.stop}, wanted text on ${k}`);
      must(m.stage === "none", "the desk's canvas is still shown");
      for (const [what, p] of [["verdict", m.v], ["short answer", m.t], ["claim", m.q]]) must(ratio(p) >= 4.5, `the ${what} reads at ${ratio(p).toFixed(2)}:1 (${p.join(" on ")})`);
      await page.waitForTimeout(2000);
      const later = await page.evaluate(() => window.__evident.stats().mode);
      must(later === "text", `two seconds later the page is in mode ${later}`);
    }, { w, h });
  }
}

await browser.close();
const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} passed`);
process.exit(passed === results.length ? 0 : 1);
