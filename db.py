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

Where it lives. By default in a local SQLite file (DB_PATH). On a host whose
disk is wiped on every restart, set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN
and the same tables live in Turso (hosted libSQL, SQLite's dialect) instead,
spoken to over its HTTP API with the standard library. The SQL is identical
either way; every public function goes through `_routed`, which runs it
against Turso when that is configured and healthy, and against the local
file otherwise. Turso being down never fails a check: see `_routed`.
"""

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
    created_at TEXT NOT NULL
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


def _init_schema(conn):
    conn.executescript(SCHEMA)
    # Lightweight migration for databases created before claim_key existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(checks)").fetchall()}
    if "claim_key" not in cols:
        conn.execute("ALTER TABLE checks ADD COLUMN claim_key TEXT")
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(verdict_cache)").fetchall()}
    if "tldr" not in vcols:
        conn.execute("ALTER TABLE verdict_cache ADD COLUMN tldr TEXT")
    if "still_open" not in vcols:
        conn.execute("ALTER TABLE verdict_cache ADD COLUMN still_open TEXT")
    if "breakdown_json" not in vcols:
        conn.execute("ALTER TABLE verdict_cache ADD COLUMN breakdown_json TEXT")
    conn.execute(
        "UPDATE checks SET claim_key = lower(trim(claim_text)) WHERE claim_key IS NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS checks_key_time ON checks (claim_key, checked_at)")


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
    if row is None:
        return None
    created = datetime.fromisoformat(row["created_at"])
    if datetime.now(timezone.utc) - created > timedelta(hours=max_age_hours):
        return None
    return {
        "search_query": row["search_query"],
        "verdict": row["verdict"],
        "tldr": row["tldr"] or "",
        "explanation": row["explanation"],
        "still_open": row["still_open"] or "",
        "breakdown": _breakdown(row),
        "cited_studies": json.loads(row["cited_json"] or "[]"),
        "studies": json.loads(row["studies_json"] or "[]"),
        "cached_at": row["created_at"],
    }


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
):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO verdict_cache
               (claim_key, claim_text, search_query, verdict, tldr, explanation, still_open,
                breakdown_json, cited_json, studies_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )


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
            """SELECT c.claim_key, v.claim_text, v.verdict, COUNT(*) AS n
               FROM checks c
               JOIN verdict_cache v ON v.claim_key = c.claim_key
               WHERE c.checked_at > ?
               GROUP BY c.claim_key
               HAVING n >= ?
               ORDER BY n DESC, MAX(c.checked_at) DESC
               LIMIT ?""",
            (since, min_checks, limit),
        ).fetchall()
        return [{"claim": r["claim_text"], "verdict": r["verdict"], "checks": r["n"]} for r in rows]


@_routed
def recent_cached(limit: int = 200) -> list[dict]:
    """Cached claims, newest first. Used for the sitemap and llms.txt."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT claim_text, verdict, created_at FROM verdict_cache "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


@_routed
def get_cached_by_key(claim_key: str) -> dict | None:
    """Cached verdict by normalized key, any age. Used for link previews."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM verdict_cache WHERE claim_key = ?", (claim_key,)
        ).fetchone()
    if row is None:
        return None
    return {
        "claim_text": row["claim_text"],
        "verdict": row["verdict"],
        "tldr": row["tldr"] or "",
        "explanation": row["explanation"],
        "still_open": row["still_open"] or "",
        "breakdown": _breakdown(row),
        "cited_studies": json.loads(row["cited_json"] or "[]"),
        "studies": json.loads(row["studies_json"] or "[]"),
        "cached_at": row["created_at"],
    }


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
            ("SELECT COUNT(*) n FROM verdict_cache", ()),
            ("SELECT COUNT(*) n FROM checks", ()),
            ("SELECT studies_json FROM verdict_cache WHERE studies_json IS NOT NULL", ()),
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
    row's insertion order, which makes every check a numbered one.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT rowid AS issue, claim_text, verdict, tldr, still_open, "
            "       studies_json, cited_json, created_at "
            "FROM verdict_cache ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        try:
            studies = json.loads(r["studies_json"] or "[]")
            cited = json.loads(r["cited_json"] or "[]")
        except (ValueError, TypeError):
            studies, cited = [], []
        out.append({"issue": r["issue"], "claim_text": r["claim_text"],
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
    studies blob along for the ride.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT rowid AS issue, claim_text, verdict FROM verdict_cache "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
