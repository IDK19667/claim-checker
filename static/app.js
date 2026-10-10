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
const DEFAULT_TITLE = "Evident: check a health claim against real research";
function setTitle(result) {
  document.title = result
    ? `${VERDICT_LABELS[result.verdict] || result.verdict}: \u201c${result.claim}\u201d \u00b7 Evident`
    : DEFAULT_TITLE;
}
const flightSection = $("flight");
const root = document.documentElement;

/* ---- the band: three looping shots, moved only by the stream ---------------
 * Checking, the verdict and an error are one layout: a strip of footage across
 * the top and the report on a sheet below it. The strip is this. Three clips,
 * about half a megabyte each, cross-faded by opacity: the pages being gone
 * through while the search runs, the marker on the page once studies have come
 * back, the microscope under the verdict.
 *
 * Nothing here is on a clock. `FILM_STAGES` maps the stages the stream already
 * emits onto the three shots, and a shot changes when its event lands. A step
 * that takes twenty seconds simply loops its own clip: a strip that moved on
 * by itself would be a progress bar that lies, which is the one thing this
 * screen must not be.
 *
 * Nothing loads until it is wanted either. The home page's first screen is a
 * claim field and the fly-through's own poster; these clips are fetched when
 * the page has been idle after becoming interactive, or when Check is pressed,
 * whichever comes first.
 */
const filmBand = $("film");
const FILM_SHOTS = ["searching", "weighing", "verdict"];
const FILM_STAGES = {
  start: "searching",   // the claim is in, the search is being built
  query: "searching",   // searching PubMed
  found: "weighing",    // studies came back, and are being read
  weigh: "weighing",    // weighing them
  done: "verdict",      // the verdict: the shot it rests on
};

const film = {
  shot: null,
  built: false,
  // No video for a reader who asked for no motion, or who is paying for
  // bytes: the same three pictures arrive as stills, in the same band, under
  // the same sheet. Nothing else about the screen changes.
  still: window.matchMedia("(prefers-reduced-motion: reduce)").matches
    || !!(navigator.connection || {}).saveData,
  // Nobody is watching: the tab is in the background, or the band has been
  // scrolled out of view while the reader goes through the studies. A clip
  // playing then is decoding video for no one.
  tabHidden: document.hidden,
  away: true,

  // The one place that decides whether a clip runs: the shot that is on plays
  // while someone can see it and motion is wanted, and every clip holds its
  // frame otherwise. A clip that is fading out is left to finish its fade,
  // and the transitionend below stops it.
  sync() {
    if (!filmBand) return;
    const run = !this.still && !this.tabHidden && !this.away;
    for (const el of filmBand.querySelectorAll("video.film-shot")) {
      if (run && this.shot && el.dataset.on === "1") {
        // Autoplay can be refused (a data saver, a locked-down profile). The
        // poster is the clip's own first frame, so a refusal is a still band,
        // not an empty one.
        const p = el.play();
        if (p && p.catch) p.catch(() => {});
      } else if (!run || el.dataset.on === "1") {
        el.pause();
      }
    }
  },

  src(name, ext) { return `${filmBand.dataset.footage}/${name}.${ext}`; },

  layer(name) {
    return filmBand.querySelector(`.film-shot[data-shot="${name}"]`);
  },

  // Built once, on the first call that needs a picture. The server may have
  // put the verdict shot here already (a shared link opens on it), and that
  // still is kept rather than replaced: it is the same frame.
  build() {
    if (this.built || !filmBand) return;
    this.built = true;
    for (const name of FILM_SHOTS) {
      if (this.layer(name)) continue;
      let el;
      if (this.still) {
        el = document.createElement("img");
        el.src = this.src(name, "webp");
        el.alt = "";
        el.decoding = "async";
      } else {
        el = document.createElement("video");
        el.muted = true;
        el.loop = true;
        el.playsInline = true;
        el.setAttribute("playsinline", "");
        el.preload = "none";
        el.poster = this.src(name, "webp");
        for (const [ext, type] of [["webm", "video/webm"], ["mp4", "video/mp4"]]) {
          const s = document.createElement("source");
          s.src = this.src(name, ext);
          s.type = type;
          el.appendChild(s);
        }
        // The clip that just faded out stops when the fade is over, not when
        // the stage changed: pausing it at the change would freeze it halfway
        // through its own cross-fade, in full view.
        el.addEventListener("transitionend", () => {
          if (el.dataset.on === "0") el.pause();
        });
      }
      el.className = "film-shot";
      el.dataset.shot = name;
      el.dataset.on = "0";
      filmBand.appendChild(el);
    }
  },

  // A page that opened on a shared result has the verdict shot as a still,
  // which is all a cold link should ever load. Once a live check starts, the
  // reader is going to watch the band for the length of a check, so the still
  // gives way to the clip it is the first frame of. So do stills built while
  // reduced motion was on, once it has been switched off.
  upgrade() {
    if (this.still || !filmBand) return;
    const stills = filmBand.querySelectorAll("img.film-shot");
    if (!stills.length) return;
    stills.forEach((el) => el.remove());
    this.built = false;
    this.build();
    // Mid-check, the shot on screen comes straight back as its clip.
    const shot = this.shot;
    if (shot) { this.shot = null; this.show(shot); }
  },

  // Fetch a clip before it is needed. Idle time on the home page takes the
  // searching shot, which is the one every check opens on; the rest are asked
  // for as the check reaches them.
  warm(name) {
    this.build();
    const el = this.layer(name);
    if (!el || el.tagName !== "VIDEO" || el.preload === "auto") return;
    el.preload = "auto";
    el.load();
  },

  show(name) {
    if (!filmBand) return;
    this.build();
    if (this.shot === name) return;
    this.shot = name;
    filmBand.hidden = false;
    for (const el of filmBand.querySelectorAll(".film-shot")) {
      const on = el.dataset.shot === name;
      el.dataset.on = on ? "1" : "0";
      if (on && el.tagName === "VIDEO") el.preload = "auto";
    }
    this.sync();
    // The next shot is usually the one after this: ask for it now so the
    // cross-fade has something to fade to.
    const next = FILM_SHOTS[FILM_SHOTS.indexOf(name) + 1];
    if (next) this.warm(next);
  },

  stop() {
    if (!filmBand) return;
    this.shot = null;
    filmBand.hidden = true;
    for (const el of filmBand.querySelectorAll(".film-shot")) {
      el.dataset.on = "0";
      if (el.tagName === "VIDEO") el.pause();
    }
  },
};

/* Pausing what nobody can see. A tab in the background is still asked to run
 * every animation on it, and a part of the page scrolled away is still asked
 * to loop. While the tab is hidden, body.paused freezes every CSS animation
 * and the band's clip holds its frame. Scrolled out of view, the band's clip
 * holds too, and so do the looping dots: the mic while it listens, the live
 * line of the reading list, the working status. Reduced motion switched on
 * mid-visit stops the clip where it is; switched off during a check, the
 * stills become clips again. */
function onTabVisibility() {
  document.body.classList.toggle("paused", document.hidden);
  film.tabHidden = document.hidden;
  film.sync();
}
document.addEventListener("visibilitychange", onTabVisibility);
onTabVisibility();

if ("IntersectionObserver" in window) {
  if (filmBand) {
    new IntersectionObserver((entries) => {
      film.away = !entries[entries.length - 1].isIntersecting;
      film.sync();
    }).observe(filmBand);
  }
  const loops = new IntersectionObserver((entries) => {
    for (const e of entries) e.target.classList.toggle("offscreen", !e.isIntersecting);
  });
  for (const id of ["mic-btn", "reading", "status"]) if ($(id)) loops.observe($(id));
} else {
  film.away = false;
}

{
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const onMotion = () => {
    film.still = motion.matches || !!(navigator.connection || {}).saveData;
    if (document.body.classList.contains("live-check")) film.upgrade();
    film.sync();
  };
  if (motion.addEventListener) motion.addEventListener("change", onMotion);
  else if (motion.addListener) motion.addListener(onMotion);   // Safari before 14
}

/* The screen the reader is on. `null` is the home page: the fly-through, or
 * the checker on its own at /checks. Everything else is the band over the
 * sheet, and the attribute on <html> is what styles it. */
const screenState = {
  phase: $("server-result") ? "result" : null,
  at(step) {
    const shot = FILM_STAGES[step];
    // A stage the map does not know is ignored rather than guessed at.
    if (shot && this.phase) film.show(shot);
  },
  enter() {
    this.phase = "checking";
    root.dataset.screen = "checking";
    // The band and the sheet arrive once, here. A shared link opens on the
    // same layout but must not animate into it: nothing moved, the reader
    // navigated there, and a fade-in would read as something still loading.
    document.body.classList.add("live-check");
    parkFlight();
    film.upgrade();
    film.show("searching");
    window.scrollTo(0, 0);
  },
  settle(phase) {
    this.phase = phase;
    root.dataset.screen = phase;
    parkFlight();
    film.show("verdict");
  },
  leave() {
    if (!this.phase) return;
    this.phase = null;
    delete root.dataset.screen;
    document.body.classList.remove("live-check");
    film.stop();
    resumeFlight();
    window.scrollTo(0, 0);
  },
};

// While the band is on screen the fly-through is not: it is five screens of
// scroll belonging to a page the reader has left. Standing it down stops it
// decoding frames for a canvas nobody can see, and the resize is what makes it
// remeasure the page it comes back to.
function parkFlight() {
  const f = window.EvidentFlight;
  if (f) f.park();
}
function resumeFlight() {
  const f = window.EvidentFlight;
  if (f) f.resume();
  window.dispatchEvent(new Event("resize"));
}

// The claim field the reader can actually see: the fly-through's own, when the
// footage is on screen, and the checker's textarea otherwise.
function focusClaim() {
  const q = $("flight-q");
  if (q && flightSection && !flightSection.hidden) q.focus({ preventScroll: true });
  else input.focus({ preventScroll: true });
}

function show(section) {
  for (const s of [askSection, resultSection, errorSection]) s.hidden = s !== section;
  askMore.hidden = section !== askSection;
  // The fly-through belongs to the home page's resting state and nowhere else.
  // The resize tells flight.js to recompute its scroll map for the page's new
  // height rather than keeping offsets from a layout that no longer exists.
  const keepFlight = section === askSection;
  if (flightSection && flightSection.hidden === keepFlight) {
    flightSection.hidden = !keepFlight;
    window.dispatchEvent(new Event("resize"));
  }
  document.body.classList.toggle("view-ask", section === askSection);
  if (section !== resultSection) { $("bar-sticky").hidden = true; document.body.classList.remove("has-bar"); }
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

// Mirrors the evidence chart in templates/index.html. Every count and every
// word of its three lines is computed server-side in evidence.py, so a
// streamed check and the page a shared link renders can never word the same
// set of studies differently.
// (d) on the result screen. The sentences are picked server-side in
// breakdown.says from prose that has already passed the gate; a result
// cached before the block existed falls back to the explanation, which is
// what that reader was shown at the time.
function renderSays(data) {
  const sec = $("says-sec"), wrap = $("says");
  if (!sec || !wrap) return;
  const bd = data.breakdown || {};
  const lines = (bd.says && bd.says.length ? bd.says : [data.explanation])
    .filter((t) => String(t || "").trim());
  sec.hidden = !lines.length;
  wrap.innerHTML = lines
    .map((t) => `<p class="says-p">${linkStudyRefs(t, data.studies.length)}</p>`)
    .join("");
}

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
    const said = [`Study ${i + 1}`, (s.type_label || "").toLowerCase(), s.year,
                  s.cited_in_verdict ? "used for this verdict"
                    : s.off_topic ? "read, off topic" : "read but not used"]
      .filter(Boolean).join(", ");
    return `<button type="button" class="barcol${s.cited_in_verdict ? " is-used" : ""}" data-i="${i}" style="--i:${i}"` +
      ` aria-label="${escapeHtml(said)}. Go to it in the list of studies.">` +
      `<span class="barcol-track"><span class="barcol-bar h-${escapeHtml(s.tier || "moderate")}"></span></span>` +
      `<span class="barcol-no">${String(i + 1).padStart(2, "0")}</span>` +
      (tip ? `<span class="barcol-tip" aria-hidden="true">${escapeHtml(tip)}</span>` : "") +
      "</button>";
  }).join("");
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
  li.focus({ preventScroll: true });
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

// ---- The deeper layer -------------------------------------------------------
// Mirrors the <details> in templates/index.html exactly: a cold shared link is
// server rendered and a live check is rendered here, and the two must be the
// same page. Nothing is computed from the text: every sentence that reaches
// this point has already been through the gate in breakdown.py, which dropped
// anything without a study number and anything carrying a figure the abstracts
// do not have. This only lays it out.
function renderBreakdown(bd, count) {
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
  // Collapsed on every new result, including one reopened from history.
  wrap.open = false;

  const prose = (text) => `<p class="deeper-p">${linkStudyRefs(text, count)}</p>`;
  const sec = (label, inner) =>
    `<section class="deeper-sec"><p class="section-label">${escapeHtml(label)}</p>${inner}</section>`;

  const out = [];
  if ((bd.parts || []).length) {
    out.push(sec("The claim, part by part", `<ol class="parts">${bd.parts.map((p) => `
      <li class="part">
        <p class="part-claim">${escapeHtml(p.part)}</p>
        <p class="part-take">${linkStudyRefs(p.assessment, count)}</p>
      </li>`).join("")}</ol>`));
  }
  if ((bd.evidence || []).length) {
    out.push(sec("What the evidence shows",
      bd.evidence.map(prose).join("") +
      `<p class="effect"><b>How big the effect is</b><span>${linkStudyRefs(bd.effect_size || "", count)}</span></p>`));
  }
  if (bd.strength) out.push(sec("Why the verdict is what it is", prose(bd.strength)));
  // The narrow groups come off the PubMed records on the server, so this
  // prints a list rather than prose: no refs to link, nothing to gate.
  const groups = bd.populations || [];
  if (bd.applies_to || bd.not_applies_to || groups.length) {
    out.push(sec("Who this applies to",
      (bd.applies_to ? prose(bd.applies_to) : "") +
      (groups.length
        ? `<p class="deeper-p narrow">Some of this evidence comes from studies of one group only: ${escapeHtml(groups.join(", "))}. A result found in one group may not hold for everyone.</p>`
        : "") +
      (bd.not_applies_to
        ? `<p class="section-label label-2">And who it does not</p>${prose(bd.not_applies_to)}`
        : "")));
  }
  if (bd.unknowns) out.push(sec("What is still unknown", prose(bd.unknowns)));
  out.push(`<p class="fine">Every sentence above names the studies it rests on. Figures are copied from the abstracts, never worked out from them. Tap a study number to read it.</p>`);
  body.innerHTML = out.join("");

  const head = wrap.querySelector(".deeper-head .group-count");
  if (head) {
    head.textContent = bd.rests_on
      ? `${bd.rests_on} ${bd.rests_on === 1 ? "study" : "studies"}`
      : "";
  }
}

function renderResult(data) {
  currentResult = data;
  $("claim-echo").textContent = data.claim;
  showTyped(data.typed);
  $("case-line").textContent = `PubMed · ${fmtDate(data.cached_at)}`;

  const block = document.querySelector(".verdict-block");
  block.dataset.verdict = data.verdict;

  // Re-trigger the stamp animation on every new result.
  const stamp = $("stamp");
  const fresh = stamp.cloneNode(false);
  fresh.textContent = VERDICT_LABELS[data.verdict] || data.verdict;
  fresh.style.setProperty("--tilt", ({ true: "-5deg", false: "-7deg", complicated: "-4deg", insufficient: "-3deg" })[data.verdict] || "-6deg");
  stamp.replaceWith(fresh);
  if (userTapped && navigator.vibrate && !matchMedia("(prefers-reduced-motion: reduce)").matches) navigator.vibrate(25);

  $("tldr").textContent = data.tldr || "";
  renderSays(data);
  $("still-open").hidden = !data.still_open;
  $("still-open-text").textContent = data.still_open || "";
  renderBreakdown(data.breakdown, data.studies.length);

  const n = data.studies.length;
  const cited = data.studies.filter((s) => s.cited_in_verdict).length;
  const ev = evidenceLevel(data.studies);
  $("record").innerHTML = recordSentence(n, cited, ev, strongestCited(data.studies));

  const note = $("cached-note");
  note.hidden = !(data.cached || data.restored);
  note.textContent = data.restored
    ? "From your history on this device."
    : `Result from a check on ${fmtDate(data.cached_at)}. Claims are re-checked after a day.`;

  renderChart(data);
  renderNextSteps(data.next_steps, data.claim);

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
  document.querySelector(".verdict-block").dataset.verdict = "";
  const log = $("reading");
  log.innerHTML = ""; log.hidden = false;
  $("feedback").hidden = true;
  document.querySelector(".evidence").hidden = true;
  $("cached-note").hidden = true;
  $("still-open").hidden = true;
  renderBreakdown(null, 0);
  $("bar-sticky").hidden = true; document.body.classList.remove("has-bar");
  for (const sec of [askSection, errorSection]) sec.hidden = true;
  askMore.hidden = true;
  // Checking is the same screen as the verdict, minus the verdict: the band
  // on top, the claim and the work printing on the sheet below it. The
  // report is shown here rather than through show(), which would scroll and
  // re-lay-out the page a second time on the way to the same place.
  screenState.enter();
  resultSection.hidden = false;
  document.body.classList.remove("view-ask");
  logLine("Building the search", true);
  screenState.at("start");
}

// A misspelled claim is checked as it was meant. Say so, and show what was
// typed, so the reader can tell the answer is to their question.
function showTyped(typed) {
  const note = $("typed-note");
  note.hidden = !typed;
  note.textContent = typed ? `Spelling fixed. You typed \u201c${typed}\u201d.` : "";
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
  // Nothing found, rate limited, provider down: same band, same sheet, same
  // way out. Only what the sheet says changes.
  screenState.settle("error");
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

// One event, one line, one move of the footage. Short labels: the picture is
// carrying the story and the panel only has to name the step.
function handleStage(ev) {
  if (ev.stage === "query" && ev.claim) {
    const typed = $("claim-echo").textContent;
    $("claim-echo").textContent = ev.claim;
    showTyped(typed);
  }
  if (ev.stage === "query") logLine(ev.query ? `Searching PubMed for “${ev.query}”` : "Searching PubMed", true);
  else if (ev.stage === "found") {
    if (ev.count === 0) logLine("Nothing matched on PubMed", true);
    else { logLine(`Found ${ev.count} ${ev.count === 1 ? "study" : "studies"}${ev.broadened ? " after widening the search" : ""}`); logLine("Reading the studies", true); }
  } else if (ev.stage === "weigh") logLine("Weighing the evidence", true);
  screenState.at(ev.stage);
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
      // The verdict arrived: the footage goes to its last frame and stays
      // there, which is the frame the panel is about to appear over.
      screenState.at("done");
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
    if (err && err.name === "AbortError") { screenState.leave(); show(askSection); focusClaim(); }
    else renderError("Couldn't reach the server. Check your connection and try again.");
  } finally {
    inFlight = false;
    controller = null;
    setWorking(false);
  }
}

$("cancel-btn")?.addEventListener("click", () => { if (controller) controller.abort(); });

form.addEventListener("submit", (e) => { e.preventDefault(); check(input.value); });

// The fly-through carries its own claim fields, on the first screen and at the
// end of the footage. They are real GET forms so they work with no JavaScript
// at all; with JavaScript they run the same streamed check this page runs,
// rather than reloading into a server-rendered result.
for (const f of document.querySelectorAll(".flight-ask")) {
  f.addEventListener("submit", (e) => {
    const field = f.querySelector("input[name=q]");
    if (!field || !field.value.trim()) return;   // let the browser's own validation speak
    e.preventDefault();
    input.value = field.value;
    check(field.value);
  });
}

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
window.addEventListener("popstate", () => {
  if (history.state && history.state.view === "result" && currentResult) { renderResult(currentResult); return; }
  goHome();
});
$("error-back").addEventListener("click", () => {
  setTitle(null); screenState.leave(); show(askSection); focusClaim();
});

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
$("sources").addEventListener("keydown", (e) => {
  if (e.target.closest("[data-stop]")) return;
  const li = e.target.closest(".study");
  if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openStudy(Number(li.dataset.i)); }
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

// The band's first clip, fetched once the page has gone quiet. Never ahead of
// the first screen, which is a claim field and one poster, and never on a
// result page, where the reader came to read rather than to check.
if (!screenState.phase && filmBand) {
  const idle = window.requestIdleCallback
    ? window.requestIdleCallback.bind(window)
    : (cb) => setTimeout(cb, 1500);
  window.addEventListener("load", () => idle(() => film.warm("searching")), { once: true });
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}
