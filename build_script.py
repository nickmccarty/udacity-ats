"""
build_script.py -- generate a recording teleprompter script for the
SQLite Search Cache micro-lesson, including slide cues, timing targets,
and presenter notes formatted for easy reading on screen.

Run:
    python build_script.py
Output:
    recording-script.md   -- markdown version
    recording-script.html -- standalone HTML teleprompter
"""

from pathlib import Path
from datetime import datetime

HERE = Path(__file__).parent

# -- Slide data ---------------------------------------------------------------
# Each entry: (slide_num, slide_title, target_seconds, script_text)
SLIDES = [
    (
        1, "Title Slide", 25,
        """\
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

Let's get into it."""
    ),
    (
        2, "Learning Objective + Bloom's Taxonomy", 35,
        """\
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
TTL for different query types. And Create a complete two-table cache module."""
    ),
    (
        3, "Prerequisites", 20,
        """\
Three prerequisites before we start.

First: S-C4.3 Web Search -- you should know how to connect to DDGS,
the search-then-fetch pattern, and how rate limiting works.

Second: S-C4.1 Search Foundations -- context budget, per-query cost,
the six search types.

Third: intermediate Python -- specifically sqlite3, hashlib, json, and
context managers.

If you're solid on all three, let's move on."""
    ),
    (
        4, "Try-and-Fail Opener", 50,
        """\
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

This is the failure mode the cache eliminates."""
    ),
    (
        5, "Concept: Query Normalisation", 55,
        """\
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
from user input should normalise before hashing."""
    ),
    (
        6, "Concept: Schema + Eviction", 50,
        """\
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
No background thread. No scheduler. The database cleans itself as you use it."""
    ),
    (
        7, "Demo: get() and put()", 65,
        """\
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

Always commit and close in the finally block. No leaked connections."""
    ),
    (
        8, "Demo: cached_search()", 45,
        """\
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
cached_search(q, search_fn=search, ttl=86400). That's it."""
    ),
    (
        9, "Hands-On Exercise", 30,
        """\
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

Take 20 to 30 minutes. The solution is in this folder."""
    ),
    (
        10, "Quiz", 35,
        """\
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
break on queries with special characters."""
    ),
    (
        11, "Recap", 30,
        """\
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

Go build it."""
    ),
    (
        12, "Outro / End Card", 15,
        """\
Thank you for watching.

The full lesson materials -- lesson plan, starter code, and solution --
are in the micro-lesson/course-4 folder.

See you in the next lesson."""
    ),
]


# -- Helpers ------------------------------------------------------------------

def fmt_time(seconds: int) -> str:
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


def total_time() -> int:
    return sum(s[2] for s in SLIDES)


# -- Markdown output ----------------------------------------------------------

def build_markdown() -> str:
    total = total_time()
    lines = [
        "# Recording Script: The SQLite Search Cache -- Eliminating Redundant API Calls",
        "",
        f"> Micro-Lesson - Course 4: Search Engineering - Advanced  ",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"> Total target time: {fmt_time(total)} ({total}s)",
        "",
        "---",
        "",
        "## Before You Record",
        "",
        "- Open `index.html` in Chrome -- this is your visual reference.",
        "- Run `python build_deck.py` to generate `sqlite-search-cache.pptx`.",
        "- Set up screen recording (Zoom, OBS, or QuickTime).",
        "- Share your screen to the PPTX in slideshow mode.",
        "- Record yourself on camera for the 30-sec intro (Slide 1), then switch to screencast.",
        "- Use the Speaker Notes from `build_deck.py` as backup -- they match this script.",
        "",
        "---",
        "",
    ]

    cumulative = 0
    for slide_num, slide_title, target_s, script in SLIDES:
        cumulative += target_s
        lines += [
            f"## Slide {slide_num}: {slide_title}",
            f"**Target: ~{target_s}s** | Cumulative: {fmt_time(cumulative)}",
            "",
            "```",
            script.strip(),
            "```",
            "",
            "---",
            "",
        ]

    lines += [
        "## Total running time",
        "",
        "| Segment | Target |",
        "| :------ | -----: |",
    ]
    cumulative = 0
    for slide_num, slide_title, target_s, _ in SLIDES:
        cumulative += target_s
        lines.append(f"| Slide {slide_num}: {slide_title} | {fmt_time(target_s)} |")
    lines += [
        f"| **Total** | **{fmt_time(total)}** |",
        "",
        "> Udacity target: 5-7 minutes. This script targets ~8:00 for a practised read.",
        "> Trim Slides 4, 7, 8 first if you run long.",
    ]
    return "\n".join(lines)


# -- HTML teleprompter --------------------------------------------------------

def build_html() -> str:
    total = total_time()
    slides_html = []
    cumulative = 0
    for slide_num, slide_title, target_s, script in SLIDES:
        cumulative += target_s
        escaped = (script.strip()
                   .replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))
        slides_html.append(f"""
  <div class="slide" id="s{slide_num}">
    <div class="slide-header">
      <span class="slide-num">Slide {slide_num}</span>
      <span class="slide-title">{slide_title}</span>
      <span class="slide-time">~{target_s}s | cumulative {fmt_time(cumulative)}</span>
    </div>
    <div class="script-body">{escaped}</div>
    <div class="slide-nav">
      {"" if slide_num == 1 else f'<button onclick="goto({slide_num-1})">&#8592; Prev</button>'}
      {"" if slide_num == len(SLIDES) else f'<button onclick="goto({slide_num+1})">Next &#8594;</button>'}
    </div>
  </div>""")

    toc_items = "\n".join(
        f'    <li><a href="#s{n}" onclick="goto({n}); return false;">'
        f'<span class="toc-num">{n}</span> {t} <em>~{s}s</em></a></li>'
        for n, t, s, _ in SLIDES
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Recording Script -- SQLite Search Cache</title>
  <style>
    :root {{
      --bg: #0f141a; --surface: #141c28; --card: #192236;
      --text: #dde8f4; --muted: #8ab4cc; --faint: #4d6f87;
      --accent: #1776a4; --teal: #00C5A1;
      --border: rgba(99,170,201,0.18);
    }}
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    html{{font-size:16px;scroll-behavior:smooth}}
    body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;min-height:100vh}}
    .sidebar{{width:260px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);padding:24px 16px;position:sticky;top:0;height:100vh;overflow-y:auto}}
    .sidebar h2{{font-size:.8rem;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:12px}}
    .sidebar ol{{padding-left:0;list-style:none;display:flex;flex-direction:column;gap:4px}}
    .sidebar li a{{display:flex;gap:8px;align-items:flex-start;padding:7px 10px;border-radius:6px;color:var(--muted);font-size:.82rem;text-decoration:none;transition:background .15s}}
    .sidebar li a:hover{{background:var(--card);color:var(--text)}}
    .toc-num{{min-width:20px;font-weight:700;color:var(--accent)}}
    .sidebar em{{font-size:.75rem;color:var(--faint)}}
    .timer-bar{{background:var(--card);border-bottom:1px solid var(--border);padding:8px 24px;display:flex;align-items:center;gap:16px;font-size:.85rem;color:var(--muted)}}
    .timer-display{{font-size:1.4rem;font-weight:700;color:var(--text);font-variant-numeric:tabular-nums;min-width:60px}}
    .timer-btn{{padding:5px 14px;border-radius:4px;border:none;font-size:.82rem;font-weight:600;cursor:pointer}}
    .timer-start{{background:#1776a4;color:#fff}}
    .timer-stop {{background:#374151;color:#fff}}
    .timer-reset{{background:transparent;color:var(--muted);border:1px solid var(--border)}}
    .main{{flex:1;display:flex;flex-direction:column;min-width:0}}
    .slides{{flex:1;padding:32px 40px;overflow-y:auto}}
    .slide{{display:none;max-width:800px;margin:0 auto}}
    .slide.active{{display:block}}
    .slide-header{{display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
    .slide-num{{background:var(--accent);color:#fff;padding:3px 10px;border-radius:4px;font-size:.8rem;font-weight:700}}
    .slide-title{{font-size:1.2rem;font-weight:700;color:var(--text)}}
    .slide-time{{font-size:.78rem;color:var(--faint);margin-left:auto}}
    .script-body{{font-size:1.25rem;line-height:2.0;color:var(--text);white-space:pre-wrap;font-weight:400;background:var(--card);padding:28px 32px;border-radius:10px;border:1px solid var(--border);letter-spacing:.01em}}
    .slide-nav{{margin-top:20px;display:flex;gap:12px}}
    .slide-nav button{{padding:8px 20px;border-radius:6px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:.9rem;cursor:pointer;transition:background .15s}}
    .slide-nav button:hover{{background:var(--accent);color:#fff;border-color:var(--accent)}}
    .progress{{height:3px;background:var(--border)}}
    .progress-fill{{height:100%;background:var(--teal);transition:width .3s}}
    .kb-hint{{padding:6px 24px;font-size:.72rem;color:var(--faint);border-top:1px solid var(--border)}}
  </style>
</head>
<body>

<aside class="sidebar">
  <h2>Slides ({fmt_time(total)} total)</h2>
  <ol>{toc_items}</ol>
</aside>

<div class="main">
  <div class="timer-bar">
    <div class="timer-display" id="timerDisplay">0:00</div>
    <button class="timer-btn timer-start" onclick="startTimer()">Start</button>
    <button class="timer-btn timer-stop"  onclick="stopTimer()">Pause</button>
    <button class="timer-btn timer-reset" onclick="resetTimer()">Reset</button>
    <span id="timerStatus" style="margin-left:8px;font-size:.8rem"></span>
  </div>
  <div class="progress"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>

  <div class="slides" id="slidesArea">
{"".join(slides_html)}
  </div>

  <div class="kb-hint">Keyboard: &larr; &rarr; arrow keys to navigate &middot; Space to start/pause timer</div>
</div>

<script>
  let current = 1;
  const total = {len(SLIDES)};
  const totalSecs = {total};
  let elapsed = 0;
  let timerInterval = null;
  const targets = {{{", ".join(f"{n}: {s}" for n, _, s, _ in SLIDES)}}};
  let slideStart = 0;

  function goto(n) {{
    document.getElementById('s' + current)?.classList.remove('active');
    current = Math.max(1, Math.min(n, total));
    const sl = document.getElementById('s' + current);
    if (sl) {{ sl.classList.add('active'); sl.scrollIntoView({{block:'nearest'}}); }}
    updateProgress();
    slideStart = elapsed;
  }}

  function updateProgress() {{
    const pct = ((current - 1) / total) * 100;
    document.getElementById('progressFill').style.width = pct + '%';
  }}

  function fmtTime(s) {{
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m + ':' + String(sec).padStart(2, '0');
  }}

  function startTimer() {{
    if (timerInterval) return;
    timerInterval = setInterval(() => {{
      elapsed++;
      document.getElementById('timerDisplay').textContent = fmtTime(elapsed);
      const targetSec = targets[current] || 0;
      const onSlide = elapsed - slideStart;
      const status = document.getElementById('timerStatus');
      if (targetSec > 0 && onSlide > targetSec + 5) {{
        status.textContent = 'Over by ' + (onSlide - targetSec) + 's';
        status.style.color = '#ef4444';
      }} else if (targetSec > 0 && onSlide >= targetSec - 5) {{
        status.textContent = 'Almost done (~' + (targetSec - onSlide) + 's left)';
        status.style.color = '#f59e0b';
      }} else {{
        status.textContent = '';
      }}
    }}, 1000);
  }}

  function stopTimer() {{
    clearInterval(timerInterval);
    timerInterval = null;
  }}

  function resetTimer() {{
    stopTimer();
    elapsed = 0;
    document.getElementById('timerDisplay').textContent = '0:00';
    document.getElementById('timerStatus').textContent = '';
  }}

  document.addEventListener('keydown', e => {{
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') {{ e.preventDefault(); goto(current + 1); }}
    if (e.key === 'ArrowLeft'  || e.key === 'PageUp')   {{ e.preventDefault(); goto(current - 1); }}
    if (e.key === ' ') {{
      e.preventDefault();
      timerInterval ? stopTimer() : startTimer();
    }}
  }});

  goto(1);
</script>
</body>
</html>"""


# -- Entry point --------------------------------------------------------------

def main():
    md_path   = HERE / "recording-script.md"
    html_path = HERE / "recording-script.html"

    md_path.write_text(build_markdown(), encoding="utf-8")
    print(f"Markdown script: {md_path}")

    html_path.write_text(build_html(), encoding="utf-8")
    print(f"HTML teleprompter: {html_path}")
    print(f"\nTotal target time: {fmt_time(total_time())} ({total_time()}s)")
    print("Open recording-script.html in Chrome.")
    print("Use arrow keys to advance slides. Space to start/pause the timer.")


if __name__ == "__main__":
    main()
