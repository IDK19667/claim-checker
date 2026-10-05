# PRODUCT.md: Claim Checker

Durable product context. Read with `DESIGN.md` (how it looks) and
`CLAUDE.md` (how to work on it). Written 2026-09-22.

## What it is

A public web app: paste a health claim you saw online, get a verdict
backed by the actual studies on PubMed, with the studies shown. One page,
no accounts, free to use, installable as a PWA on iOS and Android.

## Who it's for

Someone who just read a health claim on TikTok, WhatsApp, or from a
relative, and wants to know whether it holds up before they believe it or
argue with it. Not a clinician, not a researcher. Phone in hand, maybe 40
seconds of patience, no medical vocabulary, and no interest in reading
eight abstracts themselves.

Secondary: the person they send the verdict to. Half the value of this
app is the screenshot that leaves it, so the share card is a first-class
surface, not an afterthought.

## The magic moment

Paste a claim, watch the desk work for a few seconds, and see the verdict
land as a banner under your own words, with a rule drawn under each source
it relied on. If a new user never sees that, the product failed.
Everything before it (the masthead, the claim field, the examples) exists
to get them there in one tap, and everything after it (the share bar, the
source sheets) exists because they got there.

## The three layers

A reader should be able to stop at any of them and not feel cheated.

1. **The verdict.** The banner, the takeaway, what is still open. Readable
   in about five seconds, and the only layer most people will ever use.
2. **The evidence.** The snapshot (read, relied on, pooled, trials,
   registered, years, and the mix as a bar) and the source column. This is
   where the claim stops being our word for it.
3. **The deep dive.** One study at a time: what kind of study it is, its
   abstract, how often it has been cited, free full text where there is
   any, what PubMed puts next to it, and who wrote it.

Layer 1 is never allowed to depend on layer 2 or 3 loading.

## The two journeys that matter

**Cold open.** Land on `/` → masthead, the headline and the claim field
side by side, the ledger of real counts beneath, and the latest checks
with the evidence behind each one → tap an
example or paste a claim → the claim is set as the headline and the wire
prints beneath it (search, PubMed query, N found, reading, weighing) →
the wire clears, the verdict banner wipes in, rules draw under the cited
sources → takeaway, explanation, "still open", the record sentence →
sticky "Share this verdict".
Everything in that path is one page and one tap; there is no signup, no
modal, and no paywall anywhere in the product.

**Shared link.** Someone opens `/?q=…` from a chat → the OG card already
showed them the verdict and the claim → the page runs the check (or
serves it from the 24h cache, usually instant) → same ticket → they
either share it onward or type their own claim.

## What it must never do

1. Never claim more certainty than the evidence supports. "It's
   complicated" is a correct, common answer, and so is "Not enough
   evidence" when nothing found tests the claim.
2. Never cite a study the model wasn't shown, and never render a
   confident verdict with nothing cited (enforced in code).
3. Never fail silently: every failure path returns a real message and a
   way out.
4. Never tie a check to a person: no accounts, no analytics, no cookies.
5. Never drop "Not medical advice" from a screen.

## Scope

In: the check, the studies with their abstracts and types, the evidence
snapshot and the deep dive, share cards, trending, the front page ledger,
on-device history, offline, install.
Out: classroom/teacher features, accounts, comments, ads, a feed,
personalization, anything that needs a user record.

Built 2026-09-24: the deep dive on a study (how often it has been cited,
free full text where it exists, the studies PubMed puts next to it,
registered data, contacting researchers). It loads on demand from
`/api/study/<pmid>` and is allowed to fail: the abstract above it is
always enough on its own.

## Constraints

- Free AI tier (Gemini) with per-IP rate limits and a 24-hour verdict
  cache; a viral claim must cost one model round trip, not thousands.
- Phone first, 375px is the design width. Works offline after one visit.
- One small Flask process, SQLite, no build step, no framework.
