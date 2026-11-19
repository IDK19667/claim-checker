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

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
