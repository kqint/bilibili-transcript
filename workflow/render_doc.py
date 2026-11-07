#!/usr/bin/env python3
"""Render a Markdown document produced by bili_to_doc.py into the styled HTML page."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bili_to_doc as b  # noqa: E402
import bili_transcript as v1  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a transcript Markdown file to HTML.")
    parser.add_argument("markdown", help="Path to the .doc.md file")
    parser.add_argument("--out", default=None, help="Output .html path")
    parser.add_argument("--title", default=None, help="HTML document title")
    parser.add_argument("--ui-lang", default="zh-CN", choices=["en", "zh-CN"])
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    loc = v1.load_locales(args.ui_lang)
    v1._LOC = loc

    src = Path(args.markdown)
    if not src.exists():
        parser.error(f"File not found: {src}")
    dest = Path(args.out) if args.out else src.with_suffix(".html")
    title = args.title or src.stem

    dest.write_text(b.render_html(src.read_text(encoding="utf-8"), title), encoding="utf-8")
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
