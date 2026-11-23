# CLAUDE.md: Evident

Public, mobile-first PWA: paste a health claim, get a PubMed-backed
verdict with the studies it used. One page, no accounts. Flask + vanilla
JS, no build step.

Read before working: `AGENTS.md` (role, stack, file map, how to prompt
and verify: the cross-agent rules), `PRODUCT.md` (who it's for, the magic
moment, the two journeys), `DESIGN.md` (the visual system, before any UI
change), `DECISIONS.md` (why things are the way they are), `README.md`
(setup). Record a real decision in `DECISIONS.md` when you make one.

## Stack

| Layer | What | Notes |
|---|---|---|
| Server | Flask (`app.py`) | routes, SSE stream, OG cards, rate limits |
| Evidence | `pubmed.py` | esearch + efetch, query broadening fallback |
| Verdict | `verdict.py` | provider abstraction, JSON schema, prose gate |
| Storage | `db.py`, SQLite | verdict cache, anonymous check log, feedback |
| Cards | `og.py`, Pillow | 1200x630 link previews |
| Front end | `templates/`, `static/app.js`, `static/desk.css`, `static/desk.js`, `static/style.css` | no framework, no bundler; three.js is the one vendored file |
| Offline | `static/sw.js`, `static/manifest.webmanifest` | network-first shell, cache-first fonts |

Ask before adding a dependency, and say what it buys. The absence of a
build step is a feature: anyone can read the shipped file.

## Non-negotiables (product)

1. Never claim more certainty than the evidence supports. "It's
   complicated" is a correct verdict. No prompt tuning may push toward
   confident true/false.
2. A verdict cites only studies the model was actually shown
   (`cited_study_numbers` → PMID mapping in `verdict.py`). A verdict with
   no citations is forced to "insufficient" ("Not enough evidence") in
   code, and so is one resting only on case reports or animal and lab
   studies. Keep both.
3. Every verdict says what is still open. The `still_open` field is
   required in the schema and rendered on the ticket, the share card and
   the shared text.
4. No check fails silently. Every failure path in `app.py` /
   `verdict.py` returns a defined, non-crashing response with a way out.
5. Keep full study metadata (abstract, authors, publication types, data
   banks) flowing through the API and into `checks.studies_json`.
6. "Not medical advice" stays visible on every screen.
7. No classroom/teacher features; no accounts; no cookies; no
   third-party analytics and no per-person tracking; nothing that ties a
   check to a person. The one thing counted is per-day aggregate totals
   with no identifiers (the `counters` table, `/stats`), exactly as
   `/privacy` describes. The privacy page says so, so it has to stay true.

## Working rules (design)

- Before writing any UI, invoke the `frontend-design` skill (or
  `impeccable`) and re-read `DESIGN.md`. Refinement preserves the shipped
  world; a redesign replaces `DESIGN.md` on purpose, never by drift.
- The front page is the lamp-lit desk: `templates/index.html`,
  `static/desk.css` and `static/desk.js`, with three.js in
  `static/vendor/`. Every other page
  (about, how it works, corrections, privacy, the 404, stats) keeps
  `static/style.css`. The front must work as text, without WebGL and
  without scripts.
- After any UI change run `node scripts/qa.mjs` (server must be running).
  It checks contrast, tap areas, text size, overflow, console errors, CSP
  violations, the ground each page stands on, the first screen on short
  laptop screens and phones, and token drift, at both widths in both
  schemes, and fails loudly. Look at the screenshots it leaves in
  `$QA_OUT/qa-*.png` (default `/tmp`) too. Fix in one batch; confirm
  once; stop polishing.
- The first screen holds the claim box: at 1366x650 and up, and on a
  phone, the headline, the claim box and the button are on screen with
  no scrolling, and during a check the newest log line and Cancel are.
- `DESIGN.md` carries its tokens as design.md-standard frontmatter. When
  a token changes in `static/desk.css` or `static/style.css`, change it
  there too and re-lint:
  `npx --yes @google/design.md@latest lint --format=json DESIGN.md`
  (0 errors, 0 warnings).
- Run the mechanical detector once on changed UI files:
  `node ~/.claude/plugins/cache/impeccable/impeccable/*/skills/impeccable/scripts/detect.mjs --json templates/index.html static/app.js static/desk.css static/desk.js static/style.css`
- Motion: the camera, the light and the words move with the scroll.
  The one automatic move is the glide to the answer: once, after a check
  the reader just ran, cancelled by any input, a cut under reduced
  motion. Don't add another. A press of Next, Back, the rail or the menu
  glides to its stop; a live check stands its blank sheets on the desk
  and prints the reading log. The one deliberate exception beside the
  glide: the cue row's chevron dips once, about 3 seconds after an answer
  the reader just ran lands, cancelled by any input, never under reduced
  motion, never on a saved or shared answer. No entrances on every
  section, no animated backgrounds, no cursor effects, no scroll
  hijacking. Respect `prefers-reduced-motion`.
- Cards are not a default container. Group with rules, space and the
  section grammar already in use.
- Copy is product language: controls name their action, errors name the
  problem and the recovery. **Zero em-dashes or en-dashes in any string a
  user can see** (a test enforces it on the rendered page); commas,
  colons and full stops do the work. `verdict.tidy_prose` applies the
  same rule to model output.
- Three faces, no more: Bricolage Grotesque for display (headlines, the
  verdict, figures), Instrument Sans for everything read, IBM Plex Mono
  for labels a reader scans. One accent: sage on the night for what you
  can act on, green by day on the other pages. The book cloths
  (`--c-strong`, `--c-human`, `--c-lab`, `--c-weak`) mean the kind of
  study and nothing else. Verdicts are never colour-coded: no stylesheet
  selects on `data-verdict`.
- Only the desk's own colours on the front (`--desk-*` in `desk.css`),
  no new hue. 3px corners. No glow, neon, cyan, shadows on content, blur
  or glass, no gradients beyond the two scrims, no pills, badges, card
  grids, icon circles or emoji.
- Fonts are self-hosted in `static/fonts/woff2/`. Don't reintroduce a
  Google Fonts link: it costs a third-party connection and breaks
  offline first paint.

## Working rules (code)

- One task per change. State the task, what must not break, and the
  constraint. "Preserve the existing UI exactly" belongs in the prompt
  when the work is behavioural.
- Tests: `.venv/bin/python tests/test_app.py` (fake provider, no
  network). Add a test for any new endpoint, fallback path, or copy rule
  that matters.
- Verdict quality on real claims: `scripts/live_batch.py` (needs a key,
  costs free-tier quota). Re-run after any prompt change and read the
  `open` lines, not just the verdicts. Score it with
  `scripts/regression.py` (the 21 claims in `tests/regression_claims.json`).
- Service worker: bump `CACHE` in `static/sw.js` when shell files change,
  and add new shell files to `SHELL`.
- A result page is server-rendered: the verdict, the reasoning and the
  studies are in the HTML, with ClaimReview markup, before any JavaScript
  runs. Keep it that way. If you change how a result looks, change both
  the Jinja in `templates/index.html` and `renderResult` in `app.js`, and
  check `curl -s "localhost:5000/?q=..."` still contains the verdict.
- Secrets live in `.env` only (gitignored). Never print a key.
