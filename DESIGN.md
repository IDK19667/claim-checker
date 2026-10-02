---
version: alpha
name: Evident
description: The logic of a facts panel. One typeface, near-black on off-white with every neutral mixed from the deep field, weight and scale doing all the work. Rule weight encodes importance, numbers carry the argument, caps are set tight and never tracked out. No accent colour anywhere and no verdict is tinted.
colors:
  primary: "#0e1422"
  secondary: "#fafbfc"
  tertiary: "#666e85"
  ink: "{colors.primary}"
  ink-2: "#3a4256"
  ink-3: "{colors.tertiary}"
  paper: "{colors.secondary}"
  paper-2: "#f1f3f7"
  rule: "#d3d8e2"
  rule-2: "#a3abbf"
  ev-strong: "{colors.primary}"
  ev-moderate: "{colors.rule-2}"
  ev-weak: "{colors.secondary}"
  ink-on-band: "{colors.secondary}"
  deep: "#0b1226"
  deep-2: "#18213e"
  on-deep: "{colors.secondary}"
  on-deep-2: "#a9b3cb"
  act: "#8a4f13"
  focus: "{colors.primary}"
  header-over: "rgba(11, 18, 38, 0.78)"
  header-solid: "rgba(11, 18, 38, 0.92)"
  flight-scrim: "rgba(11, 18, 38, 0.55)"
  scrim: "rgba(0, 0, 0, 0.55)"
  shadow-sheet: "rgba(0, 0, 0, 0.28)"
typography:
  label:
    fontFamily: Libre Franklin
    fontSize: 10px
    fontWeight: 700
  label-lg:
    fontFamily: Libre Franklin
    fontSize: 10.5px
    fontWeight: 600
  label-strong:
    fontFamily: Libre Franklin
    fontSize: 11px
    fontWeight: 700
  caption-sm:
    fontFamily: Libre Franklin
    fontSize: 11.5px
    fontWeight: 400
  caption:
    fontFamily: Libre Franklin
    fontSize: 12px
    fontWeight: 400
  caption-lg:
    fontFamily: Libre Franklin
    fontSize: 12.5px
    fontWeight: 400
  body-sm:
    fontFamily: Libre Franklin
    fontSize: 13px
    fontWeight: 400
  body-note:
    fontFamily: Libre Franklin
    fontSize: 13.5px
    fontWeight: 400
  body-compact:
    fontFamily: Libre Franklin
    fontSize: 14px
    fontWeight: 400
  body:
    fontFamily: Libre Franklin
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.5
  body-lead:
    fontFamily: Libre Franklin
    fontSize: 15.5px
    fontWeight: 400
    lineHeight: 1.5
  lede:
    fontFamily: Libre Franklin
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.45
  takeaway:
    fontFamily: Libre Franklin
    fontSize: 23px
    fontWeight: 700
    lineHeight: 1.26
    letterSpacing: -0.015em
  takeaway-min:
    fontFamily: Libre Franklin
    fontSize: 19px
    fontWeight: 700
    lineHeight: 1.26
    letterSpacing: -0.015em
  figure-xl:
    fontFamily: Libre Franklin
    fontSize: 26px
    fontWeight: 900
    lineHeight: 1
    letterSpacing: -0.03em
    fontFeature: "'tnum' 1"
  entry-claim:
    fontFamily: Libre Franklin
    fontSize: 17px
    fontWeight: 600
    letterSpacing: -0.01em
  claim-input:
    fontFamily: Libre Franklin
    fontSize: 18px
    fontWeight: 600
  panel-title:
    fontFamily: Libre Franklin
    fontSize: 20px
    fontWeight: 900
    lineHeight: 1.0
    letterSpacing: -0.03em
  sheet-title:
    fontFamily: Libre Franklin
    fontSize: 22px
    fontWeight: 900
    lineHeight: 1.18
    letterSpacing: -0.025em
  hero-sub:
    fontFamily: Libre Franklin
    fontSize: 26px
    fontWeight: 600
    letterSpacing: -0.02em
  claim:
    fontFamily: Libre Franklin
    fontSize: 34px
    fontWeight: 900
    lineHeight: 1.04
    letterSpacing: -0.035em
  verdict:
    fontFamily: Libre Franklin
    fontSize: 32px
    fontWeight: 900
    lineHeight: 0.98
    letterSpacing: -0.035em
  claim-wide:
    fontFamily: Libre Franklin
    fontSize: 36px
    fontWeight: 900
    lineHeight: 1.08
    letterSpacing: -0.03em
  verdict-wide:
    fontFamily: Libre Franklin
    fontSize: 38px
    fontWeight: 900
    lineHeight: 0.98
    letterSpacing: -0.035em
  hero:
    fontFamily: Libre Franklin
    fontSize: 40px
    fontWeight: 900
    lineHeight: 0.98
    letterSpacing: -0.035em
  hero-wide:
    fontFamily: Libre Franklin
    fontSize: 52px
    fontWeight: 900
    lineHeight: 0.98
    letterSpacing: -0.035em
spacing:
  gutter: 16px
  row: 7px
  block: 18px
  section: 26px
  page: 40px
rounded:
  none: 0px
components:
  panel:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  panel-head:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  band:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ink-on-band}"
  evidence-field:
    backgroundColor: "{colors.deep}"
    textColor: "{colors.on-deep}"
  field-rule:
    backgroundColor: "{colors.deep-2}"
  field-caption:
    backgroundColor: "{colors.deep}"
    textColor: "{colors.on-deep-2}"
  action:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.act}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  button-ghost:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  field:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  hairline:
    backgroundColor: "{colors.rule}"
  hairline-2:
    backgroundColor: "{colors.rule-2}"
  surface-hover:
    backgroundColor: "{colors.paper-2}"
  header-over-flight:
    backgroundColor: "{colors.header-over}"
    textColor: "{colors.on-deep}"
  header-after-flight:
    backgroundColor: "{colors.header-solid}"
    textColor: "{colors.on-deep}"
  flight-chapter:
    backgroundColor: "{colors.flight-scrim}"
    textColor: "{colors.on-deep}"
  evidence-bar-strong:
    backgroundColor: "{colors.ev-strong}"
  evidence-bar-moderate:
    backgroundColor: "{colors.ev-moderate}"
  evidence-bar-weak:
    backgroundColor: "{colors.ev-weak}"
  caption:
    textColor: "{colors.ink-3}"
  secondary-text:
    textColor: "{colors.ink-2}"
  sheet:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  sheet-shadow:
    backgroundColor: "{colors.shadow-sheet}"
  scrim:
    backgroundColor: "{colors.scrim}"
---

# DESIGN.md: Evident

The visual system, written down. `DECISIONS.md` holds why each choice was
made; this holds what the choice is. Refinement preserves this world. A
redesign replaces this file on purpose, never by drift.

## The idea

**A facts panel, not a page of prose.** The reference is the panel on the
back of a food or medicine package: a fixed, unglamorous, ferociously
legible block where rule weight encodes importance, every row reports a
number somebody counted, and nothing is drawn that does not report
something. That is what this product does, so that is what it looks like.

This world replaced a broadsheet, and the reason is worth keeping in
view. The broadsheet was cream paper, a high contrast serif display,
hairline rules, zero radius and letterspaced small caps. Those are not
neutral choices: they are the two most common shapes an AI reaches for
when asked to make something look considered. A tool whose entire product
is credibility cannot afford to look like the default output of the thing
it is arguing with. See `DECISIONS.md`.

## One typeface

**Libre Franklin, weights 400 to 900, and nothing else.** A grotesque
with enough character to carry a headline at 900 and enough plainness to
set a paragraph at 400. Self-hosted as one variable woff2 latin subset,
29KB. Do not add a second family: the whole hierarchy is weight and
scale, and a second voice would dilute the one that is doing the work.

Do not reintroduce a Google Fonts link. It costs a third party
connection, it breaks the offline first paint, and the Content Security
Policy allows `font-src 'self'` only, so it would be blocked outright.

## Caps are tight

Labels are uppercase at weight 700, **letter spacing zero**. Tracked out
small caps is the single mannerism this design exists to avoid; it is the
tell that reads as generated. Where caps need to feel lighter, drop the
colour to `ink-3`, never open the tracking.

Headlines run tight the other way: `-0.02em` to `-0.035em`. Numbers are
always `tabular-nums`.

## Colour, and what it is not allowed to mean

Black on white with four greys, plus exactly two colours, and **neither
of them may ever touch a verdict**:

- **The field**, `#0b1226`, a deep navy ground. It carries the front
  page's hero and the report's head, and it is **identical on every
  verdict**. It brands the product; it reports nothing.
- **The action**, `#0b5fd0`, used only on things you can operate: the
  deep dive control and outbound links. Never on evidence, never on a
  verdict, never as a surface.

True, false and complicated are drawn identically, in the same ink, at
the same weight. A tool whose only product is credibility does not get
to colour its own answers, and green for true would imply a certainty
the studies do not support.

This is a deliberate reversal of the previous rule, which allowed no
colour at all. Austerity read as unfinished rather than as restraint;
see `DECISIONS.md`.

There is no dark mode. The page is white in every light, on every device,
and `color-scheme: light` keeps the browser's own furniture from going
dark around it.

## Rule weight is the hierarchy

The panel's structure is carried entirely by horizontal rules, and their
weight is the grammar:

- **8px** closes the panel head. It is the heaviest mark on the page.
- **4px** separates one group of facts from the next.
- **2px** is a border: the panel itself, a button, the masthead.
- **1px** separates one line from the line below it.

Nothing is rounded, but zero radius is a consequence of the panel, not
the signature. The signature is the rule weight.

## The evidence bar

Study design drawn as fill density, never hue: solid ink for strong,
mid grey for moderate, white with an inked edge for weak, and a 135
degree hatch for retracted. It survives greyscale, printing and colour
blindness, and it cannot be mistaken for a verdict.

## Structure

- **The masthead:** wordmark at 900 uppercase, the date, "how it works",
  closed by a 2px rule.
- **The front page:** the question as a 40px headline, the lede, then the
  ledger of real counts between two 4px rules, then the claim box and the
  primary button, then the latest checks.
- **Type-ahead:** a panel hung directly under the claim box, inside the
  field, so it uses the on-deep ink rather than the white-ground tokens.
  A heading in small caps says which kind of list it is, "Already checked"
  for literal hits and "Did you mean" for corrected spellings, because a
  fixed typo must never be passed off as what the reader typed. Each row
  is the claim with its verdict, so the answer is previewed before the tap.
  The keyboard selection carries a 3px inset rule as well as a tint, since
  tint alone is invisible to anyone arrowing through it without a mouse.
- **Nothing found:** when a check has no evidence to stand on, the source
  column ends with a panel under a 2px rule. A heading that names which
  emptiness it is ("Nothing matched" for no papers, "Nothing that tests
  this" for papers that miss the claim), one sentence saying plainly that
  this is not the same as the claim being false, then actions on ruled
  rows: the exact search, a wider one, and a plain-words hint. Below that,
  claims already checked that sit near this one. It never proposes an
  answer.
- **The 404:** the masthead, "That page is not here", and the claim field
  again, plus the most recent checks. A dead end still offers the one
  thing the visitor came for, and carries the medical disclaimer like
  every other screen.
- **The field:** the report's head and the front page's hero, both on
  `#0b1226`. The report's field carries the case line, the claim at 34px,
  the verdict at 32px, and **the evidence field**: one mark per study,
  height by study design (strong full, moderate 62%, weak 34%, retracted
  hatched at 46%), filled when the verdict leaned on it and outlined when
  it did not. Drawn from PubMed's publication types, classified once on
  the server so both renderers agree, and identical whatever the verdict
  says.
- **The report:** back, then the field, then **the panel**, which lifts
  onto the field by 14px with the only shadow in the design, then the
  reasoning in prose, then the actions, then the sources. The field and
  the panel bleed to the same edge and are therefore the same width. On a
  wide screen the report is two columns, the field and the panel in the
  first and the sources in the second, and both columns open on the same
  line; while a check is still running there is nothing to put in the
  second column, so checking stays one.
- **The panel:** head ("Evidence facts" plus the count) over an 8px rule,
  the verdict at 30px/900 over a 4px rule, the takeaway, the counted
  figures one per line, the bar and its key, and "Still open" under a
  final 4px rule. The verdict lives inside the panel because it is a
  reported fact, not a banner over one.
- **The deeper layer:** `Read the full breakdown`, a third `<details>` in
  the same fold grammar as the source groups, closed on arrival and opened
  by the reader, under a 4px rule between the reasoning and the actions.
  Its count is the number of studies the breakdown actually rests on, which
  is smaller than the number read and is not the same as the panel's
  "relied on". Inside, five sections separated by 1px rules: the claim
  split into its parts, each quoted in curly quotes exactly as the reader
  wrote it, then the evidence in short paragraphs, the effect size under
  its own 2px rule, why the verdict is what it is, who it covers and who it
  does not, and what is still unknown. Every sentence carries the study
  numbers it rests on as tap targets, and a closing line in fine print says
  so. The short answer above it does not move, shrink or change: the layer
  is additive, and a result that has no breakdown renders without it rather
  than with an empty fold.
- **Who it applies to, narrowed:** when the evidence the verdict leans on
  was run in one group only, `Who this applies to` carries a second
  paragraph naming the groups, set as an aside: 14px, indented 12px off a
  2px `--rule` on its left edge, the way a footnote to the paragraph above
  it would be set. It is drawn from the records' own titles and publication
  types, not from the model's prose, so it is a fact about the evidence
  rather than a finding from it, and it is absent when every cited study
  was general. No new token: the left rule is the same `--rule` the folds
  use.
- **The sources:** two folded groups, `Relied on for the verdict` open and
  `Read, not relied on` closed, each a `<details>` with its count. Every
  study stays in the HTML in both states, for crawlers and for a reader
  with no JavaScript. Study numbers stay global because the prose points
  at them by number.
- **Deep dive:** a boxed control on every study row, drawn at rest and
  filled with ink on hover and focus. Never revealed on hover only: on a
  phone there is no hover, and a control a mouse alone can find is a
  control most readers never find.
- **Cards** (share and link preview): the same panel logic, 1200x630.
  Wordmark, one heavy rule, the claim, the verdict reversed out of an ink
  band, the takeaway, the strongest source it leaned on, the colophon.

## The footage

The home page opens on a fly-through: real footage of one real check,
replayed. It is the one place in the product where a moving picture is
allowed, and it earns that on a single condition, stated on the clip
itself in every state: **"Illustrative footage. A real check, replayed."**
It is not a mood film and not a generated hero. It is a recording of the
thing the page does, which is why it is not covered by the ban on
generated imagery and video below.

**The footage does not follow a check.** Submit a claim and the fly-through
stands down: it is five screens of scroll belonging to a page the reader
has just left, and keeping it running means decoding frames for a canvas
nobody can see. What replaces it is **the band**.

### The band and the sheet

Checking, a verdict, an error and a check that found nothing are one
screen, at every width and on every route:

- **A band of footage across the top**, full bleed, 42vh on a desktop and
  30vh on a phone, under the masthead and above everything else. It is
  `aria-hidden` and carries nothing readable.
- **A sheet below it**, the same centred column the rest of the paper
  uses, lifted 48px into the foot of the band so the two overlap rather
  than butt, with a 3px ink edge along its top and the page ground showing
  on both sides of it. Everything the reader came for is on the sheet: the
  claim, "Check another claim", the live step lines, the verdict, the
  studies.

An earlier round ran the footage *behind* the report instead. At this
column width that left it visible only as two narrow strips at the edges,
a window frame on one side and half a bookshelf on the other, and no way
to read either as a picture. A band is the shape a cinematic still wants;
a background is not.

**Three clips, one per stage of the work.** Searching plays while the
search is built and run, weighing once studies have come back, and the
verdict shot is what the result rests on. Each is 1920x600 and crops with
a chosen `object-position`, never a default one: the book's spine and the
page it is turning, the marker's tip rather than the knuckles behind it,
the microscope rather than the rack beside it. See `media/PRODUCTION.md`
round 10.

Four rules hold it together:

- **Every clip change is an event, never a timer.** A clip changes because
  the server said that thing happened. The band never runs on to footage
  of work that has not happened.
- **Text never sits on moving pictures.** Nothing readable is on the band
  at all, which is the strongest form of the rule: no panel has to buy its
  contrast back, and so there is **no backdrop blur, no scrim and no tint
  anywhere**.
- **The footage stays at full brightness.** It is not dimmed or darkened
  to make room for the words. The words have their own room.
- **The verdict is not colour coded here either.** The stamp on the sheet
  is ink on paper, exactly as it is on `/checks`.

**The dark field and the panel under it are the same width**, and so share
a left edge. Inside the gutters the panel was a gutter narrower on each
side, which read as a misprint.

**Transitions between the three states** (claim, checking, verdict) are
`transform` and `opacity` only, 300 to 500ms, measured CLS 0. No layout
jump, no white flash. The band and the sheet arrive once, when a live
check starts, and do not re-animate when the verdict lands.

**Reduced motion, Save-Data and a slow connection get the same screens
with nothing playing**: the band shows each clip's own first frame as a
still, the same sheet arrives with the same copy in the same order, and
the same way out. Not a degraded flow, the same flow without the motion.

**A shared result link opens cold on one still**, server rendered, 27KB,
and loads no video at all. Same band, same sheet, same everything, and no
entrance: nothing moved, the reader navigated there, and a fade-in would
read as something still loading.

**The clips are never in the way of the first screen.** They are fetched
once the reader presses Check, or in idle time after the home page has
gone interactive, whichever comes first.

**"Skip to the checker"** is visible on the first screen of the
fly-through throughout. It belongs to the fly-through, which is the only
place it has anything to skip.

## The keyboard

Arrow up and down walk between studies with focus visible on each, and
Enter opens the deep dive. **`/` returns to the claim box** from anywhere
outside a sheet or a text field, with the previous claim selected.

## Motion

Two moments in the page, plus the sheet:

1. **The rules under cited sources** draw left to right, staggered 120ms.
2. **The wire** prints one line per stage while a check runs, a pulsing
   square against the live line.

The sheet rises 40px and fades in over 260ms, and **it leaves the way it
arrived**: the closed state sits on the base rule, `@starting-style`
supplies the entry, and `overlay` and `display` transition
`allow-discrete` so it is still painted on the way out.

Nothing else in the page animates. No entrances on sections, no parallax,
no cursor effects, no scroll hijacking, no animated backgrounds. All of it
collapses under `prefers-reduced-motion`.

The fly-through on `/` and the band over the sheet are the exceptions, and
both are footage rather than animation: see **The footage** above for what
they are allowed to do and what they are not.

## Copy

Plain, specific and finished. Controls name their action, errors name the
problem and the way out, every verdict ends by naming what is still open.
Numbers from the abstracts beat adjectives.

**No em-dashes or en-dashes anywhere a user can see**, in hand written
copy or model output. A test enforces it on the rendered page;
`verdict.tidy_prose` enforces it on the model's prose.

## Anti-patterns

Never reintroduce: a cream or beige ground, a serif display face,
letterspaced small caps, hairline newspaper columns, an accent colour,
colour coded verdicts, pill buttons, stat rows, gauges, glass, shadows
used for depth, emoji icons, gradient washes, generated hero imagery or
video, component library drop-ins, animated or shader backgrounds, cursor
effects, or analytics of any kind.

Two of those need their edges drawn, now that the home page carries
footage. **Generated** imagery and video stay banned: pictures invented by
a model imply evidence the page does not have. The fly-through is a
recording of a real check and says so on screen. **Glass** stays banned
without exception, including over the footage: no backdrop blur, no
translucent panels, no scrim under text. Panels are opaque or they are not
panels.
