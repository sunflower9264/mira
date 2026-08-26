from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.config import get_settings


class WikiParseError(RuntimeError):
    pass


CONVERTIBLE_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".html", ".htm",
    ".pdf", ".docx", ".pptx", ".xls", ".xlsx", ".msg", ".eml",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def conversion_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in CONVERTIBLE_SUFFIXES:
        return "document"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "unsupported"


def is_allowed_wiki_source(path: str) -> bool:
    return conversion_kind(path) != "unsupported"


async def convert_to_markdown(source: Path) -> str:
    return await asyncio.to_thread(_convert_to_markdown_sync, source)


def _convert_to_markdown_sync(source: Path) -> str:
    try:
        import docker
        from docker.types import Mount
    except ImportError as exc:
        raise WikiParseError("缺少 docker Python SDK") from exc
    if not source.is_file():
        raise WikiParseError("原始文件不存在")
    with tempfile.TemporaryDirectory(prefix="mira-wiki-parse-") as temp_name:
        output_root = Path(temp_name)
        client = docker.from_env()
        mounts = [
            Mount(target="/input", source=str(source.resolve().parent), type="bind", read_only=True),
            Mount(target="/output", source=str(output_root.resolve()), type="bind", read_only=False),
        ]
        try:
            result = client.containers.run(
                get_settings().runtime_sandbox_image,
                command=["python", "/opt/mira/convert_to_markdown.py", f"/input/{source.name}", "/output/result.md"],
                mounts=mounts,
                user=_container_user(),
                network_disabled=True,
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=128m"},
                mem_limit="1g",
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                remove=True,
                stderr=True,
                stdout=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise WikiParseError(f"文档解析失败: {exc}") from exc
        target = output_root / "result.md"
        if not target.is_file():
            detail = result.decode("utf-8", errors="replace").strip() if isinstance(result, bytes) else str(result)
            raise WikiParseError(detail or "文档解析没有产生 Markdown")
        text = target.read_text(encoding="utf-8")
        if not text.strip() or "\ufffd" in text:
            raise WikiParseError("文档解析结果为空或包含损坏字符")
        return text


def _container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "mira"
    return f"{getuid()}:{getgid()}"
