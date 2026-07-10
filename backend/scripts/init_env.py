"""初始化 backend/.env：从 .env.example 拷贝，并填充随机 secret。

幂等：.env 已存在时只补齐缺失的 AGENT_CONFIG_SECRET，不覆盖现有 secret。
"""

from __future__ import annotations

import base64
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
ENV_PATH = BACKEND_ROOT / ".env"
EXAMPLE_PATH = BACKEND_ROOT / ".env.example"
PLACEHOLDER = "__GENERATED_BY_INIT_ENV__"


def _random_secret() -> str:
    return base64.b64encode(os.urandom(48)).decode()


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def main() -> int:
    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
        updated = text
        if "AGENT_CONFIG_SECRET=" not in updated:
            updated = updated.rstrip() + f"\nAGENT_CONFIG_SECRET={_fernet_key()}\n"
        else:
            updated = updated.replace(f"AGENT_CONFIG_SECRET={PLACEHOLDER}", f"AGENT_CONFIG_SECRET={_fernet_key()}")
        if updated != text:
            ENV_PATH.write_text(updated, encoding="utf-8")
            print(f"[init_env] Updated {ENV_PATH} with AGENT_CONFIG_SECRET")
        return 0
    if not EXAMPLE_PATH.exists():
        print(f"[init_env] {EXAMPLE_PATH} not found", file=sys.stderr)
        return 1
    shutil.copyfile(EXAMPLE_PATH, ENV_PATH)
    text = ENV_PATH.read_text(encoding="utf-8")
    text = text.replace(PLACEHOLDER, _random_secret(), 1)
    text = text.replace(PLACEHOLDER, _fernet_key(), 1)
    ENV_PATH.write_text(text, encoding="utf-8")
    print(f"[init_env] Created {ENV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
