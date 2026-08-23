from __future__ import annotations

import re

TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.(input|output)\s*\}\}")


def contains_template_token(text: str) -> bool:
    return bool(TOKEN_RE.search(text))


def strip_template_tokens(text: str) -> str:
    return TOKEN_RE.sub("上游上下文", text)
