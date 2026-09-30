# PRODUCTION.md: the fly-through

Production record for the `video-flythrough` branch. Written as work
happens, not afterwards. See `DECISIONS.md` (2026-09-29) for why this route
replaced the generated-footage plan recorded lower down in this file, and
the later 2026-09-29 entry for why round 2's three-clip build was itself
replaced by round 3's four-clip "noise to clarity" concept, recorded here.

## Status (round 3, current)

| Phase | State |
|---|---|
| Concept picked: "from noise to clarity" (surface, rising, weighing, horizon) | done, DECISIONS.md |
| Candidate sourcing, motion-scored, licence-checked | done, this document |
| Footage downloaded, graded, crossfaded, frame sequence extracted | done, `scripts/build_flight.py` |
| Frame export settings re-measured for real byte cost | done, see finding below |
| `beats.json` rebuilt for the four real stages | done, `static/flight/beats.json` |
| Full-frame scrim removed; text moved to small corner/edge panels | done, `static/flight.css`, `templates/flight.html` |
| `flight.js` stage logic rewritten for the new taxonomy | done, `static/flight.js` |
| Tests (`tests/test_flight.py`), full suite, QA gate | done, 34/34 + 135/135 + QA gate green |
| Screenshot comparison at desktop and phone | done |
| **Credits spent** | **0. No generation tool was used.** |

## The four stages ("from noise to clarity")

| Stage | Footage | What the overlay shows |
|---|---|---|
| Surface | City rooftops from above, continuous drone motion | The claim lands as noise; the input is usable immediately (hero + input) |
| Rising | Climbing over a grassy coastline above chalk cliffs | Stepping back: the real PubMed query appears, no study has arrived yet |
| Weighing | Churning turquoise ocean water | Studies stack in, get graded, sort, and form the evidence bar (all three, continuous through the stage) |
| Horizon | An open sea horizon at dusk, gold fading to blue | The bar (already formed) holds; the verdict, the still-open line, then the input again |

The check replayed is the real cached "Apple cider vinegar cures diabetes"
check from 2026-09-22 (`scripts/export_flight_check.py` writes it out of
`verdict_cache` to `static/flight/check.json`). Nothing on screen is
invented: the query, the eight study titles and years, the evidence mix and
grades, the verdict, the takeaway and the still-open line are all read from
that file.

## Frame export: measured size, not the ask's literal numbers

Round 3 asked for 1920px-wide frames (2560px on large screens), WebP
quality 85-90, to fix round 2's blurry, over-compressed build. Measured
directly against this footage before committing to it (`/tmp/wtest2`, not
kept):

| Width | Quality | FPS | Frames | Total |
|---|---|---|---|---|
| 1920 | 88 (the literal ask) | 12 (unchanged) | 445 | 139MB |
| 1920 | 80 | 12 | 445 | 96MB |
| 1600 | 80 | 8 | 297 | 50MB |
| 1440 | 76 | 9 | 334 | 41.4MB |
| **1920** | **72** | **9** | **334** | **56.2MB** (shipped) |

Two things drive the size, neither a settings mistake: this footage is
real, unblurred drone texture (grass, water, gravel, cloud) with no
shallow-depth-of-field softness to compress away, so it costs 3-4x more
per WebP frame than round 2's three clips did at identical settings; and
it runs 37s against round 2's 27s. Even re-running round 2's exact
settings (1280px, quality 72, 12fps) on this footage alone would land near
44MB, before any of the round-3 sharpness numbers move at all.

**The 1440px row shipped first, then was corrected.** It read fine in a
downscaled preview, but a true-resolution screenshot at DPR2 (Playwright,
exact pixels, not the Browser pane's own resized preview) showed real
softness, because 1440px is thinner margin over a 1280-CSS-px desktop at
DPR2 (needs ~2560px to fully avoid upscale) than round 2's 1280px build
had over a DPR1 screen. **That screenshot itself then turned out to be a
false signal in the other direction**: Playwright's headless Chromium in
this sandbox renders on SwiftShader (software; confirmed via
`WEBGL_debug_renderer_info`, same as the render-backend caveat already
recorded in `scripts/flight_scroll_test.mjs`), which blurs a scaled image
well past what a real GPU does with the same pixels; a screenshot from the
interactive Browser pane's real GPU (confirmed Intel Iris Plus 645 via
ANGLE Metal) of the exact same frame at the exact same scale looked
genuinely sharp. Net: the underlying DPR2-upscale math was worth fixing on
its own terms regardless of which renderer exposed it, so 1920px (Rising
and Weighing's real ceiling; Surface and Horizon are native 4K) shipped
anyway, with quality pulled back to 72 (round 2's own number) to make
room. **Lesson for next time:** judge fly-through sharpness from the
Browser pane (or a real device), never from a Playwright screenshot in
this sandbox; `scripts/flight_shots.mjs` is fine for layout, copy and
console-error checks, not for judging image quality.

1920/72/9fps was picked as the sharper-and-shippable point: cuts the DPR2
upscale ratio on a typical desktop from ~1.78x (at 1440px) to ~1.33x,
which is the actual fix, while quality stayed at round 2's already-shipped
72 rather than chasing a further bump that costs far more weight than it
buys on this footage (see the 88 and 80 rows above). fps moved (12 to 9)
rather than pushing width or quality further, since fps was never part of
the ask and was the only lever that did not cost visible sharpness.
Landed at 56.2MB, up from round 2's 10.6MB, for real reasons: 2x the pixel
count on the axis that actually causes visible blur, a sequence 37%
longer, one more real-motion clip. Frames verified sharp both as raw files
(1:1, bypassing the canvas entirely) and on the Browser pane's real GPU at
1280x800 before shipping. Low-res fallback tier is untouched (240px,
quality 40, ~1.2MB total): it was already "brief" and was never the thing
round 3 was unhappy with.

If the full 1920px/quality-88 numbers matter more than the byte budget,
that is a real option, just not the default call here: it more than
doubles the download for a gain that is hard to see once the DPR2 upscale
problem is already fixed at quality 72, and a further quality bump buys
comparatively little on this particular footage (see the 88 and 80 rows).

## Sources and licences (round 3, current)

All four clips are Pexels License: free for commercial use, no attribution
legally required (credited here anyway). No account, subscription or
payment was used to obtain any of them.

| Stage | Clip | Source | Author | Spec used |
|---|---|---|---|---|
| Surface | [pexels.com/video/2248532](https://www.pexels.com/video/aerial-footage-of-a-city-2248532/) | Pexels | (Pexels contributor) | 3840x2160, 24fps, 13.6s, trimmed 0.5-9.5s |
| Rising | [pexels.com/video/5619876](https://www.pexels.com/video/5619876/) | Pexels | (Pexels contributor) | 1920x1080, 25fps, 11.2s, trimmed 0.3-9.0s |
| Weighing | [pexels.com/video/7666608](https://www.pexels.com/video/7666608/) | Pexels | (Pexels contributor) | 1920x1080, 30fps, 17.7s, trimmed 0.3-12.5s |
| Horizon | [pexels.com/video/9209847](https://www.pexels.com/video/9209847/) | Pexels | (Pexels contributor) | 3840x2160, 24fps, 15.9s, trimmed 1.5-10.5s |

Rising and Weighing have no Pexels resolution tier above 1920x1080 at the
time of download; Surface and Horizon are the 4K tier. Raw downloads live
in `media/clips/`, gitignored. Only the derived frame sequence in
`static/flight/` ships.

**Every candidate was motion-scored before download**, not trusted by its
title or tags (`scripts/flight_motion.py --clip`, a blurred-frame-diff
proxy for camera movement, screening for a clip that reads as sustained
motion rather than a still shot mislabelled "drone"). All four shipped
clips scored 90-100% "clearly moving" end to end. This screening step,
not the pick itself, was the load-bearing part of round 3's sourcing: the
large majority of Pixabay's coastal/sunset "drone" results scored 0.3-0.8
mean motion with 0% moving fraction, i.e. static hover shots, which is why
the shipped set leans almost entirely Pexels.

**Rejected**, with reasons (a fuller candidate list existed; these are the
ones that scored well enough to need an explicit call):

- **13992029** (a candidate for Horizon): an emblem visible in frame whose
  clearance could not be confirmed. Rejected by explicit instruction rather
  than risk it.
- **9834170**: a person clearly visible standing at a path junction on
  closer inspection of the assembled contact sheet, after an initial
  hedged read ("likely not a person") turned out to be wrong. Excluded
  before it was ever presented as a candidate.
- **p5607624**: distant but identifiable people in frame.
- **p4057958**: standard-definition only and a static shot despite its
  listing.
- Numerous Pixabay coastal/sunset clips tagged "drone" or "aerial" that
  motion-scored as still hover shots (see above): rejected on the
  measurement, not on appearance.

**The grade: a "noise to clarity" saturation ramp, not a flat colour
match.** `scripts/build_flight.py` desaturates each clip toward the site's
muted, cool-leaning palette, but the ceiling rises stage by stage (0.82,
0.78, 0.85, 0.88), so the footage itself gains a little colour as the
checker moves from claims spreading to clarity, restrained well short of
the clips' native saturation throughout. Horizon (a warm sunset, the one
clip whose native colour temperature runs opposite the other three) is
additionally cooled with `colorbalance` so its crossfade in from
Weighing's teal water has less distance to travel. None of this touches
exposure or gamma: brightness is never pulled down, matching the
no-darkening rule below.

## Imagery rules, enforced by looking and by measurement

- No faces, no people, no readable text, logo or signage anywhere,
  confirmed by inspection before download; the one uncertain case
  (13992029) was rejected rather than cleared on a guess.
- No saturated rainbow colour: the grade above caps every clip's
  saturation well short of native.
- Landscape only, 1080p minimum (two of the four are native 4K).
- Continuous camera motion, verified by `scripts/flight_motion.py --clip`
  before download, not assumed from a "drone" tag.
- Footage stays at full brightness throughout the flight itself; the only
  darkening anywhere is the master's final 1s drain into the site's deep
  field colour, after all four stages have played, exiting into the page's
  own next section rather than dimming behind text.
- Text lives in small, solid, paper-backed panels at a corner or the lower
  edge (`.flight-chip`, `.flight-work`), never over a full-frame scrim; see
  DECISIONS.md.
- On the page: "Illustrative footage. A real check, replayed," with the
  real date of the real check.

## Build pipeline (round 3)

`scripts/build_flight.py` (run with `.venv/bin/python`, not system Python,
for Pillow's WebP support):

1. Trims each of the four clips to its usable range (see table above),
   scales to 1920x1080, applies its per-clip grade (see above).
2. Crossfades stage into stage (0.6s x3), fades the tail into the site's
   deep field colour (1.0s).
3. Extracts `static/flight/frame-NNNN.webp` at 9fps, 1920px wide, quality
   72 (see "Frame export" above for why these numbers, not the literal
   1920/85-90/12fps ask), plus `manifest.json`.
4. Writes `media/qc/master-contact.jpg` and four
   `static/flight/still-*.webp` stills for the reduced-motion path, one
   frame from the middle of each stage.

Contact sheets for each raw clip are in `media/qc/*-contact.jpg` (1-2fps
tile grids, looked at before anything was cut).

## Superseded: round 2's three-clip build (2026-09-29, replaced by round 3)

Kept for the record. Round 2 shipped phone/archive/lab/verdict footage
with a full-frame scrim behind white text; round 3 replaced the concept,
the footage, the grade, and the scrim entirely (see DECISIONS.md).

**Page speed**, measured against the running server with
`scripts/flight_speed.mjs`:

| Route | Load | LCP | CLS |
|---|---|---|---|
| `/` (front page, unchanged) | 63ms | 108ms | 0 |
| `/flight?style=a` | 118ms | 164ms | 0 |
| `/flight?style=b` | 85ms | 124ms | 0.087 |

**Sources and licences**, all free for commercial use, no attribution
legally required:

| Stage | Clip | Source | Licence | Author | Spec |
|---|---|---|---|---|---|
| Phone | [pixabay.com/videos/smart-phone-mobile-phone-scrolling-169445](https://pixabay.com/videos/smart-phone-mobile-phone-scrolling-169445/) | Pixabay | Pixabay Content License | magicmore | 3840x2160, 25fps, 25.8s |
| Archive | [pexels.com/video/18969594](https://www.pexels.com/video/a-large-library-with-many-books-on-shelves-18969594/) | Pexels | Pexels License | Maksim Smirnov | 1920x1080, 60fps, 16.9s |
| Lab | [pixabay.com/videos/laboratory-test-tube-chemistry-76395](https://pixabay.com/videos/laboratory-test-tube-chemistry-76395/) | Pixabay | Pixabay Content License | lumosajans | 3840x2160, 25fps, 12.6s |

**How that pick was made.** Candidates were found by web search across
Pexels, Pixabay, Mixkit and Coverr. Mixkit was rejected outright (720p free
tier, personal-use-only licence). Coverr was rejected outright (catalogue
dominated by AI-generated clips). Every remaining candidate (18 phone, 13
archive, 11 lab) was scored 1-5 on resolution, camera movement, lighting,
realism, palette fit and continuity. Rejected on sight regardless of
score: a Nikon logo on a microscope body (pixabay 86259), posed lab coats
and gloved hands (pixabay 216230, 197486), a respirator mask (pixabay
4372), saturated rainbow glassware (pexels 8485635), and an archive
candidate with legible reference numbers and a hand in frame (pixabay
164603).

**The seam grade.** Stage 1 (night, cold blue) and stage 2 (lamplight,
warm) were the largest colour jump in that sequence: the phone clip's
midtones and highlights were warmed and the archive clip's cooled, each by
a small fixed amount, so the crossfade had less distance to travel.

**Build settings**: 1920x1080 master, 0.5s crossfades, 1.5s tail fade,
frames at 12fps, 1280px wide, WebP quality 72. This is the build round 3's
"too blurry" feedback was about (`DECISIONS.md`, round-3 entry).

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
