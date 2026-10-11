// Records one Reels-sized video (1080x1920 MP4, about 16 seconds) of a real
// check on the live site. Built on what makes a Reel travel: a hook and an
// open question in the first two seconds ("True or false? Guess first."),
// something new on screen every second or two (the check, a punch-in on the
// verdict, the shelf, the answer in big type), the question closed at the
// end, and an ask to send it to someone, since sends reach new people most.
// It has its own sound (promo/sound.mjs).
// Not part of the site: marketing tooling, run by hand or by the daily task.
//
//   node promo/record.mjs --claim "Mouth taping improves your sleep" \
//        --said "TikTok says" --hook "Tape your mouth shut. Sleep better." \
//        --send "Send this to someone who tapes their mouth." [--out dir]
//
// Prints one JSON line: the files written and what the site answered, so
// captions can be written to match the verdict exactly.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { soundtrack } from "./sound.mjs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) =>
  a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc, []));
const CLAIM = (args.claim || "").trim();
if (!CLAIM) { console.error("usage: --claim \"...\" [--hook \"...\"] [--out dir]"); process.exit(2); }
const HOOK = (args.hook || CLAIM).trim();
const SAID = (args.said || "Everyone says").trim();
const SEND = (args.send || "Send this to someone who believes it.").trim();
const BASE = args.base || "https://checkevident.com";
const today = new Date().toLocaleDateString("en-CA");
const OUT = path.resolve(args.out || `promo/out/${today}`);
const slug = CLAIM.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
const FRAMES = path.join(OUT, `.frames-${slug}`);
fs.rmSync(FRAMES, { recursive: true, force: true });
fs.mkdirSync(FRAMES, { recursive: true });

// The check itself takes 5 to 20 seconds; on screen it gets this long.
const CHECK_ON_SCREEN = 2;

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
// Sound cues, on the same clock; placed on the video's timeline at the end.
const cues = [];
const cue = (type, extra = {}) => cues.push({ type, t: Date.now() / 1000, ...extra });
cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
  const file = path.join(FRAMES, `${String(frames.length).padStart(5, "0")}.jpg`);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  // Arrival time, on the same clock as the marks below.
  frames.push({ file, t: Date.now() / 1000 });
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});

// Everything drawn over the page below exists only in this recording, in
// the site's own fonts and colours; the site is never changed.
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// Words that pop in one after another when show() runs.
const words = (text, cls = "") => text.split(/\s+/).filter(Boolean).map((w) =>
  `<span class="pw ${cls}" style="display:inline-block;opacity:0;transform:translateY(.3em);`
  + `transition:opacity .16s ease,transform .22s cubic-bezier(.2,.8,.2,1)">${esc(w)}</span>`).join(" ");
const label = (color = "var(--on-deep-2)") =>
  `font:500 15px/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:${color};margin:0`;
const STEP = 75; // ms between popping words

// A full-screen night card. Words stay hidden until show().
async function card(html, { fade = true } = {}) {
  await page.evaluate(([html, fade]) => {
    let el = document.getElementById("promo-card");
    if (!el) {
      el = document.createElement("div");
      el.id = "promo-card";
      el.style.cssText = "position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;"
        + "justify-content:center;gap:20px;padding:0 30px;background:var(--deep);color:var(--on-deep);"
        + "transition:opacity .25s ease;opacity:" + (fade ? "0" : "1");
      document.body.appendChild(el);
    }
    el.innerHTML = html;
    requestAnimationFrame(() => { el.style.opacity = "1"; });
  }, [html, fade]);
  if (fade) { cue("whoosh"); await page.waitForTimeout(260); }
}
async function uncard() {
  cue("whoosh", { amp: 0.08 });
  await page.evaluate(() => { const el = document.getElementById("promo-card"); if (el) el.style.opacity = "0"; });
  await page.waitForTimeout(260);
  await page.evaluate(() => document.getElementById("promo-card")?.remove());
}
// A caption box near the top, over the live site.
async function caption(text) {
  await page.evaluate((html) => {
    let el = document.getElementById("promo-cap");
    if (!el) {
      el = document.createElement("div");
      el.id = "promo-cap";
      el.style.cssText = "position:fixed;top:22px;left:18px;right:18px;z-index:2147483646;padding:14px 18px 16px;"
        + "background:var(--paper);color:var(--ink);border-radius:8px;box-shadow:0 10px 30px var(--shadow);"
        + "font:650 30px/1.08 var(--display);letter-spacing:-.025em;text-align:center";
      document.body.appendChild(el);
    }
    el.innerHTML = html;
  }, words(text));
  return show();
}
const uncaption = () => page.evaluate(() => document.getElementById("promo-cap")?.remove());
// Pop in every hidden word on screen; resolves when the last one is in.
async function show(sel = ".pw") {
  const t = Date.now() / 1000;
  const n = await page.evaluate(([sel, step]) => {
    const ws = [...document.querySelectorAll(sel)].filter((w) => w.style.opacity === "0");
    ws.forEach((w, i) => setTimeout(() => { w.style.opacity = "1"; w.style.transform = "none"; }, i * step));
    return ws.length;
  }, [sel, STEP]);
  cues.push({ type: "pops", t, n });
  await page.waitForTimeout(n * STEP + 220);
  return n;
}

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
  return e && e.offsetParent ? Math.round(e.getBoundingClientRect().top + scrollY) : null;
}, sel);

// The short answer, broken where it turns ("..., but ...") so each part lands.
function beats(text) {
  const parts = text.split(/,?\s+(?=(?:but|yet|although|though|however|while)\b)/i);
  return parts.length > 1 ? [parts[0].replace(/,$/, ""), parts.slice(1).join(" ")] : [text];
}

const hookHtml = `<p style="${label("var(--sage)")}">${esc(SAID)}</p>
  <p style="margin:0;font:700 54px/1.0 var(--display);letter-spacing:-.04em">${words(HOOK)}</p>
  <p style="margin:10px 0 0;font:650 30px/1.1 var(--display);letter-spacing:-.02em;color:var(--sage)">${words("True or false? Guess first.", "guess")}</p>`;

let result = {};
try {
  await page.goto(`${BASE}/?focus=1`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  // The caption box takes the place of the site's own heading on screen.
  await page.addStyleTag({ content: "html,body{overflow-x:hidden} #ask-heading{visibility:hidden}" });

  // 1. What people say. The card is up before recording starts and the page
  // behind it waits at the claim box, so the first frame is the hook.
  await card(hookHtml, { fade: false });
  await page.evaluate(() => {
    document.activeElement?.blur();
    const ask = document.getElementById("ask");
    scrollTo(0, ask ? ask.getBoundingClientRect().top + scrollY - 8 : 0);
  });
  await page.waitForTimeout(500);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1080, maxHeight: 1920, everyNthFrame: 1 });
  mark("start");
  await show(".pw:not(.guess)");
  await page.waitForTimeout(250);
  await show(".guess");
  await page.waitForTimeout(800);
  await uncard();

  // 2. "So I checked", and the claim goes in
  await caption("So we checked the research.");
  const box = page.locator("#claim-input");
  await box.click();
  const typed = Date.now() / 1000;
  await box.pressSequentially(CLAIM, { delay: 18 });
  cues.push({ type: "typing", t: typed, until: Date.now() / 1000, n: CLAIM.length });
  await page.waitForTimeout(200);
  mark("check");
  await box.press("Enter");

  // 3. The check runs; shown sped up
  await page.waitForFunction(() => {
    const r = document.getElementById("result"), s = document.getElementById("stamp");
    return r && !r.hidden && !r.classList.contains("checking") && s && s.textContent.trim();
  }, null, { timeout: 120000, polling: 200 });
  mark("verdict");
  await uncaption();

  result = await page.evaluate(() => ({
    verdict: document.getElementById("stamp").textContent.trim(),
    claim_as_checked: document.getElementById("claim-echo").textContent.trim(),
    tldr: document.getElementById("tldr").textContent.trim(),
    studies: document.querySelectorAll("li.study").length,
    relied_on: document.querySelectorAll("li.study.cited").length,
    link: location.href,
  }));

  // 4. Punch in on the verdict, and close the question from the start
  await page.waitForTimeout(350);
  cue("stamp");
  await page.evaluate(() => {
    const r = document.getElementById("result"), s = document.getElementById("stamp").getBoundingClientRect();
    const b = r.getBoundingClientRect();
    // Grow from the stamp's left edge, so the verdict stays whole on screen.
    r.style.transformOrigin = `${s.left - b.left}px ${s.top + s.height / 2 - b.top}px`;
    r.style.transition = "transform .55s cubic-bezier(.2,.8,.2,1)";
    r.style.transform = "scale(1.18)";
  });
  await page.waitForTimeout(450);
  await caption("Did you guess right?");
  await page.waitForTimeout(700);
  await uncaption();
  await page.evaluate(() => { document.getElementById("result").style.transform = "none"; });
  await page.waitForTimeout(350);

  // 5. The shelf: what it rests on
  const shelf = await top("#chart-sec");
  if (shelf != null) {
    cue("whoosh", { dur: 0.6, amp: 0.07 });
    await glide(Math.max(0, shelf - 150), 700);
    await caption(`${result.studies} real ${result.studies === 1 ? "study" : "studies"} from PubMed.`);
    await page.waitForTimeout(1100);
    await uncaption();
  }

  // 6. The answer, big
  const parts = beats(result.tldr);
  await card(`<p style="${label()}">What the studies say</p>`
    + parts.map((t, i) => `<p style="margin:0;font:650 38px/1.08 var(--display);letter-spacing:-.03em;`
      + `color:${i ? "var(--sage)" : "var(--on-deep)"}">${words(t)}</p>`).join(""));
  const n = await show();
  await page.waitForTimeout(Math.min(2400, Math.max(1300, n * 120)));

  // 7. The ask
  await card(`<p style="margin:0;font:700 44px/1.02 var(--display);letter-spacing:-.04em">${words(SEND)}</p>
    <p style="margin:0;font:650 28px/1.12 var(--display);letter-spacing:-.02em;color:var(--sage)">${words("Comment a claim. We check it next.")}</p>
    <p style="margin:18px 0 0;font:500 26px/1.2 var(--display)">checkevident.com</p>
    <p style="margin:0;font:400 17px/1.45 var(--body);color:var(--on-deep-2)">Free. Real studies. Not medical advice.</p>`);
  cue("chime");
  await show();
  await page.waitForTimeout(1700);
  mark("end");
  await cdp.send("Page.stopScreencast");
} catch (e) {
  await browser.close();
  console.log(JSON.stringify({ ok: false, claim: CLAIM, error: String(e.message || e).slice(0, 300) }));
  process.exit(1);
}

// The cover is the hook, fully shown: it makes people stop without giving
// the answer away.
const cover = path.join(OUT, `${slug}-cover.jpg`);
await card(hookHtml.replace(/opacity:0;transform:translateY\(\.3em\)/g, "opacity:1"), { fade: false });
await page.waitForTimeout(200);
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

// Place each sound on the finished video's clock: real time from the first
// frame, with the wait for the check squeezed like the picture.
const t0 = frames[0].t;
const toOut = (t) => t < marks.check ? t - t0
  : t < marks.verdict ? (marks.check - t0) + (t - marks.check) / squeeze
  : (marks.check - t0) + (marks.verdict - marks.check) / squeeze + (t - marks.verdict);
const LAG = 0.04; // the screencast shows a change a frame or two after it happens
const placed = [{ type: "riser", at: toOut(marks.check) + LAG, dur: toOut(marks.verdict) - toOut(marks.check) }];
for (const c of cues) {
  const at = toOut(c.t) + LAG;
  if (c.type === "pops") for (let i = 0; i < c.n; i++) placed.push({ type: "pop", at: at + i * STEP / 1000, k: i });
  else if (c.type === "typing") {
    const span = toOut(c.until) - toOut(c.t);
    for (let i = 0; i < c.n; i++) placed.push({ type: "click", at: at + (i + 0.5) * span / c.n });
  } else placed.push({ ...c, at });
}
const wav = path.join(FRAMES, "sound.wav");
soundtrack(placed, toOut(marks.end) + 0.5, wav);

const video = path.join(OUT, `${slug}.mp4`);
execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", list, "-i", wav,
  "-vf", "scale=1080:1920:flags=lanczos,fps=30,format=yuv420p",
  "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high",
  "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", video]);
fs.rmSync(FRAMES, { recursive: true, force: true });

const seconds = Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration",
  "-of", "csv=p=0", video]).toString().trim());
console.log(JSON.stringify({ ok: true, claim: CLAIM, said: SAID, hook: HOOK, send: SEND, video, cover,
  seconds: Math.round(seconds * 10) / 10, check_took: Math.round(waited * 10) / 10, ...result }));
