# SESSION_NOTES.md: Claim Checker handoff

Written 2026-09-28. Everything below was verified against the code at the
time of writing rather than recalled. Where something is uncertain it says
so.

Companion documents, all current: `PRODUCT.md` (who it is for), `DESIGN.md`
(the visual system and its tokens), `DECISIONS.md` (why things are the way
they are, newest first), `CLAUDE.md` and `AGENTS.md` (working rules),
`README.md` (setup), `DEPLOY.md` (publishing).

---

## 1. What the app does

Paste a health claim you saw online. The app turns it into a PubMed search,
fetches up to eight real studies, asks a model to weigh them, and returns a
verdict of **likely true**, **likely false**, or **it's complicated**,
along with the studies it actually relied on and a required statement of
what is still unresolved.

One page, no accounts, free to use, installable as a PWA, works offline
after one visit.

**Who it is for:** someone who just saw a claim on TikTok, WhatsApp, or
from a relative, has about forty seconds of patience, no medical
vocabulary, and no intention of reading eight abstracts. The secondary
audience is whoever they send the screenshot to, which is why the share
card is treated as a first-class surface rather than an afterthought.

---

## 2. Tech stack

| Layer | What | Notes |
|---|---|---|
| Server | Flask (`app.py`) | routes, SSE streaming, rate limits, OG cards |
| Evidence | NCBI E-utilities | `esearch`, `efetch`, `elink`. Free, no key, 3 req/sec |
| Model | Google Gemini `gemini-3.5-flash-lite` | via `google-genai`. Free tier |
| Storage | SQLite | verdict cache, anonymous check log, feedback |
| Cards | Pillow (`og.py`) | 1200x630 link previews |
| Front end | vanilla JS + CSS | no framework, no bundler, no build step |
| Offline | service worker | network-first shell, cache-first fonts |
| Tests | plain Python (`tests/test_app.py`) | 132 tests, fake provider, no network |
| Browser QA | Playwright (`scripts/qa.mjs`) | contrast, tap targets, overflow, tokens |

The absence of a build step is deliberate: the file you read is the file
that ships.

`anthropic` is installed and `verdict.py` supports Claude as an alternative
provider, but no Anthropic key is configured, so Gemini is what runs.

---

## 3. Every file and what it does

### Python

- **`app.py`** (608 lines). Every route, the SSE stream, rate limiting, the
  ClaimReview JSON-LD, `robots.txt`, `sitemap.xml`, `llms.txt`, the 404
  handler, and the front-page ledger. `_check_response()` is the single
  shape every result takes, server-rendered or streamed.
- **`pubmed.py`** (276). E-utilities client. Search, fetch, parse, plus the
  query-broadening fallback and the layer-3 `elink` calls (`deep_dive`,
  `cited_by_count`, `free_full_text_url`, `related_studies`).
- **`verdict.py`** (544). Provider abstraction (Gemini or Claude), the two
  prompts, the JSON schema, `ProviderError`, and `tidy_prose()` which
  strips filler openers and dashes from model output.
- **`evidence.py`** (129). Classifies each study into strong / moderate /
  weak / retracted from PubMed's publication types, and builds the snapshot
  figures. No model involved: this is a fact about the record.
- **`db.py`** (320). SQLite. Verdict cache, anonymous check log, feedback,
  trending, the front-page ledger and `recent_verdicts`.
- **`suggest.py`** (80). Type-ahead over already-checked claims, with typo
  tolerance via `difflib` plus word overlap. No model, no network.
- **`nextsteps.py`** (99). What to offer when PubMed has no answer: the
  exact search, a wider one, and related claims that do have answers.
- **`ratelimit.py`** (101). Sliding-window limiters, per IP and global.
- **`og.py`** (180). Draws the 1200x630 link-preview card with Pillow.

### Front end

- **`templates/index.html`** (421). The whole app: masthead, front page,
  result, error, study sheet, and the server-rendered result with
  ClaimReview markup.
- **`templates/privacy.html`**, **`templates/404.html`**.
- **`static/app.js`** (1194). Streaming, rendering, type-ahead, deep dive,
  share card canvas, on-device history.
- **`static/style.css`** (667). The entire visual system, tokens first.
- **`static/sw.js`**. Service worker. **Bump `CACHE` whenever a shell file
  changes** or browsers serve stale JavaScript.
- **`static/fonts/`** (14 files). Libre Franklin woff2 subsets plus TTFs
  for the Pillow card. Several older faces are still on disk unused; see
  §7.

### Tooling and docs

- **`tests/test_app.py`** (521). 132 tests. Fake provider, fake PubMed, no
  network, no key.
- **`scripts/qa.mjs`** (178). Real-browser gate.
- **`scripts/live_batch.py`**. Runs real claims against the real model to
  check verdict quality. Costs quota.
- **`render.yaml`**, **`Procfile`**, **`DEPLOY.md`**. Publishing.

---

## 4. How a check works, end to end

**Input.** The claim is submitted from the front page, capped at 500
characters. `/api/check/stream` (POST) is tried first; `/api/check` (POST)
is the non-streaming fallback.

**Rate limits.** Before anything costs money: 6 checks per IP per 10
minutes, 12 per minute site-wide, 400 per day site-wide. A cached claim
skips the limiter entirely because it is free.

**Cache.** The claim is normalised and looked up. A hit within 24 hours
returns immediately with no model call and no PubMed call.

**Sources.** On a miss, model call #1 turns the claim into a PubMed query.
`esearch` then `efetch` fetch up to 8 studies with title, abstract,
journal, year, authors, publication types and data-bank registrations. If
the search returns nothing, the last `AND` term is dropped and it retries
once, because models over-specify.

**Scoring.** Model call #2 receives the numbered studies and returns
schema-constrained JSON: `verdict`, `tldr`, `explanation`, `still_open`,
and `cited_study_numbers`. Those numbers are mapped back to PMIDs in code.
**A verdict with no citations is forced to "complicated" in code**, not by
asking the model nicely. Separately and independently, `evidence.py`
classifies every study from PubMed's publication types; the evidence
snapshot and the bar come from that, never from the model's opinion.

**Output.** The result is cached, logged anonymously with full study
metadata, and streamed to the page. The page shows three layers:

1. the verdict band, the takeaway, and what is still open
2. the evidence snapshot and the source column
3. the deep dive on any single study, loaded on demand from
   `/api/study/<pmid>`

If nothing usable came back, `nextsteps.py` supplies the empty state.

**Cost per new claim:** 2 Gemini requests. 1 if PubMed found nothing (the
weighing step short-circuits). 0 if cached.

---

## 5. Decisions, and why

The full record with reasoning is in `DECISIONS.md`, newest first. The ones
that constrain future work:

**Never claim more certainty than the evidence supports.** "It's
complicated" is a correct and common answer. This governs the empty state
too, which is why it never proposes a possible answer.

**Every verdict states what is still open.** Required in the schema,
rendered everywhere including the share card. A verdict that sounds
finished is a lie about science.

**Citations are enforced in code, not by prompt.** Both halves must stay.

**No check fails silently.** Every failure path returns a defined,
non-crashing response with a way out.

**Nothing ties a check to a person.** No accounts, no analytics, no
cookies. The privacy page says so, so it has to remain true.

**Verdicts are never colour-coded.** A tool whose only product is
credibility must not tint its own conclusions. The evidence bar is ink
only; a retracted paper is a hatch, not a red segment.

**The front page carries the work.** Real counts and real recent verdicts
with their evidence, not a landing page. This was the fix for four rounds
of "it looks like AI slop": the problem was never the typeface, it was an
empty page.

**The visual world was chosen by building parallel versions and picking
one**, four times over: the broadsheet from four, the front page from
three, then the facts panel, then the field. `DESIGN.md` is replaced
deliberately on a redesign, never by drift.

**Rejected on purpose:** scroll-driven hero video and generated imagery
(bought with photography and film we do not have; copying the surface
without the asset is the slop); dark mode (removed 2026-09-25, "paper is
the argument"); trust badges, awards and "medically reviewed by" (would be
fabrication); personalization by region or history (breaks the privacy
rule); analytics of any kind.

---

## 6. Known bugs and rough edges

**Five dead database tables: fixed 2026-09-28.** `classes`, `students`,
`curated_claims`, `assignments` and `activity_log` (left over from the
abandoned classroom version, including 20 rows pairing claim text with a
student id) were dropped from the local database and the file was vacuumed
so the deleted rows are not recoverable from free pages. Integrity check
passes; `verdict_cache` (26), `checks` (80) and `feedback` (1) are intact.
`db.py` never referenced these tables, so nothing recreates them.

**One detector warning**, `border-accent-on-rounded` on `dialog.sheet`'s
4px top edge. Believed to be a false positive, since the element has
`border-radius: 0`. Left alone deliberately: it is a design mark from the
field redesign.

**Unused font files.** Removed 2026-09-28. Ten unreferenced faces
(archivo, jetbrains x3, librecaslon x3 woff2s and three TTFs) were deleted
from `static/fonts/`, taking it from 1.4MB to 232KB. Only
`librefranklin-normal-400-900.woff2` (the page) and `LibreFranklin.ttf`
(the Pillow card) remain. Verified afterwards: 132 tests pass, the OG card
still renders, the QA gate passes, and no page requests a font that 404s.
`OFL-LibreCaslon.txt` was deliberately left in place; an orphaned licence
file costs nothing and removing licences is a habit worth not forming.

**Python version mismatch.** Development is on 3.14.2; `render.yaml` pins
3.12.7 because it is reliably available. Nothing in the code needs newer
than 3.10, but this is untested on 3.12.

**The dev server can serve a stale template.** If a template change appears
to do nothing, restart the server before debugging. This cost real time
once.

---

## 7. What is unfinished

**It has never been deployed.** Everything runs on localhost. `DEPLOY.md`
is a complete walkthrough and the repo is committed and ready; what is
missing is a GitHub repo and a Render account, which need the owner. Until
then the ClaimReview markup, sitemap, `llms.txt` and OG cards do nothing.

**Multi-provider failover** was designed but not built. Adding a second
free provider (Groq, Cerebras, Mistral) would raise the ceiling, but each
new model must be validated with `scripts/live_batch.py` against the
certainty rule before it is allowed to write verdicts. Deliberately
deferred until real traffic proves it is needed. Note that extra Gemini
keys do not help: Google applies limits per project, not per key.

**The cache does not survive deployment.** Free hosting has an ephemeral
filesystem, so the ledger and latest-checks list reset on every restart.
`render.yaml` has a commented `disk:` block for when that matters.

**No custom domain**, and `PUBLIC_URL` is unset.

**One research video was never read.** `fDTwHIKltpc`: YouTube rate-limited
the captions and the transcript endpoint now returns an empty body.

---

## 8. How to run it

```bash
cd "health-claim-checker 2"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then add GEMINI_API_KEY
.venv/bin/python app.py       # http://localhost:5000
```

A free Gemini key comes from <https://aistudio.google.com/app/apikey>.
PubMed needs no key. `HOST` defaults to `0.0.0.0`, so the site is reachable
from a phone on the same Wi-Fi, which is the device it was designed for.

**Before finishing any change:**

```bash
.venv/bin/python tests/test_app.py     # 132 tests, no network needed
node scripts/qa.mjs                    # needs the server running
```

Then look at the screenshots in `/tmp/qa-*.png`. Every flaw that mattered
in this project was found by looking, not by a passing check.

**After a UI change** also run the design-token lint and the detector, both
documented in `CLAUDE.md`, and bump `CACHE` in `static/sw.js`.

**Verified state at handoff:** 132/132 tests pass, the QA gate passes at
390px and 1280px, `design.md` lints 0 errors and 0 warnings, the front page
loads in 71ms with a CLS of 0, and 13 internal links resolve with none
broken.
