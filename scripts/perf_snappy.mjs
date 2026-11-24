#!/usr/bin/env node
/**
 * How fast the reader's own device answers: load, presses, a check, the result
 * flow and idle, on the phone profile (390x844, 150 ms, 1.6/0.75 Mbps, CPU x4,
 * as scripts/measure_load.mjs) and on a plain desktop (1440x900). Against the
 * fixture server (scripts/ui_fixture_server.py) with UI_FIXTURE_PAUSE=3.3, so
 * the stages of a check arrive over about 10 s. No model, no network.
 *
 *   node scripts/perf_snappy.mjs [baseUrl] [--only phone|desktop] [--json out.json] [--gl metal|swiftshader]
 *
 * --gl metal (the default) draws on this Mac's own GPU through ANGLE, nearer a
 * real phone than SwiftShader, which runs the GPU on the CPU and turns every
 * shader link and texture upload into seconds.
 *
 * Prints one JSON object per profile. Every time is in ms.
 */
import { chromium } from "playwright";
import fs from "node:fs";

const arg = (k) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://127.0.0.1:5674";
const only = arg("--only"), jsonOut = arg("--json");
const ARGS = arg("--gl") === "swiftshader" ? ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] : ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu"];
const LIVE = "Vitamin D supplements prevent colds";
const SAVED = "Creatine improves cognitive performance";
const browser = await chromium.launch({ args: ARGS });

const PROFILES = {
  phone: { ctx: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true }, net: true, cpu: 4 },
  desktop: { ctx: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }, net: false, cpu: 1 },
};

// ---- in-page probes -------------------------------------------------------------
function probes() {
  const P = window.__perf = { lt: [], ev: [], renders: 0, renderMs: 0, gl: {}, warm: [], arrived: 0, fr: null };
  try { new PerformanceObserver((l) => l.getEntries().forEach((e) => P.lt.push([Math.round(e.startTime), Math.round(e.duration)]))).observe({ type: "longtask", buffered: true }); } catch (e) {}
  try { new PerformanceObserver((l) => l.getEntries().forEach((e) => P.ev.push({ n: e.name, s: e.startTime, d: e.duration, id: e.interactionId }))).observe({ type: "event", durationThreshold: 16, buffered: true }); } catch (e) {}
  const look = () => {
    const el = document.getElementById("claim-input");
    if (el && performance.getEntriesByName("first-contentful-paint").length) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden" && r.top < innerHeight) { P.boxAt = performance.now(); return; }
    }
    requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
  // three.js arrives, then the desk: wrap the renderer and the GL calls that cost
  const hook = () => {
    if (!P.libAt && typeof THREE !== "undefined") P.libAt = performance.now();
    const d = window.__evd;
    if (!d || !d.renderer) { setTimeout(hook, 20); return; }
    if (d.gl) {
      const r = d.renderer, render = r.render.bind(r), compile = r.compile.bind(r), init = r.initTexture.bind(r);
      r.render = function (s, c) { const t = performance.now(); render(s, c); P.renders++; P.renderMs += performance.now() - t; };
      r.compile = function (s, c) { const t = performance.now(); compile(s, c); P.warm.push(["compile", Math.round(t), Math.round(performance.now() - t)]); };
      r.initTexture = function (x) { const t = performance.now(); init(x); P.warm.push(["tex", Math.round(t), Math.round(performance.now() - t)]); };
      const gl = r.getContext();
      ["texImage2D", "texSubImage2D", "generateMipmap", "compileShader", "linkProgram", "getProgramParameter", "getShaderParameter", "getProgramInfoLog"].forEach((k) => {
        const o = gl[k].bind(gl);
        gl[k] = function (...a) { const t = performance.now(); const v = o(...a); const g = P.gl[k] || (P.gl[k] = [0, 0]); g[0]++; g[1] += performance.now() - t; return v; };
      });
      P.glAt = performance.now();
    }
  };
  hook();
  // the answer arriving on the stream: the chunk that carries the done stage
  const read = ReadableStreamDefaultReader.prototype.read;
  const dec = new TextDecoder();
  ReadableStreamDefaultReader.prototype.read = function () {
    return read.call(this).then((r) => {
      if (r && r.value && r.value.byteLength && !P.arrived) { try { if (/"stage":\s*"done"/.test(dec.decode(r.value, { stream: true }))) P.arrived = performance.now(); } catch (e) {} }
      return r;
    });
  };
  // frames: every rAF interval while on
  P.frames = (on) => {
    if (!on) { const f = P.fr || []; P.fr = null; return f; }
    P.fr = []; let last = performance.now();
    const loop = (t) => { if (!P.fr) return; P.fr.push(Math.round(t - last)); last = t; requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  };
  // the picture and the page, as one string: any change is movement
  P.sig = () => {
    const d = window.__evd, c = d && d.camera, v = document.getElementById("vcard");
    return [Math.round(scrollY), c ? c.position.x.toFixed(4) : "", c ? c.position.y.toFixed(4) : "", c ? c.position.z.toFixed(4) : "", c ? c.fov.toFixed(3) : "", v ? v.style.opacity : ""].join(",");
  };
  // after a press: when the first movement is seen and when the last one was
  P.watch = (ms) => new Promise((res) => {
    let press = null; const s0 = P.sig(); let first = null, lastAt = null, prev = s0;
    const pd = (e) => { if (press === null) press = e.timeStamp; };
    addEventListener("pointerdown", pd, true); addEventListener("touchstart", pd, true); addEventListener("keydown", pd, true);
    const t0 = performance.now();
    const loop = () => {
      const now = performance.now(), s = P.sig();
      if (s !== prev) { if (first === null && press !== null) first = now; lastAt = now; prev = s; }
      if (now - t0 > ms || (lastAt !== null && now - lastAt > 900)) {
        removeEventListener("pointerdown", pd, true); removeEventListener("touchstart", pd, true); removeEventListener("keydown", pd, true);
        res({ first: first !== null && press !== null ? Math.round(first - press) : null, settled: lastAt !== null && press !== null ? Math.round(lastAt - press) : null, press });
        return;
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
}

// interactions recorded by the Event Timing API after t: input to next paint, per interaction
function interactions(ev, t, names) {
  const by = {};
  ev.filter((e) => e.s >= t && e.id && (!names || names.test(e.n))).forEach((e) => { const g = by[e.id] || (by[e.id] = { s: e.s, end: 0 }); g.s = Math.min(g.s, e.s); g.end = Math.max(g.end, e.s + e.d); });
  return Object.values(by).map((g) => Math.round(g.end - g.s));
}
const q = (a, p) => { if (!a.length) return null; const s = a.slice().sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
const sum = (a) => a.reduce((x, y) => x + y, 0);
const ltIn = (lt, a, b) => lt.filter((x) => x[0] >= a && x[0] < b);
const ltSum = (lt) => ({ n: lt.length, total: sum(lt.map((x) => x[1])), max: lt.length ? Math.max(...lt.map((x) => x[1])) : 0 });
const frameStats = (f) => ({ n: f.length, p50: q(f, 0.5), p95: q(f, 0.95), max: f.length ? Math.max(...f) : 0, over20: f.filter((x) => x > 20).length, over32: f.filter((x) => x > 32).length });

async function open(name) {
  const pr = PROFILES[name];
  const ctx = await browser.newContext({ ...pr.ctx, serviceWorkers: "block" });
  await ctx.addInitScript(probes);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Performance.enable");
  if (pr.net) { await cdp.send("Network.enable"); await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 }); }
  if (pr.cpu > 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: pr.cpu });
  return { ctx, page, cdp, errors, pr };
}
const ready = (page) => page.evaluate(() => Promise.race([window.__evident.ready().then(() => performance.now()), new Promise((r) => setTimeout(() => r(-1), 60000))]));
const perf = (page) => page.evaluate(() => ({ lt: __perf.lt, ev: __perf.ev, renders: __perf.renders, renderMs: Math.round(__perf.renderMs), gl: JSON.parse(JSON.stringify(__perf.gl)), warm: __perf.warm, now: performance.now() }));
async function cpuOver(page, cdp, ms) {
  const m = async () => Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((x) => [x.name, x.value]));
  const r0 = await page.evaluate(() => __perf.renders), a = await m();
  const f0 = await page.evaluate(() => window.__evident.stats().frames);
  await page.waitForTimeout(ms);
  const b = await m(), r1 = await page.evaluate(() => __perf.renders);
  const f1 = await page.evaluate(() => window.__evident.stats().frames);
  return { renders: r1 - r0, deskFrames: f1 - f0, taskMs: Math.round((b.TaskDuration - a.TaskDuration) * 1000), scriptMs: Math.round((b.ScriptDuration - a.ScriptDuration) * 1000) };
}
const press = async (page, pr, sel) => { if (pr.ctx.hasTouch) await page.tap(sel); else await page.click(sel); };
const stopNow = (page) => page.evaluate(() => { const s = window.__evident.stats(); return { stop: s.stop, stops: s.stops, mode: s.mode, next: document.getElementById("next-label").textContent }; });
async function scroll(page, cdp, pr, dy) {
  await cdp.send("Input.synthesizeScrollGesture", { x: Math.round(pr.ctx.viewport.width / 2), y: Math.round(pr.ctx.viewport.height * 0.6), yDistance: -dy, speed: 1200, gestureSourceType: pr.ctx.hasTouch ? "touch" : "mouse" });
}

// a CPU profile of the main thread for one window, its heaviest functions by self time
async function profiled(cdp, fn) {
  await cdp.send("Profiler.enable"); await cdp.send("Profiler.setSamplingInterval", { interval: 200 }); await cdp.send("Profiler.start");
  const out = await fn();
  const { profile } = await cdp.send("Profiler.stop");
  const dt = {}, byId = new Map(profile.nodes.map((n) => [n.id, n]));
  for (let i = 0; i < profile.samples.length; i++) { const n = byId.get(profile.samples[i]); const cf = n.callFrame; const k = (cf.functionName || "(anon)") + " " + cf.url.replace(/^.*\//, "").replace(/\?.*$/, "") + ":" + (cf.lineNumber + 1); dt[k] = (dt[k] || 0) + (profile.timeDeltas[i] || 0) / 1000; }
  const top = Object.entries(dt).filter(([k]) => !/^\(idle\)|^\(program\)|^\(root\)/.test(k)).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([k, v]) => [k, Math.round(v)]);
  return { out, top, program: Math.round(dt["(program)  :0"] || 0), gc: Math.round(dt["(garbage collector)  :0"] || 0) };
}

async function run(name) {
  const R = { profile: name };
  // ---- a. load, and b. typing while the desk starts ----
  {
    const { ctx, page, cdp, errors, pr } = await open(name);
    await page.goto(base + "/", { waitUntil: "commit", timeout: 120000 });
    await page.waitForFunction(() => window.__perf && window.__perf.boxAt, null, { timeout: 120000, polling: 50 });
    // typing as the 3D arrives: one key every 150 ms from the moment three.js is in
    await page.waitForFunction(() => typeof THREE !== "undefined", null, { timeout: 120000, polling: 20 });
    await page.focus("#claim-input");
    const tType = await page.evaluate(() => performance.now());
    for (const ch of "Zinc shortens") { await page.keyboard.type(ch); await page.waitForTimeout(150); }
    const readyAt = await ready(page);
    await page.waitForTimeout(1500);
    let p = await perf(page);
    const tl = await page.evaluate(() => ({ box: __perf.boxAt, lib: __perf.libAt, gl: __perf.glAt, dcl: performance.getEntriesByType("navigation")[0].domContentLoadedEventEnd, load: performance.getEntriesByType("navigation")[0].loadEventEnd, mode: window.__evident.stats().mode }));
    R.load = { boxUsable: Math.round(tl.box), dcl: Math.round(tl.dcl), loadEvent: Math.round(tl.load), libIn: Math.round(tl.lib), ready3d: Math.round(readyAt), mode: tl.mode,
      longTasksToReady: ltSum(ltIn(p.lt, 0, readyAt)), longTaskList: ltIn(p.lt, 0, readyAt + 1500).filter((x) => x[1] > 100) };
    const keys = interactions(p.ev, tType, /key|input|beforeinput/);
    R.typingWhile3dStarts = { n: keys.length, p50: q(keys, 0.5), max: keys.length ? Math.max(...keys) : null, note: "interactions slower than 16 ms only" };
    // typing at rest on the home page
    await page.$eval("#claim-input", (e) => { e.value = ""; });
    const tRest = await page.evaluate(() => performance.now());
    for (const ch of "Magnesium") { await page.keyboard.type(ch); await page.waitForTimeout(120); }
    await page.waitForTimeout(300);
    p = await perf(page);
    const k2 = interactions(p.ev, tRest, /key|input|beforeinput/);
    R.typingAtRest = { n: k2.length, p50: q(k2, 0.5), max: k2.length ? Math.max(...k2) : null };
    await page.$eval("#claim-input", (e) => { e.value = ""; e.blur(); });
    // ---- e. idle on home ----
    await page.waitForTimeout(1000);
    R.idleHome = await cpuOver(page, cdp, 5000);
    // ---- b. a scripted scroll of the home page ----
    await page.evaluate(() => __perf.frames(true));
    const r0 = await page.evaluate(() => __perf.renders), t0 = await page.evaluate(() => performance.now());
    await scroll(page, cdp, pr, 900); await page.waitForTimeout(300); await scroll(page, cdp, pr, -900); await page.waitForTimeout(500);
    const fr = await page.evaluate(() => __perf.frames(false));
    p = await perf(page);
    R.homeScroll = { ...frameStats(fr), renders: p.renders - r0, longTasks: ltSum(ltIn(p.lt, t0, p.now)), maxY: await page.evaluate(() => document.documentElement.scrollHeight - innerHeight) };
    await page.evaluate(() => scrollTo(0, 0)); await page.waitForTimeout(800);
    // ---- b. and c. the press, the check, the answer ----
    await fetch(base + "/__fixture/reset");
    await page.fill("#claim-input", LIVE);
    await page.waitForTimeout(400);
    const tSub = await page.evaluate(() => performance.now());
    await page.evaluate(() => __perf.frames(true));
    const rs0 = await page.evaluate(() => __perf.renders);
    await press(page, pr, "#submit-btn");
    await page.waitForFunction(() => window.__perf.arrived > 0, null, { timeout: 90000, polling: 20 });
    // the answer on screen: the verdict laid out and visible, then the frame after it
    const shown = await page.evaluate(() => new Promise((res) => {
      const look = () => { const s = document.getElementById("stamp"), r = s && s.getBoundingClientRect(); if (document.documentElement.classList.contains("checked") && s.textContent.trim() && r.height > 0 && r.bottom > 0 && r.top < innerHeight) requestAnimationFrame(() => res(performance.now())); else requestAnimationFrame(look); };
      look();
    }));
    const arrived = await page.evaluate(() => __perf.arrived);
    const fr2 = await page.evaluate(() => __perf.frames(false));
    p = await perf(page);
    const tap = interactions(p.ev, tSub, /pointer|click|touch|mouse/);
    R.checkPress = { firstPaintAfterPress: tap.length ? Math.max(...tap) : "<16 (no slow interaction)" };
    R.duringCheck = { ms: Math.round(arrived - tSub), frames: frameStats(fr2), renders: p.renders - rs0, longTasks: ltSum(ltIn(p.lt, tSub, arrived)) };
    R.answerArrivedToShown = { ms: Math.round(shown - arrived), longTasks: ltIn(p.lt, arrived - 5, shown).map((x) => x[1]) };
    // the glide to the answer, to rest
    const gl = await page.evaluate(() => new Promise((res) => { let prev = __perf.sig(), lastAt = performance.now(); const t0 = performance.now(); const loop = () => { const s = __perf.sig(), n = performance.now(); if (s !== prev) { prev = s; lastAt = n; } if (n - lastAt > 900 || n - t0 > 15000) res(Math.round(lastAt)); else requestAnimationFrame(loop); }; loop(); }));
    R.answerToSettled = Math.round(gl - arrived);
    // ---- d. the first Next, 1 s after the answer settled, profiled ----
    await page.waitForTimeout(1000);
    const g0 = (await perf(page)).gl, w0 = (await perf(page)).warm.length;
    const fn = await profiled(cdp, async () => {
      const w = page.evaluate(() => __perf.watch(8000));
      await page.waitForTimeout(50);
      await press(page, pr, "#next");
      return w;
    });
    p = await perf(page);
    const glDelta = Object.fromEntries(Object.entries(p.gl).map(([k, v]) => [k, [v[0] - ((g0[k] || [0])[0]), Math.round(v[1] - ((g0[k] || [0, 0])[1]))]]).filter(([, v]) => v[0]));
    R.firstNextFresh = { ...fn.out, gl: glDelta, warmBefore: p.warm.slice(0, w0).length, warmAfter: p.warm.slice(w0), top: fn.top, longTasks: ltIn(p.lt, fn.out.press - 5, fn.out.press + 3000).map((x) => x[1]) };
    // ---- d. every move after: Next to the end, then Back to the start ----
    const moves = [];
    for (let i = 0; i < 14; i++) {
      const s = await stopNow(page); if (s.stop >= s.stops && /Back to the start/.test(s.next)) break;
      if (s.stop >= s.stops && !/Keep reading/.test(s.next)) break;
      const w = page.evaluate(() => __perf.watch(8000)); await page.waitForTimeout(50);
      await press(page, pr, "#next"); const r = await w; const s2 = await stopNow(page);
      moves.push({ press: "Next", from: s.stop, to: s2.stop, label: s.next, first: r.first, settled: r.settled });
    }
    for (let i = 0; i < 14; i++) {
      const s = await stopNow(page); if (s.stop <= 1) break;
      const w = page.evaluate(() => __perf.watch(8000)); await page.waitForTimeout(50);
      await press(page, pr, "#prev"); const r = await w; const s2 = await stopNow(page);
      moves.push({ press: "Back", from: s.stop, to: s2.stop, first: r.first, settled: r.settled });
    }
    R.moves = moves;
    // ---- e. idle on a settled result ----
    await page.evaluate(() => window.__evident.goto(2)); await page.waitForTimeout(2500);
    R.idleResult = await cpuOver(page, cdp, 5000);
    R.errors = errors;
    await ctx.close();
  }
  // ---- d. a saved answer: the first Next 1 s after it is ready, and 6 s after ----
  for (const wait of [1000, 6000]) {
    const { ctx, page, cdp, errors, pr } = await open(name);
    await page.goto(`${base}/?q=${encodeURIComponent(SAVED)}`, { waitUntil: "load", timeout: 120000 });
    const rd = await ready(page);
    await page.waitForTimeout(wait);
    const w0 = (await perf(page)).warm.length;
    const fn = await profiled(cdp, async () => { const w = page.evaluate(() => __perf.watch(8000)); await page.waitForTimeout(50); await press(page, pr, "#next"); return w; });
    const p = await perf(page);
    R["firstNextSaved_" + wait] = { ready: Math.round(rd), ...fn.out, warmBeforePress: p.warm.slice(0, w0).length, warmAfter: p.warm.slice(w0).length, top: fn.top.slice(0, 6), longTasks: ltIn(p.lt, fn.out.press - 5, fn.out.press + 3000).map((x) => x[1]), errors };
    await ctx.close();
  }
  return R;
}

const all = {};
for (const n of Object.keys(PROFILES)) { if (only && only !== n) continue; all[n] = await run(n); console.log(JSON.stringify(all[n])); }
if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(all, null, 1));
await browser.close();
