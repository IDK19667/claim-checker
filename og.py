"""
Server-rendered share/preview card (1200x630 PNG) for a checked claim, so a
shared link unfurls in iMessage/WhatsApp/Slack with the verdict itself.

Same page as the app: the claim and its verdict on the night ground, joined
by the thread, the verdict ringed in sage; the short answer below on the day
stock. The ring and the ground are identical on every verdict: nothing here
is coloured by what the verdict was.
"""

import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FONTS = os.path.join(os.path.dirname(__file__), "static", "fonts")
DISPLAY = os.path.join(FONTS, "BricolageGrotesque.ttf")
BODY = os.path.join(FONTS, "InstrumentSans.ttf")
MONO = os.path.join(FONTS, "IBMPlexMono-Medium.ttf")

W, H = 1200, 630
DEEP = "#0f1211"
DEEP_LINE = "#2a302d"
ON_DEEP = "#eceee9"
ON_DEEP_3 = "#7d8580"
SAGE = "#9fc3b0"
PAPER = "#f1f2ee"
INK = "#121514"
INK_2 = "#434a46"
INK_3 = "#5d6560"
RULE = "#d9dcd5"
# The page's own words for the four verdicts (app.VERDICT_LABELS).
LABELS = {"true": "Likely true", "false": "Likely false", "complicated": "It's complicated",
          "insufficient": "Not enough evidence"}


def _font(path: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    return f


def _display(size: int) -> ImageFont.FreeTypeFont:
    return _font(DISPLAY, size, 350)


def _body(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return _font(BODY, size, weight)


def _mono(size: int) -> ImageFont.FreeTypeFont:
    return _font(MONO, size)


# The pen ring, as drawn on the page (templates/index.html), written out as
# four cubic curves in a 300 by 80 box.
_RING = [((58, 9), (150, 0), (292, 8), (296, 40)),
         ((296, 40), (300, 72), (210, 80), (140, 79)),
         ((140, 79), (70, 78), (2, 70), (4, 41)),
         ((4, 41), (6, 12), (70, 4), (168, 6))]


def _ring(im: Image.Image, box: tuple, width: float) -> Image.Image:
    """Draw the ring into `box` at twice the size and scale it down, so the
    stroke is smooth: Pillow does not antialias lines."""
    x, y, w, h = box
    k = 2
    layer = Image.new("RGBA", (im.width * k, im.height * k), (0, 0, 0, 0))
    pts = []
    for p0, p1, p2, p3 in _RING:
        for i in range(41):
            t = i / 40
            u = 1 - t
            px = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
            py = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
            pts.append(((x + px / 300 * w) * k, (y + py / 80 * h) * k))
    ImageDraw.Draw(layer).line(pts, fill=SAGE, width=round(width * k), joint="curve")
    layer = layer.resize(im.size, Image.LANCZOS)
    return Image.alpha_composite(im.convert("RGBA"), layer)


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
    inset = pad + 30

    claim_font = _body(34)
    claim_lines = _wrap(d, "\u201c" + claim.strip() + "\u201d", claim_font, W - inset - pad, 2)
    label = LABELS.get(verdict, verdict)
    size = 76
    while size > 48 and d.textlength(label, font=_display(size)) > W - inset - pad - 60:
        size -= 4
    vfont = _display(size)

    claim_label_y = 104
    claim_y = claim_label_y + 30
    verdict_label_y = claim_y + len(claim_lines) * 44 + 24
    verdict_y = verdict_label_y + 34
    field_h = verdict_y + size + 48

    # The night ground, identical on every verdict.
    d.rectangle([0, 0, W, field_h], fill=DEEP)
    d.text((pad, 34), "Evident", font=_font(DISPLAY, 30, 500), fill=ON_DEEP)
    basis = (f"{study_count} PubMed {'study' if study_count == 1 else 'studies'} read"
             if study_count else "No matching studies")
    small = _mono(16)
    d.text((W - pad - d.textlength(basis, font=small), 44), basis, font=small, fill=ON_DEEP_3)

    # The thread, from the claim's knot down to the verdict's.
    d.rectangle([pad + 3, claim_label_y + 10, pad + 4, verdict_label_y + 10], fill=DEEP_LINE)
    for ky in (claim_label_y + 10, verdict_label_y + 10):
        d.ellipse([pad, ky - 4, pad + 8, ky + 4], fill=SAGE)
    d.text((inset, claim_label_y), "The claim you checked", font=small, fill=ON_DEEP_3)
    y = claim_y
    for line in claim_lines:
        d.text((inset, y), line, font=claim_font, fill=ON_DEEP)
        y += 44
    d.text((inset, verdict_label_y), "Verdict", font=small, fill=ON_DEEP_3)
    d.text((inset, verdict_y), label, font=vfont, fill=ON_DEEP)
    vw = d.textlength(label, font=vfont)
    # Wide enough at the shoulders that the pen never crosses a letter.
    im = _ring(im, (inset - 12 - vw * 0.07, verdict_y - 8, vw * 1.14 + 24, size + 34), 2)
    d = ImageDraw.Draw(im)

    # By day: the short answer, and the study it leaned on hardest.
    y = field_h + 30
    if tldr:
        body = _body(27)
        for line in _wrap(d, tldr, body, W - pad * 2, 2):
            d.text((pad, y), line, font=body, fill=INK)
            y += 37
    if receipt and y < H - 120:
        label_r, year, title = receipt
        y += 8
        d.text((pad, y), f"{label_r}{', ' + year if year else ''}", font=_mono(15), fill=INK_3)
        y += 24
        for line in _wrap(d, title, _body(21), W - pad * 2, 1):
            d.text((pad, y), line, font=_body(21), fill=INK_2)

    # colophon
    foot = _mono(15)
    d.text((pad, H - 40), "Not medical advice", font=foot, fill=INK_3)
    host = "checkevident.com"
    d.text((W - pad - d.textlength(host, font=foot), H - 40), host, font=foot, fill=INK_3)

    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_card(claim: str, verdict: str, tldr: str, study_count: int, receipt: tuple | None = None) -> bytes:
    return _render(claim, verdict, tldr or "", study_count, tuple(receipt) if receipt else None)
