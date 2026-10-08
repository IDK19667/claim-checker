# Evident

Health claims, checked against the evidence.

Context files: `PRODUCT.md` (what it is and who for), `DESIGN.md` (the
visual system), `DECISIONS.md` (why), `CLAUDE.md` (how to work on it).

Paste a health claim you saw online. Evident searches real medical
studies on PubMed, weighs how strong they are (a large clinical trial
outranks a single small pilot), and gives you a straight answer
(**likely true / likely false / it's complicated / not enough evidence**)
with the studies it
actually used highlighted, plus one line on what's still open.

It's a mobile-first web app that installs on iPhone and Android from the
browser (no app store). One page, no accounts, no tracking.

## What it does

- **A verdict with receipts.** The result is a stamped ticket: the claim
  in quotes, the stamp breaking the card's edge, a one-line takeaway you
  can text back, the explanation (with "Study 4" as a tap target that
  opens that study), and a ledger: studies read / used / evidence
  strength. Studies the verdict relied on are literally highlighted in
  the list below.
- **An evidence meter** (Strong / Moderate / Weak / No direct evidence)
  computed from the *types* of studies the verdict cited (meta-analysis
  beats case report), not from the model's opinion. Tap it for the rules.
- **Deep dive on any study.** Tap a study for its abstract, what kind of
  study it is in plain words, the researchers, registered-data links, and
  a link to more studies on PubMed.
- **Share it.** On a phone, a sticky Share button sends a square image
  card (stamp + claim + takeaway + the strongest study it used) to any
  app. Shared links unfurl in iMessage/WhatsApp
  with the verdict as the preview. On Android, Evident appears in
  the system share sheet, so you can send it a claim from TikTok or a
  group chat directly.
- **Trending this week**: what people are checking, anonymously.
- **Real progress** while it works: the actual PubMed query it ran, how
  many studies came back, when it's weighing them.
- **Recent checks** saved on the device, readable offline. Voice input
  where the browser supports it. Thumbs up/down on any verdict.

## Run it

1. **Get a free Gemini key** at https://aistudio.google.com/apikey.
   If AI Studio shows "permission denied" errors, use the Cloud Console
   route: create a project at console.cloud.google.com → API Library →
   enable "Gemini API" → Credentials → Create credentials → API key →
   restrict it to the Gemini API and bind the service account it asks for.
   New keys look like `AQ.A…`. (A paid Claude key also works; see
   `.env.example`.)

2. **Install and configure:**
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env    # then paste your key in place of the placeholder
   ```

3. **Start it:**
   ```bash
   .venv/bin/python app.py
   ```
   It listens on all interfaces, so on the same Wi-Fi you can open
   `http://<your-computer's-IP>:5000` on a phone and use **Add to Home
   Screen** (Safari: Share → Add to Home Screen; Chrome: the Install
   button appears in the header).

## Put it online for everyone

The app is a standard Flask service with a `Procfile`, so any Python host
works (Render, Railway, Fly.io, a $5 VPS). What it needs:

- `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) as an environment variable:
  never commit `.env`.
- `TRUST_PROXY=1` so rate limiting sees real visitor IPs behind the host's
  proxy.
- **One worker process** (`--workers 1 --threads 8`, already in the
  `Procfile`). The rate limiter is in-memory; multiple processes would each
  keep their own counters.
- A persistent disk for `health_claim_checker.db` if you want the verdict
  cache to survive restarts (set `DB_PATH` to a path on it). Without one
  the app still works, it just re-checks claims after each deploy.
- HTTPS (every host above provides it). Service workers and the install
  prompt require it.

### Free-tier math

Every *uncached* check is 2 AI calls. Gemini Flash-Lite's free tier was
roughly 30 requests/min and ~1,000/day in 2026 (Google shows your exact
numbers in AI Studio). Two things keep a public deployment inside that:

- **The verdict cache** (`VERDICT_CACHE_HOURS`, default 24). A viral
  claim checked by a thousand people is one AI round-trip.
- **Rate limits** (`.env.example`): per IP address and global per-minute /
  per-day. Over the limit → a friendly "try again in N seconds", never a
  crash. Cached results are never rate-limited.

If you outgrow the free tier, the paid Gemini tier is fractions of a cent
per check and doesn't use your prompts for training (the free tier does;
see Google's terms).

## How a check works

1. If the same claim (case/punctuation-insensitive) was checked within
   `VERDICT_CACHE_HOURS`, the stored result comes back instantly.
2. Otherwise the rate limiter runs.
3. The AI model turns the claim into a PubMed search query (kept close to
   the claim's own words; MeSH-style terms for the condition).
4. PubMed's free `esearch` + `efetch` return the top 8 studies with
   abstracts, journal, year, authors, and publication types. If nothing
   matches, the last search term is dropped and it tries once more (the
   result says so).
5. The model weighs them (publication type is the main quality signal)
   and answers in a fixed JSON shape: verdict enum, a one-line takeaway,
   the explanation, one line on what's **still open**, and which study
   numbers it used. The prompt says "complicated" and "insufficient" (not
   enough evidence) are correct answers,
   that "false" needs studies that *contradict* the claim, that studies
   which merely match the words must not be cited, and that concrete
   numbers from the abstracts beat adjectives.
6. Code enforces the rest: study numbers map back to the PMIDs the model
   was shown (so it can only cite what it read), any verdict with no
   citations, or only case reports and lab studies, is forced to
   "insufficient", any malformed/refused/truncated
   model response degrades to "complicated" rather than an error, and
   `verdict.tidy_prose` strips filler openers and dashes and caps each
   field at a sentence boundary.
7. The result is cached and logged (anonymously, full study metadata kept),
   and the stages stream to the page as server-sent events.

## Design

A page from a paper of record that carries its own work. The front page is
a small nameplate, one very large headline with the claim field beside it,
a ledger of real counts (studies read, claims checked, pooled analyses),
and the latest checks with the evidence behind each one: an ink bar showing
the mix of study designs, and the real figures. The claim is set as a
headline, the verdict
is a banner between two rules, the takeaway takes a drop cap, and the
sources run in a column with a rule under the ones the verdict relied on.
Two faces (Libre Caslon Text and Archivo), one ink, no accent colour, and
verdicts are deliberately not colour-coded. This world was chosen by
building four complete versions of the app and comparing them side by
side, and the front page was chosen from three the same way;
`DECISIONS.md` records what the others were and why these won.
`DESIGN.md` is the system as shipped, with its tokens in Google's
design.md format so they can be linted against the stylesheet. On a wide
screen the report sits beside its sources. Fonts are self-hosted, so the
page makes no third-party request and looks right offline. `CLAUDE.md`
and `AGENTS.md` hold the working rules. The UI was reviewed with the
Impeccable critique flow (design-director review + mechanical anti-
pattern scan); the scan is clean and the review's priority issues are
fixed. Re-run the scan after UI edits (command in `CLAUDE.md`).

## Files

```
app.py            Flask: page + link previews, /api/check (JSON) and
                  /api/check/stream (SSE), /api/trending, /api/feedback,
                  /og/<claim>.png, PWA assets, /healthz
verdict.py        The two AI calls; Gemini or Claude behind one interface
pubmed.py         PubMed E-utilities wrapper, XML parsing, broadened retry
ratelimit.py      In-memory sliding windows (per IP + global + feedback)
db.py             SQLite: verdict_cache, anonymous checks log, feedback
evidence.py       what kind of study it is, and the snapshot figures
og.py             Pillow renderer for link-preview cards
templates/        index.html (the single page + two bottom sheets),
                  privacy.html
static/           style.css, app.js, sw.js, manifest.webmanifest, icons/,
                  tex/ (paper grain, stamp ink),
                  fonts/ (Caslon + Archivo TTFs for og.py; woff2/ for browsers)
scripts/          live_batch.py: run 14 real claims and eyeball verdicts
                  regression.py: score the 20 audit claims against their expected verdicts
                  qa.mjs: browser design QA (contrast, tap areas, tokens)
tests/            test_app.py: 63 integration tests, no network needed
```

Context: `PRODUCT.md` (audience, magic moment, journeys, scope),
`DESIGN.md` (the visual system as shipped), `DECISIONS.md` (what was
decided and why, including what was deliberately rejected), `CLAUDE.md`
(working rules).

## Found by search, and readable by machines

A result page is rendered on the server: the verdict, the takeaway, the
reasoning, what's still open and every study with its PMID are in the HTML
before any JavaScript runs. That makes a shared link paint instantly and
makes the page readable to search engines and to language models. Each
result also carries schema.org `ClaimReview` markup with the rating and
every PubMed study as a citation, with the relied-on ones flagged. There
is a `robots.txt`, a `sitemap.xml` of cached claims, canonical links, and
an `llms.txt` stating the verdict rules in plain language.

## What's verified

- Live PubMed search and parsing (including titles with inline markup).
- Both AI providers against their live endpoints, with real error
  responses mapped to sensible messages (bad key, rate limit, outage).
- 14 varied real claims end to end on Gemini: well-studied claims get
  confident verdicts citing meta-analyses; overclaims ("cures diabetes")
  come back "complicated" with the right reasoning; fringe claims with
  only word-matching studies come back "complicated" with nothing cited.
- `tests/test_app.py` (63 tests, fake provider): cache, rate limits, proxy
  handling, provider selection, every JSON-malformation path, the
  no-citation guard, broadened search, SSE stage order, trending,
  feedback, link-preview tags and images, share-target params, the prose
  gate, the still-open line through the cache, and the no-dash rule on
  the rendered page.
- The UI at phone width in light and dark: streamed progress, stamp,
  banner, study sheet, share card, feedback, trending, recent, install
  nudge, deep links, service worker (network-first, offline fallback).

Re-run `scripts/live_batch.py` after any prompt change. Verdict quality
on real claims is the one thing tests can't cover. For a score, run
`scripts/regression.py`: the 20 audit claims in
`tests/regression_claims.json`, each against the verdict it should get,
30 seconds apart (about 15 minutes of free-tier quota).

## Not medical advice

The app says so on every screen. It summarizes published abstracts with an
AI model and can be wrong; it's a research-literacy tool, not a clinician.
