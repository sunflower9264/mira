from __future__ import annotations

import gzip
import io
import json
import lzma
import stat
import tarfile
import zipfile
import zlib

import pytest

from app.services import output_contracts, text_integrity
from app.services.output_contracts import validate_contract_output


def _json_node() -> dict:
    return {
        "id": "n_json",
        "type": "generate",
        "title": "JSON",
        "prompt": "输出 JSON",
        "output_contract": {
            "type": "json",
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }


def _artifact_node(artifact_kind: str) -> dict:
    return {
        "id": f"n_{artifact_kind}",
        "type": "generate",
        "title": artifact_kind,
        "prompt": "输出文件",
        "output_contract": {"type": "artifact", "artifact_kind": artifact_kind, "max_count": 1},
    }


def _validate_artifact(path, artifact_kind: str = "archive"):
    output = json.dumps({"artifacts": [{"path": str(path), "name": path.name}]}, ensure_ascii=False)
    return validate_contract_output(_artifact_node(artifact_kind), output, workspace=path.parent)


def test_artifact_contract_runs_office_validation_when_enabled(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "documents.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("document.docx", _ooxml_bytes("word/document.xml", b"<root>valid</root>"))
    node = _artifact_node("zip")
    node["output_contract"]["validate_office_documents"] = True
    monkeypatch.setattr(
        output_contracts,
        "validate_office_documents",
        lambda _path, **_kwargs: "Office 文档无法打开",
    )

    output = json.dumps({"artifacts": [{"path": str(archive), "name": archive.name}]}, ensure_ascii=False)
    result = validate_contract_output(node, output, workspace=tmp_path)

    assert result.ok is False
    assert "Office 文档无法打开" in str(result.error)


def _write_tar_member(path, name: str, content: bytes) -> None:
    with tarfile.open(path, "w") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))


def _write_unsafe_archive(path, case: str) -> None:
    if case.startswith("zip_"):
        with zipfile.ZipFile(path, "w") as zf:
            names = {
                "zip_traversal": "../escape.txt",
                "zip_absolute": "/escape.txt",
                "zip_windows_drive": "C:/escape.txt",
            }
            if case in names:
                zf.writestr(names[case], "escape")
                return
            info = zipfile.ZipInfo("unsafe-entry")
            info.create_system = 3
            file_type = stat.S_IFLNK if case == "zip_symlink" else stat.S_IFIFO
            info.external_attr = (file_type | 0o644) << 16
            zf.writestr(info, "../escape" if case == "zip_symlink" else "")
        return

    with tarfile.open(path, "w") as tf:
        names = {
            "tar_traversal": "../escape.txt",
            "tar_absolute": "/escape.txt",
            "tar_windows_drive": "C:/escape.txt",
        }
        if case in names:
            data = b"escape"
            info = tarfile.TarInfo(names[case])
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            return
        if case == "tar_hardlink":
            target_data = b"target"
            target = tarfile.TarInfo("target.txt")
            target.size = len(target_data)
            tf.addfile(target, io.BytesIO(target_data))
            info = tarfile.TarInfo("unsafe-entry")
            info.type = tarfile.LNKTYPE
            info.linkname = "target.txt"
        elif case == "tar_symlink":
            info = tarfile.TarInfo("unsafe-entry")
            info.type = tarfile.SYMTYPE
            info.linkname = "../escape"
        else:
            info = tarfile.TarInfo("unsafe-entry")
            info.type = tarfile.FIFOTYPE
        tf.addfile(info)


def _ooxml_bytes(member_name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(member_name, content)
    return buffer.getvalue()


def test_json_contract_rejects_unicode_replacement_character() -> None:
    result = validate_contract_output(_json_node(), '{"value":"损坏�文本"}')

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


def test_json_contract_rejects_nested_unicode_replacement_character() -> None:
    node = _json_node()
    node["output_contract"]["json_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "array", "items": {"type": "string"}}},
        "required": ["value"],
    }

    result = validate_contract_output(node, '{"value":["完整","损坏�文本"]}')

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


def test_free_text_contract_rejects_unicode_replacement_character() -> None:
    node = {"id": "n_text", "type": "generate", "title": "Text", "prompt": "输出文本"}

    result = validate_contract_output(node, "损坏�文本")

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


def test_output_html_rejects_unicode_replacement_character() -> None:
    node = {"id": "n_output", "type": "output", "title": "Output", "prompt": "输出 HTML"}

    result = validate_contract_output(node, json.dumps({"html": "<p>损坏�文本</p>"}, ensure_ascii=False))

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_archive_contract_rejects_replacement_character_in_text_member(tmp_path, suffix: str) -> None:
    archive = tmp_path / f"bundle{suffix}"
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("README.md", "损坏�文本")
    else:
        _write_tar_member(archive, "README.md", "损坏�文本".encode())

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_archive_contract_requires_strict_utf8_for_text_members(tmp_path, suffix: str) -> None:
    archive = tmp_path / f"bundle{suffix}"
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("README.md", b"invalid: \xff")
    else:
        _write_tar_member(archive, "README.md", b"invalid: \xff")

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "UTF-8" in str(result.error)


@pytest.mark.parametrize(
    ("artifact_kind", "suffix", "member_name"),
    [
        ("docx", ".docx", "word/document.xml"),
        ("ppt", ".pptx", "ppt/slides/slide1.xml"),
        ("excel", ".xlsx", "xl/sharedStrings.xml"),
    ],
)
def test_ooxml_contract_rejects_replacement_character_in_xml_member(
    tmp_path,
    artifact_kind: str,
    suffix: str,
    member_name: str,
) -> None:
    document = tmp_path / f"document{suffix}"
    document.write_bytes(_ooxml_bytes(member_name, "<root>损坏�文本</root>".encode()))

    result = _validate_artifact(document, artifact_kind)

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


def test_ooxml_contract_requires_strict_utf8_for_xml_member(tmp_path) -> None:
    document = tmp_path / "document.docx"
    document.write_bytes(_ooxml_bytes("word/document.xml", b"<root>invalid: \xff</root>"))

    result = _validate_artifact(document, "docx")

    assert result.ok is False
    assert "UTF-8" in str(result.error)


@pytest.mark.parametrize(
    ("artifact_kind", "suffix", "members"),
    [
        ("zip", ".zip", (("docs/readme.txt", b"first"), ("docs\\readme.txt", b"second"))),
        ("docx", ".docx", (("word/document.xml", b"<first/>"), ("word/./document.xml", b"<second/>"))),
    ],
)
def test_zip_containers_reject_normalized_duplicate_members(
    tmp_path,
    artifact_kind: str,
    suffix: str,
    members: tuple[tuple[str, bytes], ...],
) -> None:
    document = tmp_path / f"duplicate{suffix}"
    with zipfile.ZipFile(document, "w") as zf:
        for name, content in members:
            zf.writestr(name, content)

    result = _validate_artifact(document, artifact_kind)

    assert result.ok is False
    assert "重复" in str(result.error)


@pytest.mark.parametrize(
    "error_type",
    [zlib.error, lzma.LZMAError, EOFError],
    ids=["zlib", "lzma", "eof"],
)
def test_zip_member_decompression_errors_become_validation_failures(tmp_path, monkeypatch, error_type) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", b"valid")

    def fail_read(*_args, **_kwargs):
        raise error_type("corrupt member stream")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_read)

    result = _validate_artifact(archive, "zip")

    assert result.ok is False
    assert "ZIP" in str(result.error)


@pytest.mark.parametrize(
    "error_type",
    [zlib.error, lzma.LZMAError, EOFError],
    ids=["zlib", "lzma", "eof"],
)
def test_zip_archive_crc_check_errors_become_validation_failures(tmp_path, monkeypatch, error_type) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("payload.bin", b"valid")

    def fail_testzip(*_args, **_kwargs):
        raise error_type("corrupt archive stream")

    monkeypatch.setattr(zipfile.ZipFile, "testzip", fail_testzip)

    result = _validate_artifact(archive, "zip")

    assert result.ok is False
    assert "ZIP" in str(result.error)


@pytest.mark.parametrize(
    ("artifact_kind", "suffix", "member_name"),
    [
        ("zip", ".zip", "README.md"),
        ("docx", ".docx", "word/document.xml"),
    ],
)
def test_zip_scanned_member_crc_corruption_becomes_validation_failure(
    tmp_path,
    artifact_kind: str,
    suffix: str,
    member_name: str,
) -> None:
    document = tmp_path / f"corrupt{suffix}"
    payload = b"unique scanned member payload"
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(member_name, payload)
    content = bytearray(document.read_bytes())
    content[content.index(payload)] ^= 0x01
    document.write_bytes(content)

    result = _validate_artifact(document, artifact_kind)

    assert result.ok is False
    assert "CRC" in str(result.error)


@pytest.mark.parametrize(
    ("artifact_kind", "suffix"),
    [
        ("zip", ".zip"),
        ("docx", ".docx"),
        ("ppt", ".pptx"),
        ("excel", ".xlsx"),
    ],
)
def test_zip_container_extensions_reject_non_zip_content(
    tmp_path,
    artifact_kind: str,
    suffix: str,
) -> None:
    artifact = tmp_path / f"invalid{suffix}"
    artifact.write_bytes(b"not a zip")

    result = _validate_artifact(artifact, artifact_kind)

    assert result.ok is False
    assert "ZIP" in str(result.error)


@pytest.mark.parametrize(
    ("artifact_kind", "suffix"),
    [
        ("docx", ".docx"),
        ("ppt", ".pptx"),
        ("excel", ".xlsx"),
    ],
)
def test_ooxml_contract_rejects_crc_corruption(tmp_path, artifact_kind: str, suffix: str) -> None:
    document = tmp_path / f"document{suffix}"
    payload = b"valid binary payload"
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("media/payload.bin", payload)
    content = bytearray(document.read_bytes())
    offset = content.index(payload)
    content[offset] ^= 0x01
    document.write_bytes(content)
    assert zipfile.is_zipfile(document)

    result = _validate_artifact(document, artifact_kind)

    assert result.ok is False
    assert "CRC" in str(result.error)


@pytest.mark.parametrize("archive_suffix", [".zip", ".tar"])
@pytest.mark.parametrize(
    ("document_suffix", "member_name"),
    [
        (".docx", "word/document.xml"),
        (".pptx", "ppt/slides/slide1.xml"),
        (".xlsx", "xl/sharedStrings.xml"),
    ],
)
def test_archive_contract_scans_nested_ooxml_xml_members(
    tmp_path,
    archive_suffix: str,
    document_suffix: str,
    member_name: str,
) -> None:
    archive = tmp_path / f"bundle{archive_suffix}"
    document = _ooxml_bytes(member_name, "<root>损坏�文本</root>".encode())
    document_name = f"document{document_suffix}"
    if archive_suffix == ".zip":
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(document_name, document)
    else:
        _write_tar_member(archive, document_name, document)

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "U+FFFD" in str(result.error)


def test_archive_contract_ignores_non_text_member_bytes(tmp_path) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("images/screenshot.png", b"\x89PNG\r\n\x1a\n\xff")

    result = _validate_artifact(archive)

    assert result.ok is True


def test_zip_artifact_kind_rejects_non_zip_extension(tmp_path) -> None:
    archive = tmp_path / "bundle.tar"
    _write_tar_member(archive, "README.md", b"valid")

    result = _validate_artifact(archive, "zip")

    assert result.ok is False
    assert "扩展名 .tar 不符合 zip" in str(result.error)


def test_zip_artifact_kind_rejects_crc_corruption(tmp_path) -> None:
    archive = tmp_path / "bundle.zip"
    payload = b"valid binary payload"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("payload.bin", payload)
    content = bytearray(archive.read_bytes())
    offset = content.index(payload)
    content[offset] ^= 0x01
    archive.write_bytes(content)
    assert zipfile.is_zipfile(archive)

    result = _validate_artifact(archive, "zip")

    assert result.ok is False
    assert "CRC" in str(result.error)


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".tar", b"not a tar"),
        (".gz", gzip.compress(b"plain gzip payload")),
        (".tgz", b"not a compressed tar"),
    ],
)
def test_tar_archive_extensions_reject_non_tar_content(tmp_path, suffix: str, content: bytes) -> None:
    archive = tmp_path / f"invalid{suffix}"
    archive.write_bytes(content)

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "TAR" in str(result.error)


def test_gz_extension_scans_gzip_compressed_tar_members(tmp_path) -> None:
    archive = tmp_path / "bundle.gz"
    with tarfile.open(archive, "w:gz") as tf:
        content = b"invalid: \xff"
        info = tarfile.TarInfo("README.md")
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "UTF-8" in str(result.error)


@pytest.mark.parametrize(
    ("case", "suffix"),
    [
        ("zip_traversal", ".zip"),
        ("zip_absolute", ".zip"),
        ("zip_windows_drive", ".zip"),
        ("zip_symlink", ".zip"),
        ("zip_special", ".zip"),
        ("tar_traversal", ".tar"),
        ("tar_absolute", ".tar"),
        ("tar_windows_drive", ".tar"),
        ("tar_symlink", ".tar"),
        ("tar_hardlink", ".tar"),
        ("tar_special", ".tar"),
    ],
)
def test_archive_contract_rejects_unsafe_members(tmp_path, case: str, suffix: str) -> None:
    archive = tmp_path / f"unsafe{suffix}"
    _write_unsafe_archive(archive, case)

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "不安全" in str(result.error)


def test_ooxml_contract_accepts_normal_document_members(tmp_path) -> None:
    document = tmp_path / "document.docx"
    document.write_bytes(_ooxml_bytes("word/document.xml", b"<root>valid</root>"))

    result = _validate_artifact(document, "docx")

    assert result.ok is True


def test_text_integrity_scan_limits_are_explicit() -> None:
    assert text_integrity.MAX_TEXT_SCAN_ITEMS == 10_000
    assert text_integrity.MAX_TEXT_SCAN_BYTES == 64 * 1024 * 1024
    assert text_integrity.MAX_ARCHIVE_FILE_BYTES == 512 * 1024 * 1024
    assert text_integrity.MAX_ARCHIVE_EXPANDED_BYTES == 1024 * 1024 * 1024


def test_archive_contract_enforces_top_level_file_size_limit(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", b"valid")
    monkeypatch.setattr(text_integrity, "MAX_ARCHIVE_FILE_BYTES", archive.stat().st_size - 1, raising=False)

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "压缩文件大小" in str(result.error)


def test_archive_contract_enforces_expanded_size_limit_for_binary_members(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_integrity, "MAX_ARCHIVE_EXPANDED_BYTES", 4, raising=False)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("payload.bin", b"12345")

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "展开量" in str(result.error)


def test_nested_ooxml_is_size_checked_before_container_read(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_integrity, "MAX_ARCHIVE_EXPANDED_BYTES", 8, raising=False)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("document.docx", _ooxml_bytes("word/document.xml", b"<root>valid</root>"))

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "展开量" in str(result.error)


def test_archive_contract_enforces_scan_item_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_integrity, "MAX_TEXT_SCAN_ITEMS", 1)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.bin", b"first")
        zf.writestr("second.bin", b"second")

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "扫描项数" in str(result.error)


def test_archive_contract_enforces_text_scan_byte_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_integrity, "MAX_TEXT_SCAN_BYTES", 4)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", b"12345")

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "文本/XML" in str(result.error)


def test_nested_ooxml_shares_archive_scan_item_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_integrity, "MAX_TEXT_SCAN_ITEMS", 1)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("document.docx", _ooxml_bytes("word/document.xml", b"valid"))

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "扫描项数" in str(result.error)


def test_nested_ooxml_shares_archive_text_scan_byte_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_integrity, "MAX_TEXT_SCAN_BYTES", 7)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", b"1234")
        zf.writestr("document.docx", _ooxml_bytes("word/document.xml", b"5678"))

    result = _validate_artifact(archive)

    assert result.ok is False
    assert "文本/XML" in str(result.error)
