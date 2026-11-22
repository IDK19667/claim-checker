# Warning rule, blind set (round 8)

60 sentences written by someone who never saw the rule (tests/warning_blind_q8.json):
30 warnings, 30 reassurances. Each was run through the rule's own keep decision
(`breakdown.never_dropped`, which called `is_warning`) as an unsupported sentence
would be judged in production, with no citation.

Result under the word-list rule (commit 8838dc7):

- Warnings kept: 16 of 30. 14 dropped.
- Reassurances kept: 1 of 30. That is a failure (target 0).

Reassurance kept:

- Death occurred in one participant out of several thousand and was judged unrelated.

Warnings dropped:

- Large amounts of licorice root can cause dangerously low potassium and high blood pressure.
- Do not take ibuprofen with warfarin without medical supervision, because the combination raises the chance of serious bleeding.
- Resuming heavy lifting before a tendon has healed is not without risk of a full rupture.
- It is not safe to abruptly stop corticosteroids after long-term use, as adrenal crisis may follow.
- Never combine alcohol with benzodiazepines, as breathing can slow to a halt.
- Aristolochic acid in some herbal remedies causes kidney failure and urinary tract cancer.
- Fluoroquinolone antibiotics were linked to tendon rupture, especially in people over 60.
- Aspirin should never be given to children with viral illness because of the danger of Reye syndrome.
- Taking potassium supplements alongside ACE inhibitors can cause hyperkalemia that stops the heart.
- Antibiotics were withheld in a number of patients, and some of them died of untreated sepsis.
- Colloidal silver builds up in tissue and can permanently turn the skin blue-grey.
- Older adults on sedative-hypnotics face a markedly higher risk of falls and hip fractures.
- Stopping beta blockers abruptly can trigger dangerous heart rhythms and heart attack.
- Extended fasting in people with type 1 diabetes can lead to life-threatening ketoacidosis.

Decision (fixed in advance): one reassurance kept, so the word lists are not tuned
against this set. Instead a sentence that no cited study supports is removed,
even a warning. Supported sentences are untouched. The only unsupported sentence
still kept says the claim went untested and names no harm.

Result after the change: 0 of 60 kept, 0 of 30 reassurances kept (test "blind q8").

## Saved answers (97 records, a copy of the queue-6 source db; the original was not written)

Compared with main d71163f (which shows every sentence raw), 158 sentences that
name a harm or hazard are shown on main. 15 (in 10 records) are removed on this
branch. None is supported by a cited study: each cites a study the verdict did
not rely on (or study 4, which is not in the record),. Under the previous caution rule these were kept without a citation.

- cited [4] relied [1, 2] | Apple cider vinegar cures diabetes take two | Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.
- cited [4] relied [1, 2] | Apple cider vinegar cures diabetes take three | Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.
- cited [4] relied [1, 2] | Apple cider vinegar cures diabetes take four | Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.
- cited [4] relied [1, 2] | Apple cider vinegar cures diabetes take five | Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.
- cited [4] relied [1, 2] | Apple cider vinegar cures diabetes phone one | Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.
- cited [4] relied [1, 2] | Reduced motion still frame check | Neither tested whether diabetes went away, and study 4 is a reminder that undiluted vinegar can injure the oesophagus.
- cited [6, 8] relied [1, 2, 7] | Raw milk is healthier than pasteurized milk | Raw milk consumption is linked to outbreaks of campylobacteriosis and brucellosis (Study 6, Study 8).
- cited [3] relied [4] | Antibiotics cure the common cold | Antibiotics should not be used for the common cold or influenza (Study 3).
- cited [3] relied [4] | Antibiotics cure the common cold | Most acute nose and throat infections are caused by viruses, not bacteria (Study 3).
- cited [3] relied [4] | Antibiotics cure the common cold | Evidence states that antibiotics should not be used for the common cold (Study 3).
- cited [2, 3] relied [4] | Antibiotics cure the common cold | Bacterial infections such as acute bacterial sinusitis or group A streptococcal pharyngitis (Studies 2, 3).
- cited [3] relied [4] | Antibiotics cure the common cold | Viruses cause most nose and throat infections and antibiotics should not be used for them (Study 3).
- cited [7] relied [2, 6] | Drinking a little alcohol during pregnancy is | Alcohol is a well-established teratogen that can cause variable physical and behavioral effects on the fetus (Study 7).
- cited [3] relied [2, 6] | Drinking a little alcohol during pregnancy is | Indirect evidence shows various functional and structural abnormalities in children with prenatal alcohol exposure (Study 3).
- cited [5] relied [1, 2] | Cracking your knuckles causes arthritis | People with acute physical injuries from forceful manipulation (Study 5).

Lost but supported by a cited study: 0.
Of the 15, the real warnings are the raw milk outbreaks line, "Antibiotics should not be used" (2), the alcohol teratogen line, the abnormalities line, and the vinegar "can injure the oesophagus" line on six fixture records (a mixed sentence: an untested statement plus a harm clause, dropped whole). The rest name a harm word only incidentally (who-lines, "viruses, not bacteria").
