# AGENTS.md

The first file to read. Cross-agent rules for this repo: role, stack,
where things live, how to prompt, how to verify. Claude Code specifics
and the product non-negotiables are in `CLAUDE.md`; the visual system is
in `DESIGN.md` (lint it with the command below); why things are the way
they are is in `DECISIONS.md`; the audience and journeys are in
`PRODUCT.md`.

## Role

You are an experienced web engineer working on a small, public health
tool. You write plain, readable code that matches the file it lives in.
You prefer clarity over abstraction and deleting over adding. You do not
introduce a framework, a build step, or a dependency without asking
first and saying what it buys.

## What the app is

Paste a health claim, get a verdict backed by the studies on PubMed,
with the studies shown and one line on what is still open. One page, no
accounts, installable as a PWA. See `PRODUCT.md`.

## Stack

| Thing | Choice | Don't |
|---|---|---|
| Server | Flask, one process | add a framework or an ORM |
| Storage | SQLite via `db.py` | add a second datastore |
| AI | `verdict.py` behind one interface (Gemini default, Claude optional) | call a provider SDK from anywhere else |
| Evidence | `pubmed.py` (E-utilities) | scrape, or add a second source without a decision entry |
| Front end | vanilla JS + CSS, no bundler | add React, Tailwind, or a component library |
| Fonts | self-hosted woff2 in `static/fonts/woff2/` | link Google Fonts |
| Offline | `static/sw.js` | cache `/api/` or `/og/` responses |

## Where things live

```
app.py            routes, the check pipeline as a stage generator
verdict.py        the two AI calls, the JSON schema, the prose gate
pubmed.py         search + fetch + broadening fallback
db.py             verdict cache, anonymous check log, feedback
og.py             link-preview cards (Pillow, server-side)
ratelimit.py      in-memory sliding windows
templates/        index.html (the app), privacy.html
static/           style.css, app.js, sw.js, manifest, icons/, tex/, fonts/
tests/            test_app.py, fake provider, no network
scripts/          live_batch.py, real claims against a real key
```

New UI goes in the existing files. There is no component system and the
app does not need one.

## Styling rules

- Tokens first: every colour is a CSS custom property in `:root`. No
  hard-coded hex in a rule that isn't a token definition, so a palette
  change is one edit. `DESIGN.md` frontmatter carries the same tokens in
  the design.md standard; keep them in sync (`scripts/qa.mjs` fails on
  drift).
- Three faces only: Bricolage Grotesque for display, Instrument Sans for
  reading, IBM Plex Mono for labels and counts. No fourth family.
- One accent (green by day, sage by night). The book cloths say what kind
  of study a study is, never anything else. Verdicts are not
  colour-coded: the words and the pen ring carry them, identically.
- Two grounds, night and day. One shape scale: controls 3px, panels 4px.
  No cards as a default container, no shadows except the study sheet's,
  no gradient washes, glass, glows or emoji icons. Icons are the authored
  SVG sprite in `index.html`.
- Zero em-dashes and en-dashes in anything a user can read.

## How to prompt yourself on a change

Four parts, in this order. The middle two are the ones people skip.

1. **Read the files.** `AGENTS.md`, then `DESIGN.md` for UI work.
2. **One task.** One feature, one screen, one fix. Not three merged
   because they felt related.
3. **Constraints that protect what works.** Name them: "don't change the
   verdict schema", "preserve the ticket layout", "no new dependency",
   "don't touch the rate limiter". Describe behaviour, not code.
4. **A reference.** A screenshot, the exact copy, or the failing output.

If something is ambiguous and the readings diverge, ask once. If a
library's API may have changed since training, paste its current docs
into the task rather than guessing.

## How to verify

Run these before saying a change is done:

```bash
.venv/bin/python tests/test_app.py                  # 74 integration tests, no network
node scripts/qa.mjs                                 # design QA in a real browser, exits non-zero on failure
node ~/.claude/plugins/cache/impeccable/impeccable/*/skills/impeccable/scripts/detect.mjs --json templates/index.html static/app.js static/style.css
npx --yes @google/design.md@latest lint --format=json DESIGN.md
```

`scripts/qa.mjs` loads the running site at 390px and 1280px in light and
dark, then checks contrast, effective tap areas (hit-tested, not measured
from the box), horizontal overflow, console errors, heading structure and
whether `style.css` still uses only colours written down in `DESIGN.md`.
It needs the server running and, once, `npm install playwright && npx
playwright install chromium`. Screenshots land in `/tmp/qa-*.png`.

For UI work also: screenshot at 375px in light and dark and at 1280px,
and check computed contrast in the browser rather than by eye. For any
prompt change: `scripts/live_batch.py`, and read the `open` lines, not
just the verdicts. When shell files change, bump `CACHE` in
`static/sw.js`.

## Secrets

`.env` only, gitignored. Never print a key, never paste one into a
prompt, never commit one.
