"""
Two small AI calls that sit around the PubMed lookup:

  1. extract_search_terms(claim) -> a good PubMed search string for the claim
  2. weigh_evidence(claim, studies) -> a verdict + plain-English explanation
     that actually weighs study quality (a large clinical trial should
     outrank a single small preliminary study), instead of treating
     every result as equally strong evidence.

Provider selection (checked in this order):
  - LLM_PROVIDER env var, if set ("gemini" or "anthropic")
  - GEMINI_API_KEY set     -> Gemini (free tier available, no card needed)
  - ANTHROPIC_API_KEY set  -> Claude
  - neither                -> RuntimeError with setup instructions

Note on the Gemini free tier: Google's terms say free-tier content may be
used to improve their products. The paid tier does not. Worth knowing
before pointing a classroom at it.
"""

import json
import os
import re

import breakdown
import evidence
import pubmed

VALID_VERDICTS = ("true", "false", "complicated")

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

_anthropic_client = None
_gemini_client = None


class ProviderError(Exception):
    """
    Normalized failure from whichever provider is in use, so app.py can
    map it to a sensible HTTP response without knowing the provider.
    kind: "auth" | "rate_limit" | "unavailable" | "other"
    """

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def _key(name: str) -> str | None:
    """Env var value, treating the .env.example placeholders as unset."""
    v = (os.environ.get(name) or "").strip()
    if not v or v.startswith("your_"):
        return None
    return v


def provider() -> str:
    forced = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if forced in ("gemini", "anthropic"):
        return forced
    if _key("GEMINI_API_KEY"):
        return "gemini"
    if _key("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError(
        "No AI provider key is set. Copy .env.example to .env and add either "
        "GEMINI_API_KEY (free at aistudio.google.com) or ANTHROPIC_API_KEY "
        "(console.anthropic.com)."
    )


def model_name() -> str:
    return GEMINI_MODEL if provider() == "gemini" else ANTHROPIC_MODEL


def provider_label() -> str:
    """Plain-English name of the AI service, for the privacy page. Never raises."""
    try:
        return "Google's Gemini API" if provider() == "gemini" else "Anthropic's Claude API"
    except RuntimeError:
        return "an AI model API"


# ---------------------------------------------------------------------
# Provider back-ends. Each exposes the same two primitives:
#   _text(prompt, max_tokens)          -> str
#   _json(prompt, schema, max_tokens)  -> (raw_text, stop_note)
# stop_note is None on a normal finish, else a short reason string.
# ---------------------------------------------------------------------

def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        api_key = _key("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env "
                "and add your key from console.anthropic.com."
            )
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def _anthropic_text_of(message) -> str:
    # Current models can return thinking blocks ahead of the text, so
    # message.content[0] is not guaranteed to be text.
    return "".join(b.text for b in message.content if b.type == "text").strip()


def _anthropic_wrap(fn):
    import anthropic

    try:
        return fn()
    except anthropic.AuthenticationError:
        raise ProviderError("auth", "The ANTHROPIC_API_KEY in .env was rejected. "
                                    "Check it at console.anthropic.com.")
    except anthropic.RateLimitError:
        raise ProviderError("rate_limit", "The AI service is rate-limited right now.")
    except anthropic.APIConnectionError:
        raise ProviderError("unavailable", "Couldn't reach the AI service.")
    except anthropic.APIStatusError as e:
        raise ProviderError("other", f"The AI service returned an error ({e.status_code}).")


def _anthropic_text(prompt: str, max_tokens: int) -> str:
    client = _anthropic()

    def go():
        m = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            # Trivial task: no reasoning needed, and thinking tokens would
            # otherwise count against max_tokens.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        return _anthropic_text_of(m)

    return _anthropic_wrap(go)


def _anthropic_json(prompt: str, schema: dict, max_tokens: int):
    client = _anthropic()

    def go():
        m = client.messages.create(
            model=ANTHROPIC_MODEL,
            # Thinking is on by default for this model and counts against
            # max_tokens, so leave room for it plus the short JSON answer.
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if m.stop_reason == "refusal":
            return "", "declined to answer this one"
        if m.stop_reason == "max_tokens":
            return "", "ran out of room before finishing"
        return _anthropic_text_of(m), None

    return _anthropic_wrap(go)


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        api_key = _key("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add "
                "your free key from aistudio.google.com."
            )
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _gemini_wrap(fn):
    from google.genai import errors

    try:
        return fn()
    except errors.ClientError as e:
        code = getattr(e, "code", None)
        status = str(getattr(e, "status", "") or "")
        msg = str(getattr(e, "message", "") or "")
        # Gemini reports a bad key as 400 INVALID_ARGUMENT "API key not
        # valid", not 401/403, so check the message too.
        if (code in (401, 403)
                or status in ("UNAUTHENTICATED", "PERMISSION_DENIED")
                or "api key" in msg.lower()):
            raise ProviderError("auth", "The GEMINI_API_KEY in .env was rejected. "
                                        "Check it at aistudio.google.com.")
        if code == 429:
            raise ProviderError("rate_limit", "The free Gemini tier is rate-limited right now.")
        raise ProviderError("other", f"The AI service returned an error ({code}).")
    except errors.ServerError as e:
        raise ProviderError("unavailable", f"The AI service is having trouble ({getattr(e, 'code', '?')}).")
    except errors.APIError as e:
        raise ProviderError("other", f"The AI service returned an error ({getattr(e, 'code', '?')}).")
    except (ConnectionError, TimeoutError, OSError):
        raise ProviderError("unavailable", "Couldn't reach the AI service.")


def _gemini_text(prompt: str, max_tokens: int) -> str:
    client = _gemini()

    def go():
        r = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "max_output_tokens": max_tokens,
                # No tools here; this just silences the SDK's AFC warning.
                "automatic_function_calling": {"disable": True},
            },
        )
        return (r.text or "").strip()

    return _gemini_wrap(go)


def _gemini_json(prompt: str, schema: dict, max_tokens: int):
    client = _gemini()

    def go():
        r = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "max_output_tokens": max_tokens,
                "response_mime_type": "application/json",
                "response_json_schema": schema,
                "automatic_function_calling": {"disable": True},
            },
        )
        text = (r.text or "").strip()
        finish = None
        try:
            finish = str(r.candidates[0].finish_reason)
        except (AttributeError, IndexError, TypeError):
            pass
        if finish and "MAX_TOKENS" in finish:
            return text, "ran out of room before finishing"
        if not text:
            return "", "declined to answer this one"
        return text, None

    return _gemini_wrap(go)


def _complete_text(prompt: str, max_tokens: int) -> str:
    return (_gemini_text if provider() == "gemini" else _anthropic_text)(prompt, max_tokens)


def _complete_json(prompt: str, schema: dict, max_tokens: int):
    return (_gemini_json if provider() == "gemini" else _anthropic_json)(prompt, schema, max_tokens)


# ---------------------------------------------------------------------
# Call 1: claim -> PubMed query
# ---------------------------------------------------------------------

def _clean_query(raw: str, claim: str) -> str:
    """
    Reduce whatever the model returned to a single-line PubMed query.
    Takes the first non-empty line and strips wrapping quotes/backticks.
    Falls back to the claim itself if nothing usable came back.
    """
    for line in raw.splitlines():
        line = line.strip().strip("`")
        if line.startswith(("Query:", "query:")):
            line = line.split(":", 1)[1].strip()
        if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
            line = line[1:-1].strip()
        if line:
            return line
    return claim


def extract_search_terms(claim: str) -> str:
    """Turn a casual claim into a decent PubMed search query."""
    prompt = (
        "A student typed this health claim into a fact-checking "
        f'tool: "{claim}"\n\n'
        "Write the best PubMed search query to find studies relevant to "
        "this claim.\n\n"
        "Structure: exactly 2 bracketed groups joined with one AND. The "
        "first group is the thing being taken or done, the second is the "
        "outcome it is supposed to affect. Inside each group, list the "
        "synonyms and near-synonyms for that one idea, joined with OR. AND "
        "narrows and OR widens, so every term that means roughly the same "
        "thing belongs in the same bracket. Putting two synonyms either "
        "side of an AND asks for papers that use both words, which throws "
        "away most of the good evidence.\n\n"
        "Never add a third group. A claim often carries a condition (\"but "
        "only if you are deficient\", \"in older adults\", \"over 12 "
        "weeks\"). That condition is something to judge from the evidence, "
        "not something to search for: AND-ing it excludes the very trials "
        "that would settle whether it is true. Leave it out of the query.\n\n"
        "Example for \"vitamin D stops you catching colds\":\n"
        "(vitamin D OR cholecalciferol) AND (respiratory tract infection OR "
        "common cold OR influenza OR acute respiratory infection)\n\n"
        "Keep the specific food, product, or substance named in the claim "
        "in the first group, using its common name (e.g. \"celery\", "
        "\"apple cider vinegar\"), since that is how study titles refer to "
        "it, and add its scientific or trade name as an OR if it has a "
        "well known one. For the condition or outcome, give the standard "
        "medical term (the kind used in MeSH headings) and the everyday "
        "word for it as ORs, so papers are found whichever one they use.\n\n"
        "Do not add a study-design filter: the tool runs that as a second "
        "search of its own.\n\n"
        "Respond with ONLY the search query string, nothing else. "
        "No quotes, no explanation."
    )
    return _two_groups(_clean_query(_complete_text(prompt, max_tokens=256), claim))


def _two_groups(query: str) -> str:
    """
    Keep the first two AND-groups and drop the rest.

    The prompt asks for two; this is what happens when it gets three. A
    third group is almost always the claim's condition ("only if you are
    deficient"), and AND-ing it removes the trials that would answer it:
    the landmark vitamin D meta-analyses disappear from the results of a
    query that insists every paper also say "deficiency".
    """
    parts = pubmed.split_and(query)
    return " AND ".join(parts[:2]) if len(parts) > 2 else query


# ---------------------------------------------------------------------
# Call 2: studies -> verdict
# ---------------------------------------------------------------------

def _format_study_for_prompt(i: int, study: dict) -> str:
    pub_types = ", ".join(study.get("publication_types") or []) or "not specified"
    abstract = (study.get("abstract") or "(no abstract available)").strip()
    # Trim very long abstracts so the prompt stays a reasonable size.
    if len(abstract) > 1500:
        abstract = abstract[:1500] + "..."
    # Who was enrolled, read off the record rather than left for the model
    # to notice. A trial in one narrow group is strong evidence about that
    # group and weak evidence about everyone else, and saying so here is
    # what lets the prompt below insist the answer names them.
    pop = evidence.population(study)
    who = f"NARROW POPULATION: {pop} only" if pop else "Population: general"
    return (
        f"[Study {i}] \"{study.get('title')}\"\n"
        f"Journal: {study.get('journal') or 'unknown'} ({study.get('year') or 'unknown'})\n"
        f"Publication type: {pub_types}\n"
        f"{who}\n"
        f"Abstract: {abstract}\n"
    )


# The model is constrained to emit exactly this shape. "verdict" is an
# enum so the UI never sees a value it can't render, and study numbers
# are integers so the PMID mapping below can't blow up on a string.
#
# The deeper layer's fields (breakdown.SCHEMA_PROPERTIES) are merged in
# here rather than asked for separately: the model has already read these
# abstracts in this call, and a second call would double the cost of a
# check to re-read them. See breakdown.py.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VALID_VERDICTS)},
        "tldr": {"type": "string"},
        "explanation": {"type": "string"},
        "still_open": {"type": "string"},
        "cited_study_numbers": {"type": "array", "items": {"type": "integer"}},
        **breakdown.SCHEMA_PROPERTIES,
    },
    "required": ["verdict", "tldr", "explanation", "still_open", "cited_study_numbers"]
                + breakdown.SCHEMA_REQUIRED,
    "additionalProperties": False,
}


def _fallback(explanation: str, tldr: str = "Couldn't reach a verdict from the evidence found.",
              still_open: str = "") -> dict:
    return {"verdict": "complicated", "tldr": tldr, "explanation": explanation,
            "still_open": still_open, "cited_studies": [], "breakdown": None}


# ---------------------------------------------------------------------
# The prose gate. Models pad answers with throat-clearing and dashes; the
# ticket has room for neither. This is deterministic and cheap, so it
# runs on every verdict instead of spending a second model call on it.
# ---------------------------------------------------------------------

TLDR_MAX = 160
EXPLANATION_MAX = 700
STILL_OPEN_MAX = 200

_FILLER = re.compile(
    r"^(?:it(?:'|\u2019)?s|it is)\s+(?:important|worth|crucial|essential)\s+to\s+(?:note|remember|mention|highlight)\s+that\s+"
    r"|^(?:overall|in\s+conclusion|in\s+summary|ultimately|notably|importantly|interestingly|additionally|furthermore|moreover),?\s+",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"\u201c(])")


def _cap(text: str, limit: int) -> str:
    """Cut at the last sentence boundary that fits; else at a word boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
    if ends and ends[-1] > limit // 2:
        return cut[:ends[-1]].strip()
    cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "."


def tidy_prose(text: str, limit: int) -> str:
    """
    Strip the tells (filler openers, dashes as punctuation, doubled
    spaces), make sure the text ends like a sentence, and cap its length
    at a sentence boundary. Returns "" for empty input.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # Dashes used as punctuation become a comma or a full stop.
    t = re.sub(r"\s*[\u2014\u2013]\s*(?=[A-Z])", ". ", t)
    t = re.sub(r"\s*[\u2014\u2013]\s*", ", ", t)
    t = re.sub(r"\s+--\s+", ", ", t)
    # Filler openers, per sentence.
    parts = _SENTENCE_END.split(t)
    cleaned = []
    for part in parts:
        part = part.strip()
        before = part
        part = _FILLER.sub("", part)
        if part != before and part:
            part = part[0].upper() + part[1:]
        if part:
            cleaned.append(part)
    t = " ".join(cleaned)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    if t and t[-1] not in ".!?\"\u201d":
        t += "."
    return _cap(t, limit)


def weigh_evidence(claim: str, studies: list[dict]) -> dict:
    """
    Ask the model to weigh study quality and produce a verdict.

    Returns a dict: {"verdict": "true"|"false"|"complicated",
                      "tldr": str, "explanation": str, "still_open": str,
                      "cited_studies": [pmid, ...],
                      "breakdown": dict | None}

    "breakdown" is the deeper layer, gated by breakdown.py, or None when
    nothing in it survived the gate. It costs no extra call: its fields
    ride on this one.
    """
    if not studies:
        return _fallback(
            "No relevant studies were found on PubMed for this claim. "
            "That doesn't mean the claim is false, it may just not be "
            "well studied yet, or the search terms need refining.",
            tldr="No published studies on this yet. Unproven, not disproven.",
            still_open="Whether anyone has tested this claim directly. PubMed found nothing that does.",
        )

    studies_block = "\n\n".join(
        _format_study_for_prompt(i + 1, s) for i, s in enumerate(studies)
    )
    raw, stop_note = _complete_json(weigh_prompt(claim, studies_block),
                                    VERDICT_SCHEMA, max_tokens=12000)
    return _read_verdict(raw, stop_note, studies, claim)


def weigh_prompt(claim: str, studies_block: str) -> str:
    """
    The one prompt behind every verdict. A function rather than an f-string
    inside weigh_evidence so scripts/token_delta.py can price it without
    spending a call to see it.
    """
    return f"""A student is checking this health claim: "{claim}"

Here are the top matching studies from PubMed:

{studies_block}

Weigh these studies by quality before forming a verdict. A large
randomized controlled trial or meta-analysis should count for much
more than a small preliminary study, an animal study, or a single
case report. Note if the evidence is mixed, weak, or preliminary
rather than settled.

"complicated" is a legitimate, correct verdict. Use it when the
evidence is mixed, weak, preliminary, or when the studies address
something narrower or different from what the claim actually says
(for example, "slightly lowers blood sugar" is not "cures diabetes").
Use it too when the support comes mainly from reviews, animal or
lab studies, or mechanism papers rather than human trials.

Use "false" only when the cited studies actively contradict the
claim. A claim that simply hasn't been studied, or that these
studies don't address, is "complicated", not "false". Absence of
evidence is not evidence of absence.

PubMed's search can return studies that only superficially match the
words in the claim. Only cite a study if it genuinely bears on this
claim. If none of these studies are actually about the claim, say so
plainly, give the verdict "complicated", and cite no studies.

Some studies above are marked NARROW POPULATION. They were run in one
particular group, and they are strong evidence about that group only.
Never answer a general claim from narrow-population studies alone: if
those are all you have, the verdict is "complicated" and the answer
says who the evidence covers. Whenever you lean on one, name its group
in the sentence that cites it, so "it cuts infections (Study 7)" reads
"in people with prediabetes it cut infections (Study 7)".

Write for someone scanning a phone. Answer first, details after.
Plain words, no throat-clearing ("It's important to note", "Overall").
No dashes as punctuation; use commas and full stops. When an abstract
gives a number that matters (how many people, how large the effect,
how long it was measured), use the number instead of an adjective, and
write it in digits: "60 adults", not "sixty adults".

{breakdown.PROMPT}

Respond with JSON in this exact shape:
{{
  "verdict": "true" | "false" | "complicated",
  "tldr": "one plain sentence, under 120 characters, that someone could text back to whoever posted the claim. No study numbers. Say it the way you would say it out loud to a friend: a real sentence with a real verb, not a headline and not a research summary. 'Vitamin D links to colds and flu' is wrong, it is not how anyone speaks; 'Vitamin D probably will not stop you catching a cold, unless you are low on it' is right. Do not start with a noun phrase and the word 'links'. If the verdict is complicated, the sentence must hold both sides, e.g. 'X does Y a little, but nothing shows it does Z', never a flat yes or no",
  "explanation": "2-3 sentences, under 90 words, naming which specific study numbers mattered most and why, with the concrete numbers from their abstracts where they exist",
  "still_open": "one sentence, under 30 words: the biggest gap in these studies (what they don't test, who they leave out, how short they ran), or if the question is settled, what kind of new finding would reopen it. No study numbers",
  "cited_study_numbers": [1, 2],
{breakdown.PROMPT_SHAPE}
}}"""


def _read_verdict(raw, stop_note, studies: list[dict], claim: str = "") -> dict:
    """Everything after the call: parse, map citations, gate every string."""
    if stop_note:
        return _fallback(
            f"The evidence-weighing step {stop_note}. "
            "Raw studies are still available below for manual review."
        )

    # The schema constraint should make this unnecessary, but models have
    # historically wrapped JSON in ```json fences despite instructions.
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if not isinstance(parsed, dict):
        return _fallback(
            "The evidence-weighing step returned an unexpected format. "
            "Raw studies are still available below for manual review."
        )

    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in VALID_VERDICTS:
        verdict = "complicated"

    # Map study numbers back to the PMIDs the model was actually shown.
    # This is how "cite what you used, not what you searched for" is
    # enforced. Coerce defensively: the schema says integers, but the
    # fallback path above means we can't assume it.
    cited_pmids = []
    raw_numbers = parsed.get("cited_study_numbers") or []
    if isinstance(raw_numbers, list):
        for n in raw_numbers:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 0 < n <= len(studies):
                pmid = studies[n - 1]["pmid"]
                if pmid not in cited_pmids:
                    cited_pmids.append(pmid)

    explanation = breakdown.plain(breakdown.soften(
        tidy_prose(str(parsed.get("explanation") or ""), EXPLANATION_MAX)))
    still_open = tidy_prose(str(parsed.get("still_open") or ""), STILL_OPEN_MAX)

    # The deeper layer, gated. Everything it could not tie back to these
    # abstracts has already been dropped by the time this returns.
    deeper = breakdown.ground(parsed, studies, tidy_prose, claim)

    # A true/false verdict with nothing cited is a verdict with no
    # evidence behind it. Absence of studies is never "false" (or
    # "true") here; it's "not well studied". This is the backstop for
    # rule #1 (never claim more certainty than the evidence supports)
    # if the model ignores the prompt.
    forced = False
    if verdict != "complicated" and not cited_pmids:
        verdict = "complicated"
        forced = True
        explanation = (explanation.rstrip() + " None of the studies found directly "
                       "test this claim, so it can't be rated true or false from "
                       "this evidence.").strip()
        # A verdict with nothing behind it cannot have a breakdown of what
        # the evidence shows. The deeper layer goes with the verdict.
        deeper = None

    # The same backstop, one step along: every study behind this verdict was
    # run in one narrow group. That is a real answer about those people and
    # no answer at all about the reader, who did not say they were any of
    # them. Downgrade rather than drop, and say whose evidence it is.
    narrow = evidence.narrow_populations(studies, cited_pmids)
    if not forced and verdict != "complicated" and evidence.narrow_only(studies, cited_pmids):
        verdict = "complicated"
        forced_narrow = ", ".join(narrow)
        explanation = (explanation.rstrip() + f" Every study behind this was run in "
                       f"one group ({forced_narrow}), so it answers the claim for "
                       f"them and not for people in general.").strip()

    # The takeaway is held to the same rule as the breakdown: words that
    # claim more than this evidence can carry are rewritten weaker.
    tldr = breakdown.natural(
        breakdown.plain(breakdown.soften(
            tidy_prose(str(parsed.get("tldr") or ""), TLDR_MAX))))
    if forced:
        tldr = "The studies found don't actually test this claim, so it's unproven either way."
        if not still_open:
            still_open = "Whether any study has tested this claim directly."
    if not tldr:
        # First sentence of the explanation is a serviceable one-liner.
        tldr = _cap(explanation.split(". ")[0].strip(), TLDR_MAX)

    # Who the evidence was actually collected in, read off the records
    # rather than taken from the prose. The breakdown prints it under
    # "Who this applies to", where a reader is asking exactly this.
    if deeper is not None:
        deeper["populations"] = narrow

    return {
        "verdict": verdict,
        "tldr": tldr,
        "explanation": explanation,
        "still_open": still_open,
        "cited_studies": cited_pmids,
        "breakdown": deeper,
    }
