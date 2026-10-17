# Overnight report, 2026-10-16

(The morning summary goes at the top when the run is finished.)

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
