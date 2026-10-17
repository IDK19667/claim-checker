# Overnight report, 2026-10-16

## Morning summary

**1. What's done, and where.** Everything is on one branch,
`accuracy-round-3`, pushed to GitHub. Main is untouched and nothing was
deployed.
- Step 1, done. The rewrite step never drops a hedge like "slightly" or
  "may". 21-claim regression: 19 right, 2 accepted, 0 wrong.
- Step 2, done. 20 fresh claims graded (table below).
- Step 3, done. Seven general fixes, each with tests. One more was tried
  and taken back out. On the finished code the 21 regression claims came
  out 18 right, 3 accepted, 0 wrong. 618 of 618 tests pass, and the
  design QA check passes.
- Step 4, nothing to do. All six launch-polish items were already on
  main since 9 October, so I made no empty branch.

**2. Holdout #2: FAIL.** 16 of 20 verdicts right (the bar is 16). 13 of
20 right with no misleading text (the bar is 13). 1 harmful miss (the bar
is 0): "Antibiotics cure the common cold" was stamped "It's complicated".
Its cause is fixed (c11b16b). As you asked, I did not re-run the holdout
claims, so a third fresh holdout is needed to show the fixes work.

**3. Merge: not ready yet.** The blocker is a third fresh holdout of 20
new claims, which has to pass the same bar. Merging on its own does not
deploy (Render waits for your Manual Deploy click). When holdout #3
passes, this one line merges it:

```bash
git checkout main && git pull --ff-only origin main && git merge --ff-only accuracy-round-3 && git push origin main
```

**4. What broke or was skipped.**
- The regression run caught a new harmful answer on this branch:
  "Apple cider vinegar cure diabetes" was stamped True. The cause was a
  rule added earlier in this round ("trials outrank weaker studies"),
  which overruled the model's correct no. Fixed (706c845) and checked
  twice since: False both times.
- I tried a search-prompt change for claims like "homeopathy works better
  than a placebo" and took it back out. Changing that prompt gives every
  claim a new search, and the water claim went wrong.
- Left unfixed, because each needs a prompt change or a careful one:
  - MSG called "mixed" when the review says it's unproven.
  - Creatine's page said a trial found "nobody lost hair", but that trial
    never measured hair.
  - One small trial can still produce the word "Strong".
  - The search missed the Cochrane review on antibiotics for colds.
  - A few wording slips.
- Melatonin now comes out "It's complicated" (an accepted answer) instead
  of True.
- No Gemini rate limit or daily quota limit was hit. The holdout and
  every regression run used Gemini only, with the backups switched off.
  The holdout used a separate scratch database.

## Log

### Step 1: finish accuracy-round-3 (done)

**1a. The faithfulness rewrite never drops a hedge.** Done, commit
54c3091. A rewritten short answer is refused if it only takes a hedge out
("slightly", "may", "small", "some", "low certainty" and the rest), or if
it turns a plain yes or no into anything else. A rewritten research line
that loses its hedge is dropped, never printed surer. Tests added.

Found by the regression run, two real cases:
- Exercise and depression: "slightly lowers" became "lowers".
- Sugar and hyperactivity: "Sugar does not make children hyperactive"
  became "mixed results, some trials find no effect while others show a
  link". That is the tie rule 1 forbids, under a False stamp.

**1b. Regression run (21 claims), first pass, on the code before these
fixes.** Running. Interim: 13 of the first 13 that finished matched or
were accepted, except:
- Claim 3, measles: "It's complicated", should be True. A regression from
  this round, cause found and fixed (commit b0ca73c, a pattern, not the
  claim). My rule-2 code cut review findings at every semicolon, even
  inside brackets, so "moderate certainty" lost the word "measles" it
  belonged to. The review's lowest rating anywhere (a mumps one) then
  capped the verdict. Now ratings stay with their finding, and only a
  review that rates the outcome low and nowhere higher can lower a
  verdict. The certainty line gives the review's own range ("Low to
  moderate certainty").
- Claims 16 and 18: PubMed was briefly unreachable, so the app showed its
  normal "try again" error. To be re-run.

Also fixed from the run (commit e4f64d7): "95% confidence interval"
written out is now "likely range", like "95% CI" already was.

**1b, finished. Regression result on the final code: 19 exact, 2
accepted, 0 wrong.** (Baseline was 18 exact, 3 accepted, 0 wrong, so one
better.) Measles is True again. Claims 16 and 18 were re-run after the
PubMed outage and both matched. The two accepted ones are the same kind as
before: red wine and heart health, cold showers and immunity, both "It's
complicated" where the list says something firmer and the reviews allow
either. No dash appeared on any page.

More fixes the run's text turned up, each a pattern and each with tests:
- Commit 7bd6597: PubMed sometimes turns a request away for a moment
  (that was the outage on 16 and 18). The app now spaces its PubMed
  calls a little and asks once more after a short wait, instead of
  showing the "try again" page straight away.
- Commit 020f1fb: when the app refuses a rewrite of the short answer, it
  writes a line in the server log saying so, so we can see how often.
- Commit 98efbab: Hedges' g, Cohen's d, "an SMD of" and a doubled word
  ("pooled pooled") now read in plain words.
- Commit 0332d51: "vaccine effectiveness" stays as written (it had
  printed "assess vaccine how well it works"), "lacked efficacy" reads
  "lacked an effect", and "meta analysis" without its hyphen and "sleep
  onset latency" are said in words.

Seen but left alone on purpose: one page said "these robust trials".
Words like "robust" are logged, not rewritten, which was an earlier
decision (rewriting them changed meaning too often). Worth a look later.

**1c. Tests and QA.** 606 of 606 tests pass. The QA gate passes
(contrast, tap targets, overflow, console, tokens, at both widths in both
schemes), and I looked at the phone result screenshot. Branch
accuracy-round-3 pushed.

Note: in the earlier session, one local QA run did a real check of
"Apple cider vinegar cures diabetes" on my machine. It used a little of
the free Gemini quota and nothing else.

### Step 2: second fresh holdout (done, report only, nothing changed)

20 new claims, none in the regression set or holdout #1, written down with
their expected answers before the run (5 true, 7 false, 5 mixed, 3
untested). Run on accuracy-round-3 on my machine, Gemini only (the backup
AIs switched off), with a separate scratch database, 30 seconds between
checks. No rate limits were hit; every check took 7 to 10 seconds.

I graded each one by reading the whole page (short answer, reasons, the
line under the chart, the breakdown), against Cochrane, WHO and the major
reviews. "Accepted" means a second answer I had written down as fair
before the run.

| # | Claim | Expected | Shown | Verdict | Text |
|---|---|---|---|---|---|
| 1 | Psyllium husk lowers LDL cholesterol | True | True | exact | clean |
| 2 | Beetroot juice lowers blood pressure | True | Complicated | accepted | clean |
| 3 | Caffeine improves exercise performance | True | True | exact | clean |
| 4 | Probiotics prevent diarrhea caused by antibiotics | True | True | exact | clean |
| 5 | Omega-3 supplements lower triglycerides | True | True | exact | clean |
| 6 | Antiperspirants cause breast cancer | False | False | exact | not clean: "Strong evidence" for a review of case-control studies only |
| 7 | Vitamin C supplements prevent colds | False | Complicated | accepted | clean |
| 8 | MSG causes headaches | False | Complicated | **wrong** | "can cause headaches in some tests" |
| 9 | Ab exercises burn belly fat | False | Not enough evidence | **wrong** | search found 8 off-topic papers |
| 10 | Antibiotics cure the common cold | False | Complicated | **wrong, HARMFUL** | "the studies were all in children" (they were not) |
| 11 | Homeopathy works better than a placebo | False | Not enough evidence | **wrong** | search missed the placebo reviews |
| 12 | Coconut oil is good for your heart | False | Complicated | accepted | clean |
| 13 | Artificial sweeteners help you lose weight | Complicated | Complicated | exact | not clean: "Strong evidence" rests on a 17-person, 14-day gut trial |
| 14 | Ice baths speed up muscle recovery | Complicated | Complicated | exact | clean |
| 15 | Turmeric reduces joint pain | Complicated | Complicated | exact | clean |
| 16 | Berberine helps you lose weight | Complicated | Complicated | exact | clean |
| 17 | Acupuncture relieves chronic back pain | Complicated | True | accepted | clean (low to moderate certainty shown) |
| 18 | Creatine causes hair loss | Not enough | False | accepted | not clean: says a 2009 trial found "nobody lost hair"; it never looked at hair |
| 19 | Ivermectin cures cancer | Not enough | Not enough | exact | clean |
| 20 | Chlorophyll water clears acne | Not enough | Not enough | exact | clean |

**Scores**
- Exact verdict: 11 of 20.
- Verdict right (exact or accepted): 16 of 20. Bar is 16. Meets it, just.
- Verdict right AND no misleading text: 13 of 20. Bar is 13. Meets it, just.
  (I counted "Strong evidence" on 6 and 13 as misleading, because it
  overstates how sure the evidence is. Counted leniently it would be 15.)
- Harmful misses: 1. Bar is zero.

**Result: FAIL**, on the harmful miss alone.

**The harmful miss, claim 10.** "Antibiotics cure the common cold" was
stamped "It's complicated", with the short answer "The studies found were
all in children, so they answer this for them and not for people in
general." The reasons on the same page say plainly that antibiotics do not
work for colds. The model had answered False; code overruled it. The one
paper it relied on is a general review of ear, nose and throat infections
whose abstract mentions children, and the rule that spots "a study run in
one narrow group" read a review's abstract as if it were a trial's list of
who was enrolled. A reader could take "complicated" as "antibiotics might
help adults with colds", which is exactly the misuse the claim spreads.
Also, the main Cochrane review on this question was not found.

**The other misses, none harmful:**
- 8, MSG: the systematic review says the studies were poorly blinded and
  inconsistent and that it is not established either way. The page called
  it mixed and led with "can cause headaches in some tests".
- 9, ab exercises: all 8 papers found were about exercise and body fat in
  general, none about working one spot, so it said "not enough evidence".
  The trials that test this exist.
- 11, homeopathy: the search turned "better than a placebo" into general
  words like "treatment effect" and found 5 old narrative reviews. The big
  placebo reviews were missed. It said no good study tests this.

Small wording faults seen but not counted against the text: "a drop of
-5.31 mm Hg" (a minus sign after "drop"), "ergogenic" not in plain words.
No dashes appeared on any page.

### Step 3: fix the patterns holdout #2 showed (done)

Every fix is general, has tests, and names no holdout claim. None of the
holdout #2 claims was re-run afterwards: they would no longer be a fair
test. **A third fresh holdout is needed** to know whether these fixes work
on claims nobody has tuned for.

| Commit | What it fixes | Holdout #2 case that showed it |
|---|---|---|
| c11b16b | A review or guideline enrolled nobody, so its abstract no longer makes the evidence "only in children". It is read from its title, like a pooled review already was. | 10, antibiotics and colds (the harmful miss) |
| e2e739c | A British spelling or a plural is the same search term ("homoeopathic", "abdominal exercises"). | 11, homeopathy (its main review was found, then thrown away); 9 |
| cf1b67a | A phrase PubMed has never seen is not searched as loose words ("ab exercises" had become "abnormalities AND exercise"). | 9, ab exercises (8 off-topic papers) |
| 04561b1 | "Strong evidence" needs more than a pooled review of observational studies; that now says "Moderate". | 6, antiperspirants |
| 6d91de7, 526bb2c | A fall is said once: no minus sign after "drop" or "fell by", or on its range. | seen on 2 and 5 |
| 98fa0a8 | "Safety and efficacy of X" reads "safety and effect of X", not "how well it works of X". | seen in the regression run |
| 706c845 | The "trials outrank weaker studies" rule never turns the model's no into a yes. | found by the regression run, see below |

**Tried and taken back out (039d7f8 reverts 428de9c).** A line in the
search prompt telling it to search "placebo or sham" when a claim names
no outcome (for 11, homeopathy). Any change to that prompt's wording
makes every claim get a brand new search, here and in production, and in
the regression run "8 glasses of water a day" drifted to the wrong studies
and to "It's complicated". Too broad to leave in while you were asleep.
The spelling fix already keeps the main homeopathy review. A narrower
idea for later is in DECISIONS.md.

**Regression runs (21 claims, Gemini only, 30 seconds apart):**
- With the placebo line: stopped after water went wrong (see above).
- Without it: 17 exact, 3 accepted, **1 wrong**. "Apple cider vinegar
  cure diabetes" was stamped **True**, which is harmful. The model had
  answered no ("lowers blood sugar a bit, does not cure diabetes"), but it
  marked the trials "for" because they lowered blood sugar, and the rule
  that trials outrank weaker studies (added earlier in this round) turned
  the stamp to True. Fixed as a pattern (706c845): that rule can still
  settle "It's complicated" and can still turn a yes into a no when large
  trials found nothing, but never turns a no into a yes. In every run
  tonight the rule fired only this once. Re-run: False, and the page
  reads right.
- Melatonin moved from True to "It's complicated", which the list
  accepts. Same search, same five reviews; the model weighed the review
  that found no effect in adults with chronic insomnia more heavily. One
  slip in its text: it says "healthy adults" where the review says adults
  with chronic insomnia. Not fixed (model wording).
- Final run on the finished code: **18 exact, 3 accepted, 0 wrong.** The same as the original baseline
  (18, 3, 0); one fewer exact than step 1 (19, 2, 0) because melatonin is
  now "It's complicated" twice in a row. Vinegar: False, "does not cure
  diabetes, even though it can help lower blood sugar a bit". The trials
  rule did not fire anywhere. No dash on any page. Small text faults seen,
  not fixed: melatonin says "lack clear how well it works" and "healthy
  adults" again; the measles page gives the size of the effect for autism
  (no link), not for measles.

**Patterns from holdout #2 left unfixed, and why:**
- MSG and headaches called "mixed" when the review says unproven: that is
  the model's judgement, and changing it means a prompt change (see the
  placebo note above for why I did not do that unattended).
- Creatine: the page said a 2009 trial found "nobody lost hair"; that
  trial never measured hair. The faithfulness check did not catch an
  outcome the study never looked at. Needs a careful change to that
  check.
- One small, short trial on the wrong outcome can still produce the word
  "Strong" (artificial sweeteners). Written down in DECISIONS.md.
- The main Cochrane review on antibiotics for colds was not found by the
  search. Search coverage, not fixed.
- "Ergogenic" is not put in plain words; "robust" is logged, not
  rewritten (an earlier decision).

**Tests and checks:** 618 of 618 tests pass. The QA gate passes
(contrast, tap targets, overflow, console, tokens, both widths and both
colour schemes). No shell files changed, so the service worker cache
stays at evident-v70. Branch accuracy-round-3 pushed.

### Step 4: launch polish (nothing to do, no branch made)

All six items were already built and merged to main on 2026-10-09:

| Item | Commit on main |
|---|---|
| (a) the fly-through's animation loop rests when parked, off screen or settled | afce49c |
| (b) phones get one tier of footage, loaded after the first scroll | 5615a36 |
| (c) reduced motion and rotation followed live during the visit | 195455f |
| (d) nothing plays when the tab is hidden (body.paused) | 5181bc9 |
| (e) the page cannot be pushed sideways on a phone (overflow-x clip) | b1c65b3 |
| (f) the "Before you trust a verdict" section (where studies come from, is an AI making this up, who made it, is it free, not medical advice) | 2ebc3b8 |

accuracy-round-3 already contains them, and tonight's QA gate checked
each one (fly-through rests, live gates, pause when unseen, overflow,
trust section). A launch-polish branch would have had nothing new in it,
so I did not make one.
