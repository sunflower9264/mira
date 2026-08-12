from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


OFFICE_DOCUMENT_SUFFIXES = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
}
MAX_OFFICE_DOCUMENTS = 50
MAX_OFFICE_DOCUMENT_BYTES = 512 * 1024 * 1024
OFFICE_VALIDATION_TIMEOUT_SECONDS = 120
OFFICE_VALIDATOR_USER = "mira-office-validator"
OFFICE_SANDBOX_HELPER = Path("/usr/local/libexec/mira-office-sandbox")
OFFICE_SUDO = Path("/usr/bin/sudo")
OFFICE_TEMP_ROOT = Path("/tmp")
OFFICE_HELPER_UNAVAILABLE_EXIT = 69
_COMMAND_POLL_SECONDS = 0.25
_PAGES_RE = re.compile(r"^Pages:\s*(\d+)\s*$", re.MULTILINE)


class OfficeValidationUnavailable(RuntimeError):
    """Raised when the host cannot provide the configured deterministic validator."""


class _OfficeValidationCancelled(RuntimeError):
    pass


class _OfficeValidationTimedOut(RuntimeError):
    pass


@dataclass(frozen=True)
class _OfficeDocument:
    label: str
    path: Path


@dataclass(frozen=True)
class _CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    cancelled: bool = False
    timed_out: bool = False


def validate_office_documents(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
    deadline: float | None = None,
) -> str | None:
    """Require direct or ZIP-contained Office documents to open as non-empty PDFs."""

    if deadline is None:
        deadline = time.monotonic() + OFFICE_VALIDATION_TIMEOUT_SECONDS
    try:
        _check_interrupted(deadline=deadline, cancelled=cancelled)
        libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
        if libreoffice is None:
            raise OfficeValidationUnavailable("Office 文档深检不可用：宿主机未安装 libreoffice/soffice")
        pdfinfo = shutil.which("pdfinfo")
        if pdfinfo is None:
            raise OfficeValidationUnavailable("Office 文档深检不可用：宿主机未安装 pdfinfo")
        with tempfile.TemporaryDirectory(prefix="mira-office-", dir=OFFICE_TEMP_ROOT) as temp_dir_text:
            temp_dir = Path(temp_dir_text)
            documents = _materialize_office_documents(
                path,
                temp_dir / "input",
                deadline=deadline,
                cancelled=cancelled,
            )
            if not documents:
                return "未发现可验证的 Office 文档"
            return _convert_and_check(
                documents,
                libreoffice=libreoffice,
                pdfinfo=pdfinfo,
                temp_dir=temp_dir,
                deadline=deadline,
                cancelled=cancelled,
            )
    except OfficeValidationUnavailable:
        raise
    except _OfficeValidationCancelled:
        return "Office 文档深检已取消"
    except _OfficeValidationTimedOut:
        return f"Office 文档深检超过 {OFFICE_VALIDATION_TIMEOUT_SECONDS} 秒"
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return f"Office 文档深检失败：{exc}"


def _check_interrupted(*, deadline: float, cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise _OfficeValidationCancelled
    if time.monotonic() >= deadline:
        raise _OfficeValidationTimedOut


def _copy_with_checks(
    source,
    destination,
    *,
    deadline: float,
    cancelled: Callable[[], bool] | None,
) -> None:
    while True:
        _check_interrupted(deadline=deadline, cancelled=cancelled)
        chunk = source.read(1024 * 1024)
        if not chunk:
            return
        destination.write(chunk)


def _materialize_office_documents(
    path: Path,
    input_dir: Path,
    *,
    deadline: float,
    cancelled: Callable[[], bool] | None,
) -> list[_OfficeDocument]:
    _check_interrupted(deadline=deadline, cancelled=cancelled)
    input_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in OFFICE_DOCUMENT_SUFFIXES:
        if path.stat().st_size > MAX_OFFICE_DOCUMENT_BYTES:
            raise RuntimeError("Office 文档总大小超过 512 MiB")
        target = input_dir / f"001{suffix}"
        with path.open("rb") as source, target.open("wb") as destination:
            _copy_with_checks(source, destination, deadline=deadline, cancelled=cancelled)
        return [_OfficeDocument(label=path.name, path=target)]
    if suffix != ".zip":
        return []

    documents: list[_OfficeDocument] = []
    total_bytes = 0
    _check_interrupted(deadline=deadline, cancelled=cancelled)
    with zipfile.ZipFile(path) as archive:
        members = []
        for member in archive.infolist():
            _check_interrupted(deadline=deadline, cancelled=cancelled)
            if (
                not member.is_dir()
                and PurePosixPath(member.filename.replace("\\", "/")).suffix.lower()
                in OFFICE_DOCUMENT_SUFFIXES
            ):
                members.append(member)
        if len(members) > MAX_OFFICE_DOCUMENTS:
            raise RuntimeError(f"Office 文档数量超过 {MAX_OFFICE_DOCUMENTS}")
        for index, member in enumerate(members, start=1):
            _check_interrupted(deadline=deadline, cancelled=cancelled)
            total_bytes += member.file_size
            if total_bytes > MAX_OFFICE_DOCUMENT_BYTES:
                raise RuntimeError("Office 文档总大小超过 512 MiB")
            member_suffix = PurePosixPath(member.filename.replace("\\", "/")).suffix.lower()
            target = input_dir / f"{index:03d}{member_suffix}"
            with archive.open(member) as source, target.open("wb") as destination:
                _copy_with_checks(source, destination, deadline=deadline, cancelled=cancelled)
            documents.append(_OfficeDocument(label=member.filename, path=target))
    return documents


def _convert_and_check(
    documents: list[_OfficeDocument],
    *,
    libreoffice: str,
    pdfinfo: str,
    temp_dir: Path,
    deadline: float,
    cancelled: Callable[[], bool] | None,
) -> str | None:
    output_dir = temp_dir / "output"
    profile_dir = temp_dir / "profile"
    home_dir = temp_dir / "home"
    output_dir.mkdir()
    profile_dir.mkdir()
    home_dir.mkdir()
    env = {
        **os.environ,
        "HOME": str(home_dir),
        "XDG_CACHE_HOME": str(home_dir / ".cache"),
        "XDG_CONFIG_HOME": str(home_dir / ".config"),
        "TMPDIR": str(temp_dir / "tmp"),
        "SAL_DISABLE_OPENCL": "1",
    }
    Path(env["TMPDIR"]).mkdir()
    _grant_validator_access(temp_dir, deadline=deadline, cancelled=cancelled)
    command = [
        libreoffice,
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--nologo",
        "--nodefault",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        *(str(document.path) for document in documents),
    ]
    completed = _run_command(command, env=env, deadline=deadline, cancelled=cancelled)
    if completed.cancelled:
        return "Office 文档深检已取消"
    if completed.timed_out:
        return f"Office 文档深检超过 {OFFICE_VALIDATION_TIMEOUT_SECONDS} 秒"

    missing = [document.label for document in documents if not (output_dir / f"{document.path.stem}.pdf").is_file()]
    if completed.returncode != 0 or missing:
        labels = "、".join(missing or [document.label for document in documents])
        return (
            f"Office 文档无法实际打开或转换为 PDF：{labels}。"
            "请使用标准 Office 库重新保存，避免手工重写 OOXML 命名空间"
        )

    for document in documents:
        pdf_path = output_dir / f"{document.path.stem}.pdf"
        if pdf_path.stat().st_size <= 0:
            return f"Office 文档转换结果为空：{document.label}"
        info = _run_command([pdfinfo, str(pdf_path)], env=env, deadline=deadline, cancelled=cancelled)
        if info.cancelled:
            return "Office 文档深检已取消"
        if info.timed_out:
            return f"Office 文档深检超过 {OFFICE_VALIDATION_TIMEOUT_SECONDS} 秒"
        match = _PAGES_RE.search(info.stdout)
        if info.returncode != 0 or match is None or int(match.group(1)) < 1:
            return f"Office 文档未生成有效页面：{document.label}"
    return None


def _run_command(
    command: list[str],
    *,
    env: dict[str, str],
    deadline: float,
    cancelled: Callable[[], bool] | None,
) -> _CommandResult:
    command, systemd_unit = _systemd_sandbox_command(command, env=env)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise OfficeValidationUnavailable(f"Office 文档深检不可用：{exc}") from exc

    while True:
        if cancelled is not None and cancelled():
            _stop_process(process, systemd_unit=systemd_unit)
            return _CommandResult(returncode=None, cancelled=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process, systemd_unit=systemd_unit)
            return _CommandResult(returncode=None, timed_out=True)
        try:
            stdout, stderr = process.communicate(timeout=min(_COMMAND_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            continue
        if process.returncode == OFFICE_HELPER_UNAVAILABLE_EXIT or (
            process.returncode != 0 and stderr.lstrip().startswith("sudo:")
        ):
            detail = stderr.strip() or "受限校验 helper 启动失败"
            raise OfficeValidationUnavailable(f"Office 文档深检不可用：{detail}")
        return _CommandResult(returncode=process.returncode, stdout=stdout, stderr=stderr)


def _grant_validator_access(
    temp_dir: Path,
    *,
    deadline: float,
    cancelled: Callable[[], bool] | None,
) -> None:
    setfacl = shutil.which("setfacl")
    if setfacl is None:
        raise OfficeValidationUnavailable("Office 文档深检不可用：宿主机未安装 setfacl")
    acl = f"u:{OFFICE_VALIDATOR_USER}:rwx,u:{os.getuid()}:rwx"
    for mode in ("-Rm", "-Rdm"):
        _check_interrupted(deadline=deadline, cancelled=cancelled)
        remaining = deadline - time.monotonic()
        try:
            completed = subprocess.run(
                [setfacl, mode, acl, str(temp_dir)],
                check=False,
                capture_output=True,
                text=True,
                timeout=max(0.1, min(10, remaining)),
            )
        except subprocess.TimeoutExpired as exc:
            raise _OfficeValidationTimedOut from exc
        except OSError as exc:
            raise OfficeValidationUnavailable(f"Office 文档深检不可用：无法配置校验目录 ACL：{exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"setfacl exit {completed.returncode}"
            raise OfficeValidationUnavailable(f"Office 文档深检不可用：无法配置校验目录 ACL：{detail}")


def _validate_sandbox_helper() -> None:
    try:
        metadata = OFFICE_SANDBOX_HELPER.lstat()
    except OSError as exc:
        raise OfficeValidationUnavailable(f"Office 文档深检不可用：受限校验 helper 未安装：{exc}") from exc
    unsafe_mode = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or unsafe_mode
        or not os.access(OFFICE_SANDBOX_HELPER, os.X_OK)
    ):
        raise OfficeValidationUnavailable("Office 文档深检不可用：受限校验 helper 必须是 root 所有且不可由组或其他用户写入")
    if not OFFICE_SUDO.is_file() or not os.access(OFFICE_SUDO, os.X_OK):
        raise OfficeValidationUnavailable("Office 文档深检不可用：宿主机未安装 sudo")


def _systemd_sandbox_command(command: list[str], *, env: dict[str, str]) -> tuple[list[str], str | None]:
    _validate_sandbox_helper()
    sandbox_root = Path(env["HOME"]).parent.resolve()
    executable = Path(command[0]).name
    if executable in {"libreoffice", "soffice"} and "--convert-to" in command:
        helper_args = ["libreoffice"]
    elif executable == "pdfinfo" and len(command) == 2:
        pdf_path = Path(command[1]).resolve()
        if pdf_path.parent != sandbox_root / "output" or re.fullmatch(r"[0-9]{3}\.pdf", pdf_path.name) is None:
            raise OfficeValidationUnavailable("Office 文档深检不可用：pdfinfo 校验目标不符合受限 helper 契约")
        helper_args = ["pdfinfo", pdf_path.name]
    else:
        raise OfficeValidationUnavailable("Office 文档深检不可用：受限校验 helper 拒绝未知命令")
    unit = f"mira-office-{uuid.uuid4().hex}"
    wrapped = [
        str(OFFICE_SUDO),
        "-n",
        str(OFFICE_SANDBOX_HELPER),
        "run",
        unit,
        str(sandbox_root),
        *helper_args,
    ]
    return wrapped, unit


def _stop_process(process: subprocess.Popen[str], *, systemd_unit: str | None) -> None:
    if systemd_unit is not None:
        try:
            subprocess.run(
                [str(OFFICE_SUDO), "-n", str(OFFICE_SANDBOX_HELPER), "stop", systemd_unit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
