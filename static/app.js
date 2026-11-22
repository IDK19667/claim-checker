/* Evident front end. No framework, no build step. */

const $ = (id) => document.getElementById(id);

const askSection = $("ask");
const resultSection = $("result");
const errorSection = $("error");
const form = $("claim-form");
const input = $("claim-input");
const submitBtn = $("submit-btn");
const btnLabel = submitBtn.querySelector(".btn-label");
const statusEl = $("status");

const VERDICT_LABELS = {
  true: "Likely true",
  false: "Likely false",
  complicated: "It's complicated",
  insufficient: "Not enough evidence",
};

// Claims people actually paste in. Tapping one runs it. The first screen shows
// the first three, so the order is the order they are offered in.
const EXAMPLES = [
  "Apple cider vinegar cures diabetes",
  "Cracking your knuckles causes arthritis",
  "Intermittent fasting improves brain function",
  "Detox teas flush toxins out of your body",
  "5G weakens your immune system",
  "Vaccines cause autism",
  "Ozempic cures alcoholism",
];

// ---- Evidence types: what they mean, how much they weigh ------------------

// The first matching label wins, so the badge shows the most informative type.
const TYPES = [
  // retracted first: a withdrawn trial is retracted, never strong
  ["Retracted Publication", "retracted", "Withdrawn by the journal after publication. Its findings should not be relied on."],
  // strong
  ["Meta-Analysis", "strong", "Combines the results of several studies into one estimate. It is only as sound as the studies it pools, and it can mix very different ones."],
  ["Network Meta-Analysis", "strong", "Combines many studies to compare several treatments at once. Comparisons made only indirectly are less certain than head-to-head trials."],
  ["Systematic Review", "strong", "Searches for every study on a question, using set rules for what counts. It is only as sound as the studies it found."],
  ["Randomized Controlled Trial", "strong", "Randomly assigns people to a treatment or a comparison group. A small size, a short run or a narrow group limits what it shows."],
  ["Practice Guideline", "strong", "Recommendations a medical body wrote after reviewing the evidence. They reflect that body's judgment and can lag behind newer studies."],
  // weak
  ["Case Reports", "weak", "Describes one patient or a few. It can raise a question, but it cannot show cause or how common something is."],
  ["Editorial", "weak", "An opinion piece by the journal's editors. It gives a view, not data."],
  ["Comment", "weak", "A published reply to another paper. It is not a study."],
  ["Letter", "weak", "A short letter to the journal. It is not a study."],
  ["News", "weak", "A news item. It is not a study."],
  // medium
  ["Controlled Clinical Trial", "", "Tests people against a comparison group without random assignment. The groups may have differed from the start."],
  ["Clinical Trial", "", "Tests a treatment on people, not always with a comparison group. Without one it cannot show cause."],
  ["Observational Study", "", "Watches what happens to people who did or did not do something. It can show links but not prove cause, since other factors may differ."],
  ["Multicenter Study", "", "Run at several sites, which can make results more general. The label says nothing about how well it was designed."],
  ["Comparative Study", "", "Compares two or more approaches. What it shows depends on how the comparison was run."],
  ["Review", "", "An expert's summary of the research. It depends on which studies the author chose."],
];
const IGNORE = /^(Journal Article|Research Support|English Abstract|Introductory)/;
const DEFAULT_TYPE = ["Journal article", "", "A research paper. The abstract says what kind of study it was."];

function typeInfo(types) {
  const t = (types || []).filter((x) => !IGNORE.test(x));
  for (const [label, cls, what] of TYPES) {
    const hit = t.find((x) => x.startsWith(label));
    if (hit) return { label, cls, what };
  }
  return { label: t[0] || DEFAULT_TYPE[0], cls: "", what: DEFAULT_TYPE[2] };
}

// Why a study is listed, in one line: what the verdict did with it and, for a
// study it leaned on, what that kind of study does and where it stops.
// Mirrors display.study_why; a test runs both on the same records.
// Off topic is checked first: a study set aside is never described as relied on.
function studyWhy(s) {
  if (s.off_topic) return `Read and set aside: ${String(s.off_topic).replace(/\.+$/, "")}.`;
  if (s.cited_in_verdict) {
    const ti = typeInfo(s.publication_types);
    const out = ["Relied on for the verdict."];
    if (ti.label !== DEFAULT_TYPE[0]) out.push(ti.what);
    if (s.population) out.push(`Studied in: ${s.population}.`);
    return out.join(" ");
  }
  if (s.background) {
    const why = String(s.background_why || "").replace(/\.+$/, "");
    return why ? `Background only, not counted: ${why}.` : "Background only, not counted.";
  }
  return "Read, not relied on.";
}

// The study list's groups, as indexes into `studies`, in page order: relied
// on, background only, read and not relied on, excluded as off topic. Every
// study is in exactly one. Mirrors display.source_groups; a test runs both.
function sourceGroups(studies) {
  const out = { cited: [], back: [], others: [], excluded: [] };
  (studies || []).forEach((s, i) => {
    out[s.off_topic ? "excluded" : s.cited_in_verdict ? "cited" : s.background ? "back" : "others"].push(i);
  });
  return out;
}

// A study classified off topic is never relied on, even in a result saved on
// this device before the server dropped such studies from the cited list.
// Every count, mark and chart below reads cited_in_verdict, so it is set once.
function dropOffTopicCites(data) {
  (data.studies || []).forEach((s) => { if (s.off_topic) s.cited_in_verdict = false; });
  return data;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// ---- Views -----------------------------------------------------------------

const askMore = $("ask-more");
const trustSection = $("trust");
// The tab title follows the view, so history entries and open tabs are
// tellable apart. The server sets the result title; this keeps it honest
// when the view changes without a page load.
const DEFAULT_TITLE = "Evident: check a health claim against real research";
function setTitle(result) {
  document.title = result
    ? `${VERDICT_LABELS[result.verdict] || result.verdict}: \u201c${result.claim}\u201d \u00b7 Evident`
    : DEFAULT_TITLE;
}
const root = document.documentElement;

/* Pausing what nobody can see. A tab in the background is still asked to run
 * every animation on it, and a part of the page scrolled away is still asked
 * to loop. While the tab is hidden, body.paused freezes every CSS animation.
 * Scrolled out of view, the looping dots hold too: the mic while it listens,
 * the live line of the reading list, the working status. */
function onTabVisibility() {
  document.body.classList.toggle("paused", document.hidden);
}
document.addEventListener("visibilitychange", onTabVisibility);
onTabVisibility();

if ("IntersectionObserver" in window) {
  const loops = new IntersectionObserver((entries) => {
    for (const e of entries) e.target.classList.toggle("offscreen", !e.isIntersecting);
  });
  for (const id of ["mic-btn", "reading", "status"]) if ($(id)) loops.observe($(id));
}

/* The screen the reader is on. `null` is the home page. Everything else is
 * the report on its night ground, and the attribute on <html> is what styles
 * it. There is no picture above it: while a check runs the claim and the
 * reading log are the whole screen, and they stay where the verdict will
 * be. */
const screenState = {
  phase: $("server-result") ? "result" : null,
  enter() {
    this.phase = "checking";
    root.dataset.screen = "checking";
    // The sheet arrives once, here. A shared link opens on the
    // same layout but must not animate into it: nothing moved, the reader
    // navigated there, and a fade-in would read as something still loading.
    document.body.classList.add("live-check");
    window.scrollTo(0, 0);
  },
  settle(phase) {
    this.phase = phase;
    root.dataset.screen = phase;
  },
  leave() {
    if (!this.phase) return;
    this.phase = null;
    delete root.dataset.screen;
    document.body.classList.remove("live-check");
    window.scrollTo(0, 0);
  },
};

// Back to the claim box. The checker is the first thing on the page, so
// there is nothing to scroll past: just put the cursor in it.
function focusClaim() {
  input.focus({ preventScroll: true });
}

// Every answer under "Before you trust a verdict" ends at the claim box. The
// link works without this, as #claim-input; this also puts the cursor in it,
// and keeps the fragment out of the address bar and any link shared from it.
document.addEventListener("click", (e) => {
  const to = e.target.closest && e.target.closest("a.to-claim");
  if (!to) return;
  e.preventDefault();
  input.scrollIntoView({ block: "center" });
  input.focus({ preventScroll: true });
});

// The phone's share bar waits for the short answer to scroll past. On first
// view the answer is all there is on screen, and a bar across the foot of it
// would cover the lines the reader came for. Once the summary is above the top
// edge the bar appears; scroll back up to it and the bar goes again.
let shareBarWatch = null;
function disarmShareBar() {
  if (shareBarWatch) { shareBarWatch.disconnect(); shareBarWatch = null; }
  $("bar-sticky").hidden = true;
  document.body.classList.remove("has-bar");
}
function armShareBar() {
  disarmShareBar();
  const summary = document.querySelector("#result .answer");
  if (!summary || !("IntersectionObserver" in window)) return;
  shareBarWatch = new IntersectionObserver(([e]) => {
    const past = !resultSection.hidden && !e.isIntersecting && e.boundingClientRect.bottom <= 0;
    $("bar-sticky").hidden = !past;
    document.body.classList.toggle("has-bar", past);
  });
  shareBarWatch.observe(summary);
}

function show(section) {
  for (const s of [askSection, resultSection, errorSection]) s.hidden = s !== section;
  askMore.hidden = section !== askSection;
  if (trustSection) trustSection.hidden = section !== askSection;
  document.body.classList.toggle("view-ask", section === askSection);
  if (section !== resultSection) disarmShareBar();
  window.scrollTo(0, 0);
}
if (!$("server-result")) document.body.classList.add("view-ask");

function setStatus(text, working) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("working", !!working);
}

function setWorking(on) {
  submitBtn.disabled = on;
  btnLabel.textContent = on ? "Checking…" : "Check the research";
  if (!on) setStatus("", false);
}

let currentResult = null;
let userTapped = false;
window.addEventListener("pointerdown", () => { userTapped = true; }, { once: true, passive: true });

// "Study 4" / "Studies 1, 4 and 6" in the explanation become tap targets
// that open that study's sheet, so the reader never has to count rows.
function linkStudyRefs(text, count) {
  const safe = escapeHtml(text);
  return safe.replace(/\b(Stud(?:y|ies))\s+(\d+(?:\s*(?:,\s*and|,|and|&amp;)\s*\d+)*)/gi, (m, word, nums) => {
    const linked = nums.replace(/\d+/g, (n) => {
      const i = Number(n);
      return i >= 1 && i <= count ? `<button type="button" class="ref" data-i="${i - 1}" aria-label="Open study ${i}">${i}</button>` : n;
    });
    return `${word} ${linked}`;
  });
}

// How much to lean on the answer, in one sentence. The wording of a real check
// is made on the server (evidence.evidence_line); mirrors display.confidence_line.
function confidenceLine(data) {
  // verdict.GUIDANCE_LINE, the same words (a test holds them equal).
  const GUIDANCE_LINE = "Rests on health guidance, not on these studies";
  const studies = data.studies || [];
  const cited = studies.filter((s) => s.cited_in_verdict);
  if (!studies.length) return "No direct evidence either way: nothing on PubMed matched well enough to weigh.";
  const line = String(data.evidence_line || "").trim().replace(/\.+$/, "");
  // An answer set by health guidance says so even when no study listed is relied on.
  if (line === GUIDANCE_LINE) return line + ".";
  if (!cited.length) return "No direct evidence either way: none of the studies tests this claim.";
  if (line) return line + ".";
  const tiers = new Set(cited.map((s) => s.tier || "moderate"));
  const word = tiers.has("strong") ? "Strong"
    : [...tiers].some((t) => t !== "weak" && t !== "retracted") ? "Moderate" : "Weak";
  return `${word} study designs.`;
}

// A date only when the record really has a readable one. fmtDate falls back
// to today, which is right for a check that just ran and wrong for a saved
// record with no date.
function hasDate(iso) { return !!iso && !Number.isNaN(new Date(iso).getTime()); }

function fmtDate(iso) {
  try { return new Date(iso || Date.now()).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); }
  catch { return ""; }
}

// Mirrors the evidence chart in templates/index.html. Every count and every
// word of its three lines is computed server-side in evidence.py, so a
// streamed check and the page a shared link renders can never word the same
// set of studies differently.
// ---- What the page prints from the breakdown, each sentence once -----------
// Mirrors display.py (the page a shared link gets); a test runs both on the
// same records. "What the research says" is picked from the same paragraphs
// the breakdown prints, so the breakdown loses every sentence already shown.
const WHO_LABEL = "Who was studied: ";
const splitSentences = (text) =>
  String(text || "").trim().split(/(?<=[.!?])\s+(?=[A-Z0-9\u201c"(])/).map((x) => x.trim()).filter(Boolean);
const sentenceKey = (x) => x.toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 80);
function unseen(text, seen) {
  const out = [];
  for (const sentence of splitSentences(text)) {
    const k = sentenceKey(sentence);
    if (k && !seen.has(k)) { seen.add(k); out.push(sentence); }
  }
  return out.join(" ");
}
function breakdownView(data) {
  const bd = data && data.breakdown;
  if (!bd) return null;
  const explanation = String(data.explanation || "").trim() ? [data.explanation] : [];
  let says = (bd.says || []).filter((ln) => String(ln || "").trim());
  if (!says.length) says = explanation;
  says = says.filter((ln) => !ln.toLowerCase().startsWith(WHO_LABEL.trim().toLowerCase()));
  if (!says.length) says = explanation;
  const seen = new Set();
  for (const ln of says) for (const x of splitSentences(ln)) seen.add(sentenceKey(x));
  for (const x of splitSentences(data.still_open)) seen.add(sentenceKey(x));
  const parts = [];
  for (const p of bd.parts || []) {
    const take = unseen(p.assessment, seen);
    if (take) parts.push({ part: p.part || "", assessment: take });
  }
  const evidence = (bd.evidence || []).map((para) => unseen(para, seen)).filter(Boolean);
  return {
    says, parts, evidence,
    effect_size: unseen(bd.effect_size, seen),
    concluded: bd.concluded || [],
    strength: unseen(bd.strength, seen),
    applies_to: unseen(bd.applies_to, seen),
    populations: bd.populations || [],
    not_applies_to: unseen(bd.not_applies_to, seen),
    unknowns: unseen(bd.unknowns, seen),
  };
}

// (d) on the result screen. The sentences are picked server-side in
// breakdown.says from prose that has already passed the gate; a result
// cached before the block existed falls back to the explanation, which is
// what that reader was shown at the time.
function renderSays(data) {
  const sec = $("says-sec"), wrap = $("says");
  if (!sec || !wrap) return;
  const bv = breakdownView(data);
  const lines = (bv ? bv.says : [data.explanation])
    .filter((t) => String(t || "").trim());
  sec.hidden = !lines.length;
  wrap.innerHTML = lines
    .map((t) => `<p class="says-p">${linkStudyRefs(t, data.studies.length)}</p>`)
    .join("");
}

// A result saved on this device before the shelf existed carries no cloth:
// its tier stands in, so an old check still draws a whole shelf.
const KIND_FROM_TIER = { strong: "strong", weak: "weak", retracted: "retracted" };
const kindOf = (s) => s.kind || KIND_FROM_TIER[s.tier] || "human";
const KIND_LABELS = { strong: "Strong design", human: "Human study", lab: "Animal or lab",
                      weak: "Opinion or single case", retracted: "Retracted" };

function renderChart(data) {
  const sec = $("chart-sec");
  if (!sec) return;
  const ev = data.evidence;
  if (!ev || !ev.read) { sec.hidden = true; return; }
  sec.hidden = false;
  $("chart-sum").textContent = ev.summary || "";
  $("chart-facts").textContent = ev.facts || "";
  $("chart-desc").textContent = ev.described || "";

  const bars = $("chart-bars");
  bars.classList.remove("reveal");
  bars.innerHTML = data.studies.map((s, i) => {
    const tip = [s.type_label, s.year, s.off_topic ? "off topic" : ""].filter(Boolean).join(" \u00b7 ");
    const said = [`${s.spine ? s.spine + " " : "Study "}${String(i + 1).padStart(2, "0")}`,
                  (s.type_label || "").toLowerCase() === (s.spine || "").toLowerCase() ? "" : (s.type_label || "").toLowerCase(), s.year,
                  s.cited_in_verdict ? "used for this verdict"
                    : s.off_topic ? "read, off topic"
                    : s.background ? `${s.background_why || "animal or lab"}, background only` : "read but not used"]
      .filter(Boolean).join(", ");
    return `<button type="button" class="barcol${s.cited_in_verdict ? " is-used" : ""}" data-i="${i}" style="--i:${i}"` +
      ` aria-label="${escapeHtml(said)}. Go to it in the list of studies.">` +
      `<span class="barcol-track"><span class="barcol-bar h-${escapeHtml(s.tier || "moderate")} k-${escapeHtml(kindOf(s))}">` +
      `<span class="barcol-spine" aria-hidden="true">${escapeHtml(s.spine || "")}</span></span></span> ` +
      `<span class="barcol-no">${String(i + 1).padStart(2, "0")}</span>` +
      (tip ? `<span class="barcol-tip" aria-hidden="true">${escapeHtml(tip)}</span>` : "") +
      "</button>";
  }).join("");
  // The key names only the cloths on this shelf, worded once on the server.
  const kinds = ev.kinds || Object.keys(KIND_LABELS)
    .filter((k) => data.studies.some((s) => kindOf(s) === k))
    .map((k) => ({ kind: k, label: KIND_LABELS[k] }));
  $("shelf-key").innerHTML = kinds.map((k) =>
    `<li><i class="k-${escapeHtml(k.kind)}${k.kind === "retracted" ? " h-retracted" : ""}"></i>${escapeHtml(k.label)}</li>`).join("");
  fitSpines();
  // The one new moment in the page, and only on a check that just happened:
  // a cached result is not news and does not announce itself.
  if (!data.cached && !data.restored) {
    requestAnimationFrame(() => bars.classList.add("reveal"));
  }
}

// A bar is a way into the study it stands for: mark the bar, unfold the group
// the row sits in, put the row in view and give it the focus, so a reader on
// a keyboard lands where a finger would have.
function showStudy(i) {
  for (const b of document.querySelectorAll("#chart-bars .barcol")) {
    b.classList.toggle("is-on", Number(b.dataset.i) === i);
  }
  const li = document.querySelector(`#sources .study[data-i="${i}"]`);
  if (!li) return;
  const group = li.closest("details");
  if (group) group.open = true;
  for (const el of document.querySelectorAll("#sources .study.target")) el.classList.remove("target");
  li.classList.add("target");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  li.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
  (li.querySelector(".study-go") || li).focus({ preventScroll: true });
}

// Shown only when a check came back without usable evidence. Everything
// here is derived from the claim and the local cache, and none of it
// proposes an answer: "no evidence found" is not "false", and this panel
// must never blur the two.
function renderNextSteps(ns, claim) {
  const wrap = $("next-steps");
  if (!wrap) return;
  if (!ns) { wrap.hidden = true; return; }
  wrap.hidden = false;

  const nothing = ns.kind === "nothing_found";
  $("nosteps-head").textContent = nothing ? "Nothing matched" : "Nothing that tests this";
  $("nosteps-why").textContent = nothing
    ? "PubMed returned no papers for this search. That is not the same as the claim being false: it may be unstudied, or worded in a way the index does not recognise."
    : "Papers came back, but none of them actually test this claim, so there is nothing here to weigh. That is not the same as the claim being false.";

  const acts = [];
  if (ns.exact_url) {
    acts.push(`<li>See the exact search on PubMed: <a href="${escapeHtml(ns.exact_url)}" target="_blank" rel="noopener"><code>${escapeHtml(ns.searched)}</code><svg width="15" height="15" aria-hidden="true"><use href="#i-ext"/></svg></a></li>`);
  }
  if (ns.wider_url) {
    acts.push(`<li>Search any of these terms instead of all of them: <a href="${escapeHtml(ns.wider_url)}" target="_blank" rel="noopener">${escapeHtml((ns.terms || []).slice(0, 4).join(", "))}<svg width="15" height="15" aria-hidden="true"><use href="#i-ext"/></svg></a></li>`);
  }
  acts.push("<li>Try the claim in plainer words. Name the thing and the effect, and leave out the story around them.</li>");
  $("nosteps-acts").innerHTML = acts.join("");

  const rel = ns.related || [];
  $("nosteps-related-wrap").hidden = rel.length === 0;
  if (rel.length) {
    $("nosteps-related").innerHTML = rel.map((r) => `
      <li class="entry">
        <a class="entry-link" href="/?q=${encodeURIComponent(r.claim_text)}">
          <span class="entry-claim">${escapeHtml(sentenceCase(r.claim_text))}</span>
          <span class="entry-verdict">${escapeHtml(VERDICT_LABELS[r.verdict] || r.verdict || "")}</span>
        </a>
      </li>`).join("");
  }
}

// ---- The deeper layer -------------------------------------------------------
// Mirrors the <details> in templates/index.html exactly: a cold shared link is
// server rendered and a live check is rendered here, and the two must be the
// same page. Nothing is computed from the text: every sentence that reaches
// this point has already been through the gate in breakdown.py, which dropped
// anything without a study number and anything carrying a figure the abstracts
// do not have. This only lays it out.
// Who the evidence covers and who it does not: the breakdown's own fields,
// with what was already printed above taken out. The narrow groups come off
// the PubMed records on the server, so that note is a list, not prose.
function renderWho(data) {
  const sec = $("who-sec"), wrap = $("who");
  if (!sec || !wrap) return;
  const bv = breakdownView(data);
  const count = data.studies.length;
  const groups = bv ? bv.populations : [];
  const prose = (text) => `<p class="says-p">${linkStudyRefs(text, count)}</p>`;
  const yes = !!(bv && (bv.applies_to || groups.length));
  const label = $("who-label");
  if (label) label.textContent = bv && !yes && bv.not_applies_to
    ? "Who this does not apply to" : "Who and what this applies to";
  const html = bv
    ? (bv.applies_to ? prose(bv.applies_to) : "") +
      (groups.length
        ? `<p class="deeper-p narrow">Some of this evidence comes from studies of one group only: ${escapeHtml(groups.join(", "))}. A result found in one group may not hold for everyone.</p>`
        : "") +
      (bv.not_applies_to
        ? (yes ? `<p class="section-label label-2">And who it does not</p>` : "") + prose(bv.not_applies_to)
        : "")
    : "";
  sec.hidden = !html;
  wrap.innerHTML = html;
}

function renderBreakdown(data, count, studies = []) {
  const bd = data && data.breakdown, bv = breakdownView(data);
  const wrap = $("deeper");
  if (!wrap) return;
  const body = $("deeper-body");
  if (!bd) {
    // A result cached before the breakdown existed has none. The section
    // disappears rather than opening onto an apology.
    wrap.hidden = true;
    wrap.open = false;
    body.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  // The studies the verdict used, counted from the same cited_in_verdict
  // flag the study list and the chart read. A count stored in an old
  // breakdown is never shown.
  const restsOn = studies.filter((s) => s.cited_in_verdict).length;
  // Collapsed on every new result, including one reopened from history.
  wrap.open = false;

  const prose = (text) => `<p class="deeper-p">${linkStudyRefs(text, count)}</p>`;
  const sec = (label, inner) =>
    `<section class="deeper-sec"><p class="section-label">${escapeHtml(label)}</p>${inner}</section>`;

  const out = [];
  if (bv.parts.length) {
    out.push(sec("The claim, part by part", `<ol class="parts">${bv.parts.map((p) => `
      <li class="part">
        <p class="part-claim">${escapeHtml(p.part)}</p>
        <p class="part-take">${linkStudyRefs(p.assessment, count)}</p>
      </li>`).join("")}</ol>`));
  }
  if (bv.evidence.length || bv.effect_size) {
    out.push(sec("What the evidence shows",
      bv.evidence.map(prose).join("") +
      (bv.effect_size
        ? `<p class="effect"><b>How big the effect is</b><span>${linkStudyRefs(bv.effect_size, count)}</span></p>`
        : "")));
  }
  // Quoted from the abstracts on the server, so the quote is escaped and never
  // linked: a "study 2" inside it is the authors' study 2, not ours.
  if ((bd.concluded || []).length) {
    out.push(sec("What each study concluded", `<ol class="concluded">${bd.concluded.map((c) => {
      const st = studies[c.n - 1] || {};
      const who = [`Study ${c.n}`, st.type_label, st.year].filter(Boolean).join(" · ");
      return `
      <li class="said">
        <p class="said-who">${linkStudyRefs(who, count)}</p>
        <blockquote class="said-q">${escapeHtml(c.said)}</blockquote>
      </li>`;
    }).join("")}</ol><p class="said-note">In the authors' own words, from the summary each study was published with.</p>`));
  }
  if (bv.strength) out.push(sec("Why the verdict is what it is", prose(bv.strength)));
  if (bv.unknowns) out.push(sec("What is still unknown", prose(bv.unknowns)));
  out.push(`<p class="fine">Every sentence written for this breakdown names the studies it rests on. Figures are copied from the abstracts and rounded for reading, never worked out from them. Tap a study number to read it.</p>`);
  body.innerHTML = out.join("");

  const head = wrap.querySelector(".deeper-head .group-count");
  if (head) {
    head.textContent = restsOn
      ? `${restsOn} ${restsOn === 1 ? "study" : "studies"}`
      : "";
  }
  $("deeper-count").hidden = !restsOn;
  $("deeper-what").textContent = bd.contents || "";
}

// A spine is lettered only when its word fits standing up. A short book with
// a long word shows its cloth and leaves the word to the key and the tip,
// never a word cut off part way.
function fitSpines() {
  for (const s of document.querySelectorAll(".barcol-spine")) {
    s.hidden = false;
    s.hidden = s.scrollHeight > s.clientHeight + 1;
  }
}
if (document.fonts) document.fonts.ready.then(() => fitSpines());

function renderResult(data) {
  dropOffTopicCites(data);
  currentResult = data;
  $("claim-echo").textContent = data.claim;
  showTyped(data.typed, data.typed_reason);
  $("case-line").textContent = (data.cached || data.restored) && !hasDate(data.cached_at) ? "PubMed" : `PubMed · ${fmtDate(data.cached_at)}`;

  const block = document.querySelector(".verdict-block");
  block.dataset.verdict = data.verdict;

  $("stamp").textContent = VERDICT_LABELS[data.verdict] || data.verdict;
  $("doctor-line").hidden = false;
  $("review-line").textContent = data.review_line || "";
  $("review-line").hidden = !data.review_line;
  if (userTapped && navigator.vibrate && !matchMedia("(prefers-reduced-motion: reduce)").matches) navigator.vibrate(25);

  $("tldr").textContent = data.tldr || "";
  renderSays(data);
  renderWho(data);
  $("no-evidence").hidden = data.studies.length > 0;
  $("still-open").hidden = !data.still_open;
  $("still-open-text").textContent = data.still_open || "";
  renderBreakdown(data, data.studies.length, data.studies);

  const n = data.studies.length;
  $("confidence").querySelector("mark").textContent = confidenceLine(data);

  const note = $("cached-note");
  note.hidden = !(data.cached || data.restored);
  note.textContent = data.restored
    ? "From your history on this device."
    : !hasDate(data.cached_at) ? "Result from an earlier check. Claims are re-checked after a day."
    : hasDate(data.first_checked_at)
      ? `First checked on ${fmtDate(data.first_checked_at)}. Last re-checked on ${fmtDate(data.cached_at)}. Claims are re-checked after a day.`
      : `Result from a check on ${fmtDate(data.cached_at)}. Claims are re-checked after a day.`;

  renderChart(data);
  renderNextSteps(data.next_steps, data.claim);

  $("evidence-label").textContent = n ? `Studies checked (${n})` : "Studies checked";
  const studyRow = (s, i) => {
    const ti = typeInfo(s.publication_types);
    const src = [s.journal, s.year].filter(Boolean).join(", ");
    const isDefault = ti.label === DEFAULT_TYPE[0];
    return `
      <li class="study k-${escapeHtml(kindOf(s))}${s.cited_in_verdict ? " cited" : ""}" data-i="${i}">
        <p class="meta">
          <span class="ord">${String(i + 1).padStart(2, "0")}</span>
          ${isDefault ? "" : `<span class="type ${ti.cls}">${escapeHtml(ti.label)}</span>`}
          ${s.year ? `<span>${escapeHtml(s.year)}</span>` : ""}
          ${s.cited_in_verdict ? `<span class="sr-only">Used for the verdict</span>` : ""}
        </p>
        <p class="title"><span>${escapeHtml(s.title)}</span></p>
        <p class="src">${escapeHtml(src)}${src ? " · " : ""}<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener" data-stop>PMID ${escapeHtml(s.pmid)}</a></p>
        <p class="why">${escapeHtml(studyWhy(s))}</p>
        <button type="button" class="study-go" aria-label="Deep dive on study ${i + 1}">Deep dive<svg width="15" height="15" aria-hidden="true"><use href="#i-dive"/></svg></button>
      </li>`;
  };
  // Two groups, matching the Jinja: what the verdict leaned on is open, what
  // was read and set aside is folded. Eight dense titles in a row is a wall.
  const group = (name, rows, open) => rows.length
    ? `<details class="source-group"${open ? " open" : ""}>` +
      `<summary class="group-head"><span class="group-name">${name}</span>` +
      `<span class="group-count">${rows.length}</span></summary>` +
      `<ol class="studies">${rows.join("")}</ol></details>`
    : "";
  // Animal and lab studies read for a claim about people are background
  // only: never counted as used, and grouped on their own so it shows. So
  // are opinion pieces and buying guides; the server names the group. A
  // study classified off topic goes in its own group, folded, last.
  const g = sourceGroups(data.studies);
  const rows = (idx) => idx.map((i) => studyRow(data.studies[i], i));
  const citedRows = rows(g.cited), backRows = rows(g.back), otherRows = rows(g.others);
  $("sources").innerHTML = n
    ? group("Relied on for the verdict", citedRows, true) +
      group(escapeHtml((data.evidence && data.evidence.background_label)
                       || "Background only: animal and lab studies"),
            backRows, !citedRows.length && !otherRows.length) +
      group(citedRows.length ? "Read, not relied on" : "Read", otherRows, citedRows.length === 0) +
      group("Set aside: off topic", rows(g.excluded), false)
    : `<ol class="studies"><li class="empty">Nothing on PubMed matched this well enough to weigh. That doesn't make the claim true or false: it may just not have been studied yet.</li></ol>`;

  const list = $("sources");
  list.classList.remove("sweep");
  if (!data.cached && !data.restored) {
    list.querySelectorAll(".study.cited").forEach((li, i) => li.style.setProperty("--i", i));
    requestAnimationFrame(() => list.classList.add("sweep"));
  }
  // A saved record with no search on file says nothing about one, rather
  // than "Searched PubMed for" and a blank. The link to PubMed stays.
  const usedQuery = (data.search_query_used || "").trim();
  $("query").textContent = usedQuery;
  $("query-what").hidden = !usedQuery;
  $("broadened").textContent = data.search_broadened ? " (the first search found nothing, so it was widened)" : "";
  $("more-link").href = data.pubmed_url || "#";
  $("toast").textContent = "";
  renderFeedbackState(data.claim);
  resetReport();

  armShareBar();
  setTitle(data);
  // The band goes to the verdict shot and the report lands on the sheet under
  // it. Set before the reveal, so the sheet is never painted as a bare column
  // for a frame on its way to being a sheet.
  screenState.settle("result");
  show(resultSection);
}

// While a check runs, the claim is already on the ticket and the archive's
// work prints beneath it. Cached results skip this and land directly.
function renderPending(claim) {
  currentResult = null;
  $("claim-echo").textContent = claim;
  showTyped(null);
  $("case-line").textContent = "PubMed · checking";
  const t = document.querySelector(".ticket");
  t.classList.add("pending");
  resultSection.classList.add("checking");
  const chart = $("chart-sec");
  if (chart) chart.hidden = true;
  const bars = $("chart-bars");
  if (bars) { bars.classList.remove("reveal"); bars.innerHTML = ""; }
  const says = $("says-sec");
  if (says) says.hidden = true;
  const who = $("who-sec");
  if (who) who.hidden = true;
  document.querySelector(".verdict-block").dataset.verdict = "";
  $("doctor-line").hidden = true;
  $("review-line").hidden = true;
  const log = $("reading");
  log.innerHTML = ""; log.hidden = false;
  $("feedback").hidden = true;
  document.querySelector(".evidence").hidden = true;
  $("cached-note").hidden = true;
  $("still-open").hidden = true;
  renderBreakdown(null, 0);
  disarmShareBar();
  for (const sec of [askSection, errorSection]) sec.hidden = true;
  askMore.hidden = true;
  if (trustSection) trustSection.hidden = true;
  // Checking is the same screen as the verdict, minus the verdict: the band
  // on top, the claim and the work printing on the sheet below it. The
  // report is shown here rather than through show(), which would scroll and
  // re-lay-out the page a second time on the way to the same place.
  screenState.enter();
  resultSection.hidden = false;
  document.body.classList.remove("view-ask");
  logLine("Building the search", true);
}

// A misspelled claim is checked as it was meant, and the same claim in other
// words gets its saved check. Say which, and show what was typed, so the
// reader can tell the answer is to their question. Mirrors display.typed_note;
// a test runs both on the same inputs.
// The note in three parts: the words before the reader's text, their text,
// and the words after it. display.typed_note_parts is the same.
function typedNoteParts(typed, reason) {
  if (!typed) return null;
  if (reason === "wording") return ["Showing the saved check for this claim. You typed \u201c", String(typed), "\u201d."];
  return ["Spelling fixed. You typed \u201c", String(typed), "\u201d."];
}
function typedNote(typed, reason) {
  return (typedNoteParts(typed, reason) || []).join("");
}

// The reader's text goes in <bdi>, as text, so right-to-left words cannot
// reorder the sentence around it and markup stays inert.
function showTyped(typed, reason) {
  const note = $("typed-note");
  const parts = typedNoteParts(typed, reason);
  note.hidden = !parts;
  note.textContent = "";
  if (!parts) return;
  const bdi = document.createElement("bdi");
  bdi.textContent = parts[1];
  note.append(parts[0], bdi, parts[2]);
}

function logLine(text, live) {
  const log = $("reading");
  log.querySelector(".cancel-row")?.remove();
  log.querySelectorAll("li.live").forEach((li) => li.classList.remove("live"));
  const li = document.createElement("li");
  li.textContent = text;
  if (live) li.classList.add("live");
  log.appendChild(li);
  const c = document.createElement("li");
  c.className = "cancel-row";
  c.innerHTML = `<button type="button" class="link-btn" id="cancel-live">Cancel</button>`;
  log.appendChild(c);
  c.querySelector("button").addEventListener("click", () => { if (controller) controller.abort(); });
}

function endPending() {
  document.querySelector(".ticket").classList.remove("pending");
  resultSection.classList.remove("checking");
  const log = $("reading"); log.hidden = true; log.innerHTML = "";
  $("feedback").hidden = false;
  document.querySelector(".evidence").hidden = false;
}

// What the reader is told while the screen reader's live region says it too.
function announce(text) { const el = $("announce"); if (el) el.textContent = text || ""; }

let countdownTimer = null;

// Four ways a check can fail, told apart only by what actually happened: the
// status the server sent, or no answer at all. Each has a title that names the
// problem; the line under it says what to do, and is the server's own words
// when it sent some. A check that found no studies is not here: it is an
// answer, and the result screen says so.
const ERROR_KINDS = {
  invalid: { title: "That claim can't be checked", retry: false, back: "Edit claim" },
  network: { title: "Couldn't reach the server", retry: true, back: "Check another claim" },
  limit: { title: "Too many checks right now", retry: true, back: "Check another claim" },
  provider: { title: "The checker couldn't finish this check", retry: true, back: "Check another claim" },
};
// 400: the server read the claim and refused it. 429: a limit, ours or the
// provider's. Anything else the server sent (500, 502): the checker failed.
// A per-day total, not a record of anyone (see /privacy): the request is a
// name from a short fixed list and carries nothing else. A browser that sends
// Global Privacy Control is not counted, as a courtesy.
function countEvent(name) {
  try {
    if (navigator.globalPrivacyControl) return;
    fetch("/api/count", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }), keepalive: true }).catch(() => {});
  } catch { /* a count is never worth an error */ }
}
function errorKindOf(status) {
  if (status === 400) return "invalid";
  if (status === 429) return "limit";
  return "provider";
}
// The ways out beside the message. The kind sets them, and the server's
// way_out can narrow them: "reword" when only other words can help (a reply
// the model declined is declined again), so Edit claim and no Retry;
// "either" when a retry may or may not help, so Retry and Edit claim.
function errorActions(kind, wayOut) {
  const k = ERROR_KINDS[kind] || ERROR_KINDS.provider;
  if (wayOut === "reword") return { retry: false, back: "Edit claim" };
  if (wayOut === "either") return { retry: true, back: "Edit claim" };
  return { retry: k.retry, back: k.back };
}
// The draft stays in the field on every failure. Retry and Edit both start
// from it, so nothing has to be typed again.
function renderError(kind, message, retryAfter, wayOut) {
  // Same night ground, same way out. Only what the screen says changes.
  const k = ERROR_KINDS[kind] || ERROR_KINDS.provider;
  countEvent("client_error." + (ERROR_KINDS[kind] ? kind : "provider"));
  screenState.settle("error");
  $("error-title").textContent = k.title;
  $("error-msg").textContent = message;
  announce(`${k.title}. ${message}`);
  if (lastClaim && !input.value.trim()) input.value = lastClaim;
  // The field itself is on the hidden checker screen, so the draft is shown
  // here too: the reader sees what Retry will send.
  $("error-claim").textContent = lastClaim || input.value.trim();
  $("error-claim").hidden = !$("error-claim").textContent;
  const ways = errorActions(kind, wayOut);
  $("error-back").textContent = ways.back;
  const cd = $("countdown");
  const back = $("error-retry");
  back.hidden = !(ways.retry && lastClaim);
  clearInterval(countdownTimer);
  if (retryAfter && retryAfter > 0) {
    let left = retryAfter;
    back.disabled = true;
    cd.hidden = false;
    const tick = () => {
      cd.textContent = left > 0 ? `You can try again in ${left}s` : "You can try again now";
      if (left <= 0) { back.disabled = false; clearInterval(countdownTimer); }
      left -= 1;
    };
    tick();
    countdownTimer = setInterval(tick, 1000);
  } else {
    cd.hidden = true;
    back.disabled = false;
  }
  show(errorSection);
}

// ---- Checking (streamed, with a plain-JSON fallback) -----------------------

let inFlight = false;
let controller = null;
let lastClaim = "";

// A check can be slow for good reasons: a cold start on the free host can hold
// the first byte for a minute, and PubMed or the model can take their time.
// After SLOW_MS of silence the page says it is still working, in words, with
// no new motion. After GIVE_UP_MS of silence it stops and offers a retry.
const SLOW_MS = 12000;
const GIVE_UP_MS = 90000;
let slowTimer = null, giveUpTimer = null, gaveUp = false;
const SLOW_TEXT = "Still working. The first check after a quiet spell can take up to a minute.";
function clearSlow() {
  clearTimeout(slowTimer); clearTimeout(giveUpTimer);
  const old = $("reading").querySelector("li.slow");
  if (old) old.remove();
}
function armWatchdog() {
  clearSlow();
  slowTimer = setTimeout(() => {
    const log = $("reading");
    const row = log.querySelector(".cancel-row");
    const li = document.createElement("li");
    li.className = "slow";
    li.textContent = SLOW_TEXT;
    log.insertBefore(li, row);
    announce(SLOW_TEXT);
  }, SLOW_MS);
  giveUpTimer = setTimeout(() => { gaveUp = true; if (controller) controller.abort(); }, GIVE_UP_MS);
}

// One event, one line, one move of the footage. Short labels: the picture is
// carrying the story and the panel only has to name the step.
function handleStage(ev) {
  armWatchdog();
  if (ev.stage === "query" && ev.claim) {
    const typed = $("claim-echo").textContent;
    $("claim-echo").textContent = ev.claim;
    showTyped(typed, ev.typed_reason);
  }
  if (ev.stage === "query") logLine(ev.query ? `Searching PubMed for “${ev.query}”` : "Searching PubMed", true);
  else if (ev.stage === "found") {
    if (ev.count === 0) logLine("Nothing matched on PubMed", true);
    else { logLine(`Found ${ev.count} ${ev.count === 1 ? "study" : "studies"}${ev.broadened ? " after widening the search" : ""}`); logLine("Reading the studies", true); }
  } else if (ev.stage === "weigh") logLine("Weighing the evidence", true);
}

async function checkStreamed(claim) {
  const resp = await fetch("/api/check/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ claim }),
    signal: controller.signal,
  });
  if (!resp.ok || !resp.body) throw new Error("no stream");
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "", final = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.stage === "done" || ev.stage === "error") final = ev; else handleStage(ev);
    }
  }
  if (!final) throw new Error("stream ended early");
  return final;
}

async function checkPlain(claim) {
  const resp = await fetch("/api/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ claim }),
    signal: controller.signal,
  });
  let data = null;
  try { data = await resp.json(); } catch { /* non-JSON error page */ }
  if (!resp.ok || !data) return { stage: "error", status: resp.status, error: (data && data.error) || "Something went wrong. Try again in a moment.", retry_after: data && data.retry_after, way_out: data && data.way_out };
  return { stage: "done", result: data };
}

// The verdict lands where the work was printing, so the screen looks much as
// it did while checking, and a reader can take the verdict for the whole page.
// Once it has landed, the page moves down a little on its own: the claim and
// its verdict at the top, the short answer and the reasoning under them, so
// there is plainly more to read. Only after a check the reader ran here, once,
// and never against them: a scroll, tap or key first and it stays put. Under
// reduced motion it is a jump, not a glide. A tab in the background cannot
// glide, so a reader who looked away while it checked sees it on coming back.
const NUDGE_AFTER_MS = 900;
function nudgeToAnswer() {
  const head = document.querySelector("#result .thread");
  if (!head) return;
  let touched = false;
  const stop = () => { touched = true; };
  const inputs = ["wheel", "touchstart", "pointerdown", "keydown"];
  for (const ev of inputs) addEventListener(ev, stop, { passive: true });
  const go = () => {
    for (const ev of inputs) removeEventListener(ev, stop);
    if (touched || resultSection.hidden) return;
    const top = Math.round(head.getBoundingClientRect().top + scrollY - 16);
    if (top <= scrollY + 24) return;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    scrollTo({ top, behavior: still ? "auto" : "smooth" });
  };
  setTimeout(() => {
    if (document.hidden) document.addEventListener("visibilitychange", go, { once: true });
    else go();
  }, NUDGE_AFTER_MS);
}

// The empty-field message belongs to the field: it is announced, tied to the
// textarea with aria-describedby, and gone the moment the text is valid.
const claimErrorEl = $("claim-error");
const CLAIM_DESCRIBED_BY = input.getAttribute("aria-describedby") || "";
function showClaimError(text) {
  claimErrorEl.textContent = text;
  claimErrorEl.hidden = false;
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", ("claim-error " + CLAIM_DESCRIBED_BY).trim());
}
function clearClaimError() {
  if (claimErrorEl.hidden) return;
  claimErrorEl.hidden = true;
  claimErrorEl.textContent = "";
  input.removeAttribute("aria-invalid");
  input.setAttribute("aria-describedby", CLAIM_DESCRIBED_BY);
}

async function check(claim) {
  claim = (claim || "").trim();
  if (inFlight) return;
  if (!claim) { showClaimError("Type or paste the claim first."); input.focus(); return; }
  clearClaimError();
  if (!navigator.onLine) {
    const saved = loadRecent().find((r) => r.claim.toLowerCase() === claim.toLowerCase() && r.result);
    if (saved) { renderResult({ ...saved.result, restored: true }); return; }
    lastClaim = claim;
    renderError("network", "You're offline. Reconnect, then retry. Your claim is kept below.", 0);
    return;
  }

  inFlight = true;
  lastClaim = claim;
  gaveUp = false;
  controller = new AbortController();
  setWorking(true);
  renderPending(claim);
  armWatchdog();
  try {
    let final;
    try { final = await checkStreamed(claim); }
    catch (err) {
      if (err && err.name === "AbortError") throw err;
      final = await checkPlain(claim);
    }

    if (final.stage === "error") {
      endPending();
      renderError(errorKindOf(final.status), final.error, final.retry_after, final.way_out);
    } else {
      endPending();
      // The verdict arrived: the footage goes to its last frame and stays
      // there, which is the frame the panel is about to appear over.
      // The verdict arrived. A fault from here on is ours, not the
      // network's, and must not be reported as "couldn't reach the
      // server": that sends the reader to check their wifi over our bug.
      try {
        renderResult(final.result);
        remember(final.result);
        announce(`Verdict: ${VERDICT_LABELS[final.result.verdict] || final.result.verdict}. ${final.result.tldr || ""}`);
      } catch (err) {
        console.error("Rendering the verdict failed:", err);
        renderError("provider", "The verdict came back, but this page could not draw it. Retry to see it again.", 0);
        return;
      }
      // The address carries the claim as checked: a misspelled one comes
      // back fixed, and a reload or a copied link should not bring the
      // typo back.
      const asChecked = "/?q=" + encodeURIComponent(final.result.claim || claim);
      if (location.search.includes("q=")) history.replaceState({ view: "result" }, "", asChecked);
      else history.pushState({ view: "result" }, "", asChecked);
      nudgeToAnswer();
    }
  } catch (err) {
    endPending();
    if (err && err.name === "AbortError" && gaveUp) renderError("network", "This check is taking longer than it should. Nothing is wrong on your side. Retry in a moment.", 0);
    else if (err && err.name === "AbortError") { screenState.leave(); show(askSection); focusClaim(); }
    else renderError("network", "Check your connection, then retry. Your claim is kept below.", 0);
  } finally {
    clearSlow();
    inFlight = false;
    controller = null;
    setWorking(false);
  }
}

$("cancel-btn")?.addEventListener("click", () => { if (controller) controller.abort(); });

form.addEventListener("submit", (e) => { e.preventDefault(); check(input.value); });


// When an on-screen keyboard opens it shrinks the visual viewport. If that
// leaves the claim field under it, bring the field back to the middle.
if (window.visualViewport) {
  visualViewport.addEventListener("resize", () => {
    if (document.activeElement !== input) return;
    const r = input.getBoundingClientRect();
    if (r.bottom > visualViewport.height - 12 || r.top < 0) input.scrollIntoView({ block: "center" });
  });
}

const clearBtn = $("clear-btn");
input.addEventListener("input", () => { clearBtn.hidden = !input.value; if (input.value.trim()) clearClaimError(); askSuggest(); });
clearBtn.addEventListener("click", () => { input.value = ""; clearBtn.hidden = true; closeSuggest(); input.focus(); });

// ---- Type-ahead over claims already checked -------------------------------
// A cached claim answers instantly and costs nothing, so the best thing this
// can do is steer someone onto one. Literal hits and near misses are shown
// under separate headings: a corrected spelling is never presented as what
// the reader typed.

const suggestBox = $("suggest");
const suggestList = $("suggest-list");
const suggestHead = $("suggest-head");
const suggestLive = $("suggest-live");
let suggestRows = [];
let suggestAt = -1;
let suggestTimer = null;
let suggestSeq = 0;

function closeSuggest() {
  suggestBox.hidden = true;
  suggestList.innerHTML = "";
  suggestRows = [];
  suggestAt = -1;
  input.removeAttribute("aria-activedescendant");
}

function askSuggest() {
  clearTimeout(suggestTimer);
  const q = input.value.trim();
  if (q.length < 3) { closeSuggest(); return; }
  // Debounced: typing should not fire a request per keystroke.
  suggestTimer = setTimeout(async () => {
    const seq = ++suggestSeq;
    let rows = [];
    try {
      const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`);
      rows = (await res.json()).suggestions || [];
    } catch { rows = []; }
    if (seq !== suggestSeq) return;      // a later keystroke already won
    if (document.activeElement !== input) return;
    renderSuggest(rows);
  }, 160);
}

// A claim worded twice is one row. The other wording is named under it, so
// nothing is hidden, and the row opens the one saved answer.
function suggestAlso(r) {
  const more = r.similar || [];
  if (!more.length) return "";
  const rest = more.length > 1 ? ` and ${more.length - 1} more` : "";
  return `<span class="suggest-also">Also checked as: ${escapeHtml(more[0])}${rest}</span>`;
}

function renderSuggest(rows) {
  suggestRows = rows;
  suggestAt = -1;
  if (!rows.length) { closeSuggest(); return; }

  // The list is always the claims already checked. When none contains what
  // was typed, say these are the closest ones: a corrected spelling is never
  // passed off as the reader's own words.
  const anyLiteral = rows.some((r) => r.kind === "match");
  suggestHead.textContent = anyLiteral ? "Previously checked claims" : "Previously checked claims, closest to what you typed";

  suggestList.innerHTML = rows.map((r, i) => `
    <li class="suggest-row" id="suggest-${i}" role="option" aria-selected="false" data-i="${i}">
      <span class="suggest-claim">${escapeHtml(sentenceCase(r.claim_text))}${suggestAlso(r)}</span>
      <span class="suggest-verdict">${escapeHtml(VERDICT_LABELS[r.verdict] || r.verdict || "")}</span>
    </li>`).join("");

  suggestBox.hidden = false;
  suggestLive.textContent = `${rows.length} previously checked ${rows.length === 1 ? "claim" : "claims"}. Use the arrow keys to choose one, and Enter to open its saved answer.`;
}

function moveSuggest(step) {
  if (!suggestRows.length) return;
  const rowEls = [...suggestList.children];
  if (suggestAt >= 0) rowEls[suggestAt]?.setAttribute("aria-selected", "false");
  suggestAt = (suggestAt + step + rowEls.length) % rowEls.length;
  const el = rowEls[suggestAt];
  el.setAttribute("aria-selected", "true");
  el.scrollIntoView({ block: "nearest" });
  input.setAttribute("aria-activedescendant", el.id);
}

function takeSuggest(i) {
  const row = suggestRows[i];
  if (!row) return;
  input.value = row.claim_text;
  clearBtn.hidden = false;
  closeSuggest();
  check(row.claim_text);
}

suggestList.addEventListener("click", (e) => {
  const li = e.target.closest(".suggest-row");
  if (li) takeSuggest(Number(li.dataset.i));
});
input.addEventListener("blur", () => { setTimeout(closeSuggest, 120); });

// Enter submits; Shift+Enter makes a new line. When the list is open the
// arrow keys walk it and Escape closes it without clearing what was typed.
input.addEventListener("keydown", (e) => {
  const open = !suggestBox.hidden && suggestRows.length;
  if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault(); moveSuggest(e.key === "ArrowDown" ? 1 : -1); return;
  }
  if (open && e.key === "Escape") { e.preventDefault(); closeSuggest(); return; }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (open && suggestAt >= 0) { takeSuggest(suggestAt); return; }
    closeSuggest();
    form.requestSubmit();
  }
});

function goHome() {
  setTitle(null);
  // Out of the band screen first, so the fly-through goes back to being
  // scrolled and the page goes back to being a page before anything is shown.
  screenState.leave();
  show(askSection); maybeNudge();
  input.focus({ preventScroll: true }); input.select();
}
// The same control sits above the claim while a check is running, where it is
// the way out of one. It cancels the check it is interrupting rather than
// leaving it running unseen.
$("back-btn").addEventListener("click", () => {
  if (controller) { controller.abort(); return; }
  if (history.state && history.state.view === "result") history.back(); else { history.replaceState({ view: "ask" }, "", "/"); goHome(); }
});
// Edit returns to the claim box with the claim already in it, cursor at the
// end, to reword and check again. The suggestions stay shut: the reader has
// not typed anything yet.
$("edit-claim").addEventListener("click", () => {
  const claim = (currentResult && currentResult.claim) || $("claim-echo").textContent.trim();
  history.replaceState({ view: "ask" }, "", "/");
  goHome();
  input.value = claim;
  clearBtn.hidden = !claim;
  closeSuggest();
  input.focus({ preventScroll: true });
  input.setSelectionRange(claim.length, claim.length);
});
window.addEventListener("popstate", () => {
  if (history.state && history.state.view === "result" && currentResult) { renderResult(currentResult); return; }
  goHome();
});
$("error-retry").addEventListener("click", () => { if (lastClaim) { input.value = lastClaim; check(lastClaim); } });
$("error-back").addEventListener("click", () => {
  setTitle(null); screenState.leave(); show(askSection); focusClaim();
});

// ---- Examples & trending --------------------------------------------------

$("chips").innerHTML = EXAMPLES.slice(0, 3).map((c) => `<button type="button" class="chip">${escapeHtml(c)}</button>`).join("");
$("chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  input.value = chip.textContent;
  check(chip.textContent);
});

// A listed claim as every list shows it: the first letter raised only when the
// first word is all lower case letters (parts joined by a hyphen or an
// apostrophe, trailing punctuation aside). "mRNA", "pH", "DNA", "COVID-19", a
// number or a quotation mark first are left as stored. The page only: the
// data and links keep the saved wording. display.sentence_case is the same.
function sentenceCase(text) {
  text = String(text || "");
  const word = text.trim() ? text.trim().split(/\s+/)[0] : "";
  if (text.slice(0, 1) !== word.slice(0, 1)) return text;
  const parts = word.replace(/[.,;:!?]+$/, "").split(/[-'\u2019]/);
  if (parts.every((p) => /^\p{Ll}+$/u.test(p))) return text.slice(0, 1).toUpperCase() + text.slice(1);
  return text;
}

function claimListHtml(items) {
  return items.map((r) => `
    <li>
      <button type="button" class="recent-btn" data-claim="${escapeHtml(r.claim)}">
        <span>${escapeHtml(sentenceCase(r.claim))}</span>
        <span class="tag v-${escapeHtml(r.verdict)}">${escapeHtml(VERDICT_LABELS[r.verdict] || r.verdict)}</span>
      </button>
    </li>`).join("");
}

async function loadTrending() {
  try {
    const items = await (await fetch("/api/trending")).json();
    if (!Array.isArray(items) || items.length < 3) return;
    $("trending-list").innerHTML = claimListHtml(items.slice(0, 6));
    $("trending").hidden = false;
  } catch { /* offline or not deployed: nothing to add */ }
}

$("trending-list").addEventListener("click", (e) => {
  const btn = e.target.closest(".recent-btn");
  if (!btn) return;
  input.value = btn.dataset.claim;
  check(btn.dataset.claim);
});

// ---- Recent (on this device only; full results kept so it works offline) --

const RECENT_KEY = "cc.recent.v2";

function loadRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; }
}

function remember(data) {
  try {
    const list = loadRecent().filter((r) => r.claim.toLowerCase() !== data.claim.toLowerCase());
    list.unshift({ claim: data.claim, verdict: data.verdict, tldr: data.tldr, at: Date.now(), result: data });
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 10)));
  } catch { /* storage unavailable or full: fine */ }
  renderRecent();
}

let recentExpanded = false;
function renderRecent() {
  const list = loadRecent();
  $("recent").hidden = list.length === 0;
  const shown = recentExpanded ? list : list.slice(0, 5);
  $("recent-list").innerHTML = claimListHtml(shown);
  $("recent-more").hidden = list.length <= 5 || recentExpanded;
}
$("recent-more").addEventListener("click", () => { recentExpanded = true; renderRecent(); });

$("recent-list").addEventListener("click", (e) => {
  const btn = e.target.closest(".recent-btn");
  if (!btn) return;
  const saved = loadRecent().find((r) => r.claim === btn.dataset.claim);
  // A result saved by an older version can lack its short answer or its
  // still-open line. It is not replayed as a finished verdict: it runs again.
  const whole = (x) => typeof x === "string" && x.trim() !== "";
  if (saved && saved.result && whole(saved.result.tldr) && whole(saved.result.still_open)) {
    renderResult({ ...saved.result, restored: true });
    history.replaceState(null, "", "/?q=" + encodeURIComponent(saved.claim));
  } else {
    input.value = btn.dataset.claim;
    check(btn.dataset.claim);
  }
});

$("clear-recent").addEventListener("click", () => {
  try { localStorage.removeItem(RECENT_KEY); } catch {}
  renderRecent();
});

// ---- Share: an image card when the phone can take one, else text ----------

async function copyText(str) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(str); return true; }
  } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = str; ta.setAttribute("readonly", "");
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch { return false; }
}

// The one line under every verdict: on the page (templates/index.html), in the
// shared text and on the shared image. Mirrored in og.py for the link preview.
const DOCTOR_LINE = "Check important health decisions with a doctor.";

const CARD = {
  deep: "#0f1211", deepLine: "#2a302d", onDeep: "#eceee9", onDeep2: "#9ea6a1", onDeep3: "#7d8580", sage: "#9fc3b0",
  paper: "#f1f2ee", ink: "#121514", ink2: "#434a46", ink3: "#5d6560", rule: "#d9dcd5", wood: "#2b2622",
  cloth: { strong: "#35594c", human: "#3a4e6b", lab: "#5a4760", weak: "#7e4f3d", retracted: "#434a46" },
  // each cloth at 18% on the paper, mixed in oklab as style.css mixes it: the
  // wash inside a book that was read but not used
  wash: { strong: "#cdd5cf", human: "#cdd2d6", lab: "#d4d1d3", weak: "#dcd3cc", retracted: "#cfd1cd" },
};

function wrapLines(ctx, text, maxWidth, maxLines) {
  const words = text.split(/\s+/), lines = [];
  let line = "";
  for (const w of words) {
    const trial = (line + " " + w).trim();
    if (ctx.measureText(trial).width <= maxWidth) line = trial;
    else { if (line) lines.push(line); line = w; }
    if (lines.length === maxLines) break;
  }
  if (lines.length < maxLines && line) lines.push(line);
  if (lines.length === maxLines && words.join(" ") !== lines.join(" ")) {
    let last = lines[maxLines - 1];
    while (ctx.measureText(last + "…").width > maxWidth && last.includes(" ")) last = last.slice(0, last.lastIndexOf(" "));
    lines[maxLines - 1] = last + "…";
  }
  return lines;
}

// The pen ring, the same shape as the one on the page, stretched to a box.
const RING = "M58 9 C 150 0, 292 8, 296 40 S 210 80, 140 79 S 2 70, 4 41 S 70 4, 168 6";
function ringPath(x, y, w, h) {
  let i = 0;
  return new Path2D(RING.replace(/\d+(\.\d+)?/g,
    (n) => (i++ % 2 ? y + (n / 80) * h : x + (n / 300) * w).toFixed(1)));
}

// The shared card is the page in one square: the claim and its ringed verdict
// on the night ground, joined by the thread, then by day the short answer,
// what is still open and the shelf it was read from.
async function renderShareCard(r) {
  const S = 1080, pad = 76, inset = pad + 30;
  const c = document.createElement("canvas"); c.width = S; c.height = S;
  const ctx = c.getContext("2d");
  const DISPLAY = '"Bricolage Grotesque", system-ui, sans-serif';
  const BODY = '"Instrument Sans", system-ui, sans-serif';
  const MONO = '"IBM Plex Mono", ui-monospace, monospace';
  try {
    await Promise.all([`350 96px ${DISPLAY}`, `500 36px ${DISPLAY}`, `400 40px ${BODY}`,
                       `500 20px ${MONO}`].map((f) => document.fonts.load(f)));
  } catch {}
  ctx.textBaseline = "alphabetic";
  const knot = (y) => { ctx.fillStyle = CARD.sage; ctx.beginPath(); ctx.arc(pad + 4, y, 5, 0, Math.PI * 2); ctx.fill(); };

  // Night: the masthead, the claim, the verdict.
  const n = r.studies.length, cited = r.studies.filter((s) => s.cited_in_verdict).length;
  ctx.font = `400 40px ${BODY}`;
  const claimLines = wrapLines(ctx, `\u201c${r.claim.trim()}\u201d`, S - inset - pad, 3);
  const label = VERDICT_LABELS[r.verdict] || r.verdict;
  let vSize = 104;
  ctx.font = `350 ${vSize}px ${DISPLAY}`;
  while (vSize > 64 && ctx.measureText(label).width > S - inset - pad - 40) {
    vSize -= 4; ctx.font = `350 ${vSize}px ${DISPLAY}`;
  }
  const claimTop = 196, verdictLabelY = claimTop + 34 + claimLines.length * 54 + 30;
  const verdictY = verdictLabelY + 34 + vSize * 0.9;
  // The doctor line stands just under the ring's foot (the ring ends 0.3 of
  // the verdict's size below its baseline), and the night runs on to hold it.
  const doctorY = verdictY + vSize * 0.3 + 34;
  const nightH = doctorY + 26;

  ctx.fillStyle = CARD.deep; ctx.fillRect(0, 0, S, nightH);
  ctx.fillStyle = CARD.onDeep; ctx.font = `500 36px ${DISPLAY}`;
  ctx.fillText("Evident", pad, 92);
  ctx.font = `500 20px ${MONO}`; ctx.fillStyle = CARD.onDeep3;
  const basis = n ? `${n} read, ${cited} relied on` : "No matching studies";
  ctx.fillText(basis, S - pad - ctx.measureText(basis).width, 88);

  // the thread, from the claim's knot down to the verdict's
  ctx.fillStyle = CARD.deepLine; ctx.fillRect(pad + 3.5, claimTop - 6, 1.5, verdictLabelY - claimTop);
  knot(claimTop - 6); knot(verdictLabelY - 6);
  ctx.font = `500 20px ${MONO}`; ctx.fillStyle = CARD.onDeep3;
  ctx.fillText("The claim you checked", inset, claimTop);
  ctx.fillText("Verdict", inset, verdictLabelY);
  ctx.font = `400 40px ${BODY}`; ctx.fillStyle = CARD.onDeep;
  let y = claimTop + 34 + 40;
  for (const line of claimLines) { ctx.fillText(line, inset, y); y += 54; }

  ctx.font = `350 ${vSize}px ${DISPLAY}`; ctx.fillStyle = CARD.onDeep;
  ctx.fillText(label, inset, verdictY);
  const vw = ctx.measureText(label).width;
  ctx.strokeStyle = CARD.sage; ctx.lineWidth = 2.5; ctx.lineCap = "round";
  ctx.stroke(ringPath(inset - 22, verdictY - vSize * 0.92, vw + 44, vSize * 1.22));
  ctx.font = `400 26px ${BODY}`; ctx.fillStyle = CARD.onDeep2;
  ctx.fillText(DOCTOR_LINE, inset, doctorY);

  // Day: the short answer, what is still open, the shelf.
  ctx.fillStyle = CARD.paper; ctx.fillRect(0, nightH, S, S - nightH);
  y = nightH + 52;
  if (r.tldr) {
    ctx.font = `400 32px ${BODY}`; ctx.fillStyle = CARD.ink;
    for (const line of wrapLines(ctx, r.tldr, S - pad * 2, 3)) { ctx.fillText(line, pad, y); y += 44; }
  }
  if (r.still_open && y < S - 250) {
    y += 14;
    ctx.fillStyle = CARD.ink; ctx.fillRect(pad, y - 22, S - pad * 2, 1.5);
    y += 16;
    ctx.font = `500 18px ${MONO}`; ctx.fillStyle = CARD.ink3;
    ctx.fillText("Still open", pad, y);
    y += 36;
    ctx.font = `400 26px ${BODY}`; ctx.fillStyle = CARD.ink2;
    const room = Math.max(1, Math.min(2, Math.floor((S - 190 - y) / 36) + 1));
    for (const line of wrapLines(ctx, r.still_open, S - pad * 2, room)) { ctx.fillText(line, pad, y); y += 36; }
  }

  // a mini shelf of the studies read, as in Latest checks
  const books = r.studies.map((s) => ({ kind: kindOf(s), tier: s.tier || "moderate", used: !!s.cited_in_verdict }))
    .sort((a, b) => Object.keys(CARD.cloth).indexOf(a.kind) - Object.keys(CARD.cloth).indexOf(b.kind));
  const shelfBase = S - 112, H = { strong: 56, moderate: 40, weak: 25, retracted: 25 };
  // as in Latest checks: solid cloth for the books the verdict used, outlined
  // in cloth on a pale wash for the ones read but not used
  books.forEach((b, i) => {
    const x = pad + 4 + i * 17, h = H[b.tier], cloth = CARD.cloth[b.kind] || CARD.ink3;
    if (b.used) {
      ctx.fillStyle = cloth;
      ctx.fillRect(x, shelfBase - h, 13, h);
    } else {
      ctx.fillStyle = CARD.wash[b.kind] || CARD.rule;
      ctx.fillRect(x, shelfBase - h, 13, h);
      ctx.strokeStyle = cloth; ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, shelfBase - h + 1, 11, h);
    }
  });
  if (books.length) { ctx.fillStyle = CARD.wood; ctx.fillRect(pad, shelfBase, books.length * 17 + 4, 4); }

  // colophon
  ctx.font = `500 18px ${MONO}`; ctx.fillStyle = CARD.ink3;
  ctx.fillText("Not medical advice", pad, S - 52);
  const host = (() => { try { return new URL(r.share_url || location.href).host; } catch { return location.host; } })();
  ctx.fillText(host, S - pad - ctx.measureText(host).width, S - 52);

  return new Promise((resolve) => c.toBlob((b) => resolve(b), "image/png"));
}

function shareText(r) {
  // Each sentence ends on its own full stop, so the doctor line never runs on
  // from the last word of what is still open.
  const end = (t) => (/[.!?]$/.test(t) ? t : `${t}.`);
  const still = r.still_open ? ` Still open: ${end(r.still_open.trim())}` : "";
  return `“${r.claim}”: ${VERDICT_LABELS[r.verdict] || r.verdict}. ${end((r.tldr || r.explanation || "").trim())}${still} ${DOCTOR_LINE}`;
}

async function shareResult() {
  if (!currentResult) return;
  countEvent("share.click");
  const r = currentResult;
  const url = r.share_url || (location.origin + "/?q=" + encodeURIComponent(r.claim));
  const text = shareText(r);
  const toast = $("toast");
  try {
    if (navigator.share) {
      let files;
      try {
        const blob = await renderShareCard(r);
        const file = new File([blob], "evident-check.png", { type: "image/png" });
        if (navigator.canShare && navigator.canShare({ files: [file] })) files = [file];
      } catch { /* card failed: share text */ }
      await navigator.share(files ? { files, title: "Evident", text: `${text}\n${url}` } : { title: "Evident", text, url });
      toast.textContent = "Shared";
      setTimeout(() => { toast.textContent = ""; }, 2000);
      return;
    }
    // Desktop: download the card and copy the text.
    const blob = await renderShareCard(r);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "claim-check.png"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast.textContent = (await copyText(`${text}\n${url}`)) ? "Card saved, text copied" : "Card saved";
  } catch (err) {
    if (err && err.name === "AbortError") return; // user closed the share sheet
    toast.textContent = "Couldn't share. The link is in your address bar.";
  }
  setTimeout(() => { toast.textContent = ""; }, 3000);
}

async function copyLink() {
  if (!currentResult) return;
  countEvent("share.copy");
  const url = currentResult.share_url || location.href;
  $("toast").textContent = (await copyText(url)) ? "Link copied" : "Couldn't copy. The link is in your address bar.";
  setTimeout(() => { $("toast").textContent = ""; }, 2500);
}

for (const id of ["share-btn", "share-btn-sticky"]) $(id).addEventListener("click", shareResult);
for (const id of ["copy-btn", "copy-btn-sticky"]) $(id).addEventListener("click", copyLink);

// ---- Feedback -----------------------------------------------------------------

const FB_KEY = "cc.feedback.v1";
function feedbackGiven() { try { return JSON.parse(localStorage.getItem(FB_KEY) || "{}"); } catch { return {}; } }

function renderFeedbackState(claim) {
  const done = feedbackGiven()[claim.toLowerCase()];
  $("feedback").querySelectorAll(".thumb").forEach((b) => { b.hidden = !!done; });
  $("feedback-q").textContent = done ? "Thanks. This helps improve the checker." : "Was this verdict helpful?";
}

$("feedback").addEventListener("click", async (e) => {
  const b = e.target.closest(".thumb");
  if (!b || !currentResult) return;
  const helpful = b.dataset.helpful === "1";
  try {
    const g = feedbackGiven(); g[currentResult.claim.toLowerCase()] = helpful ? "up" : "down";
    localStorage.setItem(FB_KEY, JSON.stringify(g));
  } catch {}
  renderFeedbackState(currentResult.claim);
  try {
    await fetch("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim: currentResult.claim, verdict: currentResult.verdict, helpful }) });
  } catch { /* best effort */ }
});

// ---- Report a wrong answer ----------------------------------------------------
// Anonymous like a thumb: the claim, the verdict shown and an optional short
// note go to /api/feedback, and nothing that identifies the sender.

const reportOpen = $("report-open");
const reportForm = $("report-form");
const reportDone = $("report-done");

function resetReport() {
  reportForm.hidden = true;
  reportDone.hidden = true;
  reportDone.textContent = "";
  reportOpen.hidden = false;
  reportOpen.setAttribute("aria-expanded", "false");
  $("report-note").value = "";
  $("report-send").disabled = false;
}

reportOpen.addEventListener("click", () => {
  reportForm.hidden = false;
  reportOpen.hidden = true;
  reportOpen.setAttribute("aria-expanded", "true");
  $("report-note").focus();
});
$("report-cancel").addEventListener("click", () => { resetReport(); reportOpen.focus(); });
reportForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentResult) return;
  const send = $("report-send");
  send.disabled = true;
  let ok = false;
  try {
    const res = await fetch("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim: currentResult.claim, verdict: currentResult.verdict, report: true,
                             note: $("report-note").value.trim() }) });
    ok = res.ok;
  } catch { ok = false; }
  reportDone.hidden = false;
  if (ok) {
    reportForm.hidden = true;
    reportDone.textContent = "Thanks. Your report was sent, with no name or address attached.";
  } else {
    send.disabled = false;
    reportDone.textContent = "The report did not send. Check your connection and send it again.";
  }
});

// ---- Study sheet (the deep dive) -------------------------------------------

const studySheet = $("study-sheet");

// Layer 3. One request per study, cached for the session so reopening a
// sheet is instant. Every failure path still leaves the reader the abstract.
const diveCache = new Map();
let diveToken = 0;

async function loadDeepDive(pmid) {
  const wrap = $("sheet-dive");
  const status = $("dive-status");
  const figs = $("dive-figs");
  const links = $("dive-links");
  const relWrap = $("dive-related-wrap");
  if (!wrap) return;

  // A later sheet must win, however slow an earlier request turns out to be.
  const token = ++diveToken;
  figs.hidden = true; links.hidden = true; relWrap.hidden = true;
  status.hidden = false;
  status.textContent = "Looking it up on PubMed.";

  let dive = diveCache.get(pmid);
  if (!dive) {
    try {
      const res = await fetch(`/api/study/${encodeURIComponent(pmid)}`);
      dive = await res.json();
      diveCache.set(pmid, dive);
    } catch {
      dive = { cited_by: null, full_text_url: null, related: [], partial: true,
               note: "Could not reach PubMed just now." };
    }
  }
  if (token !== diveToken) return; // a different study is open now

  const figures = [];
  if (typeof dive.cited_by === "number") {
    figures.push(["Cited by", `${dive.cited_by} ${dive.cited_by === 1 ? "paper" : "papers"}`]);
  }
  if (dive.full_text_url) figures.push(["Full text", "Free"]);

  if (figures.length) {
    figs.innerHTML = figures
      .map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`)
      .join("");
    figs.hidden = false;
  }

  if (dive.full_text_url) {
    links.innerHTML = `<a href="${escapeHtml(dive.full_text_url)}" target="_blank" rel="noopener">Read the whole paper free<svg width="16" height="16" aria-hidden="true"><use href="#i-ext"/></svg></a>`;
    links.hidden = false;
  }

  const related = dive.related || [];
  if (related.length) {
    $("dive-related").innerHTML = related.map((r) => `<li class="study">
      <p class="meta"><span>${escapeHtml(typeInfo(r.publication_types).label)}</span>${r.year ? `<span>${escapeHtml(r.year)}</span>` : ""}</p>
      <p class="title"><span>${escapeHtml(r.title)}</span></p>
      <p class="src">${escapeHtml(r.journal || "")}${r.journal ? " · " : ""}<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">PMID ${escapeHtml(r.pmid)}</a></p>
    </li>`).join("");
    relWrap.hidden = false;
  }

  // Say plainly when this is incomplete rather than implying a paper has
  // no citations when PubMed was simply unreachable.
  if (dive.note) {
    status.textContent = dive.note;
  } else if (dive.partial) {
    status.textContent = "Some of this could not be loaded. The abstract above is unaffected.";
  } else if (!figures.length && !related.length) {
    status.textContent = "Nothing cites this one yet, and PubMed lists nothing next to it.";
  } else {
    status.hidden = true;
  }
}

function openStudy(i) {
  const s = currentResult && currentResult.studies[i];
  if (!s) return;
  const ti = typeInfo(s.publication_types);
  $("sheet-meta").innerHTML = `${escapeHtml(ti.label)}${s.year ? " · " + escapeHtml(s.year) : ""}${s.cited_in_verdict ? " · <span style='color:var(--ink)'>used for the verdict</span>" : ""}${s.off_topic ? " · off topic: " + escapeHtml(s.off_topic) : ""}`;
  $("sheet-title").textContent = s.title;
  $("sheet-src").textContent = [s.journal, `PMID ${s.pmid}`].filter(Boolean).join(" · ");
  const tc = $("sheet-type");
  tc.className = `type-card ${ti.cls}`;
  tc.innerHTML = `<b>${escapeHtml(ti.label)}</b>${escapeHtml(ti.what)}`;

  const abs = $("sheet-abstract");
  if (s.abstract) {
    abs.innerHTML = s.abstract.split("\n").map((para) => {
      const m = para.match(/^([A-Z][A-Za-z /&-]{2,40}):\s(.*)$/s);
      return m ? `<p><b>${escapeHtml(m[1])}</b>${escapeHtml(m[2])}</p>` : `<p>${escapeHtml(para)}</p>`;
    }).join("");
  } else {
    abs.innerHTML = `<p>No abstract is available for this one. Open it on PubMed for details.</p>`;
  }

  $("sheet-data-wrap").hidden = !s.data_banks;
  $("sheet-data").textContent = s.data_banks ? `This study is registered with ${s.data_banks}, which usually means its protocol and sometimes its data are public.` : "";

  const authors = s.authors || [];
  $("sheet-authors-wrap").hidden = authors.length === 0;
  $("sheet-authors").textContent = authors.length > 6 ? `${authors.slice(0, 6).join(", ")} and ${authors.length - 6} more` : authors.join(", ");

  $("sheet-link").href = s.url;
  loadDeepDive(s.pmid);
  openSheet(studySheet, $("sheet-title"));
}

// showModal() focuses the first button, which scrolls a long sheet to the
// bottom. Open at the top with focus on the heading instead.
function openSheet(dialog, heading) {
  dialog.showModal();
  dialog.scrollTop = 0;
  heading.setAttribute("tabindex", "-1");
  heading.focus({ preventScroll: true });
}

$("sources").addEventListener("click", (e) => {
  if (e.target.closest("[data-stop]")) return; // the PMID link itself
  const li = e.target.closest(".study");
  if (li) openStudy(Number(li.dataset.i));
});
// A study number is a tap target wherever it appears: in the reasoning under
// the panel, and in every sentence of the breakdown under that.
for (const id of ["says", "deeper-body"]) {
  $(id)?.addEventListener("click", (e) => {
    const ref = e.target.closest(".ref");
    if (ref) openStudy(Number(ref.dataset.i));
  });
}

// A bar goes to its study. Enter and space come free with the button; the
// arrows walk the row the way they walk the source column below it.
$("chart-bars")?.addEventListener("click", (e) => {
  const col = e.target.closest(".barcol");
  if (col) showStudy(Number(col.dataset.i));
});
$("chart-bars")?.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const cols = [...$("chart-bars").querySelectorAll(".barcol")];
  const here = cols.indexOf(e.target.closest(".barcol"));
  if (here === -1) return;
  const next = cols[here + (e.key === "ArrowRight" ? 1 : -1)];
  if (!next) return;
  e.preventDefault();
  next.focus();
});

// Arrow keys walk the source column, the way a finger runs down a printed one.
$("sources").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const items = [...$("sources").querySelectorAll(".study")];
  const here = items.indexOf(e.target.closest(".study"));
  if (here === -1) return;
  const next = items[here + (e.key === "ArrowDown" ? 1 : -1)];
  if (!next) return;
  e.preventDefault();
  next.focus();
});

// "/" returns to the claim box from anywhere, for the second check and the third.
window.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector("dialog.sheet[open]")) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  e.preventDefault();
  if (resultSection.hidden && errorSection.hidden) { input.focus(); input.select(); }
  else { history.replaceState({ view: "ask" }, "", "/"); goHome(); }
});

// Sheets: close buttons, tap on the backdrop, and the meter opens "how it works".
for (const d of document.querySelectorAll("dialog.sheet")) {
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  d.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => d.close()));
  // Drag the handle (or the top of the sheet) down to dismiss. Only while the
  // sheet is a bottom sheet: wider than that it is centred with a transform of
  // its own, and an inline translateY would knock it out of the middle.
  const bottomSheet = window.matchMedia("(max-width: 719px)");
  let y0 = null;
  d.addEventListener("touchstart", (e) => {
    y0 = bottomSheet.matches && d.scrollTop === 0 ? e.touches[0].clientY : null;
    if (y0 !== null) d.classList.add("dragging");
  }, { passive: true });
  d.addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    const dy = e.touches[0].clientY - y0;
    if (dy > 0) d.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  d.addEventListener("touchend", (e) => {
    if (y0 === null) return;
    const dy = e.changedTouches[0].clientY - y0;
    d.classList.remove("dragging");
    d.style.transform = "";
    if (dy > 90) d.close();
    y0 = null;
  });
}
$("how-btn").addEventListener("click", () => openSheet($("how-sheet"), $("how-title")));
$("assess").addEventListener("click", (e) => { if (e.target.closest("#meter")) openSheet($("how-sheet"), $("how-title")); });

// ---- Voice input (where the browser has it) --------------------------------

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const mic = $("mic-btn");
  mic.hidden = false;
  let rec = null;
  mic.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new SR();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    mic.classList.add("listening");
    rec.onresult = (e) => { input.value = Array.from(e.results).map((r) => r[0].transcript).join(""); };
    rec.onend = () => { mic.classList.remove("listening"); rec = null; if (input.value.trim()) input.focus(); };
    rec.onerror = () => { mic.classList.remove("listening"); rec = null; };
    rec.start();
  });
}

// ---- Install nudge + iOS hint ------------------------------------------------

let deferredInstall = null;
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const NUDGE_KEY = "cc.nudge.dismissed";

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstall = e;
  $("install-btn").hidden = false;
  $("nudge-install").hidden = false;
});

async function promptInstall() {
  if (!deferredInstall) return;
  deferredInstall.prompt();
  await deferredInstall.userChoice;
  deferredInstall = null;
  $("install-btn").hidden = true;
  $("nudge").hidden = true;
}
$("install-btn").addEventListener("click", promptInstall);
$("nudge-install").addEventListener("click", promptInstall);
$("nudge-dismiss").addEventListener("click", () => { try { localStorage.setItem(NUDGE_KEY, "1"); } catch {} $("nudge").hidden = true; });

function maybeNudge() {
  let dismissed = false;
  try { dismissed = !!localStorage.getItem(NUDGE_KEY); } catch {}
  if (standalone || dismissed || loadRecent().length === 0) return;
  $("nudge-body").textContent = isIOS
    ? "Tap the Share button in Safari, then “Add to Home Screen.” It opens like an app and remembers your checks."
    : deferredInstall
      ? "One tap installs it. It opens like an app and remembers your checks."
      : "In your browser menu, choose “Add to Home Screen” or “Install app.”";
  $("nudge").hidden = false;
}

// ---- Deep links: /?q=… from us, /?text=… from the Android share sheet --------

const params = new URLSearchParams(location.search);
const shared = params.get("q") || params.get("text") || params.get("title") || "";
const claimFromLink = shared.replace(/https?:\/\/\S+/g, "").replace(/\s+/g, " ").trim().slice(0, 500);

// A shared link arrives with its verdict already in the page, so there is
// nothing to fetch and nothing to wait for: adopt it and carry on.
const serverResult = (() => {
  const el = $("server-result");
  if (!el) return null;
  try { return JSON.parse(el.textContent); } catch { return null; }
})();

if (serverResult) {
  document.body.classList.remove("view-ask");
  renderResult(serverResult);
  remember(serverResult);
  input.value = serverResult.claim;
} else if (claimFromLink) { input.value = claimFromLink; check(claimFromLink); }
else if (params.get("focus")) input.focus();

renderRecent();
loadTrending();
maybeNudge();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}
