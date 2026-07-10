from __future__ import annotations

import re
from typing import Any

TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.(input|output)\s*\}\}")


class TemplateError(Exception):
    pass


def contains_template_token(text: str) -> bool:
    return bool(TOKEN_RE.search(text))


def strip_template_tokens(text: str) -> str:
    return TOKEN_RE.sub("上游上下文", text)


def render_template(template: str, values: dict[str, dict[str, Any]]) -> str:
    def replace(match: re.Match[str]) -> str:
        node_id, field = match.group(1), match.group(2)
        if node_id not in values:
            raise TemplateError(f"未执行的节点 {node_id}")
        value = values[node_id].get(field)
        if value is None:
            return ""
        return str(value)

    return TOKEN_RE.sub(replace, template)

