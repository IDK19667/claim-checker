# DECISIONS.md: Evident

Decisions and the reason behind them, newest first. If a decision is
reversed, say so here rather than deleting the entry. `DESIGN.md` holds
the visual system; this holds why.

## 2026-09-29 (latest): "From noise to clarity", four clips, no scrim

**Round 2's shipped fly-through (phone/archive/lab/verdict, a full-frame
scrim behind white text) read as "too blurry", "fades when text appears",
and "doesn't feel like a fly-through".** All three complaints trace to the
same two causes: 1280px frames at WebP quality 72 upscaling visibly on a
normal desktop screen, and a heavy scrim (`rgba(11,18,38,0.86)` at its
darkest) that dimmed the footage itself every time a chapter of text
needed to be legible. Given explicit creative freedom to redesign the
concept rather than patch it, both causes were removed rather than eased.

**The concept changes to "from noise to clarity", still one stage per real
step of the pipeline:** city rooftops (surface: the noise a claim starts
as, hero and input) leads into rising over a coastline (stepping back: the
real PubMed query appears, before any study has arrived) leads into
churning water (weighing: studies stack in, get graded, and form the
evidence bar, all three continuous through one stage rather than split
across two) leads into an open sea horizon (clarity, plus what is still
unknown: the verdict, the still-open line, the input again). Clips:
Surface = Pexels 2248532, Rising = 5619876, Weighing = 7666608, Horizon =
9209847. 13992029, a Horizon candidate, was rejected for an emblem in
frame whose clearance could not be confirmed. Full sourcing, motion
scores and the rest of the rejection list are in `media/PRODUCTION.md`.

**Every candidate this round was motion-scored before download**
(`scripts/flight_motion.py --clip`), not trusted by its Pixabay/Pexels
title. This mattered: most Pixabay results tagged "drone" or "aerial"
scored as static hover shots once measured, which is why the shipped set
is almost entirely Pexels. All four shipped clips read as sustained motion
90-100% of their length, against round 2's one measured real camera move
(the archive tilt) in three clips. Every stage now gets its own
text-then-text-free beat pair rather than the one stage that earned it
last round.

**The scrim is gone, not reduced.** Footage stays at full brightness for
the entire flight; the only darkening anywhere is the master's final 1s
drain to the site's deep field colour, after all four stages have played,
which is an exit into the next section rather than a contrast aid. Text
moved into small, solid, paper-backed panels (`.flight-chip`) anchored to
a corner or the lower edge: a headline chip top-left, the work panel
bottom-left, the header itself now two small chips instead of a
full-width gradient band. Contrast comes from the panel (the same
paper-on-ink pair used sitewide, so it is unaffected by whatever colour
the footage under it happens to be), never from dimming the shot.

**Frames ship at 1920px wide, quality 72, not the literal round-3 quality
ask (85-90).** Measured directly: this footage, being real and unblurred,
costs 3-4x more per WebP frame than round 2's clips did at identical
settings, and the literal 1920/quality-88 ask measured 139MB for a 37s
sequence, unfit for a mobile-first PWA that has to work under Save-Data.
A first pass shipped 1440px/quality-76 instead, which turned out to still
read soft at true DPR2 resolution: a DPR2 screen needs roughly 2x its CSS
width in source pixels to avoid visible upscale, so 1440px was thinner
margin over a 1280-CSS-px desktop than round 2's 1280px build had at DPR1.
1920px (Rising and Weighing's real ceiling; Surface and Horizon are native
4K) cuts that upscale ratio to ~1.33x, which is what actually fixes it;
quality moved back to round 2's own 72 to make room, landing at 56.2MB.
Full measurement table, including a render-backend correction along the
way (a Playwright screenshot in this sandbox runs on software SwiftShader
and blurs noticeably worse than the real GPU a reader's browser uses, so
sharpness here was ultimately judged on the Browser pane / a real device,
not a Playwright screenshot), is in `media/PRODUCTION.md`. fps moved (12
to 9) to help make budget, since it was never part of the ask and was the
one lever that did not cost visible sharpness.

**What does not change.** Every non-negotiable from the branch's prior
entries: no faces, no readable text, logo or signage in any clip; the
claim input usable on the first screen without scrolling; "Skip to the
checker" always reachable; verdicts never colour-coded; `still_open`
rendered on every verdict; the reduced-motion/no-sequence fallback (now
four new stills matching this concept) works standalone. `tests/test_flight.py`
(34/34) and `tests/test_app.py` (135/135) pass; `node scripts/qa.mjs`
passes; `static/sw.js` bumped to v37.

## 2026-09-29 (later still): Renamed to Evident, on the video-flythrough branch

**"Claim Checker" becomes "Evident"** across every user-facing surface (page
titles, the header wordmark, the OG/ClaimReview metadata, `llms.txt`, the
PWA manifest) and the working docs that describe the shipped product
(`README.md`, `DESIGN.md`, `SESSION_NOTES.md`, `CLAUDE.md`). New tagline:
"Health claims, checked against the evidence."

**Why not "Weighed", which was the first choice.** Checked before writing
a single line: `weighed.app` resolves to a live marketing site titled
"Weighed", which is the App Store page for an existing iOS app that does
exactly this kind of thing in a neighbouring space, weight and body-fat
tracking with Apple Health integration. That is not a parked domain or a
coincidental partial match, it is an active health product using the
exact name, which is precisely the "clearly taken in health" condition
the fallback plan was written to catch.

**"Evident" is not spotless either, and that is worth saying plainly.**
`evidentapp.com` is a live self-tracking app (sleep, activity, wellbeing,
productivity, reading from Apple Health). The collision is weaker than
Weighed's: a different top-level domain, a common English word rather
than an invented one, and a self-tracking app is a further conceptual
distance from a claim-verification tool than another weight tracker was
from a health product broadly. Recorded here rather than glossed over, so
the choice is understood as "clearly better, not provably clean."

**What does not change.** The repository folder name, the Render service,
any deployed domain, and every dated entry above this one: those describe
what was actually true at the time and are left as they read. Only this
document's own title line was brought current, on the same reasoning as
`README.md`'s and `CLAUDE.md`'s.


## 2026-09-29: Second reversal within the same branch — real stock footage, not generated imagery

**This reverses the 2026-09-28 entry above, on the same branch, before any
credits were spent.** The Higgsfield route was approved, a style tile and a
scroll pacing plan were built against it, and a Pexels API key was obtained
specifically to source comparison footage for the generated stills. The key
request was refused by Pexels ("new keys are paused"), which forced sourcing
real clips by hand instead, and having done that comparison Dhruv chose real
footage over generated: "find the clips yourself... the most cinematic,
realistic, high-quality footage."

**Why real footage answers the original objection better than a generated
one did.** The 2026-09-22 and 2026-09-24 rejections were never about motion
as such; they were about "pictures of health imply evidence the page does
not have" and "copying the surface without the assets is exactly what 'AI
slop' means." Generated imagery of a library that does not exist is still a
picture asserting something no PubMed record backs. Real footage of a real
phone, a real library and a real lab is not evidence either, but it does not
pretend to be a specific place tied to a specific claim: it is illustrative
by construction, not by disclaimer alone. The "Illustrative footage,
generated" caption becomes "Illustrative footage. A real check, replayed,"
naming the actual date of the actual cached check the animation replays,
which is a stronger and more literal honesty claim than the generated
version could make.

**What changes.** Three licensed stock clips replace the five-scene
generated library route: a phone in a dark room (Pixabay 169445), library
shelves (Pexels 18969594, carried over from the very first candidate round
and never beaten), and an automated lab instrument (Pixabay 76395). The
route drops from five scenes plus two transitions to four stages, one per
real step of the pipeline: phone, archive, lab, verdict. The overlay is no
longer decorative copy beside the footage; it is the real cached "apple
cider vinegar cures diabetes" check (checked 2026-09-22) animating in, with
its real query, real study titles and years, real evidence grades from
`evidence.py`, and its real verdict and still-open line. Sources and
licences for all three clips are recorded in `media/PRODUCTION.md`.

**What does not change.** Every constraint from both prior entries still
holds: no faces, no lab coats posing at camera, no pills, no readable
screens, logos, signage or text of any kind (checked at full resolution
before any clip was chosen, not assumed from a title); the claim input
usable on the first frame with "Skip to the checker" always visible;
verdicts never colour-coded; citations enforced in code; `still_open` on
every verdict; the offline-first, no-build-step, no-accounts rules
untouched.

**Credits spent: 0.** No generation tool was used. All three clips are free
for commercial use under their own licences (Pixabay Content License,
Pexels License), recorded with direct links in `media/PRODUCTION.md`.

## 2026-09-28 (later still): Reversal — a generated fly-through hero, on a branch

**This knowingly reverses prior decisions.** Generated hero imagery and
scroll-driven video were rejected here three times: 2026-09-22 ("no
scroll-driven video, film grain over the page... Motion that decorates
makes a credibility product read as a marketing page"), 2026-09-24 ("Do
not use a generated hero video, scroll-driven storytelling, or
AI-generated imagery... Copying the surface without the assets is exactly
what 'AI slop' means"), and 2026-09-26 twice over, the second time
specifically against this same source video: "Generated hero imagery and
video (Higgsfield) are rejected on the same grounds plus two of their
own: pictures of health imply evidence the page does not have, and a
video hero breaks the offline first paint."

Dhruv reversed this in a separate session on 2026-09-28, writing
`VIDEO_REDESIGN_BRIEF.md` and recording there: **"Owner decision (Dhruv,
2026-09-28): implement all of it."** That brief instructs this entry to be
written before any building starts, quoting that line, which is done here.

**What changes and what does not.** A scroll-scrubbed first-person
fly-through through a generated library building becomes the front-page
hero, replacing the plain masthead-and-claim-field opening. The three
things that made the earlier rejections correct are addressed head-on
rather than argued away: no medical imagery of any kind (no scrubs, no
pills, no stethoscopes, nothing that reads as clinical endorsement); no
legible text, journal titles or logos in any generated frame, so nothing
implies a specific finding; a visible caption reading "Illustrative
footage, generated," which keeps the credibility promise the earlier
entries were protecting. The claim input stays reachable on the first
frame, before any video loads, with a permanent "Skip to the checker".
Every other non-negotiable is unchanged: verdicts are never colour-coded,
citations are still enforced in code, `still_open` still ships everywhere,
nothing in the pipeline changes, and the privacy page stays true.

**Where this lives.** On a branch, `video-flythrough`, per the brief's
first rule: `main` is not touched and stays what ships until Dhruv reviews
both side by side on his phone and says which one goes live. The full
build plan, including hard imagery constraints, the scroll-pacing engine,
frame QC and repair steps, the mobile pass, and what "done" means, is
`VIDEO_REDESIGN_BRIEF.md` and is not repeated here. If the branch is
merged, this entry stays; if it is discarded, this entry stays too and
says why the attempt happened and what it cost.

## 2026-09-28 (later): The palette had two tells, and the numbers were too quiet

**What read as slop.** Two values were doing most of the damage. `#000000`
on `#ffffff` is the untuned default: it vibrates on screen, no premium
print or product uses it, and it is what a palette looks like when nobody
chose one. `#0b5fd0` was generic link blue, the most default accent
available. The greys under them were pure neutrals with no temperature, so
nothing in the ramp shared a hue with anything else.

**What changed.** Every neutral is now mixed from the deep field the
product already signs itself with: ink `#0e1422`, paper `#fafbfc`, and a
navy-tinted grey ramp. One hue family reads as decided rather than
assembled. The accent moved to a warm ochre `#8a4f13`, which is used only
for actions, the deep-dive control and the PubMed link, and never touches
a verdict. Contrast was computed before anything was applied: every pair
in use passes AA, ink on paper at 17.75:1.

**Hierarchy.** The takeaway was set at 16.5px, smaller than the source
titles beside it, which is backwards for the sentence a reader actually
leaves with. It is now 19 to 23px fluid at 700. The snapshot figures went
from 15px to 26px/900, so the numbers carry their rows instead of matching
their own labels. "Still open" is a non-negotiable and was the quietest
thing on the panel; its label now reads as a heading. The year span was
deliberately held back at 16px: it is context, not a count, and it should
not shout like one.

**Three things this turned up.**

The share card and the link-preview card carry their own copies of the
palette, in a canvas constant and in `og.py`, because neither sees the
stylesheet. Both were swapped by hand. A card that leaves the app looking
unlike the app is the drift that matters most here.

The QA gate compared the body background against a hardcoded
`rgb(255,255,255)`, written when paper was pure white, and fired on all six
views. The check's intent is that a dark system preference must not darken
the page, so it now reads the declared `--paper` token and compares against
that. A palette change can no longer leave it testing the wrong colour.

The trending and recent lists were rendering as bare browser buttons with
the claim and verdict run together. The field redesign restyled the
latest-checks list into `.entry` rows but left `.recent-btn`, which
`app.js` renders, with nothing but `font-family: inherit`. They now match.

## 2026-09-28: A dead end now owes the reader a next step

Verified the whole app first: 132 tests, the QA gate at both widths, the
detector, `design.md`, a live check on a claim that was not cached, the
deep dive, type-ahead, the 404 and every route. Load 71ms, CLS 0, 13
internal links with none broken. The one detector warning is on
`dialog.sheet`'s 4px top edge, which is a deliberate mark from the field
redesign and is left alone.

Then the real request: what to say when PubMed has no answer.

**What it did before.** One sentence in the source column, and nothing
else. Honest, but a dead end. Worse, the two ways a check can come back
empty were told the same way, and they are different facts: PubMed
matching no papers at all, and papers coming back that do not test the
claim. The quartz-pyramid test case is the second kind. Its search found a
paper about quartz *clocks*.

**What it does now** (`nextsteps.py`). A panel that names which emptiness
it is, says in one line that no evidence found is not the same as false,
and then offers only things that are true and checkable: the exact search
that ran with a link to run it, the same terms joined with OR instead of
AND (a search fails most often because every term had to match at once), a
plain-words hint, and claims already checked that sit near this one. That
last part is the useful one: someone asking about cold showers and
immunity, with no evidence for their phrasing, gets pointed at the cold
showers check that does have evidence.

**What it deliberately does not do.** It never proposes an answer, never
lists "possible answers", and never softens toward a verdict. The moment a
page has no evidence is exactly the moment guessing is most tempting and
most damaging, and non-negotiable #1 governs the empty state as much as a
full one. Everything in the panel is derived from the claim text and the
local cache, so an unanswerable claim also costs no model call.

## 2026-09-27: Every live check was failing, and blaming the network

Dhruv reported "Couldn't reach the server" on every claim. The message was
wrong twice over: the server was reachable, and the check had already
succeeded.

**What actually happened.** The streamed check returned 200, the verdict
parsed, and then `renderSnapshot` threw `Cannot set properties of null`.
The snapshot's three inner elements were rendered inside
`{% if result and result.evidence %}`, so they existed on a cached
`/?q=...` page and did not exist on the front page, which is where a live
check renders from. Every streamed check died there. Because
`renderResult` sat inside the same `try` as the fetch, the failure fell
into the network handler and told the reader to check their wifi.

This was mine, from the evidence-snapshot work on 2026-09-24, and it
survived because of how I tested it: always by loading a cached result
page, never by typing a claim on a cold front page. The server-rendered
path and the streamed path render the same region, and I only exercised
one of them.

**Three fixes.**

1. The snapshot containers are now always emitted, filled when a result
   exists and empty otherwise, so both renderers find them.
2. `renderResult` and `remember` moved into their own `try`. A fault after
   the verdict arrives is ours, not the network's, and now says so:
   "The verdict came back, but this page could not draw it." Sending
   someone to check their connection over our own bug is the kind of
   silent-failure dishonesty non-negotiable #4 exists to prevent.
3. A test reads every id that `app.js` writes `innerHTML` into and asserts
   each one exists on the cold front page. Verified by reintroducing the
   guard and watching it fail by name.

**Also found:** the running dev server had been serving a stale template,
so the first fix appeared not to work. Restarting it was the difference.
Worth remembering before debugging a template change that "did nothing".

## 2026-09-26 (later): Type-ahead, a real 404, and a shell that told the truth

Dhruv sent a premium-website checklist and asked for every aspect. Most of
it was written for a clinic: editorial photography of your providers, board
certifications, "medically reviewed by", trust badges, patient intake,
facility tours, insurance navigation. This product has no providers, no
facility, no affiliations and no patients, so those items could only have
been satisfied by inventing them, which is the one thing a claims checker
must never do. Several more were already committed decisions: no imagery,
no dark mode (2026-09-25), no personalization, no accounts. What was left
was real, and is now built.

**The shell was lying about itself.** The page preloaded three fonts the
stylesheet no longer references (librecaslon 400 and 700, archivo): 124KB
fetched at the highest priority on every visit, for faces nothing uses,
while the one face in use was not preloaded at all. Both templates also
still declared `theme-color: #faf9f6`, the retired cream, and
`color-scheme: light dark` after dark mode was deliberately removed, so a
phone painted its chrome the wrong colour and offered a scheme that does
not exist. The manifest carried the same stale pair. All corrected, and a
test now asserts the preload matches `@font-face` and that no dark scheme
is claimed.

**Type-ahead over claims already checked** (`suggest.py`, `/api/suggest`).
Two reasons, in this order: a cached claim answers instantly and for free
while a new one costs a model round trip against a free-tier quota, so
steering someone onto an existing answer is the cheapest good outcome the
app has; and real claims arrive misspelled, because people retype what
they half-remember. "turmaric inflamation" now finds the turmeric check.
Matching is `difflib` plus word overlap: deterministic, local, no model, no
network, no dependency. Literal hits and fuzzy ones are shown under
separate headings, because silently correcting a spelling and presenting
it as what the reader typed is a small lie in a product whose whole claim
is not lying.

Built as an ARIA combobox rather than a styled div: arrow keys walk it,
Escape closes it without clearing the field, Enter takes the highlighted
row or submits what was typed, and `aria-activedescendant` tracks the
selection. The keyboard highlight carries an inset rule as well as a tint,
since a tint alone is invisible to someone not using a mouse.

**The type-ahead exposed a hole in the QA gate.** The panel only exists
while someone is typing, so a page-load audit never saw it, and it shipped
with `.suggest-verdict` at **1.71:1** and the heading at **3.49:1**, both
failing AA badly. Found by measuring, not by looking. The gate now types a
known misspelling, opens the panel and audits it like any other surface
across all four view combinations; verified by deliberately reintroducing
the bad colour and watching it fail.

**A 404 that is a page.** There was no handler, so a stale link ended the
visit. It now offers the claim field, the latest checks and the medical
disclaimer, returns a real 404 status, and is `noindex`.

Measured after the work: 147ms to load, 196ms to first paint, **CLS 0**,
4 requests and 108KB, 12 internal links with none broken. 121 tests pass,
the QA gate passes, `design.md` lints 0/0.

**Not done, and why.** Scroll-driven storytelling and parallax are banned
here and were rejected three times already. Dark mode is a committed
decision as of yesterday. Hyper-personalization by region or previous
visits would break non-negotiable #7. Trust badges, awards and client
logos would be fabrication. Audio feedback is wrong for a credibility tool
read on a phone in public. A serif heading face contradicts the single
face this design settled on.

## 2026-09-26 (later): The field. Austerity was reading as unfinished

Dhruv linked the Metics Media "$10K websites" video and said the facts
panel still looked like AI slop. Its captions are still ungettable: four
routes tried this session, and `timedtext` now answers HTTP 200 with a
zero byte body, which is the proof of origin gate. The free skill the
description links is behind a Drive sign in. So the video itself was not
readable, but its description states the technique plainly: a generated
scrolling hero video, Higgsfield imagery, Hostinger. Two of those cannot
be done here at all, since there is no image or video generation in this
environment.

**The useful thing was not the video, it was the pattern in the
feedback.** Three designs were rejected as slop, and all three were
austere and typographic: the broadsheet, then black on white with rule
weight as hierarchy. I kept answering "looks like slop" with more
restraint. Restraint is not the opposite of slop; to a reader, an
unrelieved page of type reads as unfinished regardless of how carefully
the type is set. That is the fourth rejection's actual content and it
took four to hear it.

**The field.** A deep navy ground, `#0b1226`, carrying the front page's
hero and the report's head. It is identical on every verdict, so it
brands the product without tinting a conclusion. The claim goes to 34px
and the verdict to 32px, reversed out. The facts panel then **lifts onto
the field** by 14px with the only shadow in the design, which is what
gives the page depth rather than a flat stack of rules.

**The evidence field is the signature.** One mark per study: height is
the study design (strong full, moderate 62 percent, weak 34 percent,
retracted hatched), filled when the verdict leaned on it and outlined
when it did not, with "8 read / 3 relied on" beneath. This is the
richness the request was asking for, and it is honest richness: it is
imagery that **is** the evidence rather than imagery that implies
evidence, which is the standing objection to generated health pictures.
It required one server change, adding a `tier` to each study payload via
`evidence.classify`, so the Jinja and the JavaScript draw the same field
instead of each classifying for itself.

**One colour was added for actions**, `#0b5fd0`, on the deep dive control
and outbound links only. Never on evidence, never on a verdict. The
previous "no colour anywhere" rule is reversed on purpose and `DESIGN.md`
says so; the rule that matters, that no verdict is ever tinted, is
untouched.

Not done, and worth stating: no generated hero video, no generated
imagery. Not out of the earlier objection, which has been overruled, but
because this environment has no way to make them. If that is still wanted
it needs a real image source.

107 tests pass, QA passes at both widths under both system preferences
with the contrast checks green on the dark ground, `DESIGN.md` re-lints
at 0 and 0. The share card carries the field too, since the card is the
half of the product that leaves.

## 2026-09-26: The broadsheet was on the list. Redesigned as a facts panel

Dhruv, for the fourth time, that the app looked like AI slop, and this
time asked to restart the UI from a blank slate with the product
unchanged. Research first. Videos stayed unavailable, so this pass read
articles and primary sources instead, and one finding settled it.

**The broadsheet was not the escape from slop. It was two of the presets
stacked.** The `frontend-design` skill's own calibration names the three
looks AI clusters around: "(1) a warm cream background with a
high-contrast serif display", "(3) a broadsheet-style layout with
hairline rules, zero border-radius, and dense newspaper-like columns".
The shipped design was both at once: `#faf9f6` cream, Libre Caslon
display, hairlines, zero radius, justified columns. Kyle Chayka names the
same tells independently and adds "tracked out subheadings with letter
spacing", which was the Archivo small caps exactly. Every earlier entry
in this file defending that world as the considered alternative to slop
was wrong on the evidence. It read as considered because it is what the
model reaches for when asked to look considered.

Examine.com supplied the product half: they grade each claimed benefit
separately with effect size, direction and consistency rather than giving
one overall score. That is why the counted figures now sit in front of
the reader instead of inside a paragraph.

Three complete directions were built at phone width on a real cached
result and compared side by side, which is the only method that has ever
worked here. **A, the facts panel**, black on white, rule weight as
hierarchy, numbers dominant. **B, the bulletin**, one constant signal
colour and the verdict at poster scale. **C, the console**, the evidence
as a real table. A was chosen: it is native to health information, it is
the furthest from every named default, and it does the most direct work
on the actual complaint, because the panel carries the comprehension
instead of the prose.

**What the world is now.** One typeface, Libre Franklin 400 to 900, self
hosted as a single 29KB variable woff2. Pure white, true black, four
greys, still no accent colour and still no verdict tinted. Rule weight is
the grammar: 8px closes the panel head, 4px separates a group, 2px is a
border, 1px separates a line. Caps are uppercase at weight with **letter
spacing zero**, which is the single mannerism this design exists to
avoid. Zero radius survives, but as a consequence of the panel rather
than as the signature.

The verdict moved inside the panel. It used to be a full bleed band over
the report; it is now a row of the panel, because it is a reported fact
and the panel is what reports facts. The counted figures, the bar and
"Still open" sit with it, and the prose explanation moved below, outside.
`DESIGN.md` was rewritten rather than edited, which is what a redesign is
supposed to do to it.

**Five faults found by looking at the screenshots, not by the checks.**
`.band` is a layout wrapper around the masthead and the ask view, and I
read its name and styled it as an ink band, which turned the whole
document black and 16px too wide. The snapshot bar was `content-box` at
`width: 100%` with side padding, so it overflowed the panel by 6px. The
UA paints `<mark>` yellow, which put an accent colour on a page whose
first rule is that it has none. The nameplate wrapped onto two lines. The
hero's second line was an `<em>` rendering as a synthesised oblique at
weight 900, which is not a posture this family has. QA caught two of
those; the other three needed eyes.

Carried over unchanged: the security headers and the referrer fix, the
folded source groups, the symmetric sheet transition, the keyboard
handling, no dark mode, and every product rule. 107 tests pass, QA passes
at both widths under both system preferences, `DESIGN.md` re-lints at 0
and 0.

Two things left alone on purpose. The detector reports
`border-accent-on-rounded` on `dialog.sheet`; it is a false positive,
since `border-radius: 0` is set in the same rule two declarations above
the border. And roughly 1.2MB of Libre Caslon, Archivo and JetBrains font
files are now dead in `static/fonts/`; they are not referenced by any
code and are safe to delete, but this project has no version control, so
deleting them is Dhruv's call rather than mine.

## 2026-09-25: The sources fold, and the deep dive gets a real door

Dhruv: "thier isnet really a ddep dive button fix that", and the result
page "just looks like a lot of words on a page and it hard to comprhend",
asking for dropdowns and tabs to organise it. Both complaints were
correct, and measurable: the result for apple cider vinegar was 2941 CSS
pixels tall on a phone, and roughly 60 percent of that was eight dense
academic titles stacked in one undifferentiated run.

**The deep dive door was a caption.** It existed, from the 09-25 pass
that added it, but at 10px small caps over a hairline it read as a label
under each row rather than a control, and its only state change was to
darken on hover. On a phone there is no hover, so the one affordance it
had was invisible to most of its readers. It is now a box at rest, 11px
in a 1px ink border with real padding, filling with ink on hover and
focus. The row is still the button and the label is still `aria-hidden`,
so nothing changed for a screen reader.

**The column now folds in two.** `Relied on for the verdict`, open, and
`Read, not relied on`, closed, each a `<details>` with its count. Three
studies visible instead of eight takes the page from 2941 to 2189, and
expanding the second group returns it to 3187. This is the three-layer
idea finally applied inside layer 3: the studies the verdict actually
leaned on are the ones a reader wants, and the rest are the audit trail.

`<details>` rather than tabs, deliberately. Tabs would need JavaScript,
an ARIA tablist, and they take content out of the linear reading order.
`<details>` works with no JavaScript, is a real control before anyone
writes any, and **keeps every study in the HTML in both states**, which
the crawler and ClaimReview rule requires. A test asserts all three
fixture PMIDs are present while one group is folded.

Study numbers stay global rather than restarting inside each group,
because the explanation points at them by number: the apple cider vinegar
verdict cites "studies 1 and 2" and "Study 6", and those exact ordinals
now sit together in the relied-on group.

One self-inflicted bug worth recording: the new grouping code declared
`const cited` in `renderResult`, which already had one, so `app.js`
failed to parse and the entire front end went inert while the
server-rendered HTML kept looking fine. Nothing in the Python tests could
see it and the page looked correct in a screenshot. Found by reading the
browser console. `node --check static/app.js` is a one-second gate that
would have caught it, and it belongs in the loop before any browser
check.

107 tests pass (four new for the grouping), `qa.mjs` passes at both
widths under both system preferences, the detector is at zero and
`DESIGN.md` re-lints clean.

## 2026-09-25 (final): No dark mode

Dhruv: "dont do dark background". **This reverses the dark stock decision
of 2026-09-22**, which said dark mode was the same page printed on dark
stock, `#12120f` and `#eeece3`. That entry stands above as the record of
what was true then; this is what is true now.

The page is paper in every light, on every device. A reader whose system
is set to dark still gets `#faf9f6`, and `color-scheme: light` on `:root`
keeps the browser's own furniture, scrollbars and form controls, from
going dark around a light page, which is the ugly half-state this change
would otherwise have created.

It is a defensible call on its own terms, not only because it was asked
for. The whole design argument is that this is a printed record of
evidence rather than a dashboard, and paper is that argument. Dark mode
was the one place the world was allowed to become something else.

Removed: the `prefers-color-scheme: dark` block in `static/style.css`,
both dark `theme-color` meta tags, and 22 dark keys and component blocks
from the `DESIGN.md` frontmatter. The documented system drops from 25
colours to 15 and 47 components to 35, and re-lints at 0 errors and
0 warnings. The `og.py` share cards were already drawn on paper and
needed no change.

`qa.mjs` keeps its two dark rows rather than deleting them, which would
have removed the only place a regression could be caught. They are named
`dark-os` now and they check the opposite thing: with the browser set to
prefer dark, the body background must still be `rgb(250, 249, 246)`.
Proved by deliberately putting a dark block back, which produced
`background is not paper: rgb(18, 18, 15)` plus eleven contrast failures,
and passing again once reverted.

103 tests pass, QA passes at both widths under both system preferences,
the detector is at zero.

## 2026-09-25 (last): Premium is finish, not decoration

Dhruv asked for videos on building interactive apps, so the UI would feel
premium, plus "other cool things". The video route is now closed: YouTube
gates captions behind a proof of origin token and refuses every transcript
path, and the previous pass of ten produced one usable idea. A search for
interactive-app videos from the same channels returns the same catalogue
this file already refuses: hero video, component drops, animated
backgrounds, cursor effects.

So the want was answered directly instead. Reading the front end rather
than a tutorial, the app turned out to be in good shape already: race safe
deep dives, abort handling, focus managed on open, drag to dismiss,
reduced motion honoured everywhere. Two real gaps, both finish rather
than feature.

**Sheets slid in and then vanished.** `dialog.sheet[open]` carried an
entrance keyframe and nothing for leaving, so a sheet that rose over
260ms disappeared between frames. That asymmetry is the single loudest
tell of an unfinished build, and it was the thing most worth fixing. The
closed state now sits on the base rule with `@starting-style` supplying
the entry, so one transition runs in both directions, and `overlay` plus
`display` transition `allow-discrete` so the sheet is still painted on
the way out. Measured in the browser: 80ms after `close()`, the sheet is
at opacity 0.43 and still `display: block`, where before it was gone.
The backdrop fades with it.

Two things had to be handled for that to be real rather than nominal.
Above 720px the sheet is centred with a transform of its own, so its
closed and open transforms differ and both are declared. And drag to
dismiss writes an inline `translateY`, which a transition would smear, so
a `dragging` class turns easing off while a finger is down. That drag is
now also limited to the widths where the sheet is a bottom sheet: on a
tablet it was writing `translateY` over the centring transform and
knocking the sheet out of the middle, which was a pre-existing bug found
while touching this code.

**The source column did not answer the keyboard.** Arrow up and down now
walk between studies with focus visible on each, and `/` returns to the
claim box from anywhere outside a sheet or text field, with the previous
claim selected so the next types over it. Neither is announced on screen.
A tool whose whole job is reading a list of sources should let you read
it without a mouse, and this is the kind of thing that reads as expensive
without adding a single mark to the page.

What was declined, again and on the same grounds: entrance animations on
sections, animated backgrounds, cursor effects, generated hero media.
`DESIGN.md` gains a Keyboard section and an honest Motion section, which
had claimed "nothing else animates" while the sheet animated. 103 tests
pass, `qa.mjs` passes at both widths in both schemes with an empty
console, and the detector is at zero.

## 2026-09-25 (later): Ten more videos, and the headers they led to

Dhruv sent a Google video search for "how to make the best website with
claude" and asked for all of it implemented. Ten videos. The caption
route from the last pass is gone: YouTube now gates `timedtext` behind a
proof-of-origin token and returns 429 to yt-dlp on every player client,
and the watch page's own transcript panel no longer fills. Installing
`curl_cffi` for impersonation fixed metadata but not captions. What is
still readable is each video's title, length and chapter list, which for
tutorials of this kind is the argument in outline. That is what this
entry is based on, and it is a weaker source than the last pass had.

Eight of the ten are the same funnel already on the record. Two are the
same videos: Tommy Chryst's full course and Metics Media's ultimate
guide were both analysed on 2026-09-22. The rest sell Hostinger,
Higgsfield or a paid skill, and their chapter lists prescribe what this
project has already rejected in writing: a generated scrolling hero
video, AI imagery, 21st.dev component drops, brand scraping, cloning a
reference site. Reliablesoft's "pick your design direction (3 options)"
is the one good idea, and it is the method that produced the broadsheet
from four variants, already adopted.

**One chapter was new: AI Foundations ends its build with a security
health check.** Nothing in this repo had ever done one. It found three
things.

`FLASK_DEBUG` defaulted to `1` while `HOST` defaulted to `0.0.0.0`, so
`python app.py` with no environment served the Werkzeug debugger, which
is an interactive Python shell, to everyone on the Wi-Fi. Production runs
gunicorn and was never exposed; the development default was the bug. Now
off unless asked for.

**No response carried a security header.** There is now a
`Content-Security-Policy` on every response, with `default-src 'self'`
and no host allowed anywhere. This is worth more here than on most sites,
because it makes the browser enforce a rule the design already had:
fonts are self-hosted, there is no analytics and no CDN, so a third-party
request is always a mistake. The policy now rejects one instead of
trusting every future change to remember. Scripts are allowed by a
per-response nonce rather than `'unsafe-inline'`; the two inline blocks
are the ClaimReview JSON-LD and the server-rendered result, both of them
Jinja-escaped already. `style-src` still allows inline styles, because
the evidence bar's widths are real percentages written as style
attributes, and that is honest rather than lazy.

The nonce and the service worker get along: `cache.put` stores the whole
response, headers included, so the cached page offline is served with the
cached policy that matches it.

**`Referrer-Policy: no-referrer` is the privacy fix, not the security
one.** A result URL carries the claim in its query string. Every study
link goes to PubMed, and every one of them was sending that URL along as
the referrer. Non-negotiable #7 says nothing ties a check to a person and
the privacy page says so in as many words; this was the one place the
code did not keep the promise. Fixed for all outbound links at once.

Also `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`frame-ancestors 'none'`, `base-uri 'none'`, `object-src 'none'`, a
`Permissions-Policy` that refuses camera, microphone and location, and
HSTS sent only to requests that already arrived over HTTPS, so a plain
HTTP deployment cannot lock itself out.

Thirteen tests cover it, including that the nonce in the header is the
one in the page and that it changes per response. 103 pass. `qa.mjs`
passes at both widths in both schemes with no console output, which is
how a CSP failure would have announced itself.

What was rejected, again: the scrolling hero video and generated imagery
(Metics Media, Ferdy, Santrel), 21st.dev drops and Firecrawl brand
scraping (Tommy Chryst), and the whole "$10K website" frame. Tech With
Tim's video argues the others sell landing pages rather than
applications, which is fair, and describes a backend, auth and an admin
portal this app already has in the form it needs.

## 2026-09-25: The verdict is a filled band, and the deep dive has a door

Dhruv asked for the important things to stand out, naming two: the verdict
and the deep dive button. The second was the real finding, because there
was no deep dive button. Layer 3 had shipped the day before reachable only
by tapping a study row, with one line of fine print saying so. A feature
nobody can find is a feature that is not there.

**The verdict** was a 13px line of small caps between two hairlines, set
smaller than the takeaway underneath it. It is now a filled ink band, full
bleed to the gutter, with the words reversed out in paper at up to 21px.
`DESIGN.md` already allowed this: "the only filled shapes are the primary
button and the verdict's own ink." Every verdict is drawn identically, so
nothing is colour coded and no verdict looks more certain than another.
Weight carries the emphasis, which was the lesson from the reference pass:
the credible products lead with density, not hue.

Changed on all three surfaces in the same pass, because a verdict that
looks different depending on where you meet it is drift: the page, the
1200x630 link-preview card in `og.py`, and the square share card drawn in
`app.js`. The share card is a first-class surface per `PRODUCT.md`, and
half the value of this app is the screenshot that leaves it.

**The deep dive** now has an affordance on every study row: "Deep dive" in
small caps with a document-and-magnifier mark over a hairline, darkening
with the row on hover and focus. The row itself stays the control, so the
label is `aria-hidden` and never separately focusable; nesting a real
button inside an element with `role="button"` would have broken the
keyboard path that already worked. The row's own label changed from "Open
study 3" to "Deep dive on study 3", and the fine print now says what is
actually behind it: citations, free full text, and the studies next to it.
Related studies inside the sheet deliberately do not get the affordance,
since they are references rather than doors.

## 2026-09-24 (later still): The page now carries the work

Dhruv said the app still looked like AI slop, for the fifth time. The four
previous answers had all been surface: a new typeface, a new palette, a new
masthead. The actual diagnosis, finally: **it was an empty page.** A giant
headline, one input, a black button. That is what every AI-built app looks
like whatever font it is set in. The broadsheet typography had been
borrowed without the density that makes a broadsheet read as authority,
and the one genuinely distinctive thing the product does, reading medical
studies, was invisible until you typed something and waited.

Researched real references at full resolution rather than guessing, and
the pattern across all of them was the same: credibility comes from
showing quantified work, not from tasteful type.

| Reference | The move taken |
|---|---|
| Examine.com | The "Research Snapshot": references, participants, trials, meta-analyses as counts |
| Ground News | Every card carries a source count and a proportion bar |
| Our World in Data | Corpus counts under the search; cards led by data graphics, not photos |
| Stripe | One live, absurdly precise real number ("Global GDP running on Stripe: 1.72133878%") |
| The Pudding | Issue numbers, so an archive has a spine |
| Apple | The hero is the thing itself at full scale |
| Awwwards winners | Rejected: that look is bought with photography, film and 3D. Copying the surface without the assets is the slop |
| Cochrane | Rejected: authority in the domain does not make the design worth copying |

What shipped, as three named layers (now written into `PRODUCT.md`):

**Layer 1, the front page.** A ledger of real counts (studies read, claims
checked, pooled analyses) and the latest checks, server rendered, each with
its issue number, verdict, evidence bar and a meta line of real figures.
Apple's lesson applied literally: the hero is the evidence, because the
evidence is the product.

**Layer 2, the evidence snapshot** above the source column: read, relied
on, pooled, trials, registered, retracted, year range, and the mix as a
bar. Computed in `evidence.py` from PubMed's curated publication types, so
it is a fact about the record and not an opinion about it.

**Layer 3, the deep dive**, which `PRODUCT.md` had listed as phase 2 since
the pivot. `/api/study/<pmid>` returns how often a paper has been cited,
free full text where PMC has it, and the studies PubMed puts next to it.
All three come from elink, which is free and needs no key. It degrades to
the abstract on any failure and says so rather than rendering a zero.

Decisions inside the decision:

- **The bar is ink only.** Verdicts are not colour coded here and neither
  is the evidence behind them. A retracted paper is a 135deg hatch rather
  than a red segment: it survives greyscale, printing and colour blindness,
  and it never implies a verdict the studies do not support.
- **A retracted paper is its own tier**, never folded into "weak". A
  withdrawn paper is a different fact from a thin one. "Vaccines cause
  autism" now shows a hatched segment on the front page, which is the
  literature being honest about itself.
- **The evidence table lives in two languages** (`evidence.py` for
  anything server rendered, `static/app.js` for the streaming path). A test
  parses the table out of `app.js` and fails if the two ever disagree,
  because silent drift would grade the same study differently depending on
  whether you streamed it or loaded it from cache. The test was checked by
  deliberately breaking it.
- **The masthead claim count stays deleted.** The ledger replaced it with
  three counts that are worth the row, and `db.ledger()` counts distinct
  PMIDs actually fetched, never attempts.

Two faults the QA gate caught that review had not: the year range was
coloured with `--rule-2`, a hairline token, at 2.02:1 against paper, and
the new evidence tokens were undocumented. Both fixed. The service worker
cache also had to be bumped twice: a stale `app.js` silently served the
old renderer and made the deep dive look intermittently broken.

## 2026-09-24 (later): The $10,000-website video, re-read in full

Re-read `snErQUyqwCU` end to end on request. It is a build-along for a
scroll-driven marketing site (coffee brand, perfume, clothing) using a
downloaded skill plus two paid connectors, Higgsfield for generated
imagery and video and Hostinger for hosting, both affiliate-linked. Its
central technique, a generated hero video that plays as the page scrolls,
is already banned here and the ban stands: this is a medical-claims tool,
and a cinematic scroll makes a credibility product read as an advert. Its
imagery step is also out: the app has no photography by design, generated
pictures of health would imply evidence that does not exist, and a video
hero would break the offline first paint and the phone load budget.

What was worth taking from it was its back half, getting the thing
online. That prompted the first real test of the production path: the app
had only ever run under the Flask dev server. Booted under gunicorn
exactly as the `Procfile` runs it, every route serves (pages, OG card,
service worker, manifest, robots, sitemap, llms.txt), the cached verdict
including its ClaimReview markup is in the HTML before any JavaScript,
and `scripts/qa.mjs` passes against the gunicorn instance.

One suspicion was checked and dismissed rather than "fixed": SQLite opens
with no WAL and no explicit busy timeout, which looks like a concurrency
bug. Twelve threads doing 144 interleaved writes and reads finished in
0.44s with zero lock errors, because the default five second busy timeout
absorbs it and the global rate limit caps the app at 12 checks a minute.
No change made. The note is here so the next pass does not re-litigate it.

## 2026-09-24: The front page, chosen from three

The broadsheet decided how the app reads; it did not decide how the front
page is built. Three structures were built as complete front pages on the
real product, with the real data, and compared at both widths:

| Treatment | What it was | Outcome |
|---|---|---|
| gazette | full ceremonial nameplate centred, date and count either side, double rule, everything centred beneath | rejected: spends the top third of a phone on ceremony before the reader can do anything |
| chronicle | nameplate between its metadata, headline left, trending in a 320px right rail on desktop | rejected: the rail splits attention between the input and other people's claims at the moment of deciding what to type |
| **record** | **small nameplate in the corner, one very large headline, claim field beside the headline on desktop** | **chosen** |

The Record won on the job rather than the look: the headline and the
claim field are both on the first screen at every width, and on desktop
they sit side by side, so the thing the visitor came to do is never below
the fold. It was also the only one whose nameplate needed no special
mobile rule. And a dateline and an issue-style nameplate are ceremony
borrowed from a paper that publishes daily; this app answers when asked.
The Record keeps the broadsheet's typography and rules without the
ritual.

Merged as the real stylesheet, same method as last time: `static/skins/`,
the `?skin=` parameter, `/lab` and `templates/lab.html` were all deleted,
and `scripts/qa.mjs` lost its `--skin` flag. The masthead rule dropped
from a 3px double to a 1px hairline, and the nameplate from 21px to 17px,
on every view. `DESIGN.md` was updated rather than replaced: this is a
refinement inside the committed world, not a new one. The hero ramp is
now 38px to 76px fluid (48px to 72px on wide screens).

Two faults were visible only in the screenshots, and both were data
dependent, which is why looking is part of the method: gazette and
chronicle reserved an empty grid column for "your recent checks", which
is hidden until a visitor has checked something, so a first-time visitor
saw a half-empty page; and the Record's nameplate broke across two lines
at 390px. The first is fixed with `auto-fit`, the second with
`white-space: nowrap` on the nameplate.

**Dropped with the other two:** the masthead claim count, and `db.totals()`
with it. The Record has no room for a number beside the nameplate at
phone width, and a count nobody sees is not worth a query. If it comes
back, it comes back as a real count, never an invented one.

## 2026-09-23: Built for crawlers and language models, and a QA gate

Six more videos. Most of their advice was already in place or already
rejected (scroll-driven hero video, component-library drop-ins, brand
scraping). Two things were genuinely missing.

**The site was invisible to anything that doesn't run JavaScript.** A
shared link returned an empty shell and filled itself in from an API call,
so Google, and every LLM that reads pages, saw nothing but a headline.
That matters more here than for most apps: people find this tool by
searching the claim itself. Now a cached result is rendered on the server,
with the verdict, the takeaway, the reasoning, what's still open and every
study with its PMID in the HTML. The page also carries schema.org
**ClaimReview** markup, rating and all, with each PubMed study as a
`citation` and `isBasedOn: true` on the ones the verdict actually relied
on. Plus `robots.txt` (naming the AI crawlers explicitly rather than
leaving them to guess), `sitemap.xml` of cached claims, `canonical` links,
and an `llms.txt` that states the verdict rules in plain language.

A side benefit worth more than the SEO: a shared link now paints
instantly, because the result is already in the page instead of costing an
API round trip.

Two honest caveats. Google's fact-check rich results require the publisher
to be approved, and an AI-written checker may never qualify; the markup is
still correct and useful to anything else that reads it. And the markup
declares the reviewer as software in its `author.description` rather than
implying a human fact-checker.

**QA became a script instead of my eyeballs.** `scripts/qa.mjs` drives a
real browser at two widths in both schemes and checks contrast, effective
tap areas, overflow, console errors, heading structure, and whether the
stylesheet still uses only colours written down in `DESIGN.md`. It found
four genuine faults on its first run: a 15px-tall "How it works" button,
and inline `Study 1` and "what does this mean?" links with 11px-tall tap
areas. Fixed by growing the hit areas invisibly rather than enlarging the
marks, which would have wrecked a justified column.

The check itself had to be fixed too: measuring `getBoundingClientRect`
said those links were still too small, because the grown area lives in a
pseudo-element. It now hit-tests outward from the centre with
`elementFromPoint`, which is what a thumb actually experiences. Verified
by deliberately breaking contrast and a tap target: 29 findings, exit 1,
clean again once reverted.

## 2026-09-22 (later): The broadsheet, chosen from four

Dhruv said the app still looked like AI slop, for the third time. It did.
Four design passes had all been single-shot generation from my own
defaults, and my defaults are the slop: big condensed caps (Bricolage),
an editorial serif (Newsreader), one bright accent used as a full-bleed
field, rounded bordered cards, a rotated CSS "stamp" pretending to be
ink. Every checklist passed because checklists measure tidiness.

The freeCodeCamp video prescribes the fix and I had skipped it: **build
several complete variants in parallel, run them side by side, look, keep
one, delete the rest.** Done properly this time. Four worlds, same claim,
same data, served at `?skin=` and compared at `/lab`:

| Variant | What it was | Outcome |
|---|---|---|
| current | the yellow/stamp/ticket world | rejected: the loudest of the four, and the only one that read as a landing page |
| answer | Archivo, no brand colour, answer-engine architecture (question, answer, numbered citations, sources) | rejected: the most usable, the least distinctive |
| instrument | JetBrains Mono, near-black, dense, acid lime | rejected: reads as a researcher's tool, not a tool for someone who just saw a claim on TikTok |
| **broadsheet** | **Libre Caslon Text + Archivo small caps, newsprint, zero accent colour, double rules, drop cap, justified column** | **chosen** |

The broadsheet won because it is the only one carrying no trend markers:
no accent colour to date it, nothing rounded, nothing borrowed from
current app fashion. For a product whose entire value is "should you
believe this", reading like a printed record of evidence *is* the design
argument.

Merged as the real stylesheet, not a skin: the other three were deleted
along with the `?skin=` parameter and `/lab`. Bricolage Grotesque,
Newsreader and IBM Plex Mono were removed from the repo entirely; the
share card, the link-preview card and the app icons were redrawn in the
new world; `DESIGN.md` was rewritten (a redesign replaces it on purpose)
and re-linted against the stylesheet.

One decision inside the decision worth naming: **verdicts are no longer
colour-coded.** True, false and complicated are the same ink, and the
banner plus the words carry the difference. A tool whose only product is
credibility should not tint its own conclusions, and it removes the last
place where a colour could imply more certainty than the evidence
supports.

## 2026-09-22: Research pass over twelve videos plus a design skill

Reviewed twelve YouTube videos on building non-slop apps with AI, plus
the `taste-skill` anti-slop rules, and applied what fits a credibility
tool. What was adopted:

- **"Answer first, then the gap"** (Profoundly's five-step framework:
  reader value first, definition of done, force a template, quality
  gates). The verdict now carries a required `still_open` line: one
  sentence naming what the studies don't settle. It appears on the
  ticket, in the share card and in the shared text. A verdict that
  hides its own limits is the exact failure mode this product exists to
  avoid, and "what would change this" is the thing a reader needs next.
- **A deterministic prose gate** (`verdict.tidy_prose`). Strips filler
  openers ("It's important to note that", "Overall,"), converts dashes
  used as punctuation to commas or full stops, closes the sentence, and
  caps length at a sentence boundary: 160 chars for the takeaway, 700
  for the explanation, 200 for the still-open line. Cheap, testable, and
  it runs on every verdict instead of a second model call.
- **Numbers over adjectives** in the prompt: when an abstract gives a
  sample size or effect size, use it. Live runs now say "over 1,200,000
  children" instead of "large studies".
- **Self-hosted fonts.** The latin woff2 subsets live in
  `static/fonts/woff2/` and are preloaded, so there is no third-party
  request, no flash of fallback type, and the PWA renders correctly
  offline on first launch. This also removes the last external
  connection from the page.
- **A real `/privacy` page**, linked in the footer and cached by the
  service worker. Needed for any future app-store listing, and honest
  about the one thing that matters here: free-tier Gemini may use
  submitted text to improve Google's products.
- **Browser surfaces themed**: scrollbars, `text-wrap: pretty`, tabular
  numerals on PMIDs, years and countdowns, and a focus response on the
  claim slip (the printed label darkens, the ruled lines deepen, the
  black ring thickens) instead of a browser outline.
- **Press states on every control**, not just the primary button.
- **A two-column result at ≥1024px**: the ticket sticks on the left, the
  study file scrolls on the right. A 640px column centred in a 1280px
  window was "responsive" but not designed, which is the specific
  complaint the $10,000-website video makes about AI output.
- **The install nudge stopped being a bordered card** and became a ruled
  section like the lists around it. Cards as a default container is an
  anti-pattern in both the Impeccable floor and the taste-skill.
- **Zero em-dashes and en-dashes** in any shipped string, enforced by a
  test on the rendered page. The taste-skill is right that it is the
  most reliable AI tell, and the gate keeps model prose in line too.
- **Context files**: `AGENTS.md` (the file the JS Mastery video is built
  around: role, stack, file map, the four-part prompt structure, the
  verification commands), `PRODUCT.md` (audience, magic moment,
  journeys, scope) and this file, plus a longer `CLAUDE.md`. Every video
  that produced good results started from a written spec and a decision
  log; every one that produced slop started from a one-line prompt.
- **`DESIGN.md` now carries design.md-standard frontmatter** (Google's
  open format, from the front-end-skills talk) and lints clean with
  `npx @google/design.md lint`: 0 errors, 0 warnings. The token values
  were diffed against `static/style.css` rather than written from
  memory, so the file is machine-checkable truth instead of a
  description. `design.md export` can emit them as CSS variables or
  Tailwind theme if this ever needs to hand off.

### The eight-point grade

The `$10,000 websites` video says to grade the build against its own
checklist and be honest. Done on 2026-09-22, at 375px and 1280px:

| Point | Grade | Reading |
|---|---|---|
| Point of view | strong | A committed world (internet claim / library verdict) that no other health site looks like. This is the thing that stops it reading as generated. |
| Typography | strong | Three voices with real jobs, a variable face used at two widths, self-hosted and preloaded. |
| Colour | strong | Five values total. One accent, used with conviction, never tinting a background by verdict. |
| Hierarchy | strong | The claim is the biggest thing, the stamp answers it, the takeaway carries the meaning, everything else recedes. |
| Imagery | **weak, on purpose** | There are no photographs anywhere. Stock imagery on a claim-checking tool would be decoration pretending to be evidence; the material here is paper grain, stamp ink and the highlighter. Accepted as a deliberate loss against this checklist. |
| Motion | strong | One authored moment (the stamp) with one echo (the highlighter sweep), plus the reading log during the wait. Reduced-motion honoured. |
| Mobile | strong | Designed at 375px first, with a sticky share bar phones get and desktop doesn't, and a genuinely different wide layout rather than a centred column. |
| The invisible finish | strong | No third-party requests, CLS 0, themed scrollbars and selection, tabular numerals, focus answered in the design's own language, offline on second visit. |

Seven strong, one deliberate weakness. The imagery point is the only one
that would change if this were a marketing page, and it isn't one.

Adopting the design.md standard had a side effect worth recording: the
Impeccable detector has drift rules that stay dormant while a project has
no documented palette, and switching them on surfaced 32 findings in CSS
that had scanned clean for days. Every one was real (three hard-coded
colours, three shadow literals, three radii and ten type sizes that
existed in the stylesheet but in no design system). The colours became
`--ink-on-band`, `--field-on-band`, `--paper-2/3`, `--scrim`,
`--shadow-toast` and `--shadow-sheet`; the rest were added to the
documented ramps. Both linters are at zero with the rules live. Two
values stay literal on purpose and are commented as such: the footer
band's black and the clear button's icon, which must not follow the
scheme because they sit on surfaces that don't.

Left alone on purpose: the OG link-preview card (1200x630) still shows
claim, stamp, takeaway and receipt without the still-open line. It is a
chat thumbnail read at a glance, and the landscape format has no room for
a fifth element. The square share card, which people actually read,
carries it.

What was deliberately rejected:

- **21st.dev component drops, animated backgrounds, typing effects,
  cursor halos, scroll-driven video, film grain over the page**
  (the short, and the $10,000-website video). Generated hero imagery and
  video (Higgsfield) are rejected on the same grounds plus two of their
  own: pictures of health imply evidence the page does not have, and a
  video hero breaks the offline first paint. This is a
  medical-claims tool. Motion that decorates makes a credibility product
  read as a marketing page, and the one authored moment (the stamp, then
  the highlighter) already carries the personality. `DESIGN.md` keeps
  the ban.
- **Cloning a successful app's layout** (the freeCodeCamp method's core
  move). Nothing in this category is worth cloning, and the committed
  world is the reason the app doesn't look generated.
- **Analytics (PostHog) and session replay.** Non-negotiable #6: nothing
  ties a check to a person. The thumbs up/down stays as the only signal.

## Earlier

- 2026-09-19 Third design pass ("do better"): yellow brand band, the
  checking scene, highlighter sweep, black footer, banded share cards.
- 2026-09-19 Second design pass: committed the stamped-ticket world after
  three macro variants; `DESIGN.md` written.
- 2026-09-16 Pivot from classroom tool to public PWA; Gemini free tier
  with per-IP limits and a 24h cache, because nobody is funding an API
  bill.
- 2026-09-14 Verdict integrity rules: citations map to the studies the
  model was shown; an uncited true/false is forced to "complicated".
