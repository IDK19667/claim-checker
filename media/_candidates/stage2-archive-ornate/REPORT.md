# Stage 2 "archive" re-sourcing: ornate/baroque library — candidate report

Prompted by a user reference photo: a grand, ornate, classical/baroque
library, a long receding corridor of stone/wood archways, floor-to-ceiling
leather-bound book shelving, warm golden light, polished wood floor, empty
of people, deep symmetric perspective (Waldsassen / Admont / Strahov /
Austrian National Library style). The three clips already kept in
`media/_candidates/stage2-archive/REPORT.md` (`pexels-854417`,
`pexels-34345704`, `pexels-14543425`) are real libraries but modern/plain
(glass elevator, corridor, children's section) — this pass looked
specifically for something closer to the reference.

Bar applied, same as every prior pass: true 4K+ (3840x2160 minimum,
verified with `ffprobe` on the actual downloaded bytes, never page
metadata or the embedded preview player), genuinely sharp at 100% pixel
crop (`scripts/frame_sharpness.py --crop`), cinematic camera motion
preferred, a locked shot acceptable only as a fallback with content
otherwise excellent, no people as the subject (incidental out-of-focus
only), no sharp readable text/logos, landscape orientation, a clean free
licence.

## Candidates checked

| Candidate | Resolution (verified) | Sharpness | Motion | Source | Licence | Verdict + reason |
|---|---|---|---|---|---|---|
| `pexels-35143877` "Stunning Baroque Library Interior in Prague" | 2160x3840 (page-stated, portrait) | not downloaded | — | pexels.com/video/stunning-baroque-library-interior-in-prague-35143877 | Pexels License | REJECT — portrait only, no landscape rendition exists. The single closest-looking title in the whole search (genuinely baroque, Prague), disqualified purely on orientation. |
| `pexels-36704883` "Classical Library with Ornate Architecture" | 1080x1920 (page-stated, portrait) | not downloaded | — | pexels.com/video/classical-library-with-ornate-architecture-36704883 | Pexels License | REJECT — portrait, and below the 4K floor even if rotated. |
| `pexels-34141700` "Stunning Baroque Architecture in Vienna Library" | **3840x2160, verified via ffprobe on real download** | not scored (content-rejected) | not scored | pexels.com/video/stunning-baroque-architecture-in-vienna-library-34141700 | Pexels License | REJECT — real Austrian National Library Prunksaal, true 4K, but every frame is a handheld shot following a crowd of tourists in sharp focus (one figure fills up to half the frame); people are the clear subject, not incidental. |
| `pexels-34141497` "Stunning Interior of Austrian Library in Vienna" | **3840x2160, verified via ffprobe on real download** | not scored (content-rejected) | not scored | pexels.com/video/stunning-interior-of-austrian-library-in-vienna-34141497 | Pexels License | REJECT — same location, wider shot, but a dozen-plus tourists standing/milling in sharp focus fill the foreground the whole clip. |
| `pexels-34141695` / `34141702` / `34141703` "...with Visitors" / "Tourists Capturing..." | not downloaded | — | — | pexels.com (Vienna library set) | Pexels License | REJECT on title alone — same Prunksaal location, titles explicitly name visitors/tourists as the subject. Not worth the download given 34141700/34141497 already confirmed the pattern. |
| `pexels-29592158` "Peaceful Library Aisle with Elegant Arched Ceiling" | 1080x1920 (page-stated, portrait) | not downloaded | — | pexels.com/video/peaceful-library-aisle-with-elegant-arched-ceiling-29592158 | Pexels License | REJECT — portrait, below 4K. |
| `pexels-36484913` "Elegant Historical Library Interior View" | 1920x1080 (page-stated, landscape) | not downloaded | — | pexels.com/video/elegant-historical-library-interior-view-36484913 | Pexels License | REJECT — below the 4K floor (no higher tier listed on the page). |
| `pexels-31253783` "Historic Library Dome Interior Captured" | 1920x1080 (page-stated) | not downloaded | — | pexels.com/video/historic-library-dome-interior-captured-31253783 | Pexels License | REJECT — below 4K; also a close-up dome detail, not the corridor shot. |
| `pexels-29582655` "Quiet Study Session in Grand Library" | not confirmed | — | — | pexels.com/video/quiet-study-session-in-grand-library-29582655 | Pexels License | REJECT — students are the stated subject. |
| `pexels-29592157` "Historic Library with Arched Ceilings and Bookshelves" | 1080x1920 (page-stated, portrait) | not downloaded | — | pexels.com/video/historic-library-with-arched-ceilings-and-bookshelves-29592157 | Pexels License | REJECT — portrait, below 4K, and page description names "focused readers" as the subject. |
| `pexels-29582651` "Quiet Reading Room in Historic Library" | 1080x1920 (page-stated, portrait) | not downloaded | — | pexels.com/video/quiet-reading-room-in-historic-library-29582651 | Pexels License | REJECT — portrait, below 4K. |
| `pexels-29241899` "Beautiful Historic Library Interior" | **1920x1080, verified via ffprobe on real download** | 2910.7 (crop 600,300,600,600) — genuinely sharp | mean 1.60 / worst 0.24 / 21% moving — mild handheld, not a real camera move | pexels.com/video/beautiful-historic-library-interior-29241899 | Pexels License | REJECT on the 4K floor only. Otherwise the best content match found: colonnaded reading-room aisle, symmetric receding perspective, rich wood/iron shelving, warm light, genuinely sharp. Has one small out-of-focus figure near the vanishing point and a readable red exit-sign / poster in the background — would also need a second look on the text rule even if resolution passed. Kept as the clearest illustration of "this look exists, just not yet at 4K on a free licence." |
| `videezy-16141` "Library of Congress Reading Room 4K" | **3840x2160, verified via ffprobe on the real download** (page preview under-reports — see note below) | 100.0 (crop 800,300,600,600) — the sharpest candidate this round | mean 0.26 / 0% moving — fully locked/static shot | videezy.com/abstract/16141-library-of-congress-reading-room-4k | Videezy "Free Download" (exact licence text did not render in two attempts to open the in-page modal; Videezy's standard free tier is commonly free-for-commercial-no-attribution, but that was **not directly confirmed on this clip** — flag before shipping) | REJECT on content, kept as the closest true-4K/sharp match. Genuinely the grandest, sharpest thing found (Library of Congress Main Reading Room: marble, gold, statues, arched galleries). But it is a locked high-angle shot of the **radial domed reading room**, not a receding corridor/aisle, so the geometry doesn't match the reference; roughly 12-15 people are seated at the desks in sharp focus throughout (not incidental); and a "MAIN CATALOG" sign is sharply readable in the background. Three content-rule violations at once. |

**Pixabay**: searched "old library", "libraries", "reading room" — no
ornate/baroque/historic candidate surfaced at all; results are dominated
by students studying, generic modern bookshelves, and a few
obviously-3D-rendered "medieval library" clips. Nothing downloaded.

**Vecteezy**: `vecteezy.com/free-videos/old-library` returned an HTTP 403.
Consistent with the Cloudflare bot-check already logged for this site in
`media/PRODUCTION.md`'s prior sourcing pass. Not investigated further,
not bypassed, per the hard rule on this project.

**Videezy**: reachable, no bot-check. This is where the one genuine 4K
ornate candidate came from (see table). Its real download needed the same
kind of correction as the Pexels gotcha: the page's `<video>` preview
element points at `static.videezy.com/.../original/...mp4`, which is
actually a 630x354 preview despite the folder name "original" — the true
file only comes from clicking "Free Download," which fires a
session-scoped `download_auth_hash` redirecting to
`static.videezy.com/system/protected/files/...mp4`. That hash is
single-use and tied to session cookies, so it has to be replayed with a
cookie jar in the same request chain (page load → download-button POST →
hashed download URL), not treated as a stable link. Recorded here in case
this site is revisited.

**Dareful**: now Shutterstock-affiliated (the homepage leads with a
Shutterstock discount banner); a library search was attempted but the
browser session lost its own navigation mid-search and the attempt was
not repeated given the low expected yield already suggested by the
homepage branding. Not thoroughly checked — flagging as incomplete rather
than claiming a clean rule-out.

**Wikimedia Commons**: media search for "baroque library interior" video
returned zero results.

**Internet Archive**: a "movies" mediatype search for "baroque library
interior" returned no relevant results (generic archive.org boilerplate
only).

**Videvo, Mixkit, heritage-library official channels (Strahov, Admont,
Austrian National Library, Trinity Long Room)**: not re-checked this
round — `media/PRODUCTION.md`'s existing entries already cover Videvo
(redirects into Freepik) and Mixkit (720p personal-use only) as ruled out
project-wide, and a web search for official heritage b-roll surfaced only
paid stock libraries (Getty, Shutterstock, Adobe Stock, Storyblocks) for
every specific site named, consistent with the brief's own expectation
not to over-invest there.

## Finalists, ranked

1. **`videezy-16141-library-of-congress-reading-room.mp4`** — not a clean
   keep, but the closest thing to "true 4K + genuinely ornate + genuinely
   sharp" found this round. If the project is willing to revisit the
   people/text rules specifically for this shot (e.g. crop tighter on the
   upper architecture, away from the desks and the catalog sign), this is
   the only candidate with the resolution and sharpness headroom to
   support that. As downloaded, it fails three content rules at once
   (subject-level people, readable signage, wrong shot geometry vs. the
   corridor reference) and its exact licence text was not confirmed.
2. **`pexels-29241899-beautiful-historic-library-interior.mp4`** — the
   best content match to the reference photo of any candidate this round
   (or last): a real, ornate, colonnaded reading-room aisle with a
   genuinely symmetric receding perspective and rich shelving. Fails only
   the resolution floor (1920x1080, confirmed) plus a minor text/person
   caveat. Worth a second check if a paid source can supply the same
   location/style at true 4K, or if the 4K floor is ever relaxed for a
   single hero shot.

No third finalist: nothing else cleared even the resolution or
orientation bar with ornate content behind it.

## Honest verdict

**Nothing clears the full bar this round.** The specific reference look
— a long, symmetric, archway-lined corridor of leather-bound shelving,
empty, true 4K, sharp, free-licensed — was not found anywhere searched:
Pexels' free tier has real baroque/historic libraries (Vienna's Prunksaal,
Prague) but every one of them is a tourist-attraction interior shot full
of visitors, because that's what gets filmed there; Pixabay has none;
Vecteezy is blocked by its own bot-check; Videezy has one genuinely
sharp true-4K ornate room (Library of Congress) but it's the wrong shot
type and full of readers and a readable sign; Wikimedia and the Internet
Archive have nothing. This is the same shape of outcome `media/PRODUCTION.md`
already recorded for stages 1 and 3 in the prior round: a real, narrow
gap in what free stock libraries carry, not a search that was cut short.

**Recommendation:** keep the existing three `stage2-archive` clips
(`pexels-854417`, `pexels-34345704`, `pexels-14543425`) as the shipped
archive footage — they still clear every bar, just with modern/plain
architecture rather than ornate. Treat the ornate look as a "would need
a paid library or a generation pass" item if the product specifically
wants the Waldsassen/Strahov aesthetic, same as the still-open lab and
claim shots from the prior round.
