import hashlib
import os
import shutil
from pathlib import Path

from app.config import get_settings


def data_dir() -> Path:
    # 必须返回绝对路径：子进程（Claude/Codex CLI、skills 解压等）以不同 cwd 运行时，
    # 任何相对路径都会基于子进程 cwd 解析到错误位置，导致 --settings、HOME 等失效。
    path = get_settings().data_dir.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    # 同上：CLI 子进程的 cwd 是 run_workspace(...)，必须给绝对路径才能正确解析
    # --settings、CLAUDE_CONFIG_DIR、HOME 等指向 runtime/homes/<user>/... 的位置。
    path = get_settings().runtime_dir.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def skills_data_dir(user_id: str) -> Path:
    path = data_dir() / user_id / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir(user_id: str) -> Path:
    # 用户上传的附件原文按 per-user 目录隔离；后端不解析文件内容，仅提供给
    # Agent runtime 使用（随 Run.inputs / resume tool_result 的 path 字段下发）。
    path = data_dir() / user_id / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


SHARED_HOME_OWNER = "_shared"


def claude_home() -> Path:
    # 全局共享一份 fake HOME；settings.json 从 DB 派生，skills 目录按启用项同步。
    # 普通用户运行时复用同一份配置，不再 per-user 隔离。
    path = runtime_dir() / "homes" / SHARED_HOME_OWNER / "claude_home"
    (path / ".claude").mkdir(parents=True, exist_ok=True)
    return path


def claude_mcp_config_path() -> Path:
    path = claude_home() / ".mira" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def codex_home() -> Path:
    # 全局共享一份 fake HOME；config.toml / auth.json 从 DB 派生。
    path = runtime_dir() / "homes" / SHARED_HOME_OWNER / "codex_home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scoped_runtime_home(provider: str, cwd: Path, *, session_scope: str | None = None) -> Path:
    scope = session_scope.strip() if isinstance(session_scope, str) and session_scope.strip() else str(cwd.resolve())
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:20]
    path = runtime_dir() / "homes" / "_scoped" / digest / provider
    path.mkdir(parents=True, exist_ok=True)
    return path


def clone_run_scoped_homes(source_run_id: str, target_run_id: str) -> None:
    source_scope = f"run:{source_run_id}"
    target_scope = f"run:{target_run_id}"
    placeholder = runtime_dir()
    for provider in ("claude_home", "codex_home"):
        source = scoped_runtime_home(provider, placeholder, session_scope=source_scope)
        target = scoped_runtime_home(provider, placeholder, session_scope=target_scope)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)


def node_workspace(user_id: str, app_id: str, node_id: str) -> Path:
    path = runtime_dir() / "workspaces" / user_id / app_id / node_id
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def claude_cli_path() -> Path:
    return _claude_cli_path_for_runtime(runtime_dir(), os.name)


def _claude_cli_path_for_runtime(root: Path, os_name: str) -> Path:
    base = root / "bin" / "claude"
    bin_dir = base / "node_modules" / ".bin"
    if os_name == "nt":
        cmd = bin_dir / "claude.cmd"
        if cmd.exists():
            return cmd.resolve()
    else:
        shim = bin_dir / "claude"
        if shim.exists():
            return shim.resolve()
    return (base / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe").resolve()


def codex_cli_path() -> Path:
    npm_cmd = runtime_dir() / "bin" / "codex" / "node_modules" / ".bin" / "codex.cmd"
    if npm_cmd.exists():
        return npm_cmd.resolve()
    npm_shim = runtime_dir() / "bin" / "codex" / "node_modules" / ".bin" / "codex"
    if npm_shim.exists():
        return npm_shim.resolve()
    bundled = (
        runtime_dir()
        / "bin"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "codex"
        / "codex.exe"
    )
    if bundled.exists():
        return bundled.resolve()
    exe = runtime_dir() / "bin" / "codex" / "codex.exe"
    if exe.exists():
        return exe.resolve()
    return (runtime_dir() / "bin" / "codex" / "codex").resolve()
