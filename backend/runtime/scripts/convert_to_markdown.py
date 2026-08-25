#!/usr/bin/env python3
"""Convert one untrusted document to Markdown inside the Mira runtime image."""

from __future__ import annotations

import sys
from pathlib import Path

from markitdown import MarkItDown, StreamInfo


TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".html", ".htm"}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: convert_to_markdown.py INPUT OUTPUT", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    if not source.is_file():
        print("input is not a file", file=sys.stderr)
        return 2
    stream_info = StreamInfo(charset="utf-8") if source.suffix.lower() in TEXT_SUFFIXES else None
    result = MarkItDown(enable_plugins=False).convert(str(source), stream_info=stream_info)
    text = result.text_content
    if not isinstance(text, str) or not text.strip():
        print("converter returned empty content", file=sys.stderr)
        return 1
    if "\ufffd" in text:
        print("converter returned U+FFFD", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
