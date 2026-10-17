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
