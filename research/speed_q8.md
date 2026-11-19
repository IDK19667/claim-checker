# Speed, queue 8

Measured with `scripts/speed.mjs` on the fixture server (`scripts/ui_fixture_server.py`):
375x812 mobile context, 4x CPU slowdown, 1.6 Mbps down / 150 ms RTT (CDP network emulation,
through a host name mapped to localhost because Chromium does not throttle localhost), cold
context, service worker blocked, 5 runs, median.

| metric | before (median of 5) | after |
|---|---|---|
| first contentful paint, home | 1112 ms (1100 to 1260) | 1112 ms, no change shipped |
| largest contentful paint, home | 1216 ms (1204 to 1360) | 1216 ms, no change shipped |
| Enter to the page's own first line ("Building the search") | 28 ms | 28 ms |
| Enter to the first server stage ("Searching PubMed"), fixture's 900 ms pause removed | 54 ms | 54 ms |

## What was looked at

The critical path of "/" is already short: 9 KB of gzipped HTML, one 16 KB gzipped stylesheet,
three self-hosted woff2 faces with `font-display: swap`, the two faces the first screen sets
preloaded, `app.js` (26 KB gzipped) as the last element of the body. CSS finishes at about
600 to 800 ms on this profile; first paint follows at about 1100 ms.

Experiments (each 5 runs, FCP / LCP medians):

| change | FCP | LCP | kept |
|---|---|---|---|
| none | 1112 | 1216 | yes (baseline) |
| drop the preload of the body face (Instrument Sans) | 1024 | 1136 | no |
| `defer` on `app.js` | no gain (1188 in one run), and the page's submit stopped streaming in the test browser | | no |
| `app.js` held back 4 s | FCP unchanged (1180) | | shows `app.js` is not what gates paint |
| display face held back 4 s | FCP unchanged (1020) | | shows the font is not what gates paint |
| no CPU or network throttle | 352 | | the rest is main-thread and layout time at 4x |

Nothing clearly blocks first paint: no render-blocking third-party, no unswapped font, no
oversized script ahead of the content. Dropping the body-face preload saves about 85 ms but
lets the body text paint in the fallback face first and swap, a visible shift on the one screen
the design is about, so it was not applied. No site file was changed.

## Caveats

- The first-stage figure is mostly the fixture's own pause (900 ms), which the script subtracts.
  The streaming response does not seem to pick up the emulated 150 ms latency, so this figure
  says the page adds almost nothing on top of the server, not what a real phone would see.
- Real-network numbers need the deployed site; this is the fixture on one machine.
