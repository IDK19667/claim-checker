#!/usr/bin/env node
/**
 * "Works, keep it" tests: behaviours of the page that a UI rebuild must not
 * lose. They describe what main does today, in a real browser, and exit
 * non-zero if any fails.
 *
 *   node scripts/ui_keep.mjs [baseUrl]
 *
 * Needs the fixture server (no key, no network, stages that wait on gates):
 *   PORT=5050 .venv/bin/python scripts/ui_fixture_server.py [cache-copy.db]
 *
 * Selectors are the ids the page already has. A rebuild that renames one
 * should rename it here on purpose, in the same change.
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const GATED = "Gated claim for stage test";
const CACHED = "Antibiotics cure the common cold";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = (path) => fetch(base + path).then((r) => r.text());

const browser = await chromium.launch();
const results = [];

async function newPage() {
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    reducedMotion: "reduce",
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base + "/");
  await page.waitForLoadState("load");
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.locator("#claim-input").scrollIntoViewIfNeeded();
  return { ctx, page, errors };
}

async function test(name, fn) {
  const env = await newPage();
  try {
    await get("/__fixture/reset");
    await fn(env.page, env);
    if (env.errors.length) throw new Error("page errors: " + env.errors.join(" | "));
    results.push({ name, ok: true });
    console.log(`PASS ${name}`);
  } catch (e) {
    results.push({ name, ok: false });
    console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally {
    for (const g of ["query", "found", "done"]) await get(`/__fixture/release/${g}`).catch(() => {});
    await env.ctx.close();
  }
}

function must(cond, msg) { if (!cond) throw new Error(msg); }
const visible = (page, sel) => page.locator(sel).isVisible();
const logText = (page) => page.evaluate(() => [...document.querySelectorAll("#reading li")].map((li) => li.textContent.trim()).filter((t) => t && t !== "Cancel"));

async function runCheck(page, claim) {
  await page.fill("#claim-input", claim);
  await page.click("#submit-btn");
  await page.waitForSelector("#stamp", { state: "visible", timeout: 20000 });
  await page.waitForFunction(() => document.querySelector("#reading")?.hidden, null, { timeout: 20000 });
}

// ---- type-ahead --------------------------------------------------------------

await test("type-ahead: arrow keys walk the suggestions and Enter picks one", async (page) => {
  await page.click("#claim-input");
  await page.keyboard.type("the", { delay: 40 });
  await page.waitForSelector("#suggest:not([hidden]) .suggest-row", { timeout: 5000 });
  const n = await page.locator(".suggest-row").count();
  must(n >= 2, `expected 2+ suggestions, got ${n}`);
  must((await page.getAttribute("#claim-input", "aria-controls")) === "suggest-list" && !(await page.getAttribute("#claim-input", "role")), "input must stay a plain textbox that controls the list (no role=combobox on a textarea)");
  await page.keyboard.press("ArrowDown");
  must((await page.getAttribute("#suggest-0", "aria-selected")) === "true", "first ArrowDown did not select row 0");
  must((await page.getAttribute("#claim-input", "aria-activedescendant")) === "suggest-0", "aria-activedescendant not set");
  await page.keyboard.press("ArrowDown");
  must((await page.getAttribute("#suggest-1", "aria-selected")) === "true", "second ArrowDown did not select row 1");
  must((await page.getAttribute("#suggest-0", "aria-selected")) === "false", "row 0 stayed selected");
  await page.keyboard.press("ArrowUp");
  must((await page.getAttribute("#suggest-0", "aria-selected")) === "true", "ArrowUp did not go back to row 0");
  await page.keyboard.press("ArrowDown");
  const picked = await page.locator("#suggest-1 .suggest-claim").textContent();
  await page.keyboard.press("Enter");
  await page.waitForSelector("#stamp", { state: "visible", timeout: 20000 });
  const echo = await page.locator("#claim-echo").textContent();
  must(echo.includes(picked.trim()), `Enter did not run the picked claim: wanted "${picked}", page shows "${echo}"`);
  must(!(await visible(page, "#suggest")), "suggestions stayed open after picking");
});

await test("type-ahead: Escape closes the suggestions and keeps what was typed", async (page) => {
  await page.click("#claim-input");
  await page.keyboard.type("antibio", { delay: 40 });
  await page.waitForSelector("#suggest:not([hidden]) .suggest-row", { timeout: 5000 });
  await page.keyboard.press("Escape");
  must(!(await visible(page, "#suggest")), "Escape left the suggestions open");
  must((await page.inputValue("#claim-input")) === "antibio", "Escape cleared the draft");
  must(!(await page.getAttribute("#claim-input", "aria-activedescendant")), "aria-activedescendant left set after Escape");
});

// ---- dialogs -----------------------------------------------------------------

await test("how-it-works dialog: Escape closes it and focus returns to its button", async (page) => {
  await page.locator("#how-btn").focus();
  await page.keyboard.press("Enter");
  await page.waitForSelector("dialog#how-sheet[open]");
  await page.keyboard.press("Escape");
  await sleep(200);
  must(!(await page.evaluate(() => document.getElementById("how-sheet").open)), "Escape did not close the dialog");
  must(await page.evaluate(() => document.activeElement?.id === "how-btn"), "focus did not return to How it works");
});

await test("study dialog: Escape and Close both close it, focus returns to the study's button", async (page) => {
  await runCheck(page, CACHED);
  for (const how of ["Escape", "Close"]) {
    const go = page.locator("#sources .study-go").first();
    await go.scrollIntoViewIfNeeded();
    await go.focus();
    await page.keyboard.press("Enter");
    await page.waitForSelector("dialog#study-sheet[open]", { timeout: 5000 });
    if (how === "Escape") await page.keyboard.press("Escape");
    else await page.click("dialog#study-sheet [data-close]");
    await sleep(200);
    must(!(await page.evaluate(() => document.getElementById("study-sheet").open)), `${how} did not close the study dialog`);
    const back = await page.evaluate(() => document.activeElement?.className || document.activeElement?.tagName);
    must(/study-go/.test(back), `after ${how}, focus is on "${back}", not the Deep dive button that opened it`);
  }
});

// ---- link copied -------------------------------------------------------------

await test('"Link copied" appears after Copy link, and the link is on the clipboard', async (page) => {
  await runCheck(page, CACHED);
  await page.click("#copy-btn");
  await page.waitForFunction(() => document.getElementById("toast").textContent === "Link copied", null, { timeout: 3000 });
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  must(/\/\?q=/.test(clip) || clip.includes("Antibiotics"), `clipboard holds "${clip}"`);
  await sleep(2800);
  must((await page.locator("#toast").textContent()) === "", "toast did not clear itself");
});

// ---- report a wrong answer ---------------------------------------------------

await test("report a wrong answer: it opens on a focused note and Cancel closes it without sending", async (page) => {
  await runCheck(page, CACHED);
  const sent = [];
  page.on("request", (r) => { if (r.url().includes("/api/feedback")) sent.push(r.url()); });
  await page.locator("#report-open").scrollIntoViewIfNeeded();
  await page.click("#report-open");
  must(await visible(page, "#report-form"), "report form did not open");
  must(await page.evaluate(() => document.activeElement?.id === "report-note"), "note field not focused on open");
  await page.fill("#report-note", "this is wrong");
  await page.click("#report-cancel");
  must(!(await visible(page, "#report-form")), "Cancel left the form open");
  must(await visible(page, "#report-open"), "the Report button did not come back");
  must(await page.evaluate(() => document.activeElement?.id === "report-open"), "focus did not return to the Report button");
  must((await page.inputValue("#report-note")) === "", "the cancelled note was kept");
  await sleep(300);
  must(sent.length === 0, "Cancel still sent a report");
});

// ---- loading stages are the stream's, not a timer ------------------------------

await test("loading stages follow the stream events in order, and wait when the server waits", async (page) => {
  await page.fill("#claim-input", GATED);
  await page.click("#submit-btn");
  await page.waitForFunction(() => /Building the search/.test(document.getElementById("reading")?.textContent || ""), null, { timeout: 5000 });
  await sleep(1800); // longer than any animation; a timer would have moved on
  let log = await logText(page);
  must(!log.some((l) => /Searching PubMed/.test(l)), "showed 'Searching PubMed' before the server sent the query stage");
  await get("/__fixture/release/query");
  await page.waitForFunction(() => /Searching PubMed/.test(document.getElementById("reading")?.textContent || ""), null, { timeout: 5000 });
  await sleep(1800);
  log = await logText(page);
  must(!log.some((l) => /^Found/.test(l)), "showed 'Found' before the server found anything");
  await get("/__fixture/release/found");
  await page.waitForFunction(() => /Weighing the evidence/.test(document.getElementById("reading")?.textContent || ""), null, { timeout: 5000 });
  await sleep(1800);
  log = await logText(page);
  const at = (re) => log.findIndex((l) => re.test(l));
  must(at(/Building the search/) < at(/Searching PubMed/) && at(/Searching PubMed/) < at(/^Found \d+ stud/) &&
       at(/^Found \d+ stud/) < at(/Reading the studies/) && at(/Reading the studies/) < at(/Weighing the evidence/) && at(/Building the search/) >= 0,
       "stage lines out of order: " + JSON.stringify(log));
  must(!(await visible(page, "#stamp")), "the verdict appeared before the server sent it");
  await get("/__fixture/release/done");
  await page.waitForSelector("#stamp", { state: "visible", timeout: 10000 });
  must((await page.locator("#stamp").textContent()).trim().length > 0, "empty verdict");
});

// ---- cancel ------------------------------------------------------------------

await test("Cancel during a check stops it and keeps the draft", async (page) => {
  await page.fill("#claim-input", GATED);
  await page.click("#submit-btn");
  await page.waitForSelector("#cancel-live", { state: "visible", timeout: 5000 });
  await page.click("#cancel-live");
  await page.waitForSelector("#ask:not([hidden])", { timeout: 5000 });
  must((await page.inputValue("#claim-input")) === GATED, "the draft was lost");
  must(!(await visible(page, "#result")), "the result screen stayed up");
  must(!(await visible(page, "#error")), "Cancel showed an error");
  must(await page.locator("#submit-btn").isEnabled(), "Check button stayed disabled");
  must((await page.locator("#btn-label, #submit-btn .btn-label").first().textContent()).trim() === "Check the research", "button label stuck on Checking");
  // And it can be run again.
  await get("/__fixture/release/query"); await get("/__fixture/release/found"); await get("/__fixture/release/done");
  await sleep(500);
  await page.click("#submit-btn");
  await page.waitForSelector("#stamp", { state: "visible", timeout: 15000 });
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
