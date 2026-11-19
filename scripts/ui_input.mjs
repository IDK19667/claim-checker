#!/usr/bin/env node
/**
 * Input fixes (queue 7, step 5): the claim field's error, its type-ahead
 * list, and its border. Needs the fixture server:
 *   PORT=5050 .venv/bin/python scripts/ui_fixture_server.py [cache-copy.db]
 *
 *   node scripts/ui_input.mjs [baseUrl]
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const browser = await chromium.launch();
const results = [];

async function test(name, fn) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  try {
    await page.goto(base + "/");
    await page.waitForLoadState("load");
    await fn(page);
    if (errors.length) throw new Error("page errors: " + errors.join(" | "));
    results.push({ name, ok: true });
    console.log(`PASS ${name}`);
  } catch (e) {
    results.push({ name, ok: false });
    console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}
function must(cond, msg) { if (!cond) throw new Error(msg); }

// ---- 1. the validation error --------------------------------------------------

await test("error: empty submit shows it, linked and invalid; typing clears all three", async (page) => {
  const input = page.locator("#claim-input");
  must((await input.getAttribute("aria-invalid")) === null, "aria-invalid set before any error");
  await page.click("#submit-btn");
  const err = page.locator("#claim-error");
  must(await err.isVisible(), "no error shown for an empty claim");
  must(/Type or paste the claim/.test(await err.textContent()), "error text changed");
  must((await input.getAttribute("aria-invalid")) === "true", "aria-invalid is not true while the error shows");
  must((await input.getAttribute("aria-describedby")).split(" ").includes("claim-error"), "error is not in aria-describedby");
  must(await page.evaluate(() => document.activeElement?.id === "claim-input"), "focus did not go to the field");
  await page.keyboard.type("Creatine", { delay: 20 });
  must(!(await err.isVisible()), "error stayed after the text became valid");
  must((await input.getAttribute("aria-invalid")) === null, "aria-invalid stayed");
  must(!(await input.getAttribute("aria-describedby")).includes("claim-error"), "describedby still names the error");
  // Whitespace alone is still invalid and does not clear it.
  await input.fill("");
  await page.click("#submit-btn");
  must(await err.isVisible(), "error did not come back");
  await page.keyboard.type("   ");
  must(await err.isVisible(), "spaces alone cleared the error");
});

// ---- 2. grouped near-duplicates, labelled list ----------------------------------

const typeAhead = async (page, text) => {
  await page.click("#claim-input");
  await page.keyboard.type(text, { delay: 30 });
  await page.waitForSelector("#suggest:not([hidden]) .suggest-row", { timeout: 5000 });
};

await test("suggestions: near-duplicate wordings are one row, the other is named, nothing else is lost", async (page) => {
  await typeAhead(page, "creatine");
  const rows = await page.locator(".suggest-row .suggest-claim").evaluateAll((els) => els.map((e) => e.textContent));
  const builds = rows.filter((r) => /Creatine builds strength|Creatine improves strength/.test(r));
  must(builds.length === 1, `expected one row for the two strength claims, got ${JSON.stringify(rows)}`);
  must(/Also checked as: Creatine (builds|improves) strength/.test(builds[0]) && !/^(.*)Also checked as: \1$/.test(builds[0]), "the folded wording is not named: " + builds[0]);
  must(rows.some((r) => /not safe for the kidneys/.test(r)), "a negated claim was folded away: " + JSON.stringify(rows));
  // Keyboard still walks the rows of the grouped list.
  await page.keyboard.press("ArrowDown");
  must((await page.getAttribute("#suggest-0", "aria-selected")) === "true", "ArrowDown did not select the first grouped row");
});

await test("suggestions: the list is labelled 'Previously checked claims', visibly and by name", async (page) => {
  await typeAhead(page, "creatine");
  const head = (await page.locator("#suggest-head").textContent()).trim();
  must(head === "Previously checked claims", `visible heading is "${head}"`);
  must(await page.locator("#suggest-head").isVisible(), "heading is not visible");
  must((await page.getAttribute("#suggest-list", "aria-labelledby")) === "suggest-head", "listbox is not named by the heading");
  must((await page.locator("#suggest-list").evaluate((e) => e.getAttribute("aria-label"))) === null, "a second, conflicting aria-label is set");
});

// ---- 3. what a pick does ----------------------------------------------------------
// Today a pick runs the check for that exact claim, and a claim in the list is
// by definition in the cache, so it opens the saved answer. The list says so.

await test("suggestions: the list says a pick opens the saved answer, and a pick does exactly that", async (page) => {
  await typeAhead(page, "antibio");
  const note = (await page.locator("#suggest-note").textContent()).trim();
  must(note === "Choose one to open its saved answer.", `hint is "${note}"`);
  must(await page.locator("#suggest-note").isVisible(), "hint is not visible");
  must((await page.getAttribute("#suggest-list", "aria-describedby")) === "suggest-note", "the list does not point at the hint");
  const bodies = [];
  page.on("response", async (r) => { if (r.url().includes("/api/check/stream")) bodies.push(await r.text().catch(() => "")); });
  await page.locator(".suggest-row").first().click();
  await page.waitForSelector("#stamp", { state: "visible", timeout: 10000 });
  await page.waitForTimeout(300);
  must(bodies.length === 1, `expected one check request, saw ${bodies.length}`);
  must(/"cached":\s*true/.test(bodies[0]), "the pick did not come from the saved answer");
});

// ---- 4. the field's border on the dark ground ------------------------------------

await test("field border: 3:1 or better against the night ground and the field fill, at both widths", async (page) => {
  for (const w of [1440, 390]) {
    await page.setViewportSize({ width: w, height: 900 });
    const m = await page.evaluate(() => {
      const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number);
      const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
      const ratio = (a, b) => { const [x, y] = [lum(rgb(a)), lum(rgb(b))].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
      const box = document.querySelector(".claim-box");
      const cs = getComputedStyle(box);
      let ground = box.parentElement;
      while (ground && /rgba\(0, 0, 0, 0\)|transparent/.test(getComputedStyle(ground).backgroundColor)) ground = ground.parentElement;
      return { width: cs.borderTopWidth, style: cs.borderTopStyle, vsGround: ratio(cs.borderTopColor, getComputedStyle(ground).backgroundColor), vsFill: ratio(cs.borderTopColor, cs.backgroundColor) };
    });
    must(m.style === "solid" && parseFloat(m.width) >= 1, `no visible border at ${w}: ${JSON.stringify(m)}`);
    must(m.vsGround >= 3 && m.vsFill >= 3, `border contrast at ${w}: ${JSON.stringify(m)}`);
  }
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
