from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_VERSION = 2


class WorkspaceTreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    sha256: str
    size: int
    mode: int | None = None
    target: str | None = None


@dataclass(frozen=True)
class TreeChange:
    path: str
    kind: str
    sha256: str | None
    size: int | None

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "sha256": self.sha256, "size": self.size}


class WorkspaceTree:
    """Owns writable branches and content-addressed immutable checkpoints.

    New checkpoints contain only a manifest plus one object per unique file.  A
    legacy directory checkpoint is still readable so existing runs can be
    migrated lazily and rerun without a flag day.
    """

    def __init__(self, run_root: Path, *, initialize: bool = True):
        self.run_root = run_root.resolve()
        self.root = self.run_root / "agent"
        self.branches_root = self.root / "branches"
        self.checkpoints_root = self.root / "checkpoints"
        self.objects_root = self.root / "objects"
        if initialize:
            self.branches_root.mkdir(parents=True, exist_ok=True)
            self.checkpoints_root.mkdir(parents=True, exist_ok=True)
            self.objects_root.mkdir(parents=True, exist_ok=True)

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
        target = self.branch_workspace(branch_id)
        if target.exists():
            raise WorkspaceTreeError(f"目标 branch 已存在：{branch_id}")
        target.mkdir(parents=True, exist_ok=False)
        try:
            self._materialize_checkpoint(source, target)
            _make_writable(target)
        except Exception:
            remove_tree(target)
            raise
        return target

    def materialize_checkpoint(self, checkpoint_id: str, target: Path) -> Path:
        source = self.checkpoint_snapshot(checkpoint_id)
        if target.exists():
            raise WorkspaceTreeError(f"目标 workspace 已存在：{target}")
        target.mkdir(parents=True, exist_ok=False)
        try:
            self._materialize_checkpoint(source, target)
        except Exception:
            remove_tree(target)
            raise
        return target

    def checkpoint_hash(self, checkpoint_id: str) -> str:
        return tree_hash(self.checkpoint_snapshot(checkpoint_id))

    def verify_checkpoint(self, checkpoint_id: str) -> str:
        snapshot = self.checkpoint_snapshot(checkpoint_id)
        entries, objects_root = _load_snapshot(snapshot)
        for entry in entries.values():
            if entry.kind != "file":
                continue
            source = objects_root / _object_relpath(entry.sha256) if objects_root else snapshot / entry.path
            if (
                not source.is_file()
                or source.stat().st_size != entry.size
                or _file_sha256(source) != entry.sha256
            ):
                raise WorkspaceTreeError(f"checkpoint 对象完整性校验失败：{entry.path}")
        return _entries_hash(entries)

    def migrate_legacy_checkpoint(
        self,
        checkpoint_id: str,
        *,
        expected_hash: str,
        keep_legacy: bool,
    ) -> bool:
        source = self.checkpoint_snapshot(checkpoint_id)
        manifest = source / "manifest.json"
        if manifest.is_file():
            if tree_hash(source) != expected_hash:
                raise WorkspaceTreeError(f"checkpoint hash 不一致：{checkpoint_id}")
            return False
        if not source.is_dir():
            raise WorkspaceTreeError(f"checkpoint 不存在：{checkpoint_id}")
        entries = _scan_tree_with_dirs(source)
        if _entries_hash(entries) != expected_hash:
            raise WorkspaceTreeError(f"checkpoint hash 不一致：{checkpoint_id}")
        for entry in entries.values():
            if entry.kind != "file":
                continue
            source_file = source / entry.path
            allow_link = source_file.stat().st_mode & 0o222 == 0
            self._store_object(source_file, entry.sha256, allow_link=allow_link)
        temporary = source.parent / f".{source.name}.cas-v2-{uuid.uuid4().hex}"
        legacy = source.parent / "legacy-tree"
        if temporary.exists() or legacy.exists():
            raise WorkspaceTreeError(f"checkpoint 迁移暂存目录已存在：{checkpoint_id}")
        temporary.mkdir()
        (temporary / "manifest.json").write_text(_manifest_json(entries), encoding="utf-8")
        _make_read_only(temporary)
        try:
            os.replace(source, legacy)
            os.replace(temporary, source)
        except Exception:
            if legacy.exists() and not source.exists():
                os.replace(legacy, source)
            remove_tree(temporary)
            raise
        if self.verify_checkpoint(checkpoint_id) != expected_hash:
            remove_tree(source)
            os.replace(legacy, source)
            raise WorkspaceTreeError(f"迁移后 checkpoint hash 不一致：{checkpoint_id}")
        if not keep_legacy:
            remove_tree(legacy)
        return True

    def drop_legacy_checkpoint(self, checkpoint_id: str, *, expected_hash: str) -> bool:
        source = self.checkpoint_snapshot(checkpoint_id)
        legacy = source.parent / "legacy-tree"
        if not legacy.exists():
            return False
        if not (source / "manifest.json").is_file() or self.verify_checkpoint(checkpoint_id) != expected_hash:
            raise WorkspaceTreeError(f"CAS checkpoint 校验失败：{checkpoint_id}")
        if tree_hash(legacy) != expected_hash:
            raise WorkspaceTreeError(f"legacy checkpoint 校验失败：{checkpoint_id}")
        remove_tree(legacy)
        return True

    def create_checkpoint(self, branch_id: str, checkpoint_id: str) -> tuple[Path, str]:
        source = self.branch_workspace(branch_id)
        if not source.is_dir():
            raise WorkspaceTreeError(f"branch workspace 不存在：{branch_id}")
        target = self.checkpoint_snapshot(checkpoint_id)
        if target.exists():
            raise WorkspaceTreeError(f"目标 checkpoint 已存在：{checkpoint_id}")
        entries = _scan_tree_with_dirs(source)
        for entry in entries.values():
            if entry.kind == "file":
                self._store_object(source / entry.path, entry.sha256, allow_link=False)
        target.mkdir(parents=True, exist_ok=False)
        manifest = target / "manifest.json"
        manifest.write_text(_manifest_json(entries), encoding="utf-8")
        _make_read_only(target)
        return target, _entries_hash(entries)

    def clone_checkpoint_from(self, source: Path, checkpoint_id: str) -> tuple[Path, str]:
        """Clone a checkpoint into this run without copying unchanged content."""
        target = self.checkpoint_snapshot(checkpoint_id)
        if target.exists():
            raise WorkspaceTreeError(f"目标 checkpoint 已存在：{checkpoint_id}")
        entries, source_objects = _load_snapshot(source)
        for entry in entries.values():
            if entry.kind != "file":
                continue
            source_object = source_objects / _object_relpath(entry.sha256) if source_objects else source / entry.path
            if not source_object.is_file():
                raise WorkspaceTreeError(f"checkpoint 对象不存在：{entry.path}")
            self._store_object(source_object, entry.sha256, allow_link=True)
        target.mkdir(parents=True, exist_ok=False)
        (target / "manifest.json").write_text(_manifest_json(entries), encoding="utf-8")
        _make_read_only(target)
        return target, _entries_hash(entries)

    def diff(self, checkpoint_id: str, branch_id: str) -> list[TreeChange]:
        base = {
            path: entry
            for path, entry in scan_tree(self.checkpoint_snapshot(checkpoint_id)).items()
            if entry.kind != "dir"
        }
        current = {
            path: entry
            for path, entry in scan_tree(self.branch_workspace(branch_id)).items()
            if entry.kind != "dir"
        }
        changes: list[TreeChange] = []
        for path in sorted(set(base) | set(current)):
            before, after = base.get(path), current.get(path)
            if _same_entry(before, after):
                continue
            if after is None:
                changes.append(TreeChange(path=path, kind="deleted", sha256=None, size=None))
            else:
                changes.append(TreeChange(path=path, kind=after.kind, sha256=after.sha256, size=after.size))
        return changes

    def discard_branch(self, branch_id: str) -> None:
        remove_tree(self.branches_root / _safe_segment(branch_id))

    def _materialize_checkpoint(self, source: Path, target: Path) -> None:
        entries, objects_root = _load_snapshot(source)
        directory_modes: list[tuple[Path, int]] = []
        for entry in entries.values():
            destination = target / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if entry.kind == "dir":
                destination.mkdir(parents=True, exist_ok=True)
                if entry.mode is not None:
                    directory_modes.append((destination, entry.mode))
            elif entry.kind == "symlink":
                destination.symlink_to(entry.target or "")
            elif entry.kind == "file":
                source_object = objects_root / _object_relpath(entry.sha256) if objects_root else source / entry.path
                if not source_object.is_file():
                    raise WorkspaceTreeError(f"checkpoint 对象不存在：{entry.path}")
                if source_object.stat().st_size != entry.size or _file_sha256(source_object) != entry.sha256:
                    raise WorkspaceTreeError(f"checkpoint 对象完整性校验失败：{entry.path}")
                shutil.copy2(source_object, destination, follow_symlinks=False)
                if entry.mode is not None:
                    destination.chmod(entry.mode)
            else:
                raise WorkspaceTreeError(f"workspace 包含不支持的文件类型：{entry.path}")
        for directory, mode in reversed(directory_modes):
            directory.chmod(mode)

    def _store_object(self, source: Path, digest: str, *, allow_link: bool) -> None:
        target = self.objects_root / _object_relpath(digest)
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
        try:
            if allow_link:
                try:
                    os.link(source, temporary)
                except OSError:
                    shutil.copy2(source, temporary, follow_symlinks=False)
            else:
                _copy_file(source, temporary)
            if _file_sha256(temporary) != digest:
                raise WorkspaceTreeError(f"对象 hash 校验失败：{source}")
            os.replace(temporary, target)
            target.chmod(target.stat().st_mode & ~0o222)
        finally:
            temporary.unlink(missing_ok=True)


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or path.is_file():
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o600)
        path.unlink()
        return
    for current in path.rglob("*"):
        if current.is_symlink():
            continue
        try:
            if current.is_dir():
                current.chmod(current.stat().st_mode | 0o700)
        except OSError:
            pass
    try:
        path.chmod(path.stat().st_mode | 0o700)
    except OSError:
        pass
    shutil.rmtree(path)


def scan_tree(root: Path) -> dict[str, TreeEntry]:
    return {
        path: entry
        for path, entry in _scan_tree_with_dirs(root).items()
        if entry.kind != "dir"
    }


def _scan_tree_with_dirs(root: Path) -> dict[str, TreeEntry]:
    if not root.is_dir():
        raise WorkspaceTreeError(f"workspace tree 不存在：{root}")
    manifest = root / "manifest.json"
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if raw.get("version") == MANIFEST_VERSION:
                return _entries_from_manifest(raw)
        except (OSError, ValueError, TypeError):
            pass
    entries: dict[str, TreeEntry] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            entries[relative] = TreeEntry(relative, "dir", "", 0, mode & 0o7777)
            continue
        if stat.S_ISREG(mode):
            entries[relative] = TreeEntry(relative, "file", _file_sha256(path), path.stat().st_size, mode & 0o7777)
            continue
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            encoded = target.encode("utf-8", errors="surrogateescape")
            entries[relative] = TreeEntry(relative, "symlink", hashlib.sha256(encoded).hexdigest(), len(encoded), target=target)
            continue
        raise WorkspaceTreeError(f"workspace 包含不支持的特殊文件：{relative}")
    return entries


def tree_hash(root: Path) -> str:
    entries, _ = _load_snapshot(root) if (root / "manifest.json").is_file() else (scan_tree(root), None)
    return _entries_hash(entries)


def _load_snapshot(snapshot: Path) -> tuple[dict[str, TreeEntry], Path | None]:
    if not snapshot.is_dir():
        raise WorkspaceTreeError("来源 checkpoint 不存在")
    manifest = snapshot / "manifest.json"
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if raw.get("version") == MANIFEST_VERSION:
                return _entries_from_manifest(raw), snapshot.parents[2] / "objects"
        except (OSError, ValueError, TypeError) as exc:
            raise WorkspaceTreeError("checkpoint manifest 无效") from exc
    return _scan_tree_with_dirs(snapshot), None


def _entries_from_manifest(raw: dict[str, Any]) -> dict[str, TreeEntry]:
    entries: dict[str, TreeEntry] = {}
    for item in raw.get("entries", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise WorkspaceTreeError("checkpoint manifest 条目无效")
        entry = TreeEntry(
            item["path"], item.get("kind", ""), item.get("sha256", ""), int(item.get("size", 0)),
            int(item["mode"]) if item.get("mode") is not None else None,
            item.get("target") if isinstance(item.get("target"), str) else None,
        )
        _validate_relative_path(entry.path)
        if entry.kind not in {"dir", "file", "symlink"}:
            raise WorkspaceTreeError("checkpoint manifest 文件类型无效")
        if entry.path in entries or entry.size < 0:
            raise WorkspaceTreeError("checkpoint manifest 条目无效")
        if entry.kind in {"file", "symlink"} and (
            len(entry.sha256) != 64 or any(char not in "0123456789abcdef" for char in entry.sha256)
        ):
            raise WorkspaceTreeError("checkpoint manifest hash 无效")
        if entry.kind == "symlink" and entry.target is None:
            raise WorkspaceTreeError("checkpoint manifest 链接无效")
        entries[entry.path] = entry
    return entries


def _manifest_json(entries: dict[str, TreeEntry]) -> str:
    return json.dumps(
        {"version": MANIFEST_VERSION, "entries": [
            {"path": e.path, "kind": e.kind, "sha256": e.sha256, "size": e.size, "mode": e.mode, "target": e.target}
            for e in entries.values()
        ]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _entries_hash(entries: dict[str, TreeEntry]) -> str:
    digest = hashlib.sha256()
    for entry in entries.values():
        if entry.kind == "dir":
            continue
        digest.update(entry.path.encode("utf-8")); digest.update(b"\0")
        digest.update(entry.kind.encode("ascii")); digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii")); digest.update(b"\0")
        digest.update(str(entry.size).encode("ascii")); digest.update(b"\n")
    return digest.hexdigest()


def _object_relpath(digest: str) -> Path:
    return Path(digest[:2]) / digest


def _same_entry(left: TreeEntry | None, right: TreeEntry | None) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.path,
        left.kind,
        left.sha256,
        left.size,
        left.target,
    ) == (
        right.path,
        right.kind,
        right.sha256,
        right.size,
        right.target,
    )


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkspaceTreeError("checkpoint manifest 路径无效")


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def _make_writable(root: Path) -> None:
    root.chmod(root.stat().st_mode | 0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(path.stat().st_mode | (0o700 if path.is_dir() else 0o600))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, target: Path) -> None:
    try:
        with source.open("rb") as source_handle, target.open("wb") as target_handle:
            import fcntl

            fcntl.ioctl(target_handle.fileno(), 0x40049409, source_handle.fileno())
        shutil.copystat(source, target, follow_symlinks=False)
    except (ImportError, OSError):
        target.unlink(missing_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def _safe_segment(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("._")
    return safe or hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
