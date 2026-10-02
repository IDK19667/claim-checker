"""
SQLite storage. Two tables:

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
"""

import json
import os
import re
import sqlite3
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


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
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


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---- Verdict cache -------------------------------------------------------

def normalize_claim(claim: str) -> str:
    """Case/whitespace/trailing-punctuation insensitive key for a claim."""
    key = claim.strip().lower()
    key = re.sub(r"\s+", " ", key)
    key = key.rstrip(" .!?")
    return key


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


def recent_cached(limit: int = 200) -> list[dict]:
    """Cached claims, newest first. Used for the sitemap and llms.txt."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT claim_text, verdict, created_at FROM verdict_cache "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


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

def add_feedback(claim: str, verdict: str | None, helpful: bool, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback (claim_key, verdict, helpful, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (normalize_claim(claim), verdict, int(bool(helpful)), (note or "")[:300], _now()),
        )


def recent_checks(limit: int = 50) -> list[dict]:
    """Newest first. Not exposed publicly yet; here for ops/debugging."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, claim_text, verdict, cached, checked_at FROM checks "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def ledger() -> dict:
    """
    Real counts for the front page. Every number here is a fact about this
    database, never a rounded-up impression of one: `studies_read` counts
    distinct PubMed records actually fetched and shown, not fetch attempts.
    """
    with get_conn() as conn:
        claims = conn.execute("SELECT COUNT(*) n FROM verdict_cache").fetchone()["n"]
        checks = conn.execute("SELECT COUNT(*) n FROM checks").fetchone()["n"]
        rows = conn.execute(
            "SELECT studies_json FROM verdict_cache WHERE studies_json IS NOT NULL"
        ).fetchall()
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
