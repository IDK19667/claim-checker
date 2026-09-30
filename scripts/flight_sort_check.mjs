// One-off: sweep the lab beat finely and check whether the card stack ever
// settles into a clean, non-overlapping order, or whether the overlap in
// the earlier screenshot is a permanent misplacement rather than a normal
// mid-shuffle crossing. Reads each card's rendered top (getBoundingClientRect,
// which already includes the translateY transform) rather than trusting the
// transform value alone.
import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto("http://localhost:5000/flight", { waitUntil: "networkidle" });

const travel = await page.evaluate(() => {
  const s = document.getElementById("flight");
  return parseFloat(getComputedStyle(s).height) - window.innerHeight;
});

for (let frac = 0.45; frac <= 0.7; frac += 0.02) {
  await page.evaluate((y) => window.scrollTo(0, y), Math.round(travel * frac));
  await page.waitForTimeout(250);
  const info = await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll(".stack li"));
    const rects = items.map((el) => el.getBoundingClientRect());
    let overlaps = 0;
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i], b = rects[j];
        if (a.top < b.bottom - 2 && b.top < a.bottom - 2) overlaps++;
      }
    }
    return { overlaps, tops: rects.map((r) => Math.round(r.top)) };
  });
  console.log(frac.toFixed(2), "overlaps:", info.overlaps, "tops:", info.tops.join(","));
}

await browser.close();
