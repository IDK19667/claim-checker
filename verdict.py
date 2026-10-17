"""
Two small AI calls that sit around the PubMed lookup:

  1. extract_search_terms(claim) -> a good PubMed search string for the claim,
     and the claim itself with its spelling fixed
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

import difflib
import hashlib
import json
import logging
import os
import re

import breakdown
import evidence
import pubmed

# "insufficient" is "Not enough evidence": nothing found tests the claim.
# It is not a softer "complicated", which is for real evidence that is
# mixed, partial, narrow or uncertain. See DECISIONS.md.
VALID_VERDICTS = ("true", "false", "complicated", "insufficient")

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


def _anthropic_text(prompt: str, max_tokens: int, temperature: float | None = None) -> str:
    client = _anthropic()
    extra = {} if temperature is None else {"temperature": temperature}

    def go():
        m = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            # Trivial task: no reasoning needed, and thinking tokens would
            # otherwise count against max_tokens.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
        return _anthropic_text_of(m)

    return _anthropic_wrap(go)


def _anthropic_json(prompt: str, schema: dict, max_tokens: int, temperature: float | None = None):
    # `temperature` is accepted and ignored: thinking is on for this call,
    # and the API refuses any temperature but the default while it is.
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


def _gemini_text(prompt: str, max_tokens: int, temperature: float | None = None) -> str:
    client = _gemini()
    config = {
        "max_output_tokens": max_tokens,
        # No tools here; this just silences the SDK's AFC warning.
        "automatic_function_calling": {"disable": True},
    }
    if temperature is not None:
        config["temperature"] = temperature
        if temperature == 0:
            # A fixed seed too: temperature 0 alone still varies run to run.
            config["seed"] = 0

    def go():
        r = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
        )
        return (r.text or "").strip()

    return _gemini_wrap(go)


def _gemini_json(prompt: str, schema: dict, max_tokens: int, temperature: float | None = None):
    client = _gemini()
    config = {
        "max_output_tokens": max_tokens,
        "response_mime_type": "application/json",
        "response_json_schema": schema,
        "automatic_function_calling": {"disable": True},
    }
    if temperature is not None:
        config["temperature"] = temperature
        if temperature == 0:
            # A fixed seed too: temperature 0 alone still varies run to run.
            config["seed"] = 0

    def go():
        r = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
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


def _complete_text(prompt: str, max_tokens: int, temperature: float | None = None) -> str:
    return (_gemini_text if provider() == "gemini" else _anthropic_text)(
        prompt, max_tokens, temperature)


# ---------------------------------------------------------------------
# The faithfulness check: one more call, after the answer is final
#
# The gates check that every figure is in an abstract. They cannot check
# that a figure belongs to the group the sentence names (the yoga arm, not
# the walking arm), that a line agrees with its study's own conclusion,
# or that a study's headline finding made it in at all. That takes a
# reading, so the short answer and each "What the research says" line are
# read once more against the abstracts they cite. A line that fails is
# rewritten, through the same gates as everything else, or dropped. The
# call fails open: when it cannot run, the answer stands as it was.
# ---------------------------------------------------------------------

_faith_log = logging.getLogger("evident.faithful")

FAITHFUL_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["ok", "rewrite", "drop"]},
                    "problem": {"type": "string",
                                "enum": ["none", "direction", "group", "unsupported"]},
                    "rewrite": {"type": "string"},
                },
                "required": ["line", "verdict", "problem", "rewrite"],
            },
        },
        "missing": {"type": "string"},
    },
    "required": ["lines", "missing"],
}


def _faithful_prompt(claim: str, tldr: str, lines: list[str], numbers: list[int],
                     studies: list[dict]) -> str:
    shown = "\n\n".join(
        f"[Study {n}] \"{studies[n - 1].get('title')}\"\n"
        f"Abstract: {(studies[n - 1].get('abstract') or '(no abstract)').strip()[:2500]}"
        for n in numbers)
    listed = "\n".join([f'L0 (the short answer): "{tldr}"' if tldr else "L0: (none)"]
                        + [f'L{i}: "{t}"' for i, t in enumerate(lines, 1)])
    return f"""You are checking a summary of health research against the abstracts it
cites, before a reader sees it.

Claim: "{claim}"

The studies the summary relies on:

{shown}

The lines to check:

{listed}

For every line, L0 included, say:
"ok" when everything it states matches the abstract it cites;
"rewrite" when it fails any of these, with the corrected line in "rewrite":
  direction: it points the other way from, or says more than, that study's own
  conclusion;
  group: a number it gives belongs to a different group from the one it names
  (one arm of the trial and not another, one subgroup and not everyone);
  unsupported: it states something the abstract does not say;
"drop" when the abstract cannot support any version of it.
Give "problem" as the failure, or "none" for ok. Leave "rewrite" empty unless
the verdict is rewrite.

L0 is the short answer. It cites no study: check it against all of them. A
rewrite of it keeps its yes, no or mixed answer and fixes only what is wrong.
Any other rewrite keeps its own citation, "(Study N)", and cites nothing else.

Then "missing": if one study's own headline finding, what its conclusion says
it found about this claim, is in none of the lines, write one plain sentence
stating it, ending with its citation "(Study N)". Otherwise "".

Write the way the lines are written: plain words a 12-year-old can follow,
numbers as digits, no dashes. Never add a number the abstract does not print.

Return JSON: {{"lines": [{{"line": 0, "verdict": "ok", "problem": "none",
"rewrite": ""}}], "missing": ""}}."""


# The takeaways code writes for itself are not the model's reading of a
# study, so there is nothing in an abstract to hold them to.
def _code_takeaway(tldr: str) -> bool:
    fixed = {UNTESTED_TLDR, LOW_CERTAINTY_TLDR, CONFOUNDED_TLDR, _UNSAFE_UNPROVEN,
             *RANKED_TLDR.values()}
    return (tldr in fixed or tldr.startswith((
        "The strongest studies here", "No randomized trial or major review here",
        "The studies found were all in", "Nothing has tested this in people yet",
        "No published studies on this yet")))


def _gate_line(text: str, studies: list[dict], allowed: set, skip=()) -> str:
    """One rewritten line through the gates every line has passed: the prose
    rules, a citation of a study used, no figure the abstracts lack."""
    count = len(studies)
    t = breakdown.plain(breakdown.soften(tidy_prose(text or "", EXPLANATION_MAX)))
    t = breakdown.keep_sentences(t, count, breakdown.corpus(studies))
    t = breakdown.drop_citing(t, skip, count)
    kept = [x for x in breakdown.sentences(t)
            if breakdown.refs(x, count) and set(breakdown.refs(x, count)) <= allowed]
    return breakdown.for_reading(" ".join(kept[:2]), studies) if kept else ""


def _swap(text: str, old: str, new: str) -> str:
    """Replace one sentence of prose where it appears word for word."""
    if not text or not old or old not in text:
        return text
    return re.sub(r"\s{2,}", " ", text.replace(old, new)).strip()


def check_faithful(result: dict, studies: list[dict], claim: str) -> dict:
    """
    The short answer and the "What the research says" lines, read against
    the abstracts they cite, and fixed where they fail. Runs only when there
    are lines to check; costs one call; changes nothing if the call fails.
    """
    bd = result.get("breakdown") or {}
    lines = list(bd.get("says") or [])
    cited = [i + 1 for i, s in enumerate(studies) if s["pmid"] in set(result.get("cited_studies") or ())]
    if not lines or not cited:
        return result
    count = len(studies)
    numbers = sorted({n for t in lines for n in breakdown.refs(t, count)} | set(cited))
    tldr = result.get("tldr") or ""
    check_tldr = tldr if tldr and not _code_takeaway(tldr) else ""
    try:
        raw, stop = _complete_json(_faithful_prompt(claim, check_tldr, lines, numbers, studies),
                                   FAITHFUL_SCHEMA, max_tokens=4000, temperature=0)
        if stop:
            return result
        read = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except (ProviderError, json.JSONDecodeError, AttributeError, TypeError) as e:
        _faith_log.warning("the faithfulness check did not run, so the answer stands: %s", e)
        return result
    if not isinstance(read, dict):
        return result
    allowed = set(cited)
    skip = {i + 1 for i, s in enumerate(studies) if s.get("background")}
    explanation = result.get("explanation") or ""
    evidence_paras = list(bd.get("evidence") or [])
    # A line can come from any field of the breakdown, the effect row and
    # the part-by-part answers included, so a fix is made wherever it sits.
    fields = {k: bd.get(k) or "" for k in ("effect_size", "strength", "applies_to",
                                           "not_applies_to", "unknowns")}
    parts = [dict(p) for p in bd.get("parts") or []]
    kept_lines = list(lines)
    for item in read.get("lines") or []:
        if not isinstance(item, dict) or item.get("verdict") not in ("rewrite", "drop"):
            continue
        try:
            k = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        if k == 0:
            if not check_tldr or item.get("verdict") != "rewrite":
                continue
            fixed = breakdown.for_reading(breakdown.natural(breakdown.plain(breakdown.soften(
                tidy_prose(breakdown._REF.sub("", str(item.get("rewrite") or "")).replace(" ()", ""),
                           TLDR_MAX)))), studies).strip()
            # Nor may it take the size of the effect back out of it, or turn
            # a plain yes or no into a tie.
            if (fixed and not breakdown.stamp_conflict(result.get("verdict") or "", fixed, "", claim)
                    and not breakdown.lost_hedge(tldr, fixed, claim)
                    and breakdown.kept_answer(tldr, fixed, claim)):
                _faith_log.warning("the short answer did not match its abstracts (%s), so it "
                                   "was rewritten: %r -> %r", item.get("problem"), tldr, fixed)
                tldr = fixed
            continue
        if not 0 < k <= len(lines):
            continue
        old = lines[k - 1]
        new = (_gate_line(str(item.get("rewrite") or ""), studies, allowed, skip)
               if item.get("verdict") == "rewrite" else "")
        # A line that failed is never swapped for a surer one: a rewrite
        # that only loses its hedge goes, and the line with it.
        if new and breakdown.lost_hedge(old, new, claim):
            new = ""
        _faith_log.warning("a research line did not match its abstract (%s), so it was %s: %r",
                           item.get("problem"), "rewritten" if new else "dropped", old)
        kept_lines = [new if t == old else t for t in kept_lines]
        explanation = _swap(explanation, old, new)
        evidence_paras = [_swap(p, old, new) for p in evidence_paras]
        fields = {k: _swap(v, old, new) for k, v in fields.items()}
        for p in parts:
            p["assessment"] = _swap(p.get("assessment") or "", old, new)
    kept_lines = [t for t in kept_lines if t]
    missing = _gate_line(str(read.get("missing") or ""), studies, allowed, skip)
    if missing and not any(missing.lower()[:40] in t.lower() for t in kept_lines):
        kept_lines = kept_lines[:breakdown.SAYS_MAX - 1] + [missing]
    # With every line gone the page falls back to the explanation, so that
    # has lost the same sentences. If nothing of it is left either, the
    # short answer stands in rather than a line that failed the check.
    # A part whose whole answer failed points at the paragraphs below, as the
    # gate does when it drops one, or goes when there are none; an effect
    # row left empty is left out of the page.
    evidence_paras = [p for p in evidence_paras if p]
    for p in parts:
        if not p["assessment"] and evidence_paras:
            p["assessment"] = breakdown.PART_BELOW
    new_bd = breakdown.settle_effect(
        {**bd, **fields, "parts": [p for p in parts if p["assessment"]],
         "says": kept_lines, "evidence": evidence_paras})
    return {**result, "tldr": tldr, "explanation": explanation or tldr, "breakdown": new_bd}


def _complete_json(prompt: str, schema: dict, max_tokens: int, temperature: float | None = None):
    return (_gemini_json if provider() == "gemini" else _anthropic_json)(
        prompt, schema, max_tokens, temperature)


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
        if _LABELLED.match(line):
            continue
        if line.startswith(("Query:", "query:")):
            line = line.split(":", 1)[1].strip()
        if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
            line = line[1:-1].strip()
        if line:
            return line
    return claim


def extract_search_terms(claim: str) -> tuple[str, str, str]:
    """
    Turn a casual claim into a decent PubMed search query.

    Returns (query, surrogate, understood). `understood` is the claim with
    its spelling fixed, or the claim exactly as typed when nothing needed
    fixing or the fix went further than spelling (see spelling_fix).
    The surrogate is the OR-group of measurable
    stand-ins for an outcome nobody measures directly, empty when trials
    measure the outcome itself. It never enters the query: it runs as a
    search of its own in pubmed.search_and_fetch, so a claim about hair
    loss can still reach the trials that measured the hormone.
    """
    # Temperature 0: the same claim is meant to get the same search, so the
    # model is asked for its single most likely wording, not a fresh one.
    raw = _complete_text(_query_prompt(claim), max_tokens=256, temperature=0)
    return _two_groups(_clean_query(raw, claim)), _surrogate(raw), spelling_fix(claim, _claim_line(raw))


def _query_prompt(claim: str) -> str:
    return (
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
        "For a drink, name the drinking, not the chemical in it: \"red wine "
        "is good for the heart\" starts (red wine OR wine OR alcohol drinking "
        "OR alcohol consumption OR moderate drinking). Never use \"ethanol\" "
        "or a compound such as \"polyphenols\" or \"resveratrol\" for a "
        "drink: they find ethanol injected during heart surgery and cocoa "
        "supplements, not people drinking it.\n\n"
        "When the claim is about lowering the risk of something or "
        "preventing it, the outcome is getting it, not living with it, so "
        "the outcome group asks for new cases: for \"exercise lowers the "
        "risk of depression\" it is (incident depression OR depression "
        "prevention OR risk of depression OR depressive symptoms onset). "
        "Otherwise the search returns treatment trials in people who already "
        "have it, which cannot answer the claim.\n\n"
        "Do not add a study-design filter: the tool runs that as a second "
        "search of its own.\n\n"
        "Then, and only if nobody measures the claim's outcome directly, add "
        "a second line beginning \"Surrogate: \" with an OR group of the "
        "measurable stand-ins for it: the things a trial would measure "
        "instead. For \"creatine makes you lose your hair\" that line is "
        "\"Surrogate: dihydrotestosterone OR DHT OR testosterone\", because "
        "the trials measured the hormone and counted nobody's hair. Leave the "
        "line out when trials measure the outcome itself, and never move the "
        "surrogate into the query: it runs as a search of its own.\n\n"
        "People type fast and misspell. Search with the words they meant: "
        "\"vacines cause autisim\" is a claim about vaccines and autism. "
        "Then, only if the claim has spelling mistakes or typos, add a last "
        "line beginning \"Claim: \" with the claim as they meant to type "
        "it: the spelling fixed and nothing else changed. Keep their words, "
        "their order and their meaning, including any \"not\". Leave the "
        "line out when the spelling is already right.\n\n"
        "Respond with the search query on the first line and nothing else "
        "except those optional lines. No quotes, no explanation."
    )


# Which wording of the prompt above wrote a saved search. A saved search is
# only reused under the prompt that wrote it, so changing the prompt starts
# every claim afresh rather than mixing old searches with new ones.
QUERY_PROMPT_VERSION = hashlib.sha256(_query_prompt("\0").encode()).hexdigest()[:12]


# Lines after the query that carry something else, never the query itself.
_LABELLED = re.compile(r"(?i)^(surrogate|indirect|proxy|claim)\s*:")


def _claim_line(raw: str) -> str:
    """The optional "Claim:" line, or ""."""
    for line in str(raw or "").splitlines():
        line = line.strip().strip("`")
        if re.match(r"(?i)^claim\s*:", line):
            return line.split(":", 1)[1].strip().strip("\"'\u201c\u201d ")
    return ""


# How alike the typed and corrected claims must be, letter for letter, for
# the change to count as a spelling fix rather than a rewrite.
SPELLING_SIMILARITY = 0.72
# "dont", "doesnt": the commonest typo of all, and it carries the "not".
_APOSTROPHE = re.compile(r"\b(do|does|did|is|are|was|were|ca|wo|could|should|would|has|have)nt\b",
                         re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|none|nothing|cannot|without|\w+n't)\b",
                       re.IGNORECASE)


def _letters(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", (text or "").lower())).strip()


def spelling_fix(typed: str, fixed: str) -> str:
    """
    The corrected claim, or the claim as typed.

    The model is asked to fix spelling and nothing else, and this checks it
    did: the two must stay close letter for letter, keep about the same
    number of words, and say "not" the same number of times. A model that
    tidied "vacines dont cause autisim" into "vaccines cause autism" has
    changed the question, and the reader gets their own words back instead.
    """
    typed = (typed or "").strip()
    fixed = re.sub(r"\s+", " ", (fixed or "").strip())
    if not fixed or len(fixed) > len(typed) * 1.5 + 10:
        return typed
    a, b = _letters(typed), _letters(fixed)
    if not b or a == b:
        return typed
    if abs(len(a.split()) - len(b.split())) > 2:
        return typed
    fixed = _APOSTROPHE.sub(lambda m: m.group(1) + "n't", fixed)
    if len(_NEGATION.findall(_APOSTROPHE.sub(lambda m: m.group(1) + "n't", typed))) \
            != len(_NEGATION.findall(fixed)):
        return typed
    if difflib.SequenceMatcher(None, a, b).ratio() < SPELLING_SIMILARITY:
        return typed
    typed_words = a.split()
    if not all(_misspelled_from(w, typed_words) for w in b.split()):
        return typed
    return fixed[:1].upper() + fixed[1:]


# Texting shorthand a corrected claim may spell out in full.
_SHORTHAND = {"u": "you", "ur": "your", "r": "are", "n": "and", "2": "to", "4": "for",
              "b4": "before", "bc": "because", "cuz": "because", "coz": "because", "w": "with",
              "thru": "through", "gud": "good", "gr8": "great", "luv": "love", "y": "why",
              "da": "the", "dat": "that"}


def _misspelled_from(word: str, typed_words: list[str]) -> bool:
    """
    True when this word of the corrected claim is one the reader typed, a
    misspelling of one, shorthand for one, or half of two run together
    ("redwine"). "causes" is none of those for "prevents", so a fix that
    swaps one for the other is a rewrite, not a spelling fix.
    """
    w = word.replace("'", "")
    for t in typed_words:
        t = t.replace("'", "")
        if w == t or _SHORTHAND.get(t) == w:
            return True
        if len(w) >= 3 and len(t) > len(w) and w in t:
            return True
        if difflib.SequenceMatcher(None, w, t).ratio() >= 0.6:
            return True
    return False


# A surrogate long enough to be prose is not an OR group.
SURROGATE_MAX = 120


def _surrogate(raw: str) -> str:
    """
    The optional "Surrogate:" line, or "".

    The label is required rather than inferred from position. An unlabelled
    second line is as likely to be the model explaining itself as naming a
    stand-in, and a stray sentence pushed into a PubMed query would quietly
    cost a check one of its eight slots.
    """
    for line in str(raw or "").splitlines():
        line = line.strip().strip("`")
        if not re.match(r"(?i)^(surrogate|indirect|proxy)\s*:", line):
            continue
        group = line.split(":", 1)[1].strip().strip("()\"' ")
        if (not group or len(group) > SURROGATE_MAX or " AND " in group.upper()
                or "[" in group
                or re.fullmatch(r"(?i)\s*(none|n/?a|-+)\s*", group)):
            return ""
        return group
    return ""


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

def _format_study_for_prompt(i: int, study: dict, outcome: str = "",
                             surrogate: str = "", claim: str = "") -> str:
    pub_types = ", ".join(study.get("publication_types") or []) or "not specified"
    abstract = (study.get("abstract") or "(no abstract available)").strip()
    # Trim very long abstracts so the prompt stays a reasonable size.
    if len(abstract) > 1500:
        abstract = abstract[:1500] + "..."
    # Who was enrolled, read off the record rather than left for the model
    # to notice. A trial in one narrow group is strong evidence about that
    # group and weak evidence about everyone else, and saying so here is
    # what lets the prompt below insist the answer names them.
    pop = evidence.population(study, claim)
    if pop and pop in evidence.claim_groups(claim):
        who = f"Population: {pop}, the group this claim is about"
    else:
        who = f"NARROW POPULATION: {pop} only" if pop else "Population: general"
    # Whether this record measured the claim's outcome or a stand-in for it,
    # read off the title and abstract rather than left to the model. A trial
    # that measured a hormone has not measured hair loss, and the answer is
    # only allowed to use it if it says which one it measured.
    stand_in = evidence.indirect(study, outcome, surrogate)
    lines = [
        f"[Study {i}] \"{study.get('title')}\"",
        f"Journal: {study.get('journal') or 'unknown'} ({study.get('year') or 'unknown'})",
        f"Publication type: {pub_types}",
        who,
    ]
    if stand_in:
        lines.append(f"INDIRECT: measures {stand_in}, not the outcome in the claim")
    # How much weight this one record can bear, read off the record. The
    # model was calling a trial of two ways to use a desk "randomised
    # evidence" that desks help; a trial with nobody who went without
    # cannot say that, and the line here is what lets the prompt insist.
    caveats = evidence.trial_caveats(study)
    if "no untreated comparison group" in caveats:
        lines.append("NO UNTREATED COMPARISON GROUP: everyone got some version of "
                     "the treatment, so this cannot show it beats doing nothing")
        caveats.remove("no untreated comparison group")
    if caveats:
        lines.append("LIMITED: " + "; ".join(caveats))
    why_off = evidence.off_topic(study, claim)
    if why_off:
        lines.append(f"OFF TOPIC: {why_off}. Do not cite it")
    elif evidence.background_reasons([study], claim):
        why_back = evidence.background_reasons([study], claim)[study["pmid"]]
        lines.append(f"BACKGROUND ONLY: {_BACKGROUND_SAID[why_back]}. It is not a test of "
                     f"this claim in people and never counts as evidence for or against it")
    rated = evidence.review_certainty(study, outcome, strict=True)
    if rated in ("low", "very low"):
        where = " on this outcome" if evidence.certainty_on_outcome(study, outcome) else ""
        lines.append(f"LOW CERTAINTY: this review rates its own evidence{where} "
                     f"as {rated} certainty")
    lines.append(f"Abstract: {abstract}\n")
    return "\n".join(lines)


# The BACKGROUND ONLY marker's words, by evidence.background_reasons.
_BACKGROUND_SAID = {
    evidence.BG_LAB: "an animal or lab study",
    evidence.BG_OPINION: "an opinion piece or commentary, which measured nothing",
    evidence.BG_GUIDE: "a product or buying guide, which measured nothing",
}


def _subgroup_block(studies: list[dict], claim: str) -> str:
    """
    The sentences in these abstracts that answer the claim's own condition.

    Pulled out of the abstracts in code and handed to the model as its own
    block, because the failure this fixes is a reading failure: the model
    had these sentences in front of it and still wrote that the condition
    was never tested.
    """
    hits = evidence.subgroup_findings(studies, claim)
    if not hits:
        return ""
    lines = [f"- Study {h['study']}, on {h['condition']}: \"{h['sentence']}\""
             for h in hits]
    return ("SUBGROUP FINDINGS the abstracts above report on the claim's own "
            "condition:\n" + "\n".join(lines))


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
        # One judgement per study, on the two things a study has to match to
        # be evidence for this claim. Read by _relevance below.
        "relevance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "study": {"type": "integer"},
                    "subject": {"type": "boolean"},
                    "outcome": {"type": "boolean"},
                    # Whether it reports or pools anything measured in
                    # people. An essay, a buying guide or a cow trial does not.
                    "in_people": {"type": "boolean"},
                    # Which way its own result points on this claim. With
                    # the design, read in code, this is how a trial that
                    # says yes outranks a survey that says no.
                    "finding": {"type": "string",
                                "enum": ["for", "against", "mixed", "unclear"]},
                },
                "required": ["study", "subject", "outcome", "in_people", "finding"],
                "additionalProperties": False,
            },
        },
        **breakdown.SCHEMA_PROPERTIES,
    },
    "required": ["verdict", "tldr", "explanation", "still_open", "cited_study_numbers",
                 "relevance"] + breakdown.SCHEMA_REQUIRED,
    "additionalProperties": False,
}


def _fallback(explanation: str, tldr: str = "Couldn't reach a verdict from the evidence found.",
              still_open: str = "", verdict: str = "complicated") -> dict:
    return {"verdict": verdict, "tldr": tldr, "explanation": explanation,
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

# Words this product never says (the copy rules in CLAUDE.md). Flagged, not
# rewritten: swapping one word for another can change what a sentence
# claims, and a verdict's wording is not ours to guess at. The flag is a log
# line naming the words, never the text, so the prompt can be fixed against
# it. "elevated" and "elevation" are left out on purpose: elevated blood
# pressure is a finding, not a sales word.
BANNED_WORDS = ("leverage", "seamless", "empower", "unlock", "robust",
                "actionable", "data-driven", "solutions", "testament",
                "landscape", "delve", "elevate")
_BANNED = re.compile(
    r"\b(?:leverag(?:e|es|ed|ing)|seamless(?:ly)?|empower\w*|unlock\w*|robust\w*"
    r"|actionable|data-driven|solutions|testaments?|landscapes?"
    r"|delv(?:e|es|ed|ing)|elevate|elevates|elevating)\b",
    re.IGNORECASE,
)
_copy_log = logging.getLogger("evident.copy")


def banned_words(text: str) -> list[str]:
    """The banned words in `text`, lower-cased, each once, in order of first use."""
    seen: list[str] = []
    for m in _BANNED.finditer(text or ""):
        w = m.group(0).lower()
        if w not in seen:
            seen.append(w)
    return seen


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
    # A dash between numbers is a range: "5 to 10 mg", never "5, 10 mg".
    t = re.sub(r"(?<=\d)\s*[\u2014\u2013]\s*(?=\d)", " to ", t)
    # Dashes used as punctuation become a comma or a full stop.
    t = re.sub(r"\s*[\u2014\u2013]\s*(?=[A-Z])", ". ", t)
    t = re.sub(r"\s*[\u2014\u2013]\s*", ", ", t)
    t = re.sub(r"\s+--\s+", ", ", t)
    # A hyphen with a space either side, between words, is a dash too. Only
    # between letters, so "5 - 10 mg" keeps its meaning.
    t = re.sub(r"(?<=[A-Za-z])\s+-\s+(?=[A-Za-z])", ", ", t)
    # Numbers in digits, here and not later, so a number spelled out in
    # words meets the gate like any other (breakdown.digits).
    t = breakdown.digits(t)
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
    hits = banned_words(t)
    if hits:
        _copy_log.warning("model prose used banned words: %s", ", ".join(hits))
    return _cap(t, limit)


def weigh_evidence(claim: str, studies: list[dict], query: str = "",
                   surrogate: str = "") -> dict:
    """
    Ask the model to weigh study quality and produce a verdict.

    Returns a dict: {"verdict": "true"|"false"|"complicated",
                      "tldr": str, "explanation": str, "still_open": str,
                      "cited_studies": [pmid, ...],
                      "breakdown": dict | None}

    "breakdown" is the deeper layer, gated by breakdown.py, or None when
    nothing in it survived the gate. It costs no extra call: its fields
    ride on this one.

    `query` and `surrogate` come from the search step. They are what lets
    this step tell a study that measured the claim's outcome from one that
    measured a stand-in. Normally one call; a second only when the answer
    contradicts itself or wanders off the claim, which the consistency
    check below catches before a reader sees it.
    """
    if not studies:
        return _fallback(
            "No relevant studies were found on PubMed for this claim. "
            "That doesn't mean the claim is false, it may just not be "
            "well studied yet, or the search terms need refining.",
            tldr="No published studies on this yet. Unproven, not disproven.",
            still_open="Whether anyone has tested this claim directly. PubMed found nothing that does.",
            verdict="insufficient",
        )

    groups = pubmed.split_and(query or "")
    subject = groups[0] if groups else ""
    outcome = groups[1] if len(groups) > 1 else ""
    studies_block = "\n\n".join(
        _format_study_for_prompt(i + 1, s, outcome, surrogate, claim)
        for i, s in enumerate(studies)
    )
    prompt = weigh_prompt(claim, studies_block,
                          _subgroup_block(studies, claim)) + _confounded_note(studies)
    reported = evidence.conditions_reported(studies, claim)

    # Temperature 0, like the search: the same claim over the same studies
    # should read the same way every time it is checked.
    raw, stop_note = _complete_json(prompt, VERDICT_SCHEMA, max_tokens=12000, temperature=0)
    result = _read_verdict(raw, stop_note, studies, claim, subject=subject, outcome=outcome)
    kept = raw, stop_note
    read = {}
    said = _stamp_disagrees(result, claim, read)

    # One retry, and only when the answer disagrees with itself. Dropping a
    # contradictory sentence is not an option here: the two halves are both
    # claims about the evidence, and code cannot tell which one is the true
    # one. Asking again is the cheapest honest move, and if the second
    # answer is no better the first one stands.
    faults = _faults(result, claim, reported, studies, said)
    if faults:
        raw, stop_note = _complete_json(prompt + _redo_note(faults),
                                        VERDICT_SCHEMA, max_tokens=12000, temperature=0)
        again = _read_verdict(raw, stop_note, studies, claim, subject=subject, outcome=outcome)
        # A retry that failed outright has no breakdown and is not an answer.
        # One that says nothing here tests the claim may have none either,
        # and is one.
        answered = again.get("breakdown") or (again.get("verdict") == "insufficient"
                                              and again.get("tldr"))
        if answered:
            said_again = _stamp_disagrees(again, claim, read)
            if len(_faults(again, claim, reported, studies, said_again)) < len(faults):
                result, said = again, said_again
                kept = raw, stop_note
    # The stamp and the takeaway are one answer. Asked once to agree and
    # still apart, the stamp follows the takeaway: a reader acts on the
    # sentence, and the sentence is the half the model wrote out in words.
    # The stamp is set before the gates in _read_verdict, so every downgrade
    # there still applies to it.
    if said:
        _stamp_log.warning("stamp %r still disagreed with the takeaway (%s) after the retry, "
                           "so it follows the takeaway: %r", result.get("verdict"), said,
                           result.get("tldr"))
        result = _read_verdict(*kept, studies, claim, follow=breakdown.STAMP_FOR[said],
                               subject=subject, outcome=outcome)
    # Which studies were off topic is a fact about this answer, so it is
    # written onto the records the answer is shown and cached with: the
    # chart leaves them out of its strong count, and says why.
    off = result.pop("off_topic", None) or {}
    back = result.pop("background", None) or {}
    result.pop("ranked", None)
    result.pop("guarded", None)
    for s in studies:
        s.pop("off_topic", None)
        s.pop("background", None)
        if s["pmid"] in off:
            s["off_topic"] = off[s["pmid"]]
        elif s["pmid"] in back:
            s["background"] = back[s["pmid"]]
    result = _settle_confounding(_settle_ruling(result, claim), studies)
    return check_faithful(result, studies, claim)


def _confounded_numbers(result: dict, studies: list[dict]) -> list[int]:
    """The study numbers of the cited reviews that say their link is likely
    explained by lifestyle or other factors, when the takeaway does not."""
    if breakdown.says_confounded(result.get("tldr") or ""):
        return []
    cited = set(result.get("cited_studies") or ())
    return [i + 1 for i, s in enumerate(studies)
            if s["pmid"] in cited and evidence.confounded(s)]


def _settle_confounding(result: dict, studies: list[dict]) -> dict:
    """
    The last word on a takeaway that still presents a link its own main
    review puts down to lifestyle and other factors. It is replaced, not
    added to: "may lower heart risks, but..." still leads with the benefit.
    """
    if not _confounded_numbers(result, studies):
        return result
    _stamp_log.warning("the takeaway left out that the main review puts the link down to "
                       "other factors, so it was replaced: %r", result.get("tldr"))
    return {**result, "tldr": CONFOUNDED_TLDR}


# The one small call behind the rule above. Code screens every takeaway for
# a plain answer the stamp does not match, cheaply and with false alarms
# ("eight glasses is just a rough guide" reads to it as a plain yes); a
# stamp is only ever changed on what this call reads. It runs only when the
# screen fires, about one check in five, so the free tier holds.
DIRECTION_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["yes", "no", "untested", "mixed"]}},
    "required": ["answer"],
}


def _direction_prompt(claim: str, tldr: str) -> str:
    return f"""A health claim and the one-sentence answer written about it. Say which
way the answer answers the claim exactly as written.

Claim: "{claim}"
Answer: "{tldr}"

yes: the answer says the claim is true.
no: the answer says the claim is false as written: it does not happen, it is
not needed or not the rule the claim states, it does not do what the claim
promises, it is no better than what the claim compares it with, or it is a
myth.
untested: the answer says nothing has tested or shown the claim, so whether it
is true is unknown.
mixed: the answer says the evidence points both ways, or that the claim holds
only partly, a little, or for some people.

When the claim states a fixed amount, rule or need ("you need", "everyone
should", "every day"), an answer that the amount varies from person to person,
is only a rough guide or rule of thumb, or is not required, is no: the claim
states a requirement and the answer says there is not one.

Return JSON: {{"answer": "yes" | "no" | "untested" | "mixed"}}."""


def classify_direction(claim: str, tldr: str) -> str | None:
    """Which way the takeaway answers the claim, read by the model, or None
    when the call fails. A failed read changes nothing."""
    try:
        raw, stop = _complete_json(_direction_prompt(claim, tldr), DIRECTION_SCHEMA,
                                   max_tokens=2000, temperature=0)
    except ProviderError:
        return None
    if stop:
        return None
    try:
        said = json.loads(raw.replace("```json", "").replace("```", "").strip()).get("answer")
    except (json.JSONDecodeError, AttributeError):
        return None
    return said if said in breakdown.STANCES else None


def _stamp_disagrees(result: dict, claim: str, read: dict) -> str | None:
    """
    The plain answer ("yes", "no" or "untested") the takeaway gives when the
    stamp does not match it, or None when they agree. `read` keeps each
    takeaway's reading, so a retry that writes the same sentence costs no
    second call.
    """
    verdict, tldr = result.get("verdict") or "", result.get("tldr") or ""
    if not breakdown.words_stamp(verdict, tldr, claim):
        return None
    if tldr not in read:
        read[tldr] = classify_direction(claim, tldr)
        if read[tldr] is None:
            _stamp_log.warning("could not read which way the takeaway answers the claim, so "
                               "the stamp %r stands: %r", verdict, tldr)
    said = read[tldr]
    if not said or said in breakdown._AGREES.get(verdict, set()):
        return None
    return said


def _faults(result: dict, claim: str, reported: list[str],
            studies: list[dict] = (), said: str | None = None) -> list[str]:
    """Everything this answer says twice or says about something else."""
    bd = result.get("breakdown") or {}
    texts = [result.get("tldr"), result.get("explanation"), result.get("still_open")]
    texts += list(bd.get("evidence") or [])
    texts += [p.get("assessment") for p in (bd.get("parts") or [])]
    texts += [bd.get(k) for k in ("effect_size", "strength", "applies_to",
                                  "not_applies_to", "unknowns")]
    found = breakdown.contradictions(*[t for t in texts if t], reported=reported)
    stray = breakdown.off_claim(result.get("tldr") or "", claim)
    if stray:
        found.append("the takeaway answers for " + ", ".join(stray)
                     + ", which the claim never mentions")
    if said:
        found.append(breakdown.stamp_note(result.get("verdict") or "", result.get("tldr") or "",
                                          said, claim))
    else:
        clash = breakdown.stamp_conflict(result.get("verdict") or "", "",
                                         result.get("explanation") or "", claim)
        if clash:
            found.append(clash)
    ranked = result.get("ranked")
    if ranked:
        found.append(f"Randomized trials ({_refs(ranked['trials'])}) found "
                     f"{'it works' if ranked['verdict'] == 'true' else 'it does not work'}, and "
                     f"only weaker {ranked['kinds']} ({_refs(ranked['weaker'])}) point the other "
                     f"way. Trials outrank them, so the verdict must be "
                     f"\"{ranked['verdict']}\", not a tie, and the answer must say the weaker "
                     f"studies disagree and count for less")
    guarded = result.get("guarded")
    if guarded:
        doing = ("calls it safe" if guarded["subject"] == "that it is safe"
                 else f"steers people {guarded['subject']}")
        found.append(f"This answer {doing} without a randomized trial or a major review "
                     f"that says so. Follow the strongest evidence"
                     + (f" ({_refs(guarded['follows'])})" if guarded["follows"] else "")
                     + " and state the risk plainly")
    for n in _confounded_numbers(result, list(studies)):
        found.append(f"Study {n}, a review the answer relies on, says the link it found is "
                     f"likely explained by lifestyle or other factors rather than by the "
                     f"thing itself, and the takeaway does not say so. The takeaway must say "
                     f"that plainly, and must not present the benefit as real")
        break
    return found


# The takeaways code writes itself, when a gate below changes the stamp.
LOW_CERTAINTY_TLDR = ("The studies found point towards yes, but the certainty of "
                      "that evidence is low.")
UNTESTED_TLDR = "The studies found don't actually test this claim, so it's unproven either way."
CONFOUNDED_TLDR = ("The main review says lifestyle and other factors likely explain much of "
                   "the link these studies found.")

_stamp_log = logging.getLogger("evident.stamp")


def _settle_ruling(result: dict, claim: str) -> dict:
    """
    The last word on an explanation that rules on the claim outright ("the
    claim is false") against a stamp its takeaway agrees with. By now the
    takeaway and the stamp are one answer, so the ruling sentence is the odd
    one out, and it goes rather than the answer changing under it.
    """
    verdict = result.get("verdict") or ""
    exp = result.get("explanation") or ""
    if not breakdown.stamp_conflict(verdict, "", exp, claim):
        return result
    kept = [t for t in breakdown.sentences(exp)
            if not breakdown.stamp_conflict(verdict, "", t, claim)]
    _stamp_log.warning("dropped an explanation sentence that ruled against the stamp %r",
                       verdict)
    return {**result, "explanation": " ".join(kept).strip()}


def _redo_note(faults: list[str]) -> str:
    """The one thing added to the prompt on the retry: what went wrong."""
    return ("\n\nA first attempt at this answer was rejected, because "
            + "; and ".join(faults)
            + ". Write the whole answer again with that fixed, keeping every "
              "rule above. Decide which of the two readings the abstracts "
              "actually support and say only that one.")


def _denial_note(claim: str) -> str:
    """
    A claim typed as a "no" ("smoking does not cause lung cancer") gets one
    more paragraph, written about it alone. Given to every claim, its
    example primed plain claims the other way round ("vaccines cause
    autism" came back "true" over "vaccines do not cause autism"), so every
    other claim is sent the prompt exactly as it was.
    """
    if not breakdown.claim_denies(claim):
        return ""
    return """

This claim itself says no: it says something does not happen. Answer it
exactly as it is written. If the studies show it does not happen, the
claim is right: the takeaway agrees with it and the verdict is "true".
If they show it does happen, the verdict is "false"."""


def _narrower_example(claim: str) -> str:
    """
    The example under "complicated". For most claims it is the one it has
    always been. For a claim that promises the whole thing (a cure), that
    example says the opposite of the rule in `_absolute_note`, so the claim
    gets a version that points there instead.
    """
    if breakdown.absolute_claim(claim):
        return (" in who or what it covers (a smaller effect than a claim\n"
                "promises is a different case, set out below)")
    return (' (for example, "slightly lowers blood sugar" is not "cures\n'
            'diabetes")')


def _absolute_note(claim: str) -> str:
    """
    A claim that promises the whole thing ("apple cider vinegar cures
    diabetes") is tested as written: studies showing a real but partial
    effect answer it with a no. This is the one place the prompt asks for
    a plainer verdict than "complicated", and only for these claims; every
    other claim is sent the prompt exactly as before, the same way the
    note for a claim that says "not" is kept to those claims.
    """
    word = breakdown.absolute_claim(claim)
    if not word:
        return ""
    return f"""

This claim promises the whole thing: it says "{word}". Test it exactly
as written. If studies in people tested it and found a real but partial
effect (a little lower, somewhat milder, fewer cases but not none), they
contradict the claim as written, because they looked for the effect and
found less than it promises: the verdict is "false". The takeaway then
says what it does do, and then that it does not do what the claim says,
for example "Zinc may shorten a cold by a day or so, but it does not
cure it." If studies show the whole effect the claim names (a cure, a
remission, complete prevention) in some people and not in others, the
verdict is "complicated". If nothing here tests it in people, it is
"insufficient", as always."""


def _confounded_note(studies: list[dict]) -> str:
    """
    A review in this set that says its own link is likely explained by
    lifestyle or other factors gets one more paragraph, naming it. Every
    other set of studies is sent the prompt exactly as before.
    """
    numbers = [str(i + 1) for i, s in enumerate(studies) if evidence.confounded(s)]
    if not numbers:
        return ""
    which = f"Study {numbers[0]}" if len(numbers) == 1 else "Studies " + ", ".join(numbers)
    return f"""

{which} says the link it found is likely explained by lifestyle or other
factors (confounding) rather than by the thing itself. If you rely on it,
the takeaway must say that plainly, and must not present the benefit as
real: not "it may lower the risk", but that the people who do it differ
in other ways that likely explain the link."""


def weigh_prompt(claim: str, studies_block: str, subgroup_block: str = "") -> str:
    """
    The one prompt behind every verdict. A function rather than an f-string
    inside weigh_evidence so scripts/token_delta.py can price it without
    spending a call to see it.
    """
    return f"""A student is checking this health claim: "{claim}"

Here are the top matching studies from PubMed:

{studies_block}
{subgroup_block}

Weigh these studies by quality before forming a verdict. A large
randomized controlled trial or meta-analysis should count for much
more than a small preliminary study, an animal study, or a single
case report. Note if the evidence is mixed, weak, or preliminary
rather than settled.

There are four verdicts, and two of them are honest ways of not
saying yes or no.

"complicated" is a legitimate, correct verdict. Use it when human
studies do test the claim and what they show is mixed, weak,
preliminary, low certainty, true only for one group, or narrower than
the claim{_narrower_example(claim)}.

"insufficient" means not enough evidence: nothing here actually tests
the claim. Use it when the only papers are case reports about single
patients, animal or lab studies, mechanism papers, indirect evidence
alone, or studies about something else. A claim that simply hasn't been
studied is "insufficient", not "false" and not "complicated". A case
report of someone harmed is a reason for caution, never evidence that
the thing does not work. Its takeaway says plainly that it hasn't been
tested: "No study has tested whether detox teas remove toxins; the only
reports are of people made ill by them."

For a claim that one thing causes or changes another, a controlled
trial that gave people the thing and measured the claim's outcome
outranks an observational link, because people who differ in one habit
differ in many others. When controlled trials of the claim find no
effect and only observational studies find a link, the trials answer
it: the verdict is "false", and the answer says the link was seen only
in observational data.

Use "false" only when the cited studies actively contradict the
claim. Absence of evidence is not evidence of absence. A comparison is contradicted
when the studies tested it and found no difference: "X is better
than Y" is false when trials of X against Y found they work about
the same.

The verdict and the takeaway are one answer said twice, and they must
agree. If your takeaway would tell a friend a plain "no" ("sugar does
not make children hyperactive", "it works no better than ordinary
dieting"), the verdict is "false". If it says a plain "yes", the verdict
is "true". "complicated" is for an answer that really is "partly" or "it
depends", and its takeaway holds both sides. Never stamp "complicated"
over a takeaway that says plainly yes or plainly no.{_denial_note(claim)}{_absolute_note(claim)}

PubMed's search can return studies that only superficially match the
words in the claim. A study is evidence for this claim only when it
matches both halves of it: it tests the claim's own subject (not a
neighbour, a different drug given to the same people, or the same
substance used another way) and it measures the claim's own outcome
(or, when marked INDIRECT, its stand-in). A drug-dose trial in smokers
with lung cancer is not evidence that smoking causes lung cancer; a
trial of exercise as a treatment for people who already have diabetes is
not evidence that exercise prevents diabetes; a review of fasting and
sports performance is not evidence about fasting and weight loss. The
other way round is a match: a review showing a vaccine prevents measles
at all does measure a claim that it reduces severe measles, because an
illness that never starts cannot become severe. Judge every study on both in "relevance", and cite only
the ones that match both. Studies marked OFF TOPIC are never cited. If
none of these studies are actually about the claim, say so plainly,
give the verdict "insufficient", and cite no studies.

Judge one more thing about every study in "relevance": "in_people" is
true only when the paper reports or pools something measured in people
(a trial, an observational study, or a review of those). It is false for
animal studies, farm animals included, and lab studies; for opinion
pieces, commentaries, editorials and letters; for product or buying
guides; and for essays that argue a view without reporting data. A study
marked BACKGROUND ONLY is one of those already. Such a paper is
background: it can be mentioned as such, and it never counts as evidence
for or against the claim.

And "finding": which way the study's own result points on this claim.
"for" when it found what the claim says happens; "against" when it found
no effect or the opposite; "mixed" when it found both; "unclear" when it
reports no result on this claim. Judge the study's result, not your
verdict.

Not every study is an equal test. A randomized trial, or a review that
pools randomized trials, is the fair test of whether one thing causes
another. Observational studies, surveys, reviews of those, and animal or
lab studies are weaker. When the randomized evidence points one way and
the weaker studies point the other, the verdict follows the trials:
"true" or "false", never "complicated" for that reason. Say in the
explanation that the weaker studies disagree, and why they count for
less. That is not a tie.

Some studies above are marked NARROW POPULATION. They were run in one
particular group, and they are strong evidence about that group only.
Never answer a general claim from narrow-population studies alone: if
those are all you have, the verdict is "complicated" and the answer
says who the evidence covers. Whenever you lean on one, name its group
in the sentence that cites it, so "it cuts infections (Study 7)" reads
"in people with prediabetes it cut infections (Study 7)". A claim that
names a group, or is about something that happens to one group (measles
vaccines and children, menopause and women), is not a general claim:
studies marked as the group this claim is about answer it as asked.

Answer the claim that was typed. Its subject and its outcome are both
fixed by the words in it, and the answer has to be about those two
things. A search for one of them drags in neighbours: a claim about
screen light returns trials of blue-light filtering glasses, a claim
about a supplement returns trials of a lotion. A product that filters
or blocks the thing in the claim is evidence about that product, not
about the thing. If that is all the evidence there is, the verdict is
"insufficient" and the answer says the claim's own question was not
tested. Never answer the neighbouring question as though it were this
one, and leave out details that belong only to it.

"true" needs evidence that can carry it. Studies marked LIMITED (small,
pilot or feasibility, funded by the maker) or LOW CERTAINTY can make an
answer lean, never settle it: if they are all you have, the verdict is
"complicated" and the answer says the certainty is low. A study marked NO
UNTREATED COMPARISON GROUP is not randomised evidence that the treatment
works, whatever its design is called; say what it compared ("two ways of
using the desk"), never "a randomised trial found desks help".

Some studies above are marked INDIRECT. They measured a stand-in rather
than the outcome in the claim: a hormone instead of hair, a blood marker
instead of an illness. Say so in the sentence that cites one, call it
indirect evidence, and say what it does and does not show, as in "it
raised the hormone linked to hair loss (Study 4), which is a reason to
look and not a finding that anyone lost hair". Indirect evidence alone
never supports "true" or "false"; if it is all there is, the verdict is
"insufficient".

If a SUBGROUP FINDINGS block appears above, those sentences are the
answer to the condition the claim carries ("only if you are low",
"in children", "if you have diabetes"). Report what they found,
including when two of them disagree, which is itself the finding. Never
write that the condition was not tested when a sentence there reports on
it. If no such block appears, the abstracts are silent on the condition
and saying so is correct.

Write for someone scanning a phone. Answer first, details after.
Plain words, no throat-clearing ("It's important to note", "Overall").
No dashes as punctuation; use commas and full stops. When an abstract
gives a number that matters (how many people, how large the effect,
how long it was measured), use the number instead of an adjective, and
write it in digits: "60 adults", not "sixty adults".

{breakdown.PROMPT}

Respond with JSON in this exact shape:
{{
  "verdict": "true" | "false" | "complicated" | "insufficient",
  "tldr": "one plain sentence, under 120 characters, that someone could text back to whoever posted the claim. No study numbers. Say it the way you would say it out loud to a friend: a real sentence with a real verb, not a headline and not a research summary. 'Vitamin D links to colds and flu' is wrong, it is not how anyone speaks; 'Vitamin D probably will not stop you catching a cold, unless you are low on it' is right. Do not start with a noun phrase and the word 'links'. If the verdict is complicated, the sentence must hold both sides, e.g. 'X does Y a little, but nothing shows it does Z', never a flat yes or no. If it is insufficient, the sentence says nothing has tested it, and never says it does not work. Its subject is the claim's own subject: start with the thing the claim is about, not with the product the trials happened to test. 'Nothing here tests whether screen light harms eyes, only whether filtering glasses help' is right; 'Blue light glasses do not help your eyes' answers a question nobody asked",
  "explanation": "2-3 sentences, under 90 words, naming which specific study numbers mattered most and why, with the concrete numbers from their abstracts where they exist",
  "still_open": "one sentence, under 30 words: the biggest gap in these studies (what they don't test, who they leave out, how short they ran), or if the question is settled, what kind of new finding would reopen it. No study numbers",
  "cited_study_numbers": [1, 2],
  "relevance": [{{"study": 1, "subject": true, "outcome": true, "in_people": true, "finding": "for"}}, {{"study": 2, "subject": true, "outcome": false, "in_people": true, "finding": "unclear"}}],
{breakdown.PROMPT_SHAPE}
}}"""


def _read_verdict(raw, stop_note, studies: list[dict], claim: str = "",
                  follow: str | None = None, subject: str = "", outcome: str = "") -> dict:
    """
    Everything after the call: parse, map citations, gate every string.

    `follow` is the stamp the takeaway commits to, when weigh_evidence found
    the two still apart. It replaces the model's stamp before any gate runs,
    so the gates judge it like any other. `subject` is the query's first
    OR-group, for the relevance gate; `outcome` its second, for the
    certainty a review gives that outcome.
    """
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

    # The relevance gate. A study the code knows cannot bear on this claim,
    # or that the model itself judged to miss its subject or its outcome, is
    # not used, whatever the model put in its cited list, and no sentence
    # that leans on it alone reaches the reader.
    off = _relevance(parsed, studies, claim, subject)
    off_numbers = {i + 1 for i, s in enumerate(studies) if s["pmid"] in off}
    cited_pmids = [p for p in cited_pmids if p not in off]

    # Animal and lab studies are background for a claim about people. They
    # can say why a thing might work, never whether it does in people, so
    # they are never counted as used, whatever the model put in its list.
    # So are opinion pieces, commentaries and buying guides, for any claim:
    # nothing in them was measured. Code finds the ones it can on sight; the
    # model's in_people reading finds the rest. What the model leaned on is
    # kept a moment longer, because an answer resting on nothing else is
    # "not tested in people", which says more than "nothing cited".
    reasons = {**_not_tested(parsed, studies, claim),
               **evidence.background_reasons(studies, claim)}
    reasons = {p: why for p, why in reasons.items() if p not in off}
    background = [s["pmid"] for s in studies if s["pmid"] in reasons]
    leaned_on = cited_pmids
    cited_pmids = [p for p in cited_pmids if p not in background]
    background_numbers = {i + 1 for i, s in enumerate(studies) if s["pmid"] in background}

    explanation = breakdown.drop_citing(breakdown.tell_straight(breakdown.plain(breakdown.soften(
        tidy_prose(str(parsed.get("explanation") or ""), EXPLANATION_MAX))), studies),
        off_numbers, len(studies))
    still_open = tidy_prose(str(parsed.get("still_open") or ""), STILL_OPEN_MAX)

    # The deeper layer, gated. Everything it could not tie back to these
    # abstracts has already been dropped by the time this returns.
    deeper = breakdown.ground(parsed, studies, tidy_prose, claim, skip=off_numbers)

    # The takeaway is held to the same rule as the breakdown: words that
    # claim more than this evidence can carry are rewritten weaker.
    tldr = breakdown.natural(
        breakdown.plain(breakdown.soften(
            tidy_prose(str(parsed.get("tldr") or ""), TLDR_MAX))))
    # And to the size of it. "Reduces colds and flu" is the wrong sentence
    # for an odds ratio of 0.88, whatever else is right about it. The label
    # is measured from the prose the breakdown is actually printing, so the
    # two layers cannot disagree about how big the effect was.
    sized = (deeper or {}).get("effect") or {}
    # "No clear effect" is never the answer when a randomized trial used
    # here found the effect and calls it significant: the size is left
    # unsaid rather than said wrong.
    if sized.get("null") and _significant_trial(parsed, studies, cited_pmids):
        deeper["effect"] = sized = {}
    if sized.get("label"):
        tldr = _cap(breakdown.match_effect(tldr, sized["label"]), TLDR_MAX)
    if follow:
        verdict = follow

    # Randomized trials outrank weaker studies. When the trials point one
    # way and observational studies, surveys or animal work the other, the
    # verdict follows the trials and the answer says the rest disagree: a
    # trial that says yes against a survey that says no is not a tie.
    ranked = _trials_outweigh(parsed, studies, off, reasons)
    forced_rank = None
    # Set when a takeaway below is code's own, written for the stamp it set:
    # a later downgrade replaces it rather than leaving it to argue.
    templated = False
    if ranked and verdict != "insufficient":
        if verdict != ranked["verdict"]:
            forced_rank = ranked
            verdict = ranked["verdict"]
            for n in ranked["trials"]:
                pmid = studies[n - 1]["pmid"]
                if pmid not in cited_pmids:
                    cited_pmids.append(pmid)
                if pmid not in leaned_on:
                    leaned_on.append(pmid)
            tldr = RANKED_TLDR[verdict]
            templated = True
        if forced_rank or not _says_weaker(explanation, ranked):
            explanation = (explanation.rstrip() + " " + _ranked_sentence(ranked)).strip()

    # The harm guard. An answer that would steer someone away from
    # sunscreen, vaccination, pasteurization or a prescribed medicine, or
    # call something safe, has to rest on a randomized trial or a major
    # review that says so. Otherwise it follows the strongest evidence and
    # states the risk plainly.
    guarded = _harm_guard(claim, verdict, parsed, studies, off, reasons, cited_pmids)
    if guarded:
        for n in guarded["follows"]:
            pmid = studies[n - 1]["pmid"]
            if pmid not in cited_pmids:
                cited_pmids.append(pmid)
            if pmid not in leaned_on:
                leaned_on.append(pmid)
        if guarded["verdict"] != verdict:
            verdict = guarded["verdict"]
            tldr = (_UNSAFE_UNPROVEN if guarded["risk"] == _UNSAFE_UNPROVEN
                    and verdict == "complicated"
                    else _cap(_GUARD_TLDR[verdict].format(risk=guarded["risk"]), TLDR_MAX))
            templated = True
        else:
            guarded = {**guarded, "kept": True}
        explanation = (explanation.rstrip() + " " + _guard_sentence(guarded)).strip()

    # A true/false verdict with nothing cited is a verdict with no
    # evidence behind it. Absence of studies is never "false" (or
    # "true") here; it's "not well studied". This is the backstop for
    # rule #1 (never claim more certainty than the evidence supports)
    # if the model ignores the prompt.
    forced = False
    if verdict != "insufficient" and not leaned_on:
        verdict = "insufficient"
        forced = True
        explanation = (explanation.rstrip() + " None of the studies found directly "
                       "test this claim, so there isn't enough evidence to rate it.").strip()
        # A verdict with nothing behind it cannot have a breakdown of what
        # the evidence shows. The deeper layer goes with the verdict.
        deeper = None

    # The same backstop, one step along: every study behind this verdict was
    # run in one narrow group. That is a real answer about those people and
    # no answer at all about the reader, who did not say they were any of
    # them. Downgrade rather than drop, and say whose evidence it is.
    narrow = evidence.narrow_populations(studies, cited_pmids)
    forced_narrow = ""
    # Next: everything cited is a single case, an animal or a dish. That is
    # not a test of the claim in people, whatever the model stamped on it,
    # and a report of one person harmed never makes "does not work".
    thin = ""
    if not forced and verdict != "insufficient":
        thin = evidence.untested_in_people(studies, leaned_on, reasons)
        if thin:
            was = verdict
            verdict = "insufficient"
            explanation = (explanation.rstrip() + f" Nothing here tests this claim in "
                           f"people, only {thin}, so there isn't enough evidence to "
                           f"rate it.").strip()
            if was == "false" and "single patients" in thin:
                explanation += (" A report of someone harmed is a reason for caution, "
                                "not evidence that it does not work.")
    elif verdict == "insufficient" and leaned_on:
        thin = evidence.untested_in_people(studies, leaned_on, reasons)
    # The backstop again, now that background studies are out: a verdict
    # still standing on nothing used becomes "not enough evidence".
    if verdict != "insufficient" and not cited_pmids:
        verdict = "insufficient"
        forced = True
        explanation = (explanation.rstrip() + " None of the studies found directly "
                       "test this claim, so there isn't enough evidence to rate it.").strip()
        deeper = None

    if (not forced and verdict in ("true", "false")
            and evidence.narrow_only(studies, cited_pmids, claim)):
        verdict = "complicated"
        forced_narrow = ", ".join(narrow)
        explanation = (explanation.rstrip() + f" Every study behind this was run in "
                       f"one group ({forced_narrow}), so it answers the claim for "
                       f"them and not for people in general.").strip()

    # And one more: the evidence behind a "true" has to be able to carry it.
    # A review that grades its own certainty low, or a stack of small and
    # pilot trials, makes an answer lean; it does not make it likely true.
    forced_low = ""
    if not forced and verdict == "true":
        forced_low = evidence.certainty_cap(
            studies, cited_pmids, bool(breakdown._COMPARATIVE_CLAIM.search(claim)),
            outcome) or ""
        if forced_low:
            verdict = "complicated"
            explanation = (explanation.rstrip() + f" The certainty of this evidence is "
                           f"low, because {forced_low}, so it can't be rated likely "
                           f"true.").strip()

    # A claim that promises the whole thing ("cures diabetes") is tested as
    # written. When the model's own takeaway says the whole thing does not
    # happen ("it lowers blood sugar a bit, but it does not cure it") and it
    # stamped "complicated" anyway, the stamp follows the words. The one
    # place code raises a verdict, and only this far: from "complicated" to
    # "false", only on human evidence that was used, never past a downgrade
    # made above, and never for a group the evidence does not cover.
    absolute = breakdown.absolute_claim(claim)
    if (absolute and verdict == "complicated" and cited_pmids
            and not (forced or thin or forced_narrow or forced_low)
            and breakdown.denies_absolute(tldr, claim)
            and not evidence.narrow_only(studies, cited_pmids, claim)):
        verdict = "false"
        explanation = (explanation.rstrip() + f' The claim says "{absolute}", and these '
                       f"studies found a smaller effect than that, so as written it is "
                       f"likely false.").strip()
    # A downgrade made here, in code, leaves the model's takeaway answering
    # the question the stamp no longer answers. The takeaway follows the
    # stamp, so the two never disagree on the reader's screen.
    if forced_narrow and (templated or breakdown.stamp_conflict(verdict, tldr, "", claim)):
        tldr = _cap(f"The studies found were all in {forced_narrow}, so they answer "
                    f"this for them and not for people in general.", TLDR_MAX)
    if forced_low:
        leaned = f"{tldr.rstrip().rstrip('.')}, but the certainty of that evidence is low."
        tldr = (leaned if len(leaned) <= TLDR_MAX and tldr and not templated
                and not breakdown.stamp_conflict(verdict, leaned, "", claim)
                else LOW_CERTAINTY_TLDR)
    if thin and (templated or breakdown.stamp_conflict(verdict, tldr, "", claim)):
        tldr = _cap(f"Nothing has tested this in people yet. The only papers are {thin}.",
                    TLDR_MAX)
    if forced:
        tldr = UNTESTED_TLDR
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
        # "What the research says" on the result screen: three to five
        # sentences picked from prose that has already passed the gate, so
        # the block costs no extra call and cannot outrun the evidence.
        deeper["says"] = breakdown.says(explanation, deeper, studies,
                                        skip=background_numbers)
        # "Rests on N studies" counts what the verdict stands on, so an
        # animal or lab study a part mentions is not one of them.
        deeper["rests_on"] = len([p for p in breakdown.cited_pmids(deeper, studies)
                                  if p not in background])

    # Last, and only now that every gate above has checked each figure
    # against the abstract that printed it: figures are rounded for reading
    # and a measured change gets its unit back (breakdown.for_reading).
    # A figure no abstract gave a unit for is left out, and a field that was
    # nothing but that figure says so rather than going blank.
    readable = lambda text: breakdown.for_reading(text, studies)
    left_out = breakdown.UNITLESS_LEFT_OUT
    tldr, explanation, still_open = readable(tldr), readable(explanation), readable(still_open)
    if not explanation:
        explanation = left_out
    if not tldr:
        tldr = _cap(explanation.split(". ")[0].strip(), TLDR_MAX)
    if deeper is not None:
        for key in ("effect_size", "strength", "applies_to", "not_applies_to", "unknowns"):
            if deeper.get(key):
                deeper[key] = readable(deeper[key]) or (left_out if key == "effect_size" else "")
        deeper["evidence"] = [x for x in (readable(t) for t in deeper.get("evidence") or []) if x]
        deeper["says"] = [x for x in (readable(t) for t in deeper.get("says") or []) if x]
        for part in deeper.get("parts") or []:
            part["assessment"] = readable(part.get("assessment")) or left_out
        deeper = breakdown.settle_effect(deeper)

    return {
        "verdict": verdict,
        "tldr": tldr,
        "explanation": explanation,
        "still_open": still_open,
        "cited_studies": cited_pmids,
        "breakdown": deeper,
        # Set when code had to turn the stamp to follow the trials, so the
        # retry can ask for an answer that does it in its own words.
        "ranked": forced_rank,
        "guarded": guarded if guarded and not guarded.get("kept") else None,
        "off_topic": off,
        "background": reasons,
    }


OFF_SUBJECT = "tests something other than what this claim is about"
OFF_OUTCOME = "measures a different outcome from the one this claim names"


def _relevance(parsed: dict, studies: list[dict], claim: str, subject: str = "") -> dict:
    """
    {pmid: why} for every study that is not evidence for this claim: the
    ones code can rule out on sight, then the ones the model judged to miss
    the claim's subject or its outcome. A missing or malformed judgement
    rules nothing out; the gate only ever removes on a clear "no".

    `subject` is the query's first OR-group. A record that never names it,
    in any of its words, is not a test of the thing the claim is about,
    whatever the model judged: the model read a trial of one seed extract
    as a test of seed cycling.
    """
    off = {}
    for s in studies:
        why = evidence.off_topic(s, claim)
        if why:
            off[s["pmid"]] = why
        elif subject and not evidence.names_subject(s, subject):
            off[s["pmid"]] = OFF_SUBJECT
    for item in parsed.get("relevance") or []:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("study"))
        except (TypeError, ValueError):
            continue
        if not 0 < n <= len(studies) or studies[n - 1]["pmid"] in off:
            continue
        if item.get("subject") is False:
            off[studies[n - 1]["pmid"]] = OFF_SUBJECT
        elif item.get("outcome") is False:
            off[studies[n - 1]["pmid"]] = OFF_OUTCOME
    return off


# The takeaway code writes when it turns the stamp to follow the trials.
RANKED_TLDR = {
    "true": "Randomized trials say yes. Some weaker studies disagree, but trials are the fairer test.",
    "false": "Randomized trials say no. Some weaker studies disagree, but trials are the fairer test.",
}
_WEAKER_SAID = re.compile(
    r"\b(?:observational|cohort|case[- ]control|cross[- ]sectional|survey|animal|"
    r"weaker|association|associated|linked)\w*", re.IGNORECASE)


def _refs(numbers: list[int]) -> str:
    """Study numbers as the prose writes them: "Study 3", "Studies 1 and 4"."""
    ns = [str(n) for n in numbers]
    if len(ns) == 1:
        return f"Study {ns[0]}"
    return f"Studies {', '.join(ns[:-1])} and {ns[-1]}"


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _findings(parsed: dict, studies: list[dict]) -> dict:
    """{study number: "for" | "against" | "mixed" | "unclear"} from the
    model's relevance reading, for the studies it gave one."""
    found = {}
    for item in parsed.get("relevance") or []:
        if not isinstance(item, dict) or item.get("finding") not in (
                "for", "against", "mixed", "unclear"):
            continue
        try:
            n = int(item.get("study"))
        except (TypeError, ValueError):
            continue
        if 0 < n <= len(studies):
            found[n] = item["finding"]
    return found


def _trials_outweigh(parsed: dict, studies: list[dict], off: dict, reasons: dict) -> dict | None:
    """
    When the randomized evidence used points one way and weaker studies
    the other: {"verdict", "trials", "weaker", "kinds"}, else None. The
    direction of each study is the model's "finding"; its design is read
    off the record (evidence.design_rank). The trials have to agree with
    each other, and at least one has to be able to bear it: a review of
    trials, or a randomized trial that is not small, a pilot, uncontrolled
    or paid for by the maker. One small pilot does not outrank a field.
    """
    found = {n: f for n, f in _findings(parsed, studies).items() if f in ("for", "against")}
    trials, weaker = [], []
    for n, finding in sorted(found.items()):
        s = studies[n - 1]
        if s["pmid"] in off:
            continue
        why = reasons.get(s["pmid"])
        rank = evidence.design_rank(s)
        if rank == "trial" and not why:
            trials.append((n, finding))
        elif rank == "weaker" and why in (None, evidence.BG_LAB):
            weaker.append((n, finding))
    directions = {f for _, f in trials}
    if len(directions) != 1:
        return None
    direction = directions.pop()
    against = [n for n, f in weaker if f != direction]
    if not against or not any(evidence.is_review(studies[n - 1])
                              or not evidence.trial_caveats(studies[n - 1])
                              for n, _ in trials):
        return None
    kinds = list(dict.fromkeys(evidence.weaker_kind(studies[n - 1]) for n in against))
    return {"verdict": "true" if direction == "for" else "false",
            "trials": [n for n, _ in trials], "weaker": against, "kinds": _and(kinds)}


def _says_weaker(explanation: str, ranked: dict) -> bool:
    """Whether the explanation already says the weaker studies disagree."""
    return bool(_WEAKER_SAID.search(explanation or ""))


def _ranked_sentence(ranked: dict) -> str:
    found = "it does" if ranked["verdict"] == "true" else "it does not"
    return (f"Randomized trials ({_refs(ranked['trials'])}) found {found}, and they are the "
            f"fairer test. The {ranked['kinds']} that point the other way "
            f"({_refs(ranked['weaker'])}) count for less, so the verdict follows the trials.")


# What the answer says when the harm guard has to act. The risk sentence
# is evidence.protective_practice's, for the practice the claim is about.
_GUARD_TLDR = {
    "true": "The strongest studies here support this claim. {risk}",
    "false": "The strongest studies here go against this claim. {risk}",
    "complicated": "No randomized trial or major review here settles this. {risk}",
}
_UNSAFE_UNPROVEN = "No randomized trial or major review here shows it is safe."


def _strong(study: dict) -> bool:
    """A randomized trial, a review of them, or any systematic review or
    meta-analysis: the evidence a risky answer has to rest on."""
    return evidence.design_rank(study) == "trial" or evidence.is_review(study)


def _harm_guard(claim: str, verdict: str, parsed: dict, studies: list[dict], off: dict,
                reasons: dict, cited_pmids: list[str]) -> dict | None:
    """
    When this verdict would steer a reader away from a protective practice
    (sunscreen, vaccination, pasteurization, a prescribed medicine), or
    call something safe, and no randomized trial or major review used says
    so: {"verdict", "follows", "risk", "subject"} for what the answer must
    say instead, else None. It follows the strongest evidence that points
    the protective way when there is some, and otherwise stops short of the
    risky answer. Nothing happens on a reading with no findings at all.
    """
    practice = evidence.protective_practice(claim)
    if practice:
        risky = ("true", "complicated") if practice["against"] else ("false",)
        safe_way = "false" if practice["against"] else "true"
        risk = practice["risk"]
        subject = f"away from {practice['practice']}"
    elif evidence.claims_safe(claim):
        risky, safe_way, risk, subject = ("true",), "false", _UNSAFE_UNPROVEN, "that it is safe"
    else:
        return None
    findings = _findings(parsed, studies)
    if verdict not in risky or not findings:
        return None
    usable = [n for n in findings
              if studies[n - 1]["pmid"] not in off and studies[n - 1]["pmid"] not in reasons
              and _strong(studies[n - 1])]
    backing = {"true": ("for",), "false": ("against",), "complicated": ("for", "mixed")}[verdict]
    if any(findings[n] in backing and studies[n - 1]["pmid"] in cited_pmids for n in usable):
        return None
    toward = "for" if safe_way == "true" else "against"
    follows = sorted(n for n in usable if findings[n] == toward)
    return {"verdict": safe_way if follows else "complicated", "follows": follows,
            "risk": risk, "subject": subject}


def _guard_sentence(guard: dict) -> str:
    if guard["subject"] == "that it is safe":
        lead = _UNSAFE_UNPROVEN
    else:
        lead = (f"An answer that steers people {guard['subject']} needs a randomized trial "
                f"or a major review behind it, and none here says so.")
    if guard["follows"]:
        lead += f" The strongest evidence here ({_refs(guard['follows'])}) points the other way."
    return lead if guard["risk"] == _UNSAFE_UNPROVEN else f"{lead} {guard['risk']}"


def _significant_trial(parsed: dict, studies: list[dict], cited_pmids: list[str]) -> bool:
    """A randomized trial used for this verdict, or a review of them, that
    found the claim's effect and reports it as statistically significant."""
    return any(f == "for" and studies[n - 1]["pmid"] in cited_pmids
               and evidence.design_rank(studies[n - 1]) == "trial"
               and evidence.significant(studies[n - 1])
               for n, f in _findings(parsed, studies).items())


def _not_tested(parsed: dict, studies: list[dict], claim: str) -> dict:
    """
    {pmid: why} for the studies the model judged report nothing measured in
    people: an essay, a buying guide, a review of animal work. Background,
    like the ones code finds on sight. Never a trial PubMed tags as one and
    whose record names the people in it, which is a test in people whatever
    the reading says; and nothing at all for a claim about animals.
    """
    if not evidence.about_people(claim):
        return {}
    out = {}
    for item in parsed.get("relevance") or []:
        if not isinstance(item, dict) or item.get("in_people") is not False:
            continue
        try:
            n = int(item.get("study"))
        except (TypeError, ValueError):
            continue
        if 0 < n <= len(studies) and not evidence.human_trial(studies[n - 1]):
            out[studies[n - 1]["pmid"]] = evidence.BG_NOT_TESTED
    return out
