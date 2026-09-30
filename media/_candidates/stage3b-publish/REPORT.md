# Stage 3b sourcing: "the paper goes public" (closing beat before the navy fade)

Round 5 sourcing pass. Goal: replace the current stage-3 ending (chemical-mixing
lab footage, `pexels-31575747` / `pixabay-216231`) with a new closing beat
inserted before the fade to navy: researchers writing up findings, and/or that
paper going online. See `../../PRODUCTION.md` for the sourcing bar and the
Pexels real-download trick (page metadata under-reports resolution; always
verify on actual downloaded bytes).

Two halves were searched, per the brief: **writing** (hands on a manuscript/
document) and **publishing** (a webpage/upload/submit action). They split, as
expected — no single clip does both.

## Method

Every candidate's real resolution was confirmed with the Pexels download
trick (`curl -e <page-url> https://www.pexels.com/download/video/<id>/` ->
302 -> `videos.pexels.com/video-files/<id>/..._<W>_<H>_<fps>fps.mp4`) or, for
Pixabay, the CDN `_large.mp4` link, then verified with `ffprobe` on the
downloaded bytes. Three frames (10/50/90%) were extracted with ffmpeg and
scored with `scripts/frame_sharpness.py --crop` on a crop over the actual
subject (hands, keys, or screen text), never the full frame (a full-frame
score is dragged down by out-of-focus background/bokeh on every shallow-DOF
clip here, so it is not a useful signal on its own). `scripts/flight_motion.py
--clip` was run on every clip that appeared to have any camera movement;
none of the candidates below have real camera motion, they are all
locked-off shots with hand/typing motion in frame, which the brief accepts
as a fallback.

## Candidates checked

### Writing half (hands on a manuscript / document)

| Candidate | Resolution (ffprobe, real bytes) | Sharpness (crop) | Motion | Source | License | Verdict |
|---|---|---|---|---|---|---|
| **pexels-8534605** "A document on the table" | 3840x2160 pass | f1 77.0 / f2 94.8 / f3 74.4 (crop 1500,300,600,500 -- hand + handwritten notes) | mean 1.78, worst 1.13, 2% moving -- locked, hand-only motion | pexels.com/video/a-document-on-the-table-8534605 | Pexels License | **KEEP. Clears the bar.** Hand pointing at/annotating a page of handwritten organic-chemistry notes (esters, reaction diagrams) in monochrome grade. Genuinely sharp at 100% crop -- the best-measured sharpness of this whole pass, on par with the project's best-ever lab clip (`pexels-31575747`, 67-85). No face, no logo, no readable brand. Reads as "reviewing/annotating research notes" rather than literally "typing a manuscript," but fits the writing beat well. |
| pexels-8035630 "Top view of a person using laptop" | 4096x2160 pass | f1 3.9 / **f2 595.1** / f3 6.8 (crop 1950,950,500,400 -- hands on keys) | mean 2.08, worst 0.08, 32% moving -- locked, hands-only | pexels.com/video/top-view-of-a-person-using-laptop-8035617 (sibling take 8035617, same shoot) | Pexels License | **Reject as framed.** Beautiful overhead desk composition (screenplay pages, books, pens) and genuinely razor-sharp at its best instant (f2), but that sharpness is not sustained across the sampled frames -- hands move in and out of the focal plane during fast typing, so only a sub-second window is truly crisp. More importantly: the laptop screen is filling nearly a third of frame and legibly reads **"Men In Black"** (a real, trademarked screenplay title) plus a prop book cover reading **"A Good Screenplay."** Both are real, readable titles in focus, which fails the no-readable-real-titles rule outright at this framing. Same issue on sibling take 8035617 (identical desk, same screen content, not re-downloaded as a video -- one frame kept for the record). Could only be used with a tight crop on hands+keys that excludes the screen entirely, mirroring the `pexels-31575747` precedent from the lab pass. |
| pexels-8036698 "A person typing on a laptop" | 4096x2160 pass | f1 5.1 / f2 16.2 / f3 31.9 (crop 1850,400,500,400 -- screen text) | mean 1.56, worst 0.30, 12% moving -- locked, hands-only | pexels.com/video/a-person-typing-on-a-laptop-8036698 (same shoot/actor as above, library setting) | Pexels License | **Near-miss, kept as fallback.** Over-the-shoulder angle, warm library bookshelf background reads well thematically. Screen shows fictional script dialogue at an angle -- legible but generic, not a real trademark, passes content. Sharpness is moderate, well short of 8534605's numbers, and never reaches "tack sharp." Usable only as a compromise pick, not a clean clear of the bar. |
| pexels-34124549 "Hands typing fast on a laptop keyboard" | 3840x2160 pass, 60fps | f1 4.2 / f2 7.0 / f3 11.0 (crop 1400,700,600,500) | mean 7.19, 85% moving (hand motion, not camera) | pexels.com/video/hands-typing-fast-on-a-laptop-keyboard-34124550 | Pexels License | **Reject.** Very shallow depth of field -- even static parts of the keyboard at frame edges are soft; fails sharpness everywhere checked, despite the fast, legible motion. No screen visible (content would otherwise pass). |
| pexels-6258102 "Hands typing in a keyboard" | 3840x2160 pass | f1 2.8 / f2 3.1 / f3 3.6 (crop 1200,700,600,500) | mean 4.35, 82% moving (hand motion) | pexels.com/video/hands-typing-in-a-key-board-6258102 | Pexels License | **Reject.** Dim backlit-keyboard low-light shot, soft at 100% crop throughout. No screen in frame (content otherwise clean). |
| pexels-8256903 "A person typing" | 4096x2160 pass | f1 1.9 / f2 3.6 / f3 2.5 (crop 600,300,500,500) | mean 6.97, 93% moving (hand motion) | pexels.com/video/a-person-typing-8256903 | Pexels License | **Reject.** Soft at 100% crop throughout despite confirmed true 4K. |
| pexels-6985339 "Close-up view of person typing in a laptop" | 3840x2160 pass | f1 6.7 / f2 5.6 / f3 6.7 (crop 2500,1000,500,400) | mean 3.04, 68% moving | pexels.com/video/close-up-view-of-person-typing-in-a-laptop-6985339 | Pexels License | **Reject.** Shallow DOF, blank blurred screen, hand only marginally sharper than background; doesn't clear the bar. |
| pexels-7605085 "Typing in laptop" | 4096x2160 pass | f1 13.8 / f2 4.9 / f3 8.7 (crop 2200,700,500,400) | mean 4.90, 92% moving | pexels.com/video/typing-in-laptop-7605085 | Pexels License | **Reject.** Extreme close-up, hands sharp-ish but keys themselves soft/blurred by DOF; casual in-bed setting reads wrong tonally too. |
| pexels-34771078 "Hands typing on laptop in dark environment" | 3840x2160 pass | f1 1.4 / f2 1.5 / f3 1.6 (full frame, too dark to crop meaningfully) | not scored | pexels.com/video/hands-typing-on-laptop-in-dark-environment-34771078 | Pexels License | **Reject.** Badly underexposed, nothing reads as sharp or legible. |
| pexels-8036708 "A man working inside the library" | 4096x2160 pass | not scored (content reject first) | not scored | pexels.com/video/a-man-working-inside-the-library-8036708 | Pexels License | **Reject.** Same shoot again, this time foreground-focused on a rolled prop page with hands/laptop soft in the background -- wrong focal plane for this beat. |
| pixabay-47158 "Computer, laptop, keyboard" | 3840x2160 pass | f1 2.7 / f2 2.1 / f3 2.5 (crop 850,700,500,400) | mean 1.17, 13% moving -- mostly still | pixabay.com/videos/computer-laptop-keyboard-to-write-47158 | Pixabay Content License | **Reject.** Soft at 100% crop; also a static "resting hand, glasses off" pose rather than active writing, reads as a stock "burnout" cliche. |

### Publishing half (a webpage / upload / submit action)

| Candidate | Resolution (ffprobe, real bytes) | Sharpness (crop) | Motion | Source | License | Verdict |
|---|---|---|---|---|---|---|
| pexels-38496194 "Browsing open access guide on laptop" | 3840x2160 pass | f1 35.7 / f2 9.2 / f3 23.4 (crop 1720,1000,680,300 -- screen text) | mean 0.72, worst 0.09, 2% moving -- locked | pexels.com/video/browsing-open-access-guide-on-laptop-38496194 | Pexels License | **Best/only real match, does not clearly clear the sharpness bar.** Over-the-shoulder shot of a laptop screen showing a university "Open Access Guide for Researchers" page (University of Helsinki's HELDA repository, real institution but the text is soft-focus/out-of-frame-plane, not sharp, and small enough that only the large heading "OPEN ACCESS GUIDE FOR RESEARCHERS" is confidently legible). Content is an almost literal match for "the paper going public" -- exactly what this beat needs. Foreground person is an out-of-focus silhouette, incidental, never the sharp subject. But measured sharpness (9-36 at crop) is well short of this project's "clears the bar" clips (60-95 range) -- the screen is never truly tack-sharp, it's a soft/aliased camera-on-screen capture. Camera is locked (motion script reads "mostly still"); only a mouse/scroll implies the hand is doing something. Kept as the best real candidate found, flagged honestly as a near-miss on sharpness rather than a clean pass. |
| pexels-5527596 "Man, desk, laptop, working" | 3840x2160 pass | not scored (content reject first) | not scored | pexels.com/video/man-desk-laptop-working-5527596 | Pexels License | **Reject on content.** Screen legibly shows the real, readable "Pexels" website UI/logo (open on the actual Pexels.com homepage) -- a real, identifiable brand in focus. Also a casual feet-up browsing pose, wrong tone for "publishing research." |

### Sources checked, no usable result

- **Pexels** "uploading document website," "submit button click screen,"
  "academic journal website scroll" searches: dominated by generic UI
  animation stock (subscribe/like button loops), staged "businesswoman with
  stacks of documents" sets, or iStock sponsored results (not free). Nothing
  beyond what's tabled above reached full inspection.
- **Pixabay** "writing manuscript typing" (198 results after 4K+landscape
  filter): browsed the first two pages: dominated by decorative book/pen
  stills, a "girl writing student" school-notebook clip (wrong register),
  and the one candidate above. "Scrolling website research" pulled mostly
  clip-art-style DNA/science icon animations, not real research-website
  footage -- not pursued further.
- **Vecteezy, Videvo, Videezy, Dareful**: not reached this round; the
  writing/publishing halves both turned up viable Pexels candidates before
  needing to fall back this far, per the brief's priority order. (Prior
  rounds in this project already found Vecteezy CAPTCHA-gated and
  Videvo/Mazwai redirecting into Freepik -- see `../../../PRODUCTION.md`.)
- **Wikimedia Commons / Internet Archive / government sources (NIH, NASA,
  NIST, DOE)**: not reached this round for the same reason; prior rounds in
  this project already found government b-roll capped at 1080p across every
  agency tried.

## Verdict

**Writing half: one candidate clears the bar outright.** `pexels-8534605`
(hand annotating handwritten chemistry notes) is genuinely sharp at 100%
crop, true 4K, clean content, no readable logos or faces. It is not a
literal "typing a manuscript on a laptop" shot, but it reads clearly as
research writing/annotation and is the sharpest candidate found in either
half of this pass.

**Publishing half: nothing clears the bar.** `pexels-38496194` is the only
candidate whose content is a real match ("Open Access Guide for
Researchers," a live research-repository page) and it is kept as the best
available, but its measured sharpness (9-36 at 100% crop) does not reach
this project's established "clears the bar" range (60-95+). This is an
honest near-miss, not a pass -- consistent with this branch's prior pattern
of the "hardest shot" not clearing every bar on the first attempt. If this
beat ships, it should ship knowingly softer than the rest of the sequence,
or a further sourcing pass (paid library, or a tighter crop/digital
sharpen in the grade) should be considered before commit.

Total: 4 video files kept (`pexels-8534605.mp4`, `pexels-8035630.mp4`,
`pexels-8036698.mp4`, `pexels-38496194.mp4`), all with their 3 extracted
frames alongside; one extra frame kept for sibling take `pexels-8035617`
(no video, duplicate content issue of 8035630). All other downloads were
deleted after scoring.
