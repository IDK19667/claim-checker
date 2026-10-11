# Making the day's videos

The routine for one day's posting pack: three short videos of real checks
on checkevident.com, with captions and reply texts. It only makes files in
`promo/out/`; a person posts them by hand. Ask Claude "make today's videos"
and it follows these steps.

## Rules

- Never post, upload, comment, message, follow or log in anywhere.
- Never edit site code, commit, push, or open `.env`. Only write inside `promo/out/`.
- At most 5 checks on the live site per day. They share its small daily AI quota with real visitors.
- Every check shows on the site's public "latest checks", so every claim must be one a normal person would ask. Skip: suicide, self-harm, eating disorders or extreme dieting, drug or medicine doses, sex, named people, attacks on any group, politics, conspiracy bait, anything made to trick the site. If in doubt, skip it.
- No em dashes or en dashes in anything meant for posting.
- The account is anonymous: no names or personal details anywhere.

## Steps

1. Read `promo/out/done.txt` (claims already made, one per line). If the owner pasted claims from comments, use those first: every video ends with "Comment a claim. We check it next.", so answering them keeps that promise.
2. Search the web for health claims going around now: viral TikTok and Instagram wellness trends, food and supplement myths in this week's news.
3. Pick 3: one plain statement under 12 words that studies can answer, not in `done.txt`, safe under the rules. Prefer claims many people believe.
4. For each, one at a time:
   `node promo/record.mjs --claim "<claim>" --said "<who says it>" --hook "<hook>"`
   `--said` is who is spreading it, shown small above the hook: "TikTok says", "Wellness TikTok says", "Everyone says", "Your gym says". `--hook` is the claim said the way its fans say it, as a bold statement under 8 words, e.g. "Tape your mouth shut. Sleep better.". It must mean the same as the claim being checked. The script prints one JSON line: `ok`, `video`, `cover`, `verdict`, `tldr`, `studies`, `relied_on`, `link`. On failure wait a minute and retry once, then skip.
5. Append each recorded claim to `promo/out/done.txt`.
6. Write `captions.md` next to the videos, in the shape of `promo/out/2026-10-10/captions.md`: the how-to-post checklist, then for each video the files, the verdict and short answer copied exactly, an Instagram caption ("Verdict: <verdict>." in the site's own words, never more certain than the site; studies read and used; "Comment a claim and we'll check it next."; "Check any claim free at checkevident.com (link in bio)"; "Not medical advice."), 6 to 8 hashtags, a TikTok caption, two different reply texts that name checkevident.com (links do not click in Instagram or TikTok comments), the full `link` for Reddit, X or YouTube, and where the claim is trending.
