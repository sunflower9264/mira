#!/usr/bin/python3 -I
from __future__ import annotations

import grp
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


VALIDATOR_USER = "mira-office-validator"
VALIDATOR_GROUP = "mira-office-validator"
SYSTEMD_RUN = Path("/usr/bin/systemd-run")
SYSTEMCTL = Path("/usr/bin/systemctl")
LIBREOFFICE_CANDIDATES = (Path("/usr/bin/libreoffice"), Path("/usr/bin/soffice"))
PDFINFO = Path("/usr/bin/pdfinfo")
PDFTOTEXT = Path("/usr/bin/pdftotext")
PRLIMIT = Path("/usr/bin/prlimit")
PYTHON = Path("/usr/bin/python3")
TEMP_ROOT = Path("/tmp")
UNIT_RE = re.compile(r"mira-office-[0-9a-f]{32}\Z")
JOB_RE = re.compile(r"mira-office-[A-Za-z0-9._-]+\Z")
PDF_RE = re.compile(r"[0-9]{3}\.pdf\Z")
OFFICE_SUFFIXES = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp"}
MAX_OFFICE_DOCUMENTS = 50
UNAVAILABLE_EXIT = 69
SYSTEMD_PROPERTIES = (
    "User=mira-office-validator",
    "Group=mira-office-validator",
    "PrivateTmp=yes",
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "ProtectProc=invisible",
    "PrivateDevices=yes",
    "TemporaryFileSystem=/run",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "RestrictAddressFamilies=AF_UNIX",
    "RestrictSUIDSGID=yes",
    "LockPersonality=yes",
    "CapabilityBoundingSet=",
    "MemoryMax=3G",
    "TasksMax=128",
    "LimitNOFILE=256",
    "LimitFSIZE=536870912",
    "CPUQuota=200%",
    "RuntimeMaxSec=125",
    "KillMode=mixed",
)
_PAGES_RE = re.compile(r"^Pages:\s*(\d+)\s*$", re.MULTILINE)
_SMOKE_PROBE = r"""
import json
import os
import socket
import sys
from pathlib import Path

job_root = Path(sys.argv[1])
marker = Path(sys.argv[2])
parent_pid = int(sys.argv[3])
docker_gid = int(sys.argv[4])
result = {
    "euid": os.geteuid(),
    "groups": sorted(set(os.getgroups() + [os.getegid()])),
    "input_read": False,
    "output_write": False,
    "repo_marker_readable": False,
    "docker_socket_connected": False,
    "parent_signal_allowed": False,
    "no_new_privs": None,
}
try:
    result["input_read"] = (job_root / "input" / "smoke.txt").read_text(encoding="utf-8") == "mira-office-smoke\n"
except OSError:
    pass
try:
    (job_root / "output" / "smoke-write.txt").write_text("ok\n", encoding="utf-8")
    result["output_write"] = True
except OSError:
    pass
try:
    marker.read_bytes()
    result["repo_marker_readable"] = True
except OSError:
    pass
probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    probe.connect("/run/docker.sock")
    result["docker_socket_connected"] = True
except OSError:
    pass
finally:
    probe.close()
try:
    os.kill(parent_pid, 0)
    result["parent_signal_allowed"] = True
except OSError:
    pass
try:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("NoNewPrivs:"):
            result["no_new_privs"] = int(line.split(":", 1)[1].strip())
            break
except OSError:
    pass
result["docker_group_present"] = docker_gid >= 0 and docker_gid in result["groups"]
payload = json.dumps(result, sort_keys=True)
(job_root / "output" / "smoke.json").write_text(payload + "\n", encoding="utf-8")
print(payload)
"""


class SandboxError(RuntimeError):
    pass


def _fail(message: str, *, code: int = 64) -> int:
    print(f"mira-office-sandbox: {message}", file=sys.stderr)
    return code


def _require_executable(path: Path) -> Path:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise SandboxError(f"required executable is unavailable: {path}")
    return path


def _libreoffice_path() -> Path:
    for candidate in LIBREOFFICE_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise SandboxError("libreoffice/soffice is unavailable")


def _validate_unit(value: str) -> str:
    if UNIT_RE.fullmatch(value) is None:
        raise SandboxError("invalid unit name")
    return value


def _sudo_uid() -> int:
    value = os.environ.get("SUDO_UID")
    if value is None:
        return os.getuid()
    try:
        return int(value)
    except ValueError as exc:
        raise SandboxError("invalid SUDO_UID") from exc


def _sudo_gid() -> int:
    value = os.environ.get("SUDO_GID")
    if value is None:
        return os.getgid()
    try:
        return int(value)
    except ValueError as exc:
        raise SandboxError("invalid SUDO_GID") from exc


def _validate_job_root(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise SandboxError("job root must be an absolute, non-symlink directory")
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise SandboxError(f"job root is unavailable: {exc}") from exc
    if resolved.parent != TEMP_ROOT or JOB_RE.fullmatch(resolved.name) is None or not resolved.is_dir():
        raise SandboxError("job root must be a /tmp/mira-office-* directory")
    if metadata.st_uid != _sudo_uid():
        raise SandboxError("job root is not owned by the invoking user")
    return resolved


def _validate_validator_identity() -> None:
    try:
        docker_gid = grp.getgrnam("docker").gr_gid
    except KeyError:
        docker_gid = -1
    try:
        validator_gid = grp.getgrnam(VALIDATOR_GROUP).gr_gid
        groups = os.getgrouplist(VALIDATOR_USER, validator_gid)
    except KeyError as exc:
        raise SandboxError("validator user/group is unavailable") from exc
    if set(groups) != {validator_gid}:
        raise SandboxError("validator user must not belong to supplementary groups")
    if docker_gid >= 0 and docker_gid in groups:
        raise SandboxError("validator user must not belong to the docker group")


def _document_paths(job_root: Path) -> list[Path]:
    input_dir = job_root / "input"
    documents = sorted(
        path
        for path in input_dir.iterdir()
        if path.suffix.lower() in OFFICE_SUFFIXES and path.is_file() and not path.is_symlink()
    )
    if not documents:
        raise SandboxError("job root contains no Office documents")
    if len(documents) > MAX_OFFICE_DOCUMENTS:
        raise SandboxError(f"job root contains more than {MAX_OFFICE_DOCUMENTS} Office documents")
    return documents


def _limited(command: list[str]) -> list[str]:
    _require_executable(PRLIMIT)
    return [
        str(PRLIMIT),
        "--as=3221225472",
        "--cpu=120",
        "--fsize=536870912",
        "--nofile=256",
        "--",
        *command,
    ]


def _libreoffice_command(job_root: Path) -> list[str]:
    executable = _libreoffice_path()
    profile_dir = job_root / "profile"
    output_dir = job_root / "output"
    return _limited(
        [
            str(executable),
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            *(str(path) for path in _document_paths(job_root)),
        ]
    )


def _pdfinfo_command(job_root: Path, filename: str) -> list[str]:
    if PDF_RE.fullmatch(filename) is None:
        raise SandboxError("invalid PDF filename")
    path = job_root / "output" / filename
    if not path.is_file() or path.is_symlink():
        raise SandboxError("PDF is unavailable")
    _require_executable(PDFINFO)
    return _limited([str(PDFINFO), str(path)])


def _pdftotext_command(job_root: Path, filename: str) -> list[str]:
    if PDF_RE.fullmatch(filename) is None:
        raise SandboxError("invalid PDF filename")
    path = job_root / "output" / filename
    if not path.is_file() or path.is_symlink():
        raise SandboxError("PDF is unavailable")
    output = job_root / "output" / f"{path.stem}.bbox.html"
    if output.is_symlink():
        raise SandboxError("PDF text-boundary output is unsafe")
    _require_executable(PDFTOTEXT)
    return _limited([str(PDFTOTEXT), "-bbox-layout", str(path), str(output)])


def _systemd_command(unit: str, job_root: Path, command: list[str]) -> list[str]:
    _require_executable(SYSTEMD_RUN)
    home_dir = job_root / "home"
    return [
        str(SYSTEMD_RUN),
        "--wait",
        "--pipe",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        f"--working-directory={job_root}",
        *(f"--property={value}" for value in SYSTEMD_PROPERTIES),
        f"--property=BindPaths={job_root}",
        f"--setenv=HOME={home_dir}",
        f"--setenv=XDG_CACHE_HOME={home_dir / '.cache'}",
        f"--setenv=XDG_CONFIG_HOME={home_dir / '.config'}",
        f"--setenv=TMPDIR={job_root / 'tmp'}",
        "--setenv=SAL_DISABLE_OPENCL=1",
        "--setenv=LC_ALL=C",
        "--",
        *command,
    ]


def _run_transient(unit: str, job_root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            _systemd_command(unit, job_root, command),
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        _restore_job_ownership(job_root)
    if completed.returncode != 0 and completed.stderr.lstrip().startswith("Failed to"):
        raise SandboxError(completed.stderr.strip() or "systemd transient unit failed")
    return completed


def _restore_job_ownership(job_root: Path) -> None:
    uid = _sudo_uid()
    gid = _sudo_gid()
    for root, directories, files in os.walk(job_root, topdown=False, followlinks=False):
        root_path = Path(root)
        for name in [*files, *directories]:
            try:
                os.lchown(root_path / name, uid, gid)
            except FileNotFoundError:
                continue
        os.lchown(root_path, uid, gid)


def _relay(completed: subprocess.CompletedProcess[str]) -> int:
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode


def _run(argv: list[str]) -> int:
    if len(argv) not in {3, 4}:
        return _fail("usage: run <unit-id> <job-root> libreoffice|pdfinfo|pdftotext [NNN.pdf]")
    unit = _validate_unit(argv[0])
    job_root = _validate_job_root(argv[1])
    mode = argv[2]
    _validate_validator_identity()
    if mode == "libreoffice" and len(argv) == 3:
        command = _libreoffice_command(job_root)
    elif mode == "pdfinfo" and len(argv) == 4:
        command = _pdfinfo_command(job_root, argv[3])
    elif mode == "pdftotext" and len(argv) == 4:
        command = _pdftotext_command(job_root, argv[3])
    else:
        raise SandboxError("unsupported validator command")
    return _relay(_run_transient(unit, job_root, command))


def _stop(argv: list[str]) -> int:
    if len(argv) != 1:
        return _fail("usage: stop <unit-id>")
    unit = _validate_unit(argv[0])
    _require_executable(SYSTEMCTL)
    completed = subprocess.run(
        [str(SYSTEMCTL), "stop", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode


def _docker_gid() -> int:
    try:
        return grp.getgrnam("docker").gr_gid
    except KeyError:
        return -1


def _smoke(argv: list[str]) -> int:
    if len(argv) != 3:
        return _fail("usage: smoke <job-root> <repo-marker> <parent-pid>")
    job_root = _validate_job_root(argv[0])
    marker = Path(argv[1])
    if not marker.is_absolute() or marker.is_symlink() or not marker.is_file():
        raise SandboxError("repo marker must be an absolute, non-symlink file")
    try:
        parent_pid = int(argv[2])
    except ValueError as exc:
        raise SandboxError("invalid parent PID") from exc
    if parent_pid <= 1:
        raise SandboxError("invalid parent PID")
    _validate_validator_identity()
    _require_executable(PYTHON)

    probe_unit = f"mira-office-{uuid.uuid4().hex}"
    probe = _run_transient(
        probe_unit,
        job_root,
        [str(PYTHON), "-I", "-c", _SMOKE_PROBE, str(job_root), str(marker), str(parent_pid), str(_docker_gid())],
    )
    if probe.returncode != 0:
        return _relay(probe)
    try:
        result = json.loads((job_root / "output" / "smoke.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxError(f"smoke result is unavailable: {exc}") from exc
    expected = {
        "input_read": True,
        "output_write": True,
        "repo_marker_readable": False,
        "docker_socket_connected": False,
        "parent_signal_allowed": False,
        "docker_group_present": False,
        "no_new_privs": 1,
    }
    mismatches = {key: result.get(key) for key, value in expected.items() if result.get(key) != value}
    if result.get("euid") == 0:
        mismatches["euid"] = 0
    if mismatches:
        raise SandboxError(f"isolation smoke failed: {json.dumps(mismatches, sort_keys=True)}")

    convert = _run_transient(f"mira-office-{uuid.uuid4().hex}", job_root, _libreoffice_command(job_root))
    if convert.returncode != 0:
        return _relay(convert)
    pdf_path = job_root / "output" / "001.pdf"
    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise SandboxError("LibreOffice smoke did not create output/001.pdf")
    info = _run_transient(
        f"mira-office-{uuid.uuid4().hex}",
        job_root,
        _pdfinfo_command(job_root, pdf_path.name),
    )
    if info.returncode != 0:
        return _relay(info)
    match = _PAGES_RE.search(info.stdout)
    if match is None or int(match.group(1)) < 1:
        raise SandboxError("pdfinfo smoke did not report a non-empty PDF")
    text_bounds = _run_transient(
        f"mira-office-{uuid.uuid4().hex}",
        job_root,
        _pdftotext_command(job_root, pdf_path.name),
    )
    if text_bounds.returncode != 0:
        return _relay(text_bounds)
    bbox_path = job_root / "output" / "001.bbox.html"
    if not bbox_path.is_file() or b"<page " not in bbox_path.read_bytes():
        raise SandboxError("pdftotext smoke did not report page text bounds")
    result["pdf_pages"] = int(match.group(1))
    print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return _fail("usage: run|stop|smoke ...")
    try:
        if argv[0] == "run":
            return _run(argv[1:])
        if argv[0] == "stop":
            return _stop(argv[1:])
        if argv[0] == "smoke":
            return _smoke(argv[1:])
        return _fail("unsupported command")
    except SandboxError as exc:
        return _fail(str(exc), code=UNAVAILABLE_EXIT)
    except OSError as exc:
        return _fail(str(exc), code=UNAVAILABLE_EXIT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
