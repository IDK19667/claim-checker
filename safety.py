"""
The safety gate for topics where a wrong answer can hurt someone.

Cancer screening or treatment, vaccines, a change to a medicine (stopping,
starting, swapping, a different dose), pregnancy and children are read from
the claim and the search terms. On those topics a confident "true" or "false"
needs evidence that can carry it, and when it does not have it the stamp is
"Not enough evidence" and the page tells the reader to talk to a doctor.

Thin: fewer than two studies in people were relied on and none of them is a
pooled review, or none is a randomized trial, meta-analysis or systematic
review. Mixed: the relied-on studies point different ways. A major health
guideline that agrees with the stamp counts as strong evidence and passes
both tests, so a well-supported answer ("the whooping cough vaccine in
pregnancy protects newborns", "vaccines do not cause autism") is not held
back. Nothing here names a claim: it reads topics, and designs.
"""
from __future__ import annotations

import re

import evidence

_VERBS = (r"(?:stop\w*|start\w*|switch\w*|swap\w*|replac\w*|skip\w*|quit\w*|miss\w*|"
          r"halv\w*|doubl\w*|taper\w*|discontinu\w*|com(?:e|ing) off|go(?:ing)? off|"
          r"giv(?:e|ing) up|ditch\w*|instead of|resum\w*|adjust\w*)")
_DOSE_VERBS = r"(?:increas\w*|reduc\w*|lower\w*|cut\w*|chang\w*|doubl\w*|halv\w*|adjust\w*|skip\w*|miss\w*)"
_MEDS = (r"(?:medicat\w*|medicines?|drugs?|pills?|prescri\w+|tablets?|statins?|insulin|"
         r"antibiotics?|antidepressants?|antipsychotics?|anticoagulants?|blood thinners?|"
         r"metformin|levothyroxine|inhalers?|steroids?|opioids?|painkillers?|"
         r"blood pressure (?:meds?|tablets?|pills?)|birth control|contracepti\w+|"
         r"treatments?|therap(?:y|ies)|doses?|dosage)")

TOPICS = {
    "cancer": re.compile(
        r"\b(?:mammogra\w*|colonoscop\w*|pap (?:smear|test)s?|psa|biops\w*|"
        r"(?:cancers?|tumou?rs?|oncolog\w*|chemo\w*|radiotherapy|melanoma|leuk(?:a)?emia|lymphoma)\b"
        r".{0,60}\b(?:screen\w*|treat\w*|therap\w*|chemo\w*|radiat\w*|surger\w*|cur(?:e|es|ed)|"
        r"remed\w*|drugs?|medic\w*|detect\w*|diagnos\w*|immunotherap\w*)|"
        r"(?:screen\w*|treat\w*|therap\w*|chemo\w*|radiat\w*|surger\w*|cur(?:e|es|ed)|"
        r"remed\w*|detect\w*|diagnos\w*|immunotherap\w*)\b.{0,60}\b"
        r"(?:cancers?|tumou?rs?|oncolog\w*|melanoma|leuk(?:a)?emia|lymphoma)|"
        r"chemo\w*|radiotherapy)\b", re.IGNORECASE),
    "vaccine": re.compile(
        r"\b(?:vaccin\w*|immuni[sz]ations?|booster shots?|flu shots?|jabs?|mmr|hpv|"
        r"dtap|tdap)\b", re.IGNORECASE),
    "medication": re.compile(
        rf"\b{_VERBS}\b.{{0,50}}\b{_MEDS}\b|\b{_MEDS}\b.{{0,50}}\b{_VERBS}\b|"
        rf"\b{_DOSE_VERBS}\b.{{0,30}}\b(?:doses?|dosage)\b", re.IGNORECASE),
    "pregnancy": re.compile(
        r"\b(?:pregnan\w*|prenatal|antenatal|trimesters?|miscarriage|f(?:o)?etus|f(?:o)?etal|"
        r"expecting mothers?|morning sickness|gestation\w*|breastfe\w*|lactati\w*|"
        r"conceiv\w*|preconception)\b", re.IGNORECASE),
    "children": re.compile(
        r"\b(?:child|children|kids?|infants?|babies|baby|toddlers?|newborns?|neonat\w*|"
        r"teen(?:ager)?s?|adolescen\w*|pa?ediatric\w*|boys|girls|preschool\w*)\b", re.IGNORECASE),
}

SEE_DOCTOR = "This touches a health decision: talk to a doctor before acting on it."
THIN_TLDR = ("The studies found are too few or too weak to answer this safely, so there is not "
             "enough evidence to rate it.")
MIXED_TLDR = ("The studies found point different ways on a question that affects health, so "
              "there is not enough evidence to rate it.")
THIN_NOTE = ("This is a topic where a wrong answer can hurt, and fewer than two studies in "
             "people, or no randomized trial or review, stand behind it.")
MIXED_NOTE = ("This is a topic where a wrong answer can hurt, and the studies relied on do not "
              "point the same way.")
CODE_TLDRS = (THIN_TLDR, MIXED_TLDR)


def topics(claim: str, query: str = "") -> list[str]:
    """The risky topics the claim and the search terms touch, in a fixed order."""
    text = f"{claim or ''} {query or ''}"
    return [name for name, pattern in TOPICS.items() if pattern.search(text)]


def see_doctor(claim: str, query: str, verdict: str) -> str | None:
    """The line telling a reader to see a doctor: on a risky topic whenever
    the answer is not a plain yes or no."""
    if verdict in ("insufficient", "complicated") and topics(claim, query):
        return SEE_DOCTOR
    return None


def _pooled(study: dict) -> bool:
    return evidence.is_review(study)


def _strong(study: dict) -> bool:
    return evidence.design_rank(study) == "trial" or _pooled(study)


def judge(studies: list[dict], cited_pmids, stance: dict | None = None,
          directions: dict | None = None) -> str:
    """"thin", "mixed" or "" for the studies relied on. `stance` maps a study
    number to the model's for/against/mixed reading, `directions` to the
    checked direction of its finding; both are keyed by study number."""
    cited = set(cited_pmids or ())
    relied = [(i + 1, s) for i, s in enumerate(studies) if s.get("pmid") in cited
              and not s.get("background")]
    strong = [(n, s) for n, s in relied if _strong(s)]
    if (len(relied) < 2 and not any(_pooled(s) for _, s in relied)) or not strong:
        return "thin"
    # Randomized trials speak for the evidence when there are any: a review
    # of observational studies that points the other way counts for less.
    trials = [(n, s) for n, s in strong if evidence.design_rank(s) == "trial"]
    strong = trials or strong
    stance = stance or {}
    reading = {stance[n] for n, _ in strong if stance.get(n) in ("for", "against", "mixed")}
    if "mixed" in reading or {"for", "against"} <= reading:
        return "mixed"
    dirs = {(directions or {}).get(n) for n, _ in strong} - {None}
    if "mixed" in dirs or {"improved", "worsened"} <= dirs:
        return "mixed"
    return ""


def gate(result: dict, studies: list[dict], claim: str, query: str = "",
         stance: dict | None = None, directions: dict | None = None) -> dict:
    """
    Force a confident verdict on a risky topic to "insufficient" when the
    evidence is thin or mixed. A guideline that agrees with the stamp is
    strong evidence and lets it stand. Returns `result` unchanged, or a copy
    with the stamp, the short answer and the explanation replaced.
    """
    verdict = result.get("verdict")
    if verdict not in ("true", "false") or not topics(claim, query):
        return result
    if result.get("guideline") == verdict:
        return result
    why = judge(studies, result.get("cited_studies") or (), stance, directions)
    if not why:
        return result
    tldr, note = (THIN_TLDR, THIN_NOTE) if why == "thin" else (MIXED_TLDR, MIXED_NOTE)
    explanation = (str(result.get("explanation") or "").rstrip() + " " + note).strip()
    return {**result, "verdict": "insufficient", "tldr": tldr, "explanation": explanation,
            "safety": why}
