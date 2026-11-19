#!/usr/bin/env node
/**
 * The footer's separator dots never end or start a line, at any width: a dot
 * belongs to the gap before a link, and the paragraph clips the dot of every
 * link that starts a row. Also: the links are 44px rows, nothing scrolls
 * sideways, and no dot is written into the text.
 *
 *   node scripts/ui_footer.mjs [baseUrl]
 */
import { chromium } from "playwright";

const base = process.argv[2]?.startsWith("http") ? process.argv[2] : "http://localhost:5050";
const browser = await chromium.launch();
let bad = 0;
const fail = (m) => { bad++; console.log("FAIL " + m); };
for (const path of ["/", "/about", "/privacy"]) {
  for (const w of [320, 360, 390, 430, 768, 1440]) {
    const page = await browser.newPage({ viewport: { width: w, height: 800 } });
    await page.goto(base + path); await page.waitForLoadState("load");
    const r = await page.evaluate(() => {
      const box = document.querySelector(".foot-links");
      const cb = box.getBoundingClientRect();
      const links = [...box.querySelectorAll("a")].map((a) => {
        const b = a.getBoundingClientRect();
        const pseudo = getComputedStyle(a, "::before");
        return { l: b.left, t: b.top, h: b.height, content: pseudo.content, left: parseFloat(pseudo.left), w: parseFloat(pseudo.width) };
      });
      return { clipLeft: cb.left, clipRight: cb.right, links, text: box.textContent, scrollW: document.documentElement.scrollWidth, innerW: innerWidth };
    });
    const where = `${path} @${w}`;
    if (/[·]/.test(r.text)) fail(`${where}: a dot is written into the footer text`);
    if (r.scrollW > r.innerW + 1) fail(`${where}: horizontal scroll`);
    r.links.forEach((k, i) => {
      if (k.h < 43.5) fail(`${where}: link ${i} is ${k.h}px tall`);
      const centre = k.l + k.left + k.w / 2;          // the dot's centre
      const startsRow = i === 0 || Math.abs(k.t - r.links[i - 1].t) > 4;
      const seen = centre - 3 > r.clipLeft && centre + 3 < r.clipRight;
      if (startsRow && seen) fail(`${where}: link ${i} starts a row and its dot is visible`);
      if (!startsRow && !seen) fail(`${where}: link ${i} is mid-row and its dot is clipped`);
    });
    // The last link is never followed by anything, so nothing can dangle at a line's end.
    if (w === 390 && path === "/") await page.locator(".foot-links").screenshot({ path: "/tmp/qa-foot-390.png" });
    await page.close();
  }
}
await browser.close();
console.log(bad ? `\n${bad} failed` : "all passed");
process.exit(bad ? 1 : 0);
