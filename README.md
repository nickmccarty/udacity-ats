# The SQLite Search Cache
**Udacity Teaching Sample &mdash; Harness Engineering &middot; Course 4: Search Engineering &mdash; Advanced**

---

## Quick Start for Evaluators

| Artifact | How to access |
|----------|---------------|
| Web lesson (primary) | Open `index.html` in any modern browser |
| Video | Embedded in `index.html`; local copy at `video/sqlite-search-cache-lesson.mp4` |
| Google Slides deck | https://docs.google.com/presentation/d/1KSPGWO9CC-HQ0nHnOh0cN5ESCNHW2AwSTDP1BxZGi6g/edit?usp=sharing |
| Jupyter notebook (Colab) | https://colab.research.google.com/drive/1NXp8Dhl67z1pTTy8wl0f9tORWbyR3YkE?usp=sharing |
| PPTX slide deck | `sqlite-search-cache.pptx` (python-pptx generated, Udacity template) |

---

## Learning Objective

Implement a `cached_search()` wrapper backed by SQLite that normalises query strings to cache keys via SHA-256, stores results with configurable TTL, serves cache hits without calling the API, and evicts expired entries on every write — reducing multi-session API call volume to zero for repeated queries within the TTL window.

**Standards covered:** S-C4.3 Web Search &middot; S-C4.8 Search Orchestration  
**Level:** Advanced &middot; **Estimated time:** 30 min  
**Bloom's coverage:** All six levels (Remember through Create)

---

## Curriculum Gap Justification

S-C4.3 teaches web search integration; S-C4.8 covers multi-source orchestration and search budgeting. Neither addresses what happens when a multi-session workflow re-issues the same queries across runs.

In single-session exercises the cost of redundancy is invisible. In production autoresearch, a 12-query session run 10 times produces 120 API calls, of which ~108 are typically redundant — burning API credits, accumulating rate-limit pressure, and adding 30-90 seconds of latency per session with no new information.

The harness this lesson is built from ships two cache tables: `search_cache` (per-query results, 24h TTL) and `research_cache` (full `gather_research()` output, opt-in via `RESEARCH_CACHE=1`). Without the cache, autoresearch pays API cost and wall time on every restart for identical queries.

---

## File Inventory

```
micro-lesson/course-4/
+-- index.html                          # Primary web lesson (open in browser)
+-- sqlite-search-cache.pptx            # PPTX deck (Udacity template, 20" x 11.25")
+-- sqlite_search_cache_colab.ipynb     # Jupyter notebook for Google Colab
+-- sqlite-search-cache.md              # Lesson plan document
+-- recording-script.md                 # Full narration script with timestamps
+-- build_deck.py                       # PPTX generation script (python-pptx)
+-- README.md                           # This file
+-- video/
    +-- produce_video.py                # E2E video production pipeline
    +-- sqlite-search-cache-lesson.mp4  # Final rendered video
    +-- slides/                         # Generated animated HTML slides (01-11)
    +-- segments/                       # Intermediate .webm and .mp4 segment files
```

---

## Bloom's Taxonomy Alignment

| Level | Verb | Activity |
|-------|------|----------|
| Remember | Recall | Cache table names (`search_cache`), default TTL (86,400 s), normalisation steps |
| Understand | Explain | Why normalisation precedes hashing; why TTL eviction is inline rather than scheduled |
| Apply | Implement | `cached_search()` from the starter skeleton (web lesson + Colab notebook) |
| Analyze | Calculate | Trace a query log; compute API call reduction for a given session count |
| Evaluate | Assess | Choose appropriate TTL for breaking-news vs. evergreen queries; decide when to invalidate manually |
| Create | Build | Two-table cache module with `stats()` + `clear_expired()`; before/after comparison |

---

## Production Pipeline

All artifacts were generated programmatically. No screen-recording software, no manual slide editors, no post-production timeline.

1. **Content design** -- Lesson plan, Bloom's alignment, and recording script authored in Markdown. Segment boundaries mapped to natural sentence pauses identified with `ffmpeg silencedetect -35dB` and verified against timestamped transcripts.

2. **HTML slides** -- `produce_video.py` generates 11 animated HTML slides at 1280x720, styled with Udacity design tokens (Open Sans, Roboto Mono, navy #171A53, seafoam #00C5A1, brand blue #2015FF). Each element fades in on a precise millisecond schedule via a lightweight JavaScript event player.

3. **Screen recording** -- Playwright (headless Chromium, `device_scale_factor=1.5` for crisp sub-pixel rendering) navigates to each slide, waits 2.5 s for Google Fonts to settle, fires the animation scheduler, and records the result as a `.webm`.

4. **Audio extraction** -- `ffmpeg` extracts each segment from one of six source WAV files using sample-accurate `-ss` / `-t` parameters. Clip boundaries were chosen at silence gaps in the recordings.

5. **Segment merge** -- Each `.webm` + `.wav` pair is merged into H.264 MP4 at CRF 15. The `-shortest` flag trims each segment to audio length.

6. **Concatenation** -- All 11 segments are stream-copied (no re-encode) via the `concat` demuxer into the final MP4. Total runtime: 6:59.

7. **PPTX** -- `build_deck.py` generates a 20"x11.25" deck using `python-pptx`, aligned to the Udacity Teaching Sample template with matching colors and speaker notes.

8. **Deployment** -- Final MP4 uploaded to Cloudinary and embedded in `index.html` via the Cloudinary Player iframe with responsive `aspect-ratio: 16/9` sizing.

### Running the Build (optional)

The rendered MP4 and PPTX are already included. To regenerate from source:

```bash
# Video pipeline (~7 min, requires source WAV files)
cd micro-lesson/course-4/video
python produce_video.py

# PPTX deck
cd micro-lesson/course-4
python build_deck.py
```

**Requirements:** `playwright` with Chromium (`playwright install chromium`), `ffmpeg` on PATH, `python-pptx`

---

## Development Timeline

This lesson was produced in a single working session. The table below reflects actual wall-clock time from blank canvas to final submission.

| Phase | Actor | Duration |
|-------|-------|----------|
| Curriculum gap analysis &amp; lesson brief | NotebookLM + Claude Code (3 source docs + PPTX) | 25 min |
| Lesson drafts &mdash; 2 candidate courses (C2 + C4) | Claude Code | 30 min |
| Audio recording &amp; transcription | Harness voice pipeline | 25 min |
| Video generation script | Claude Code | 20 min |
| Video critique, revision &amp; approval | Nick McCarty | 75 min |
| Web lesson polish, Colab notebook, GitHub | Claude Code | 15 min |
| **Total** | | **~3 h 10 min** |

The 75-minute revision block is where the lesson becomes a lesson: reordering segments for pedagogical flow, adjusting pacing at natural pauses, verifying Bloom's coverage across all six levels, and testing the Colab notebook cold as a student would encounter it. The AI pipeline accelerates production of artifacts; the curatorial judgment above the pipeline is human.

---

## Design System

The web lesson and video slides conform to the Udacity design system:

| Token | Hex | Usage |
|-------|-----|-------|
| Navy | `#171A53` | Headings, sidebar, header bands |
| Brand Blue | `#2015FF` | Primary accents, CTAs, interactive elements |
| Seafoam | `#00C5A1` | Success states, teal accents |
| Light Blue | `#6597FF` | Supplementary accents |
| Purple | `#B181FF` | Bloom's Level 1 (Remember) |
| Lime | `#BDEA09` | Highlight badges |
| Open Sans | -- | Titles, body text |
| Roboto Mono | -- | Code blocks, monospaced content |

---

*Submitted May 2026 &middot; Nick McCarty*
