# Non-live merge gate: polish (a1f171c) against main d71163f

Run 2026-11-19. All servers and tests ran with blank keys (no Gemini or other paid calls). Fixture server on port 5050 (polish) and 5051 (main, ui-rebuild worktree), each on a fresh copy of src.db. Live sets (regression, safety) are not part of this file.

Summary: everything PASS except one item, the 320 px first-screen check at 568 px viewport height (FAIL, identical on main).

| # | Check | Result |
|---|---|---|
| 1 | Unit tests (tests/test_app.py) | PASS, 1319/1319 |
| 2 | qa.mjs | PASS: contrast, tap targets, overflow, console, tokens |
| 3 | ui_keep | PASS, 8/8 |
| 4 | ui_input | PASS, 5/5 |
| 5 | ui_states | PASS, 11/11 |
| 6 | ui_result | PASS, all passed |
| 7 | ui_footer | PASS, all passed |
| 8 | ui_measure --assert | PASS: field, label, submit, privacy line and 3 examples on the first screen in all 8 cases. 1440: input top 138, submit bottom 285; 390 and 375: input top 412, submit bottom 559 |
| 9 | axe-core, serious and critical, 8 pages x 2 widths | PASS: 0 serious/critical (in fact 0 violations of any impact) on home, result, error, /method, /about, /privacy, /corrections, /checks at 375 and 1280 |
| 10 | Sideways scroll at 375 | PASS: scrollWidth 375 = clientWidth 375 on all 8 pages (home, result, error, privacy, method, about, corrections, checks) |
| 11 | Claim box and submit button on first screen | PASS at 375x812 (input 412 to 488, submit 509 to 559), 768x1024 (457 to 532, 553 to 603), 1280x800 (138 to 214, 235 to 285). 320: see below |
| 12 | Page weight vs main | PASS, not increased (table below) |

## 11a. 320 px first screen

| viewport | input (top, bottom) | submit (top, bottom) | both inside first screen |
|---|---|---|---|
| 320x568 | 504, 579 | 600, 650 | **FAIL** (input cut at 568, submit starts below the fold) |
| 320x640 | 504, 579 | 600, 650 | **FAIL** (submit bottom 650 > 640) |
| 320x667 | 504, 579 | 600, 650 | PASS |
| 320x700 | 504, 579 | 600, 650 | PASS |

The positions are the same on main (d71163f) at every height, so this is not a regression from polish. Only the very short 320x568 and 320x640 phones are affected; the check in the earlier gate (ui_measure --assert) does not include 320. No sideways scroll at 320 (scrollWidth 320).

## 12. Page weight (page_weight.mjs, first load of "/", fixture server)

| viewport | polish requests | polish MB | polish Fast 3G load | main requests | main MB | main Fast 3G load |
|---|---|---|---|---|---|---|
| 375x812 phone | 23 | 0.17 | 1.1 s | 23 | 0.17 | 1.1 s |
| 375x812 plain viewport | 23 | 0.17 | 1.1 s | 23 | 0.17 | 1.1 s |
| 1440x900 desktop | 23 | 0.17 | 1.2 s | 23 | 0.17 | 1.1 s |

Frame/bundle requests: 0 on both. The 0.1 s desktop load difference is timing noise (other scripts ran at the same time); request count and bytes are equal.

## Notes

- ui_footer is a script not run in the si2 gate; it passed.
- Screenshots for a later reviewer (home, result, error, /privacy, at 375 and 1280, light and dark; 16 JPEGs, 0.7 MB) are in the session scratchpad at q8/gate_shots/, not committed.
- The fixture server replays one vitamin D answer for any live claim; the result pages therefore show that answer.
