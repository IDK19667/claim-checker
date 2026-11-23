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

// The one escaper for text written through innerHTML. It encodes the quotes
// too, so a value inside an attribute cannot end it: a saved claim such as
// `is "x" style="..."` stays one attribute value. Where the code can set
// textContent or setAttribute instead, it does; that is the better way.
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function escapeHtml(str) {
  return (str == null ? "" : String(str)).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

// The one gate for an address taken from result data into an href: it passes
// only an http or https address, or a path on this site, and anything else
// (javascript:, data:, a protocol-relative //host) comes back as null so the
// link is left out.
function safeHref(url) {
  const raw = String(url == null ? "" : url).trim();
  // A space or a control character inside is refused: the browser drops a tab or a newline and reads what is left.
  if (!raw || /[\u0000-\u0020\u007f]/.test(raw)) return null;
  if (raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\")) return raw;
  // Only the plain form, http:// or https:// and then the host, which both sides read the same way.
  if (!/^https?:\/\/[^/\\?#]/i.test(raw)) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch { return null; }
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

// One page, three views. On a result <html> carries `checked`, and the desk's
// stops after the first are shown; at home and on an error only stop 1 is.
// The claim box, the error and the home panel are all in stop 1.
function show(section) {
  const result = section === resultSection;
  root.classList.toggle("checked", result);
  // A result keeps the claim box: it is stop 1, the claim as typed, ready to edit.
  askSection.hidden = !(section === askSection || result);
  errorSection.hidden = section !== errorSection;
  askMore.hidden = section !== askSection;
  if (trustSection) trustSection.hidden = section !== askSection;
  document.body.classList.toggle("view-ask", section === askSection);
  if (!result) tellDesk("desk:home", {});
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

// A result saved on this device before the shelf existed carries no cloth:
// its tier stands in, so an old check still draws a whole shelf.
const KIND_FROM_TIER = { strong: "strong", weak: "weak", retracted: "retracted" };
const kindOf = (s) => s.kind || KIND_FROM_TIER[s.tier] || "human";
const KIND_LABELS = { strong: "Strong design", human: "Human study", lab: "Animal or lab",
                      weak: "Opinion or single case", retracted: "Retracted" };

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
  const exactUrl = safeHref(ns.exact_url), widerUrl = safeHref(ns.wider_url);
  if (exactUrl) {
    acts.push(`<li>See the exact search on PubMed: <a href="${escapeHtml(exactUrl)}" target="_blank" rel="noopener"><code>${escapeHtml(ns.searched)}</code><svg width="15" height="15" aria-hidden="true"><use href="#i-ext"/></svg></a></li>`);
  }
  if (widerUrl) {
    acts.push(`<li>Search any of these terms instead of all of them: <a href="${escapeHtml(widerUrl)}" target="_blank" rel="noopener">${escapeHtml((ns.terms || []).slice(0, 4).join(", "))}<svg width="15" height="15" aria-hidden="true"><use href="#i-ext"/></svg></a></li>`);
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

// ---- The desk front's result stops ------------------------------------------
// Mirrors templates/index.html exactly: a shared link is server rendered and a
// live check is rendered here, and the two must be the same page. Every line
// below is worded from the record's own study list (display.desk_stops,
// desk_lines, desk_named, desk_refs and rests_by_design are the same; a test
// runs both on the same records). Nothing is computed from the prose: every
// sentence has already been through the gate in breakdown.py.

const VERDICT_SETS = { relied: "Relied on", back: "Background only, not counted", read: "Read, not relied on", off: "Set aside: off topic" };
const pad2 = (n) => String(n).padStart(2, "0");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
function joinAnd(items) {
  const xs = items.filter(Boolean);
  if (xs.length <= 1) return xs[0] || "";
  return xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1];
}
// The lines of "What the research says": the breakdown's, or the explanation.
function saysLines(data) {
  const bv = breakdownView(data);
  return (bv ? bv.says : [data.explanation]).filter((t) => String(t || "").trim());
}
// The stops this result shows, by name. A stop with nothing real to show is
// left out: Weighed needs a study, Research a study and something written.
function deskStops(data) {
  const out = ["Claim", "Answer", "Read"];
  if ((data.studies || []).length) {
    out.push("Weighed");
    if (breakdownView(data) || saysLines(data).length) out.push("Research");
  }
  out.push("Open");
  return out;
}
function deskLines(data) {
  const studies = data.studies || [];
  const g = sourceGroups(studies);
  const n = studies.length, r = g.cited.length, b = g.back.length, o = g.others.length, x = g.excluded.length;
  if (!n) return { read: "No studies read.", path: "", legend: "", below: "Below: the search that ran, and what is still open.", rests: "" };
  const path = joinAnd([r ? `${r} relied on` : "None relied on", b ? `${b} background only` : "",
                        o ? `${o} read and not relied on` : "", x ? `${x} set aside as off topic` : ""]) + ".";
  const rest = [];
  if (o + b) rest.push(`the ${o + b} read but not relied on` + (r ? " at its edge" : ""));
  if (x) rest.push(`the ${x} off topic face down`);
  const legend = r
    ? `In the light: the ${plural(r, "paper", "papers")} the answer relies on` + (rest.length ? ", with " + joinAnd(rest) : "") + "."
    : "In the light: " + joinAnd(rest) + ". None was relied on.";
  const below = "Below: what was read, the " + plural(n, "study", "studies")
    + (deskStops(data).includes("Research") ? ", the full answer," : ",") + " and what is still open.";
  const more = [];
  if (o + b) more.push(`${o + b} more ${o + b === 1 ? "was" : "were"} read and not relied on`);
  if (x) more.push(`${x} ${x === 1 ? "was" : "were"} set aside as off topic`);
  const tail = joinAnd(more);
  const rests = `Rests on ${plural(r, "study", "studies")}.` + (tail ? " " + tail.slice(0, 1).toUpperCase() + tail.slice(1) + "." : "");
  return { read: plural(n, "study", "studies") + " read.", path, legend, below, rests };
}
function restsByDesign(studies) {
  const order = [], by = {};
  (studies || []).forEach((s, i) => {
    if (!s.cited_in_verdict || s.off_topic) return;
    const d = typeInfo(s.publication_types).label;
    if (!by[d]) { by[d] = []; order.push(d); }
    by[d].push(i + 1);
  });
  return order.map((d) => {
    const ns = by[d];
    let label = d.toLowerCase();
    if (ns.length > 1) {
      if (/analysis$/.test(label)) label = label.replace(/analysis$/, "analyses");
      else if (/(review|trial|article|study)$/.test(label)) label = label.endsWith("study") ? label.replace(/study$/, "studies") : label + "s";
      else if (!label.endsWith("y")) label += "s";
    }
    if (d === DEFAULT_TYPE[0]) label += ", design not labelled";
    return { count: ns.length, label, refs: (ns.length > 1 ? "Studies " : "Study ") + ns.join(", ") };
  });
}
// "Study 4", "Studies 1, 4 and 6": each number a link to its row in Weighed.
const REF_RE = /\b(Stud(?:y|ies))\s+(\d+(?:\s*(?:,\s*and|,|and|&amp;)\s*\d+)*)/gi;
function deskRefs(text, count) {
  return escapeHtml(text).replace(REF_RE, (m, word, nums) => word + " " + nums.replace(/\d+/g, (d) => {
    const i = Number(d);
    return i >= 1 && i <= count
      ? `<a class="ref" href="#row-${i}" data-n="${i}" aria-label="Study ${i}, show it in the list">${i}</a>` : d;
  }));
}
function deskNamed(data) {
  const n = (data.studies || []).length, out = new Set();
  for (const line of saysLines(data)) {
    for (const m of escapeHtml(line).matchAll(REF_RE)) {
      for (const d of m[2].match(/\d+/g)) { const i = Number(d); if (i >= 1 && i <= n) out.add(i); }
    }
  }
  return [...out].sort((a, b) => a - b);
}

const backBtn = '<button class="back" type="button" data-go="1">Back to the short answer</button>';
const eyebrow = (stops, name, text) =>
  `<p class="eyebrow"${name === "Open" ? ' id="h-open"' : ""}><b>${pad2(stops.indexOf(name) + 1)}</b>${escapeHtml(text)}</p>`;

function studyRowHtml(s, i, key, named) {
  const n = i + 1, d = typeInfo(s.publication_types).label;
  const title = String(s.title || "");
  const href = safeHref(s.url);
  const src = (s.journal ? `${escapeHtml(s.journal)} · ` : "") + (s.year ? `${escapeHtml(s.year)} · ` : "")
    + (href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" aria-label="Study ${n} on PubMed, opens in a new tab">PubMed ${escapeHtml(s.pmid)}</a>` : `PubMed ${escapeHtml(s.pmid)}`);
  return `<li class="row" id="row-${n}" data-n="${n}" data-i="${i}">`
    + `<button class="row-head" type="button" aria-pressed="false" aria-label="Study ${n}, ${escapeHtml(d)}: ${escapeHtml(title.replace(/\n/g, " ").replace(/^\.+|\.+$/g, ""))}. Lift its sheet">`
    + `<span class="num">${n}</span><span class="design">${escapeHtml(d)}</span><span class="ttl">${escapeHtml(title)}</span></button>`
    + `<div class="body"><p class="src">${src}</p>`
    + `<button class="row-more" type="button" aria-expanded="false" aria-controls="more-${n}" aria-label="Details for study ${n}">Details</button>`
    + `<div class="more" id="more-${n}" hidden><p class="status${key !== "relied" ? " no" : ""}">${VERDICT_SETS[key]}</p>`
    + (named.includes(n) ? '<p class="named">The answer names it</p>' : "")
    + `<p class="note why">${escapeHtml(studyWhy(s))}</p>`
    + `<button type="button" class="row-more study-go" aria-label="Deep dive on study ${n}">Deep dive</button></div></div></li>`;
}

// Stop 4, Weighed: every study as a row, in its group; the first group open.
function weighedHtml(data) {
  const studies = data.studies || [];
  if (!studies.length) return "";
  const stops = deskStops(data), g = sourceGroups(studies), named = deskNamed(data);
  const groups = [
    ["relied", "Relied on for the verdict", g.cited],
    ["back", (data.evidence && data.evidence.background_label) || "Background only: animal and lab studies", g.back],
    ["read", g.cited.length ? "Read, not relied on" : "Read", g.others],
    ["off", "Set aside: off topic", g.excluded],
  ].filter((x) => x[2].length);
  const rows = groups.map(([key, title, idx], gi) =>
    `<h3 class="grp-h"><button class="grp-btn" type="button" aria-label="${escapeHtml(title)} (${idx.length})" aria-controls="grp-${key}" aria-expanded="${gi === 0}">${escapeHtml(title)} (${idx.length})</button></h3>`
    + `<ul class="rows" id="grp-${key}"${gi ? " hidden" : ""}>${idx.map((i) => studyRowHtml(studies[i], i, key, named)).join("")}</ul>`).join("");
  return `<section class="stop long" id="s4" data-stop="Weighed" aria-labelledby="h-sort"><div class="cap left"><div class="inner wide">`
    + `<div class="panel list" id="list-panel" tabindex="-1">${backBtn}${eyebrow(stops, "Weighed", "Weighed, not counted")}`
    + `<h2 id="h-sort">Which studies the answer rests on</h2>`
    + `<p class="intro">Pick a study to lift its sheet. Tap a sheet on the desk to find it here. Details open its abstract, how often it has been cited and the studies next to it.</p>`
    + `<div id="rows">${rows}</div></div></div></div></section>`;
}

// Stop 5, Research: the thorough answer. The longer parts fold away, closed.
function researchHtml(data) {
  const stops = deskStops(data);
  if (!stops.includes("Research")) return "";
  const studies = data.studies || [], n = studies.length, bv = breakdownView(data);
  const p = (cls, text) => `<p class="${cls}">${deskRefs(text, n)}</p>`;
  let h = `<div class="says" id="says-sec">${saysLines(data).map((t) => p("says-p", t)).join("")}</div>`;
  const whoYes = !!(bv && (bv.applies_to || bv.populations.length));
  if (bv && (bv.applies_to || bv.not_applies_to || bv.populations.length)) {
    h += `<div class="who" id="who-sec"><h3 id="who-label">${!whoYes && bv.not_applies_to ? "Who this does not apply to" : "Who and what this applies to"}</h3>`
      + (bv.applies_to ? p("says-p", bv.applies_to) : "")
      + (bv.populations.length ? `<p class="narrow">Some of this evidence comes from studies of one group only: ${escapeHtml(bv.populations.join(", "))}. A result found in one group may not hold for everyone.</p>` : "")
      + (bv.not_applies_to ? (whoYes ? '<p class="intro label-2">And who it does not</p>' : "") + p("says-p", bv.not_applies_to) : "")
      + "</div>";
  }
  if (bv) {
    if (bv.parts.length) {
      h += `<details class="said-wrap" data-fold><summary>The claim, part by part (${bv.parts.length})</summary>`
        + bv.parts.map((x) => `<p class="intro part-claim">${escapeHtml(x.part)}</p>${p("part-take", x.assessment)}`).join("") + "</details>";
    }
    const pts = bv.evidence.length + (bv.effect_size ? 1 : 0);
    if (pts) {
      h += `<details class="said-wrap" data-fold><summary>What the evidence shows (${pts} ${pts === 1 ? "point" : "points"})</summary>`
        + `<ul class="ev">${bv.evidence.map((x) => `<li>${deskRefs(x, n)}</li>`).join("")}</ul>`
        + (bv.effect_size ? `<p class="effect"><b>How big the effect is.</b> <span>${deskRefs(bv.effect_size, n)}</span></p>` : "")
        + "</details>";
    }
  }
  const ev = String(data.evidence_line || "").replace(/^\.+|\.+$/g, "");
  h += `<h3>How sure, and why</h3><p class="evline">${escapeHtml(data.evidence_line ? ev + "." : confidenceLine(data))}</p>`;
  if (bv && bv.strength) h += p("strength", bv.strength);
  if (bv && bv.unknowns) h += p("unknowns", bv.unknowns);
  if (bv && bv.concluded.length) {
    h += `<details class="said-wrap"><summary>What each study concluded (${bv.concluded.length})</summary><ol class="said">`
      + bv.concluded.map((c) => {
        const st = studies[c.n - 1] || {};
        return `<li><p class="who">Study <a class="ref" href="#row-${Number(c.n)}" data-n="${Number(c.n)}" aria-label="Study ${Number(c.n)}, show it in the list">${Number(c.n)}</a>`
          + (st.type_label ? ` · ${escapeHtml(st.type_label)}` : "") + (st.year ? ` · ${escapeHtml(st.year)}` : "")
          + `</p><blockquote>${escapeHtml(c.said)}</blockquote></li>`;
      }).join("")
      + `</ol><p class="fine">In the authors' own words, from the summary each study was published with.</p></details>`;
  }
  h += `<h3>What the verdict rests on</h3><p class="rests-line">${escapeHtml(deskLines(data).rests)}</p>`;
  const rb = restsByDesign(studies);
  if (rb.length) h += `<ul class="rests">${rb.map((d) => `<li><b>${d.count}</b><span>${deskRefs(`${d.label} (${d.refs})`, n)}</span></li>`).join("")}</ul>`;
  if (bv) h += '<p class="fine">Every sentence written for this breakdown names the studies it rests on. Figures are copied from the abstracts and rounded for reading, never worked out from them.</p>';
  return `<section class="stop long" id="s5" data-stop="Research" aria-labelledby="h-research"><div class="cap left"><div class="inner wider">`
    + `<div class="panel read" id="read-panel" tabindex="-1">${backBtn}${eyebrow(stops, "Research", "The thorough answer")}`
    + `<h2 id="h-research">What the research says</h2><div id="research">${h}</div></div></div></div></section>`;
}

// The desk (static/desk.js) listens for these: a result to lay out, the way
// home, and the one glide to the answer after a check the reader just ran.
function tellDesk(name, detail) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

// `replay` is the same result shown again by Back or Forward: the desk keeps the
// place the browser restores and never glides to the answer a second time.
function renderResult(data, replay) {
  dropOffTopicCites(data);
  currentResult = data;
  const n = data.studies.length, stops = deskStops(data), dl = deskLines(data);
  // Stop 1 keeps the claim in its box, so Back from the answer is the claim.
  input.value = data.claim;
  clearBtn.hidden = !input.value;
  // Stop 2: the card and how sure.
  $("claim-echo").textContent = data.claim;
  $("stamp").textContent = VERDICT_LABELS[data.verdict] || data.verdict;
  $("tldr").textContent = data.tldr || "";
  showTyped(data.typed, data.typed_reason);
  $("confidence").textContent = confidenceLine(data);
  $("doctor-line").hidden = false;
  $("below").textContent = dl.below;
  resultSection.dataset.verdict = data.verdict;
  if (userTapped && navigator.vibrate && !matchMedia("(prefers-reduced-motion: reduce)").matches) navigator.vibrate(25);
  // Stop 3: what was read.
  $("h-read").textContent = dl.read;
  $("path").textContent = dl.path;
  $("legend3").textContent = dl.legend;
  $("legend3").hidden = !dl.legend;
  const none = n === 0;
  $("nothing").hidden = !none;
  $("no-evidence").hidden = !none;
  $("nothing").querySelectorAll(":scope > p.scope").forEach((x) => x.remove());
  if (none) {
    $("nothing").insertAdjacentHTML("afterbegin", saysLines(data).map((t) => `<p class="scope">${escapeHtml(t)}</p>`).join(""));
  }
  const usedQuery = (data.search_query_used || "").trim();
  $("nothing").querySelector(".query-line").innerHTML = none && usedQuery ? `Searched PubMed for <code>${escapeHtml(usedQuery)}</code>` : "";
  renderNextSteps(none ? data.next_steps : null, data.claim);
  // D2: on the no-studies stop the explanation above already says nothing matched. The block's actions and close
  // claims follow it without the block's own heading and paragraph, and the template's line (for scripts off)
  // stands only when the block is not there.
  const steps = none && !$("next-steps").hidden;
  $("nosteps-head").hidden = $("nosteps-why").hidden = steps;
  if (steps) $("no-evidence").hidden = true;
  // Stops 4 and 5 are there only when they have something real to show.
  $("s4")?.remove();
  $("s5")?.remove();
  $("s6").insertAdjacentHTML("beforebegin", weighedHtml(data) + researchHtml(data));
  // Stop 6: what is still open, the search, and the ways on.
  $("h-open").innerHTML = `<b>${pad2(stops.indexOf("Open") + 1)}</b>Still open`;
  $("still-open").hidden = !data.still_open;
  $("still-open-text").textContent = data.still_open || "";
  $("query").textContent = usedQuery;
  $("query-what").hidden = !usedQuery;
  $("broadened").textContent = data.search_broadened ? " (the first search found nothing, so it was widened)" : "";
  $("more-link").href = safeHref(data.pubmed_url) || "#";
  $("review-line").textContent = data.review_line || "";
  $("review-line").hidden = !data.review_line;
  const note = $("cached-note");
  note.hidden = !(data.cached || data.restored);
  note.textContent = data.restored
    ? "From your history on this device."
    : !hasDate(data.cached_at) ? "Result from an earlier check. Claims are re-checked after a day."
    : hasDate(data.first_checked_at)
      ? `First checked on ${fmtDate(data.first_checked_at)}. Last re-checked on ${fmtDate(data.cached_at)}. Claims are re-checked after a day.`
      : `Result from a check on ${fmtDate(data.cached_at)}. Claims are re-checked after a day.`;
  $("toast").textContent = "";
  renderFeedbackState(data.claim);
  resetReport();
  setTitle(data);
  screenState.settle("result");
  show(resultSection);
  tellDesk("desk:result", { data, fresh: !replay && !data.cached && !data.restored, replay: !!replay });
}

// While a check runs the claim stays in its box and the work prints under it,
// in stop 1. Cached results skip this and land directly.
function renderPending(claim) {
  // A check started from a result's claim box goes back to stop 1 on its own:
  // the old answer's stops go, and the desk clears for the new sheets.
  if (root.classList.contains("checked")) show(askSection);
  currentResult = null;
  const log = $("reading");
  log.innerHTML = ""; log.hidden = false;
  errorSection.hidden = true;
  askMore.hidden = true;
  if (trustSection) trustSection.hidden = true;
  $("doctor-line").hidden = true;
  screenState.enter();
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
  const log = $("reading"); log.hidden = true; log.innerHTML = "";
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
    // The desk stands one blank sheet in its stack for each paper found.
    tellDesk("desk:stage", { stage: "found", count: ev.count || 0 });
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
    // A list answering after Enter never opens over the check it started.
    if (inFlight) return;
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
for (const b of document.querySelectorAll("[data-again]")) b.addEventListener("click", () => {
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
  if (history.state && history.state.view === "result" && currentResult) { renderResult(currentResult, true); return; }
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

$("share-btn").addEventListener("click", shareResult);
$("copy-btn").addEventListener("click", copyLink);

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

  if (safeHref(dive.full_text_url)) {
    links.innerHTML = `<a href="${escapeHtml(safeHref(dive.full_text_url))}" target="_blank" rel="noopener">Read the whole paper free<svg width="16" height="16" aria-hidden="true"><use href="#i-ext"/></svg></a>`;
    links.hidden = false;
  }

  const related = dive.related || [];
  if (related.length) {
    $("dive-related").innerHTML = related.map((r) => `<li class="study">
      <p class="meta"><span>${escapeHtml(typeInfo(r.publication_types).label)}</span>${r.year ? `<span>${escapeHtml(r.year)}</span>` : ""}</p>
      <p class="title"><span>${escapeHtml(r.title)}</span></p>
      <p class="src">${escapeHtml(r.journal || "")}${r.journal ? " · " : ""}${safeHref(r.url) ? `<a href="${escapeHtml(safeHref(r.url))}" target="_blank" rel="noopener">PMID ${escapeHtml(r.pmid)}</a>` : `PMID ${escapeHtml(r.pmid)}`}</p>
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

  $("sheet-link").href = safeHref(s.url) || "https://pubmed.ncbi.nlm.nih.gov/";
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

// Deep dive under a study's details opens its sheet. The rest of the row (lift,
// details, the group buttons, the study numbers in the answer) is the desk's.
$("track").addEventListener("click", (e) => {
  const go = e.target.closest(".study-go");
  if (go) openStudy(Number(go.closest(".row").dataset.i));
});

// "/" returns to the claim box from anywhere, for the second check and the third.
window.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector("dialog.sheet[open]")) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  e.preventDefault();
  if (!root.classList.contains("checked") && errorSection.hidden) { input.focus(); input.select(); }
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
// How it works is a link to /method, so it works with no script at all; with
// one it opens the same words in a sheet over the page instead.
for (const id of ["how-btn", "how-btn-2"]) {
  $(id)?.addEventListener("click", (e) => { e.preventDefault(); openSheet($("how-sheet"), $("how-title")); });
}
$("meter").addEventListener("click", () => openSheet($("how-sheet"), $("how-title")));

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
