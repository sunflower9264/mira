from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import mira_office_sandbox


def _job_root() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="mira-office-test-", dir="/tmp")


def test_validate_job_root_accepts_only_owned_direct_tmp_directory(monkeypatch) -> None:
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    with _job_root() as root_text:
        root = Path(root_text)
        assert mira_office_sandbox._validate_job_root(str(root)) == root.resolve()

        link = root.parent / f"{root.name}-link"
        link.symlink_to(root, target_is_directory=True)
        try:
            with pytest.raises(mira_office_sandbox.SandboxError, match="non-symlink"):
                mira_office_sandbox._validate_job_root(str(link))
        finally:
            link.unlink()

    with pytest.raises(mira_office_sandbox.SandboxError, match="/tmp/mira-office"):
        mira_office_sandbox._validate_job_root(str(Path.cwd()))


def test_systemd_command_contains_required_isolation_properties(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mira_office_sandbox, "_require_executable", lambda path: path)
    job_root = tmp_path / "mira-office-test"
    command = mira_office_sandbox._systemd_command(
        "mira-office-0123456789abcdef0123456789abcdef",
        job_root,
        ["/usr/bin/true"],
    )

    for expected in (
        "--property=User=mira-office-validator",
        "--property=Group=mira-office-validator",
        "--property=NoNewPrivileges=yes",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes",
        "--property=ProtectProc=invisible",
        "--property=PrivateDevices=yes",
        "--property=TemporaryFileSystem=/run",
        "--property=ProtectKernelModules=yes",
        "--property=ProtectKernelLogs=yes",
        "--property=CapabilityBoundingSet=",
        "--property=MemoryMax=3G",
        "--property=LimitFSIZE=536870912",
        "--property=RuntimeMaxSec=125",
        "--property=KillMode=mixed",
        f"--property=BindPaths={job_root}",
    ):
        assert expected in command
    assert not any("ProcSubset=" in item for item in command)
    assert command[-2:] == ["--", "/usr/bin/true"]


def test_helper_rejects_arbitrary_commands_and_pdf_paths(monkeypatch) -> None:
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    monkeypatch.setattr(mira_office_sandbox, "_validate_validator_identity", lambda: None)
    with _job_root() as root_text:
        root = Path(root_text)
        (root / "input").mkdir()
        (root / "output").mkdir()

        with pytest.raises(mira_office_sandbox.SandboxError, match="unsupported"):
            mira_office_sandbox._run(
                ["mira-office-0123456789abcdef0123456789abcdef", str(root), "/bin/sh"]
            )
        with pytest.raises(mira_office_sandbox.SandboxError, match="invalid PDF filename"):
            mira_office_sandbox._pdfinfo_command(root, "../outside.pdf")


def test_unit_name_is_fixed_hex_identifier() -> None:
    valid = "mira-office-0123456789abcdef0123456789abcdef"
    assert mira_office_sandbox._validate_unit(valid) == valid
    for invalid in (
        "mira-office-short",
        "mira-office-0123456789ABCDEF0123456789ABCDEF",
        f"{valid}.service",
        "other-0123456789abcdef0123456789abcdef",
    ):
        with pytest.raises(mira_office_sandbox.SandboxError, match="invalid unit"):
            mira_office_sandbox._validate_unit(invalid)


def test_run_transient_restores_job_ownership(tmp_path, monkeypatch) -> None:
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    restored: list[Path] = []
    monkeypatch.setattr(mira_office_sandbox, "_systemd_command", lambda *_args: ["systemd-run"])
    monkeypatch.setattr(mira_office_sandbox.subprocess, "run", lambda *_args, **_kwargs: completed)
    monkeypatch.setattr(mira_office_sandbox, "_restore_job_ownership", restored.append)

    result = mira_office_sandbox._run_transient("unit", tmp_path, ["/usr/bin/true"])

    assert result is completed
    assert restored == [tmp_path]
