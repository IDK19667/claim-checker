"""
SQLite storage, optionally kept in Turso. Tables:

  verdict_cache  one row per distinct (normalized) claim, so a viral claim
                 checked by thousands of people costs one AI round-trip.
                 Rows expire by age; PubMed keeps indexing new studies.

  checks         anonymous log of every check (no user identity — this is
                 a public tool). Keeps the full study payload (pmid,
                 authors, publication types, data banks) as JSON even
                 though nothing reads it back yet. That's the hook for the
                 future "deep dive" feature (related studies / open
                 datasets / researcher contact) so building it later
                 doesn't require re-plumbing how checks are saved.

  query_cache, feedback  see the schema below.

  counters       per-day totals and nothing else: (day, name, count). What is
                 counted is the fixed list COUNTER_NAMES below: page views by
                 kind, claims submitted, results completed, errors by kind,
                 share clicks. No claim text, no address, no user agent, no
                 visitor id: a count cannot be traced to a person because
                 nothing about a person goes in.

Where it lives. By default in a local SQLite file (DB_PATH). On a host whose
disk is wiped on every restart, set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN
and the same tables live in Turso (hosted libSQL, SQLite's dialect) instead,
spoken to over its HTTP API with the standard library. The SQL is identical
either way; every public function goes through `_routed`, which runs it
against Turso when that is configured and healthy, and against the local
file otherwise. Turso being down never fails a check: see `_routed`.
"""

import atexit
import base64
import functools
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("DB_PATH", "health_claim_checker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS verdict_cache (
    claim_key TEXT PRIMARY KEY,
    claim_text TEXT NOT NULL,
    search_query TEXT,
    verdict TEXT NOT NULL,
    tldr TEXT,
    explanation TEXT,
    still_open TEXT,
    -- The deeper layer, as the gate left it. Null on rows cached before it
    -- existed, which render as a result with no breakdown rather than an
    -- empty one; they get one when the claim is next re-checked.
    breakdown_json TEXT,
    cited_json TEXT,
    studies_json TEXT,
    created_at TEXT NOT NULL,
    -- verdict.METHOD_STAMP when the answer was made: a hash of the code that
    -- decides an answer. Null on rows saved before it existed.
    method_stamp TEXT
);

-- The PubMed search a claim was given, kept so the same claim always runs
-- the same search: a re-check reuses it instead of asking the model again,
-- which could word it differently and find different studies. Keyed on the
-- claim and on the version of the prompt that wrote it, so changing that
-- prompt starts afresh. Holds what verdict_cache holds: the claim, never
-- who typed it.
CREATE TABLE IF NOT EXISTS query_cache (
    claim_key TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    search_query TEXT NOT NULL,
    surrogate TEXT,
    understood TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (claim_key, prompt_version)
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_key TEXT,
    claim_text TEXT NOT NULL,
    verdict TEXT,
    explanation TEXT,
    cached INTEGER NOT NULL DEFAULT 0,
    -- Full study payload, reserved for the future deep-dive feature.
    studies_json TEXT,
    checked_at TEXT NOT NULL
);

-- Anonymous thumbs up/down on a verdict. No identity, no free text
-- beyond a short optional note.
CREATE TABLE IF NOT EXISTS counters (
    day TEXT NOT NULL,
    name TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_key TEXT NOT NULL,
    verdict TEXT,
    helpful INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
"""


log = logging.getLogger("evident.db")

# ---- Turso ----------------------------------------------------------------------

TURSO_TIMEOUT = 4          # seconds, for one call
TURSO_FAIL_LIMIT = 3       # consecutive failures before Turso is set aside
TURSO_COOLDOWN = 60        # seconds Turso is skipped after that

_tl = threading.local()    # which backend the current call is using
_lock = threading.Lock()   # guards the three values below
_failures = 0
_skip_until = 0.0
_schema_ready = False
_last_ok = None            # None: no Turso call yet; True/False: how the latest went


class TursoError(Exception):
    """A Turso call that did not work. The message never holds the token."""


def _turso_config():
    """(base https url, token) when both env vars are set, else None."""
    url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    if not url or not token:
        return None
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url.rstrip("/"), token


def _http_post(url: str, token: str, body: dict, timeout: float) -> dict:
    """POST JSON, return the parsed reply. Tests replace this with a fake."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _arg(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _cell(c: dict):
    kind = c.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(c["value"])
    if kind == "float":
        return float(c["value"])
    if kind == "blob":
        return base64.b64decode(c.get("base64") or "")
    return c.get("value")


class _Rows:
    """What a Turso statement returns: the little of sqlite3's cursor we use."""

    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _TursoConn:
    """Looks enough like a sqlite3 connection for the functions below. Every
    statement is its own request, committed as it runs; `batch` sends several
    in one request. Rows are dicts, so row["name"] and dict(row) both work."""

    def __init__(self, base: str, token: str):
        self._base, self._token = base, token

    def batch(self, stmts) -> list[_Rows]:
        reqs = [{"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in params]}}
                for sql, params in stmts]
        reqs.append({"type": "close"})
        try:
            reply = _http_post(self._base + "/v2/pipeline", self._token,
                               {"requests": reqs}, TURSO_TIMEOUT)
            results = reply["results"]
            if len(results) < len(stmts):
                raise TursoError("short reply")
            out = []
            for r in results[:len(stmts)]:
                if r.get("type") != "ok":
                    raise TursoError("statement rejected")
                res = r["response"]["result"]
                names = [col.get("name") for col in res.get("cols") or []]
                out.append(_Rows([dict(zip(names, (_cell(c) for c in row)))
                                  for row in res.get("rows") or []]))
            return out
        except TursoError:
            raise
        except urllib.error.HTTPError as e:
            raise TursoError(f"HTTP {e.code}") from None
        except Exception as e:  # network, timeout, bad JSON, bad shape
            raise TursoError(type(e).__name__) from None

    def execute(self, sql: str, params=()) -> _Rows:
        return self.batch([(sql, tuple(params))])[0]

    def executescript(self, script: str):
        lines = [ln for ln in script.splitlines() if not ln.lstrip().startswith("--")]
        stmts = [x.strip() for x in "\n".join(lines).split(";") if x.strip()]
        self.batch([(x, ()) for x in stmts])


def _turso_usable() -> bool:
    if _turso_config() is None:
        return False
    with _lock:
        return time.monotonic() >= _skip_until


def _turso_ok():
    global _failures, _last_ok
    with _lock:
        _failures = 0
        _last_ok = True


def _turso_failed(err: Exception):
    """Count it; after TURSO_FAIL_LIMIT in a row, skip Turso for a while. One
    more failure after that skips it again, one success clears the count."""
    global _failures, _skip_until, _last_ok
    with _lock:
        _last_ok = False
        _failures += 1
        tripped = _failures >= TURSO_FAIL_LIMIT
        if tripped:
            _skip_until = time.monotonic() + TURSO_COOLDOWN
            _failures = TURSO_FAIL_LIMIT - 1
    log.warning("Turso call failed (%s); using local SQLite for this call%s", err,
                f", and skipping Turso for {TURSO_COOLDOWN}s" if tripped else "")


def _reset_turso_state():
    """For tests: forget failures, cool-down and whether Turso has its tables."""
    global _failures, _skip_until, _schema_ready, _last_ok
    with _lock:
        _failures, _skip_until, _schema_ready, _last_ok = 0, 0.0, False, None


def store_status() -> str:
    """Where storage is going, from in-memory state only (no Turso call):
    "local", "turso", "turso-failing" or "turso-unused". Leaks nothing else."""
    if _turso_config() is None:
        return "local"
    with _lock:
        if _last_ok is None:
            return "turso-unused"
        if _last_ok is False or time.monotonic() < _skip_until:
            return "turso-failing"
        return "turso"


@contextmanager
def get_conn():
    if getattr(_tl, "turso", False):
        base, token = _turso_config()
        yield _TursoConn(base, token)
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _routed(fn):
    """
    Run a storage function against Turso when it is configured and not being
    skipped, and against the local SQLite file otherwise. If Turso fails
    (network, HTTP error, bad reply) the failure is logged without the token
    and the same function runs again against the local file: a read there is
    at worst a miss, a write lands there. Nothing raises because Turso is down.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _schema_ready
        if _turso_usable():
            _tl.turso = True
            try:
                if not _schema_ready:
                    with get_conn() as conn:
                        _init_schema(conn)
                    _schema_ready = True
                result = fn(*args, **kwargs)
                _turso_ok()
                return result
            except Exception as e:  # Turso, or a reply we cannot read
                _turso_failed(TursoError(type(e).__name__) if not isinstance(e, TursoError) else e)
            finally:
                _tl.turso = False
        return fn(*args, **kwargs)
    return wrapper


def _run_many(conn, stmts) -> list[_Rows]:
    """Several reads: one request to Turso, one after another locally."""
    if isinstance(conn, _TursoConn):
        return conn.batch(stmts)
    return [conn.execute(sql, params) for sql, params in stmts]


def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn, table: str, column: str, kind: str = "TEXT"):
    """
    Add a column an older database lacks. Two processes starting at once can
    both see it missing and both add it: the second ALTER is then refused
    (sqlite3.OperationalError "duplicate column name" locally, a rejected
    statement, TursoError, from Turso). Whatever the error, when the column
    is there afterwards the job is done; only when it is still missing is the
    error raised.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    except Exception:
        if column not in _columns(conn, table):
            raise


def _init_schema(conn):
    conn.executescript(SCHEMA)
    # Lightweight migration for databases created before these columns existed.
    if "claim_key" not in _columns(conn, "checks"):
        _add_column(conn, "checks", "claim_key")
    vcols = _columns(conn, "verdict_cache")
    for column in ("tldr", "still_open", "breakdown_json", "method_stamp"):
        if column not in vcols:
            _add_column(conn, "verdict_cache", column)
    conn.execute(
        "UPDATE checks SET claim_key = lower(trim(claim_text)) WHERE claim_key IS NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS checks_key_time ON checks (claim_key, checked_at)")
    # The newest saved claims first (find_equivalent, the latest list) are
    # read from this index, not by sorting the whole table.
    conn.execute("CREATE INDEX IF NOT EXISTS verdict_cache_time ON verdict_cache (created_at)")


def init_db():
    """Create the tables locally always (the fallback needs them) and in
    Turso too when it is configured. A Turso failure here is not fatal: the
    first call that finds Turso healthy creates the tables then."""
    global _schema_ready
    with get_conn() as conn:
        _init_schema(conn)
    if _turso_usable():
        _tl.turso = True
        try:
            with get_conn() as conn:
                _init_schema(conn)
            _schema_ready = True
            _turso_ok()
        except Exception as e:
            _turso_failed(TursoError(type(e).__name__) if not isinstance(e, TursoError) else e)
        finally:
            _tl.turso = False


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---- Verdict cache -------------------------------------------------------

def normalize_claim(claim: str) -> str:
    """Case/whitespace/trailing-punctuation insensitive key for a claim."""
    key = claim.strip().lower()
    key = re.sub(r"\s+", " ", key)
    key = key.rstrip(" .!?")
    return key


@_routed
def get_cached_query(claim: str, prompt_version: str) -> tuple[str, str, str] | None:
    """(search query, surrogate, claim as understood) this claim was given
    before, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT search_query, surrogate, understood FROM query_cache "
            "WHERE claim_key = ? AND prompt_version = ?",
            (normalize_claim(claim), prompt_version)).fetchone()
    if not row:
        return None
    return row["search_query"], row["surrogate"] or "", row["understood"] or claim


@_routed
def put_cached_query(claim: str, prompt_version: str, search_query: str,
                     surrogate: str, understood: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO query_cache
               (claim_key, prompt_version, search_query, surrogate, understood, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (normalize_claim(claim), prompt_version, search_query, surrogate or "",
             understood or claim, _now()))


@_routed
def get_cached_verdict(claim: str, max_age_hours: float) -> dict | None:
    """Return a cached check for this claim if one exists and is fresh enough."""
    if max_age_hours <= 0:
        return None
    key = normalize_claim(claim)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM verdict_cache WHERE claim_key = ?", (key,)
        ).fetchone()
    if row is None or not _whole(row):
        return None
    created = datetime.fromisoformat(row["created_at"])
    if datetime.now(timezone.utc) - created > timedelta(hours=max_age_hours):
        return None
    return {
        "claim_text": row["claim_text"],
        "search_query": row["search_query"],
        "verdict": row["verdict"],
        "tldr": row["tldr"] or "",
        "explanation": row["explanation"],
        "still_open": row["still_open"] or "",
        "breakdown": _breakdown(row),
        "cited_studies": json.loads(row["cited_json"] or "[]"),
        "studies": json.loads(row["studies_json"] or "[]"),
        "cached_at": row["created_at"],
        "method_stamp": _stamp(row),
    }


def _whole(row) -> bool:
    """
    A saved answer carries both its short answer and its still-open line.
    One saved before those columns existed (they are NULL), or damaged since,
    is not served: to the check it is a miss, so the check runs again and the
    row is replaced; to a shared link or a card it is no saved answer at all.
    A line counts only when it has at least one letter or digit, in any
    script: a zero-width space, a lone full stop or a lone dash is empty.
    Nor is one saved from a reply that could not be read (_failed).
    """
    return all(_worded(row[k]) for k in ("tldr", "still_open")) and not _failed(row)


def _worded(line) -> bool:
    """A line that says something: at least one letter or digit, in any script."""
    return any(ch.isalnum() for ch in str(line or ""))


def _listable(row) -> bool:
    """
    A row a public list may show (type-ahead, trending, related claims, the
    sitemap, llms.txt): the same line test as _whole, with one allowance kept
    from before, that an old real answer saved before the short answer or the
    still-open line existed (NULL or blank) still stands in those lists. A
    line that is there but has no letter or digit is no line, so that row is
    left out, as a failed reply is.
    """
    return not _failed(row) and all(
        not str(row[k] or "").strip() or _worded(row[k]) for k in ("tldr", "still_open"))


# The short answer the fallback for a reply that could not be read at all (cut
# off, declined, not JSON) wrote in its place: verdict._fallback's own line.
# Rows saved with it are kept, but they are a failed check, not a verdict: never
# served, and left out of every public list, count, suggestion, feed and sitemap.
# A failed reply is no longer saved; this keeps the ones saved before that out.
FAILED_REPLY_TLDR = "Couldn't reach a verdict from the evidence found."
# The same test in SQL, for the readers that feed a public list. Its one
# parameter is FAILED_REPLY_TLDR.
_ANSWERED = "TRIM(COALESCE({t}tldr, '')) != ?"


def _failed(row) -> bool:
    """A row saved from a reply that could not be read: its short answer is the fallback's own line."""
    return str(row["tldr"] or "").strip() == FAILED_REPLY_TLDR


def whole(row) -> bool:
    """_whole, for callers outside this module: a row that is a finished answer."""
    return _whole(row)


def _stamp(row) -> str | None:
    """The row's method stamp, or None (a row, or a database, from before it)."""
    try:
        return row["method_stamp"] or None
    except (IndexError, KeyError):
        return None


def _breakdown(row) -> dict | None:
    """
    The deeper layer off a cache row. A row from before the column existed,
    or one holding something unreadable, gives None: a result with no
    breakdown renders cleanly, and that is a better answer than a half one.
    """
    try:
        raw = row["breakdown_json"]
    except (IndexError, KeyError):
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


@_routed
def put_cached_verdict(
    claim: str,
    search_query: str,
    verdict: str,
    explanation: str,
    cited_studies: list[str],
    studies: list[dict],
    tldr: str = "",
    still_open: str = "",
    breakdown: dict | None = None,
    method_stamp: str | None = None,
):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO verdict_cache
               (claim_key, claim_text, search_query, verdict, tldr, explanation, still_open,
                breakdown_json, cited_json, studies_json, created_at, method_stamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                normalize_claim(claim),
                claim,
                search_query,
                verdict,
                tldr,
                explanation,
                still_open,
                json.dumps(breakdown) if breakdown else None,
                json.dumps(cited_studies),
                json.dumps(studies),
                _now(),
                method_stamp or None,   # an empty stamp is no stamp
            ),
        )


@_routed
def refresh_saved(claim: str):
    """Today's date on a saved answer kept as it is (the method and the
    studies are the same as when it was made). Nothing else changes."""
    with get_conn() as conn:
        conn.execute("UPDATE verdict_cache SET created_at = ? WHERE claim_key = ?",
                     (_now(), normalize_claim(claim)))


# ---- Anonymous check log -------------------------------------------------

@_routed
def log_check(claim_text: str, verdict: str, explanation: str,
              studies: list[dict], cached: bool = False):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO checks
               (claim_key, claim_text, verdict, explanation, cached, studies_json, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (normalize_claim(claim_text), claim_text, verdict, explanation,
             int(cached), json.dumps(studies), _now()),
        )


@_routed
def trending(days: int = 7, min_checks: int = 2, limit: int = 8) -> list[dict]:
    """
    Claims checked by several people recently, newest-popular first. The
    min_checks floor keeps one person's odd query from being broadcast.
    Only claims that still have a cached verdict are returned, so tapping
    one is always instant and free.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.claim_key, v.claim_text, v.verdict, v.tldr, v.still_open, COUNT(*) AS n
               FROM checks c
               JOIN verdict_cache v ON v.claim_key = c.claim_key
               WHERE c.checked_at > ? AND """ + _ANSWERED.format(t="v.") + """
               GROUP BY c.claim_key
               HAVING n >= ?
               ORDER BY n DESC, MAX(c.checked_at) DESC
               LIMIT ?""",
            (since, FAILED_REPLY_TLDR, min_checks, limit),
        ).fetchall()
        return [{"claim": r["claim_text"], "verdict": r["verdict"], "checks": r["n"]}
                for r in rows if _listable(r)]


@_routed
def recent_cached(limit: int = 200) -> list[dict]:
    """Cached claims, newest first. Used for the sitemap and llms.txt."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT claim_text, verdict, created_at, tldr, still_open FROM verdict_cache "
            "WHERE " + _ANSWERED.format(t="") + " "
            "ORDER BY created_at DESC LIMIT ?", (FAILED_REPLY_TLDR, limit)
        ).fetchall()
        return [{k: r[k] for k in ("claim_text", "verdict", "created_at")} for r in rows if _listable(r)]


@_routed
def get_cached_by_key(claim_key: str) -> dict | None:
    """Cached verdict by normalized key, any age. Used for link previews."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM verdict_cache WHERE claim_key = ?", (claim_key,)
        ).fetchone()
    if row is None or not _whole(row):
        return None
    return {
        "claim_text": row["claim_text"],
        # The search that was actually run, as stored. A result opened from
        # a link used to drop it and show an empty "Searched PubMed for".
        "search_query": row["search_query"] or "",
        "verdict": row["verdict"],
        "tldr": row["tldr"] or "",
        "explanation": row["explanation"],
        "still_open": row["still_open"] or "",
        "breakdown": _breakdown(row),
        "cited_studies": json.loads(row["cited_json"] or "[]"),
        "studies": json.loads(row["studies_json"] or "[]"),
        "cached_at": row["created_at"],
        "method_stamp": _stamp(row),
    }


# ---- The same claim, typed another way ------------------------------------
# Two claims are the same claim only when, after the steps in `claim_fold`,
# they leave exactly the same words in exactly the same order. No synonyms,
# no "close enough", no word missing or extra. Ignored: case, quotes,
# apostrophes, punctuation, hyphens between words, extra spaces, "&" for
# "and", a short list of small words, the question form, a plural or verb
# "-s", and where a single hedge word stands. Never folded: negations, digits
# and what is joined to them, word order, prepositions (over and under, to
# and from), and/or, more/less, before/after, tense, and which hedge it is.
# suggest._STOP is not reused: dropped whole it would merge "men over 50"
# with "men under 50" and "can cause" with "causes".

# Dropped, these and only these: articles, possessives, "it", the linking
# verbs and the do-support of a question. Every other word is kept.
_FOLD_DROP = frozenset("a an the my your our their its it is are be has have do does".split())
# A hedge counts wherever it stands, and only one of them can move.
_FOLD_HEDGES = frozenset("can could may might will would should must shall".split())
# A final s that is not a plural, or whose removal makes another word.
_FOLD_KEEP_S = frozenset("""
diabetes herpes mumps measles rabies scabies rickets shingles hives tetanus aids
virus lupus gas bias pancreas atlas christmas canvas lens news series species
always perhaps sometimes whereas yes thus plus its his hers ours yours theirs
physics genetics economics ethics aerobics athletics gymnastics politics statistics
glasses braces gums greens sweets spirits fries dates means odds pots tens
pcos sids sars mers ards feces faeces arthritis psoriasis
""".split())
_FOLD_QUOTES = {"‘": "'", "’": "'", "‛": "'", "′": "'", "`": "'",
                "“": '"', "”": '"', "„": '"', "″": '"'}
_FOLD_DASHES = "‐‑‒–—―−"
_FOLD_LEAD = [("is", "it", "true", "that"), ("is", "it", "true"), ("true", "or", "false")]
_FOLD_TRAIL = [("true", "or", "false"), ("or", "not")]
# A number stays one token with its inner separators: 2.5, 2-5, 4,000, 1/2, 3:1.
# It also keeps its sign and its decimal point: a "-" right before a digit,
# with no letter or digit just before it, is a minus sign (-5, but the joiner
# in omega-3 and 2-5), and a "." right before a digit, with no digit just
# before it, is a decimal point (.5, -.5). So "-5 C" is never "5 C", nor
# ".5 mg" "5 mg". Every dash, the minus sign U+2212 included, is a "-" by then.
_FOLD_NUMBER = r"[.,/:\-]\d+"
_FOLD_TOKEN = re.compile(
    r"(?<!\w)-\.?\d+(?:" + _FOLD_NUMBER + r")*\w*"
    r"|(?<!\d)\.\d+(?:" + _FOLD_NUMBER + r")*\w*"
    r"|\w*\d(?:" + _FOLD_NUMBER + r")+\w*|\w+|\S")


def _fold_contractions(t: str) -> str:
    """Spelled out, never dropped: the "not" always stays."""
    t = re.sub(r"\bcan'?t\b", "can not", t)
    t = re.sub(r"\bcannot\b", "can not", t)
    t = re.sub(r"\bwon'?t\b", "will not", t)
    t = re.sub(r"\bshan'?t\b", "shall not", t)
    t = re.sub(r"n't\b", " not", t)
    # The other short forms are spelled out as their own word, so "she'll"
    # never becomes "shell" nor "we're" "were". The "'d" of "I'd" can mean
    # had or would, so it is kept apart as a bare "d" and never guessed.
    t = re.sub(r"(?<=\w)'(ll|re|ve|m|d)\b",
               lambda m: " " + {"ll": "will", "re": "are", "ve": "have", "m": "am", "d": "d"}[m.group(1)], t)
    return re.sub(r"\b(do|does|did|is|are|was|were|has|have|had|should|would|could|must)nt\b",
                  r"\1 not", t)


def _fold_plural(w: str) -> str:
    if len(w) <= 3 or not w.isalpha() or w in _FOLD_KEEP_S:
        return w
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ie"):
        return w[:-2] + "y"
    if w.endswith(("sses", "shes", "xes")):
        return w[:-2]
    if w.endswith(("ss", "us", "is")):
        return w
    return w[:-1] if w.endswith("s") else w


# Compatibility forms whose plain form loses what they say: a raised or
# lowered digit (10⁹ is not 109, H₂O is not H2O) and a fraction (½ is not 12).
_FOLD_LOSSY_TAGS = ("<super>", "<sub>", "<fraction>")
_FOLD_BEFORE_NUMBER = re.compile(r"\s+\.?\d")


def _fold_tokens(claim: str) -> list:
    import unicodedata
    raw = str(claim or "")
    if any(unicodedata.decomposition(ch).startswith(_FOLD_LOSSY_TAGS) for ch in raw):
        return None   # such a claim has no fold key at all
    # No letter is folded into more than one: ß would become ss (Maße is not
    # Masse), a ligature such as ﬁ fi, ㎎ mg. A character that folding turns
    # into several, any of them a letter or digit, leaves the claim no key.
    for ch in set(raw):
        folded = unicodedata.normalize("NFKC", ch).casefold()
        if len(folded) > 1 and any(c.isalnum() for c in folded):
            return None
    t = unicodedata.normalize("NFKC", raw)
    for k, v in _FOLD_QUOTES.items():
        t = t.replace(k, v)
    for d in _FOLD_DASHES:
        t = t.replace(d, "-")
    t = _fold_contractions(t.casefold().replace("&", " and ")).replace("'", "")
    out = []
    for m in _FOLD_TOKEN.finditer(t):
        tok = m.group()
        if tok == "-" and _FOLD_BEFORE_NUMBER.match(t, m.end()):
            # A dash standing free right before a number ("at - 5 C",
            # "5 - 10 kg") may be a minus or a range: it is kept, never dropped.
            out.append(tok)
            continue
        if len(tok) == 1 and not tok.isalnum():
            cat = unicodedata.category(tok)
            # Symbols (% + < > $ # and emoji) stay; punctuation goes.
            if cat.startswith("S") or tok in "%‰#":
                out.append(tok)
            elif cat[0] in "MLN" or cat == "Cf":
                # A combining mark (a vowel sign, an accent that does not
                # compose), a letter or digit the tokens do not keep, or an
                # invisible joiner inside a word: dropping it could make two
                # different words one. Such a claim has no fold key at all.
                return None
            continue
        out.append(tok)
    return out


def claim_fold(claim: str) -> tuple:
    """The ordered key two wordings of one claim share. See the notes above.
    Empty, so it matches nothing, when folding would drop any character that
    is part of a word (see `_fold_tokens`): no letter is ever silently lost."""
    toks = _fold_tokens(claim)
    if toks is None:
        return ()
    changed = True
    while changed:
        changed = False
        for w in _FOLD_LEAD:
            if len(toks) > len(w) and tuple(toks[:len(w)]) == w:
                toks, changed = toks[len(w):], True
                break
        for w in _FOLD_TRAIL:
            if len(toks) > len(w) and tuple(toks[-len(w):]) == w:
                toks, changed = toks[:-len(w)], True
                break
    # Read before plurals fold, so "soda cans" never becomes a hedge.
    hedges = [w for w in toks if w in _FOLD_HEDGES]
    if len(hedges) == 1:
        i = toks.index(hedges[0])
        toks = ["~" + hedges[0]] + toks[:i] + toks[i + 1:]
    return tuple(_fold_plural(w) for w in toks if w not in _FOLD_DROP)


# Problems the page repairs when it renders (display.saved_record_problems):
# they do not stop a saved answer being served for another wording.
_REPAIRED_AT_RENDER = frozenset({"count_mismatch", "cited_off_topic"})


# How many saved claims a search for a reworded match reads: the newest ones.
# Folding costs about 0.2 ms a claim, and on Turso the rows come back in one
# request under its timeout, so the cost of a miss is bounded. An older claim
# past this many is not matched (a miss only costs a fresh check). See
# DECISIONS.md: when the table nears this size, add an indexed fold key.
EQUIVALENT_SCAN = 2000


@_routed
def find_equivalent(claim: str) -> str | None:
    """
    The stored wording of a saved answer to this same claim, or None. Only a
    whole row (`_whole`) whose record has no problem the page cannot repair
    is returned. A row stored under this claim's own key comes first (one
    lookup by key, at any age), then the newest of the EQUIVALENT_SCAN
    newest saved claims. Any age: the caller decides whether it is fresh
    enough.
    """
    key = claim_fold(claim)
    if not key:
        return None
    own = normalize_claim(claim)
    # The unrouted reader: this call already runs on one backend.
    mine = get_cached_by_key.__wrapped__(own)
    if mine and claim_fold(mine["claim_text"]) == key and servable(mine):
        return mine["claim_text"]
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT claim_key, claim_text, tldr, still_open FROM verdict_cache "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (EQUIVALENT_SCAN,)).fetchall()
    for r in rows:
        if r["claim_key"] == own or not (r["claim_text"] and _whole(r)):
            continue
        if claim_fold(r["claim_text"]) != key:
            continue
        saved = get_cached_by_key.__wrapped__(r["claim_key"])
        if servable(saved):
            return saved["claim_text"]
    return None


def servable(saved: dict | None) -> bool:
    """A saved answer (as get_cached_by_key reads it) that is whole and has no
    problem the page cannot repair when it renders."""
    if not saved:
        return False
    import display
    problems = set(display.saved_record_problems(
        saved["claim_text"], saved["verdict"], saved["cited_studies"], saved["studies"],
        saved["breakdown"], saved["search_query"], saved["cached_at"]))
    return not problems - _REPAIRED_AT_RENDER


@_routed
def first_fresh_check(claim_key: str) -> str | None:
    """
    When this claim was first checked for real (not answered from the cache),
    from the anonymous check log, or None if the log has no such row. The
    cache row itself only keeps its latest date: a re-check replaces it.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(checked_at) FROM checks WHERE claim_key = ? AND cached = 0",
            (claim_key,)
        ).fetchone()
    return row[0] if row and row[0] else None


# ---- Feedback ----------------------------------------------------------------

@_routed
def add_feedback(claim: str, verdict: str | None, helpful: bool, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback (claim_key, verdict, helpful, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (normalize_claim(claim), verdict, int(bool(helpful)), (note or "")[:300], _now()),
        )


@_routed
def recent_checks(limit: int = 50) -> list[dict]:
    """Newest first. Not exposed publicly yet; here for ops/debugging."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, claim_text, verdict, cached, checked_at FROM checks "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


@_routed
def ledger() -> dict:
    """
    Real counts for the front page. Every number here is a fact about this
    database, never a rounded-up impression of one: `studies_read` counts
    distinct PubMed records actually fetched and shown, not fetch attempts.
    """
    with get_conn() as conn:
        a, b, c = _run_many(conn, [
            ("SELECT COUNT(*) n FROM verdict_cache WHERE " + _ANSWERED.format(t=""), (FAILED_REPLY_TLDR,)),
            ("SELECT COUNT(*) n FROM checks", ()),
            ("SELECT studies_json FROM verdict_cache WHERE studies_json IS NOT NULL AND "
             + _ANSWERED.format(t=""), (FAILED_REPLY_TLDR,)),
        ])
        claims, checks, rows = a.fetchone()["n"], b.fetchone()["n"], c.fetchall()
    pmids, pooled = set(), set()
    for r in rows:
        try:
            studies = json.loads(r["studies_json"] or "[]")
        except (ValueError, TypeError):
            continue
        for s in studies:
            pmid = str(s.get("pmid") or "")
            if not pmid:
                continue
            pmids.add(pmid)
            types = " ".join(s.get("publication_types") or [])
            if "Meta-Analysis" in types or "Systematic Review" in types:
                pooled.add(pmid)
    return {"claims": claims, "checks": checks,
            "studies_read": len(pmids), "pooled": len(pooled)}


@_routed
def recent_verdicts(limit: int = 12) -> list[dict]:
    """
    Cached verdicts newest first, with the studies behind each one so the
    front page can show the evidence rather than describe it. `issue` is the
    row's place in check order, counted from the rows there now, so the
    newest is always numbered the same as the "Claims checked" count: rowid
    left gaps where a row was replaced, and the list began at No. 18 under
    a count of 17. One COUNT(*) and the row's place in the list give the
    number, so the cost does not grow with every row (a correlated count
    per row made the home page quadratic in the table's size).
    """
    with get_conn() as conn:
        a, b = _run_many(conn, [
            ("SELECT COUNT(*) n FROM verdict_cache WHERE " + _ANSWERED.format(t=""), (FAILED_REPLY_TLDR,)),
            ("SELECT claim_text, search_query, verdict, tldr, still_open, studies_json, cited_json, created_at "
             "FROM verdict_cache WHERE " + _ANSWERED.format(t="") + " "
             "ORDER BY created_at DESC, rowid DESC LIMIT ?", (FAILED_REPLY_TLDR, limit)),
        ])
        total, rows = a.fetchone()["n"], b.fetchall()
    out = []
    for i, r in enumerate(rows):
        try:
            studies = json.loads(r["studies_json"] or "[]")
            cited = json.loads(r["cited_json"] or "[]")
        except (ValueError, TypeError):
            studies, cited = [], []
        out.append({"issue": total - i, "claim_text": r["claim_text"],
                    "search_query": r["search_query"] or "",
                    "verdict": r["verdict"], "tldr": r["tldr"] or "",
                    "still_open": r["still_open"] or "",
                    "created_at": r["created_at"],
                    "studies": studies, "cited": cited})
    return out


@_routed
def all_claims(limit: int = 500) -> list[dict]:
    """
    Every cached claim with its verdict, newest first, and nothing else.
    Deliberately light: this feeds type-ahead, so it must not drag the
    studies blob along for the ride, and it carries no list number either:
    nothing that reads it shows one, and counting costs a query per keystroke.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT claim_text, verdict, tldr, still_open FROM verdict_cache "
            "WHERE " + _ANSWERED.format(t="") + " "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (FAILED_REPLY_TLDR, limit)
        ).fetchall()
    return [{"claim_text": r["claim_text"], "verdict": r["verdict"]} for r in rows if _listable(r)]


@_routed
def saved_stamp() -> tuple:
    """(row count, newest save date): changes whenever a saved answer is added or replaced."""
    with get_conn() as conn:
        r = conn.execute("SELECT COUNT(*) n, MAX(created_at) m FROM verdict_cache").fetchone()
    return (r["n"], r["m"])


@_routed
def saved_records(limit: int = 500) -> list[dict]:
    """
    Saved answers as stored, newest first, with the JSON columns still text so
    a record that cannot be read is reported rather than raised. For the
    sitemap, which checks each one before listing it.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT claim_text, verdict, search_query, cited_json, studies_json, breakdown_json, created_at, "
            "tldr, still_open FROM verdict_cache WHERE " + _ANSWERED.format(t="") + " "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (FAILED_REPLY_TLDR, limit)
        ).fetchall()
    keep = ("claim_text", "verdict", "search_query", "cited_json", "studies_json", "breakdown_json", "created_at")
    return [{k: r[k] for k in keep} for r in rows if _listable(r)]


# ---- Daily counts -----------------------------------------------------------------
# Counting must never slow a page or fail a check, and on Turso every write is
# a network round trip. So a count is added to a small in-memory tally and a
# background thread writes the tally out every COUNT_FLUSH_SECONDS (and once more
# at exit). A restart can lose at most that many seconds of counts. Tests set
# COUNT_FLUSH_SECONDS to 0, which starts no thread, and call flush_counts().

# Counted on the server.
SERVER_COUNTERS = (
    "view.home", "view.result", "view.info",
    "check.submitted", "check.completed",
    "error.server",                       # a 500 or an unhandled exception
    "provider.rate_limit",                # the AI provider answered 429
    "provider.unavailable",               # the AI provider answered 503 or could not be reached
    "provider.auth",                      # its key rejected or missing
    "provider.other",                     # any other provider error
    "pubmed.failure",                     # PubMed could not be reached or answered badly
)
# Sent by the page to POST /api/count: the only names that endpoint accepts.
CLIENT_COUNTERS = (
    "share.click", "share.copy",
    "client_error.invalid", "client_error.network", "client_error.limit", "client_error.provider",
)
COUNTER_NAMES = frozenset(SERVER_COUNTERS) | frozenset(CLIENT_COUNTERS)

COUNT_FLUSH_SECONDS = 30
_pending: dict = {}
_pending_lock = threading.Lock()
_flusher_started = False


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def count_event(name: str, n: int = 1) -> None:
    """Add to today's total for `name`. A name not on the list is ignored,
    so nothing but the fixed counters can ever reach the table."""
    global _flusher_started
    if name not in COUNTER_NAMES:
        return
    key = (_today(), name)
    with _pending_lock:
        _pending[key] = _pending.get(key, 0) + n
        start = COUNT_FLUSH_SECONDS > 0 and not _flusher_started
        if start:
            _flusher_started = True
    if start:
        threading.Thread(target=_flush_loop, name="count-flush", daemon=True).start()
        atexit.register(flush_counts)


def _flush_loop():
    while True:
        time.sleep(max(1, COUNT_FLUSH_SECONDS))
        flush_counts()


def flush_counts() -> None:
    """Write the tally out. Never raises: a count that cannot be saved is
    dropped after a log line, because a count is not worth a failed request."""
    with _pending_lock:
        batch = list(_pending.items())
        _pending.clear()
    if not batch:
        return
    try:
        _write_counts(batch)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not save %d day counts: %s", len(batch), type(e).__name__)


@_routed
def _write_counts(items) -> None:
    with get_conn() as conn:
        _run_many(conn, [("INSERT INTO counters (day, name, count) VALUES (?, ?, ?) "
                          "ON CONFLICT(day, name) DO UPDATE SET count = count + excluded.count",
                          (day, name, n)) for (day, name), n in items])


@_routed
def counts_since(days: int = 30) -> list[dict]:
    """Saved totals for the last `days` days (today included), as
    [{"day", "name", "count"}], oldest first. Counts not yet flushed are not in it."""
    first = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute("SELECT day, name, count FROM counters WHERE day >= ? ORDER BY day, name",
                            (first,)).fetchall()
    return [dict(r) for r in rows]
