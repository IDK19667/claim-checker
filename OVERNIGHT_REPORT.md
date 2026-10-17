# Overnight report, 2026-10-16

(The morning summary goes at the top when the run is finished.)

## Log

### Step 1: finish accuracy-round-3 (in progress)

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
