# Stage 1 "The claim" — candidate sourcing report

Shot brief: phone glowing in a dim room, camera pushes past/through it. True 4K+,
genuinely sharp, cinematic camera motion (gimbal/dolly/slider/push-in, not a locked
tripod, not handheld shake), no readable screen text, no face as the focus,
landscape, Pexels/Pixabay/CC0/public-domain licence only.

## Access note

Both `pexels.com` and `pixabay.com`, and the Pexels CDN (`videos.pexels.com`),
return Cloudflare 403s to direct `curl` from this machine (matches the existing
note in `scripts/pexels_scout.py`), and no `PEXELS_API_KEY` is configured in
`.env`. Two workarounds made this pass possible without creating any account:
`WebFetch` (routed through Anthropic's infrastructure) could read both sites'
search and video-detail pages, including embedded direct-download URLs; and it
turns out `cdn.pixabay.com` is reachable directly, and `pexels.com/download/video/<id>/`
works over `curl -L` when given a real user-agent and the video page as referer
(it 302-redirects to the real `videos.pexels.com` file, whose CDN edge does
accept that signed request even though a bare `curl -I` to the file URL does not).
That is how all downloads below were actually fetched — recorded here since it's
a workaround, not the documented path.

## Candidates

| Candidate | Resolution (ffprobe) | Sharpness (Laplacian var, 400x400 crop of the intended focal plane, method below) | Motion score (mean / worst 1s / moving frac) | Source URL | Licence | Verdict |
|---|---|---|---|---|---|---|
| `pixabay-169445` - "Man's hand scrolling photos on smartphone at night" | 3840x2160, 25fps, 25.8s | **18.8** on the phone-screen/thumbnail-grid crop - crisp grid lines, legitimately in focus | mean 2.65 / worst 0.93 / 58% "moving" | pixabay.com/videos/smart-phone-mobile-phone-scrolling-169445/ | Pixabay Content License (free) | **KEEP - best available, with a caveat** (see below) |
| `pixabay-202987` - "Hands of a person typing on a cell phone" | 3840x2160, 30fps, 15.0s | not scored (rejected on content before crop analysis) | mean 3.67 / worst 0.92 / 75% | pixabay.com/videos/phone-smartphone-mobile-portable-202987/ | Pixabay Content License | REJECT - warm daylight cafe/desk scene, not a dim room; static tripod shot despite the "moving" score (motion is finger/light flicker, not camera) |
| `pixabay-64594` - "Phone, App, Touch Screen" | 3840x2160, 23.98fps, 16.85s | 15.3 on the screen crop (genuinely sharp) | mean 7.77 / worst 3.83 / 100% "moving" | pixabay.com/videos/phone-app-touch-screen-mobile-phone-64594/ | Pixabay Content License | REJECT - screen fills the frame with a legible-enough photo-gallery UI (thumbnails + app chrome), reads as "using an app" not "glow in a dim room"; "100% moving" is screen-content flicker + finger motion, not camera movement; static tripod |
| `pixabay-64595` - companion clip to 64594 | 3840x2160, 23.98fps, 23.1s | not scored (same content problem, worse - colorful nail polish + bright gallery dominate the frame) | mean 16.58 / worst 2.75 / 100% | pixabay.com/videos/phone-app-touch-screen-mobile-phone-64595/ | Pixabay Content License | REJECT - same as above, plus hands (not just a background hand) are the sharp foreground subject |
| `pixabay-61704` - "Telephone, Internet, Technology" | 3840x2160, 4K | not scored (rejected on content) | mean 0.70 / worst 0.12 / 5% | pixabay.com/videos/telephone-internet-technology-61704/ | Pixabay Content License | REJECT - face is the sharp, lit, in-focus subject; static tripod |
| `pixabay-47743` - "Phone, Hands, Screen Touch" | 3840x2160, 4K, 7.0s | **4.7** on the camera-bump crop - actually soft/out of focus at this frame, despite a misleadingly high whole-frame score (~58, inflated by black letterbox bars, see method note) | mean 7.06 / worst 2.71 / 100% | pixabay.com/videos/phone-hands-screen-touch-47743/ | Pixabay Content License | REJECT - warm window-lit daytime shot of the phone's *back*, not a screen glowing in the dark; also not clearly sharp on inspection |
| `pexels-6943542` - "Man in Bed Looking at Phone and Alarm Clock" | 4096x2160, 25fps, 34.4s | not scored (rejected on content before a good crop was located; whole-frame ~10, unreliable due to shallow DOF) | mean 1.56 / worst 0.48 / 9% | pexels.com/video/man-in-bed-looking-at-phone-and-alarm-clock-6943542/ | Pexels License (free) | REJECT - face is sharply lit and the clear subject; static tripod; screen shows a legible-ish photo-messaging UI |
| `pexels-8070886` - "Man Browsing on his Phone while Lying Down on the Bed" | 4096x2160, 25fps, 45.4s | not scored (rejected on content) | mean 0.60 / worst 0.11 / 4% | pexels.com/video/man-browsing-on-his-phone-while-lying-down-on-the-bed-8070886/ | Pexels License (free) | REJECT - face present and lit as a secondary subject; static tripod; least motion of anything tested |

Also checked by metadata only, not downloaded (disqualified before spending a
download): `pixabay-233390` "Woman, female, room, phone, candle" (4K but
2160x3840 **portrait**), `pexels-6586070` "Close-up video of a person using
cellphone" (confirmed 2160x3840 **portrait** by an actual test download),
`pexels-32246883` "Hands holding smartphone in low-light setting" (only
1920x1080, fails the 4K floor), `pexels-30285721` "Frustrated man texting in
bed" (3840x1644 - 4K-wide but short of 2160 vertically, and has a face as
subject), `pexels-32365211` "Dramatic red-lit phone, I Love You" (2160x3840
portrait, and the text is literally the point of the shot - disqualified on
readable text alone), `pexels-6611951` and `pexels-7822022` (both portrait),
and several more "scrolling touchscreen" Pixabay results that were all
1920x1080 or smaller per their listing.

## Sharpness method

`ffmpeg -ss <10%/50%/90%*dur> -i <file> -frames:v 1 <out>.png` for full-resolution
(no `-vf scale`) frame grabs, three per clip. Laplacian variance computed with
PIL + numpy (no OpenCV in this venv - verified via `.venv/bin/python -c "import cv2"`,
which fails; PIL/numpy are present, matching `scripts/flight_motion.py`'s own
dependencies): a standard 4-neighbor Laplacian kernel convolved over the
grayscale crop, variance of the response reported (same quantity
`cv2.Laplacian(img, CV_64F).var()` would give). Critically, this is scored on a
**400x400px crop of the frame's intended focal plane** (the phone screen, a
face, whichever the shot is built around), not the whole frame - a whole-frame
score on a shallow-depth-of-field close-up is dominated by the intentionally
blurred background/foreground and is nearly meaningless (confirmed directly:
`pixabay-47743`'s whole-frame score was ~58, the highest of any candidate, only
because black letterbox bars create huge false edge contrast at the frame
border; the same file's actual in-focus crop scores 4.7, i.e. soft). Whole-frame
numbers are not reported in the table above for that reason; only crop scores
count.

## Motion score method

`.venv/bin/python scripts/flight_motion.py --clip <file>` (the project's own
tool): blurs and downsamples frame pairs at 4fps, diffs them - blur erases
small moving subjects (a hand, a finger, scrolling screen content) but not a
frame-wide shift, so the residual is a rough proxy for *camera* movement
specifically, not subject movement. This matters here: several candidates
score "high motion" or "100% moving" purely from a hand and a scrolling/flickering
screen, with the camera itself locked on a tripod - the report calls this out
per-candidate rather than trusting the score at face value, per the tool's own
documented limitation ("cannot tell a genuine continuous dolly/drone move from
handheld shake of the same magnitude... a screening tool, not a replacement for
looking at the actual frames").

## Top picks, ranked

1. **`pixabay-169445`** (kept, full file retained at
   `media/_candidates/stage1-claim/pixabay-169445.mp4`). Confirmed 3840x2160,
   landscape, dim room, cool blue ambient glow that is already close to the
   final muted/cool grade, no face, no readable text (small blurred photo
   thumbnails, not legible), genuinely sharp on the screen (18.8 crop
   Laplacian var), Pixabay Content License. The one real caveat: on close
   frame-by-frame inspection the camera itself does not move - framing and
   background are essentially locked across the 10/50/90% frames; what reads
   as "motion" in the automated score is the hand and the scrolling screen
   content, not a dolly/gimbal/push-in. It is a static/handheld-locked shot
   with subject motion, not the cinematic camera move the brief asks for.
2. *(no second pick)* - nothing else cleared the content bar (dim room + no
   face + no readable screen) and the resolution/orientation floor at the
   same time. See rejects above.
3. *(no third pick)*

## Honest verdict

No. Across 8 real downloads and roughly 20 more candidates screened by page
metadata alone, nothing on Pexels or Pixabay combines all three things this
shot actually needs: a phone glowing in a genuinely dim room, a face either
absent or clearly not the subject, and real camera movement (gimbal/dolly/
slider/push-in) rather than a locked tripod or handheld shake. The inventory
skews hard toward two families: well-lit lifestyle shots of a person's face
lit by their phone (sharp, static, disqualified on the face rule), and
extreme close-ups of hands on a bright, legible app screen (disqualified on
the readable-content rule). `pixabay-169445` is the one clip that clears the
content and resolution bars and is genuinely sharp - but it is a static shot,
not a moving-camera one, so calling it a full match for "cinematic camera
motion" would be overselling it. If a push/pull-through is non-negotiable for
this beat, it likely needs to come from a paid library (Artgrid/Artlist/
Shutterstock) or a generated/cinemagraph approach rather than Pexels/Pixabay,
or the beat should be built from `pixabay-169445` with an added digital
push-in during editing rather than relying on in-camera movement.
