"""
produce_video.py -- E2E 7-minute video production pipeline for Course 4 micro-lesson.

Architecture
  1. Generate 11 focused animated HTML slides (Udacity design system).
  2. Serve slides via a local HTTP server so Chromium loads them correctly.
  3. Python playwright (headless Chromium) records each slide as a .webm.
  4. ffmpeg extracts the matching audio sub-clip from the source WAV.
  5. ffmpeg merges each .webm + sub-clip .wav -> segment .mp4.
  6. ffmpeg concatenates all segments -> sqlite-search-cache-lesson.mp4.

Each slide covers exactly one idea, tightly aligned to the transcript sentences
spoken during that clip.  Total runtime: ~7:08.

Run:
    python micro-lesson/course-4/video/produce_video.py
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE         = Path(__file__).parent
SLIDES_DIR   = HERE / "slides"
SEGMENTS_DIR = HERE / "segments"
NOTES_DIR    = Path(r"C:\Users\nicho\Desktop\harness-refactor\notes")
FINAL_OUT    = HERE / "sqlite-search-cache-lesson.mp4"

# ---------------------------------------------------------------------------
# Headshot overlay  (segment 1 only -- "Hi, I'm Nick McCarty" intro)
# ---------------------------------------------------------------------------
HEADSHOT_PATH  = HERE / "nick-profile.png"
AVATAR_T_START = 1.5    # seconds into seg01 where avatar appears
AVATAR_T_END   = 7.5    # seconds into seg01 where avatar disappears
AVATAR_SIZE    = 160    # diameter in output pixels (1920x1080 frame)
AVATAR_PAD     = 44     # distance from bottom-left corner in pixels

W, H = 1280, 720

# ---------------------------------------------------------------------------
# Segment map  (slide_num, audio_file, audio_ss, audio_dur, record_dur_s)
#
# audio_ss    = start offset in the source WAV (seconds)
# audio_dur   = how many seconds to extract from that offset
# record_dur_s = how long playwright records the slide (equals audio_dur)
#
# Splits are at natural sentence boundaries from the transcripts:
#
#  src 215724 (54.72s):
#    [0-22]     "invisible in single-session ... identical round trips."
#    [22-54.72] "We're going to build ... already seen."  (full remainder)
#
#  src 220201 (76.74s):
#    [0-28]      "Before we build ... run it yourself."
#    [28-76.74]  "In answer ... 108 redundant calls ... every time auto-research runs."  (full remainder)
#
#  src 220431 (100.08s):
#    [0-30]  "Core insight ... semantically identical queries are different keys."
#    [30-75] "The normalisation pipeline ... rejoins with exactly one space."
#
#  src 221230 (107.16s):
#    [0-35]  "public interface ... call put to store the results and return them"
#    [35-80] "four design choices ... cache hit rate in production"
#
#  src 221743 (108.72s):
#    [0-70]  "One question ... normalize with space.join ... before hashing."
#
#  src 221936 (79.08s):
#    [0-65]  "Here's what you can do ... provider agnostic TTL tunable"
#
#  src 222003 (14.22s):
#    [0-14.22] full outro
#
# Total: 22+32.72+28+48.74+30+45+35+45+70+65+14.22 = 435.68s ~ 7:16
# ---------------------------------------------------------------------------
SEGMENTS = [
    # slide  source WAV                           ss    dur   rec
    (1,  "op-note-2026-05-19-215724.wav",   0.0,  25.4,  25.4),   # problem
    (2,  "op-note-2026-05-19-215724.wav",  25.4,  29.32, 29.32),  # solution
    (3,  "op-note-2026-05-19-220201.wav",   0.0,  22.0,  22.0),   # try-it-first (run yourself)
    (4,  "op-note-2026-05-19-220201.wav",  22.0,  54.74, 54.74),  # the waste (12 calls → failure)
    (5,  "op-note-2026-05-19-220431.wav",   0.0,  46.3,  46.3),   # sha256 sensitivity (split at silence)
    (6,  "op-note-2026-05-19-220431.wav",  46.3,  51.5,  51.5),   # normalisation pipeline (lower/split/join/rule)
    (7,  "op-note-2026-05-19-221230.wav",   0.0,  22.0,  22.0),   # cached_search() 4-line logic
    (8,  "op-note-2026-05-19-221230.wav",  22.0,  65.4,  65.4),   # 4 design choices (split at silence @22s)
    (9,  "op-note-2026-05-19-221743.wav",   0.0,  60.0,  60.0),   # quiz (correct=B at ~40s)
    (10, "op-note-2026-05-19-221936.wav",   0.0,  29.0,  29.0),   # recap (explain + normalise points)
    (11, "op-note-2026-05-19-222003.wav",   0.0,  14.22, 14.22),  # outro
]

# ---------------------------------------------------------------------------
# Local HTTP server  (avoids file:// blank-page in Chromium)
# ---------------------------------------------------------------------------
HTTP_PORT = 8765

class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SLIDES_DIR), **kwargs)
    def log_message(self, *a):
        pass

def start_http_server():
    server = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), _SilentHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"  HTTP server: http://127.0.0.1:{HTTP_PORT}/  (serving {SLIDES_DIR.name}/)")

def slide_url(slide_num):
    return f"http://127.0.0.1:{HTTP_PORT}/slide{slide_num:02d}.html"

# ---------------------------------------------------------------------------
# Google Fonts (Plus Jakarta Sans, Barlow Condensed, Inter, JetBrains Mono)
# ---------------------------------------------------------------------------
_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Open+Sans:wght@300;400;600'
    '&family=Barlow+Condensed:wght@500;700'
    '&family=Roboto+Mono:wght@400;500'
    '&display=swap" rel="stylesheet">'
)

# ---------------------------------------------------------------------------
# Udacity logo  (white SVG -- always shown on navy pill)
# ---------------------------------------------------------------------------
_UDACITY_LOGO_SRC = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB3aWR0aD0iMTQ0IiBoZWlnaHQ9IjQwIiB2aWV3Qm94PSIwIDAgMTQ0IDQwIiBmaWxsPSJub25lIiB4bWxucz0i"
    "aHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgo8cGF0aCBmaWxsLXJ1bGU9ImV2ZW5vZGQiIGNsaXAtcnVsZT0iZXZl"
    "bm9kZCIgZD0iTTIuMTc4OTggNy44OTQ0M0w5LjA0MDY0IDMuOTEzMzlWMTUuMDg0N0M5LjAyNzEgMTkuMjI4MiAxMS45"
    "MzY5IDIyLjkzODQgMTUuOTQyOSAyMy45MTM0TDE2LjAzNzYgMjMuOTQwNUwxNS45ODM1IDI0LjAwODJDMTUuODg4OCAy"
    "NC4xMyAxNS44MDc2IDI0LjIzODQgMTUuNjk5MyAyNC4zNDY3QzE1LjMzMzkgMjQuNzEyMyAxNC45NDE0IDI1LjAzNzMg"
    "MTQuNTA4MyAyNS4zMjE2QzE0LjA3NTIgMjUuNjA2IDEzLjYyODYgMjUuODQ5NyAxMy4xNTQ5IDI2LjA1MjlDMTIuNjY3"
    "NyAyNi4yNTYgMTIuMTY3IDI2LjQwNDkgMTEuNjUyNyAyNi41MTMzQzExLjExMTMgMjYuNjIxNiAxMC42NTEyIDI2LjY3"
    "NTcgMTAuMTUwNCAyNi42NzU3SDEwLjA5NjNDOS41NTQ5MiAyNi42NzU3IDkuMDEzNTcgMjYuNjIxNiA4LjQ4NTc1IDI2"
    "LjUxMzNDNy45NzE0NyAyNi40MDQ5IDcuNDcwNzEgMjYuMjU2IDYuOTgzNDkgMjYuMDM5M0M2LjUwOTgxIDI1LjgzNjIg"
    "Ni4wNDk2NiAyNS41OTI1IDUuNjMwMTEgMjUuMzA4MUM1LjIxMDU2IDI1LjAyMzcgNC44MDQ1NSAyNC42OTg4IDQuNDUy"
    "NjcgMjQuMzMzMkM0LjA4NzI1IDIzLjk2NzYgMy43NjI0NCAyMy41NzQ5IDMuNDc4MjMgMjMuMTU1MUMzLjE5NDAyIDIy"
    "LjcyMTggMi45NTA0MSAyMi4yNzQ5IDIuNzQ3NCAyMS44MDFDMi41NDQ0IDIxLjMyNzEgMi4zOTU1MyAyMC44MjYgMi4y"
    "ODcyNSAyMC4zMTE1QzIuMTc4OTggMTkuNzU2MyAyLjE2NTQ1IDE5LjE4NzYgMi4xNjU0NSAxOC43MDAxVjcuOTM1MDVM"
    "Mi4xNzg5OCA3Ljg5NDQzWk0xOS40MzQ2IDIxLjk3N0MxOS44MjcxIDIwLjg2NjcgMjAuMDMwMSAxOS43Njk5IDIwLjA0"
    "MzYgMTguNzQwN1Y2LjkzMzAyTDI1LjI0MDYgMy45NTQwMVYxNS4xMjUzQzI1LjI0MDYgMTUuNjI2MyAyNS4xODY1IDE2"
    "LjExMzggMjUuMTA1MyAxNi41NDcxQzI1LjAxMDYgMTYuOTk0IDI0Ljg3NTIgMTcuNDQwOCAyNC42ODU4IDE3Ljg3NDFD"
    "MjQuNTA5OCAxOC4yOTM5IDI0LjI3OTcgMTguNzAwMSAyNC4wMjI2IDE5LjA2NTdDMjMuNzY1NSAxOS40NDQ5IDIzLjQ2"
    "NzcgMTkuNzk2OSAyMy4xNTY0IDIwLjEwODRDMjIuODMxNiAyMC40MzM0IDIyLjQ3OTcgMjAuNzE3NyAyMi4xMDA4IDIw"
    "Ljk3NUMyMS43MjE4IDIxLjIzMjMgMjEuMzE1OCAyMS40NDg5IDIwLjg5NjMgMjEuNjI1QzIwLjQ2MzIgMjEuODE0NSAy"
    "MC4wNzA3IDIxLjkzNjQgMTkuNTcgMjIuMDMxMkMxOS41NTY0IDIyLjAzMTIgMTkuNTI5NCAyMi4wMzEyIDE5LjUxNTgg"
    "MjIuMDQ0N0wxOS40MjExIDIyLjA1ODNMMTkuNDM0NiAyMS45NzdaTTAuMTA4MzA3IDYuODExMTVWMTguNjU5NUMwLjA5"
    "NDc3MzIgMjQuMTQzNiA0LjU2MDk0IDI4LjYyNTYgMTAuMDgyNyAyOC42MzkySDEwLjEwOThDMTEuODU1NyAyOC42Mzky"
    "IDEzLjU4OCAyOC4xNzg4IDE1LjA5MDMgMjcuMzEyMkwyMi4zODUgMjMuMTQxNkMyNS40MDMgMjEuNTg0MyAyNy4yOTc4"
    "IDE4LjUxMDUgMjcuMjk3OCAxNS4xMjUzVjIuNzQ4ODZDMjcuMjk3OCAyLjcwODI0IDI3LjI4NDIgMi42ODExNiAyNy4y"
    "NDM2IDIuNjY3NjJMMjUuNDU3MiAxLjYyNDk2QzI1LjQzMDEgMS42MTE0MiAyNS4zODk1IDEuNjExNDIgMjUuMzYyNCAx"
    "LjYyNDk2TDE4LjA0MDYgNS43NDE0MkMxOC4wMTM2IDUuNzU0OTYgMTcuOTg2NSA1Ljc5NTU4IDE3Ljk4NjUgNS44MjI2"
    "NlYxOC43MTM3QzE3Ljk4NjUgMTkuMjU1MyAxNy45MzI0IDE5Ljc5NjkgMTcuODI0MSAyMC4zMjVDMTcuNzE1OCAyMC44"
    "Mzk2IDE3LjU1MzQgMjEuMzQwNiAxNy4zNTA0IDIxLjgxNDVDMTcuMzA5OCAyMS45MDkzIDE3LjI2OTIgMjEuOTkwNiAx"
    "Ny4yMjg2IDIyLjA4NTRMMTcuMjE1MSAyMi4xMjZIMTcuMTc0NUMxNy4wNTI3IDIyLjExMjQgMTYuOTcxNSAyMi4wOTg5"
    "IDE2Ljc1NDkgMjIuMDU4M0wxNi43MDA4IDIyLjA0NDdDMTYuMjQwNiAyMS45NDk5IDE1LjMwNjggMjEuNjkyNyAxNC4x"
    "OTcgMjAuOTc1QzEzLjgxODEgMjAuNzE3NyAxMy40NjYyIDIwLjQzMzQgMTMuMTQxNCAyMC4xMDg0QzEyLjgxNjYgMTku"
    "NzgzNCAxMi41MzI0IDE5LjQzMTMgMTIuMjc1MiAxOS4wNTIyQzEyLjAxODEgMTguNjczIDExLjgwMTUgMTguMjY2OCAx"
    "MS42MjU2IDE3Ljg0N0MxMS40NDk3IDE3LjQyNzMgMTEuMzE0MyAxNi45ODA0IDExLjIxOTYgMTYuNTJDMTEuMTM4NCAxNi"
    "4xMTM4IDExLjA5NzggMTUuNjk0IDExLjExMTMgMTUuMTExOFYyLjc0ODg2QzExLjExMTMgMi43MDgyNCAxMS4wOTc4IDIu"
    "NjgxMTYgMTEuMDU3MiAyLjY2NzYyTDkuMjcwNzEgMS41ODQzNEM5LjI1NzE4IDEuNTcwOCA5LjIzMDExIDEuNTcwOCA5"
    "LjIxNjU4IDEuNTcwOEM5LjIwMzA0IDEuNTcwOCA5LjE3NTk4IDEuNTcwOCA5LjE2MjQ0IDEuNTg0MzRMMC4xNDg5MDkg"
    "Ni43Mjk5MUMwLjEyMTg0MSA2Ljc0MzQ1IDAuMTA4MzA3IDYuNzg0MDcgMC4xMDgzMDcgNi44MTExNVoiIGZpbGw9Indoa"
    "XRlIi8+CjxwYXRoIGZpbGwtcnVsZT0iZXZlbm9kZCIgY2xpcC1ydWxlPSJldmVub2RkIiBkPSJNNDkuOTY2OSAxNi41"
    "ODc3QzQ5Ljk2NjkgMTguNjczIDQ4LjE4MDUgMjAuMjk3OSA0NS45MDY4IDIwLjI5NzlDNDMuNjMzMSAyMC4yOTc5IDQx"
    "LjkwMDggMTguNzAwMSA0MS45MDA4IDE2LjU4NzdWOC4yMTkzNUgzOS44NDM2VjE2LjYxNDdDMzkuODQzNiAxOS44NTEg"
    "NDIuMzg4IDIyLjE5MzYgNDUuOTA2OCAyMi4xOTM2QzQ5LjQyNTYgMjIuMTkzNiA1Mi4wMjQxIDE5Ljg1MSA1Mi4wMjQx"
    "IDE2LjYxNDdWOC4yMDU4MUg0OS45NjY5VjE2LjU4NzdaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBmaWxsLXJ1bGU9ImV2"
    "ZW5vZGQiIGNsaXAtcnVsZT0iZXZlbm9kZCIgZD0iTTY3LjY0MjIgMTUuMTExN0M2Ny42NDIyIDE4LjI2NjcgNjUuNzc0"
    "NSAyMC4xNDg5IDYyLjY0ODIgMjAuMTQ4OUw2MC4yMzkyIDIwLjEzNTRWMTAuMDg4SDYyLjY0ODJDNjUuNzc0NSAxMC4w"
    "ODggNjcuNjQyMiAxMS45NTY3IDY3LjY0MjIgMTUuMTExN1pNNjIuNzcgOC4yMTkzNUw1OC4yMDkxIDguMjA1ODFINTgu"
    "MTgyVjIyLjAxNzZINjIuNzU2NEM2Ni45NjU1IDIyLjAxNzYgNjkuNjk5MyAxOS4zMDk0IDY5LjY5OTMgMTUuMTExN0M2"
    "OS42NzIyIDEwLjkyNzUgNjYuOTY1NSA4LjIxOTM1IDYyLjc3IDguMjE5MzVaIiBmaWxsPSJ3aGl0ZSIvPgo8cGF0aCBm"
    "aWxsLXJ1bGU9ImV2ZW5vZGQiIGNsaXAtcnVsZT0iZXZlbm9kZCIgZD0iTTE0MS42MDUgOC4yMDU4MUwxMzcuNTg1IDE0"
    "LjAwMTNMMTMzLjUzOCA4LjIxOTM1TDEzMy41MjUgOC4yMDU4MUgxMzEuMjExTDEzNi41NTYgMTYuMTI3M1YyMi4wMTc2"
    "SDEzOC42MTRWMTYuMTQwOEwxNDMuODY1IDguMjU5OTdMMTQzLjg5MiA4LjIwNTgxSDE0MS42MDVaIiBmaWxsPSJ3aGl0"
    "ZSIvPgo8cGF0aCBmaWxsLXJ1bGU9ImV2ZW5vZGQiIGNsaXAtcnVsZT0iZXZlbm9kZCIgZD0iTTExNi4yNDIgMTAuMDg4"
    "SDEyMC45NjVWMjIuMDE3NkgxMjMuMDIzVjEwLjA4OEgxMjcuNzQ2VjguMjA1ODFIMTE2LjI0MlYxMC4wODhaIiBmaWxs"
    "PSJ3aGl0ZSIvPgo8cGF0aCBmaWxsLXJ1bGU9ImV2ZW5vZGQiIGNsaXAtcnVsZT0iZXZlbm9kZCIgZD0iTTEwOC43MzEg"
    "MjIuMDE3NkgxMTAuNzg4VjguMjA1ODFIMTA4LjczMVYyMi4wMTc2WiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZmlsbC1y"
    "dWxlPSJldmVub2RkIiBjbGlwLXJ1bGU9ImV2ZW5vZGQiIGQ9Ik0xMDEuMTI1IDE4LjY3M0MxMDEuMTExIDE4LjY4NjUg"
    "OTkuNTgyIDIwLjI5NzkgOTcuMTQ1OSAyMC4yOTc5Qzk0LjMxNzMgMjAuMjk3OSA5Mi4xNzkgMTguMDIzIDkyLjE3OSAx"
    "NS4wMDM0QzkyLjE3OSAxMS45ODM3IDk0LjMwMzggOS45MTE5NiA5Ny4xMDUzIDkuOTExOTZDOTkuMzc5IDkuOTExOTYg"
    "MTAwLjgyNyAxMS4yMzkgMTAwLjg0MSAxMS4yNTI1TDEwMS4xMzggMTEuNTIzM0wxMDIuMjM1IDEwLjAyMDNMMTAyLjAz"
    "MiA5LjgwMzYzQzEwMS45NjQgOS43MzU5MyAxMDAuMzEzIDguMDAyNjkgOTcuMDc4MiA4LjAwMjY5QzkzLjExMjggOC4w"
    "NDMzMSA5MC4xMzU0IDExLjA0OTQgOTAuMTM1NCAxNS4wMzA0QzkwLjEzNTQgMTkuMDExNSA5My4xMzk5IDIyLjE5MzYg"
    "OTcuMTMyNCAyMi4xOTM2QzEwMC40NzUgMjIuMTkzNiAxMDIuMzE2IDIwLjEzNTQgMTAyLjM5NyAyMC4wNTQxTDEwMi42"
    "IDE5LjgzNzVMMTAxLjQwOSAxOC4zODg2TDEwMS4xMjUgMTguNjczWiIgZmlsbD0id2hpdGUiLz4KPHBhdGggZmlsbC1y"
    "dWxlPSJldmVub2RkIiBjbGlwLXJ1bGU9ImV2ZW5vZGQiIGQ9Ik04MS43MzA4IDE1Ljg3TDc3LjY0MzYgMTYuNjk2TDc5"
    "Ljg0OTYgMTEuMjc5Nkw4MS43MzA4IDE1Ljg3Wk04MC43OTY5IDguMjE5MzZINzguOTE1N0w3My4yOTkyIDIyLjAxNzZI"
    "NzUuNDY0Nkw3Ni43MDk3IDE4Ljk3MDlMODIuNTAyMiAxNy43OTI4TDg0LjIyMSAyMi4wMDQxTDg0LjIzNDUgMjIuMDMx"
    "MUg4Ni40TDgwLjc5NjkgOC4yMzI5VjguMjE5MzZaIiBmaWxsPSJ3aGl0ZSIvPgo8L3N2Zz4K"
)
_LOGO_PILL = (
    '<span style="background:#171a53;border-radius:5px;padding:6px 9px 3px;'
    'display:inline-flex;align-items:center;line-height:0;">'
    f'<img src="{_UDACITY_LOGO_SRC}" style="height:15px;" alt="Udacity">'
    '</span>'
)

# ---------------------------------------------------------------------------
# Shared CSS  --  Udacity design system tokens (light theme)
# ---------------------------------------------------------------------------
BASE_CSS = """
:root {
  --bg:       #ffffff;
  --bg-card:  #f6f6f6;
  --bg-code:  #eaeef2;
  --border:   #dbe2e8;
  --text:     #0b0b0b;
  --navy:     #171a53;
  --muted:    #444466;
  --dim:      #888;
  --blue:     #2015ff;
  --teal:     #00c5a1;
  --red:      #dc2626;
  --lime:     #bdea09;
  --c-kw:     #b91c1c;
  --c-fn:     #1d1acc;
  --c-str:    #0f766e;
  --c-num:    #171a53;
  --c-cmt:    #888;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  width: 1280px; height: 720px; overflow: hidden;
  background: var(--bg);
  font-family: 'Open Sans', system-ui, sans-serif;
  color: var(--muted);
}

/* ---- Header band ---- */
.header-band {
  position:absolute; top:0; left:0; right:0; height:44px;
  background:#f6f6f6; border-bottom:1px solid var(--border);
  z-index:0;
}

/* ---- Color bar (sits above header band) ---- */
.bar       { position:absolute; top:0; left:0; right:0; height:4px; background:var(--navy); z-index:1; }
.bar.teal  { background:var(--teal); }
.bar.red   { background:var(--red); }

/* ---- Slide header ---- */
.slide-header {
  position:absolute; top:10px; left:36px; right:36px;
  display:flex; align-items:center; gap:10px;
  z-index:2;
}
.chip {
  font-family:'Barlow Condensed',sans-serif;
  font-size:11px; font-weight:700; letter-spacing:.13em;
  text-transform:uppercase;
  padding:3px 10px; border-radius:4px;
  background:var(--navy); color:#fff;
}
.chip.teal { background:var(--teal); }
.chip.red  { background:var(--red); }
.slide-label {
  font-family:'Barlow Condensed',sans-serif;
  font-size:12px; font-weight:500; letter-spacing:.08em;
  text-transform:uppercase; color:var(--dim);
}

/* ---- Content area ---- */
.content { position:absolute; top:52px; left:36px; right:36px; bottom:16px; }

/* ---- Animation ---- */
.hi { opacity:0; transform:translateY(11px);
      transition: opacity .4s ease, transform .4s ease; }
.hi.show { opacity:1; transform:none; }

/* ---- Typography  (Udacity spec: Open Sans 300/400/600, Roboto Mono 400) ---- */
.t-hero {
  font-family:'Open Sans',sans-serif;
  font-size:46px; font-weight:300;
  line-height:1.15; letter-spacing:-.5px; color:var(--navy);
}
.t-h2 {
  font-family:'Open Sans',sans-serif;
  font-size:32px; font-weight:400; line-height:1.25; color:var(--navy);
}
.t-h3 {
  font-family:'Open Sans',sans-serif;
  font-size:24px; font-weight:600; line-height:1.35; color:var(--navy);
}
.t-label {
  font-family:'Barlow Condensed',sans-serif;
  font-size:11px; font-weight:700; letter-spacing:.1em;
  text-transform:uppercase; color:var(--dim);
}
.t-body  { font-family:'Open Sans',sans-serif; font-size:18px; line-height:1.7;  color:var(--muted); }
.t-small { font-family:'Open Sans',sans-serif; font-size:15px; line-height:1.65; color:var(--muted); }
.t-blue  { color:var(--blue); }
.t-teal  { color:var(--teal); }
.t-red   { color:var(--red); }
.t-lime  { color:#4a5800; }
.t-navy  { color:var(--navy); }
.t-white { color:var(--navy); }
.t-dim   { color:var(--dim); }
.t-mono  { font-family:'Roboto Mono',monospace; }

/* ---- Card ---- */
.card {
  background:var(--bg-card);
  border:1px solid var(--border);
  border-radius:12px;
  padding:16px 20px;
}

/* ---- Code block ---- */
pre.code {
  background:var(--bg-code);
  border:1px solid var(--border);
  border-left:3px solid var(--navy);
  border-radius:8px;
  padding:16px 20px;
  font-family:'Roboto Mono','Courier New',monospace;
  font-size:13px; line-height:1.65;
  color:var(--c-num);
  white-space:pre; overflow:hidden;
}
pre.code .kw  { color:var(--c-kw); }
pre.code .fn  { color:var(--c-fn); }
pre.code .str { color:var(--c-str); }
pre.code .cmt { color:var(--c-cmt); font-style:italic; }
pre.code .num { color:var(--c-num); }
pre.code .hl  {
  display:inline-block; width:100%;
  background:rgba(0,197,161,.12);
  border-left:3px solid var(--teal);
  margin-left:-3px; padding-left:3px;
  transition:background .35s, border-color .35s;
}
pre.code .hl.hot  { background:rgba(0,197,161,.22); }
pre.code .hl.warm { background:rgba(32,21,255,.1); border-color:var(--blue); }

/* ---- Callout ---- */
.callout { border-radius:8px; padding:12px 16px; font-size:14px; line-height:1.65; }
.co-red  { background:#fee2e2; border-left:3px solid var(--red);  color:#991b1b; }
.co-teal { background:#e6faf7; border-left:3px solid var(--teal); color:#007a66; }
.co-blue { background:#eef2ff; border-left:3px solid var(--blue); color:#1d1acc; }
.co-navy { background:#f0f1f8; border-left:3px solid var(--navy); color:var(--navy); }

/* ---- Badge ---- */
.badge { display:inline-block; padding:2px 10px; border-radius:99px; font-size:12px; font-weight:600; }
.b-blue { background:#eef2ff; color:#1d1acc; border:1px solid rgba(32,21,255,.25); }
.b-teal { background:#e6faf7; color:#007a66; border:1px solid rgba(0,197,161,.35); }
.b-gray { background:#f0f0f0; color:var(--muted); border:1px solid var(--border); }
.b-red  { background:#fee2e2; color:#991b1b; border:1px solid rgba(220,38,38,.3); }

/* ---- Step bullet ---- */
.step-dot {
  width:8px; height:8px; border-radius:50%;
  background:var(--teal); flex-shrink:0; margin-top:7px;
}

/* ---- Big number ---- */
.stat-num {
  font-family:'Plus Jakarta Sans',sans-serif;
  font-size:88px; font-weight:800; line-height:1;
  font-variant-numeric:tabular-nums;
}

/* ---- Quiz option ---- */
.qopt {
  display:flex; gap:12px; align-items:flex-start;
  background:var(--bg-card); border:1px solid var(--border);
  border-radius:8px; padding:11px 14px; margin-bottom:8px;
  transition:border-color .3s, background .3s;
}
.qopt.correct { border-color:var(--teal); background:#e6faf7; }
.qopt.wrong   { border-color:var(--red);  background:#fee2e2; }
.opt-letter {
  min-width:24px; height:24px; border-radius:50%;
  background:var(--dim); color:#fff;
  display:flex; align-items:center; justify-content:center;
  font-size:11px; font-weight:700; flex-shrink:0;
  transition:background .3s;
}
.qopt.correct .opt-letter { background:var(--teal); }
.qopt.wrong   .opt-letter { background:var(--red);  }
.opt-text { font-size:13.5px; color:var(--muted); line-height:1.55; }
.qopt.correct .opt-text { color:#007a66; }
.qopt.wrong   .opt-text { color:#991b1b; }
"""

BASE_JS = """
function play(evts) {
  const events = evts || window.__EVENTS__ || [];
  events.forEach(([ms, id, cls]) => {
    setTimeout(() => {
      const el = typeof id === 'string'
        ? document.getElementById(id)
        : document.querySelectorAll(id.sel)[id.idx || 0];
      if (el) el.classList.add(cls || 'show');
    }, ms);
  });
}
window.play = play;
"""

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
def slide_html(num, chip_text, chip_cls, bar_cls, body_html, events):
    ev = json.dumps(events)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
{_FONTS}
<style>
{BASE_CSS}
</style>
</head>
<body>
<div class="header-band"></div>
<div class="bar {bar_cls}"></div>
<div class="slide-header">
  <span class="chip {chip_cls}">{chip_text}</span>
  <span class="slide-label">Course 4 &mdash; SQLite Search Cache &mdash; {num}/11</span>
  <div style="margin-left:auto;">{_LOGO_PILL}</div>
</div>
<div class="content">
{body_html}
</div>
<script>
window.__EVENTS__ = {ev};
{BASE_JS}
</script>
</body>
</html>"""


# ===========================================================================
# Slide generators
# ===========================================================================

# ---------------------------------------------------------------------------
# Slide 1 -- The Problem  (22s)
# "This lesson solves a cost and latency problem invisible in single-session
#  exercises ... re-issues the same web search queries ... burning API credits,
#  hitting rate limits, adding latency per session with identical round trips."
# ---------------------------------------------------------------------------
def slide_01():
    body = f"""
<div style="position:absolute;top:30px;left:0;right:0;padding:0 4px;">
  <div id="s1-kicker" class="hi" style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
    {_LOGO_PILL}
    <span class="t-label">Harness Engineering &nbsp;&middot;&nbsp; Course 4</span>
  </div>
  <div id="s1-title" class="hi t-hero" style="margin-bottom:18px;">
    A cost &amp; latency problem<br/>
    <span class="t-blue">invisible in single-session work</span>
  </div>
  <div id="s1-body" class="hi t-body" style="max-width:780px; margin-bottom:20px;">
    Multi-session auto-research re-issues the same web search queries on every
    loop &mdash; burning API credits, hitting rate limits, adding latency with
    identical round trips each time.
  </div>
  <div id="s1-cards" class="hi" style="display:flex;gap:12px;">
    <div class="card" style="flex:1;padding:14px 18px;">
      <div class="t-label t-red" style="margin-bottom:6px;">Cost</div>
      <div class="t-small">API credits consumed for results already fetched in session&nbsp;1</div>
    </div>
    <div class="card" style="flex:1;padding:14px 18px;">
      <div class="t-label" style="margin-bottom:6px;color:var(--red);">Rate Limits</div>
      <div class="t-small">Same queries push you toward provider throttle thresholds</div>
    </div>
    <div class="card" style="flex:1;padding:14px 18px;">
      <div class="t-label" style="margin-bottom:6px;color:var(--red);">Latency</div>
      <div class="t-small">30&ndash;90 s of round-trip time added per session for zero new information</div>
    </div>
  </div>
</div>"""
    events = [
        [200,   "s1-kicker", "show"],
        [1000,  "s1-title",  "show"],
        [5000,  "s1-body",   "show"],
        [12000, "s1-cards",  "show"],
    ]
    return slide_html(1, "THE PROBLEM", "red", "red", body, events)


# ---------------------------------------------------------------------------
# Slide 2 -- The Solution  (29s)
# "We're going to build a SHA-256 keyed SQLite cache that intercepts queries
#  before they reach the API. Sessions 2 through N make 0 API calls for any
#  query the cache has already seen."
# ---------------------------------------------------------------------------
def slide_02():
    body = """
<div style="position:absolute;top:24px;left:0;right:0;padding:0 4px;">
  <div id="s2-title" class="hi t-h2" style="margin-bottom:18px;">
    The fix: a SHA-256 keyed SQLite cache
  </div>
  <div id="s2-arch" class="hi" style="display:flex;align-items:stretch;gap:12px;margin-bottom:22px;">
    <div class="card" style="flex:1;padding:18px;text-align:center;">
      <div class="t-label" style="margin-bottom:8px;color:var(--dim);">Every query</div>
      <div class="t-h3 t-mono" style="font-size:18px;margin-bottom:6px;">search(q)</div>
      <div class="t-small" style="color:var(--dim);">session 1 &middot; 2 &middot; 3 &middot; N</div>
    </div>
    <div style="display:flex;align-items:center;color:var(--dim);font-size:22px;">&rarr;</div>
    <div class="card" style="flex:2;padding:18px;border-color:rgba(23,26,83,.35);">
      <div class="t-label t-blue" style="margin-bottom:8px;">SQLite cache</div>
      <div style="display:flex;gap:10px;margin-top:6px;">
        <div class="callout co-teal" style="flex:1;font-size:13px;">
          <strong>HIT:</strong> return stored result &mdash; 0 API calls
        </div>
        <div class="callout co-red" style="flex:1;font-size:13px;">
          <strong>MISS:</strong> call API &rarr; store &rarr; return
        </div>
      </div>
    </div>
  </div>
  <div id="s2-stat" class="hi" style="display:flex;align-items:center;gap:36px;">
    <div>
      <div class="stat-num t-teal">0</div>
      <div class="t-label" style="margin-top:6px;">API calls &mdash; sessions 2 through N</div>
    </div>
    <div id="s2-badges" class="hi" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px;">
      <span class="badge b-blue">SHA-256 key</span>
      <span class="badge b-blue">SQLite storage</span>
      <span class="badge b-teal">TTL eviction</span>
      <span class="badge b-gray">provider-agnostic</span>
    </div>
  </div>
</div>"""
    events = [
        [500,   "s2-title",  "show"],
        [2500,  "s2-arch",   "show"],
        [14000, "s2-stat",   "show"],
        [18000, "s2-badges", "show"],
    ]
    return slide_html(2, "THE SOLUTION", "", "", body, events)


# ---------------------------------------------------------------------------
# Slide 3 -- Try It First  (28s)
# "Before we build the cache, I want you to experience what it prevents.
#  This stub simulates three auto-research sessions. Each session issues the
#  same four queries to the search API. Before I explain what happens, run it."
# ---------------------------------------------------------------------------
def slide_03():
    code = (
        '<span class="kw">import</span> time\n'
        '<span class="kw">from</span> ddgs <span class="kw">import</span> DDGS\n\n'
        'api_calls = <span class="num">0</span>\n\n'
        '<span class="kw">def</span> <span class="fn">search</span>(query, max_results=<span class="num">10</span>):\n'
        '    <span class="kw">global</span> api_calls\n'
        '    api_calls += <span class="num">1</span>\n'
        '    <span class="fn">print</span>(f<span class="str">"[API #{api_calls}] {query[:55]}"</span>)\n'
        '    <span class="kw">return</span> <span class="fn">list</span>(DDGS().text(query, max_results=max_results))\n\n'
        '<span id="s3-qs" class="hl">QUERIES = [\n'
        '    <span class="str">"transformer attention mechanisms survey"</span>,\n'
        '    <span class="str">"KV cache optimization techniques"</span>,\n'
        '    <span class="str">"flash attention implementation pytorch"</span>,\n'
        '    <span class="str">"multi-head latent attention MLA"</span>,\n'
        ']</span>\n\n'
        '<span class="kw">for</span> session <span class="kw">in</span> <span class="fn">range</span>(<span class="num">1</span>, <span class="num">4</span>):  <span class="cmt"># 3 sessions</span>\n'
        '    <span class="kw">for</span> q <span class="kw">in</span> QUERIES:\n'
        '        <span class="fn">search</span>(q)\n\n'
        '<span class="fn">print</span>(f<span class="str">"Total: {api_calls} calls"</span>)'
    )
    body = f"""
<div style="position:absolute;top:10px;left:0;right:560px;padding:0 4px;">
  <div id="s3-hdr" class="hi t-h3" style="margin-bottom:12px;">
    Try it first &mdash; before the explanation
  </div>
  <pre id="s3-code" class="hi code" style="font-size:13px;">{code}</pre>
</div>
<div style="position:absolute;top:10px;left:700px;right:0;padding:0 4px;">
  <div id="s3-what" class="hi callout co-blue" style="margin-bottom:16px;">
    Three auto-research sessions.<br/>
    Same four queries each session.<br/>
    Run it &mdash; count the API calls.
  </div>
  <div id="s3-q" class="hi t-h2" style="color:var(--muted);font-size:22px;line-height:1.4;margin-top:24px;">
    How many of those calls<br/>carry <span class="t-white">new information?</span>
  </div>
</div>"""
    events = [
        [200,   "s3-hdr",  "show"],
        [2000,  "s3-code", "show"],
        [8000,  "s3-qs",   "hot"],
        [16000, "s3-what", "show"],
        [22000, "s3-q",    "show"],
    ]
    return slide_html(3, "TRY-AND-FAIL", "red", "red", body, events)


# ---------------------------------------------------------------------------
# Slide 4 -- The Waste  (45s)
# "12 total calls, 4 per session, times 3 sessions, but only 4 were novel.
#  Sessions 2 and 3 paid API cost for results already available from session 1.
#  No errors, no warnings. The waste is silent.
#  At scale: 12 queries x 10 sessions = 108 redundant calls, 30-90s latency."
# ---------------------------------------------------------------------------
def slide_04():
    body = """
<div style="position:absolute;top:10px;left:0;right:628px;padding:0 4px;">
  <div id="s4-hdr" class="hi t-label" style="margin-bottom:8px;">The result</div>
  <div id="s4-num" class="hi" style="margin-bottom:10px;">
    <span class="stat-num t-red">12</span>
    <div class="t-label" style="margin-top:4px;">total API calls</div>
  </div>
  <div id="s4-breakdown" class="hi card" style="margin-bottom:14px;font-size:15px;line-height:1.9;">
    4 queries &times; 3 sessions = <span class="t-red">12 calls</span><br/>
    Novel results &mdash; session 1: <span class="t-teal">4</span><br/>
    Novel results &mdash; sessions 2&ndash;3: <span class="t-red">0</span><br/>
    Redundant calls: <span class="t-red">8 (67%)</span>
  </div>
  <div id="s4-silent" class="hi callout co-red" style="font-size:13.5px;">
    No exception. No warning. No log entry.<br/>
    <strong>The waste is completely silent.</strong>
  </div>
</div>
<div style="position:absolute;top:10px;left:648px;right:0;padding:0 4px;">
  <div id="s4-scale-hdr" class="hi t-label" style="margin-bottom:8px;color:var(--muted);">At production scale</div>
  <div id="s4-scale" class="hi card" style="margin-bottom:14px;font-size:15px;line-height:1.9;">
    12 queries &times; 10 sessions<br/>
    = <span class="t-red">108 redundant calls</span><br/>
    = <span class="t-red">30&ndash;90 s added latency</span><br/>
    &nbsp;&nbsp;every time auto-research runs
  </div>
  <div id="s4-fix" class="hi callout co-teal" style="font-size:13.5px;">
    The cache eliminates this completely.<br/>
    Sessions 2&ndash;N cost <strong>0 API calls</strong> for any query already seen.
  </div>
</div>"""
    events = [
        [200,   "s4-hdr",       "show"],
        [1000,  "s4-num",       "show"],
        [5000,  "s4-breakdown", "show"],
        [18000, "s4-silent",    "show"],
        [26000, "s4-scale-hdr", "show"],
        [27000, "s4-scale",     "show"],
        [38000, "s4-fix",       "show"],
    ]
    return slide_html(4, "TRY-AND-FAIL", "red", "red", body, events)


# ---------------------------------------------------------------------------
# Slide 5 -- SHA-256 Sensitivity  (30s)
# "SHA-256 is deterministic. The same bytes always produce the same digest,
#  but it is completely case-sensitive and whitespace-sensitive.
#  'Flash Attention' vs 'flash attention' => different digests.
#  A double space => a third key. Without normalisation: different keys."
# ---------------------------------------------------------------------------
def slide_05():
    body = """
<div style="position:absolute;top:16px;left:0;right:0;padding:0 4px;">
  <div id="s5-title" class="hi t-h2" style="margin-bottom:14px;">
    Core insight: SHA-256 is deterministic &mdash; <span class="t-red">and case-sensitive</span>
  </div>
  <div id="s5-fact" class="hi callout co-blue" style="max-width:820px;margin-bottom:18px;">
    Same bytes &rarr; same digest. Always. But a single uppercase letter or an extra space
    produces a <strong>completely different</strong> 256-bit output.
  </div>
  <div id="s5-ex" class="hi card t-mono" style="font-size:14px;line-height:2.1;margin-bottom:16px;">
    <div class="t-label" style="margin-bottom:8px;font-size:10px;">Without normalisation &mdash; three different cache keys</div>
    sha256(<span class="t-white">"Flash Attention"</span>)
    &nbsp;= <span class="t-red">"a1b2c3..."</span><br/>
    sha256(<span class="t-white">"flash attention"</span>)
    &nbsp;= <span class="t-red">"9f8e7d..."</span>
    <span style="color:var(--dim);font-size:12px;"> &larr; different!</span><br/>
    sha256(<span class="t-white">"flash&nbsp;&nbsp;attention"</span>)
    = <span class="t-red">"4d5e6f..."</span>
    <span style="color:var(--dim);font-size:12px;"> &larr; extra space = third key</span>
  </div>
  <div id="s5-conclusion" class="hi callout co-red">
    Semantically identical queries &rarr; <strong>different cache keys</strong> &rarr; cache always misses.
    Every session makes the full API call.
  </div>
</div>"""
    events = [
        [200,   "s5-title",      "show"],
        [2500,  "s5-fact",       "show"],
        [8000,  "s5-ex",         "show"],
        [23000, "s5-conclusion", "show"],
    ]
    return slide_html(5, "CONCEPT: NORMALISATION", "", "", body, events)


# ---------------------------------------------------------------------------
# Slide 6 -- The Normalisation Pipeline  (45s)
# "The normalisation pipeline is three steps.
#  .lower() folds all uppercase. .split() tokenises on ANY whitespace --
#  tabs, double spaces, newlines. KEY STEP: split() with no arg splits on all
#  whitespace. ' '.join() rejoins with exactly one space between each token."
# ---------------------------------------------------------------------------
def slide_06():
    body = """
<div style="position:absolute;top:14px;left:0;right:0;padding:0 4px;">
  <div id="s6-title" class="hi t-h2" style="margin-bottom:18px;">
    The normalisation pipeline &mdash; three steps
  </div>
  <div style="display:flex;gap:12px;margin-bottom:18px;">
    <div id="s6-s1" class="hi card" style="flex:1;padding:16px 18px;">
      <div class="t-label t-blue" style="margin-bottom:8px;">Step 1</div>
      <div class="t-mono t-h3" style="font-size:20px;margin-bottom:8px;">.lower()</div>
      <div class="t-small">Folds ALL uppercase to lowercase.<br/>
        <span class="t-mono" style="font-size:13px;">"Flash" &rarr; "flash"</span>
      </div>
    </div>
    <div id="s6-s2" class="hi card" style="flex:1;padding:16px 18px;border-color:rgba(0,197,161,.4);">
      <div class="t-label t-teal" style="margin-bottom:8px;">Step 2 &mdash; KEY STEP</div>
      <div class="t-mono t-h3" style="font-size:20px;margin-bottom:8px;">.split()</div>
      <div class="t-small">Splits on <em>any</em> whitespace: tabs, double spaces, newlines.
        No argument = all whitespace, not just spaces.
      </div>
    </div>
    <div id="s6-s3" class="hi card" style="flex:1;padding:16px 18px;">
      <div class="t-label t-blue" style="margin-bottom:8px;">Step 3</div>
      <div class="t-mono t-h3" style="font-size:20px;margin-bottom:8px;">" ".join()</div>
      <div class="t-small">Rejoins with exactly one space.<br/>
        Canonical form guaranteed.
      </div>
    </div>
  </div>
  <pre id="s6-code" class="hi code" style="font-size:14.5px;">
<span class="kw">def</span> <span class="fn">_cache_key</span>(query: <span class="fn">str</span>) -> <span class="fn">str</span>:
    normalised = <span class="str">" "</span>.join(query.lower().split())
    <span class="kw">return</span> hashlib.sha256(normalised.encode()).hexdigest()</pre>
  <div id="s6-rule" class="hi callout co-teal" style="margin-top:14px;font-size:13.5px;">
    Rule: every cache key derived from user input should normalise before hashing.
  </div>
</div>"""
    events = [
        [500,   "s6-title", "show"],
        [2000,  "s6-s1",    "show"],   # "Query.lower folds..." at ~2.2s offset
        [6000,  "s6-s2",    "show"],   # "Split tokenizes..." at ~6.3s offset
        [22000, "s6-s3",    "show"],   # "' '.join() rejoins..." at ~22.4s offset
        [33000, "s6-code",  "show"],   # "Only then do we hash." at ~33.4s offset
        [41000, "s6-rule",  "show"],   # "This pattern is worth internalizing" at ~42.2s offset
    ]
    return slide_html(6, "CONCEPT: NORMALISATION", "", "", body, events)


# ---------------------------------------------------------------------------
# Slide 7 -- cached_search() Logic  (35s)
# "Here's the public interface. The logic is four lines.
#  Call get() -- if not None, print cache HIT and return it.
#  Otherwise print cache MISS, call search_fn, call put() and return."
# ---------------------------------------------------------------------------
def slide_07():
    fn_code = (
        '<span class="kw">def</span> <span class="fn">cached_search</span>(\n'
        '    query: <span class="fn">str</span>,\n'
        '    search_fn: Callable[[str, int], list[dict]],\n'
        '    ttl: int = DEFAULT_TTL,\n'
        '    max_results: int = <span class="num">10</span>,\n'
        ') -> list[dict]:\n'
        '<span id="s7-hit" class="hl">    cached = <span class="fn">get</span>(query)\n'
        '    <span class="kw">if</span> cached <span class="kw">is not</span> None:\n'
        '        <span class="fn">print</span>(f<span class="str">"[cache HIT ] {query[:55]}"</span>)\n'
        '        <span class="kw">return</span> cached</span>\n'
        '<span id="s7-miss" class="hl">    <span class="fn">print</span>(f<span class="str">"[cache MISS] {query[:55]}"</span>)\n'
        '    results = <span class="fn">search_fn</span>(query, max_results)\n'
        '    <span class="kw">if</span> results:\n'
        '        <span class="fn">put</span>(query, results, ttl=ttl)\n'
        '    <span class="kw">return</span> results</span>'
    )
    body = f"""
<div style="position:absolute;top:10px;left:0;right:500px;padding:0 4px;">
  <div id="s7-hdr" class="hi t-h3" style="margin-bottom:12px;">
    The public interface &mdash; four lines of logic
  </div>
  <pre id="s7-code" class="hi code" style="font-size:13.5px;">{fn_code}</pre>
</div>
<div style="position:absolute;top:10px;left:790px;right:0;padding:0 4px;">
  <div id="s7-hit-card" class="hi callout co-teal" style="margin-bottom:14px;">
    <strong>HIT path:</strong><br/>
    get() returns stored results.<br/>
    No network call. Instant return.
  </div>
  <div id="s7-miss-card" class="hi callout co-red">
    <strong>MISS path:</strong><br/>
    Call the search provider.<br/>
    Store in cache. Return results.
  </div>
  <div id="s7-onechange" class="hi card" style="margin-top:14px;font-size:13px;">
    <div class="t-label" style="margin-bottom:6px;">One-line callsite change</div>
    <span class="t-mono" style="color:var(--c-kw);font-size:12px;">search(q)</span>
    <span style="color:var(--dim)"> &rarr; </span>
    <span class="t-mono" style="color:var(--c-teal,#00c5a1);font-size:12px;">cached_search(q, search)</span>
  </div>
</div>"""
    events = [
        [200,   "s7-hdr",       "show"],
        [1000,  "s7-code",      "show"],
        [1500,  "s7-hit",       "hot"],
        [2500,  "s7-hit-card",  "show"],
        [9000,  "s7-miss",      "hot"],
        [10500, "s7-miss-card", "show"],
        [14000, "s7-onechange", "show"],
    ]
    return slide_html(7, "DEMO: cached_search()", "", "", body, events)


# ---------------------------------------------------------------------------
# Slide 8 -- Design Choices  (45s)
# "Four design choices worth noting.
#  1. search_fn is a Callable -- provider-agnostic.
#  2. Never cache empty results -- empty usually means API failure.
#  3. TTL kwarg -- short for breaking news, long for evergreen topics.
#  4. HIT/MISS labels are structured -- easy to grep, easy to measure."
# ---------------------------------------------------------------------------
def slide_08():
    body = """
<div style="position:absolute;top:10px;left:0;right:0;padding:0 4px;">
  <div id="s8-hdr" class="hi t-h2" style="margin-bottom:18px;">
    4 design choices in <span class="t-mono" style="font-size:26px;">cached_search()</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
    <div id="s8-c1" class="hi card" style="padding:16px 20px;">
      <div class="t-label t-blue" style="margin-bottom:6px;">1 &mdash; search_fn: Callable</div>
      <div class="t-small">Provider-agnostic. Pass DDGS, Tavily, Bing, or a test stub.
      The cache doesn&rsquo;t care which search backend you use.</div>
    </div>
    <div id="s8-c2" class="hi card" style="padding:16px 20px;">
      <div class="t-label t-teal" style="margin-bottom:6px;">2 &mdash; Never cache empty</div>
      <div class="t-small"><span class="t-mono">if results: put(...)</span>
      An empty list usually means the API failed or returned nothing.
      Caching it would block retries for the full TTL window.</div>
    </div>
    <div id="s8-c3" class="hi card" style="padding:16px 20px;">
      <div class="t-label t-blue" style="margin-bottom:6px;">3 &mdash; TTL per query</div>
      <div class="t-small"><strong>Short TTL</strong> for breaking news queries.<br/>
      <strong>Long TTL</strong> for evergreen research topics.<br/>
      Default: 86&thinsp;400 s (24 h).</div>
    </div>
    <div id="s8-c4" class="hi card" style="padding:16px 20px;">
      <div class="t-label t-teal" style="margin-bottom:6px;">4 &mdash; Structured HIT/MISS labels</div>
      <div class="t-small"><span class="t-mono">[cache HIT ]</span> / <span class="t-mono">[cache MISS]</span>
      Easy to grep across multi-session logs. Measure hit rate in production with a one-liner.</div>
    </div>
  </div>
</div>"""
    events = [
        [200,   "s8-hdr", "show"],
        [3000,  "s8-c1",  "show"],
        [22000, "s8-c2",  "show"],
        [37000, "s8-c3",  "show"],
        [52000, "s8-c4",  "show"],
    ]
    return slide_html(8, "DEMO: cached_search()", "", "", body, events)


# ---------------------------------------------------------------------------
# Slide 9 -- Quiz  (70s)
# "Your cached_search() uses SHA-256 of the raw query string.
#  Session 1: 'Flash Attention'. Session 2: 'flash attention'. Cache MISS.
#  Root cause + correct fix?  -- Answer: B (normalise before hashing)"
# ---------------------------------------------------------------------------
def slide_09():
    opts = [
        ("A", "Use SQLite LIKE instead of WHERE key=? for case-insensitive lookup.", False),
        ("B", "Key was computed from the raw string. Fix: normalise with "
              "<code style='color:#00c5a1;font-family:monospace'>"
              "\" \".join(query.lower().split())</code> before hashing.", True),
        ("C", "Increase the TTL so session 2 is more likely to find a live entry.", False),
        ("D", "Store the raw query string as the key instead of a hash.", False),
    ]
    opts_html = "".join(
        f'<div id="s9-o{i}" class="hi qopt">'
        f'<div class="opt-letter">{letter}</div>'
        f'<div class="opt-text">{text}</div></div>'
        for i, (letter, text, _) in enumerate(opts)
    )
    body = f"""
<div style="position:absolute;top:6px;left:0;right:0;padding:0 4px;">
  <div id="s9-q" class="hi card" style="margin-bottom:14px;font-size:15px;line-height:1.75;padding:18px 22px;">
    Your <span class="t-mono t-blue">cached_search()</span> uses SHA-256 of the
    <em>raw</em> query string as the key. A user submits
    <strong>&ldquo;Flash Attention&rdquo;</strong> in session&nbsp;1 and
    <strong>&ldquo;flash attention&rdquo;</strong> in session&nbsp;2.
    The cache returns MISS and makes a second API call.
    <br/><strong>What is the root cause and the correct fix?</strong>
  </div>
  {opts_html}
</div>"""
    events = [
        [200,   "s9-q",  "show"],
        [22000, "s9-o0", "show"],
        [23000, "s9-o1", "show"],
        [24000, "s9-o2", "show"],
        [25000, "s9-o3", "show"],
        [40000, "s9-o1", "correct"],
        [50000, "s9-o0", "wrong"],
        [51000, "s9-o2", "wrong"],
        [52000, "s9-o3", "wrong"],
    ]
    return slide_html(9, "QUIZ", "", "", body, events)


# ---------------------------------------------------------------------------
# Slide 10 -- Recap  (65s)
# "You can explain why repeated queries waste calls. You can normalise
#  with ' '.join(query.lower().split()). You can implement get()+put().
#  You can wrap any search callable with cached_search(). TTL tunable."
# ---------------------------------------------------------------------------
def slide_10():
    points = [
        ("Explain",     "Why repeated auto-research queries waste API calls across sessions and why the failure is silent."),
        ("Normalise",   "<span class='t-mono' style='color:#00c5a1'>\" \".join(query.lower().split())</span> before SHA-256 &mdash; same intent always maps to the same key."),
        ("Implement",   "<span class='t-mono t-blue'>get()</span> with expiry check and <span class='t-mono t-blue'>put()</span> with upsert + inline eviction."),
        ("Wrap",        "Any search callable with <span class='t-mono t-blue'>cached_search(q, search_fn)</span> in a one-line change &mdash; provider-agnostic and TTL-tunable."),
    ]
    items = "".join(
        f"""<div id="s10-p{i}" class="hi" style="display:flex;gap:12px;margin-bottom:12px;align-items:flex-start;">
          <div class="step-dot"></div>
          <div>
            <span class="t-navy" style="font-weight:700;">{verb}:</span>
            <span class="t-small"> {desc}</span>
          </div>
        </div>"""
        for i, (verb, desc) in enumerate(points)
    )
    body = f"""
<div style="position:absolute;top:14px;left:0;right:0;padding:0 4px;">
  <div id="s10-hdr" class="hi t-h2" style="margin-bottom:22px;">
    What you can do now that you couldn&rsquo;t before:
  </div>
  {items}
  <div id="s10-stat" class="hi callout co-teal" style="margin-top:16px;font-size:14.5px;">
    Sessions 2&ndash;N: <strong>0 API calls</strong> for any repeated query within the TTL window.
    &nbsp; A 12-query loop &times; 10 sessions = <strong>108 API calls saved</strong> per topic per day.
  </div>
</div>"""
    delays = [2000, 10000]
    events = [
        [200, "s10-hdr", "show"],
    ] + [[delays[i], f"s10-p{i}", "show"] for i in range(2)]
    return slide_html(10, "RECAP", "teal", "teal", body, events)


# ---------------------------------------------------------------------------
# Slide 11 -- Outro  (14.22s)
# ---------------------------------------------------------------------------
def slide_11():
    body = """
<div style="position:absolute;top:0;left:0;right:0;bottom:0;
     display:flex;flex-direction:column;align-items:center;justify-content:center;">
  <div id="s11-ty" class="hi t-h2" style="margin-bottom:14px;text-align:center;">
    Thank you for watching.
  </div>
  <div id="s11-where" class="hi t-body" style="text-align:center;margin-bottom:18px;">
    Lesson materials &mdash; starter code &amp; solution &mdash; in
    <span class="t-mono t-blue">micro-lesson/course-4/</span>
  </div>
  <div id="s11-see" class="hi t-h3 t-teal" style="text-align:center;">
    See you in the next lesson.
  </div>
</div>"""
    events = [
        [200,  "s11-ty",    "show"],
        [2500, "s11-where", "show"],
        [6000, "s11-see",   "show"],
    ]
    return slide_html(11, "OUTRO", "teal", "teal", body, events)


# ---------------------------------------------------------------------------
# Slide registry  -- keyed by slide_num in SEGMENTS
# ---------------------------------------------------------------------------
SLIDE_GENERATORS = {
    1: slide_01, 2: slide_02, 3: slide_03,  4: slide_04,
    5: slide_05, 6: slide_06, 7: slide_07,  8: slide_08,
    9: slide_09, 10: slide_10, 11: slide_11,
}


def generate_slides():
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    for num, gen in SLIDE_GENERATORS.items():
        p = SLIDES_DIR / f"slide{num:02d}.html"
        p.write_text(gen(), encoding="utf-8")
        print(f"  wrote {p.name}")
        paths[num] = p
    return paths


# ===========================================================================
# recording helpers (Python playwright -- in-process, headless, no popups)
# ===========================================================================
def record_segment(browser, slide_num, url, duration_s):
    webm_out = SEGMENTS_DIR / f"seg{slide_num:02d}.webm"
    print(f"  [slide {slide_num:02d}] navigate -> {url}")
    ctx = browser.new_context(
        viewport={"width": W, "height": H},
        device_scale_factor=1.5,
        record_video_dir=str(SEGMENTS_DIR),
        record_video_size={"width": W, "height": H},
    )
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    time.sleep(2.5)   # let Google Fonts settle
    print(f"  [slide {slide_num:02d}] video-start, recording {duration_s:.1f}s ...")
    page.evaluate("play(window.__EVENTS__)")
    time.sleep(duration_s)
    ctx.close()   # flushes video to disk
    recorded = Path(page.video.path())
    shutil.move(str(recorded), webm_out)
    print(f"  [slide {slide_num:02d}] saved -> {webm_out.name}")
    return webm_out


# ===========================================================================
# ffmpeg helpers
# ===========================================================================
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_SI = None
if sys.platform == "win32":
    _SI = subprocess.STARTUPINFO()
    _SI.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _SI.wShowWindow = 0  # SW_HIDE


def ff(*args):
    cmd = ["ffmpeg", "-y"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, startupinfo=_SI)
    if result.returncode != 0:
        print(f"  [ffmpeg err]\n{result.stderr[-600:]}", file=sys.stderr)
        raise RuntimeError("ffmpeg failed")
    return result


def extract_audio_clip(src_wav, ss, duration, out_wav):
    """Extract [ss, ss+duration] from a PCM WAV and resample to 48 kHz stereo."""
    ff("-ss", str(ss), "-t", str(duration),
       "-i", str(src_wav),
       "-ar", "48000", "-ac", "2",
       str(out_wav))


def merge_av(webm, wav, out):
    """Merge screen recording + audio clip into a single .mp4 segment."""
    ff("-i", str(webm), "-i", str(wav),
       "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-preset", "fast", "-crf", "15",
       "-c:a", "aac", "-ar", "48000", "-ac", "2",
       "-shortest", "-movflags", "+faststart",
       str(out))
    print(f"  merged -> {out.name}")


def concat_segments(mp4_files, final):
    concat_list = SEGMENTS_DIR / "concat_list.txt"
    lines = [f"file '{str(p).replace(chr(92), '/')}'" for p in mp4_files]
    concat_list.write_text("\n".join(lines), encoding="utf-8")
    ff("-f", "concat", "-safe", "0",
       "-i", str(concat_list),
       "-c", "copy", str(final))
    print(f"  final -> {final}")


def overlay_avatar(mp4_in, avatar, t_start, t_end, size, pad, mp4_out):
    """Composite a circular headshot in the lower-left corner for [t_start, t_end]."""
    flt = (
        f"[1:v]scale={size}:{size}:flags=lanczos,format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'"
        f":a='255*lte(hypot(X-W/2\\,Y-H/2)\\,W/2-2)'[av];"
        f"[0:v][av]overlay=x={pad}:y=main_h-{size}-{pad}"
        f":enable='between(t\\,{t_start}\\,{t_end})'"
    )
    ff("-i", str(mp4_in), "-i", str(avatar),
       "-filter_complex", flt,
       "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-preset", "fast", "-crf", "15",
       "-c:a", "copy",
       "-movflags", "+faststart",
       str(mp4_out))
    print(f"  avatar overlay -> {mp4_out.name}")


# ===========================================================================
# Main pipeline
# ===========================================================================
def main():
    print("=== Course 4 Micro-Lesson Video Production ===\n")

    # 1. Generate slides
    print("[1/4] Generating animated HTML slides ...")
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    slide_paths = generate_slides()

    # 2. Start HTTP server
    start_http_server()
    time.sleep(0.5)   # let server bind

    # 3. Record slides with headless Chromium via Python playwright
    print("\n[2/4] Recording slides via playwright ...")
    webm_files = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-gpu"])
        for (slide_num, audio_file, audio_ss, audio_dur, rec_dur) in SEGMENTS:
            url     = slide_url(slide_num)
            src_wav = NOTES_DIR / audio_file
            if not src_wav.exists():
                print(f"  WARNING: audio not found: {src_wav}", file=sys.stderr)
                continue
            try:
                webm = record_segment(browser, slide_num, url, rec_dur)
                webm_files.append((slide_num, webm, src_wav, audio_ss, audio_dur))
            except Exception as e:
                print(f"  ERROR slide {slide_num}: {e}", file=sys.stderr)
                break
        browser.close()

    # 4. Extract audio sub-clips + merge with video
    print("\n[3/4] Extracting audio clips and merging ...")
    mp4_files = []
    for (slide_num, webm, src_wav, ss, dur) in webm_files:
        clip_wav = SEGMENTS_DIR / f"seg{slide_num:02d}_clip.wav"
        out_mp4  = SEGMENTS_DIR / f"seg{slide_num:02d}.mp4"
        try:
            extract_audio_clip(src_wav, ss, dur, clip_wav)
            merge_av(webm, clip_wav, out_mp4)
            if slide_num == 1 and HEADSHOT_PATH.exists():
                tmp = out_mp4.with_suffix(".tmp.mp4")
                overlay_avatar(out_mp4, HEADSHOT_PATH,
                               AVATAR_T_START, AVATAR_T_END,
                               AVATAR_SIZE, AVATAR_PAD, tmp)
                tmp.replace(out_mp4)
            mp4_files.append(out_mp4)
        except Exception as e:
            print(f"  ERROR merging slide {slide_num}: {e}", file=sys.stderr)

    if not mp4_files:
        print("No segments to concatenate.", file=sys.stderr)
        sys.exit(1)

    # 5. Concatenate
    print("\n[4/4] Concatenating segments ...")
    FINAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    concat_segments(mp4_files, FINAL_OUT)

    total_s = sum(dur for _, _, _, _, dur in SEGMENTS)
    m, s = divmod(int(total_s), 60)
    print(f"\nDone.  Runtime: {m}:{s:02d}   Output: {FINAL_OUT}")


if __name__ == "__main__":
    main()
