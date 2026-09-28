"""
In-memory sliding-window rate limiting for the checker endpoint.

Two layers:
  - per student (or per IP when no student is selected), so one person
    can't hammer the free tier on everyone else's behalf
  - global, so the whole app stays under the provider's free-tier
    ceiling no matter how many students are online

Each check costs up to 2 AI calls, so the global limits are expressed
in *checks*, and should be set to roughly half the provider's request
limit. Cached results never touch the limiter — they're free.

In-memory is fine for a single-process local pilot. If this ever runs
behind multiple worker processes, move the counters to SQLite or Redis.
"""

import os
import threading
import time
from collections import defaultdict, deque


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class SlidingWindow:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """
        Returns (allowed, retry_after_seconds). Records the hit if allowed.
        A limit <= 0 disables this window.
        """
        if self.limit <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(q[0] + self.window - now) + 1)
            q.append(now)
            return True, 0


# Defaults sized for Gemini Flash-Lite's free tier (~30 req/min, ~1000/day
# as of 2026 — Google now shows the exact numbers only inside AI Studio).
# Each check = up to 2 requests, hence roughly half of those.
PER_USER = SlidingWindow(
    limit=_env_int("RATE_LIMIT_PER_USER", 6),
    window_seconds=_env_int("RATE_LIMIT_PER_USER_WINDOW_SEC", 600),
)
GLOBAL_MINUTE = SlidingWindow(
    limit=_env_int("RATE_LIMIT_GLOBAL_PER_MINUTE", 12),
    window_seconds=60,
)
GLOBAL_DAY = SlidingWindow(
    limit=_env_int("RATE_LIMIT_GLOBAL_PER_DAY", 400),
    window_seconds=86400,
)


def check(user_key: str) -> tuple[bool, str | None, int]:
    """
    Returns (allowed, message, retry_after_seconds).
    Order: global-day, global-minute, then per-user, so a single blocked
    user doesn't burn a slot in the global windows.
    """
    ok, wait = GLOBAL_DAY.check("global")
    if not ok:
        return False, ("The checker has hit its daily limit on the free AI tier. "
                       "It resets in about "
                       f"{max(1, wait // 3600)} hour(s)."), wait
    ok, wait = GLOBAL_MINUTE.check("global")
    if not ok:
        return False, ("Lots of people are checking claims right now. "
                       f"Try again in {wait} seconds."), wait
    ok, wait = PER_USER.check(user_key)
    if not ok:
        return False, (f"You've checked several claims in a row. "
                       f"Take a breather, try again in {wait} seconds."), wait
    return True, None, 0

# Feedback is cheap to store but shouldn't be floodable.
FEEDBACK = SlidingWindow(limit=_env_int("RATE_LIMIT_FEEDBACK_PER_HOUR", 30), window_seconds=3600)

# The deep dive costs NCBI three link calls, not an AI call, so it can be
# far looser than a check. It is still capped: PubMed is a shared public
# service and we are a guest on it.
DEEP_DIVE = SlidingWindow(limit=_env_int("RATE_LIMIT_DEEP_DIVE_PER_MINUTE", 30),
                          window_seconds=60)
