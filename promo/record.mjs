// Records one Reels-sized video (1080x1920 MP4) of a real check on the live
// site: a hook card, the claim typed in, the check running (sped up), the
// verdict, a slow scroll through the answer and the studies, and an end card.
// Not part of the site: marketing tooling, run by hand or by the daily task.
//
//   node promo/record.mjs --claim "Coffee causes cancer" \
//        --hook "Does coffee cause cancer?" [--out promo/out/2026-10-11]
//
// Prints one JSON line: the files written and what the site answered, so
// captions can be written to match the verdict exactly.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) =>
  a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc, []));
const CLAIM = (args.claim || "").trim();
if (!CLAIM) { console.error("usage: --claim \"...\" [--hook \"...\"] [--out dir]"); process.exit(2); }
const HOOK = (args.hook || `“${CLAIM}”. True or not?`).trim();
const BASE = args.base || "https://checkevident.com";
const today = new Date().toLocaleDateString("en-CA");
const OUT = path.resolve(args.out || `promo/out/${today}`);
const slug = CLAIM.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
const FRAMES = path.join(OUT, `.frames-${slug}`);
fs.rmSync(FRAMES, { recursive: true, force: true });
fs.mkdirSync(FRAMES, { recursive: true });

// The check itself takes 5 to 20 seconds; on screen it gets this long.
const CHECK_ON_SCREEN = 3.5;

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 405, height: 720 }, deviceScaleFactor: 8 / 3,
  isMobile: true, hasTouch: true, colorScheme: "light",
});
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);

const frames = [];
const marks = {};
const mark = (name) => { marks[name] = Date.now() / 1000; };
cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
  const file = path.join(FRAMES, `${String(frames.length).padStart(5, "0")}.jpg`);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  // Arrival time, on the same clock as the marks below.
  frames.push({ file, t: Date.now() / 1000 });
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});

// A card laid over the page, in the site's own fonts and colours. It is
// only drawn in this recording; the site is never changed.
async function card(html, instant = false) {
  await page.evaluate(([html, instant]) => {
    let el = document.getElementById("promo-card");
    if (!el) {
      el = document.createElement("div");
      el.id = "promo-card";
      el.style.cssText = "position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;"
        + "justify-content:center;gap:22px;padding:0 34px;background:var(--deep);color:var(--on-deep);"
        + "transition:opacity .35s ease;opacity:" + (instant ? "1" : "0");
      document.body.appendChild(el);
    }
    el.innerHTML = html;
    requestAnimationFrame(() => { el.style.opacity = "1"; });
  }, [html, instant]);
  await page.waitForTimeout(400);
}
async function uncard() {
  await page.evaluate(() => { const el = document.getElementById("promo-card"); if (el) el.style.opacity = "0"; });
  await page.waitForTimeout(400);
  await page.evaluate(() => document.getElementById("promo-card")?.remove());
}
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const label = "font:500 15px/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--on-deep-2)";

// A scroll that eases in and out, like a thumb, rather than jumping.
async function glide(toY, ms) {
  await page.evaluate(([toY, ms]) => new Promise((done) => {
    const from = scrollY, dist = toY - from, t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / ms), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      scrollTo(0, from + dist * e);
      k < 1 ? requestAnimationFrame(step) : done();
    };
    requestAnimationFrame(step);
  }), [toY, ms]);
}
const top = (sel) => page.evaluate((sel) => {
  const e = document.querySelector(sel);
  return e ? Math.round(e.getBoundingClientRect().top + scrollY) : null;
}, sel);

let result = {};
try {
  await page.goto(`${BASE}/?focus=1`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  // 1. Hook. The card is up before recording starts, and the page behind
  // it is moved to the claim box, so the first frame is the hook itself.
  await card(`<p style="${label}">Checked against the research</p>
    <p style="margin:0;font:600 50px/1.04 var(--display);letter-spacing:-.03em">${esc(HOOK)}</p>`, true);
  await page.evaluate(() => {
    document.activeElement?.blur();
    const ask = document.getElementById("ask");
    scrollTo(0, ask ? ask.getBoundingClientRect().top + scrollY - 8 : 0);
  });
  await page.waitForTimeout(500);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1080, maxHeight: 1920, everyNthFrame: 1 });
  mark("start");
  await page.waitForTimeout(1700);
  await uncard();

  // 2. Type the claim and send it
  const box = page.locator("#claim-input");
  await box.click();
  await box.pressSequentially(CLAIM, { delay: 35 });
  await page.waitForTimeout(350);
  mark("check");
  await box.press("Enter");

  // 3. The check runs; shown sped up
  await page.waitForFunction(() => {
    const r = document.getElementById("result"), s = document.getElementById("stamp");
    return r && !r.hidden && !r.classList.contains("checking") && s && s.textContent.trim();
  }, null, { timeout: 120000, polling: 200 });
  mark("verdict");

  // 4. The verdict, then a slow look at what it rests on
  await page.waitForTimeout(2400);
  for (const [sel, ms, hold, above] of [["#tldr", 1100, 2200, 150], ["#chart-sec", 1200, 2000, 40], ["#sources", 1100, 1700, 40]]) {
    const y = await top(sel);
    if (y == null) continue;
    await glide(Math.max(0, y - above), ms);
    await page.waitForTimeout(hold);
  }

  result = await page.evaluate(() => ({
    verdict: document.getElementById("stamp").textContent.trim(),
    claim_as_checked: document.getElementById("claim-echo").textContent.trim(),
    tldr: document.getElementById("tldr").textContent.trim(),
    studies: document.querySelectorAll("li.study").length,
    relied_on: document.querySelectorAll("li.study.cited").length,
    link: location.href,
  }));

  // 5. End card
  await card(`<p style="margin:0;font:600 46px/1.05 var(--display);letter-spacing:-.03em">Check any health claim.</p>
    <p style="margin:0;font:500 30px/1.2 var(--display);color:var(--sage)">checkevident.com</p>
    <p style="margin:6px 0 0;font:400 18px/1.45 var(--body);color:var(--on-deep-2)">Free. No account. Real studies.<br>Not medical advice.</p>`);
  await page.waitForTimeout(2300);
  mark("end");
  await cdp.send("Page.stopScreencast");
} catch (e) {
  await browser.close();
  console.log(JSON.stringify({ ok: false, claim: CLAIM, error: String(e.message || e).slice(0, 300) }));
  process.exit(1);
}

// The verdict frame, for a cover image
const cover = path.join(OUT, `${slug}-cover.jpg`);
await page.evaluate(() => {
  document.getElementById("promo-card")?.remove();
  const e = document.getElementById("claim-echo");
  scrollTo(0, e ? e.getBoundingClientRect().top + scrollY - 70 : 0);
});
await page.waitForTimeout(400);
await page.screenshot({ path: cover, type: "jpeg", quality: 92 });
await browser.close();

// Turn the frames into a timeline: each frame lasts until the next one,
// and the wait for the check is squeezed to CHECK_ON_SCREEN seconds.
const waited = marks.verdict - marks.check;
const squeeze = Math.max(1, waited / CHECK_ON_SCREEN);
// Frames that arrive after the end card (the page being torn down) are dropped.
while (frames.length > 1 && frames[frames.length - 1].t > marks.end) frames.pop();
const lines = ["ffconcat version 1.0"];
for (let i = 0; i < frames.length; i++) {
  const t = frames[i].t, next = i + 1 < frames.length ? frames[i + 1].t : marks.end;
  let d = Math.max(0, next - t);
  if (t >= marks.check && t < marks.verdict) d /= squeeze;
  if (d <= 0) continue;
  lines.push(`file '${path.basename(frames[i].file)}'`, `duration ${d.toFixed(4)}`);
}
lines.push(`file '${path.basename(frames[frames.length - 1].file)}'`);
const list = path.join(FRAMES, "list.ffconcat");
fs.writeFileSync(list, lines.join("\n") + "\n");

const video = path.join(OUT, `${slug}.mp4`);
execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", list,
  "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
  "-vf", "scale=1080:1920:flags=lanczos,fps=30,format=yuv420p",
  "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high",
  "-c:a", "aac", "-shortest", "-movflags", "+faststart", video]);
fs.rmSync(FRAMES, { recursive: true, force: true });

const seconds = Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration",
  "-of", "csv=p=0", video]).toString().trim());
console.log(JSON.stringify({ ok: true, claim: CLAIM, hook: HOOK, video, cover,
  seconds: Math.round(seconds * 10) / 10, check_took: Math.round(waited * 10) / 10, ...result }));
