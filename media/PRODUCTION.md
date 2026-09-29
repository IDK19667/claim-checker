# PRODUCTION.md: the fly-through

Production record for the `video-flythrough` branch. Written as work
happens, not afterwards. See `DECISIONS.md` (2026-09-29) for why this route
replaced the generated-footage plan recorded lower down in this file.

## Status

| Phase | State |
|---|---|
| DECISIONS reversal entries | done, `b4a2d93` and the 2026-09-29 entry |
| Clip sourcing and scoring | done, this document |
| Footage downloaded, QC'd | done, `media/clips/`, contact sheets in `media/qc/` |
| Master built, graded, frame sequence extracted | done, `scripts/build_flight.py` |
| `beats.json` for the four real stages | done, `static/flight/beats.json` |
| Real check exported for the overlay | done, `scripts/export_flight_check.py` |
| Overlay styles A and B built | done, `static/flight.css`, `static/flight.js` |
| Screenshot comparison at desktop and phone | done, both styles clean at both sizes, awaiting Dhruv's pick |
| Tests (`tests/test_flight.py`), full suite, QA gate | done, 29/29 + 132/132 + QA gate green |
| Page speed measured (`scripts/flight_speed.mjs`) | done, see finding below |
| **Credits spent** | **0. No generation tool was used.** |

## The four stages

One per real step of the pipeline, not five scenes of invented geography.

| Stage | Footage | What the overlay shows |
|---|---|---|
| Phone | A hand scrolling a phone at night, screen unreadable | The real claim arrives with no source attached; the claim input is usable immediately |
| Archive | Library shelves, warm light, no people | The real PubMed query, a counter climbing to 8, the real study titles stacking in |
| Lab | An automated pipette head over sample tubes, cool light | Each real study gets its real evidence grade from `evidence.py`; the stack sorts, strongest at the bottom |
| Verdict | Fades into the site's deep field colour | The evidence bar, the real verdict word, the real takeaway, the real still-open line (a dashed card), then the claim input again |

The check replayed is the real cached "Apple cider vinegar cures diabetes"
check from 2026-09-22 (`scripts/export_flight_check.py` writes it out of
`verdict_cache` to `static/flight/check.json`). Nothing on screen is
invented: the query, the eight study titles and years, the evidence mix and
grades, the verdict, the takeaway and the still-open line are all read from
that file.

## Page speed

Measured against the running server with `scripts/flight_speed.mjs`.

| Route | Load | LCP | CLS |
|---|---|---|---|
| `/` (front page, unchanged) | 63ms | 108ms | 0 |
| `/flight?style=a` | 118ms | 164ms | 0 |
| `/flight?style=b` | 85ms | 124ms | **0.087** |

The front page matches its recorded baseline (71ms, CLS 0), confirming
nothing on this branch touches it. Style B's non-zero CLS traces to the
font swap (`librefranklin`, preloaded, `font-display: swap`) resizing its
boxed `.flight-work` and `.chapter` panels once the real font arrives,
where a fallback font wrapped a line differently. Style A has no such box
around its text, so the same swap reflows text in place with nothing
box-shaped to measure. A real, if small, cost specific to style B's
treatment, not a defect in the scroll engine.

## Sources and licences

All three clips are free for commercial use, no attribution legally
required (credited here anyway). No account, subscription or payment was
used to obtain any of them.

| Stage | Clip | Source | Licence | Author | Spec |
|---|---|---|---|---|---|
| Phone | [pixabay.com/videos/smart-phone-mobile-phone-scrolling-169445](https://pixabay.com/videos/smart-phone-mobile-phone-scrolling-169445/) | Pixabay | Pixabay Content License | magicmore | 3840x2160, 25fps, 25.8s |
| Archive | [pexels.com/video/18969594](https://www.pexels.com/video/a-large-library-with-many-books-on-shelves-18969594/) | Pexels | Pexels License | Maksim Smirnov | 1920x1080, 60fps, 16.9s |
| Lab | [pixabay.com/videos/laboratory-test-tube-chemistry-76395](https://pixabay.com/videos/laboratory-test-tube-chemistry-76395/) | Pixabay | Pixabay Content License | lumosajans | 3840x2160, 25fps, 12.6s |

Raw downloads live in `media/clips/`, gitignored (large binary, easily
re-fetched from the URLs above). Only the derived frame sequence in
`static/flight/` ships.

**How the pick was made.** For each stage, candidates were found by web
search across Pexels, Pixabay, Mixkit and Coverr, then narrowed:

- **Mixkit rejected outright.** Its free tier is 720p under the "Mixkit
  Restricted License", personal use only; commercial use needs an Envato
  Elements subscription. Fails the resolution floor and the free-commercial-
  use requirement at once.
- **Coverr rejected outright.** Current catalogue is dominated by
  AI-generated clips (93 of the clips on one search page were tagged
  `user-ai-generation`), which fails the realism rule regardless of
  resolution.
- Every remaining candidate (18 for phone, 13 for archive, 11 for lab) was
  downloaded and inspected as three frames (12%, 50%, 88%) at full
  resolution, then zoomed to 1:1 on anything that might carry text, a logo,
  or a face, before being scored 1-5 on resolution, camera movement,
  lighting, realism, palette fit and continuity with its neighbours.
- Rejected on sight regardless of score: a Nikon logo readable on a
  microscope body (pixabay 86259), lab coats and gloved hands posed at
  camera (pixabay 216230, 197486), a respirator mask (pixabay 4372), a
  rainbow of saturated glassware that would have undone the site's own
  never-colour-code-a-conclusion rule (pexels 8485635), and an archive
  candidate with legible reference numbers and a hand in frame (pixabay
  164603, scored but explicitly rejected by Dhruv for exactly this).

**The seam grade.** Stage 1 (night, cold blue) and stage 2 (lamplight, warm)
are the largest colour jump in the sequence. Rather than pick different
clips, `scripts/build_flight.py` lifts red in the phone clip's midtones and
highlights and lifts blue in the archive clip's, each by a small fixed
amount, so the half-second crossfade between them has less distance to
travel. See the comments in that file for why a symmetric warm/cool shift
was rejected (it pushes the whole stage toward teal instead of narrowing the
gap).

## Imagery rules, enforced by looking, not by prompt

Nothing here was generated, so there was no prompt to constrain. The same
constraints applied to candidate selection instead:

- No faces, no people in lab coats posing at camera, no pills.
- No readable screen content, app UI, logo, brand name or signage anywhere,
  confirmed by a 1:1 crop on every candidate that had anything to check.
- No saturated rainbow colour that would compete with the site's ink-only
  verdict rule.
- Landscape only, 1080p minimum (two of the three are 4K).
- On the page: "Illustrative footage. A real check, replayed," with the
  real date of the real check.

## Build pipeline

`scripts/build_flight.py` (run with `.venv/bin/python`, not system Python,
for Pillow's WebP support):

1. Trims each clip to its usable range, scales to 1920x1080, applies the
   per-clip grade.
2. Crossfades stage into stage (0.5s), fades the tail into the site's deep
   field colour (1.5s).
3. Extracts `static/flight/frame-NNNN.webp` at 12fps, 1280px wide, quality
   72, plus `manifest.json` (frame count, fps, dimensions, poster, total
   bytes).
4. Writes `media/qc/master-contact.jpg`, a whole-flight contact sheet, and
   four `static/flight/still-*.webp` stills for the reduced-motion path.

Contact sheets for each raw clip are in `media/qc/*-contact.jpg` (1-2fps
tile grids, looked at before anything was cut).

## Credits

| Job | Purpose | Credits | Balance after |
|---|---|---|---|
| — | No generation tool used | 0 | n/a |

---

## Superseded: the generated five-scene route (2026-09-28, reversed 2026-09-29)

Kept for the record rather than deleted, per `DECISIONS.md`'s own rule.
Nothing below this line was built; no credits were spent against it either.

### The approved route (superseded)

Owner approval, 2026-09-29: keep the aerial reveal as the ending, add an
open card-drawer moment in Scene 04, and pair it with a "what is still
open" line there.

| Scene | Visual story | Website copy |
|---|---|---|
| 01 Arrive | Dusk exterior of an old stone library, eye height. Camera arcs past a lamp post, lines up with the open double doors, glides in. | Hero: promise line, the claim input, "Skip to the checker" |
| 02 Reading room | Slows. Long banked sweep along oak tables with closed journals and green lamps. S-curve around a globe, yaws up to the stacks. | "We search the published research, not the internet." |
| — transition | Through a narrow gap between two tall shelves. | none |
| 03 The stacks | Weaves an aisle of bound volumes, turns right at the end, dips to look along a shelf edge. | "Up to eight real studies per claim. Every one linked." |
| 04a Archive room | Through a side door into a quiet room of card drawers and filing cabinets. Curves past them. | "Each study graded by what kind of evidence it is." |
| 04b The open drawer | Camera slows and dips to a single card drawer standing open, index cards blank, the gap behind them dark. Lifts away and turns for the rear door. | "And every verdict says what is still open." |
| — transition | Out the rear door into a courtyard, still moving, yaws 180° to face the building. | none |
| 05 Reveal | Flies backwards and climbs. The whole library revealed in its campus: paths, a street with traffic, neighbouring buildings. | "Likely true. Likely false. It's complicated. And what's still open." + the claim input again |

**Why the drawer earned its beat.** `still_open` is the one field this
product requires on every verdict and the thing that separates it from
every other checker. An open drawer with blank cards was the only moment in
the flight that said "there is more here that nobody has filled in" without
asserting any finding.

### Imagery constraints that would have governed every prompt (superseded)

- No people at all. No scrubs, lab coats, stethoscopes, pills, or anything
  that reads as clinical endorsement or "medically reviewed".
- No legible text anywhere: blank spines, no signage, no logos, no lettering
  on the index cards.
- Books and papers closed or blank. Nothing that looks like a finding.
- Never the words "drone", "quadcopter" or "UAV" in a prompt: the model
  draws one in frame. Say the camera itself flies and nothing flying is
  visible.
- Everything stationary. Nothing appears, disappears or changes.
- On the page: a caption reading "Illustrative footage, generated."
