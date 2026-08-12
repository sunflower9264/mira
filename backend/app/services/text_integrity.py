from __future__ import annotations

import io
import lzma
import re
import stat
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


MAX_TEXT_SCAN_ITEMS = 10_000
MAX_TEXT_SCAN_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 1024 * 1024 * 1024

UNICODE_REPLACEMENT_ERROR = (
    "输出包含 Unicode replacement character U+FFFD（�），说明文本可能已损坏；"
    "请重新生成受影响内容，不能仅删除该字符。"
)

_OOXML_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
_TEXT_EXTENSIONS = {
    ".astro",
    ".bash",
    ".bat",
    ".c",
    ".cc",
    ".cfg",
    ".cjs",
    ".cmd",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".cxx",
    ".env",
    ".fish",
    ".gql",
    ".go",
    ".gradle",
    ".graphql",
    ".h",
    ".hh",
    ".hpp",
    ".htm",
    ".html",
    ".hxx",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".kt",
    ".kts",
    ".lock",
    ".log",
    ".markdown",
    ".md",
    ".mjs",
    ".npmrc",
    ".php",
    ".properties",
    ".proto",
    ".ps1",
    ".py",
    ".pyi",
    ".rb",
    ".rels",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".svg",
    ".swift",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
_TEXT_FILENAMES = {
    ".dockerignore",
    ".editorconfig",
    ".env",
    ".gitignore",
    ".npmrc",
    ".nvmrc",
    ".python-version",
    ".tool-versions",
    "changelog",
    "dockerfile",
    "gemfile",
    "license",
    "makefile",
    "notice",
    "procfile",
    "rakefile",
    "readme",
}
_ZIP_READ_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    NotImplementedError,
    EOFError,
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
)


@dataclass
class _ScanBudget:
    items: int = 0
    text_bytes: int = 0
    archive_bytes: int = 0

    def add_item(self, label: str) -> str | None:
        self.items += 1
        if self.items > MAX_TEXT_SCAN_ITEMS:
            return f"{label}：扫描项数超过上限 {MAX_TEXT_SCAN_ITEMS}"
        return None

    def reserve_text(self, size: int, label: str) -> str | None:
        if size < 0 or self.text_bytes + size > MAX_TEXT_SCAN_BYTES:
            return f"{label}：文本/XML 扫描量超过上限 {MAX_TEXT_SCAN_BYTES} 字节"
        self.text_bytes += size
        return None

    def reserve_archive(self, size: int, label: str) -> str | None:
        if size < 0 or self.archive_bytes + size > MAX_ARCHIVE_EXPANDED_BYTES:
            return f"{label}：归档展开量超过上限 {MAX_ARCHIVE_EXPANDED_BYTES} 字节"
        self.archive_bytes += size
        return None


def contains_unicode_replacement(value: Any) -> bool:
    if isinstance(value, str):
        return "\ufffd" in value
    if isinstance(value, dict):
        return any(contains_unicode_replacement(key) or contains_unicode_replacement(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_unicode_replacement(item) for item in value)
    return False


def validate_artifact_text_integrity(path: Path) -> str | None:
    budget = _ScanBudget()
    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    is_ooxml = suffix in _OOXML_EXTENSIONS
    is_zip = suffix == ".zip"
    is_tar = lower_name.endswith((".tar", ".gz", ".tgz"))

    if is_ooxml or is_zip or is_tar:
        try:
            archive_size = path.stat().st_size
        except OSError as exc:
            return f"{path.name} 无法读取：{exc}"
        if archive_size > MAX_ARCHIVE_FILE_BYTES:
            return f"{path.name}：压缩文件大小超过上限 {MAX_ARCHIVE_FILE_BYTES} 字节"

    if is_ooxml:
        if not zipfile.is_zipfile(path):
            return f"{path.name} 不是有效的 OOXML ZIP 文件"
        return _scan_ooxml(path, budget, label=path.name)
    if is_zip:
        if not zipfile.is_zipfile(path):
            return f"{path.name} 不是有效的 ZIP 文件"
        return _scan_zip_archive(path, budget, label=path.name)
    if is_tar:
        if not tarfile.is_tarfile(path):
            return f"{path.name} 不是有效的 TAR 归档"
        return _scan_tar_archive(path, budget, label=path.name)
    if _is_text_member(path.name):
        item_error = budget.add_item(path.name)
        if item_error:
            return item_error
        try:
            size = path.stat().st_size
        except OSError as exc:
            return f"{path.name} 无法读取：{exc}"
        budget_error = budget.reserve_text(size, path.name)
        if budget_error:
            return budget_error
        try:
            return _validate_utf8_text(path.read_bytes(), path.name)
        except OSError as exc:
            return f"{path.name} 无法读取：{exc}"
    return None


def _scan_zip_archive(path: Path, budget: _ScanBudget, *, label: str) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            normalized_names: set[str] = set()
            for info in archive.infolist():
                member_label = f"{label} 成员 {info.filename}"
                item_error = budget.add_item(member_label)
                if item_error:
                    return item_error
                name_error = _validate_member_name(info.filename, member_label)
                if name_error:
                    return name_error
                normalized_name = _normalized_member_name(info.filename)
                if normalized_name in normalized_names:
                    return f"{member_label} 规范化后与其他成员路径重复：{normalized_name}"
                normalized_names.add(normalized_name)
                type_error = _validate_zip_member_type(info, member_label)
                if type_error:
                    return type_error
                if info.is_dir():
                    continue
                archive_error = budget.reserve_archive(info.file_size, member_label)
                if archive_error:
                    return archive_error
                if _is_ooxml_member(info.filename):
                    nested = archive.read(info)
                    nested_error = _scan_ooxml(io.BytesIO(nested), budget, label=member_label)
                    if nested_error:
                        return nested_error
                elif _is_text_member(info.filename):
                    text_error = _read_zip_text_member(archive, info, budget, member_label)
                    if text_error:
                        return text_error
            bad_member = archive.testzip()
            if bad_member:
                return f"{label} ZIP 成员 {bad_member} CRC 校验失败"
    except _ZIP_READ_ERRORS as exc:
        return f"{label} ZIP 完整性校验失败：{exc}"
    return None


def _scan_tar_archive(path: Path, budget: _ScanBudget, *, label: str) -> str | None:
    try:
        with tarfile.open(path, mode="r:*") as archive:
            for info in archive:
                member_label = f"{label} 成员 {info.name}"
                item_error = budget.add_item(member_label)
                if item_error:
                    return item_error
                name_error = _validate_member_name(info.name, member_label)
                if name_error:
                    return name_error
                if not (info.isfile() or info.isdir()):
                    return f"{member_label} 的成员类型不安全"
                if info.isdir():
                    continue
                archive_error = budget.reserve_archive(info.size, member_label)
                if archive_error:
                    return archive_error
                if _is_ooxml_member(info.name):
                    member = archive.extractfile(info)
                    if member is None:
                        return f"{member_label} 无法读取"
                    nested_error = _scan_ooxml(io.BytesIO(member.read()), budget, label=member_label)
                    if nested_error:
                        return nested_error
                elif _is_text_member(info.name):
                    budget_error = budget.reserve_text(info.size, member_label)
                    if budget_error:
                        return budget_error
                    member = archive.extractfile(info)
                    if member is None:
                        return f"{member_label} 无法读取"
                    text_error = _validate_utf8_text(member.read(), member_label)
                    if text_error:
                        return text_error
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        return f"{label} 无法作为 TAR 读取：{exc}"
    return None


def _scan_ooxml(source: Any, budget: _ScanBudget, *, label: str) -> str | None:
    try:
        with zipfile.ZipFile(source) as document:
            normalized_names: set[str] = set()
            for info in document.infolist():
                member_label = f"{label} XML 成员 {info.filename}"
                item_error = budget.add_item(member_label)
                if item_error:
                    return item_error
                name_error = _validate_member_name(info.filename, member_label)
                if name_error:
                    return name_error
                normalized_name = _normalized_member_name(info.filename)
                if normalized_name in normalized_names:
                    return f"{member_label} 规范化后与其他成员路径重复：{normalized_name}"
                normalized_names.add(normalized_name)
                type_error = _validate_zip_member_type(info, member_label)
                if type_error:
                    return type_error
                if info.is_dir():
                    continue
                archive_error = budget.reserve_archive(info.file_size, member_label)
                if archive_error:
                    return archive_error
                if not _is_xml_member(info.filename):
                    continue
                text_error = _read_zip_text_member(document, info, budget, member_label)
                if text_error:
                    return text_error
            bad_member = document.testzip()
            if bad_member:
                return f"{label} OOXML ZIP 成员 {bad_member} CRC 校验失败"
    except _ZIP_READ_ERRORS as exc:
        return f"{label} OOXML ZIP 完整性校验失败：{exc}"
    return None


def _read_zip_text_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    budget: _ScanBudget,
    label: str,
) -> str | None:
    budget_error = budget.reserve_text(info.file_size, label)
    if budget_error:
        return budget_error
    return _validate_utf8_text(archive.read(info), label)


def _validate_utf8_text(content: bytes, label: str) -> str | None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"{label} 不是严格 UTF-8 文本（字节偏移 {exc.start}）"
    if contains_unicode_replacement(text):
        return f"{label}：{UNICODE_REPLACEMENT_ERROR}"
    return None


def _validate_member_name(name: str, label: str) -> str | None:
    if contains_unicode_replacement(name):
        return f"{label} 的文件名包含 Unicode replacement character U+FFFD（�）"
    normalized = name.replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"^[a-zA-Z]:", normalized)
        or ".." in PurePosixPath(normalized).parts
    ):
        return f"{label} 的成员路径不安全"
    return None


def _normalized_member_name(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).as_posix()


def _validate_zip_member_type(info: zipfile.ZipInfo, label: str) -> str | None:
    if info.is_dir():
        return None
    file_type = stat.S_IFMT(info.external_attr >> 16)
    if file_type not in (0, stat.S_IFREG):
        return f"{label} 的成员类型不安全"
    return None


def _is_text_member(name: str) -> bool:
    member = PurePosixPath(name.replace("\\", "/"))
    return member.suffix.lower() in _TEXT_EXTENSIONS or member.name.lower() in _TEXT_FILENAMES


def _is_xml_member(name: str) -> bool:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower() in {".xml", ".rels"}


def _is_ooxml_member(name: str) -> bool:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower() in _OOXML_EXTENSIONS
