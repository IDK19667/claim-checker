# Stage 2 "Searching the research" — candidate sourcing report

Shot brief: gliding along research shelves, an archive, or a room of screens
pulling up data. True 4K+, genuinely sharp, cinematic camera motion, no
readable text/logos in focus, no people as the subject, landscape,
Pexels/Pixabay/CC0/public-domain licence only.

## Government sources: ruled out

Checked first, per the brief. NIH b-roll is campus exteriors only. NLM's
digitized archive footage is pre-1950s film, wrong era and resolution
entirely. NIST has genuine lab b-roll (Boulder Labs reel) but it is
physics/metrology content, not library/archive shelving, so it reads wrong
for this specific shot even where usable elsewhere. Nothing downloaded from
government sources for this stage.

## Candidates (Pexels; all verified 3840x2160 via ffprobe on the actual file)

| Candidate | Resolution | Sharpness (Laplacian var, focal crop) | Motion (mean / min / moving frac) | Source | Licence | Verdict |
|---|---|---|---|---|---|---|
| `pexels-854417-video-inside-library` | 3840x2160 | 20-27 | mean 8.36 / min 4.50 / 100% | pexels.com/video/video-inside-a-library-854417 | Pexels License | **KEEP - top pick.** Academic library corridor push, floor-to-ceiling shelving, zero people, sustained camera glide the whole clip, consistently sharp. |
| `pexels-34345704-modern-library-glass-elevator` | 3840x2160 | 56-79 (sharpest of the set) | 88% moving | pexels.com/video/... | Pexels License | KEEP - 2nd. Glass-elevator atrium shelving, genuine gimbal/crane move. Short (6s) with two small incidental out-of-focus people. |
| `pexels-14543425-walking-modern-library` | 3840x2160 | 5.6-... (collapses at speed) | 100% moving, mean 12.72 (fastest) | pexels.com/video/... | Pexels License | KEEP - 3rd. Fastest, most energetic glide, but a children's-library aisle (bright colours, holiday decor) and produces real motion blur at high speed. |
| `pexels-6550428-card-catalog-library` | 3840x2160 | not fully scored | 0% moving (static) | pexels.com/video/... | Pexels License | REJECT (for this brief) - best content match of the whole search (symmetric card-catalog aisle, no people) but completely static, no camera motion at all. Worth reconsidering only if a locked shot becomes acceptable. |
| `pexels-5028622-server-room` | 3840x2160 | not scored | static | pexels.com/video/... | Pexels License | REJECT - static, readable "Samsung" logo in frame. |
| `pexels-6281210-distant-people-bookshelf` | 3840x2160 | not scored | static | pexels.com/video/... | Pexels License | REJECT - static, readable book-spine text in sharp focus, people present. |
| `pexels-8869625-archive-handing-books` | 3840x2160 | not scored | - | pexels.com/video/... | Pexels License | REJECT - two men are the clear subject, soft focus. |
| `pexels-6279431-books-on-shelves-closeup` | **2560x1440 actual** (page claimed 3840x2160) | not scored | - | pexels.com/video/... | Pexels License | REJECT - Pexels metadata mislabeling, caught only by verifying the real downloaded file with ffprobe; fails the 4K floor. |
| `pexels-34811326-students-studying-aerial` | 3840x2160 | not scored | static | pexels.com/video/... | Pexels License | REJECT - despite the name, a locked static shot; people-centric. |

Also rejected pre-download from page metadata: two "library aisle" clips
capped at 1080x1920 portrait (no 4K rendition exists), one "baroque
library" clip with 4K only in portrait orientation.

## Top picks, ranked

1. **`pexels-854417-video-inside-library.mp4`** (kept) — clears all three
   bars at once: archive content, real sustained camera glide, true sharp
   4K. Closest thing to a clean pass across all three stages sourced this
   round.
2. **`pexels-34345704-modern-library-glass-elevator.mp4`** (kept) —
   sharpest of the set, real gimbal/crane move, but short and has two
   small incidental people (out of focus, not the subject — should be
   fine against the content rule, flagging for the record).
3. **`pexels-14543425-walking-modern-library.mp4`** (kept) — most energy,
   but wrong room (children's section) and loses sharpness at speed.

## Honest verdict

**Yes, barely.** Unlike stages 1 and 3, this shot has one candidate
(`pexels-854417`) that is simultaneously archive/library content, genuinely
camera-moving, and true sharp 4K, with no readable text or people problem.
The margin is not huge (Pexels' free download cap and the general scarcity
of long, glide-only library footage meant relatively few real candidates
existed at all), but this stage does not need a Higgsfield fallback.
