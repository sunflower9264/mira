import hashlib
import os
import shutil
import uuid
from pathlib import Path

from app.config import get_settings


def data_dir() -> Path:
    # 必须返回绝对路径：子进程（Codex、skills 解压等）以不同 cwd 运行时，
    # 任何相对路径都会基于子进程 cwd 解析到错误位置，导致 --settings、HOME 等失效。
    path = get_settings().data_dir.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    # 同上：Codex 的 cwd 是 run_workspace(...)，必须给绝对路径才能正确解析 HOME。
    path = get_settings().runtime_dir.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def skills_data_dir(user_id: str) -> Path:
    path = data_dir() / user_id / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def skill_dependency_cache_dir() -> Path:
    path = data_dir() / "_skill_dependencies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir(user_id: str) -> Path:
    # 用户上传的附件原文按 per-user 目录隔离；后端不解析文件内容，仅提供给
    # Agent runtime 使用（随 Run.inputs / resume tool_result 的 path 字段下发）。
    path = data_dir() / user_id / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


SHARED_HOME_OWNER = "_shared"


def codex_home() -> Path:
    # 全局共享一份 fake HOME；config.toml / auth.json 从 DB 派生。
    path = runtime_dir() / "homes" / SHARED_HOME_OWNER / "codex_home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scoped_codex_home(cwd: Path, *, session_scope: str | None = None) -> Path:
    scope = session_scope.strip() if isinstance(session_scope, str) and session_scope.strip() else str(cwd.resolve())
    path = _scoped_codex_home_path(scope)
    path.mkdir(parents=True, exist_ok=True)
    if scope.startswith("run:"):
        (path.parent / ".mira-scope").write_text(scope, encoding="utf-8")
    return path


def clone_run_scoped_homes(source_run_id: str, target_run_id: str) -> None:
    source_scope = f"run:{source_run_id}"
    target_scope = f"run:{target_run_id}"
    source = _scoped_codex_home_path(source_scope)
    if source.is_dir():
        target = _scoped_codex_home_path(target_scope)
        if target.exists():
            raise FileExistsError(f"目标 Run HOME 已存在：{target_run_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
        try:
            shutil.copytree(
                source,
                temporary,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".npm",
                    ".cache",
                    ".tmp",
                    "tmp",
                    "logs_*.sqlite*",
                    "thread-writer-locks",
                    "skills",
                ),
            )
            os.replace(temporary, target)
            (target.parent / ".mira-scope").write_text(target_scope, encoding="utf-8")
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def run_scoped_home_path(run_id: str) -> Path:
    return _scoped_codex_home_path(f"run:{run_id}")


def compact_run_scoped_home(run_id: str) -> None:
    home = _scoped_codex_home_path(f"run:{run_id}")
    if not home.is_dir():
        return
    for name in (".npm", ".cache", ".tmp", "tmp", "thread-writer-locks", "skills"):
        shutil.rmtree(home / name, ignore_errors=True)
    shutil.rmtree(home / ".agents" / "skills", ignore_errors=True)
    for path in home.glob("logs_*.sqlite*"):
        path.unlink(missing_ok=True)


def remove_run_scoped_home(run_id: str) -> None:
    home = _scoped_codex_home_path(f"run:{run_id}")
    shutil.rmtree(home.parent, ignore_errors=True)


def run_scoped_home_size(run_id: str) -> int:
    home = _scoped_codex_home_path(f"run:{run_id}")
    total = 0
    if not home.is_dir():
        return total
    for root, _, files in os.walk(home):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def run_scoped_home_clone_size(run_id: str) -> int:
    home = _scoped_codex_home_path(f"run:{run_id}")
    excluded_dirs = {".npm", ".cache", ".tmp", "tmp", "thread-writer-locks", "skills"}
    total = 0
    if not home.is_dir():
        return total
    for root, dirs, files in os.walk(home):
        dirs[:] = [name for name in dirs if name not in excluded_dirs]
        for name in files:
            if name.startswith("logs_") and ".sqlite" in name:
                continue
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _scoped_codex_home_path(scope: str) -> Path:
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:20]
    return runtime_dir() / "homes" / "_scoped" / digest / "codex_home"


def run_workspace(user_id: str, app_id: str, run_id: str) -> Path:
    path = run_workspace_path(user_id, app_id, run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_workspace_path(user_id: str, app_id: str, run_id: str) -> Path:
    """Return the run workspace location without creating it."""

    return get_settings().runtime_dir.resolve() / "workspaces" / user_id / app_id / run_id


def run_workspaces_root_path() -> Path:
    """Return the run workspace collection root without creating it."""

    return get_settings().runtime_dir.resolve() / "workspaces"


def nlcompile_workspace(user_id: str) -> Path:
    # NL compile 调用 LLM 时的临时 cwd；和真实 run 的 workspaces 隔离，避免误用上下文。
    path = runtime_dir() / "workspaces" / user_id / "_nlcompile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prompt_assistant_workspace(user_id: str) -> Path:
    path = runtime_dir() / "workspaces" / user_id / "_prompt_assistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


def graph_layout_workspace(user_id: str) -> Path:
    path = runtime_dir() / "workspaces" / user_id / "_graph_layout"
    path.mkdir(parents=True, exist_ok=True)
    return path
