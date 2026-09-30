# Stage 3/4 lab footage: wider sourcing pass (v2)

Second pass, resuming a session that was interrupted by an API rate limit
partway through re-checking Pexels. Picks up from `media/_candidates/
stage3-lab/REPORT.md` (13 candidates, all rejected -- see that file for
the full record) plus the partial work already sitting in this directory
(`pexels-31575747.mp4` + 3 frames + `headers.txt`, from before the
interruption).

## Correction carried over from the brief: Pexels resolution

Confirmed directly: Pexels' free/no-login download endpoint
(`https://www.pexels.com/download/video/<id>/`, requested with a
`Referer` header set to the clip's own page) redirects to a
`videos.pexels.com/video-files/<id>/<file>_<W>_<H>_<fps>fps.mp4` URL at
the clip's true original resolution -- not the capped 2560x1440 preview
the first pass mistakenly read out of the page's embedded player JSON.
This is a real fix: several Pexels lab clips are genuinely true 4K on
the actual downloaded file, confirmed with `ffprobe`, not page metadata.
That does **not** mean Pexels' lab catalogue clears the other three
bars -- see below.

## Method notes (same as v1, for continuity)

- Resolution: `ffprobe` on the downloaded file only, never site metadata
  or the redirect filename alone (the filename matches ffprobe in every
  case checked here, but it's still verified independently).
- Sharpness: `scripts/frame_sharpness.py` Laplacian-variance on a crop
  centered on the actual in-focus subject in each frame (coordinates
  noted per row), not the full frame.
- Motion: `scripts/flight_motion.py --clip`, which screens *global*
  (camera) motion separately from *local* (hands/liquid) motion, cross-
  checked by eye against the three sampled frames.

## Candidates checked this pass

### Pexels (re-verified with the corrected download method)

| Candidate | Resolution (ffprobe) | Sharpness (crop) | Motion | Source | License | Role | Verdict |
|---|---|---|---|---|---|---|---|
| pexels-31575747 | 3840x2160 pass | f1 67.3 / f2 84.5 / f3 76.3 (crop 1550,1100,500,400 -- hands/laptop) | mean 1.95, worst 0.70, 12% moving -- "mostly still" | pexels.com/video/young-scientist-working-in-laboratory-31575747 | Pexels License (free, no attribution required) | establishing/locked only | **Reject as walk-through.** Genuinely sharp, true 4K, real lab (working bench, fume hood, pipettors, fire extinguisher, stools), scientist in side-profile at a laptop, not looking at camera. But the camera itself barely moves -- 3 sampled frames are nearly the same framing, confirmed by eye and by the motion script. Also carries readable signage in the background (a laminated safety poster, and in f3 a label reading "AGITADOR MULTIPLE" and partial "LABORATORIOS" text), which fails the no-readable-text rule outright if used at this framing. Best-verified true-4K real-lab clip of this whole pass; kept as the top near-miss. |
| pexels-4121322 | 3840x2160 pass | f1 10.2 / f2 4.7 (crop 1600,400,500,500 -- microscope/hand) | mean 1.02, worst 0.15, 9% moving -- "mostly still" | pexels.com/video/lab-laboratory-medical-medicine-4121322 | Pexels License | n/a | **Reject.** True 4K confirmed, but this is an obviously styled stock-shoot "clean lab" set (surgical mask, gloves, perfect diffused lighting, color-graded petri dishes) rather than a real working lab, and the camera is locked off -- only the hand/pipette-arm moves. Deleted after frame extraction. |
| pexels-8534540 | 3840x2160 pass | f1 25.8 (crop 1600,900,500,500) | mean 1.20, worst 0.54, 7% moving -- "mostly still" | pexels.com/video/a-man-doing-research-8534540 | Pexels License | n/a | **Reject.** True 4K, but an even more overtly staged/sci-fi-styled set (colored blue/purple gel lighting, a novelty magnifying headset, a readable ID badge and a readable "SOLUTIO CAUSTIC..." bottle label) -- fails content on both "real lab" and "no readable text," and the camera is locked. Deleted after frame extraction. |
| pexels-8533507 | 2160x3840 (portrait) | not scored | not scored | pexels.com/video/scientists-talking-in-a-lab-8533507 | Pexels License | n/a | **Reject outright on orientation** -- vertical video, confirmed from the download redirect filename before downloading the body. |
| pexels-8533503 | 2160x3840 (portrait) | not scored | not scored | pexels.com/video/a-scientist-looking-at-test-tubes-in-a-lab-8533503 | Pexels License | n/a | **Reject outright on orientation** -- vertical, same check. |
| pexels-9573566 | 2160x4096 (portrait) | not scored | not scored | pexels.com/video/a-scientist-working-at-a-laboratory-9573566 | Pexels License | n/a | **Reject on orientation.** Filename reads "uhd_2160_4096," i.e. portrait, not landscape -- confirmed by ffprobe after a full download; deleted immediately. |
| pexels-8091889 | 1920x1080 | not scored | not scored | pexels.com/video/a-man-working-in-the-laboratory-8091889 | Pexels License | n/a | **Reject on resolution** -- confirmed from the download redirect filename, not downloaded. |
| pexels-8852420 | 1080x1920 (portrait, HD) | not scored | not scored | pexels.com/video/scientist-working-together-8852420 | Pexels License | n/a | **Reject on resolution and orientation** -- confirmed from the redirect filename, not downloaded. |

Pexels' own free 4K lab/scientist catalogue is small, and once the true
resolution is checked (not assumed from the page), the pattern holds
from the first pass: what's sharp and real is locked off; what's staged
enough to look "produced" is still locked off and adds readable text;
half the remaining 4K-tagged results are portrait-orientation phone-style
clips, not landscape. No walk-through or pull-back shot was found on
Pexels in either pass.

### Mixkit (new source, not in the original brief list -- checked because
it surfaced directly for "scientist walking through laboratory")

Three lab/scientist clips with camera movement exist
(`scientist-walking-through-laboratory-24076`,
`scientist-walking-down-a-corridor-4747`,
`scientists-looking-at-laboratory-pipes-24084`). All ruled out without
downloading:
- `-24076` and `-24084`: free tier is 720p under the **Mixkit Restricted
  License ("personal use only")** -- fails both the 4K bar and the
  licensing rule (no commercial/site use permitted); true 4K exists only
  behind an Envato Elements subscription.
- `-4747`: free and commercially licensed (Mixkit Stock Video Free
  License), but tops out at 1920x1080 -- fails the 4K bar outright, and
  is a static (not moving-camera) shot regardless.

### Vecteezy (named priority source)

**Inaccessible.** Both the search-listing page and a retry after a wait
were blocked by Vecteezy's Cloudflare bot-check ("Performing security
verification") in the browser tool; this is a bot-detection gate, which
is not something to route around. No Vecteezy candidate could be
evaluated this pass.

### Videvo (named priority source)

**Consolidated into Freepik.** `videvo.net/stock-video-footage/...`
URLs now 301-redirect to `freepik.com/videos` -- Videvo no longer exists
as an independent catalogue to search the way the brief describes.
Freepik's free tier is attribution-required and account-gated in a way
that doesn't match any of the accepted licenses, and wasn't pursued
further given the time budget and the consistent pattern below.

### Videezy, Dareful, Mazwai, Life of Vids (named priority sources)

Not pulled from directly. A search for Dareful/Mazwai confirmed both are
travel/nature/urban/human-interest-focused libraries by their own
description, not science or lab content -- exactly the kind of long-shot
source the brief says not to spread into. Videezy and Life of Vids were
not checked at all; time went to the sources most likely to pay off
(Pexels re-check, NASA, Wikimedia/DOE) instead.

### NASA / images.nasa.gov (named priority source)

The official `images-api.nasa.gov` search/asset API (not the gated
website) was used directly -- public, unauthenticated, no bot-block.

| Candidate | Resolution (ffprobe, `~orig.mp4`) | Content | Verdict |
|---|---|---|---|
| `jsc2021m000793` -- JSC Microbiology Laboratory | **1920x1080** | Real, modern, working microbiology lab at Johnson Space Center | **Reject on resolution.** Even the `~orig` (highest, ~196MB) rendition is 1080p -- NASA does not publish this b-roll at 4K. Deleted after ffprobe. |
| `jsc2021m000789` -- JSC Materials Evaluation Laboratory | **1920x1080** | Real materials-testing lab, JSC | **Reject on resolution**, same as above. Deleted after ffprobe. |
| `KSC-20231207-...-ISS25TH-...-UHD` -- "ISS@25: Unique Laboratory" | not fully verified (aborted) | An 800MB+ multi-segment 25th-anniversary highlight reel, not a single lab shot | **Abandoned before scoring.** "UHD" in the filename plus the multi-hundred-MB size suggested a real 4K master, but this is a compilation reel spanning many unrelated ISS moments, not an isolated lab walk-through/pull-back; downloading and scrubbing an 800MB+ reel for one usable 10-second segment was a poor use of the remaining budget given every other NASA lab-specific asset checked came back 1080p. Deleted (partially downloaded) without full ffprobe. |
| Other "laboratory"/"research laboratory biology" API search hits | -- | Ribbon-cuttings, wind-tunnel dedications, astronaut podcast inflight interviews, 1970s lunar-biology archival film, rover-ops reels | **None are a plausible content match** for a clean modern biomedical/life-sciences bench-and-microscope lab; not downloaded. |

NASA's genuine interior b-roll (JSC labs) is real, clean, and exactly
on-topic for content -- no readable-text problem, no staging problem --
but it is produced and distributed at 1080p, with no 4K rendition
exposed anywhere in the asset manifest, even at the "orig" tier. This is
the same pattern the first pass found in the NHGRI/genome.gov catalogue:
government lab b-roll is a broadcast (1080p) product, not a 4K one.

### Wikimedia Commons (named priority source)

Commons' own search API was used directly (public, no bot-block).

| Candidate | Resolution | Content | License | Verdict |
|---|---|---|---|---|
| `File:Stock lab footage.webm` | 1280x720 | CDC: "workers in a hematology lab including fumehood" -- real, clean, on-topic | Public Domain Mark 1.0 (US federal work) | **Reject on resolution.** Exactly the right content and a completely unambiguous license, but caps at 720p -- no higher rendition exists on Commons. |
| `File:Video of Idaho National Laboratory.ogv` | 720x720 | Real "hot cell" remote-handling footage, Idaho National Laboratory (DOE) | Public domain (DOE employee work) | **Reject on resolution** (and square aspect ratio besides). |
| `File:Video about the IVN-Tandem at the Argonne National Laboratory.ogv` | 720x720 | Real equipment demo, Argonne National Laboratory (DOE) | Public domain, but the file page itself warns "national laboratories operate under varying licences and some are not free" | **Reject on resolution**, and the license caveat would need per-lab verification even if resolution passed. |

No DOE national lab (LBL, ORNL, LANL, PNNL) was found to run an openly
downloadable b-roll media gallery outside what's mirrored to Commons --
Berkeley Lab's public asset is a *photo* archive, not video; ORNL's
public page is a photo gallery plus a media-contact email, not a
self-serve video library. Neither was pursued further given Commons
already showed the DOE-lab content that exists there tops out at 720p.

### Research universities (named priority source, explicit ambiguity warning)

Not downloaded from, on inspection alone: Stanford's B-Roll-for-News-
Agencies service is explicitly restricted to "non-commercial news
organizations" and is password-gated; Stanford Medicine's b-roll
requires emailing a film-request address for access, not a self-serve
open download; Johns Hopkins' Hub/Communications office runs press
media services with no visible open-license video library; MIT and UCSF
returned nothing specific to check. Per the brief's own instruction to
reject ambiguous university licensing, none of this was pursued into a
download -- the restriction is stated on the source pages themselves, not
inferred.

### Internet Archive (named priority source)

Searched via the public `advancedsearch.php` API (title contains
"laboratory", mediatype video). Of 25 results, essentially all are
YouTube mirrors (CDC training videos, news clips), cartoons ("Dexter's
Laboratory"), decades-old archival film, or otherwise unrelated content
-- nothing resembling modern, openly-licensed, high-resolution working-
lab b-roll. Not pursued further.

## Files kept in this directory

- `pexels-31575747.mp4` -- the one finalist kept as a video file (true
  4K, sharpest, cleanest real-lab content of everything checked across
  both passes). Frames: `pexels-31575747_f1/f2/f3.png`.
- Frames only (source videos deleted after scoring, per the brief):
  `pexels-4121322_f1/f2/f3.png`, `pexels-8534540_f1/f2/f3.png`.
- `headers.txt` -- the curl response headers from the interrupted prior
  session, kept as the original evidence that the corrected Pexels
  download method works (shows the 302 redirect to the true
  `3840_2160` file for 31575747).

## Ranked picks (up to 4 -- only 1 clears enough bars to be worth listing)

1. **pexels-31575747.mp4** (3840x2160, Pexels License, free/no
   attribution). Sharpness 67-85 (crop on hands/laptop) -- comfortably
   the sharpest true-4K real-lab footage found across both passes,
   beating even the first pass's best macro inserts. Real working lab:
   fume hood, pipettors, glassware, a fire extinguisher, stools, a
   scientist mid-task and not looking at camera. **Still fails the
   motion bar** (near-locked, "mostly still" per the motion script and
   confirmed by eye -- the three sampled frames barely reframe) and
   **fails the no-text bar** (a readable safety poster and a partial
   "AGITADOR MULTIPLE" / "LABORATORIOS" label sit in the background).
   Best available as a locked establishing/insert shot if the "no
   sustained camera motion" and "no readable text" rules were relaxed
   or the shot were cropped tighter to avoid the signage; not usable
   as-is for either the walk-through or the pull-back beat as specified.

Nothing else from this pass is close enough to rank. The four v1
near-miss finalists in `media/_candidates/stage3-lab/` (pixabay-262189,
pixabay-216231, pixabay-262464, pixabay-262188) remain the other
reference points if a compromise pick is needed.

## Honest verdict

**No -- this wider pass did not find a genuine walk-through or pull-back
shot that clears all four bars, and the picture is now more complete
rather than more hopeful.** The Pexels resolution correction was real
and worth making (31575747 is genuinely sharper and cleaner than
anything in the first pass), but it surfaces the same structural problem
from a different angle: Pexels' free lab catalogue that is *real and
unstaged* is shot locked-off, and the two clips with any staging budget
add either a fabricated "clean lab" look or outright readable labels/
signage. Every other newly-checked source in this pass ran into one of
two walls: (a) a hard resolution ceiling below 4K that the brief didn't
anticipate being this consistent -- NASA's own JSC lab b-roll, Wikimedia's
CDC and DOE lab footage, and Mixkit's commercially-free tier all cap at
1080p or below, even at their "original" quality tier -- or (b) access
that this task correctly won't route around (Vecteezy's bot-check) or
licensing ambiguity this task correctly won't guess past (every
university press office, Freepik/Videvo's account gate). The emerging
pattern across two full passes and ten-plus sources: genuinely free,
genuinely 4K, genuinely sharp, genuinely moving-camera, genuinely
unstaged/unbranded lab footage is not sitting in any of the libraries
this kind of project is allowed to pull from for free. A real
walk-through or pull-back for this beat most likely requires either a
paid clip (Pexels/Pixabay Pro, Storyblocks, iStock all surface plausible
thumbnails in search but were out of scope here), a relaxed constraint
(accept a locked shot, or accept a short crop of 31575747 that avoids
its signage), or a different execution entirely (e.g., a short
composited move built from a locked plate, or commissioning/recording
original footage).
