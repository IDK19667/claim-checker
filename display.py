"""
What the result page prints, worked out from fields the check already saved.

Nothing here asks a model for anything and nothing here changes an answer:
it only decides which of the saved sentences are shown where, so the same
sentence is never printed twice on one page. static/app.js carries the same
rules (breakdownView) for a check that has just run, and a test runs both on
the same records and compares them.
"""
import re

WHO_LABEL = "Who was studied: "

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])")


def split_sentences(text) -> list:
    return [s.strip() for s in _SENTENCE_END.split(str(text or "").strip()) if s.strip()]


def _key(sentence: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", sentence.lower())[:80]


def unseen(text, seen: set) -> str:
    """`text` without the sentences already printed; what is left is marked printed."""
    out = []
    for sentence in split_sentences(text):
        k = _key(sentence)
        if k and k not in seen:
            seen.add(k)
            out.append(sentence)
    return " ".join(out)


def breakdown_view(result):
    """
    The breakdown as the page prints it, or None when the record has none.

    "What the research says" is picked from the same paragraphs the breakdown
    prints, and says its who-line from the same field as "Who this applies
    to". Printing the breakdown as it stands therefore says several sentences
    twice, the effect size most of all. Here the lines of "What the research
    says" come first, and every later block loses the sentences already shown.
    """
    if not result:
        return None
    bd = result.get("breakdown")
    if not bd:
        return None
    says = [ln for ln in (bd.get("says") or []) if str(ln or "").strip()]
    if not says and str(result.get("explanation") or "").strip():
        says = [result["explanation"]]
    # The who-line is the applies_to sentence again, with a label. The block
    # that says who it applies to is the one that keeps it.
    says = [ln for ln in says if not ln.lower().startswith(WHO_LABEL.strip().lower())]
    if not says and str(result.get("explanation") or "").strip():
        says = [result["explanation"]]
    seen = set()
    for ln in says:
        for s in split_sentences(ln):
            seen.add(_key(s))
    for s in split_sentences(result.get("still_open")):
        seen.add(_key(s))

    parts = []
    for p in bd.get("parts") or []:
        take = unseen(p.get("assessment"), seen)
        if take:
            parts.append({"part": p.get("part") or "", "assessment": take})
    evidence = [x for x in (unseen(para, seen) for para in bd.get("evidence") or []) if x]
    return {
        "says": says,
        "parts": parts,
        "evidence": evidence,
        "effect_size": unseen(bd.get("effect_size"), seen),
        "concluded": bd.get("concluded") or [],
        "strength": unseen(bd.get("strength"), seen),
        "applies_to": unseen(bd.get("applies_to"), seen),
        "populations": bd.get("populations") or [],
        "not_applies_to": unseen(bd.get("not_applies_to"), seen),
        "unknowns": unseen(bd.get("unknowns"), seen),
    }


def confidence_line(result) -> str:
    """
    How much to lean on the answer, in one sentence, from fields already saved.

    The wording of a real check is evidence.evidence_line, worked out on the
    server when it ran. A saved record without it says what its studies were,
    which is all the old level word ever measured.
    """
    studies = result.get("studies") or []
    cited = [s for s in studies if s.get("cited_in_verdict")]
    if not studies:
        return "No direct evidence either way: nothing on PubMed matched well enough to weigh."
    if not cited:
        return "No direct evidence either way: none of the studies tests this claim."
    line = str(result.get("evidence_line") or "").strip().rstrip(".")
    if line:
        return line + "."
    tiers = {s.get("tier") or "moderate" for s in cited}
    word = "Strong" if "strong" in tiers else "Moderate" if tiers - {"weak", "retracted"} else "Weak"
    return f"{word} study designs."
