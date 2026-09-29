# PRODUCTION.md: the fly-through

Production record for the `video-flythrough` branch. Route, prompts, job
IDs, repairs and credits. Written as work happens, not afterwards.

## Status

| Phase | State |
|---|---|
| DECISIONS reversal entry | done, `b4a2d93` |
| Style tile | done, `0def8d2` |
| Visual Story approved | **done, 2026-09-29** (Dhruv, with Scene 04 change) |
| Scroll pacing plan | done, this document + `static/flight/beats.json` |
| Start-still options | **not started, awaiting approval to spend** |
| Clips A/B/C | not started |
| Frame extraction | not started |
| **Credits spent** | **0** |

## The approved route

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
| 04b **The open drawer** | Camera slows and dips to a single card drawer standing open, index cards blank, the gap behind them dark. Lifts away and turns for the rear door. | "And every verdict says what is still open." |
| — transition | Out the rear door into a courtyard, still moving, yaws 180° to face the building. | none |
| 05 Reveal | Flies backwards and climbs. The whole library revealed in its campus: paths, a street with traffic, neighbouring buildings. | "Likely true. Likely false. It's complicated. And what's still open." + the claim input again |

**Why the drawer earns its beat.** `still_open` is the one field this
product requires on every verdict and the thing that separates it from
every other checker. An open drawer with blank cards is the only moment in
the flight that says "there is more here that nobody has filled in" without
asserting any finding. It is also the cheapest possible shot: one static
object, no motion inside the frame.

## Imagery constraints, enforced in every prompt

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

## Credits

Checked after every job. Nothing spent yet.

| Job | Purpose | Credits | Balance after |
|---|---|---|---|
| — | — | — | — |
