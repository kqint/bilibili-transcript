#!/usr/bin/env python3
"""Version 2: Bilibili video to structured document.

Inspired by the bilibili-to-doc skill: prefer Bilibili AI subtitles obtained with
yt-dlp and browser cookies, fall back to the player API and finally to local ASR,
then organize the transcript into a Markdown document plus a readable HTML page.

    python workflow/bili_to_doc.py <url> [--start 20:40] [--end 21:36]
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
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency 'requests'. Run: pip install -r workflow/requirements.txt")


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bili_transcript as v1  # noqa: E402

DOC_CSS = """
:root{
  --bg:#f7f8fa; --card:#fff; --fg:#23272f; --muted:#6b7280; --accent:#00a1d6;
  --border:#e6e8eb; --code:#f2f4f7; --quote:#eff8fc;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#121418; --card:#191c21; --fg:#e6e8eb; --muted:#98a1ad; --accent:#2bb8e6;
         --border:#2a2f37; --code:#22272e; --quote:#1c2a32; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:16px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC","Microsoft YaHei",sans-serif}
.page{max-width:900px;margin:0 auto;padding:44px 22px 90px}
article{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:38px 42px;box-shadow:0 10px 36px rgba(0,0,0,.06)}
h1{font-size:30px;line-height:1.35;margin:0 0 18px;letter-spacing:.01em}
h2{font-size:21px;margin:40px 0 14px;padding-bottom:9px;border-bottom:1px solid var(--border)}
h3{font-size:17px;margin:26px 0 10px}
p{margin:0 0 1.05em;text-align:justify}
blockquote{margin:0 0 26px;padding:14px 18px;background:var(--quote);
  border-left:4px solid var(--accent);border-radius:0 10px 10px 0;color:var(--muted);font-size:14px}
blockquote p{margin:2px 0}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
hr{border:0;border-top:1px solid var(--border);margin:34px 0}
code{background:var(--code);border-radius:5px;padding:2px 6px;font-size:.9em;
  font-family:"JetBrains Mono",Consolas,"Courier New",monospace}
pre{background:var(--code);border:1px solid var(--border);border-radius:10px;
  padding:16px 18px;overflow:auto}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:18px 0;font-size:15px}
th,td{border:1px solid var(--border);padding:8px 12px;text-align:left}
th{background:var(--code)}
ul,ol{padding-left:1.35em}
li{margin:4px 0}
.toc{background:var(--code);border:1px solid var(--border);border-radius:10px;
  padding:14px 18px;margin:0 0 26px;font-size:14px}
.toc ul{margin:6px 0 0;padding-left:1.2em}
.toc .toctitle{font-weight:700;color:var(--muted);letter-spacing:.08em;font-size:12px}
footer{margin-top:30px;color:var(--muted);font-size:13px;text-align:center}
@media (max-width:640px){article{padding:24px 18px;border-radius:12px}h1{font-size:23px}}
"""


def T(key: str, **kw) -> str:
    return v1.T(key, **kw)


def log(message: str) -> None:
    print(message, flush=True)


def parse_clock(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return v1.parse_time(str(value))


def read_outline(path: Path, start: float) -> list[dict]:  # noqa: C901
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("sections", data) if isinstance(data, dict) else data
    outline = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            outline.append({"start": start, "title": item})
            continue
        begin = parse_clock(item.get("start", start))
        outline.append({"start": begin, "title": str(item.get("title") or "")})
    outline.sort(key=lambda entry: entry["start"])
    return outline


def clean_subtitle_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


TIMESTAMP_RE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)


def parse_timestamp(groups) -> float:
    hours = int(groups[0] or 0)
    return hours * 3600 + int(groups[1]) * 60 + int(groups[2]) + int(groups[3].ljust(3, "0")) / 1000


def parse_subtitle_file(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".json":
        return parse_bilibili_json(raw)
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n"))
    lines: list[dict] = []
    for block in blocks:
        block = block.strip()
        if not block or block.upper().startswith("WEBVTT"):
            continue
        rows = block.split("\n")
        for index, row in enumerate(rows):
            match = TIMESTAMP_RE.search(row)
            if not match:
                continue
            content = clean_subtitle_text(" ".join(rows[index + 1:]))
            if content:
                lines.append({
                    "from": parse_timestamp(match.groups()[:4]),
                    "to": parse_timestamp(match.groups()[4:]),
                    "text": content,
                })
            break
    return dedupe_subtitles(lines)


def parse_bilibili_json(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    body = data.get("body") if isinstance(data, dict) else None
    if not body:
        return []
    return [
        {"from": float(item.get("from", 0)), "to": float(item.get("to", 0)),
         "text": clean_subtitle_text(item.get("content", ""))}
        for item in body
        if clean_subtitle_text(item.get("content", ""))
    ]


def dedupe_subtitles(lines: list[dict]) -> list[dict]:
    result: list[dict] = []
    for line in lines:
        if result and line["text"] == result[-1]["text"]:
            result[-1]["to"] = line["to"]
            continue
        previous = result[-1]["text"] if result else ""
        if (
            previous
            and line["text"] in previous
            and len(line["text"]) >= len(previous) * 0.6
            and line["from"] <= result[-1]["to"] + 0.2
        ):
            result[-1]["to"] = line["to"]
            continue
        result.append(line)
    return result


def fetch_subtitles_ytdlp(
    url: str, workdir: Path, browser: str, cookies: str | None
) -> tuple[list[dict] | None, str]:
    try:
        import importlib.util

        if not importlib.util.find_spec("yt_dlp"):
            return None, T("doc.no-subtitle-tools")
    except ValueError:
        return None, T("doc.no-subtitle-tools")

    candidates = [browser]
    if browser == "auto":
        candidates = ["chrome", "edge", "firefox", "safari"]
    elif browser == "none" and not cookies:
        return None, T("doc.no-cookies")

    for candidate in candidates:
        for old in workdir.glob("*"):
            old.unlink(missing_ok=True)
        cmd = [
            sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings",
            "--no-playlist", "--skip-download", "--write-subs",
            "--sub-langs", "ai-zh,zh.*", "--sub-format", "srt/vtt",
            "--convert-subs", "srt",
            "-o", str(workdir / "%(id)s.%(ext)s"), url,
        ]
        if cookies:
            cmd += ["--cookies", cookies]
        elif candidate:
            cmd += ["--cookies-from-browser", candidate]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return None, "yt-dlp timeout"
        files = sorted(
            [*workdir.glob("*.srt"), *workdir.glob("*.vtt"), *workdir.glob("*.json")],
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if proc.returncode == 0 and files:
            for file in files:
                lines = parse_subtitle_file(file)
                if lines:
                    log(T("doc.subtitles-found", path=str(file)))
                    return lines, ""
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            reason = message[-1] if message else f"exit {proc.returncode}"
            if browser != "auto":
                return None, reason
        elif browser != "auto":
            return None, "no subtitle track"
    return None, "no subtitle track"


def read_cookie_value(path: Path, name: str) -> str | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for row in content.splitlines():
        if not row or row.startswith("#"):
            continue
        parts = row.split("\t")
        if len(parts) >= 7 and parts[5] == name:
            return parts[6]
    return None


def load_subtitles(
    client, bvid: str, cid: int, args, workdir: Path
) -> tuple[list[dict], str]:
    if args.force_asr:
        return [], "asr"

    log(T("doc.checking-api"))
    try:
        tracks = client.subtitles(bvid, cid)
    except (v1.BiliError, requests.RequestException):
        tracks = []
    track = v1.pick_subtitle(tracks)
    if track and track.get("subtitle_url"):
        try:
            body = client.fetch_subtitle_body(track["subtitle_url"], bvid)
        except (requests.RequestException, ValueError):
            body = []
        if body:
            log(T("log.using-subtitle-track", lang=track.get("lan_doc") or track.get("lan")))
            return body, "subtitle"

    if args.no_cookies:
        log(T("doc.no-cookies"))
        return [], "asr"

    log(T("doc.fetching-subtitles"))
    url = f"https://www.bilibili.com/video/{bvid}" + (f"?p={args.page}" if args.page > 1 else "")
    lines, reason = fetch_subtitles_ytdlp(url, workdir, args.cookies_from_browser, args.cookies)
    if lines:
        return lines, "subtitle"
    log(T("doc.subtitles-failed", reason=reason))
    return [], "asr"


def auto_chapters(lines: list[dict], start: float, end: float, gap: float = 9.0) -> list[dict]:
    chapters: list[dict] = []
    current: dict | None = None
    for line in lines:
        if current is None or line["from"] - current["lines"][-1]["to"] > gap:
            current = {"start": line["from"], "end": end, "title": "", "lines": []}
            chapters.append(current)
        current["lines"].append(line)
    for index, chapter in enumerate(chapters):
        chapter["title"] = T("doc.chapter", n=index + 1)
    return chapters


def outline_chapters(lines: list[dict], outline: list[dict], start: float, end: float) -> list[dict]:
    marks = [entry for entry in outline if entry["start"] <= end]
    if not marks:
        return auto_chapters(lines, start, end)
    chapters: list[dict] = []
    for index, mark in enumerate(marks):
        begin = mark["start"]
        stop = marks[index + 1]["start"] if index + 1 < len(marks) else end + 1e-6
        selected = [line for line in lines if begin - 0.01 <= line["from"] < stop]
        chapters.append({"start": begin, "end": stop, "title": mark["title"], "lines": selected})
    if not chapters:
        return auto_chapters(lines, start, end)
    return chapters


def build_markdown(
    title: str, bvid: str, url: str, author: str, part: str,
    start: float, end: float, mode: str, chapters: list[dict], lines: list[dict],
) -> str:
    sep = "：" if T("html.lang") == "zh-CN" else ": "
    meta = []
    meta.append(f"> {T('doc.source')}{sep}[Bilibili · {bvid}]({url})" +
                (f" | {T('doc.speaker')}{sep}{author}" if author else ""))
    second = f"> {T('doc.range')}{sep}{v1.fmt_clock(start)} - {v1.fmt_clock(end)}"
    if part:
        second += f" | {T('doc.part')}{sep}{part}"
    meta.append(second)
    mode_label = T("doc.mode-subtitle") if mode == "subtitle" else T("doc.mode-asr")
    meta.append(f"> {T('doc.mode')}{sep}{mode_label} | {T('doc.generated')}{sep}{time.strftime('%Y-%m-%d %H:%M')}")

    body = [f"# {title}", "", *meta, "", "<!--TOC-->", "", "---", ""]
    for index, chapter in enumerate(chapters):
        heading = chapter["title"] or T("doc.chapter", n=index + 1)
        body.append(f"## {heading}")
        body.append("")
        paragraphs = v1.build_paragraphs(chapter["lines"]) if chapter["lines"] else []
        if not paragraphs:
            body.append(f"*{T('doc.no-content')}*")
        else:
            if chapter is chapters[-1] and not re.search(r"[。！？!?；;…]$", paragraphs[-1]):
                paragraphs[-1] += T("doc.incomplete")
            body.extend(paragraphs)
        body.append("")

    body.extend(["---", "", f"## {T('doc.timeline')}", ""])
    for line in lines:
        body.append(f"- `{v1.fmt_clock(line['from'])}` {line['text']}")

    body.extend([
        "", "---", "", f"*{T('doc.disclaimer')}*", "",
        f"*{url}?t={int(start)}*", "",
    ])
    return "\n".join(body)


def render_html(markdown_text: str, title: str) -> str:
    import markdown as markdown_lib

    text = markdown_text.replace("<!--TOC-->", f"[TOC]")
    content = markdown_lib.markdown(
        text,
        extensions=["fenced_code", "tables", "toc", "sane_lists", "nl2br"],
        extension_configs={"toc": {"toc_depth": "2-3", "title": T("doc.toc")}},
    )
    return f"""<!DOCTYPE html>
<html lang="{html.escape(T('html.lang'))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{html.escape(title)}</title>
<style>{DOC_CSS}</style>
</head>
<body>
<div class="page">
<article>
{content}
<footer>{html.escape(T('html.footer'))}</footer>
</article>
</div>
</body>
</html>
"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args_peek = sys.argv[1:]
    loc = v1.load_locales(v1.detect_ui_lang(args_peek))
    v1._LOC = loc

    parser = argparse.ArgumentParser(description=T("cli.doc-description"))
    parser.add_argument("url", help=T("cli.url-help"))
    parser.add_argument("--start", default=None, help=T("cli.start-help"))
    parser.add_argument("--end", default=None, help=T("cli.end-help"))
    parser.add_argument("--page", type=int, default=1, help=T("cli.page-help"))
    parser.add_argument("--outline", default=None, help=T("cli.outline-help"))
    parser.add_argument("--cookies-from-browser", default="auto",
                        help=T("cli.cookies-from-browser-help"))
    parser.add_argument("--cookies", default=None, help=T("cli.cookies-help"))
    parser.add_argument("--no-cookies", action="store_true", help=T("cli.no-cookies-help"))
    parser.add_argument("--sessdata", default=os.environ.get("BILI_SESSDATA"),
                        help=T("cli.sessdata-help"))
    parser.add_argument("--model", default="large-v3", help=T("cli.model-help"))
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                        help=T("cli.device-help"))
    parser.add_argument("--language", default="zh", help=T("cli.language-help"))
    parser.add_argument("--prompt", default=None, help=T("cli.prompt-help"))
    parser.add_argument("--hotwords", default=None, help=T("cli.hotwords-help"))
    parser.add_argument("--glossary", default=None, help=T("cli.glossary-help"))
    parser.add_argument("--no-asr", action="store_true", help=T("cli.no-asr-help"))
    parser.add_argument("--force-asr", action="store_true", help=T("cli.force-asr-help"))
    parser.add_argument("--out-dir", default=str(ROOT / "output"), help=T("cli.out-dir-help"))
    parser.add_argument("--name", default=None, help=T("cli.name-help"))
    parser.add_argument("--keep-audio", action="store_true", help=T("cli.keep-audio-help"))
    parser.add_argument("--ui-lang", default="zh-CN", choices=["en", "zh-CN"],
                        help=T("cli.ui-lang-help"))
    args = parser.parse_args()

    try:
        bvid = v1.extract_bvid(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    url = f"https://www.bilibili.com/video/{bvid}"
    sessdata = args.sessdata
    if not sessdata and args.cookies:
        sessdata = read_cookie_value(Path(args.cookies), "SESSDATA")
    client = v1.BiliClient(sessdata)
    try:
        info = client.view(bvid)
    except (v1.BiliError, requests.RequestException) as exc:
        parser.error(str(exc))

    pages = info.get("pages") or []
    if pages and args.page > len(pages):
        parser.error(T("error.page-range", page=args.page, count=len(pages)))
    page = pages[args.page - 1] if pages else {}
    cid = int(page.get("cid") or info.get("cid"))
    part = page.get("part") or ""
    title = info.get("title") or bvid
    author = (info.get("owner") or {}).get("name") or ""
    duration = float(info.get("duration") or 0)

    start = parse_clock(args.start) if args.start else 0.0
    end = parse_clock(args.end) if args.end else duration
    if end <= start:
        parser.error(T("error.end-before-start"))

    log(T("log.video-title", title=title))
    log(T("log.duration", duration=v1.fmt_clock(duration)))
    log(T("log.range", start=v1.fmt_clock(start), end=v1.fmt_clock(end)))

    workdir = Path(tempfile.mkdtemp(prefix="bili_doc_"))
    audio_path: Path | None = None
    try:
        lines, mode = load_subtitles(client, bvid, cid, args, workdir)
        if lines:
            clipped = v1.clip_lines(lines, start, end)
            if clipped:
                lines = clipped
            else:
                log(T("doc.subtitles-out-of-range"))
                lines = []

        if not lines:
            if args.no_asr:
                log(T("log.empty-result"))
                return 2
            log(T("doc.using-asr"))
            log(T("log.downloading-audio"))
            segments = client.audio_segments(bvid, cid)
            audio_path = v1.extract_audio(segments, start, end, workdir / "segment.wav", workdir)
            log(T("log.transcribing"))
            asr_lines = v1.transcribe(
                audio_path, args.model, args.device, args.language, loc, log,
                hotwords=args.hotwords, prompt=args.prompt,
            )
            lines = v1.clip_lines(asr_lines, 0, end - start)
            for line in lines:
                line["from"] += start
                line["to"] += start
            mode = "asr"

        if not lines:
            log(T("log.empty-result"))
            return 2

        glossary_path = Path(args.glossary) if args.glossary else ROOT / "glossary.json"
        if glossary_path.exists():
            v1.apply_glossary(lines, v1.load_glossary(glossary_path))

        if args.outline:
            outline = read_outline(Path(args.outline), start)
            log(T("doc.outline-loaded", count=len(outline)))
            chapters = outline_chapters(lines, outline, start, end)
        else:
            log(T("doc.auto-chapters"))
            chapters = auto_chapters(lines, start, end)

        markdown_text = build_markdown(
            title, bvid, url, author, part, start, end, mode, chapters, lines
        )

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if args.name:
            stem = args.name
        elif args.start or args.end:
            stem = f"{bvid}_{v1.fmt_file_time(start)}-{v1.fmt_file_time(end)}"
        else:
            stem = f"{bvid}_full"
        md_path = out_dir / f"{stem}.doc.md"
        html_path = out_dir / f"{stem}.doc.html"
        md_path.write_text(markdown_text, encoding="utf-8")
        html_path.write_text(
            render_html(markdown_text, T("doc.doc-title", title=title,
                                         start=v1.fmt_clock(start), end=v1.fmt_clock(end))),
            encoding="utf-8",
        )
        log(T("doc.done-md", path=str(md_path)))
        log(T("doc.done-html", path=str(html_path)))
        return 0
    finally:
        if args.keep_audio and audio_path and audio_path.exists():
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            keep = out_dir / f"{args.name or bvid}_{v1.fmt_file_time(start)}-{v1.fmt_file_time(end)}.wav"
            shutil.copy2(audio_path, keep)
            log(T("log.audio-saved", path=str(keep)))
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
