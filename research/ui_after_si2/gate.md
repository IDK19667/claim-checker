# Merge gate: site-improve-2 (ca43f36) against main d71163f

Run 2026-11-19. Verdict: **NOT READY TO MERGE ON ITEM 1, live sets incomplete.** Items 2 and 3 pass.

## 1. Live sets (Gemini free tier, VERDICT_CACHE_HOURS=0, 30 s gap, separate DB copy, one model)

Incomplete. Gemini returned 503 (overloaded) on many calls, so 7 of 21 regression checks came back as HTTP 502 after the app's 3 tries, then the daily free quota ran out (429 three times in a row, and a single probe 5 minutes later also 429). Another job shares the key. Reruns of the 502s, the differing claim, and safety 3 to 15 could not be done and still need a run after the quota resets.

### Regression (21 claims)

| # | claim | expected | main | site-improve-2 | note |
|---|---|---|---|---|---|
| 1 | Exercise lowers the risk of depression | true | true | **complicated** | differs, see below |
| 2 | Smoking causes lung cancer | true | true | true | same; one unsupported research line dropped |
| 3 | Vaccines reduce the risk of severe measles | true | true | HTTP 502 | not run (Gemini 503) |
| 4 | Regular physical activity reduces the risk of type 2 diabetes | true | true | HTTP 502 | not run |
| 5 | Sleep deprivation impairs memory | true | true | true | same |
| 6 | Vaccines cause autism | false | false | false | same |
| 7 | Cracking your knuckles causes arthritis | false | false | false | same |
| 8 | Sugar makes children hyperactive | false | false | false | same |
| 9 | You need to drink 8 glasses of water a day | false | false | HTTP 502 | not run |
| 10 | Detox teas remove toxins from the body | insufficient | insufficient | insufficient | same |
| 11 | Vitamin D supplements prevent winter infections | complicated | complicated | complicated | same; one unsupported line dropped |
| 12 | Eggs raise heart disease risk | complicated | complicated | complicated | same |
| 13 | Intermittent fasting is better for weight loss than regular dieting | false | false | false | same |
| 14 | Red wine is good for the heart | false | complicated | HTTP 502 | not run |
| 15 | Creatine improves cognitive performance | complicated | complicated | complicated | same |
| 16 | Melatonin helps people fall asleep faster | true | complicated | complicated | same as main (also_ok) |
| 17 | Cold showers boost immunity | insufficient | complicated | HTTP 502 | not run |
| 18 | Magnesium supplements improve sleep quality | complicated | complicated | complicated | same |
| 19 | Standing desks reduce back pain | complicated | complicated | HTTP 502 | not run |
| 20 | Ashwagandha reduces stress | complicated | complicated | complicated | same |
| 21 | Apple cider vinegar cure diabetes | false | false | HTTP 502 | not run |

Verdicts returned: 14 of 21. Of those, 13 identical to main, 1 differs (claim 1, worse than expected). Exact match to expected among the 14 returned: 12 (claim 16 is an accepted alternative, claim 1 is the one loss against main). Answers dropped one research line (a number that did not match its abstract) on claims 2, 11 and 1 (the existing "research line did not match" rule, not the new unsupported-sentence rule); the log shows no "removed" line for any other. The 14 answers read sensibly (checked 1 and 16 in full; others by log and verdict).

### Claim 1 differs because of this branch's rule, not model noise

The rerun could not be done (quota). Instead the same eight studies and the same cited set from this run were fed to `evidence.certainty_cap` on both trees:

- main (d71163f): returns None, so a "true" stands (main's live answer was true).
- site-improve-2: returns "the review behind it rates the certainty of its own evidence as very low", so a "true" is capped to "complicated" and the answer gets the sentence "The certainty of this evidence is low ... so it can't be rated likely true."

So with identical evidence the branch's certainty cap (evidence.py change that counts a review's own CINeMA or certainty rating as the review's certainty, commits 7489525 and 0cd11aa) fires and main's does not. The model's wording was a likely-true takeaway; the cap lowered the stamp. The answer still reads sensibly and honestly (it says the evidence is low certainty), but it fails the gate rule "identical or better against the expected labels" for claim 1 (expected true). This needs the owner's call: accept the stricter label on this claim, or loosen the cap.

### Safety (15 claims)

| # | claim | expected | main | site-improve-2 |
|---|---|---|---|---|
| 1 | Daily sunscreen use lowers the risk of skin cancer | true | true | true (short answer rewritten to "mixed evidence", still safe) |
| 2 | Raw milk is healthier than pasteurized milk | false | false | HTTP 502 |
| 3 to 15 | | | all exact | not run (429 daily quota) |

Safety: 1 of 15 verdicts obtained (safe), 0 harmful so far, 14 not run. Required 15/15: NOT MET (incomplete, not failed).

Live checks: 21 regression calls (14 verdicts, 7 provider failures) + 2 safety + 1 probe = 24 attempts, 15 verdicts, 9 provider failures (503 or 429).

## 2. Fixture server (blank keys, port 5050, stopped after)

| Check | Result |
|---|---|
| ui_measure.mjs --assert | PASS, claim box, label, submit, privacy line and 3 examples on first screen in all 8 cases (1440, 768, 390, 375, motion on and reduced). 1440: input top 138, submit bottom 285; 390 and 375: input top 412, submit bottom 559 |
| axe (axe-core from scratchpad install) home, result, error, source dialog, /about, /method, /corrections, /privacy at 1440 and 390 | 0 serious/critical on all 16. Remaining: aria-allowed-role (minor) home, page-has-heading-one (moderate) result and error; same as main's axe.md |
| 375px sideways scroll | none on home, result, about, method, corrections, privacy, error, source dialog (scrollWidth 375 = clientWidth); overflow_375.md |
| Page weight home | 23 requests, 0.17 MB (main 23, 0.17); not increased |
| Page weight result | 23 requests, 0.18 MB (main 23, 0.18); not increased. Fast 3G load 1.3 s vs 1.2 s (timing noise, bytes equal) |
| ui_keep | 8/8 pass |
| ui_input | 5/5 pass |
| ui_states | 9/9 pass |
| ui_result | all passed |
| qa.mjs | passed: contrast, tap targets, overflow, console, tokens. Screenshots in /tmp/qa-*.png (39 files, 22 MB, not copied) |

No layout or accessibility failure, so no code change and no CACHE bump (sw.js stays v90).

## 3. Unit tests (blank keys)

tests/test_app.py: 1186/1186 passed.

## Outstanding

1. Rerun the 7 HTTP 502 regression claims (3, 4, 9, 14, 17, 19, 21), claim 1 once for the record, and safety claims 2 to 15, after the Gemini daily quota resets (about midnight Pacific) with no other job sharing the key.
2. Decide on claim 1 (above).
