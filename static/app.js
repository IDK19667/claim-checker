/* Claim Checker front end. No framework, no build step. */

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
};

// Claims people actually paste in. Tapping one runs it.
const EXAMPLES = [
  "Apple cider vinegar cures diabetes",
  "5G weakens your immune system",
  "Vaccines cause autism",
  "Detox teas flush toxins out of your body",
  "Cracking your knuckles causes arthritis",
  "Intermittent fasting improves brain function",
  "Ozempic cures alcoholism",
];

// ---- Evidence types: what they mean, how much they weigh ------------------

// The first matching label wins, so the badge shows the most informative type.
const TYPES = [
  // strong
  ["Meta-Analysis", "strong", "Pools the results of many studies into one bigger answer. About as strong as evidence gets."],
  ["Network Meta-Analysis", "strong", "Compares several treatments at once by pooling many studies. Very strong."],
  ["Systematic Review", "strong", "A structured review of every study on a question, with rules for what counts. Very strong."],
  ["Randomized Controlled Trial", "strong", "People were randomly assigned to get the thing or not, then compared. The gold standard for “does X cause Y.”"],
  ["Practice Guideline", "strong", "Official recommendations from a medical body, based on the evidence it reviewed."],
  // weak
  ["Retracted Publication", "retracted", "Withdrawn by the journal after publication. Its findings shouldn't be trusted."],
  ["Case Reports", "weak", "The story of one patient, or a few. Interesting, but proves nothing on its own."],
  ["Editorial", "weak", "An opinion piece by the journal's editors, not a study."],
  ["Comment", "weak", "A published comment on another paper, not a study."],
  ["Letter", "weak", "A letter to the journal, not a study."],
  ["News", "weak", "A news item, not a study."],
  // medium
  ["Controlled Clinical Trial", "", "Tested on people against a comparison group, but not randomized. Good, with caveats."],
  ["Clinical Trial", "", "Tested on people, but not necessarily randomized or compared. Good, with caveats."],
  ["Observational Study", "", "Watched what happened to people who did or didn't do X. Can show links, not cause."],
  ["Multicenter Study", "", "Run at several hospitals or sites, which makes results more general."],
  ["Comparative Study", "", "Compared two or more approaches. How well depends on the design."],
  ["Review", "", "An expert's summary of the research. Useful context, but it depends on which studies they picked."],
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

// Deterministic. Based only on the kinds of studies the verdict cited,
// not on what the model thinks of them.
function evidenceLevel(studies) {
  const cited = studies.filter((s) => s.cited_in_verdict);
  if (!cited.length) return { level: 0, label: "No direct evidence", note: "None of the studies found actually test this claim, so it can't be rated true or false." };
  const kinds = cited.map((s) => typeInfo(s.publication_types).cls);
  if (kinds.includes("strong")) return { level: 3, label: "Strong", note: "Rests on a meta-analysis, systematic review, randomized trial, or official guideline." };
  if (kinds.some((k) => k === "")) return { level: 2, label: "Moderate", note: "Human studies or expert reviews, but no randomized trial or meta-analysis behind it." };
  return { level: 1, label: "Weak", note: "Only case reports, letters, opinion pieces, or a retracted paper." };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// ---- Views -----------------------------------------------------------------

const askMore = $("ask-more");
// The tab title follows the view, so history entries and open tabs are
// tellable apart. The server sets the result title; this keeps it honest
// when the view changes without a page load.
const DEFAULT_TITLE = "Claim Checker: check a health claim against real research";
function setTitle(result) {
  document.title = result
    ? `${VERDICT_LABELS[result.verdict] || result.verdict}: \u201c${result.claim}\u201d \u00b7 Claim Checker`
    : DEFAULT_TITLE;
}
function show(section) {
  for (const s of [askSection, resultSection, errorSection]) s.hidden = s !== section;
  askMore.hidden = section !== askSection;
  document.body.classList.toggle("view-ask", section === askSection);
  if (section !== resultSection) { $("bar-sticky").hidden = true; document.body.classList.remove("has-bar"); }
  window.scrollTo(0, 0);
}
if (!document.getElementById("server-result")) document.body.classList.add("view-ask");

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

// What was read and relied on, as one sentence. The evidence word is
// highlighted, the way you'd mark the line that matters on a printout.
function recordSentence(n, cited, ev, best) {
  const what = `<button type="button" class="what link-btn" id="meter">what does this mean?</button>`;
  if (!n) return `Found nothing on PubMed that matched well enough to weigh. <mark>No direct evidence</mark> either way. ${what}`;
  const read = `Read ${n} ${n === 1 ? "study" : "studies"} from PubMed`;
  if (!cited || ev.level === 0) return `${read}, but none of them actually tests this claim, so there's <mark>no direct evidence</mark> either way. ${what}`;
  const ti = best ? typeInfo(best.publication_types) : null;
  const strongest = ti && ti.label !== DEFAULT_TYPE[0] ? ` The strongest is a ${ti.label.toLowerCase()}, so` : " So";
  return `${read} and relied on ${cited}.${strongest} the evidence is <mark>${ev.label.toLowerCase()}</mark>. ${what}`;
}

function fmtDate(iso) {
  try { return new Date(iso || Date.now()).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); }
  catch { return ""; }
}

// Mirrors the snapshot markup in templates/index.html. The numbers are
// computed server-side in evidence.py so a streamed check and a cached page
// can never disagree about what was read.
function renderSnapshot(ev) {
  const wrap = $("snapshot");
  if (!wrap) return;
  if (!ev || !ev.read) { wrap.hidden = true; return; }
  wrap.hidden = false;

  const mix = ev.mix || [];
  const bar = $("snapshot-bar");
  bar.innerHTML = mix.map((m) => `<i class="seg seg-${m.tier}" style="width:${m.percent}%"></i>`).join("");
  bar.setAttribute("aria-label", mix.map((m) => `${m.count} ${m.label}`).join(", "));

  const figs = [["Studies read", ev.read], ["Relied on for this verdict", ev.relied_on]];
  if (ev.pooled) figs.push(["Pooled analyses", ev.pooled]);
  if (ev.trials) figs.push(["Trials", ev.trials]);
  if (ev.registered) figs.push(["Registered", ev.registered]);
  if (ev.retracted) figs.push(["Retracted", ev.retracted, "fig-warn"]);
  if (ev.year_from) {
    figs.push(["Published between", ev.year_from === ev.year_to ? `${ev.year_from}` : `${ev.year_from} to ${ev.year_to}`, "fig-span"]);
  }
  $("snapshot-figs").innerHTML = figs
    .map(([k, v, cls]) => `<div${cls ? ` class="${cls}"` : ""}><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`)
    .join("");

  $("snapshot-key").innerHTML = mix
    .map((m) => `<span class="key-item"><i class="seg seg-${m.tier}"></i>${m.count} ${escapeHtml(m.label)}</span>`)
    .join("");
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
          <span class="entry-claim">${escapeHtml(r.claim_text)}</span>
          <span class="entry-verdict">${escapeHtml(VERDICT_LABELS[r.verdict] || r.verdict || "")}</span>
        </a>
      </li>`).join("");
  }
}

function renderResult(data) {
  currentResult = data;
  $("claim-echo").textContent = data.claim;
  $("case-line").textContent = `PubMed · ${fmtDate(data.cached_at)}`;

  const block = document.querySelector(".verdict-block");
  block.dataset.verdict = data.verdict;

  // Re-trigger the stamp animation on every new result.
  const stamp = $("stamp");
  const fresh = stamp.cloneNode(false);
  fresh.textContent = VERDICT_LABELS[data.verdict] || data.verdict;
  fresh.style.setProperty("--tilt", ({ true: "-5deg", false: "-7deg", complicated: "-4deg" })[data.verdict] || "-6deg");
  stamp.replaceWith(fresh);
  if (userTapped && navigator.vibrate && !matchMedia("(prefers-reduced-motion: reduce)").matches) navigator.vibrate(25);

  $("tldr").textContent = data.tldr || "";
  $("explanation").innerHTML = linkStudyRefs(data.explanation, data.studies.length);
  $("still-open").hidden = !data.still_open;
  $("still-open-text").textContent = data.still_open || "";

  const n = data.studies.length;
  const cited = data.studies.filter((s) => s.cited_in_verdict).length;
  const ev = evidenceLevel(data.studies);
  $("record").innerHTML = recordSentence(n, cited, ev, strongestCited(data.studies));

  const note = $("cached-note");
  note.hidden = !(data.cached || data.restored);
  note.textContent = data.restored
    ? "From your history on this device."
    : `Result from a check on ${fmtDate(data.cached_at)}. Claims are re-checked after a day.`;

  renderSnapshot(data.evidence);
  renderNextSteps(data.next_steps, data.claim);
  const sub = $("panel-sub");
  if (sub) sub.textContent = n ? `${n} stud${n === 1 ? "y" : "ies"} read from PubMed` : "Nothing matched on PubMed";

  // The evidence field: one mark per study, height by tier, filled when the
  // verdict leaned on it. The tier is classified once on the server.
  const field = $("field"), cap = $("field-cap");
  if (field && cap) {
    field.hidden = cap.hidden = !n;
    if (n) {
      field.innerHTML = data.studies
        .map((s) => `<i class="fb fb-${escapeHtml(s.tier || "moderate")}${s.cited_in_verdict ? " fb-used" : ""}"></i>`)
        .join("");
      field.setAttribute("aria-label", `${n} stud${n === 1 ? "y" : "ies"} read, ${cited} relied on`);
      cap.innerHTML = `<span>${n} read</span><span>${cited} relied on</span>`;
    }
  }

  $("evidence-label").textContent = n ? `Studies checked (${n})` : "Studies checked";
  const studyRow = (s, i) => {
    const ti = typeInfo(s.publication_types);
    const src = [s.journal, s.year].filter(Boolean).join(", ");
    const isDefault = ti.label === DEFAULT_TYPE[0];
    return `
      <li class="study${s.cited_in_verdict ? " cited" : ""}" data-i="${i}" tabindex="0" role="button" aria-label="Deep dive on study ${i + 1}">
        <p class="meta">
          <span class="ord">${String(i + 1).padStart(2, "0")}</span>
          ${isDefault ? "" : `<span class="type ${ti.cls}">${escapeHtml(ti.label)}</span>`}
          ${s.year ? `<span>${escapeHtml(s.year)}</span>` : ""}
          ${s.cited_in_verdict ? `<span class="sr-only">Used for the verdict</span>` : ""}
        </p>
        <p class="title"><span>${escapeHtml(s.title)}</span></p>
        <p class="src">${escapeHtml(src)}${src ? " · " : ""}<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener" data-stop>PMID ${escapeHtml(s.pmid)}</a></p>
        <span class="study-go" aria-hidden="true">Deep dive<svg width="15" height="15"><use href="#i-dive"/></svg></span>
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
  const citedRows = [], otherRows = [];
  data.studies.forEach((s, i) => (s.cited_in_verdict ? citedRows : otherRows).push(studyRow(s, i)));
  $("sources").innerHTML = n
    ? group("Relied on for the verdict", citedRows, true) +
      group(citedRows.length ? "Read, not relied on" : "Read", otherRows, citedRows.length === 0)
    : `<ol class="studies"><li class="empty">Nothing on PubMed matched this well enough to weigh. That doesn't make the claim true or false: it may just not have been studied yet.</li></ol>`;

  const list = $("sources");
  list.classList.remove("sweep");
  if (!data.cached && !data.restored) {
    list.querySelectorAll(".study.cited").forEach((li, i) => li.style.setProperty("--i", i));
    requestAnimationFrame(() => list.classList.add("sweep"));
  }
  $("query").textContent = data.search_query_used || "";
  $("broadened").textContent = data.search_broadened ? " (the first search found nothing, so it was widened)" : "";
  $("more-link").href = data.pubmed_url || "#";
  $("toast").textContent = "";
  renderFeedbackState(data.claim);

  $("bar-sticky").hidden = false;
  document.body.classList.add("has-bar");
  setTitle(data);
  show(resultSection);
}

// While a check runs, the claim is already on the ticket and the archive's
// work prints beneath it. Cached results skip this and land directly.
function renderPending(claim) {
  currentResult = null;
  $("claim-echo").textContent = claim;
  $("case-line").textContent = "PubMed · checking";
  const t = document.querySelector(".ticket");
  t.classList.add("pending");
  resultSection.classList.add("checking");
  const f = $("field"), fc = $("field-cap");
  if (f) { f.hidden = true; f.innerHTML = ""; }
  if (fc) { fc.hidden = true; fc.innerHTML = ""; }
  document.querySelector(".verdict-block").dataset.verdict = "";
  const log = $("reading");
  log.innerHTML = ""; log.hidden = false;
  $("feedback").hidden = true;
  document.querySelector(".evidence").hidden = true;
  $("cached-note").hidden = true;
  $("still-open").hidden = true;
  $("bar-sticky").hidden = true; document.body.classList.remove("has-bar");
  for (const sec of [askSection, errorSection]) sec.hidden = true;
  askMore.hidden = true;
  resultSection.hidden = false;
  document.body.classList.remove("view-ask");
  window.scrollTo(0, 0);
  logLine("Turning the claim into a PubMed search", true);
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

let countdownTimer = null;
function renderError(message, retryAfter) {
  $("error-msg").textContent = message;
  const cd = $("countdown");
  const back = $("error-back");
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

function handleStage(ev) {
  if (ev.stage === "query") logLine(`Searching PubMed for “${ev.query}”`, true);
  else if (ev.stage === "found") {
    if (ev.count === 0) logLine("Nothing matched on PubMed", true);
    else { logLine(`Found ${ev.count} ${ev.count === 1 ? "study" : "studies"}${ev.broadened ? " after widening the search" : ""}`); logLine("Reading the abstracts", true); }
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
  if (!resp.ok || !data) return { stage: "error", error: (data && data.error) || "Something went wrong. Try again in a moment.", retry_after: data && data.retry_after };
  return { stage: "done", result: data };
}

async function check(claim) {
  claim = (claim || "").trim();
  if (inFlight) return;
  if (!claim) { setStatus("Type or paste the claim first.", false); input.focus(); return; }
  if (!navigator.onLine) {
    const saved = loadRecent().find((r) => r.claim.toLowerCase() === claim.toLowerCase() && r.result);
    if (saved) { renderResult({ ...saved.result, restored: true }); return; }
    renderError("You're offline. Checking a claim needs a connection.");
    return;
  }

  inFlight = true;
  controller = new AbortController();
  setWorking(true);
  renderPending(claim);
  try {
    let final;
    try { final = await checkStreamed(claim); }
    catch (err) {
      if (err && err.name === "AbortError") throw err;
      final = await checkPlain(claim);
    }

    if (final.stage === "error") {
      endPending();
      renderError(final.error, final.retry_after);
    } else {
      endPending();
      // The verdict arrived. A fault from here on is ours, not the
      // network's, and must not be reported as "couldn't reach the
      // server": that sends the reader to check their wifi over our bug.
      try {
        renderResult(final.result);
        remember(final.result);
      } catch (err) {
        console.error("Rendering the verdict failed:", err);
        renderError("The verdict came back, but this page could not draw it. Reload and try again.");
        return;
      }
      if (location.search.includes("q=")) history.replaceState({ view: "result" }, "", "/?q=" + encodeURIComponent(claim));
      else history.pushState({ view: "result" }, "", "/?q=" + encodeURIComponent(claim));
    }
  } catch (err) {
    endPending();
    if (err && err.name === "AbortError") { show(askSection); input.focus(); }
    else renderError("Couldn't reach the server. Check your connection and try again.");
  } finally {
    inFlight = false;
    controller = null;
    setWorking(false);
  }
}

$("cancel-btn")?.addEventListener("click", () => { if (controller) controller.abort(); });

form.addEventListener("submit", (e) => { e.preventDefault(); check(input.value); });

const clearBtn = $("clear-btn");
input.addEventListener("input", () => { clearBtn.hidden = !input.value; askSuggest(); });
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
  input.setAttribute("aria-expanded", "false");
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

function renderSuggest(rows) {
  suggestRows = rows;
  suggestAt = -1;
  if (!rows.length) { closeSuggest(); return; }

  const anyLiteral = rows.some((r) => r.kind === "match");
  suggestHead.textContent = anyLiteral ? "Already checked" : "Did you mean";

  suggestList.innerHTML = rows.map((r, i) => `
    <li class="suggest-row" id="suggest-${i}" role="option" aria-selected="false" data-i="${i}">
      <span class="suggest-claim">${escapeHtml(r.claim_text)}</span>
      <span class="suggest-verdict">${escapeHtml(VERDICT_LABELS[r.verdict] || r.verdict || "")}</span>
    </li>`).join("");

  suggestBox.hidden = false;
  input.setAttribute("aria-expanded", "true");
  suggestLive.textContent = `${rows.length} ${rows.length === 1 ? "claim" : "claims"} already checked. Use the arrow keys to choose one.`;
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
  show(askSection); maybeNudge();
  input.focus({ preventScroll: true }); input.select();
}
$("back-btn").addEventListener("click", () => {
  if (history.state && history.state.view === "result") history.back(); else { history.replaceState({ view: "ask" }, "", "/"); goHome(); }
});
window.addEventListener("popstate", () => {
  if (history.state && history.state.view === "result" && currentResult) { renderResult(currentResult); return; }
  goHome();
});
$("error-back").addEventListener("click", () => { setTitle(null); show(askSection); input.focus(); });

// ---- Examples & trending --------------------------------------------------

$("chips").innerHTML = EXAMPLES.map((c) => `<button type="button" class="chip">${escapeHtml(c)}</button>`).join("");
$("chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  input.value = chip.textContent;
  check(chip.textContent);
});

function claimListHtml(items) {
  return items.map((r) => `
    <li>
      <button type="button" class="recent-btn" data-claim="${escapeHtml(r.claim)}">
        <span>${escapeHtml(r.claim)}</span>
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
    $("examples").hidden = true;
  } catch { /* offline or not deployed: examples stay */ }
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
  if (saved && saved.result) {
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

const CARD = { paper: "#fafbfc", ink: "#0e1422", ink2: "#3a4256", ink3: "#666e85", rule: "#d3d8e2" };

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

// Caps on the card are set tight, as on the page: tracking stays at zero and
// this only exists so the drawing code can measure a run of characters.
function tracked(ctx, text, x, y, tracking) {
  for (const ch of text) { ctx.fillText(ch, x, y); x += ctx.measureText(ch).width + tracking; }
  return x;
}
function trackedWidth(ctx, text, tracking) {
  let w = 0; for (const ch of text) w += ctx.measureText(ch).width + tracking; return w;
}

// The shared card is the same panel: white, one ink, one heavy rule, the
// claim, the verdict reversed out of an ink band, the source it leaned on.
async function renderShareCard(r) {
  const S = 1080, pad = 76;
  const c = document.createElement("canvas"); c.width = S; c.height = S;
  const ctx = c.getContext("2d");
  // One family, as on the page. Weight is the only axis that carries meaning.
  const SER = '"Libre Franklin", system-ui, sans-serif';
  const SAN = SER;
  try {
    await Promise.all([`900 62px ${SER}`, `600 34px ${SER}`, `400 26px ${SER}`,
                       `700 18px ${SER}`, `900 27px ${SER}`].map((f) => document.fonts.load(f)));
  } catch {}

  ctx.fillStyle = CARD.paper; ctx.fillRect(0, 0, S, S);
  ctx.textBaseline = "alphabetic";

  // masthead
  ctx.fillStyle = CARD.ink; ctx.font = `900 32px ${SER}`;
  ctx.fillText("CLAIM CHECKER", pad, 74);
  const n = r.studies.length, cited = r.studies.filter((s) => s.cited_in_verdict).length;
  ctx.font = `700 17px ${SAN}`; ctx.fillStyle = CARD.ink3;
  const basis = n ? `${n} READ · ${cited} RELIED ON` : "NO MATCHING STUDIES";
  tracked(ctx, basis, S - pad - trackedWidth(ctx, basis, 0), 70, 0);
  ctx.fillStyle = CARD.ink;
  ctx.fillRect(pad, 96, S - pad * 2, 2); ctx.fillRect(pad, 102, S - pad * 2, 1);

  // the claim as the headline
  ctx.font = `900 60px ${SER}`;
  const claimLines = wrapLines(ctx, r.claim.trim(), S - pad * 2, 4);
  let y = 178;
  for (const line of claimLines) { ctx.fillText(line, pad, y); y += 72; }

  // The verdict as a filled band with the words reversed out, matching the
  // page and the link-preview card. Every verdict is drawn this way.
  y += 6;
  const bandH = 66;
  ctx.fillStyle = CARD.ink;
  ctx.fillRect(pad, y, S - pad * 2, bandH);
  ctx.font = `900 30px ${SAN}`;
  ctx.fillStyle = CARD.paper;
  tracked(ctx, (VERDICT_LABELS[r.verdict] || r.verdict).toUpperCase(), pad + 18, y + 43, 0);
  ctx.fillStyle = CARD.ink;
  y += bandH + 46;

  // the takeaway
  if (r.tldr) {
    ctx.font = `600 34px ${SER}`; ctx.fillStyle = CARD.ink;
    for (const line of wrapLines(ctx, r.tldr, S - pad * 2, 3)) { ctx.fillText(line, pad, y); y += 46; }
  }

  // what is still open
  if (r.still_open) {
    y += 14;
    ctx.font = `700 15px ${SAN}`; ctx.fillStyle = CARD.ink3;
    tracked(ctx, "STILL OPEN", pad, y, 0);
    y += 26;
    ctx.font = `400 26px ${SER}`; ctx.fillStyle = CARD.ink2;
    for (const line of wrapLines(ctx, r.still_open, S - pad * 2, 2)) { ctx.fillText(line, pad, y); y += 34; }
  }

  // the source it leaned on hardest
  const best = strongestCited(r.studies);
  if (best) {
    y += 20;
    const ti = typeInfo(best.publication_types);
    ctx.font = `700 15px ${SAN}`; ctx.fillStyle = CARD.ink3;
    tracked(ctx, `${ti.label.toUpperCase()}${best.year ? " · " + best.year : ""}`, pad, y, 0);
    y += 28;
    ctx.font = `400 26px ${SER}`; ctx.fillStyle = CARD.ink2;
    for (const line of wrapLines(ctx, best.title, S - pad * 2, 2)) { ctx.fillText(line, pad, y); y += 34; }
  }

  // colophon
  ctx.fillStyle = CARD.rule; ctx.fillRect(pad, S - 96, S - pad * 2, 1);
  ctx.font = `700 16px ${SAN}`; ctx.fillStyle = CARD.ink3;
  tracked(ctx, "NOT MEDICAL ADVICE", pad, S - 62, 2);
  const host = (() => { try { return new URL(r.share_url || location.href).host; } catch { return location.host; } })();
  tracked(ctx, host.toUpperCase(), S - pad - trackedWidth(ctx, host.toUpperCase(), 2), S - 62, 2);

  return new Promise((resolve) => c.toBlob((b) => resolve(b), "image/png"));
}

function strongestCited(studies) {
  const rank = { strong: 3, "": 2, weak: 1, retracted: 0 };
  return studies.filter((s) => s.cited_in_verdict)
    .sort((a, b) => rank[typeInfo(b.publication_types).cls] - rank[typeInfo(a.publication_types).cls])[0] || null;
}

function shareText(r) {
  const still = r.still_open ? ` Still open: ${r.still_open}` : "";
  return `“${r.claim}”: ${VERDICT_LABELS[r.verdict] || r.verdict}. ${r.tldr || r.explanation}${still}`;
}

async function shareResult() {
  if (!currentResult) return;
  const r = currentResult;
  const url = r.share_url || (location.origin + "/?q=" + encodeURIComponent(r.claim));
  const text = shareText(r);
  const toast = $("toast");
  try {
    if (navigator.share) {
      let files;
      try {
        const blob = await renderShareCard(r);
        const file = new File([blob], "claim-check.png", { type: "image/png" });
        if (navigator.canShare && navigator.canShare({ files: [file] })) files = [file];
      } catch { /* card failed: share text */ }
      await navigator.share(files ? { files, title: "Claim Checker", text: `${text}\n${url}` } : { title: "Claim Checker", text, url });
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
  $("sheet-meta").innerHTML = `${escapeHtml(ti.label)}${s.year ? " · " + escapeHtml(s.year) : ""}${s.cited_in_verdict ? " · <span style='color:var(--ink)'>used for the verdict</span>" : ""}`;
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
$("sources").addEventListener("keydown", (e) => {
  if (e.target.closest("[data-stop]")) return;
  const li = e.target.closest(".study");
  if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openStudy(Number(li.dataset.i)); }
});
$("explanation").addEventListener("click", (e) => {
  const ref = e.target.closest(".ref");
  if (ref) openStudy(Number(ref.dataset.i));
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
$("record").addEventListener("click", (e) => { if (e.target.closest("#meter")) openSheet($("how-sheet"), $("how-title")); });

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
