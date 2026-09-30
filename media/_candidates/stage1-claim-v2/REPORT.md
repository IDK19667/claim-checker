# Stage 1 "The claim" — wider sourcing pass (v2)

Second pass, wider than `media/_candidates/stage1-claim/REPORT.md` (read first —
that pass covered Pexels + Pixabay and kept one clip, `pixabay-169445.mp4`,
which clears content/resolution/sharpness but has **no camera movement**: only
the hand/screen content moves, the camera itself is locked). This pass adds
Vecteezy, Videvo, Videezy, Dareful, Mazwai, Life of Vids, a brief Wikimedia
Commons / Internet Archive check, and a corrected Pexels re-pass using the
real download endpoint (`pexels.com/download/video/<id>/`, which 302s to the
true-original `videos.pexels.com` file — verified directly with `ffprobe`,
not the page's embedded preview-player JSON, which under-reports resolution).

## Resumed state

Found one file already in this directory from the interrupted prior attempt:
`pexels-30285721.mp4` ("Frustrated man texting in bed late at night"), 29MB.
Verified with `ffprobe`: **3364x1440**, h264, 30fps, 31.7s, plays cleanly
end-to-end (no corruption from the interrupted run). This is *below* the
2160-vertical 4K floor — consistent with the first pass's metadata-only note
on this same clip ("3840x1644 — 4K-wide but short of 2160 vertically"). Since
it fails the hard resolution bar either way, it was rejected on resolution
without further testing, and the file was deleted after confirming
(no frames needed — ffprobe alone is disqualifying).

## New sources checked (beyond Pexels/Pixabay)

| Source | Outcome |
|---|---|
| **Vecteezy** | Both `curl` (with browser UA) and `WebFetch` got a hard `403` on every page tried (homepage, `/free-videos/phone-at-night`, `/free-videos/phone-dark`, `/free-videos/phone-light-up`) — no workaround found this pass (the Pexels/Pixabay workarounds from pass 1 don't transfer). Google-indexed snippets confirm relevant categories exist (Phone At Night — 261 clips, Phone Dark — 2,908 clips, Dark Room — 839 clips) but none could actually be opened, so nothing from Vecteezy could be screened. **Unresolved access gap, not a content verdict.**
| **Videvo** | Same `403` wall via direct `curl`; WebSearch turned up nothing specific to this shot (results were dominated by other libraries). Not screened.
| **Videezy** | Reachable directly (`curl` 200). Crawled its "Free Dark Stock Video Footage" category (`/free-video/dark`) in full: every result is abstract/particles/fire-and-smoke/light-bulb backgrounds, film-projector arts-and-culture clips, or fabric-in-wind loops — no phone or lifestyle b-roll of any kind in that category. Not a library that stocks this kind of shot. Not pursued further.
| **Dareful** | Reachable directly (`curl` 200), but its on-site search for "phone" returns a literal 404/no-results page, and its category nav is purely landscape/nature (aerial, beach, city, clouds, drone, forest, lake, mountains, ocean, sky, sunset, time-lapse, travel, trees, water). Confirms the task brief's expectation — this library doesn't carry lifestyle/device footage. Not pursued further.
| **Mazwai** | `mazwai.com/stock-video-footage/phone` now 301-redirects to `freepik.com/videos/phone` — Mazwai has been absorbed into Freepik and no longer serves its own free CC library at that URL. Off-mission (Freepik's video library is a different, mostly paid/Pro catalog, out of scope for this task). Not pursued further.
| **Life of Vids** | Distributes through a Pixabay contributor account (`pixabay.com/users/life-of-vids-1282862/`) rather than its own CDN — i.e. its catalog is already inside the Pixabay search the first pass covered. Not a separate pool.
| **Wikimedia Commons** | One quick search per the brief's "don't over-invest" guidance: turned up only a couple of still photographs (e.g. "Person looking at smartphone in the dark") and generic darkroom/video-camera reference images — no relevant video b-roll. As expected, not a fit for this kind of lifestyle shot.
| **Internet Archive** | Not searched beyond the above — brief said not to over-invest here, and Wikimedia's complete miss on the same kind of content made Archive.org a low-probability use of remaining budget.

## Pexels re-pass (corrected download method)

Searched with broadened terms (`phone glow dark`, `iphone dark`, `cell phone
night`, `phone dark push in slider gimbal`, plus following up specific
promising titles) and screened every landscape, dark-room, phone-related
result found. Full candidate list:

| Candidate | Resolution | Orientation | Verdict / rejection category |
|---|---|---|---|
| `pexels-8088612` "Fashion People Smartphone Dark" (cottonbro studio) | **3840x2160, true 4K, confirmed via `ffprobe` on the actual downloaded file** (59.6MB, h264, 25fps, 28.6s) | landscape | **REJECT — content.** Downloaded and inspected all 3 sampled frames (10/50/90%) at full resolution (see `pexels-8088612-f_10/50/90.png`, kept as evidence). This is a crowd scene: multiple people's faces are sharply lit, in focus, and prominent in every frame — one man looks straight at camera close-up, several more surround him. Squarely fails the "no sharp-focus faces" rule; not a borderline case. Not scored for sharpness/motion since content alone disqualifies it. |
| `pexels-30285721` "Frustrated Man Texting in Bed Late at Night" | 3364x1440 actual (download endpoint, `ffprobe`-verified) | landscape | REJECT — resolution (below 2160 vertical floor). Carried over from the interrupted prior attempt; re-verified, not re-downloaded. |
| `pexels-38988031` "Smartphone on Table with Pexels Logo at Night" | 3840x2160 (per page metadata) | landscape | REJECT — content (Pexels logo/branding visible on screen = readable content) **and** motion (page-described as a static shot, phone stationary on a table for the full 14s). Not downloaded — disqualified on two independent grounds before spending the download. |
| `pexels-7986754` "Woman Using Smartphone" | 2160x3840 | **portrait** | REJECT — orientation. Not downloaded. |
| `pexels-33942937` "Close-up of Hands Using Smartphone in Dark" | 1920x1080 | landscape | REJECT — resolution (1080p only, nowhere near 4K). Not downloaded. |
| `pexels-6611951`, `pexels-6586070` | portrait (per pass-1 findings, re-confirmed by title/metadata this pass) | portrait | REJECT — orientation (carried over, not re-tested) |

No other new Pexels candidates surfaced that weren't already rejected in the
first pass (that pass's reject list — `pixabay-202987`, `pixabay-64594`,
`pixabay-64595`, `pixabay-61704`, `pixabay-47743`, `pexels-6943542`,
`pexels-8070886`, etc. — was not re-tested; nothing in this pass's searching
turned up a reason to revisit those verdicts).

## Ranked finalists

**None.** Zero candidates from this pass cleared all four hard-bar
requirements (resolution, sharpness, real camera motion, content). No video
files were kept from this pass; only the three evidence frames from the one
candidate that got far enough to be downloaded (`pexels-8088612`, rejected on
content) remain in this directory.

## Honest verdict

**No — this wider pass did not find anything that beats `pixabay-169445`.**
Across Vecteezy (blocked entirely — a real access gap, not a screened
rejection), Videezy and Dareful (both reachable but confirmed, by actually
browsing their relevant categories, to not stock this kind of footage),
Mazwai (no longer an independent free source), Life of Vids (already folded
into the Pixabay pool from pass 1), Wikimedia/Archive.org (as expected, no
lifestyle b-roll), and a corrected Pexels re-pass, the only new candidate
that reached full-resolution inspection (`pexels-8088612`, genuinely true 4K)
fails hard on content — it's a crowd of people with several sharp, prominent,
camera-facing faces, the opposite of what this shot needs. Everything else
was disqualified by resolution or orientation before it was worth a download.

The one real gap in this pass is Vecteezy: its listed categories (Phone At
Night, Phone Dark, Phone Light Up) sound like exactly the right hunting
ground and were never actually opened because of a persistent 403 on both
`curl` and `WebFetch`. If that access problem gets solved (e.g. from a
different network, or with an actual account/session), Vecteezy is the one
unchecked source worth returning to before concluding this shot needs to
come from a paid library or a digital push-in added in post. Everywhere else
checked this pass is now genuinely exhausted for this brief.

`pixabay-169445` (in `media/_candidates/stage1-claim/`) remains the best
available real-footage option, with the same caveat pass 1 already recorded:
it is a locked/handheld-static shot, not a moving-camera one.
