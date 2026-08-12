from __future__ import annotations

import json
import os
import shutil
import stat
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import office_documents, output_contracts


def _fake_which(name: str) -> str:
    return f"/usr/bin/{name}"


@pytest.fixture
def fake_validator_acl(monkeypatch) -> None:
    monkeypatch.setattr(office_documents, "_grant_validator_access", lambda _path, **_kwargs: None)


def test_validate_office_documents_converts_zip_members(tmp_path, monkeypatch, fake_validator_acl) -> None:
    archive = tmp_path / "documents.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("nested/first.DOCX", b"docx")
        bundle.writestr("second.xlsx", b"xlsx")
        bundle.writestr("README.md", b"text")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> office_documents._CommandResult:
        calls.append(command)
        if "--convert-to" in command:
            output_dir = Path(command[command.index("--outdir") + 1])
            for input_path in command[command.index("--outdir") + 2 :]:
                (output_dir / f"{Path(input_path).stem}.pdf").write_bytes(b"%PDF-1.4")
            return office_documents._CommandResult(returncode=0)
        return office_documents._CommandResult(returncode=0, stdout="Pages:          2\n")

    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)
    monkeypatch.setattr(office_documents, "_run_command", fake_run)

    assert office_documents.validate_office_documents(archive) is None
    assert len([command for command in calls if "--convert-to" in command]) == 1
    assert len([command for command in calls if command[0].endswith("pdfinfo")]) == 2


def test_validate_office_documents_requires_at_least_one_document(tmp_path, monkeypatch, fake_validator_acl) -> None:
    archive = tmp_path / "documents.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("README.md", "none")

    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)

    assert office_documents.validate_office_documents(archive) == "未发现可验证的 Office 文档"


def test_validate_office_documents_rejects_missing_converted_pdf(tmp_path, monkeypatch, fake_validator_acl) -> None:
    archive = tmp_path / "documents.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("broken.docx", b"broken")

    def fake_run(command: list[str], **_kwargs) -> office_documents._CommandResult:
        return office_documents._CommandResult(returncode=0)

    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)
    monkeypatch.setattr(office_documents, "_run_command", fake_run)

    error = office_documents.validate_office_documents(archive)

    assert error is not None
    assert "broken.docx" in error
    assert "无法实际打开" in error


def test_validate_office_documents_rejects_zero_page_pdf(tmp_path, monkeypatch, fake_validator_acl) -> None:
    document = tmp_path / "empty.docx"
    document.write_bytes(b"docx")

    def fake_run(command: list[str], **_kwargs) -> office_documents._CommandResult:
        if "--convert-to" in command:
            output_dir = Path(command[command.index("--outdir") + 1])
            (output_dir / "001.pdf").write_bytes(b"%PDF-1.4")
            return office_documents._CommandResult(returncode=0)
        return office_documents._CommandResult(returncode=0, stdout="Pages:          0\n")

    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)
    monkeypatch.setattr(office_documents, "_run_command", fake_run)

    assert office_documents.validate_office_documents(document) == "Office 文档未生成有效页面：empty.docx"


def test_validate_office_documents_limits_direct_file_size(tmp_path, monkeypatch, fake_validator_acl) -> None:
    document = tmp_path / "large.docx"
    document.write_bytes(b"12345")
    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)
    monkeypatch.setattr(office_documents, "MAX_OFFICE_DOCUMENT_BYTES", 4)

    error = office_documents.validate_office_documents(document)

    assert error is not None
    assert "总大小" in error


def test_grant_validator_access_sets_access_and_default_acl(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(office_documents.shutil, "which", lambda name: "/usr/bin/setfacl" if name == "setfacl" else None)
    monkeypatch.setattr(office_documents.subprocess, "run", fake_run)

    office_documents._grant_validator_access(
        tmp_path,
        deadline=time.monotonic() + 10,
        cancelled=None,
    )

    assert calls == [
        [
            "/usr/bin/setfacl",
            "-Rm",
            f"u:mira-office-validator:rwx,u:{os.getuid()}:rwx",
            str(tmp_path),
        ],
        [
            "/usr/bin/setfacl",
            "-Rdm",
            f"u:mira-office-validator:rwx,u:{os.getuid()}:rwx",
            str(tmp_path),
        ],
    ]


def test_systemd_sandbox_command_uses_only_allowlisted_helper_modes(tmp_path, monkeypatch) -> None:
    sandbox_root = tmp_path / "mira-office-test"
    output_dir = sandbox_root / "output"
    output_dir.mkdir(parents=True)
    pdf = output_dir / "001.pdf"
    pdf.write_bytes(b"%PDF")
    env = {"HOME": str(sandbox_root / "home")}
    monkeypatch.setattr(office_documents, "_validate_sandbox_helper", lambda: None)

    convert, convert_unit = office_documents._systemd_sandbox_command(
        ["/usr/bin/libreoffice", "--convert-to", "pdf"],
        env=env,
    )
    inspect, inspect_unit = office_documents._systemd_sandbox_command(
        ["/usr/bin/pdfinfo", str(pdf)],
        env=env,
    )

    assert convert[:4] == [
        "/usr/bin/sudo",
        "-n",
        "/usr/local/libexec/mira-office-sandbox",
        "run",
    ]
    assert convert[-2:] == [str(sandbox_root.resolve()), "libreoffice"]
    assert inspect[-3:] == [str(sandbox_root.resolve()), "pdfinfo", "001.pdf"]
    assert convert_unit is not None and convert_unit.startswith("mira-office-")
    assert inspect_unit is not None and inspect_unit.startswith("mira-office-")

    with pytest.raises(office_documents.OfficeValidationUnavailable, match="拒绝未知命令"):
        office_documents._systemd_sandbox_command(["/bin/sh", "-c", "id"], env=env)
    with pytest.raises(office_documents.OfficeValidationUnavailable, match="不符合受限 helper 契约"):
        office_documents._systemd_sandbox_command(
            ["/usr/bin/pdfinfo", str(output_dir / "../outside.pdf")],
            env=env,
        )


def test_validate_sandbox_helper_rejects_non_root_or_writable_install(tmp_path, monkeypatch) -> None:
    helper = tmp_path / "mira-office-sandbox"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    monkeypatch.setattr(office_documents, "OFFICE_SANDBOX_HELPER", helper)

    with pytest.raises(office_documents.OfficeValidationUnavailable, match="root 所有"):
        office_documents._validate_sandbox_helper()

    fake_metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o775, st_uid=0)
    monkeypatch.setattr(Path, "lstat", lambda _self: fake_metadata)
    monkeypatch.setattr(os, "access", lambda _path, _mode: True)
    with pytest.raises(office_documents.OfficeValidationUnavailable, match="root 所有"):
        office_documents._validate_sandbox_helper()


def test_validate_office_documents_cancels_during_direct_copy(tmp_path, monkeypatch) -> None:
    document = tmp_path / "large.docx"
    document.write_bytes(b"x" * (2 * 1024 * 1024))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)

    assert office_documents.validate_office_documents(document, cancelled=cancelled) == "Office 文档深检已取消"


def test_validate_office_documents_cancels_during_zip_member_copy(tmp_path, monkeypatch) -> None:
    archive_path = tmp_path / "large.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("large.docx", b"x" * (2 * 1024 * 1024))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 7

    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)

    assert office_documents.validate_office_documents(archive_path, cancelled=cancelled) == "Office 文档深检已取消"


def test_validate_office_documents_checks_deadline_before_materializing(tmp_path, monkeypatch) -> None:
    document = tmp_path / "document.docx"
    document.write_bytes(b"docx")
    monkeypatch.setattr(office_documents.shutil, "which", _fake_which)

    error = office_documents.validate_office_documents(document, deadline=time.monotonic() - 1)

    assert error == "Office 文档深检超过 120 秒"


def test_artifact_contract_reuses_one_office_deadline(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    for path in (first, second):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("README.txt", "ok")
    deadlines: list[float | None] = []

    def validate_office(_path: Path, **kwargs) -> str | None:
        deadlines.append(kwargs.get("deadline"))
        return None

    monkeypatch.setattr(output_contracts, "validate_office_documents", validate_office)
    node = {
        "type": "generate",
        "output_contract": {
            "type": "artifact",
            "artifact_kind": "zip",
            "max_count": 2,
            "validate_office_documents": True,
        },
    }
    output = json.dumps(
        {
            "artifacts": [
                {"path": first.name, "name": first.name},
                {"path": second.name, "name": second.name},
            ]
        }
    )

    result = output_contracts.validate_contract_output(node, output, workspace=tmp_path)

    assert result.ok
    assert len(deadlines) == 2
    assert deadlines[0] is not None
    assert deadlines[0] == deadlines[1]


def _write_minimal_docx(path: Path, *, prefixed_relationships: bool) -> None:
    relationships = (
        '<rel:Relationships xmlns:rel="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<rel:Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</rel:Relationships>"
        if prefixed_relationships
        else '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w") as document:
        document.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        document.writestr("_rels/.rels", relationships)
        document.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Mira Office validation</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )


@pytest.mark.skipif(
    not (
        (shutil.which("libreoffice") or shutil.which("soffice"))
        and shutil.which("pdfinfo")
        and shutil.which("setfacl")
        and office_documents.OFFICE_SANDBOX_HELPER.is_file()
    ),
    reason="host LibreOffice/pdfinfo/setfacl or system Office sandbox helper unavailable",
)
def test_real_libreoffice_rejects_prefixed_relationship_namespace(tmp_path) -> None:
    valid = tmp_path / "valid.docx"
    invalid = tmp_path / "invalid.docx"
    _write_minimal_docx(valid, prefixed_relationships=False)
    _write_minimal_docx(invalid, prefixed_relationships=True)

    assert office_documents.validate_office_documents(valid) is None
    error = office_documents.validate_office_documents(invalid)
    assert error is not None
    assert "无法实际打开" in error
