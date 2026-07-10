"""后端开发启动入口。

依次执行：
  1) init_env：确保 .env 存在并填充 JWT_SECRET（幂等）
  2) ensure_runtimes：检查/构建 Docker Agent runtime 镜像（已存在则跳过）
  3) init_admin：读取 .env 中的 ADMIN_USERNAME / ADMIN_PASSWORD，upsert 管理员账号
  4) `python -m uvicorn` 启动热重载

入口本身用 `uv run python backend/scripts/dev.py` 调起，确保运行在 venv 内。
脚本内部复用当前 Python，避免 Windows 上嵌套调用 `uv run <console-script>` 时
出现 trampoline 路径规范化失败。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent


def _run(args: list[str], cwd: Path | None = None) -> int:
    print(f"[dev] $ {' '.join(args)}")
    return subprocess.call(args, cwd=str(cwd) if cwd else None)


def main() -> int:
    rc = _run([sys.executable, str(SCRIPT_DIR / "init_env.py")])
    if rc != 0:
        return rc
    rc = _run([sys.executable, str(SCRIPT_DIR / "ensure_runtimes.py")])
    if rc != 0:
        return rc
    rc = _run(
        [sys.executable, str(SCRIPT_DIR / "init_admin.py")],
        cwd=BACKEND_ROOT,
    )
    if rc != 0:
        return rc
    return _run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--reload",
            "--reload-dir",
            "app",
            "--reload-dir",
            "scripts",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        cwd=BACKEND_ROOT,
    )


if __name__ == "__main__":
    sys.exit(main())
