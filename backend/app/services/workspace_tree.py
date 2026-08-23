from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkspaceTreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    sha256: str
    size: int


@dataclass(frozen=True)
class TreeChange:
    path: str
    kind: str
    sha256: str | None
    size: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "size": self.size,
        }


class WorkspaceTree:
    """Owns the writable branch worktrees and immutable run checkpoints."""

    def __init__(self, run_root: Path):
        self.run_root = run_root.resolve()
        self.root = self.run_root / "agent"
        self.branches_root = self.root / "branches"
        self.checkpoints_root = self.root / "checkpoints"
        self.branches_root.mkdir(parents=True, exist_ok=True)
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)

    def branch_workspace(self, branch_id: str) -> Path:
        return self.branches_root / _safe_segment(branch_id) / "work"

    def checkpoint_snapshot(self, checkpoint_id: str) -> Path:
        return self.checkpoints_root / _safe_segment(checkpoint_id) / "tree"

    def create_empty_branch(self, branch_id: str) -> Path:
        workspace = self.branch_workspace(branch_id)
        if workspace.exists():
            return workspace
        workspace.mkdir(parents=True, exist_ok=False)
        return workspace

    def fork_branch(self, checkpoint_id: str, branch_id: str) -> Path:
        source = self.checkpoint_snapshot(checkpoint_id)
        if not source.is_dir():
            raise WorkspaceTreeError(f"checkpoint 不存在：{checkpoint_id}")
        target = self.branch_workspace(branch_id)
        _copy_tree_atomic(source, target)
        _make_writable(target)
        return target

    def create_checkpoint(self, branch_id: str, checkpoint_id: str) -> tuple[Path, str]:
        source = self.branch_workspace(branch_id)
        if not source.is_dir():
            raise WorkspaceTreeError(f"branch workspace 不存在：{branch_id}")
        target = self.checkpoint_snapshot(checkpoint_id)
        _copy_tree_atomic(source, target)
        _make_read_only(target)
        return target, tree_hash(target)

    def clone_checkpoint_from(self, source: Path, checkpoint_id: str) -> tuple[Path, str]:
        if not source.is_dir():
            raise WorkspaceTreeError("来源 checkpoint 不存在")
        target = self.checkpoint_snapshot(checkpoint_id)
        _copy_tree_atomic(source, target)
        _make_read_only(target)
        return target, tree_hash(target)

    def diff(self, checkpoint_id: str, branch_id: str) -> list[TreeChange]:
        base = scan_tree(self.checkpoint_snapshot(checkpoint_id))
        current = scan_tree(self.branch_workspace(branch_id))
        changes: list[TreeChange] = []
        for path in sorted(set(base) | set(current)):
            before = base.get(path)
            after = current.get(path)
            if before == after:
                continue
            if after is None:
                changes.append(TreeChange(path=path, kind="deleted", sha256=None, size=None))
            else:
                changes.append(
                    TreeChange(path=path, kind=after.kind, sha256=after.sha256, size=after.size)
                )
        return changes

    def discard_branch(self, branch_id: str) -> None:
        path = self.branches_root / _safe_segment(branch_id)
        remove_tree(path)


def remove_tree(path: Path) -> None:
    """Remove a workspace tree even when it contains immutable checkpoints."""
    if not path.exists():
        return
    for current in path.rglob("*"):
        if current.is_symlink():
            continue
        current.chmod(current.stat().st_mode | (0o700 if current.is_dir() else 0o600))
    path.chmod(path.stat().st_mode | 0o700)
    shutil.rmtree(path)


def scan_tree(root: Path) -> dict[str, TreeEntry]:
    if not root.is_dir():
        raise WorkspaceTreeError(f"workspace tree 不存在：{root}")
    entries: dict[str, TreeEntry] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if stat.S_ISREG(mode):
            digest = _file_sha256(path)
            entries[relative] = TreeEntry(relative, "file", digest, path.stat().st_size)
            continue
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            encoded = target.encode("utf-8", errors="surrogateescape")
            entries[relative] = TreeEntry(relative, "symlink", hashlib.sha256(encoded).hexdigest(), len(encoded))
            continue
        raise WorkspaceTreeError(f"workspace 包含不支持的特殊文件：{relative}")
    return entries


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for entry in scan_tree(root).values():
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _copy_tree_atomic(source: Path, target: Path) -> None:
    if target.exists():
        raise WorkspaceTreeError(f"目标 workspace 已存在：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary, symlinks=True, copy_function=_copy_file)
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _copy_file(source: str, target: str) -> str:
    source_path = Path(source)
    target_path = Path(target)
    try:
        with source_path.open("rb") as source_handle, target_path.open("wb") as target_handle:
            import fcntl

            fcntl.ioctl(target_handle.fileno(), 0x40049409, source_handle.fileno())
        shutil.copystat(source_path, target_path, follow_symlinks=False)
    except (ImportError, OSError):
        try:
            target_path.unlink()
        except FileNotFoundError:
            pass
        shutil.copy2(source_path, target_path, follow_symlinks=False)
    return str(target_path)


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        path.chmod(mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def _make_writable(root: Path) -> None:
    root.chmod(root.stat().st_mode | 0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        path.chmod(mode | (0o700 if path.is_dir() else 0o600))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_segment(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("._")
    return safe or hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
