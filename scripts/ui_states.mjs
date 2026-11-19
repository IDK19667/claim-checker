#!/usr/bin/env node
/**
 * Loading, failure and dialog states, in a real browser, against the fixture
 * server (scripts/ui_fixture_server.py). Exits non-zero if any check fails.
 *
 *   node scripts/ui_states.mjs [baseUrl]
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = (p) => fetch(base + p).then((r) => r.text());
const browser = await chromium.launch();
const results = [];
function must(c, m) { if (!c) throw new Error(m); }

async function test(name, fn, { w = 390, h = 844 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  try {
    await get("/__fixture/reset");
    await page.goto(base + "/"); await page.waitForLoadState("load");
    await fn(page);
    must(!errors.length, "page errors: " + errors.join(" | "));
    results.push(true); console.log(`PASS ${name}`);
  } catch (e) {
    results.push(false); console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally {
    for (const g of ["query", "found", "done"]) await get(`/__fixture/release/${g}`).catch(() => {});
    await ctx.close();
  }
}
const send = async (page, claim) => { await page.fill("#claim-input", claim); await page.press("#claim-input", "Enter"); };
const errorScreen = (page) => page.evaluate(() => ({
  title: document.getElementById("error-title").textContent, msg: document.getElementById("error-msg").textContent,
  retry: !document.getElementById("error-retry").hidden, back: document.getElementById("error-back").textContent,
  draft: document.getElementById("claim-input").value,
  shown: (() => { const el = document.getElementById("error-claim"); const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? el.textContent : ""; })(),
}));
const top = (page, sel) => page.evaluate((s) => Math.round(document.querySelector(s).getBoundingClientRect().top + scrollY), sel);

for (const [w, h] of [[390, 844], [1440, 900]]) {
  await test(`loading at ${w}: no banner, and the claim does not move when the verdict arrives`, async (page) => {
    await send(page, "Gated claim for stage test");
    await page.waitForSelector("#claim-echo", { state: "visible" });
    await sleep(300);
    must(await page.evaluate(() => !document.getElementById("film")), "a banner element is on the page");
    const before = await top(page, "#claim-echo");
    must(before < 260, `the claim starts ${before}px down: a banner or a tall block is above it`);
    for (const g of ["query", "found", "done"]) { await get(`/__fixture/release/${g}`); await sleep(250); }
    await page.waitForSelector("#stamp", { state: "visible" });
    await sleep(1500);
    await page.evaluate(() => scrollTo(0, 0));
    must((await top(page, "#claim-echo")) === before, "the claim moved when the verdict arrived");
  }, { w, h });
}

await test("invalid input: its own title, the server's words, Edit claim and no Retry, draft kept", async (page) => {
  await page.evaluate(() => document.getElementById("claim-input").removeAttribute("maxlength"));
  await send(page, "x".repeat(520));
  await page.waitForSelector("#error:not([hidden])");
  const e = await errorScreen(page);
  must(e.title === "That claim can't be checked" && /Trim the claim/.test(e.msg), JSON.stringify(e));
  must(!e.retry && e.back === "Edit claim" && e.draft.length === 520 && e.shown.length > 0 && e.shown.startsWith("xxx"), JSON.stringify(e));
});
await test("network failure: its own title, Retry offered, draft kept", async (page) => {
  await page.route("**/api/check**", (r) => r.abort());
  await send(page, "Network failure claim");
  await page.waitForSelector("#error:not([hidden])");
  const e = await errorScreen(page);
  must(e.title === "Couldn't reach the server" && /connection/.test(e.msg) && e.retry && e.draft === "Network failure claim" && e.shown === "Network failure claim", JSON.stringify(e));
});
await test("rate limit: its own title, the server's words, a countdown, draft kept", async (page) => {
  await send(page, "Provider busy claim");
  await page.waitForSelector("#error:not([hidden])");
  const e = await errorScreen(page);
  must(e.title === "Too many checks right now" && /busy/.test(e.msg) && e.retry && e.draft === "Provider busy claim" && e.shown === "Provider busy claim", JSON.stringify(e));
  must(await page.locator("#countdown").isVisible(), "no countdown");
});
await test("provider failure and PubMed failure: the checker's own title, the server's words, draft kept", async (page) => {
  await send(page, "Provider down claim");
  await page.waitForSelector("#error:not([hidden])");
  let e = await errorScreen(page);
  must(e.title === "The checker couldn't finish this check" && /AI service/.test(e.msg) && e.retry && e.shown === "Provider down claim", JSON.stringify(e));
  await page.click("#error-back");
  await send(page, "PubMed down claim");
  await page.waitForFunction(() => /PubMed/.test(document.getElementById("error-msg").textContent));
  e = await errorScreen(page);
  must(e.title === "The checker couldn't finish this check" && e.draft === "PubMed down claim" && e.shown === "PubMed down claim", JSON.stringify(e));
});
await test("no relevant evidence: an answer, not an error, that says what to try", async (page) => {
  await send(page, "No evidence claim");
  await page.waitForSelector("#stamp", { state: "visible", timeout: 15000 });
  await page.waitForFunction(() => document.querySelector("#reading")?.hidden);
  must(await page.locator("#no-evidence").isVisible(), "no 'nothing matched' note");
  must(!(await page.locator("#error").isVisible()), "shown as an error");
});

await test("source dialog: Close stays at the top while the dialog scrolls, and is the only Close", async (page) => {
  await send(page, "Antibiotics cure the common cold");
  await page.waitForSelector("#stamp", { state: "visible" });
  await page.waitForFunction(() => document.querySelector("#reading")?.hidden);
  const go = page.locator("#sources .study-go").first();
  await go.scrollIntoViewIfNeeded(); await go.focus(); await page.keyboard.press("Enter");
  await page.waitForSelector("dialog#study-sheet[open]");
  must(await page.evaluate(() => document.getElementById("study-sheet").contains(document.activeElement)), "focus did not move into the dialog");
  await page.evaluate(() => { document.getElementById("study-sheet").scrollTop = 600; });
  await sleep(200);
  const r = await page.evaluate(() => {
    const d = document.getElementById("study-sheet"), c = d.querySelector(".sheet-close").getBoundingClientRect();
    return { scrolled: d.scrollTop, gap: Math.round(c.top - d.getBoundingClientRect().top), h: Math.round(c.height), w: Math.round(c.width),
      hit: document.elementFromPoint(c.left + c.width / 2, c.top + c.height / 2).className, closes: d.querySelectorAll("[data-close]").length };
  });
  must(r.scrolled > 100 && r.gap < 20 && r.h >= 44 && r.w >= 44 && r.hit === "sheet-close" && r.closes === 1, JSON.stringify(r));
  await page.click(".sheet-close"); await sleep(200);
  must(await page.evaluate(() => /study-go/.test(document.activeElement.className)), "focus did not return to the opener");
});

await test("share bar: not on first view, appears after the short answer, goes when scrolled back", async (page) => {
  await send(page, "Antibiotics cure the common cold");
  await page.waitForSelector("#stamp", { state: "visible" });
  await page.waitForFunction(() => document.querySelector("#reading")?.hidden);
  await page.evaluate(() => scrollTo(0, 0)); await sleep(300);
  const shown = () => page.locator("#bar-sticky").isVisible();
  must(!(await shown()), "the share bar covers the answer on first view");
  await page.evaluate(() => { const a = document.querySelector("#result .answer"); scrollTo(0, a.getBoundingClientRect().bottom + scrollY + 40); });
  await sleep(300);
  must(await shown(), "the share bar did not appear after the summary");
  const tall = await page.evaluate(() => document.getElementById("share-btn-sticky").getBoundingClientRect().height);
  must(tall >= 44, `share button is ${tall}px tall`);
  await page.evaluate(() => scrollTo(0, 0)); await sleep(300);
  must(!(await shown()), "the share bar stayed after scrolling back to the answer");
});

await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
