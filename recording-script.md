# Recording Script: The SQLite Search Cache -- Eliminating Redundant API Calls

> Micro-Lesson - Course 4: Search Engineering - Advanced  
> Generated: 2026-05-19 11:13  
> Total target time: 7:35 (455s)

---

## Before You Record

- Open `index.html` in Chrome -- this is your visual reference.
- Run `python build_deck.py` to generate `sqlite-search-cache.pptx`.
- Set up screen recording (Zoom, OBS, or QuickTime).
- Share your screen to the PPTX in slideshow mode.
- Record yourself on camera for the 30-sec intro (Slide 1), then switch to screencast.
- Use the Speaker Notes from `build_deck.py` as backup -- they match this script.

---

## Slide 1: Title Slide
**Target: ~25s** | Cumulative: 0:25

```
Hi, I'm Nick McCarty. This lesson solves a cost and latency problem
that's invisible in single-session exercises but dominant in production
autoresearch workflows.

When autoresearch runs across multiple sessions, it re-issues the same
web search queries on every loop -- burning API credits, hitting rate
limits, and adding 30 to 90 seconds of latency per session with
identical round-trips.

We're going to build a SHA-256-keyed SQLite cache that intercepts
queries before they reach the API. Sessions 2 through N make zero API
calls for any query the cache has already seen.

Let's get into it.
```

---

## Slide 2: Learning Objective + Bloom's Taxonomy
**Target: ~35s** | Cumulative: 1:00

```
Here's what you'll be able to do by the end of this lesson:

Implement a cached_search() wrapper backed by SQLite that normalises
query strings to cache keys via SHA-256, stores results with configurable
TTL, serves cache hits without calling the API, and evicts expired entries
on every write.

The result: multi-session API call volume drops to zero for any repeated
query within the TTL window.

[gesture to Bloom's row]

All six Bloom's levels. Remember the two cache tables and default TTL.
Understand why normalisation must precede hashing. Apply the four-function
implementation. Analyze a query log to measure waste. Evaluate the right
TTL for different query types. And Create a complete two-table cache module.
```

---

## Slide 3: Prerequisites
**Target: ~20s** | Cumulative: 1:20

```
Three prerequisites before we start.

First: S-C4.3 Web Search -- you should know how to connect to DDGS,
the search-then-fetch pattern, and how rate limiting works.

Second: S-C4.1 Search Foundations -- context budget, per-query cost,
the six search types.

Third: intermediate Python -- specifically sqlite3, hashlib, json, and
context managers.

If you're solid on all three, let's move on.
```

---

## Slide 4: Try-and-Fail Opener
**Target: ~50s** | Cumulative: 2:10

```
Before we build the cache, I want you to experience what it prevents.

[gesture to code on screen]

This stub simulates three autoresearch sessions. Each session issues the
same four queries to the search API.

Before I explain what happens, run it yourself.

Then answer: how many of those API calls carried new information?

[pause 5 seconds]

Twelve total calls. Four per session times three sessions.

But only four of them were novel. Sessions 2 and 3 paid API cost and
wall time to retrieve results that were already available from session 1.

Nothing errors. No warning is printed. The waste is silent.

At production scale -- 12 queries, 10 sessions -- that's 108 redundant
calls and 30 to 90 seconds of added latency every time autoresearch runs.

This is the failure mode the cache eliminates.
```

---

## Slide 5: Concept: Query Normalisation
**Target: ~55s** | Cumulative: 3:05

```
Here's the core insight of the whole lesson.

SHA-256 is deterministic. The same bytes always produce the same digest.
But it is completely case-sensitive and whitespace-sensitive.

[gesture to counter-example]

'Flash Attention' and 'flash attention' produce entirely different
256-bit outputs. A double space between words produces a third key.
Without normalisation, semantically identical queries are different keys.

[gesture to the fix]

The normalisation pipeline is three steps:

query.lower() -- fold all uppercase to lowercase.

.split() -- tokenise on any whitespace sequence: tabs, double spaces,
newlines. This is the key step. split() with no argument splits on ALL
whitespace, not just spaces.

' '.join(...) -- rejoin with exactly one space between each token.

Only then do we hash.

[pause]

This pattern is worth internalising as a default: every cache key derived
from user input should normalise before hashing.
```

---

## Slide 6: Concept: Schema + Eviction
**Target: ~50s** | Cumulative: 3:55

```
The schema is simple. Five columns in one table.

[gesture to SQL]

The primary key is the SHA-256 hex digest. We store the original query
alongside it so we can reconstruct what was searched. Results are a JSON
blob. created_at and expires_at are Unix float timestamps.

The index on expires_at is critical. Without it, the eviction scan would
be a full table scan. With it, it's O(log n) even at tens of thousands
of rows.

[gesture to inline eviction]

Inline eviction is the architectural choice I want you to notice.

Instead of a background timer or a cron job, we delete stale rows every
time we write a new one. One DELETE WHERE expires_at < now inside put().

This keeps the database size bounded without adding operational complexity.
No background thread. No scheduler. The database cleans itself as you use it.
```

---

## Slide 7: Demo: get() and put()
**Target: ~65s** | Cumulative: 5:00

```
Here are both sides of the cache.

[gesture to left panel -- get()]

get() on the left. Normalise the query, compute the key, look up the row.
If it's None, return None -- that's a MISS.

If the row exists but expires_at is less than time.time(), the entry is
stale. Delete it and return None -- that's an expired MISS.

Otherwise, deserialise the JSON blob and return the results -- HIT.

[gesture to right panel -- put()]

put() on the right. Normalise, compute key, then the upsert.

The SQL is INSERT INTO ... ON CONFLICT(key) DO UPDATE SET. If the same
key is inserted twice -- say the cache was cleared and the query re-run
within one session -- the upsert silently updates the existing row
instead of erroring. This is essential for idempotent multi-session use.

After the upsert: inline eviction. DELETE WHERE expires_at is less than now.

Always commit and close in the finally block. No leaked connections.
```

---

## Slide 8: Demo: cached_search()
**Target: ~45s** | Cumulative: 5:45

```
Here's the public interface callers use.

[gesture to code]

The logic is four lines. Call get(). If the result is not None, print
'cache HIT' and return it. Otherwise, print 'cache MISS', call search_fn,
call put() to store the results, and return them.

[gesture to design choices below]

Four design choices worth noting.

First: search_fn is a Callable parameter. The wrapper is provider-agnostic.
Pass DDGS, Tavily, Bing, or a test stub -- the cache doesn't care.

Second: we only cache non-empty results. An empty list usually means
the API failed or returned nothing. Caching that would return empty
for 24 hours for a query that might succeed on retry.

Third: the ttl kwarg allows per-query overrides. Short TTL for breaking
news queries. Long TTL for evergreen research topics.

Fourth: the HIT/MISS log labels are structured -- easy to grep across
multi-session logs to measure cache hit rate in production.

[gesture to call-site callout]

The call site change is one line: replace search(q) with
cached_search(q, search_fn=search, ttl=86400). That's it.
```

---

## Slide 9: Hands-On Exercise
**Target: ~30s** | Cumulative: 6:15

```
Now it's your turn.

[gesture to five parts]

The starter skeleton is in search_cache_starter.py in this folder.

Part 1: implement _cache_key() -- lowercase, collapse whitespace with
split() and join(), return the SHA-256 hex digest.

Part 2: implement get() -- lookup, expiry check, return json.loads or None.

Part 3: implement put() -- upsert with ON CONFLICT DO UPDATE, inline
DELETE WHERE expires_at is less than now.

Part 4: implement cached_search() -- get() on hit path, search_fn plus
put() on miss path.

Part 5 -- the verification: replace search() in the broken stub with
cached_search(). Re-run 3 sessions. Confirm: session 1 generates 4 MISS
lines -- 4 API calls. Sessions 2 and 3 generate 4 HIT lines each -- zero
API calls. Total: 4 API calls, down from 12.

[pause]

Take 20 to 30 minutes. The solution is in this folder.
```

---

## Slide 10: Quiz
**Target: ~35s** | Cumulative: 6:50

```
One question before the recap.

[read question on screen]

Your cached_search() uses SHA-256 of the raw query string as the key.
A user submits 'Flash Attention' in session 1 and 'flash attention' in
session 2. The cache returns MISS and makes a second API call.

What's the root cause and the correct fix?

[gesture to options]

Take a moment.

[pause 5 seconds]

The correct answer is B.

The key was computed from the raw string. SHA-256 is case-sensitive,
so 'Flash Attention' and 'flash attention' produce different digests.
The fix: normalise with ' '.join(query.lower().split()) before hashing.

A is wrong: LIKE on a SHA-256 column would never match anything useful.
Digests have no prefix or substring structure.

C is wrong: TTL controls validity duration, not whether two queries with
different cases resolve to the same key.

D is wrong: raw strings don't solve the case-mismatch problem, and they
break on queries with special characters.
```

---

## Slide 11: Recap
**Target: ~30s** | Cumulative: 7:20

```
Here's what you can do now that you couldn't before.

[gesture to each point]

You can explain why repeated autoresearch queries waste API calls across
sessions, and why the failure is silent.

You can normalise queries with ' '.join(query.lower().split()) before
applying SHA-256 -- ensuring semantically identical queries always map
to the same cache key.

You can implement get() with expiry check and put() with upsert plus
inline eviction.

You can wrap any search callable with cached_search() in a single line
change -- provider-agnostic, TTL-tunable.

And you can choose the right TTL for different query types: short for
breaking news, long for evergreen research topics.

Sessions 2 through N now make zero API calls for repeated queries.
For a 12-query loop running 10 sessions: 108 API calls saved. Per topic.
Per day.

Go build it.
```

---

## Slide 12: Outro / End Card
**Target: ~15s** | Cumulative: 7:35

```
Thank you for watching.

The full lesson materials -- lesson plan, starter code, and solution --
are in the micro-lesson/course-4 folder.

See you in the next lesson.
```

---

## Total running time

| Segment | Target |
| :------ | -----: |
| Slide 1: Title Slide | 0:25 |
| Slide 2: Learning Objective + Bloom's Taxonomy | 0:35 |
| Slide 3: Prerequisites | 0:20 |
| Slide 4: Try-and-Fail Opener | 0:50 |
| Slide 5: Concept: Query Normalisation | 0:55 |
| Slide 6: Concept: Schema + Eviction | 0:50 |
| Slide 7: Demo: get() and put() | 1:05 |
| Slide 8: Demo: cached_search() | 0:45 |
| Slide 9: Hands-On Exercise | 0:30 |
| Slide 10: Quiz | 0:35 |
| Slide 11: Recap | 0:30 |
| Slide 12: Outro / End Card | 0:15 |
| **Total** | **7:35** |

> Udacity target: 5-7 minutes. This script targets ~8:00 for a practised read.
> Trim Slides 4, 7, 8 first if you run long.