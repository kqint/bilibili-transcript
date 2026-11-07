# Bilibili Video to Text

English | [简体中文](README.zh-CN.md)

Turn any segment of a Bilibili video into a clean transcript, then render it as a
self-contained, readable HTML page. The tool first looks for official or AI subtitle
tracks; when none are available it downloads only the requested audio range and runs
local speech recognition with faster-whisper.

## Features

- Range selection with `20:40`, `1:02:03` or plain seconds.
- Official subtitle extraction with optional `SESSDATA` cookie.
- Automatic fallback to local ASR, downloading only the requested audio slice.
- faster-whisper with CUDA acceleration and automatic CPU fallback.
- Editable glossary to fix names and terminology that ASR mishears.
- Plain text output plus a polished HTML reader (dark mode, copy button, timestamp links).
- Localized interface and document labels (`en`, `zh-CN`).

## Two Workflows

| Workflow | Entry point | Output | Best for |
| --- | --- | --- | --- |
| Transcript (v1) | `workflow/bili_transcript.py` | `.txt` + `.html` | Exact time range, quick reading, timestamps |
| Structured document (v2) | `workflow/bili_to_doc.py` | `.doc.md` + `.doc.html` | Notes, articles, chaptered documents |

Both share subtitle lookup, audio slicing, speech recognition, the glossary, and the locales.
Version 2 follows the approach of [bilibili-to-doc](https://github.com/programmerloverun/bilibili-to-doc):
it prefers Bilibili AI subtitles fetched with `yt-dlp` and browser cookies, then the player
API, and only then falls back to local ASR.

## Requirements

- Python 3.10 or newer.
- `ffmpeg` available in `PATH`.
- Optional: an NVIDIA GPU for much faster recognition.

## Install

```powershell
python -m pip install -r workflow/requirements.txt
```

For GPU acceleration install the CUDA runtime wheels as well:

```powershell
python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

## Usage

```powershell
python workflow/bili_transcript.py "https://www.bilibili.com/video/BV1c1WAzXEGE" 20:40 21:36
```

Files are written to `output/`:

- `BV1c1WAzXEGE_20m40s-21m36s.txt` - full transcript and timed lines.
- `BV1c1WAzXEGE_20m40s-21m36s.html` - readable page, open it in any browser.

Or use the convenience wrapper:

```powershell
.\run.ps1 "https://www.bilibili.com/video/BV1c1WAzXEGE" 20:40 21:36
```

## Version 2: Structured Document

```powershell
python workflow/bili_to_doc.py "https://www.bilibili.com/video/BV1c1WAzXEGE" `
  --start 20:40 --end 21:36 `
  --outline workflow/outline.example.json
```

This writes `output/BV1c1WAzXEGE_20m40s-21m36s.doc.md` and `.doc.html`. The document has a
metadata block, a table of contents, chapter headings, the full text, a timestamped
timeline, and a source link.

Options specific to this workflow:

| Option | Description |
| --- | --- |
| `--start`, `--end` | Time range; omit both to document the whole video. |
| `--outline FILE` | JSON sections: `[{"start":"20:40","title":"..."}]`. |
| `--cookies-from-browser` | `chrome`, `edge`, `firefox`, `safari`, `auto` or `none`. |
| `--cookies FILE` | Netscape `cookies.txt` exported from a browser. |
| `--no-cookies` | Skip AI subtitle download and use the API / ASR. |
| `--no-asr` | Fail instead of falling back to speech recognition. |

Edit the generated `.doc.md` to refine headings or add tables and code blocks, then
rebuild the HTML:

```powershell
python workflow/render_doc.py "output/BV1c1WAzXEGE_20m40s-21m36s.doc.md"
```

`SKILL.md` describes the same flow as an agent skill, including the polish step. `run-doc.ps1` is a PowerShell wrapper: `

```powershell
.\run-doc.ps1 "<URL>" 20:40 21:36 --outline workflow/outline.example.json
```


### Obtaining cookies for AI subtitles

Bilibili only exposes AI subtitles to signed-in accounts. `SESSDATA` is the cookie
that matters because it carries the login token; `DedeUserID` is optional and
`buvid3`/`buvid4` are generated automatically. Exporting all `bilibili.com` cookies
is the simplest and most reliable option.

#### Option 1: export cookies.txt (recommended)

1. Install the Chrome extension `Get cookies.txt LOCALLY`.
2. Sign in at bilibili.com.
3. Click the extension icon and export cookies for `bilibili.com`.
4. Save the file as `cookies.txt` and pass it to the workflow.

```powershell
python workflow/bili_to_doc.py "<URL>" --start 20:40 --end 21:36 --cookies "cookies.txt"
```

#### Option 2: copy SESSDATA only

1. Sign in at bilibili.com and press `F12`.
2. Open Application -> Cookies -> `https://www.bilibili.com`.
3. Copy the value of `SESSDATA`.
4. Pass it with `--sessdata` or set `BILI_SESSDATA`.

```powershell
python workflow/bili_to_doc.py "<URL>" --start 20:40 --end 21:36 --sessdata "<value>"
# or
$env:BILI_SESSDATA = "<value>"
```

A minimal Netscape cookie file is also accepted (fields are tab-separated):

```text
# Netscape HTTP Cookie File
.bilibili.com	TRUE	/	TRUE	2147483647	SESSDATA	<value>
```

#### Option 3: read cookies directly from the browser

```powershell
python workflow/bili_to_doc.py "<URL>" --cookies-from-browser chrome
```

Supported values: `chrome`, `edge`, `firefox`, `safari`, `auto`, `none`. Chrome 127+
uses app-bound encryption and locks its cookie database while running, so this often
fails with `Could not copy Chrome cookie database`. Close Chrome and retry, or use
Option 1.

#### Verify the cookie

```powershell
python workflow/bili_to_doc.py "<URL>" --start 20:40 --end 21:36 --cookies "cookies.txt" --no-asr
```

If AI subtitles are reachable the command finishes and the document header reports
`Bilibili AI subtitles`. Exit code 2 means the video has no accessible AI subtitle,
in which case rerun without `--no-asr` to fall back to local ASR.

Cookies also unlock higher-bitrate DASH audio, which improves speech recognition even
when the video has no subtitles. A `cookies.txt` file is equivalent to your account
login: never commit or share it. It is already listed in `.gitignore`.

## Options

| Option | Description |
| --- | --- |
| `--page N` | Select a part of a multi-part video (1-based). |
| `--model NAME` | Whisper model, default `large-v3`. Use `small`/`medium` on weak hardware. |
| `--device auto\|cpu\|cuda` | Recognition device, default `auto`. |
| `--language zh` | Speech language hint. |
| `--prompt TEXT` | Initial prompt that biases names and terms. |
| `--hotwords TEXT` | Space-separated hotwords for the decoder. |
| `--glossary PATH` | JSON glossary of replacements; defaults to `glossary.json`. |
| `--sessdata VALUE` | Bilibili `SESSDATA` cookie, also read from `BILI_SESSDATA`. |
| `--no-asr` | Only use official subtitles, never fall back to ASR. |
| `--force-asr` | Ignore official subtitles and always use ASR. |
| `--out-dir PATH` | Output directory, default `output/`. |
| `--name STEM` | Override the output file stem. |
| `--keep-audio` | Keep the extracted audio segment in the output directory. |
| `--ui-lang en\|zh-CN` | Interface language, default `zh-CN`. |

## How It Works

1. Parse the BV id, resolve `aid`/`cid`, and read the video title and parts.
2. Request the player API for subtitle tracks. With a valid `SESSDATA` cookie this
   returns official or AI subtitles for many videos.
3. If no subtitles are available, request the playable stream and use `ffmpeg` to
   extract only the requested seconds as 16 kHz mono WAV.
4. Transcribe with faster-whisper, then normalize punctuation and apply the glossary.
5. Write the text file and the HTML page.

## Glossary

`glossary.json` maps misheard strings to corrected ones:

```json
{
  "replace": {
    "新闻公作者": "新闻工作者",
    "田竄": "田端"
  }
}
```

Keep entries short and specific so they do not damage correct text.

## Getting SESSDATA

1. Sign in to bilibili.com in your browser.
2. Open developer tools, go to Application, Cookies, `https://www.bilibili.com`.
3. Copy the `SESSDATA` value.

Then either set the environment variable:

```powershell
$env:BILI_SESSDATA = "your-value"
```

or pass `--sessdata "your-value"`.

Without it the tool still works through ASR.

## Troubleshooting

- `Library cublas64_12.dll is not found`: install the CUDA wheels above, or run with
  `--device cpu`.
- Slow first run: the Whisper model is downloaded once and cached by Hugging Face.
  Set `HF_ENDPOINT=https://hf-mirror.com` if the download is slow.
- `ffmpeg not found`: install ffmpeg and make sure it is in `PATH`.
- Names are wrong: add glossary entries or pass `--prompt` / `--hotwords`.
- The range ends mid-sentence: increase the end time by a few seconds.

## Project Structure

```
bili_transcript.py    v1 CLI: transcript + HTML
bili_to_doc.py        v2 CLI: structured Markdown document + HTML
render_doc.py         Re-render a .doc.md as the styled HTML page
requirements.txt      Python dependencies
glossary.json         ASR correction dictionary
locales/              UI strings (en, zh-CN)
output/               Generated transcripts and documents
run.ps1               PowerShell wrapper for v1
SKILL.md              Agent skill definition for the v2 workflow
```

## Notes

Official subtitles are used verbatim when available. ASR output is approximate,
especially for proper nouns; review the text before publishing.
