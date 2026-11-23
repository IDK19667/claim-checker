"""Record and replay for the answer engine, from outside it.

    import harness
    h = harness.install("record", "runs/store")   # or live, replay, fill
    ... run checks ...
    h.reset(); ...; print(h.snapshot())           # numbers for one check
    h.uninstall()

It wraps the two places the engine leaves the process, without editing a
line of it:
  - every model call: `verdict._gemini` is replaced by a function that
    hands the engine a stand-in client, whose `models.generate_content`
    is the one call behind `_gemini_text` and `_gemini_json`. One
    `_gemini()` is one logical call; each `generate_content` on it is one
    try (the engine's own retry loop makes the tries).
  - every PubMed request: `pubmed._paced_get`, which `pubmed._get` looks up
    by name on each call.

Modes:
  live    call through; count and time only, nothing saved or served.
  record  call through and save every successful answer.
  replay  serve saved answers only. A miss raises ReplayMiss, naming the
          call kind and key, and is listed in `misses`: never a live call.
  fill    replay; on a miss call through and save.

Key: sha256 of the canonical request. A model call: the model name, the
whole contents and the whole config (temperature, seed, max tokens, schema,
mime type and the rest). A PubMed request: the endpoint and its parameters
without `tool` and `email`. One JSON file per key under STORE/model/ and
STORE/pubmed/, written atomically, so two threads can save at once.

quota_log=PATH: every try of a live model call that fails with 429 adds one
JSON line there: the time, the model, the status, and what the error says
about the quota (metric, id, limit, retry delay, a short message). Never
the key, a request header or the prompt. The same lines are kept in
`quota_seen`, and the ones for the current check in snapshot()["quota"].

latency=True: a replay hit sleeps the recorded milliseconds, so two versions
of the code can be timed against each other without spending quota.

The numbers are process-wide (one lock), so read them for one check at a
time: reset, run the check, snapshot. Calls the engine makes from its own
worker threads during that check are counted too.
"""
import base64
import hashlib
import json
import os
import pathlib
import tempfile
import threading
import time

MODES = ("live", "record", "replay", "fill")
_DROP_PARAMS = ("tool", "email")


class ReplayMiss(Exception):
    """A replay found no saved answer for a call. Not a provider error on
    purpose: the engine's retry and fail-open paths do not swallow it as a
    passing fault, and the harness lists it in `misses` either way."""

    def __init__(self, kind, key, detail=""):
        super().__init__(f"replay miss: {kind} call, key {key}{' (' + detail + ')' if detail else ''}")
        self.kind, self.key, self.detail = kind, key, detail


def quota_info(e, model=None) -> dict:
    """What a 429 from the provider says about the quota it hit. Read from
    google.genai's APIError: `details` holds the response body, shaped like
    {"error": {"code", "message", "status", "details": [QuotaFailure with
    violations (quotaMetric, quotaId, quotaValue, quotaDimensions),
    RetryInfo with retryDelay, ...]}}. Anything missing is left out; a body
    of another shape gives just the time, model and status."""
    info = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model,
            "status": getattr(e, "code", None)}
    body = getattr(e, "details", None)
    err = body.get("error", body) if isinstance(body, dict) else {}
    err = err if isinstance(err, dict) else {}
    msg = getattr(e, "message", None) or err.get("message")
    if msg:
        info["message"] = str(msg)[:300]
    if err.get("status"):
        info["error_status"] = err["status"]
    quotas = []
    for d in err.get("details") or []:
        if not isinstance(d, dict):
            continue
        if d.get("retryDelay"):
            info["retry_delay"] = d["retryDelay"]
        for v in d.get("violations") or []:
            if isinstance(v, dict) and (v.get("quotaId") or v.get("quotaMetric")):
                q = {k: v[k] for k in ("quotaMetric", "quotaId", "quotaValue", "quotaDimensions") if k in v}
                q["kind"] = quota_kind(q)
                quotas.append(q)
    if quotas:
        info["quotas"] = quotas
    kinds = {q["kind"] for q in quotas}
    info["kind"] = "day" if "day" in kinds else ("minute" if "minute" in kinds else
                                                  (sorted(kinds)[0] if kinds else None))
    return info


def quota_kind(q: dict):
    text = f"{q.get('quotaId', '')} {q.get('quotaMetric', '')}".lower()
    if "perday" in text or "per_day" in text or "daily" in text:
        return "day"
    if "perminute" in text or "per_minute" in text:
        return "minute"
    return "other" if text.strip() else None


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_plain)


def _plain(o):
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json", exclude_none=True)
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, bytes):
        return base64.b64encode(o).decode()
    return str(o)


def model_request(kw: dict) -> dict:
    return {"model": kw.get("model"), "contents": kw.get("contents"),
            "config": json.loads(canonical(kw.get("config") or {}))}


def pubmed_request(endpoint: str, params: dict) -> dict:
    p = {k: v for k, v in (params or {}).items() if k not in _DROP_PARAMS}
    return {"endpoint": endpoint, "params": json.loads(canonical(p))}


def key_of(request: dict) -> str:
    return hashlib.sha256(canonical(request).encode("utf-8")).hexdigest()


def _usage(resp) -> tuple[int, int]:
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return 0, 0
    return ((getattr(u, "prompt_token_count", 0) or 0),
            (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0))


def dump_model_response(resp) -> dict:
    """The response as JSON, enough to rebuild the object the engine reads
    (text, candidates and finish reason, usage numbers)."""
    return json.loads(resp.model_dump_json(exclude_none=True))


def load_model_response(data: dict):
    from google.genai import types
    return types.GenerateContentResponse.model_validate_json(json.dumps(data))


def dump_pubmed_response(resp) -> dict:
    content = resp.content or b""
    try:
        body = {"text": content.decode("utf-8")}
    except UnicodeDecodeError:
        body = {"base64": base64.b64encode(content).decode()}
    return {"status_code": resp.status_code, "encoding": resp.encoding,
            "content_type": resp.headers.get("Content-Type", ""), **body}


def load_pubmed_response(data: dict, request: dict):
    import requests
    from requests.structures import CaseInsensitiveDict
    r = requests.Response()
    r.status_code = data["status_code"]
    r._content = data["text"].encode("utf-8") if "text" in data else base64.b64decode(data["base64"])
    r.encoding = data.get("encoding")
    r.headers = CaseInsensitiveDict({"Content-Type": data.get("content_type", "")})
    r.url = "replay://" + request["endpoint"]
    r.reason = "OK"
    return r


class Store:
    def __init__(self, root):
        self.root = pathlib.Path(root)

    def path(self, kind, key):
        return self.root / kind / f"{key}.json"

    def get(self, kind, key):
        p = self.path(kind, key)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def put(self, kind, key, record):
        p = self.path(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{key[:12]}.", suffix=".tmp", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False))
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


_ZERO = {"model_calls": 0, "model_tries": 0, "model_live": 0, "model_replayed": 0,
         "tokens_in": 0, "tokens_out": 0, "model_ms": 0,
         "pubmed_requests": 0, "pubmed_live": 0, "pubmed_replayed": 0, "pubmed_ms": 0,
         "saved": 0}


class Harness:
    def __init__(self, mode, store, latency=False, quota_log=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode != "live" and not store:
            raise ValueError("record, replay and fill need a store folder")
        self.mode, self.latency = mode, latency
        self.store = Store(store) if store else None
        self._lock = threading.Lock()
        self.reset()
        self.misses = []        # every miss since install, never reset
        self.quota_seen = []    # every 429 since install, never reset
        self.quota_log = pathlib.Path(quota_log) if quota_log else None
        self._orig_gemini = self._orig_paced = None

    # ---- numbers ------------------------------------------------------
    def reset(self):
        with self._lock:
            self._n = dict(_ZERO)
            self._check_misses = []
            self._check_quota = []

    def snapshot(self) -> dict:
        with self._lock:
            out = dict(self._n)
            out["misses"] = list(self._check_misses)
            out["quota"] = list(self._check_quota)
        out["model_ms"] = int(out["model_ms"]); out["pubmed_ms"] = int(out["pubmed_ms"])
        return out

    def _add(self, **kv):
        with self._lock:
            for k, v in kv.items():
                self._n[k] += v

    def _miss(self, kind, key, detail):
        m = {"kind": kind, "key": key, "detail": detail}
        with self._lock:
            self.misses.append(m); self._check_misses.append(m)
        raise ReplayMiss(kind, key, detail)

    def _serve(self, kind, key):
        """A saved record for a replay or fill, or None."""
        if self.mode in ("replay", "fill"):
            return self.store.get(kind, key)
        return None

    def _quota(self, info):
        with self._lock:
            self.quota_seen.append(info); self._check_quota.append(info)
            if self.quota_log:
                self.quota_log.parent.mkdir(parents=True, exist_ok=True)
                with self.quota_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(info, ensure_ascii=False) + "\n")

    def _sleep_recorded(self, rec):
        if self.latency:
            time.sleep((rec.get("ms") or 0) / 1000)

    # ---- model ----------------------------------------------------------
    def _generate(self, call, real, kw):
        """One try of one logical call. `call` holds the try count;
        `real()` gives the real client, made only when needed."""
        req = model_request(kw)
        key = key_of(req)
        with self._lock:
            call["tries"] += 1
            self._n["model_tries"] += 1
        rec = self._serve("model", key)
        if rec is not None:
            self._sleep_recorded(rec)
            resp = load_model_response(rec["response"])
            tin, tout = _usage(resp)
            self._add(model_replayed=1, tokens_in=tin, tokens_out=tout, model_ms=rec.get("ms") or 0)
            return resp
        if self.mode == "replay":
            self._miss("model", key, f"{req['model']}, {len(str(req['contents']))} chars")
        t0 = time.perf_counter()
        try:
            resp = real().models.generate_content(**kw)
        except Exception as e:
            if getattr(e, "code", None) == 429:
                self._quota(quota_info(e, req["model"]))
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000
            self._add(model_ms=ms, model_live=1)
        tin, tout = _usage(resp)
        self._add(tokens_in=tin, tokens_out=tout)
        if self.mode in ("record", "fill"):
            self.store.put("model", key, {"kind": "model", "key": key, "request": req,
                                          "response": dump_model_response(resp),
                                          "ms": round(ms, 1), "tries": call["tries"],
                                          "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._add(saved=1)
        return resp

    def _gemini(self):
        """Stands in for verdict._gemini: one invocation is one logical call."""
        self._add(model_calls=1)
        orig = self._orig_gemini
        if self.mode in ("live", "record"):
            client = orig()                   # same failure as before when no key is set
            real = lambda: client             # noqa: E731
        elif self.mode == "replay":
            def real():                       # a replay never builds a real client
                self._miss("model", "-", "the engine asked the client for something other than generate_content")
        else:
            cache = []
            def real():
                if not cache:
                    cache.append(orig())
                return cache[0]
        call = {"tries": 0}
        harness = self

        class _Models:
            def generate_content(self, **kw):
                return harness._generate(call, real, kw)

            def __getattr__(self, name):      # anything else the engine may ask for
                return getattr(real().models, name)

        class _Client:
            models = _Models()

            def __getattr__(self, name):
                return getattr(real(), name)

        return _Client()

    # ---- PubMed -----------------------------------------------------------
    def _paced_get(self, endpoint, params):
        req = pubmed_request(endpoint, params)
        key = key_of(req)
        self._add(pubmed_requests=1)
        rec = self._serve("pubmed", key)
        if rec is not None:
            self._sleep_recorded(rec)
            self._add(pubmed_replayed=1, pubmed_ms=rec.get("ms") or 0)
            return load_pubmed_response(rec["response"], req)
        if self.mode == "replay":
            self._miss("pubmed", key, endpoint)
        t0 = time.perf_counter()
        try:
            resp = self._orig_paced(endpoint, params)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            self._add(pubmed_ms=ms, pubmed_live=1)
        if self.mode in ("record", "fill") and 200 <= resp.status_code < 300:
            self.store.put("pubmed", key, {"kind": "pubmed", "key": key, "request": req,
                                           "response": dump_pubmed_response(resp),
                                           "ms": round(ms, 1), "tries": 1,
                                           "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._add(saved=1)
        return resp

    # ---- wiring -------------------------------------------------------------
    def _install(self):
        import pubmed
        import verdict
        self._orig_gemini, self._orig_paced = verdict._gemini, pubmed._paced_get
        verdict._gemini = self._gemini
        pubmed._paced_get = self._paced_get

    def uninstall(self):
        import pubmed
        import verdict
        if self._orig_gemini is not None:
            verdict._gemini, pubmed._paced_get = self._orig_gemini, self._orig_paced
            self._orig_gemini = self._orig_paced = None


_active = None


def install(mode, store=None, latency=False, quota_log=None) -> Harness:
    """Wrap the engine's model calls and PubMed requests. One at a time:
    a second install first removes the one before."""
    global _active
    if _active is not None:
        _active.uninstall()
    h = Harness(mode, store, latency, quota_log)
    h._install()
    _active = h
    return h


# ---- claims files -----------------------------------------------------------
def load_claims(path) -> list[dict]:
    """Items from a gold file (a list, or an object with `claims`) or from
    tests/regression_claims.json / tests/safety_claims.json, in one shape:
    id, claim, reference, also_ok, group, safety{warning_needed, harmful_if}."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    items = data["claims"] if isinstance(data, dict) else data
    out = []
    for i, it in enumerate(items, 1):
        cid = it.get("id") or str(it.get("n") or i)
        safety = dict(it.get("safety") or {})
        safety.setdefault("warning_needed", False)
        safety.setdefault("harmful_if", list(it.get("harmful") or []))
        out.append({"id": str(cid), "claim": it["claim"],
                    "reference": it.get("reference") or it.get("expected"),
                    "also_ok": list(it.get("also_ok") or []),
                    "group": it.get("group") or it.get("kind") or "",
                    "safety": safety})
    ids = [c["id"] for c in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: ids are not unique")
    return out
