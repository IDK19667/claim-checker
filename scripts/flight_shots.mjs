// Screenshots of /flight at real sizes, for looking at.
//
// The Browser pane scales a 1280 viewport down to fit, which is fine for
// checking that something works and useless for judging type and contrast.
// This drives a real Chromium at the exact sizes and writes PNGs.
//
//   node scripts/flight_shots.mjs
//
// The server must be running on :5000.

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = "http://localhost:5000";
const OUT = "/tmp/flight-shots";

// Fractions of the pinned TRAVEL (section height minus one viewport), one
// per stage. Using fractions of the section height instead lands these
// mid-transition, which is how the first round of these screenshots managed
// to show the lab footage with the opening form still on it.
// Fractions computed against the sum of beats.json vh values, which is
// exactly what flight.js lays proportionally across the pinned travel, so
// these land in the same beat every time regardless of viewport height.
// Recomputed for round 5's five-stage beats.json (claim/archive/weighing/
// write/publish, then the footage-free horizon hold; desktop vh sums to 601).
const STOPS = [
  ["1-claim", 0.046],    // claim-hold: the static entry screen, claim input usable
  ["2-move", 0.231],     // claim-move: text-free, the claim clip's own camera move
  ["3-search", 0.350],   // archive-text: the real query, before any study has arrived
  ["4-weigh", 0.538],    // weighing-text: studies stacking in, grading underway
  ["5-write", 0.686],    // write-text: the findings being written up
  ["6-publish", 0.790],  // publish-text: the paper going public
  ["7-verdict", 0.925],  // horizon-hold: the verdict, the still-open card, the input again
];

const SIZES = [
  ["desktop", 1280, 800],
  ["phone", 390, 844],
];

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();

for (const [sizeName, width, height] of SIZES) {
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: 2,
  });
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`${BASE}/flight`, { waitUntil: "networkidle" });

  const travel = await page.evaluate(() => {
    const s = document.getElementById("flight");
    return parseFloat(getComputedStyle(s).height) - window.innerHeight;
  });

  for (const [label, frac] of STOPS) {
    await page.evaluate((y) => window.scrollTo(0, y), Math.round(travel * frac));
    // Let the frame fetch, decode and paint before capturing.
    await page.waitForTimeout(1400);
    await page.screenshot({
      path: `${OUT}/${sizeName}-${label}.png`,
    });
  }

  console.log(
    `${sizeName}: ${STOPS.length} shots, ` +
      `travel ${Math.round(travel)}px` +
      (errors.length ? `, ${errors.length} console errors: ${errors[0]}` : ", no console errors")
  );
  await page.close();
}

await browser.close();
console.log(`written to ${OUT}`);
