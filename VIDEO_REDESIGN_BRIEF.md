# VIDEO_REDESIGN_BRIEF.md: Fly-through redesign for Claim Checker

Written 2026-09-28 in a separate Cowork session, for the Claude Code session that
owns this repo. Source: Bart Slodyczka, "Build a $10K Website With Claude Opus 5.5"
(YouTube `_PtVROzu3_w`, 21:25), analysed from the full transcript and ~40 sampled
frames, plus his public repo `Barty-Bart/opus-55-10k-websites` (two prompts, README).

**Owner decision (Dhruv, 2026-09-28): implement all of it.** This knowingly reverses
the rejections recorded in DECISIONS.md (2026-09-22, 09-24, 09-25 x2). Record that
reversal as a new DECISIONS.md entry before starting, quoting this line.

---

## 0. Ground rules for this build

1. **Branch, don't touch main.** `git switch -c video-flythrough`. `main` stays
   deployable. Merge only when Dhruv says so after seeing both side by side.
2. **Techniques, not copies.** His repo has no licence (all rights reserved). Do not
   paste his prompt text, copy, layout, images or clips into this repo. Everything
   below is written fresh; generate every asset new.
3. **The product rules still hold.** Nothing here overrides: never overstate
   certainty; every verdict states what is still open; citations enforced in code;
   no fabricated credibility; no verdict colour-coding; no analytics; privacy page
   stays true. If a step below would break one of these, stop and say so.
4. **The checker must never be slower to reach.** The claim input is usable on the
   first frame, before a single video frame loads. "Skip to the checker" is always
   visible during the fly-through.
5. **Verify by looking.** Run `tests/test_app.py`, `scripts/qa.mjs`, the token lint
   and the detector after each phase, and look at the screenshots. Bump `CACHE` in
   `static/sw.js` on every shell change.

---

## 1. What the video contains (every element, mapped to this site)

| # | Element in the video | What it becomes here |
|---|---|---|
| 1 | Scroll-scrubbed first-person fly-through as the hero | Section 3: "the claim's journey" fly-through |
| 2 | One continuous take from 2–3 chained clips (image-to-video, then video-extension) | Same method, Section 4 |
| 3 | Start still + final aerial "reveal" still generated first | Same, Section 4 |
| 4 | Visual Story table (Scene / Visual story / Website copy) before any footage | Required, Section 3 |
| 5 | Scroll pacing plan in viewport heights, piecewise timeline | Required, Section 5 |
| 6 | Copy chapters that fade in, hold, fade out over the footage | Section 5 |
| 7 | Glass header over the footage, solid after it | Section 6 |
| 8 | Sticky image beside scrolling text | Section 7 |
| 9 | Cards that stack as you scroll | Section 7 (evidence cards) |
| 10 | Strong hover states on every interactive block | Section 7 |
| 11 | Primary action section that fits one viewport (his booking form) | The claim form, Section 7 |
| 12 | Reference-image redesign loop (his Pinterest step) | Section 8 |
| 13 | Style tile before build | Section 2 |
| 14 | Mobile pass: reuse footage, centre crop, per-beat focus, own pacing | Section 9 |
| 15 | Accessible hamburger menu, 44px targets, safe areas, anchor offsets | Section 9 |
| 16 | Frame QC: contact sheets, cut detection, first/last frame match | Section 4 |
| 17 | Repair instead of regenerate (trim, targeted edit, extend from clean tail) | Section 4 |
| 18 | Credit tracking (his run: 18 jobs, 553.5 credits; README: 600–700) | Section 10 |
| 19 | Production note / handoff | Section 10 |

His linked resources and what happens to each:
- **GitHub prompts:** techniques extracted into this brief (not copied).
- **Higgsfield:** the generation tool. This repo already has the higgsfield skills in
  `.agents/skills/`; the Higgsfield MCP must be connected and the account must have
  credits. Use Dhruv's own account; do not use or place the video's referral link.
- **Hostinger:** not used. Hosting stays Render per DEPLOY.md.
- **Coaching, X, channel membership:** not applicable to the site.

---

## 2. Style tile first

Before footage, build `static/styletile.html` (not linked from the site) showing the
current DESIGN.md tokens applied to: header in both states, hero chapter copy over a
dark frame, the claim input, a verdict band, an evidence card, a stacked-card
section, buttons, links. The fly-through must live inside the existing identity
(Libre Franklin, ink on paper, deep field, no accent tint on verdicts). Add any new
tokens to DESIGN.md first, then use them.

---

## 3. The flight: "the claim's journey"

The route must be a real, continuous physical space, and it must not imply evidence
that does not exist. Proposed route (adapt if a better one exists, but present the
table before generating anything):

| Scene | Visual story | Website copy |
|---|---|---|
| 01 Arrive | Dusk exterior of an old stone medical library, eye height; camera arcs past a lamp post and lines up with the open double doors, glides in. | Hero: the product promise in one line + the claim input + "Skip to the checker". |
| 02 Reading room | Slows; long banked sweep along oak tables with closed journals and green lamps; S-curve around a globe; yaws to look up at the stacks. | "We search the published research, not the internet." |
| Transition | Flies through a narrow gap between two tall shelves. | No copy. |
| 03 The stacks | Weaves an aisle of bound volumes, turns right at the end, dips to look along a shelf edge. | "Up to eight real studies per claim. Every one linked." |
| 04 The archive room | Through a side door into a quiet room of card drawers and filing cabinets; curves past; heads for a rear door open to the night. | "Each study graded by what kind of evidence it is." |
| Transition | Out the rear door into a courtyard, keeps moving, yaws 180° to face the building. | No copy. |
| 05 Reveal | Flies backwards and climbs; the whole library revealed in its campus: paths, street with traffic, neighbouring buildings. | "Likely true. Likely false. It's complicated. And what's still open." + claim input again. |

Hard constraints on imagery (health-specific, non-negotiable):
- **No people in scrubs or lab coats, no stethoscopes, no pills, no doctors** — nothing
  that reads as medical endorsement or "medically reviewed".
- **No legible text anywhere**: no journal titles, no signage, no logos. Prompt for
  "blank spines, no lettering".
- Books and papers stay closed or blank; nothing that looks like a specific finding.
- Add a one-line caption under the hero: "Illustrative footage, generated." It is
  true, and it keeps the credibility promise.

After the table, write the scroll pacing plan (Section 5) and wait for Dhruv's OK
before spending credits.

---

## 4. Generate and repair the footage

1. **Check the live model schema** first (durations, aspect, modes, reference roles).
   Use Dhruv's billing only; do not buy or change plans.
2. **Stills:** 2–3 options for the Scene 01 start frame (open doors, dusk). Dhruv
   picks. Then one high aerial "reveal" still of the same building from the rear,
   generated from the chosen start still so the architecture matches.
3. **Clips:** split into 3 segments of ≤15s.
   - A: image-to-video from the start still (Scenes 01–02).
   - B: video extension from A (transition + Scene 03).
   - C: video extension from B, with the reveal still as image reference (Scene 04,
     exit, 180° yaw, reverse climb).
4. **Prompt wording:** never write "drone", "quadcopter" or "UAV" (models draw one in
   frame). Say: one continuous first-person camera move, no cuts; the camera itself
   flies; nothing flying is visible. State that every object is stationary, nothing
   appears, disappears or changes. Give timed manoeuvres ("3–7s: slows, banks left,
   yaws right along the table"). Say what is visible through the next opening. No
   text, logos, signage, lettering.
5. **Inspect every clip with FFmpeg before the next:** contact sheet at 1–2 fps (look
   at it), `select='gt(scene,0.3)'` cut detection, zoom on doorways and the last 2s,
   compare last frame of each clip with first of the next. The video's own build
   shipped two defects (objects vanishing, a person appearing); do not repeat that.
6. **Repair, don't regenerate:** trim to clean frames and extend from the clean tail;
   for a small stray object, cut that stretch, run a text video-edit to remove it,
   splice back. No open-ended regeneration loops. Keep job IDs; a pending job is not
   a failed one.
7. **Stitch one master:** normalise (`scale=1920:1080:flags=lanczos,fps=24,
   format=yuv420p,setsar=1`); hard concat where extensions already match; 0.125s
   crossfade only where a repaired piece meets an original. Re-run cut detection and
   a whole-flight contact sheet.
8. **Frames:** `ffmpeg -i master.mp4 -an -vf "fps=20,scale=1440:-2:flags=lanczos"
   -c:v libwebp -quality 78 -start_number 0 static/flight/frame-%04d.webp`. Write
   `static/flight/manifest.json` (count, fps, size, pattern, poster, version for
   cache-busting). Check first, middle, last frames exist and render.

Put the master video and raw clips outside `static/` (e.g. `media/`, gitignored if
large); only the frame sequence and poster ship.

---

## 5. Scroll engine

- Pinned `<canvas>` stage, vanilla JS in `static/flight.js` (no framework, no build
  step, matching the repo's rule).
- **Piecewise timeline** in `static/flight/beats.json`: each beat has `vh` (scroll
  distance), `from`/`to` clip seconds or a single `hold` frame, the copy chapter id,
  and an optional mobile `vh` override and `focusX` (0–1) for the mobile crop.
- Pacing: hold the opening frame long enough to read the hero and use the input;
  give dense interior moves more distance; give the 180° yaw its own short beat;
  hold the final aerial.
- Copy chapters are real HTML over the canvas: fade in, hold, fade out inside their
  beat; first chapter visible at rest; last holds. Hidden chapters are `inert` (no
  clicks, not in tab order).
- Native scroll only, forwards and backwards. No scroll hijacking.
- Loading: poster (frame 0) immediately; fetch the needed frame first, prefetch in
  the scroll direction; cap concurrent requests (~6) and decoded bitmaps (~60);
  abort obsolete fetches on fast jumps; `close()` evicted ImageBitmaps; bounded
  retries; handle resize and DPR (cap 2).
- `prefers-reduced-motion`, Save-Data, or a failed load: no sequence fetch; show
  each chapter over its representative still in normal page flow.
- The page must make complete sense with the animation off.

---

## 6. Glass-to-solid header (desktop and mobile)

- Over the flight: transparent with a top-to-bottom gradient of the darkest brand
  colour, ~70% at the top edge fading to 0, no hard line. Logo, menu and action
  legible over the brightest frame (test the lightest scene, not frame 0).
- After the flight: ~92% opaque same colour, `backdrop-filter: blur(10px)` plus the
  `-webkit-` prefix, hairline bottom border.
- Switch when the pinned section's bottom reaches the header's bottom. Recompute on
  scroll (passive), resize, orientation change and load. One state class; transition
  only background, border and blur over ~300ms; never animate layout. Reverts on
  scroll up. Open mobile menu always solid. Reduced motion: instant switch.

---

## 7. Sections after the flight

Map his sections onto what this site already has, keeping the front page's real
content (ledger counts, recent verdicts with evidence) as the substance:

- **Sticky image + scrolling text:** "How a check works" — a still from the flight
  stays pinned on one side while the four steps (claim → search → grade → verdict)
  scroll past on the other. Steps describe the real pipeline from SESSION_NOTES §4.
- **Stacking cards:** recent real verdicts as cards that stack as you scroll (CSS
  `position: sticky` with increasing `top` offsets; no JS needed). Each card: claim,
  verdict word (ink only, never tinted), evidence bar, still-open line.
- **Hover states:** every card, link and button gets a considered hover and a
  visible `:focus-visible` equivalent.
- **Primary action in one viewport:** a closing section where the claim input, the
  submit button and the privacy line fit a single phone screen without scrolling.
- Keep the existing result, study sheet, share card and error states unchanged in
  behaviour.

---

## 8. Reference loop

Dhruv supplies 2–4 reference screenshots (Pinterest or real sites) in `/references`
with one line each on what to take. Extract only characteristics (spacing, type
scale, composition, motion); never copy branding or artwork. Show the change, then
cut anything that makes the page feel cheaper or louder than the footage — the
video's own second pass had to remove a scrolling banner for exactly this.

---

## 9. Mobile pass

- Reuse the desktop frames; do not generate new video.
- Canvas cover-fit: `scale = max(cw/fw, ch/fh)`, centred, backing store =
  viewport × DPR (cap 2), recompute on resize/orientation.
- Check the centre ~45% of every frame through the whole flight: doorways, the path
  ahead, the yaw and the final building must stay in crop. Where not, set that
  beat's `focusX` and ease between beats.
- Optional: a pre-cropped portrait sequence (`crop=ih*9/16:ih` with the same focus,
  never upscale); phones request only that set, desktops only landscape; cancel and
  release on breakpoint change.
- Mobile pacing: own `vh` per beat; shorter holds; copy anchored low over a
  bottom-up scrim, clear of the path ahead; one short heading per chapter during the
  flight.
- Persistent top bar outside the pinned section: symbol + wordmark left (one line),
  menu button right. Menu: accessible dialog, close button, Escape, focus trap and
  return, closes on link select, locks background scroll.
- 44×44px minimum targets, safe-area insets, `scroll-padding-top` = header + gap.
- Every section single column; no horizontal overflow anywhere.
- Validate at 360×740, 375×812, 390×844, 430×932, portrait and landscape, plus
  1280 desktop again after mobile changes.

---

## 10. Done means

- Tests 132/132 (plus new tests for `beats.json` schema and manifest integrity),
  QA gate green at 390 and 1280, token lint and detector clean, zero console errors,
  no 404s for frames.
- Measured, not promised: frame-sequence total bytes, first-frame time, LCP and CLS
  on the front page before and after. If LCP or CLS gets materially worse than
  `main`, say so plainly.
- Production note in `media/PRODUCTION.md`: route, prompts, job IDs, repairs, credits
  used (check balance after each job; extensions can cost more than the estimate).
- DESIGN.md and DECISIONS.md updated. SESSION_NOTES.md gains a "video-flythrough
  branch" section.
- Dhruv reviews `main` and the branch side by side on his phone before any merge.
