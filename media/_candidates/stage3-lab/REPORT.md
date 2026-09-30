# Stage 3/4 lab footage: candidate report

Sourcing pass for the "weighing the studies" (moving through a working
research lab) and "the honest answer" (pull-back from the lab) beats.
13 candidates were pulled down and checked; **none clears all four hard
requirements (true 4K, genuinely sharp, real cinematic camera motion,
clean lab content) at once.** Details and the honest verdict are at the
bottom.

## Method notes

- Resolution verified with `ffprobe` on the downloaded file, not site
  metadata (Pexels in particular claims a "Dimensions" figure that is the
  *original upload*, not what the free/unauthenticated download actually
  serves -- see Pexels section below).
- Sharpness: Laplacian-variance on the 3 extracted frames (10/50/90%),
  computed with a hand-rolled kernel in PIL/numpy (`cv2` isn't installed
  in this project's venv). **Calibration reference**: the same metric on
  the already-shipped `media/clips/03-weighing-...mp4` (churning ocean
  water) scores **583.7**. That's texture-heavy footage so it's not an
  apples-to-apples target, but it shows the gap: every lab candidate here
  scores 30-190x lower. Read the sharpness column as relative-among-
  candidates, cross-checked by eye on the actual PNGs, not as a pass/fail
  threshold on its own.
- Motion: `scripts/flight_motion.py --clip`, which scores *global* frame
  motion after heavy blur (camera movement) rather than *local* motion
  (hands, liquid). This matters a lot here: several candidates score
  "mixed" or better only because hands are moving in a locked-off frame --
  confirmed by eye, the three sampled frames are pixel-for-pixel the same
  framing.

## Pexels: ruled out as a source for this shot

Pexels' page metadata advertises source resolution (e.g. "3840x2160"),
but the actual file served by the free/no-login download flow -- read
directly out of the page's embedded JSON (`video.src`) -- tops out at
**2560x1440** ("Young Scientist Working in Laboratory", id 31575747: page
claims 3840x2160, `src` is `13456598_2560_1440_30fps.mp4`). No 4K
rendition is exposed without an account. Every other Pexels lab/scientist
result checked (`researcher-looking-through-a-microscope-6130122` etc.)
was native 1920x1080 or lower. Pexels is not a viable source for the
true-4K requirement here without logging in, which this task doesn't do.
Dropped after this finding; all downloaded candidates below are Pixabay.

## US government sources: ruled out

NIH/NHGRI b-roll (`genome.gov/27555967/laboratory-broll-from-nhgri`) is
real lab footage (ClinSeq, NISC, Ostrander Lab, etc.) and genuinely public
domain, but every listed rendition is **1920x1080 broadcast XDCAM, 29.97
fps** -- no 4K exists in the catalogue. NCATS b-roll page
(`ncats.nih.gov/media/video/broll`) returned 403 to fetch. NIST's video
pages (Boulder b-roll, fire research lab) don't expose resolution or
content detail without loading the video player itself, and NIST's
audience is physical-science/metrology facilities, which reads further
from "biomedical research lab" than the Pixabay finds below. None of the
gov sources were worth downloading once the NHGRI catalogue confirmed no
true-4K tier exists for lab b-roll. This tracks with the general pattern:
government science-comms footage is produced for broadcast (1080p), not
for 4K cinematic use.

## Candidates downloaded and scored (Pixabay)

| Candidate | Resolution (ffprobe) | Sharpness (Lap.var, f1/f2/f3) | Motion (mean / worst 1s / moving%) | Source | Licence | Verdict |
|---|---|---|---|---|---|---|
| pixabay-262188.mp4 | 4096x2160 pass | 3.5 / 3.2 / 3.2 (soft, shallow DOF) | 3.23 / 0.56 / 53% | pixabay.com/videos/scientist-laboratory-biology-262188 | Pixabay License | Reject as walk-through: static macro of microscope + petri dish, motion is the hands, not the camera. Clean content (no text/logos, hands/gloves only). Reads as detail/insert, not the main shot. |
| pixabay-262187 (frames kept, video deleted -- duplicate of above) | 4096x2160 pass | 3.4 / 3.1 / 3.1 | 1.65 / 0.25 / 19% | pixabay.com/videos/scientist-laboratory-biology-262187 | Pixabay License | Reject: same series as 262188, even less motion. Duplicate coverage. |
| pixabay-262189.mp4 | 4096x2160 pass | 12.2 / 12.0 / 8.7 (sharpest real-lab macro) | 3.77 / 0.36 / 56% | pixabay.com/videos/laboratory-glassware-microbiology-262189 | Pixabay License | Best of the microscope-macro series: genuinely sharp, gorgeous close detail on the objective lenses with a slight rack/push, no text, no people. Still reads as a DETAIL/INSERT shot, not a "moving through the lab" walk. Closest thing here to a clean pass, but fails the cinematic-motion requirement as a primary shot. |
| pixabay-216230 (frames kept, video deleted) | 3840x2160 pass | 12.4 / 11.2 / 12.3 | 3.36 / 0.44 / 55% | pixabay.com/videos/chemistry-science-laboratory-216230 | Pixabay License | Reject: camera is locked off (3 sampled frames are identically framed); all scored "motion" is a gloved hand pouring liquid. Real chemistry bench, hands pipetting -- good content match -- but no camera movement at all. |
| pixabay-216231.mp4 | 3840x2160 pass | 11.2 / 12.5 / 9.8 | 4.84 / 1.46 / 79% | pixabay.com/videos/chemistry-science-laboratory-216231 | Pixabay License | Same series/uploader as 216230, same problem: locked-off close shot, hands do all the moving. Best "moving fraction" of the whole set (79%) but that's local motion, not the camera. Kept as the best hands/pipetting content reference despite failing the motion requirement. |
| pixabay-216232 (frames kept, video deleted) | 3840x2160 pass | 23.5 / 24.9 / 14.3 | 8.37 / 1.26 / 90% | pixabay.com/videos/chemistry-science-laboratory-216232 | Pixabay License | Reject on content: this is outdoor field water sampling on a boat (river/lake in the background), not an indoor lab, and a "Masterflex"/"easy-Load" brand logo is clearly readable on the equipment. Best motion score in the set, wrong shot entirely. |
| pixabay-262464.mp4 | 3840x2160 pass | 84.0 / 101.1 / 103.3 (sharpest of all) | 1.28 / 0.31 / 9% | pixabay.com/videos/scientist-laboratory-lab-262464 | Pixabay License | Reject on content: real Indian QC/materials-testing lab (genuinely working, genuinely sharp, some real camera drift) but the frame is full of readable signage and handwritten labels -- "R-PET CHIPS", "RECYCLED POLYESTER", "PH Cum Co... Met[er]", "Rack No. 32", jar labels, a wall photo/certificate. Fails "no readable text" outright. Kept as a reference for what genuinely sharp real lab footage looks like; would need a full reframe/crop to use, and even then most of the frame is signage. |
| pixabay-329197 (frames kept, video deleted) | 3840x2160 pass | 22.2 / 19.0 / 15.5 | 3.19 / 0.41 / 48% | pixabay.com/videos/microscope-study-woman-scientist-329197 | Pixabay License | Reject: the monitor shows a glowing, stylised blue/green "cell visualization" with UI chrome that reads as AI-generated/3D-rendered, not a real microscopy readout -- inconsistent with "real stock footage." Also has readable on-screen UI text. Skip regardless of otherwise-decent numbers. |
| pixabay-197486 (frames kept, video deleted) | 3840x2160 pass | 4.4 / 5.4 / 5.0 | 6.99 / 0.27 / 66% | pixabay.com/videos/scientific-laboratory-science-197486 | Pixabay License | Reject: real footage, and f1 (10%) is a nicely composed wide-ish lab shot, but f3 (90%) is almost entirely defocused -- a whip/rack move that goes badly soft partway through. Inconsistent sharpness across the clip disqualifies it as a "genuinely sharp" pick. |
| pixabay-226198 | 1920x1080 FAIL | not scored (resolution fail) | not scored | pixabay.com/videos/science-laboratory-medical-226198 | Pixabay License | Reject: fails the 4K floor outright. Deleted before frame work. |
| pixabay-86260 | 3840x2160 pass | not fully scored (locked shot, rejected on inspection) | 0.77 / 0.10 / 5% (essentially static) | pixabay.com/videos/laboratory-microscope-science-86260 | Pixabay License | Reject: locked tripod shot, face-on (not incidental/out-of-focus), and a clearly readable "CLEMEX" brand logo on the instrument screen. |
| pixabay-192281 | 1920x1080 FAIL | -- | -- | pixabay.com/videos/scientist-laboratory-research-space-192281 | Pixabay License | Reject: fails the 4K floor. Deleted before frame work (static scientist-at-laptop shot anyway per page description). |
| pixabay-274913 | 1920x1080 FAIL | -- | -- | pixabay.com/videos/chemistry-laboratory-science-274913 | Pixabay License | Reject: fails the 4K floor. Deleted before frame work. |

Files kept in this directory: `pixabay-216231.mp4`, `pixabay-262188.mp4`,
`pixabay-262189.mp4`, `pixabay-262464.mp4`, plus the 10%/50%/90% frame
PNGs for every candidate above (kept even for deleted videos, as a visual
record of why each was rejected).

## Top picks (ranked, with the caveat below)

None of these is a clean pass. Ranked by "least compromised":

1. **pixabay-262189.mp4** (4096x2160, Pixabay License) -- the sharpest
   genuine lab footage in the set (Lap.var ~8-12 vs. 3-5 for its siblings),
   clean content (no text, no faces, no forbidden objects), real subtle
   push/rack on the microscope objectives. Reads better as a DETAIL/INSERT
   cutaway (objective lenses racking into focus) than as the sustained
   "moving through the lab" shot the brief wants -- there's real but small
   motion, not a walk-through. Best candidate for a short insert within
   stage 3, not for carrying the whole beat.
2. **pixabay-216231.mp4** (3840x2160, Pixabay License) -- best content
   match for "hands pipetting at a bench" specifically named in the brief,
   and the best moving-fraction number in the set (79%) -- but that motion
   is entirely the hands; the camera itself is locked off (identical
   framing at 10/50/90%). Would only work if "cinematic camera motion"
   were relaxed to allow a static/locked shot with foreground hand action,
   which the brief explicitly does not allow.
3. **pixabay-262464.mp4** (3840x2160, Pixabay License) -- by far the
   sharpest and most naturally-moving-camera footage of the whole set
   (Lap.var 84-103, some real camera drift), and a genuinely real working
   QC lab. Disqualified by readable signage/text filling much of the
   frame; only usable, if at all, after a tight reframe/crop that avoids
   every label, which would need to be evaluated against the actual crop,
   not this full frame.
4. **pixabay-262188.mp4** (4096x2160, Pixabay License) -- same
   microscope-macro series as #1, kept as a second, slightly softer detail
   option (Lap.var ~3.2-3.5) in case #1's exact framing doesn't fit.

None is a "pull-back/wide" shot in the sense stage 4 wants (camera
retreating from the lab to reveal it receding) -- nothing in this batch
had that motion. The closest in spirit is 262464's subtle drift, but it's
blocked by its own signage.

## Honest verdict

**No.** I could not find a clip that is simultaneously (a) a real working
research lab -- no pills, patients, hospital beds, or readable text/logos
-- (b) genuinely moving on camera in a cinematic (gimbal/dolly/steadicam)
way rather than a locked shot with hand action, and (c) truly sharp at
true 4K, all at once. The pattern across ~13 checked candidates and two
source categories (Pexels, ruled out entirely on the 4K requirement; US
government b-roll, ruled out on the same ground) is consistent: free-
licence lab footage that is SHARP is sharp because it's a locked-off macro
insert (microscope objectives, a pipette tip) with no camera motion;
free-licence lab footage that MOVES is either outdoor/field work with
visible branding (216232), a different genre entirely (262464's QC lab,
covered in signage), or AI-generated (329197). The one clip that combines
real camera movement with a real indoor lab and decent framing (197486)
falls apart on sharpness for a third of its duration. This is consistent
with the brief's own prediction that this would be the hardest shot in
the sequence -- it was, and stretching a locked macro shot or a
signage-heavy lab into "the walk-through" would be a worse call than
flagging the gap honestly.
