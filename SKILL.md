---
name: bilibili-to-doc-local
description: >
  Use when the user asks to "提取B站视频", "B站视频转文档", "把B站视频整理成笔记",
  or provides a bilibili.com URL and wants a structured Markdown document.
  Prefers Bilibili AI subtitles via yt-dlp and browser cookies, falls back to the
  player API and then to local Whisper speech recognition, and writes a Markdown
  document plus a readable HTML page.
version: 0.1.0
---

# Bilibili Video to Document (local variant)

## Overview

This skill turns a Bilibili video or a time range of it into a structured document.
It is the second workflow in this project and follows the approach of
`programmerloverun/bilibili-to-doc`: try the video's AI subtitles first, then organize
the transcript into a document. Unlike that skill it also works without cookies by
falling back to local speech recognition, and it supports precise time ranges.

## Prerequisites

- Python 3.10+ with `workflow/requirements.txt` installed.
- `ffmpeg` in `PATH`.
- Login cookies for Bilibili AI subtitles (optional but recommended). AI subtitles
  are only exposed to signed-in accounts.

## Workflow

### Step 1: Extract the transcript and draft document

```powershell
python workflow/bili_to_doc.py "<BILIBILI_URL>" `
  --start 20:40 --end 21:36 `
  --outline workflow/outline.example.json `
  --model large-v3
```

What happens:

1. `yt-dlp` is invoked with `--cookies-from-browser` (default `auto`) and
   `--write-subs --sub-langs ai-zh,zh.*` to fetch the AI subtitle track.
2. If that fails, the player API is queried with `--sessdata` / `BILI_SESSDATA`.
3. If no subtitles are available, only the requested audio range is downloaded and
   transcribed with faster-whisper.
4. The transcript is split into chapters from `--outline`, or automatically by pauses.
5. `output/<stem>.doc.md` and `output/<stem>.doc.html` are written.

Relevant options:

| Option | Purpose |
| --- | --- |
| `--start` / `--end` | Time range; omit both for the whole video. |
| `--outline FILE` | JSON sections: `[{"start":"20:40","title":"..."}]`. |
| `--cookies-from-browser` | `chrome`, `edge`, `firefox`, `safari`, `auto`, `none`. |
| `--cookies FILE` | Netscape `cookies.txt` exported from a browser. |
| `--sessdata VALUE` | Bilibili `SESSDATA` cookie. |
| `--no-cookies` | Skip AI subtitles and use the API / ASR. |
| `--glossary FILE` | Name-correction dictionary, default `glossary.json`. |
| `--prompt` / `--hotwords` | Bias ASR towards names and terms. |
| `--force-asr` | Ignore subtitles and always use ASR. |

### Step 2: Polish the document

The script produces a faithful draft. Read `output/<stem>.doc.md` and improve it:

- Give each section a clear, content-based heading if auto chapters were used.
- Merge short sections, split long ones, and remove verbal fillers or repetition.
- Use tables for comparisons and fenced code blocks for commands, config, or code.
- Keep timestamps and facts; do not invent content that is not in the transcript.
- Keep the metadata block and the closing disclaimer intact.

Write the polished result back to the same `.doc.md`, then regenerate the HTML:

```powershell
python workflow/render_doc.py "output/<stem>.doc.md"
```

### Step 3: Report

Tell the user where the `.doc.md` and `.doc.html` files are, which extraction mode
was used (AI subtitles or ASR), and any uncertain names that need review.

## Cookie setup

AI subtitles require a signed-in account. Prefer a browser that `yt-dlp` can read:

```powershell
python workflow/bili_to_doc.py "<URL>" --cookies-from-browser edge
```

If Chrome or Edge is running with app-bound encryption, cookie extraction may fail.
In that case export `cookies.txt` with a browser extension and pass `--cookies`, or
copy `SESSDATA` from the browser developer tools and use `--sessdata`.

## Notes

- AI subtitles are used verbatim and are the most accurate source.
- ASR output is approximate, especially for proper nouns. Extend `glossary.json`.
- The last sentence may be truncated when the end time cuts a sentence.
