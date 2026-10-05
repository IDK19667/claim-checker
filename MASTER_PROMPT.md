# Master prompt: Claim Checker

Paste everything below the line into a coding agent to rebuild this app
from nothing. It is written as instructions to that agent, not as notes to
yourself, so it can be pasted verbatim.

One honest caveat before you use it: this prompt encodes the *conclusions*
of the work, not the work. The visual world was chosen by building four
complete versions and picking one; the front page by building three more.
An agent handed the conclusions will produce something close on the first
try and will still need the same looking, fixing and arguing to get the
last 20%. Expect to iterate. Nothing below is a substitute for opening the
page and reading it.

---

Build **Claim Checker**: a public, mobile-first web app where someone
pastes a health claim they saw online and gets a verdict backed by the
actual studies on PubMed, with those studies shown. One page, no accounts,
free to use, installable as a PWA.

## Who it is for

Someone who just read a health claim on TikTok, WhatsApp, or from a
relative, and wants to know whether it holds up before they believe it or
argue with it. Not a clinician, not a researcher. Phone in hand, about 40
seconds of patience, no medical vocabulary, and no interest in reading
eight abstracts themselves.

Secondary audience: the person they send the verdict to. Half the value of
this app is the screenshot that leaves it, so the share card is a
first-class surface, not an afterthought.

## Stack

Flask, SQLite, vanilla JavaScript, no framework and no build step. The
absence of a build step is a feature: anyone can read the shipped file.
Ask before adding any dependency and say what it buys. Server-render every
result: the verdict, the reasoning and the studies must be in the HTML
before any JavaScript runs, so that crawlers and language models see them.

Files: `app.py` (routes, SSE stream, rate limits), `pubmed.py` (E-utilities
client), `verdict.py` (model provider abstraction, JSON schema, prose
gate), `evidence.py` (study classification and the snapshot figures),
`db.py` (SQLite cache and anonymous log), `og.py` (Pillow link-preview
cards), `templates/`, `static/app.js`, `static/style.css`, `static/sw.js`.

## How a check works

1. The user submits a claim, capped at 500 characters.
2. A model turns the claim into a PubMed search query. If the search
   returns nothing, drop the last `AND` term and retry once: models
   over-specify.
3. `esearch` then `efetch` against NCBI E-utilities
   (`https://eutils.ncbi.nlm.nih.gov/entrez/eutils`). No API key needed;
   the limit is 3 requests/second. Send a tool name and email as courtesy
   parameters. Fetch up to 8 studies with title, abstract, journal, year,
   authors, publication types and data-bank registrations.
4. A second model call weighs those studies and returns schema-constrained
   JSON: `verdict` (`true` | `false` | `complicated` | `insufficient`), `tldr`,
   `explanation`, `still_open`, and `cited_study_numbers`.
5. Map the cited numbers back to PMIDs. Cache the result in SQLite for 24
   hours, keyed on a normalised claim, and log the check anonymously with
   the full study metadata.
6. Stream the stages to the page over server-sent events so the user
   watches the work happen: searching, the query used, N found, reading,
   weighing.

## Non-negotiable rules

1. **Never claim more certainty than the evidence supports.** "It's
   complicated" is a correct and common answer. No prompt tuning may push
   toward confident true/false.
2. **A verdict cites only studies the model was actually shown.** A
   verdict with no citations is forced to "insufficient" (not enough
   evidence) **in code**, not
   by asking the model nicely. Keep both halves.
3. **Every verdict says what is still open.** `still_open` is required in
   the schema and rendered on the page, the share card and the shared
   text. A verdict that sounds finished is a lie about science.
4. **No check fails silently.** Every failure path returns a defined,
   non-crashing response with a way out. A malformed, refused or truncated
   model response degrades to "complicated", never to an error page.
5. **Keep full study metadata** flowing through the API and into storage.
   Later features depend on it and re-plumbing is expensive.
6. **"Not medical advice" stays visible on every screen.**
7. **Nothing ties a check to a person.** No accounts, no analytics, no
   cookies, no session replay. Write a privacy page that says so, and keep
   it true.

## The three layers

A reader should be able to stop at any layer and not feel cheated. Layer 1
must never depend on layers 2 or 3 loading.

**Layer 1, the verdict.** The banner, the takeaway, what is still open.
Readable in about five seconds, and the only layer most people will use.

**Layer 2, the evidence.** A snapshot computed from PubMed's own curated
publication types, never from the model's opinion: studies read, relied
on, pooled analyses, trials, registered, retracted, the year range, and
the mix of study designs drawn as a proportion bar. Then the source
column, with a rule drawn under the studies the verdict relied on.

**Layer 3, the deep dive.** One study at a time, loaded on demand from
`/api/study/<pmid>`, using NCBI's free `elink` service:

- `linkname=pubmed_pubmed_citedin` gives how many papers cite it
- `linkname=pubmed_pmc` gives a free full-text link when one exists
- `linkname=pubmed_pubmed` gives the studies PubMed puts next to it
  (exclude the paper itself, take about six)

Plus the abstract, what kind of study it is in plain English, its data-bank
registrations, and its authors. It must degrade to the abstract on any
failure and **say so** rather than rendering a zero: "no citations" and
"we could not reach PubMed" are different facts and must never look alike.

Give it a visible door. A row that happens to be clickable is not a
feature; put a labelled affordance on every study.

## Weighing the evidence

Classify every study by its PubMed publication types, first match winning.
Ignore `Journal Article`, `Research Support`, `English Abstract` and
`Introductory`: they describe the paperwork, not the study.

- **strong**: Meta-Analysis, Network Meta-Analysis, Systematic Review,
  Randomized Controlled Trial, Practice Guideline
- **retracted**: Retracted Publication. Its own tier, never folded into
  weak. A withdrawn paper is a different fact from a thin one.
- **weak**: Case Reports, Editorial, Comment, Letter, News
- **moderate**: Controlled Clinical Trial, Clinical Trial, Observational
  Study, Multicenter Study, Comparative Study, Review, and anything
  untyped. Unknown is never strong.

If this table has to exist in two languages (server and browser), write a
test that parses one out of the other and fails when they disagree, then
prove the test fails by deliberately breaking it.

## The look

A page from a paper of record. This world is decided. Refine it; do not
redesign it.

- **Two faces only.** Libre Caslon Text for everything the paper says,
  Archivo in letterspaced small caps for everything a reader scans.
  Self-host both as woff2 subsets: a Google Fonts link costs a third-party
  connection and breaks the offline first paint.
- **Palette.** Paper `#faf9f6`, ink `#14140f`, secondary ink `#44443c`,
  tertiary `#6f6f66`, rule `#d8d6cd`, field `#f2f0ea`. Dark mode is the
  same page printed on dark stock: `#12120f` paper, `#eeece3` ink,
  `#2a2a24` rules.
- **No accent colour anywhere, and verdicts are never colour-coded.** A
  tool whose only product is credibility must not tint its own
  conclusions. Green-for-true would imply certainty the studies do not
  support.
- **Nothing is rounded.** `border-radius: 0` everywhere, including modals.
- **Rules, not boxes.** Group with hairlines, space and section grammar.
  Cards are not a default container. No shadows, no glass, no gradients,
  no grain.
- **The verdict is a filled ink band**, full bleed to the gutter, words
  reversed out in paper. It is the densest mark on the screen because it
  is the one thing the reader came for. Every verdict is drawn identically
  on every surface: weight carries emphasis, never hue.
- **The evidence bar is ink only**: solid ink for strong, mid-grey for
  moderate, the rule colour for weak, and a 135° hatch for retracted. It
  survives greyscale, printing and colour blindness.
- **Motion: two moments only.** The verdict band wiping in, and the rules
  drawing under cited sources. No entrance animations on every section, no
  animated backgrounds, no cursor effects, no scroll hijacking. Respect
  `prefers-reduced-motion`.
- **Copy is product language.** Controls name their action; errors name
  the problem and the recovery. **Zero em-dashes or en-dashes in any
  string a user can see.** Commas, colons and full stops do the work.
  Apply the same rule to model output with a deterministic post-processor.

## The front page must carry the work

This is the single most important instruction here, and the one an AI will
get wrong by default.

Do not build a landing page. A giant headline, one input and a button is
what every AI-built app looks like, whatever typeface it is set in. The
page must show the product's actual work:

- A ledger of real counts in small type: studies read, claims checked,
  pooled analyses. Compute them from the database. **Never invent or round
  a number**; count distinct records actually fetched, not attempts.
- The latest checks, server-rendered, each with an issue number, the
  claim, the verdict, its evidence bar, and a line of real figures
  ("8 studies read, 3 relied on, 4 pooled, 2007 to 2025").

The hero is the evidence, because the evidence is the product.

## What not to do

Each of these is a mistake that was actually made and had to be undone:

- Do not reach for a generated hero video, scroll-driven storytelling, or
  AI-generated imagery. That look is bought with photography, film and 3D.
  Copying the surface without the assets is exactly what "AI slop" means.
  A medical-claims tool that reads like an advert has lost the argument.
- Do not colour-code verdicts, add an accent colour, or use emoji icons.
- Do not put a stat row, a gauge, a pill button, a glass panel or a
  testimonial section anywhere.
- Do not add analytics "just to see how it is used".
- Do not optimise against linters and call it design. Linters measure
  tidiness. Tidiness is not taste.
- Do not let a hidden section reserve an empty grid column. Use
  `auto-fit`, and check the empty state, which is what a first-time
  visitor sees.

## Verification, every time

- Tests that run with a fake provider and fake PubMed, no network, no key.
  Cover every endpoint, every fallback path, and every copy rule that
  matters.
- A real browser QA script that fails loudly on contrast below 4.5:1,
  tap targets hit-tested with `elementFromPoint` rather than measured,
  horizontal overflow, console errors, and drift between the stylesheet
  and the documented design tokens. Run it at phone and desktop widths in
  both colour schemes.
- Look at the screenshots. Every single flaw that mattered in this project
  was found by looking, not by a passing check.
- If you use a service worker, bump its cache version whenever a shell
  file changes, or you will serve stale JavaScript and think your new code
  is broken.

## Rate limits and cost

Assume a free model tier. Per-IP limits on checks, a global per-minute and
per-day cap, and a 24-hour verdict cache keyed on the normalised claim so
that a viral claim costs one model round trip rather than thousands.
Secrets live in a gitignored `.env` and are never printed.

---

**Adapting this.** If your tool builds React and Tailwind rather than
Flask (Lovable, v0, Bolt), keep everything above except the Stack section,
and replace it with your tool's defaults. The product rules, the three
layers, the look, the front-page instruction and the what-not-to-do list
are the parts worth keeping; they are what makes it this app rather than a
search box on an empty page.
