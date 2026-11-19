#!/usr/bin/env node
/**
 * The result page in a real browser: what the unit tests cannot see.
 *
 *   node scripts/ui_result.mjs [baseUrl]
 *
 * Needs the fixture server (no key, no network):
 *   PORT=5050 .venv/bin/python scripts/ui_fixture_server.py [cache-copy.db]
 * Exits non-zero if any check fails.
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const CLAIM = "Antibiotics cure the common cold";
const url = `${base}/?q=${encodeURIComponent(CLAIM)}`;
const browser = await chromium.launch();
let failed = 0;

async function test(name, w, h, fn) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  try {
    await page.goto(url);
    await page.waitForLoadState("load");
    await page.evaluate(() => document.fonts && document.fonts.ready);
    await fn(page);
    if (errors.length) throw new Error("page errors: " + errors.join(" | "));
    console.log(`PASS ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n     ${String(e.message || e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}
const must = (c, m) => { if (!c) throw new Error(m); };
const top = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); return e ? e.getBoundingClientRect().top + scrollY : null; }, sel);

await test("edit claim returns to the claim box with the claim filled", 1280, 900, async (page) => {
  await page.click("#edit-claim");
  await page.waitForSelector("#ask:not([hidden])");
  const v = await page.inputValue("#claim-input");
  must(v === CLAIM, `field holds "${v}"`);
  must(await page.evaluate(() => document.activeElement?.id === "claim-input"), "claim box is not focused");
  const caret = await page.evaluate(() => document.getElementById("claim-input").selectionStart);
  must(caret === CLAIM.length, `cursor at ${caret}, not at the end`);
  must(!(await page.locator("#suggest").isVisible()), "suggestions opened on edit");
  must(!(await page.locator("#result").isVisible()), "result still showing");
});

await browser.close();
console.log(failed ? `\n${failed} failed` : "\nall passed");
process.exit(failed ? 1 : 0);
