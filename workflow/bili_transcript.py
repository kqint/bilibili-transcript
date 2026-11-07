#!/usr/bin/env python3
"""Extract a Bilibili video transcript for a time range and render it as text + HTML.

Strategy:
1. Ask the Bilibili player API for official / AI subtitle tracks (needs SESSDATA).
2. If none are available, download only the requested audio segment and run
   faster-whisper speech recognition.
3. Emit a plain text file and a self-contained, readable HTML page.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency 'requests'. Run: pip install -r workflow/requirements.txt")


ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "locales"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
API = "https://api.bilibili.com"

STYLE_CSS = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --fg:#1f2329; --muted:#6b7280;
  --accent:#00a1d6; --border:#e5e7eb; --code:#f3f4f6;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#14161a; --card:#1b1e24; --fg:#e6e8eb; --muted:#9aa3af;
         --accent:#25b7e6; --border:#2b3038; --code:#22262d; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:16px/1.9 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC","Microsoft YaHei",sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:40px 20px 80px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:32px 34px;box-shadow:0 8px 30px rgba(0,0,0,.06)}
.brand{display:inline-block;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);font-weight:700;margin-bottom:12px}
h1{font-size:28px;line-height:1.35;margin:0 0 18px}
.meta{display:flex;flex-wrap:wrap;gap:8px 10px;margin:0 0 8px;padding:0;list-style:none}
.meta li{background:var(--code);color:var(--muted);border-radius:999px;
  padding:4px 12px;font-size:13px}
.meta a{color:var(--accent);text-decoration:none}
h2{font-size:18px;margin:36px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.toolbar{display:flex;justify-content:flex-end;margin-top:36px}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;
  padding:8px 16px;font-size:14px;cursor:pointer}
button:hover{filter:brightness(1.08)}
.transcript p{margin:0 0 1.05em;text-align:justify}
.transcript p:first-child::first-letter{font-weight:700}
ol.timed{list-style:none;margin:0;padding:0}
ol.timed li{display:flex;gap:14px;padding:7px 0;border-bottom:1px dashed var(--border)}
ol.timed li:last-child{border-bottom:0}
ol.timed .t{flex:0 0 64px;color:var(--accent);font-variant-numeric:tabular-nums;
  font-size:14px;text-decoration:none;padding-top:2px}
ol.timed .x{flex:1}
footer{margin-top:26px;color:var(--muted);font-size:13px;text-align:center}
@media (max-width:600px){.card{padding:22px 18px;border-radius:12px}h1{font-size:22px}}
"""

COPY_JS = """
function copyText(){
  const el=document.getElementById('fulltext');
  const text=el.innerText.replace(/\\n{3,}/g,'\\n\\n').trim();
  navigator.clipboard.writeText(text).then(()=>{
    const b=document.getElementById('copy');
    const old=b.dataset.label;
    b.textContent=b.dataset.copied;
    setTimeout(()=>{b.textContent=old;},1500);
  });
}
"""


def load_locales(ui_lang: str) -> dict:
    lang = ui_lang if (LOCALES_DIR / f"{ui_lang}.json").exists() else "en"
    with open(LOCALES_DIR / f"{lang}.json", encoding="utf-8") as fh:
        return json.load(fh)


def t(loc: dict, key: str, **kw) -> str:
    node = loc
    for part in key.split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    text = node if isinstance(node, str) else key
    try:
        return text.format(**kw)
    except (KeyError, IndexError):
        return text


_LOC: dict = {}


def T(key: str, **kw) -> str:
    return t(_LOC, key, **kw)


def detect_ui_lang(argv: list[str]) -> str:
    for index, item in enumerate(argv):
        if item == "--ui-lang" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--ui-lang="):
            return item.split("=", 1)[1]
    return "zh-CN"


def parse_time(value: str) -> float:
    value = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)
    parts = value.split(":")
    if not 1 < len(parts) <= 3:
        raise ValueError(T("error.bad-time", value=value))
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def fmt_clock(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_file_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    out = f"{m}m{s:02d}s"
    return f"{h}h{out}" if h else out


def extract_bvid(text: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if not match:
        raise ValueError(T("error.no-bv"))
    return match.group(1)


class BiliError(RuntimeError):
    pass


class BiliClient:
    def __init__(self, sessdata: str | None = None) -> None:
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        if sessdata:
            self.http.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
        try:
            spi = self.http.get(f"{API}/x/frontend/finger/spi", timeout=15).json()
            data = spi.get("data") or {}
            if data.get("b_3"):
                self.http.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
            if data.get("b_4"):
                self.http.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
        except requests.RequestException:
            pass

    def get(self, path: str, params: dict, referer: str | None = None) -> dict:
        headers = {"Referer": referer} if referer else None
        resp = self.http.get(f"{API}{path}", params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise BiliError(f"{payload.get('code')}: {payload.get('message')}")
        return payload.get("data") or {}

    def view(self, bvid: str) -> dict:
        return self.get("/x/web-interface/view", {"bvid": bvid})

    def subtitles(self, bvid: str, cid: int) -> list[dict]:
        url = f"https://www.bilibili.com/video/{bvid}"
        data = self.get("/x/player/v2", {"bvid": bvid, "cid": cid}, referer=url)
        sub = data.get("subtitle") or {}
        return sub.get("subtitles") or []

    def fetch_subtitle_body(self, url: str, bvid: str) -> list[dict]:
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        resp = self.http.get(
            url,
            headers={"Referer": f"https://www.bilibili.com/video/{bvid}", "User-Agent": UA},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json().get("body") or []
        return [
            {"from": float(item.get("from", 0)), "to": float(item.get("to", 0)),
             "text": (item.get("content") or "").strip()}
            for item in body
            if (item.get("content") or "").strip()
        ]

    def audio_segments(self, bvid: str, cid: int) -> list[dict]:
        url = f"https://www.bilibili.com/video/{bvid}"

        try:
            dash_data = self.get(
                "/x/player/playurl",
                {"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1},
                referer=url,
            )
        except BiliError:
            dash_data = {}
        audios = (dash_data.get("dash") or {}).get("audio") or []
        if audios:
            best = max(audios, key=lambda item: item.get("bandwidth") or 0)
            return [{
                "url": best["baseUrl"].replace("http://", "https://", 1),
                "length": dash_data.get("timelength") or 0,
            }]

        params = {
            "bvid": bvid, "cid": cid, "fnval": 0, "qn": 16,
            "platform": "html5", "high_quality": 1,
        }
        data = self.get("/x/player/playurl", params, referer=url)
        segments = data.get("durl") or []
        if not segments:
            raise BiliError(T("error.playurl"))
        for seg in segments:
            seg["url"] = seg["url"].replace("http://", "https://", 1)
        return segments


def pick_subtitle(tracks: list[dict]) -> dict | None:
    if not tracks:
        return None
    for track in tracks:
        if str(track.get("lan", "")).lower().startswith("zh"):
            return track
    return tracks[0]


def ffmpeg_headers(url_ref: str) -> str:
    return f"Referer: {url_ref}\r\nUser-Agent: {UA}\r\n"


def check_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(T("error.ffmpeg-missing"))
    return exe


def extract_audio(
    segments: list[dict], start: float, end: float, out_wav: Path, workdir: Path
) -> Path:
    ffmpeg = check_ffmpeg()
    pieces: list[Path] = []
    cursor = 0.0
    for idx, seg in enumerate(segments):
        length = float(seg.get("length") or 0) / 1000.0
        seg_start, seg_end = cursor, cursor + length
        cursor = seg_end
        if seg_end <= start or seg_start >= end:
            continue
        local_start = max(start, seg_start) - seg_start
        duration = min(end, seg_end) - max(start, seg_start)
        if duration <= 0.2:
            continue
        piece = workdir / f"part{idx}.wav"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-headers", ffmpeg_headers("https://www.bilibili.com/"),
            "-ss", f"{local_start:.3f}", "-t", f"{duration:.3f}",
            "-i", seg["url"],
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(piece),
        ]
        subprocess.run(cmd, check=True)
        if piece.exists() and piece.stat().st_size > 1024:
            pieces.append(piece)
    if not pieces:
        raise RuntimeError(T("error.audio-extract"))
    if len(pieces) == 1:
        pieces[0].replace(out_wav)
        return out_wav
    concat_list = workdir / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in pieces), encoding="utf-8"
    )
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
         "-safe", "0", "-i", str(concat_list), "-c", "copy", str(out_wav)],
        check=True,
    )
    return out_wav


def collapse_repeats(text: str) -> str:
    """Collapse immediate duplicate phrases produced by Whisper loops."""
    text = text.strip()
    if not text:
        return text
    pattern = re.compile(r"(.{4,40}?)(?:[，,、；;]?\1)+")
    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(r"\1", text)
    return text


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：、）】》])", "", text)
    text = re.sub(r"(?<=[（【《])\s+", "", text)
    if re.search(r"[\u4e00-\u9fff]", text):
        for src, dst in ((",", "，"), (";", "；"), (":", "："), ("?", "？"), ("!", "！")):
            text = text.replace(src, dst)
    return text


def load_glossary(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    mapping = data.get("replace", data) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in mapping.items() if k}


def apply_glossary(lines: list[dict], mapping: dict[str, str]) -> None:
    if not mapping:
        return
    keys = sorted(mapping, key=len, reverse=True)
    for line in lines:
        for key in keys:
            line["text"] = line["text"].replace(key, mapping[key])


def optional_simplify(text: str) -> str:
    try:
        import zhconv  # type: ignore
    except ImportError:
        return text
    return zhconv.convert(text, "zh-cn")


def enable_nvidia_dlls() -> None:
    """Make pip-installed CUDA runtime DLLs discoverable on Windows."""
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    if not spec or not spec.submodule_search_locations:
        return
    base = Path(list(spec.submodule_search_locations)[0])
    for sub in ("cublas", "cudnn", "cuda_runtime"):
        for folder in (base / sub / "bin", base / sub / "lib"):
            if not folder.is_dir():
                continue
            try:
                os.add_dll_directory(str(folder))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


def transcribe(
    wav: Path, model_name: str, device: str, language: str, loc: dict, log,
    hotwords: str | None = None, prompt: str | None = None,
) -> list[dict]:
    enable_nvidia_dlls()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit(T("error.whisper-missing"))

    candidates: list[tuple[str, str]] = []
    if device == "auto":
        try:
            import ctranslate2  # type: ignore

            if ctranslate2.get_cuda_device_count() > 0:
                candidates.append(("cuda", "float16"))
        except Exception:
            pass
        candidates.append(("cpu", "int8"))
    elif device == "cuda":
        candidates.append(("cuda", "float16"))
    else:
        candidates.append(("cpu", "int8"))

    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
        channels = handle.getnchannels()
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width, np.int16)
    audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    if dtype is not np.int8:
        audio = audio / float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    last_error: Exception | None = None
    for dev, compute in candidates:
        try:
            log(t(loc, "log.loading-model", model=model_name, device=dev))
            model = WhisperModel(model_name, device=dev, compute_type=compute)
            segments, _info = model.transcribe(
                audio,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                initial_prompt=prompt or "以下是普通话的句子，请使用简体中文并加上标点。",
                hotwords=hotwords,
                condition_on_previous_text=True,
                no_repeat_ngram_size=4,
                repetition_penalty=1.02,
            )
            results = [
                {"from": float(s.start), "to": float(s.end),
                 "text": optional_simplify(normalize_text(collapse_repeats(s.text)))}
                for s in segments
                if s.text and s.text.strip()
            ]
            if dev == "cuda":
                log(t(loc, "log.device-cuda"))
            return results
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if dev == "cuda":
                log(t(loc, "log.cuda-fallback"))
    raise RuntimeError(T("error.asr-failed", error=last_error))


def clip_lines(lines: list[dict], start: float, end: float, offset: float = 0.0) -> list[dict]:
    out = []
    for line in lines:
        line_start = line["from"] + offset
        line_end = line["to"] + offset
        if line_end < start - 0.05 or line_start > end + 0.05:
            continue
        text = line["text"].strip()
        if not text:
            continue
        out.append({"from": max(line_start, start), "to": min(line_end, end), "text": text})
    return out


def join_lines(lines: list[dict]) -> str:
    buf = ""
    for line in lines:
        text = line["text"].strip()
        if not text:
            continue
        if not buf:
            buf = text
        elif re.search(r"[，。！？!?；;：:、…]$", buf) or re.match(r"[，。！？!?；;：:、…]", text):
            buf += text
        else:
            buf += ("，" if re.search(r"[\u4e00-\u9fff]$", buf) else " ") + text
    return buf


def build_paragraphs(lines: list[dict], max_chars: int = 110) -> list[str]:
    sentences = re.findall(r"[^。！？!?；;…]+[。！？!?；;…]*", join_lines(lines))
    paragraphs: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) > max_chars:
            paragraphs.append(current)
            current = sentence
        else:
            current += sentence
    if current.strip():
        paragraphs.append(current.strip())
    return paragraphs


def write_txt(
    path: Path, loc: dict, title: str, url: str, part: str,
    start: float, end: float, mode: str, paragraphs: list[str], lines: list[dict]
) -> None:
    body = [
        title,
        url,
        f"{t(loc, 'html.page')}: {part}" if part else "",
        f"{t(loc, 'html.range')}: {fmt_clock(start)} - {fmt_clock(end)}",
        f"{t(loc, 'html.mode-official') if mode == 'official' else t(loc, 'html.mode-asr')}",
        "",
        f"===== {t(loc, 'html.transcript')} =====",
        "",
        "\n\n".join(paragraphs),
        "",
        f"===== {t(loc, 'html.timed')} =====",
        "",
    ]
    for line in lines:
        body.append(f"[{fmt_clock(line['from'])}] {line['text']}")
    path.write_text("\n".join(body).strip() + "\n", encoding="utf-8")


def write_html(
    path: Path, loc: dict, title: str, url: str, part: str,
    start: float, end: float, mode: str, paragraphs: list[str], lines: list[dict]
) -> None:
    jump = f"{url}?t={int(start)}"
    mode_label = t(loc, "html.mode-official") if mode == "official" else t(loc, "html.mode-asr")
    char_count = sum(len(p) for p in paragraphs)
    meta = [
        f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">{t(loc, "html.source")}</a></li>',
    ]
    if part:
        meta.append(f'<li>{html.escape(t(loc, "html.page"))}: {html.escape(part)}</li>')
    meta.extend([
        f'<li>{t(loc, "html.range")}: {fmt_clock(start)} - {fmt_clock(end)}</li>',
        f'<li>{mode_label}</li>',
        f'<li>{t(loc, "html.characters")}: {char_count}</li>',
        f'<li>{t(loc, "html.lines")}: {len(lines)}</li>',
        f'<li>{t(loc, "html.generated")}: {time.strftime("%Y-%m-%d %H:%M")}</li>',
    ])
    paras = "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs) or (
        f'<p class="muted">{t(loc, "html.empty")}</p>'
    )
    timed = "\n".join(
        f'<li><a class="t" href="{html.escape(url)}?t={int(line["from"])}" '
        f'target="_blank" rel="noopener" title="{html.escape(t(loc, "html.jump"))}">'
        f'{fmt_clock(line["from"])}</a><span class="x">{html.escape(line["text"])}</span></li>'
        for line in lines
    )
    doc = f"""<!DOCTYPE html>
<html lang="{html.escape(t(loc, "html.lang"))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{html.escape(t(loc, "html.doc-title", title=title, start=fmt_clock(start), end=fmt_clock(end)))}</title>
<style>{STYLE_CSS}</style>
</head>
<body>
<div class="wrap">
<article class="card">
  <span class="brand">{html.escape(t(loc, "html.brand"))}</span>
  <h1>{html.escape(title)}</h1>
  <ul class="meta">{''.join(meta)}</ul>

  <div class="toolbar">
    <button id="copy" data-label="{html.escape(t(loc, "html.copy"))}"
      data-copied="{html.escape(t(loc, "html.copied"))}"
      onclick="copyText()">{html.escape(t(loc, "html.copy"))}</button>
  </div>

  <h2>{html.escape(t(loc, "html.transcript"))}</h2>
  <div class="transcript" id="fulltext">
{paras}
  </div>

  <h2>{html.escape(t(loc, "html.timed"))}</h2>
  <ol class="timed">
{timed}
  </ol>

  <footer>{html.escape(t(loc, "html.footer"))} · <a href="{html.escape(jump)}"
    target="_blank" rel="noopener">{fmt_clock(start)}</a></footer>
</article>
</div>
<script>{COPY_JS}</script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def main() -> int:
    global _LOC
    _LOC = load_locales(detect_ui_lang(sys.argv[1:]))

    parser = argparse.ArgumentParser(description=T("cli.description"))
    parser.add_argument("url", help=T("cli.url-help"))
    parser.add_argument("start", help=T("cli.start-help"))
    parser.add_argument("end", help=T("cli.end-help"))
    parser.add_argument("--page", type=int, default=1, help=T("cli.page-help"))
    parser.add_argument("--model", default="large-v3", help=T("cli.model-help"))
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help=T("cli.device-help"))
    parser.add_argument("--language", default="zh", help=T("cli.language-help"))
    parser.add_argument("--hotwords", default=None, help=T("cli.hotwords-help"))
    parser.add_argument("--prompt", default=None, help=T("cli.prompt-help"))
    parser.add_argument("--glossary", default=None, help=T("cli.glossary-help"))
    parser.add_argument("--out-dir", default=str(ROOT / "output"), help=T("cli.out-dir-help"))
    parser.add_argument("--sessdata", default=os.environ.get("BILI_SESSDATA"), help=T("cli.sessdata-help"))
    parser.add_argument("--no-asr", action="store_true", help=T("cli.no-asr-help"))
    parser.add_argument("--force-asr", action="store_true", help=T("cli.force-asr-help"))
    parser.add_argument("--keep-audio", action="store_true", help=T("cli.keep-audio-help"))
    parser.add_argument("--ui-lang", default="zh-CN", choices=["en", "zh-CN"], help=T("cli.ui-lang-help"))
    parser.add_argument("--name", default=None, help=T("cli.name-help"))
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    loc = _LOC
    log = lambda msg: print(msg, flush=True)  # noqa: E731

    try:
        start, end = parse_time(args.start), parse_time(args.end)
    except ValueError as exc:
        parser.error(str(exc))
    if end <= start:
        parser.error(T("error.end-before-start"))

    try:
        bvid = extract_bvid(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    url = f"https://www.bilibili.com/video/{bvid}"
    client = BiliClient(args.sessdata)
    try:
        info = client.view(bvid)
    except (BiliError, requests.RequestException) as exc:
        parser.error(str(exc))
    pages = info.get("pages") or []
    if args.page > len(pages) and pages:
        parser.error(T("error.page-range", page=args.page, count=len(pages)))
    page = pages[args.page - 1] if pages else {}
    cid = int(page.get("cid") or info.get("cid"))
    part = page.get("part") or ""
    title = info.get("title") or bvid

    log(t(loc, "log.video-title", title=title))
    log(t(loc, "log.duration", duration=fmt_clock(float(info.get("duration") or 0))))
    log(t(loc, "log.range", start=fmt_clock(start), end=fmt_clock(end)))

    lines: list[dict] = []
    mode = "asr"
    if not args.force_asr:
        try:
            tracks = client.subtitles(bvid, cid)
        except BiliError as exc:
            log(f"[warn] {exc}")
            tracks = []
        if tracks:
            log(t(loc, "log.official-subtitles", count=len(tracks)))
            track = pick_subtitle(tracks)
            if track and track.get("subtitle_url"):
                log(t(loc, "log.using-subtitle-track", lang=track.get("lan_doc") or track.get("lan")))
                body = client.fetch_subtitle_body(track["subtitle_url"], bvid)
                lines = clip_lines(body, start, end)
                mode = "official"
            else:
                log(t(loc, "log.subtitle-url-missing"))
        else:
            log(t(loc, "log.no-official-subtitles"))

    audio_path: Path | None = None
    workdir = Path(tempfile.mkdtemp(prefix="bili_asr_"))
    try:
        if not lines:
            if args.no_asr:
                log(t(loc, "log.empty-result"))
                return 2
            log(t(loc, "log.downloading-audio"))
            segments = client.audio_segments(bvid, cid)
            audio_path = extract_audio(segments, start, end, workdir / "segment.wav", workdir)
            log(t(loc, "log.audio-saved", path=str(audio_path)))
            log(t(loc, "log.transcribing"))
            asr_lines = transcribe(
                audio_path, args.model, args.device, args.language, loc, log,
                hotwords=args.hotwords, prompt=args.prompt,
            )
            lines = clip_lines(asr_lines, 0, end - start)
            for line in lines:
                line["from"] += start
                line["to"] += start
            glossary_path = Path(args.glossary) if args.glossary else ROOT / "glossary.json"
            if glossary_path.exists():
                apply_glossary(lines, load_glossary(glossary_path))
    finally:
        if args.keep_audio and audio_path and audio_path.exists():
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            keep = out_dir / f"{args.name or bvid}_{fmt_file_time(start)}-{fmt_file_time(end)}.wav"
            shutil.copy2(audio_path, keep)
            log(t(loc, "log.audio-saved", path=str(keep)))
        shutil.rmtree(workdir, ignore_errors=True)

    if not lines:
        log(t(loc, "log.empty-result"))
        return 2

    paragraphs = build_paragraphs(lines)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or f"{bvid}_{fmt_file_time(start)}-{fmt_file_time(end)}"
    txt_path = out_dir / f"{stem}.txt"
    html_path = out_dir / f"{stem}.html"

    write_txt(txt_path, loc, title, url, part, start, end, mode, paragraphs, lines)
    write_html(html_path, loc, title, url, part, start, end, mode, paragraphs, lines)
    log(t(loc, "log.done-txt", path=str(txt_path)))
    log(t(loc, "log.done-html", path=str(html_path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
