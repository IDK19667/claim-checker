"""
Server-rendered share/preview card (1200x630 PNG) for a checked claim, so a
shared link unfurls in iMessage/WhatsApp/Slack with the verdict itself.

Same page as the app: newsprint, one ink, a double-rule masthead, the
claim as the headline and the verdict reversed out of a filled band.
No colour.
"""

import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FONTS = os.path.join(os.path.dirname(__file__), "static", "fonts")
FONT_PATH = os.path.join(FONTS, "LibreFranklin.ttf")

W, H = 1200, 630
PAPER = "#fafbfc"
INK = "#0e1422"
DEEP = "#0b1226"
DEEP_2 = "#18213e"
ON_DEEP = "#fafbfc"
ON_DEEP_2 = "#a9b3cb"
INK_2 = "#3a4256"
INK_3 = "#666e85"
RULE = "#d3d8e2"
LABELS = {"true": "LIKELY TRUE", "false": "LIKELY FALSE", "complicated": "IT'S COMPLICATED",
          "insufficient": "NOT ENOUGH EVIDENCE"}


def _f(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """One family, as on the page. Weight is the only axis that carries meaning."""
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


# The page sets caps tight, so the card does too. Kept as a function because
# the drawing code measures with it.
def _serif(size: int, weight: int = 400, italic: bool = False) -> ImageFont.FreeTypeFont:
    return _f(size, weight)


def _sans(size: int, weight: int = 600, width: int = 100) -> ImageFont.FreeTypeFont:
    return _f(size, weight)


def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill: str, tracking: float):
    """Archivo has no small caps, so letterspacing is drawn by hand."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _tracked_width(draw: ImageDraw.ImageDraw, text: str, font, tracking: float) -> float:
    return sum(draw.textlength(c, font=font) + tracking for c in text)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    words = text.split()
    lines, line = [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines and line:
        lines.append(line)
    if lines and len(lines) == max_lines:
        last = lines[-1]
        if " ".join(lines) != " ".join(words):
            while draw.textlength(last + "…", font=font) > max_width and " " in last:
                last = last[: last.rfind(" ")]
            lines[-1] = last + "…"
    return lines


def strongest_cited(studies: list[dict], cited: list[str]) -> tuple[str, str, str] | None:
    """(label, year, title) of the best study the verdict actually relied on."""
    STRONG = ("Meta-Analysis", "Systematic Review", "Randomized Controlled Trial", "Practice Guideline")
    WEAK = ("Case Reports", "Editorial", "Comment", "Letter", "News", "Retracted Publication")

    def rank(s):
        types = s.get("publication_types") or []
        if any(t.startswith(x) for x in STRONG for t in types):
            return 3
        if any(t.startswith(x) for x in WEAK for t in types):
            return 1
        return 2

    pool = [s for s in studies if s.get("pmid") in set(cited or [])]
    if not pool:
        return None
    best = sorted(pool, key=rank, reverse=True)[0]
    types = [t for t in (best.get("publication_types") or []) if not t.startswith(("Journal Article", "Research Support", "English Abstract"))]
    label = types[0] if types else "Journal article"
    return label, str(best.get("year") or ""), str(best.get("title") or "")


@lru_cache(maxsize=256)
def _render(claim: str, verdict: str, tldr: str, study_count: int, receipt: tuple | None) -> bytes:
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    pad = 64

    # The field: the same dark ground the report carries, identical on every
    # verdict, so it brands the card without tinting the conclusion.
    field_h = 352
    d.rectangle([0, 0, W, field_h], fill=DEEP)

    d.text((pad, 34), "CLAIM CHECKER", font=_f(28, 900), fill=ON_DEEP)
    basis = f"{study_count} PUBMED {'STUDY' if study_count == 1 else 'STUDIES'} READ" if study_count else "NO MATCHING STUDIES"
    sans_small = _f(17, 700)
    w = _tracked_width(d, basis, sans_small, 0)
    _tracked(d, (W - pad - w, 40), basis, sans_small, ON_DEEP_2, 0)

    # the claim, as the headline, reversed out of the field
    head = _f(56, 900)
    lines = _wrap(d, claim.strip(), head, W - pad * 2, 2)
    y = 108
    for line in lines:
        d.text((pad, y), line, font=head, fill=ON_DEEP)
        y += 68

    # The verdict, at scale on the field. Weight carries it, never hue.
    y += 8
    d.rectangle([pad, y, W - pad, y + 2], fill=DEEP_2)
    y += 20
    _tracked(d, (pad, y), "VERDICT", _f(15, 700), ON_DEEP_2, 0)
    y += 26
    label = LABELS.get(verdict, verdict.upper())
    _tracked(d, (pad, y), label, _f(40, 900), ON_DEEP, 0)

    # the takeaway sits on the page below the field
    y = field_h + 36

    # the takeaway
    if tldr:
        body = _f(31, 600)
        for line in _wrap(d, tldr, body, W - pad * 2, 2):
            d.text((pad, y), line, font=body, fill=INK)
            y += 42

    # the source it leaned on hardest
    if receipt:
        label_r, year, title = receipt
        y += 14
        meta = f"{label_r.upper()}{' · ' + year if year else ''}"
        _tracked(d, (pad, y), meta, _f(15, 700), INK_3, 0)
        y += 26
        tf = _f(23, 400)
        for line in _wrap(d, title, tf, W - pad * 2, 1):
            d.text((pad, y), line, font=tf, fill=INK_2)
            y += 32

    # colophon
    d.rectangle([pad, H - 76, W - pad, H - 75], fill=RULE)
    foot = _f(16, 700)
    _tracked(d, (pad, H - 58), "NOT MEDICAL ADVICE", foot, INK_3, 0)
    w = _tracked_width(d, "CLAIM CHECKER", foot, 0)
    _tracked(d, (W - pad - w, H - 58), "CLAIM CHECKER", foot, INK_3, 0)

    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_card(claim: str, verdict: str, tldr: str, study_count: int, receipt: tuple | None = None) -> bytes:
    return _render(claim, verdict, tldr or "", study_count, tuple(receipt) if receipt else None)
