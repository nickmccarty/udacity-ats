# MICRO-LESSON PLAN — (Agentic AI) Harness Engineering

## Author: Nick McCarty

| **Lesson title** | The SQLite Search Cache: Eliminating Redundant API Calls in Multi-Session Research |
| :---- | :---- |
| **Difficulty level** | Advanced |
| **Lesson type** | Part of a longer lesson (extends S-C4.3 / S-I4.3 — Web Search and S-C4.8 / S-I4.8 — Search Orchestration) |
| **Lesson domain/topic** | Agentic AI / Harness Engineering — Search Engineering for AI Agents |
| **Description** | When autoresearch runs across multiple sessions, it re-issues the same web search queries on every loop — burning API credits, hitting rate limits, and inflating wall time with identical round-trips. This lesson teaches a production fix: a SHA-256-keyed SQLite cache with configurable TTL that intercepts queries before they reach the API, stores results on miss, and serves them from disk on hit. Learners see the redundancy failure directly in autoresearch logs, implement the `cached_search()` wrapper from a starter skeleton, and verify hit/miss behaviour across simulated multi-session runs. A second table — the research context cache — extends the pattern to full `gather_research()` outputs, reducing session startup from minutes to milliseconds. |
| **Prerequisite skills** | S-C4.3 / S-I4.3 — Web Search: connecting to a search API (DDGS), the search-then-fetch pattern, rate limiting. S-C4.1 — Search Foundations: context budget, per-query cost, the six search types. Intermediate Python: `sqlite3`, `hashlib`, `json`, context managers. |
| **Skill taught in the lesson** | Building a SHA-256-keyed SQLite search cache with TTL expiry and upsert semantics that wraps any search callable — eliminating redundant API calls across multi-session autoresearch runs. |
| **Learning Objective** | By the end of the lesson, the student should be able to implement a `cached_search()` wrapper backed by a SQLite database that normalises query strings to cache keys via SHA-256, stores results with configurable TTL, serves cache hits without calling the API, and evicts expired entries on every write — reducing multi-session API call volume to zero for repeated queries within the TTL window. |

---

## Curriculum Alignment — Course 4 Scope

This lesson is additive to **S-C4.3 / S-I4.3 — Web Search** and **S-C4.8 / S-I4.8 — Search Orchestration and the Agentic Search Loop**. The Course 4 scope teaches search-then-fetch, query engineering, rate limiting defence, and multi-source routing. It does not address query-level result persistence across sessions — a cost and reliability concern that is invisible in single-session exercises but dominant in production multi-session workflows like autoresearch.

The lesson also connects to **S-C4.6 — Memory Search** by showing how the same caching architecture scales from individual queries to full research contexts (`research_cache` table), illustrating that session memory and search caching are the same architectural pattern at different granularities.

---

## Bloom's Taxonomy Alignment

| Bloom's Level | What the Learner Does in This Lesson |
| :---- | :---- |
| **Remember** | Recall the two cache tables (`search_cache`, `research_cache`), the default TTL (86,400s), and the normalisation steps applied before hashing (lowercase + whitespace collapse). |
| **Understand** | Explain why query normalisation must happen before hashing (case variants and extra spaces produce different SHA-256 hashes for semantically identical queries); explain why TTL eviction happens at write time rather than on a background timer. |
| **Apply** | Implement `cached_search()` from the starter skeleton: normalise → hash → get → if None: call API → put; implement `put()` with upsert and inline expired-entry eviction. |
| **Analyze** | Trace an autoresearch run's query log and identify which queries are repeated across sessions; calculate the API call reduction after the cache is added. |
| **Evaluate** | Assess the appropriate TTL for different query types (breaking news vs. evergreen research topics); decide when a cache hit should be invalidated manually versus allowed to expire naturally. |
| **Create** | Build a complete two-table cache module with `stats()` and `clear_expired()` management helpers, integrate it into a mock autoresearch loop, and produce a before/after API call count comparison. |

---

## Try-and-Fail Opener (5 minutes)

**Setup — give the learner this instrumented autoresearch stub:**

```python
# starter/autoresearch_broken.py
import time
from ddgs import DDGS

api_calls = 0

def search(query: str, max_results: int = 10) -> list[dict]:
    global api_calls
    api_calls += 1
    print(f"[API CALL #{api_calls}] {query[:60]}")
    return list(DDGS().text(query, max_results=max_results))

# Simulates 3 autoresearch sessions, each issuing the same 4 queries
QUERIES = [
    "transformer attention mechanisms survey",
    "KV cache optimization techniques",
    "flash attention implementation pytorch",
    "multi-head latent attention MLA",
]

for session in range(1, 4):
    print(f"\n=== Session {session} ===")
    results = []
    for q in QUERIES:
        results.extend(search(q))
    time.sleep(0.5)

print(f"\nTotal API calls: {api_calls}")   # -> 12
print(f"Unique queries:  {len(QUERIES)}")  # ->  4
```

**Exercise:** Run this. Note the total API call count and the wall time. Then answer: how many of those calls carried new information?

**Expected failure:** 12 API calls total — 4 per session × 3 sessions. Sessions 2 and 3 issue identical queries to the same search API, returning (nearly) identical results, burning 8 calls that deliver no new information. In a real autoresearch run with 12 queries per session and 10+ sessions, this produces 108+ redundant API calls, accumulates rate-limit pressure, and adds 30–90 seconds of latency per session. The failure is silent: nothing errors, no warning is printed, the results are simply re-fetched and discarded after context deduplication.

---

## Hands-On Exercise (20–30 minutes)

| **Scenario** | |
| :---- | ----- |
| Your autoresearch loop runs overnight — 15 sessions, 12 queries each, 180 total API calls. After inspecting the query log you find that 8 of the 12 queries are identical across all 15 sessions. That is 105 redundant calls. You need a SQLite-backed cache that intercepts queries before they reach the DDGS API, stores results on miss, and serves them from disk on hit — with a 24-hour TTL so stale results expire automatically. | |
| **Instructions** | |
| **Part 1 — Implement `_cache_key(query)`.**  Lowercase the query, collapse all whitespace to single spaces with `split()`/`join()`, and return the SHA-256 hex digest. This normalisation ensures "Flash Attention" and "flash  attention" map to the same key. **Part 2 — Implement `get(query)`.**  Connect to the SQLite database, look up the key, check `expires_at < time.time()` (delete and return None if expired), and return `json.loads(results_json)` on hit. **Part 3 — Implement `put(query, results, ttl)`.**  Use `INSERT INTO ... ON CONFLICT(key) DO UPDATE SET ...` (upsert) so re-running a search on a cache miss doesn't error. Set `expires_at = time.time() + ttl`. Inline `DELETE FROM search_cache WHERE expires_at < now` to evict stale rows on every write — no background timer needed. **Part 4 — Implement `cached_search(query, search_fn, ttl, max_results)`.**  Check get(); if not None, print `[cache HIT]` and return. Otherwise print `[cache MISS]`, call `search_fn(query, max_results)`, call `put()`, and return results. **Part 5 — Verify.**  Replace `search()` in the autoresearch stub with `cached_search()`. Re-run 3 sessions. Confirm: session 1 generates 4 MISS lines (4 API calls); sessions 2 and 3 generate 4 HIT lines each (0 API calls). Total API calls: 4. | |
| **Starter code** | See `starter/search_cache_starter.py` below. | |
| **Solution** | See `solution/search_cache.py` below. | |

### Starter Code

```python
# starter/search_cache_starter.py
import hashlib, json, os, sqlite3, time
from collections.abc import Callable

DB_PATH     = "search_cache.db"
DEFAULT_TTL = 86_400   # 24 hours


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            key        TEXT PRIMARY KEY,
            query      TEXT NOT NULL,
            results    TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expires ON search_cache(expires_at)"
    )
    conn.commit()
    return conn


def _cache_key(query: str) -> str:
    # TODO: lowercase, collapse whitespace, return SHA-256 hex digest
    pass


def get(query: str) -> list[dict] | None:
    # TODO: lookup key, check expiry, return json.loads(results_json) or None
    pass


def put(query: str, results: list[dict], ttl: int = DEFAULT_TTL) -> None:
    # TODO: upsert with ON CONFLICT(key) DO UPDATE
    # TODO: inline eviction: DELETE WHERE expires_at < now
    pass


def cached_search(
    query: str,
    search_fn: Callable[[str, int], list[dict]],
    ttl: int = DEFAULT_TTL,
    max_results: int = 10,
) -> list[dict]:
    # TODO: get() -> HIT path; search_fn() + put() -> MISS path
    pass
```

### Solution

```python
# solution/search_cache.py
import hashlib, json, os, sqlite3, time
from collections.abc import Callable

DB_PATH     = os.environ.get("SEARCH_CACHE_DB", "search_cache.db")
DEFAULT_TTL = 86_400   # 24 hours


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            key        TEXT PRIMARY KEY,
            query      TEXT NOT NULL,
            results    TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expires ON search_cache(expires_at)"
    )
    conn.commit()
    return conn


def _cache_key(query: str) -> str:
    normalised = " ".join(query.lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()


def get(query: str) -> list[dict] | None:
    key  = _cache_key(query)
    now  = time.time()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT results, expires_at FROM search_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        results_json, expires_at = row
        if expires_at < now:
            conn.execute("DELETE FROM search_cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return json.loads(results_json)
    finally:
        conn.close()


def put(query: str, results: list[dict], ttl: int = DEFAULT_TTL) -> None:
    key  = _cache_key(query)
    now  = time.time()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO search_cache (key, query, results, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                results    = excluded.results,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (key, query, json.dumps(results, ensure_ascii=False), now, now + ttl),
        )
        conn.execute("DELETE FROM search_cache WHERE expires_at < ?", (now,))
        conn.commit()
    finally:
        conn.close()


def cached_search(
    query: str,
    search_fn: Callable[[str, int], list[dict]],
    ttl: int = DEFAULT_TTL,
    max_results: int = 10,
) -> list[dict]:
    cached = get(query)
    if cached is not None:
        print(f"[cache HIT ] {query[:60]}")
        return cached
    print(f"[cache MISS] {query[:60]}")
    results = search_fn(query, max_results)
    if results:
        put(query, results, ttl=ttl)
    return results


def stats() -> dict:
    now  = time.time()
    conn = _connect()
    try:
        total   = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM search_cache WHERE expires_at < ?", (now,)
        ).fetchone()[0]
        size_kb = os.path.getsize(DB_PATH) // 1024 if os.path.exists(DB_PATH) else 0
        return {"total": total, "expired": expired, "live": total - expired,
                "size_kb": size_kb}
    finally:
        conn.close()


def clear_expired() -> int:
    now  = time.time()
    conn = _connect()
    try:
        n = conn.execute(
            "DELETE FROM search_cache WHERE expires_at < ?", (now,)
        ).rowcount
        conn.commit()
        return n
    finally:
        conn.close()
```

---

## Multiple-Choice Quiz Question

| **Prompt** | |
| :---- | :---- |
| Your `cached_search()` implementation uses SHA-256 of the raw query string as the cache key. A user submits "Flash Attention" in session 1 and "flash attention" in session 2. The cache returns a MISS in session 2 and makes a second API call. What is the root cause and the correct fix? | |
| **Correct Answer** | **Feedback if this response is NOT chosen** |
| The key was computed from the raw string, so case variation produced a different hash. The fix is to normalise the query before hashing: lowercase and collapse whitespace with `" ".join(query.lower().split())`, then pass the result to `hashlib.sha256()`. | This is the root cause. SHA-256 is deterministic but case-sensitive: "Flash" and "flash" produce completely different 256-bit digests. Query normalisation — lowercase + whitespace collapse — must happen before hashing so that semantically equivalent queries always resolve to the same cache key. |
| **Incorrect Answers** | **Feedback if this response IS chosen** |
| The SQLite `LIKE` operator is case-insensitive; use it instead of `WHERE key = ?` for the lookup. | `LIKE` comparisons on a SHA-256 hash column would never match anything useful — SHA-256 digests do not have meaningful prefix or substring structure. The fix belongs before hashing, not in the SQL query. |
| Increase the TTL so the second session is more likely to find a live entry. | TTL controls how long a cached result stays valid, not whether two queries with different cases resolve to the same key. A longer TTL would not help here: session 2 would still compute a different hash and hit the API regardless of how long session 1's result has been stored. |
| Store the raw query string as the key instead of a hash. | Storing raw strings as keys breaks on queries containing special characters and makes key-length unbounded. More importantly, it does not solve the case-mismatch problem — "Flash Attention" and "flash attention" are still different strings. Normalisation before hashing is the correct approach. |
| **Feedback when the quiz is correctly solved** | |
| Correct. Normalisation before hashing is the key insight: `" ".join(query.lower().split())` collapses all case and whitespace variations to a canonical form before SHA-256 is applied, guaranteeing that semantically identical queries always resolve to the same cache entry. This pattern appears in production caches across many domains — it is worth internalising as a default. | |

---

## Curriculum Gap Justification

**Course 4 scope reference:** S-C4.3 teaches web search integration, query engineering, and rate limiting defence. S-C4.8 covers multi-source search orchestration and search budgeting. Neither addresses query-level result persistence across sessions.

In single-session exercises, search result reuse is invisible — each session starts fresh and the cost of redundant queries is confined to that session. In multi-session workflows like autoresearch, the cost is multiplicative: a 12-query session run 10 times produces 120 API calls, of which 108 are typically redundant. Rate-limit pressure accumulates across sessions, not just within one.

The production harness addresses this with two cache tables: `search_cache` (per-query results, always active) and `research_cache` (full `gather_research()` output, opt-in via `RESEARCH_CACHE=1`). The second table reduces autoresearch session startup from 3–8 minutes of search round-trips to a single SQLite lookup — a latency reduction that compounds across every subsequent session on the same topic.

The `_migrate()` helper in the production implementation — which adds missing columns to existing tables without dropping them — is itself a lesson in production database engineering that complements the schema design taught in S-C4.

---

## Slide Deck and Videos

| **Slide Deck** (Google Slides link) | *(to be added)* |
| :---- | :---- |
| **Lesson Video** (5-7 min screencast) | *(to be added)* |
| **Instructor Intro Video** (30 sec, on camera) | *(to be added)* |
