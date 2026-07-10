from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from types import TracebackType
from typing import Awaitable, Callable

from app.runtime.ask_user_bridge import InternalAskUserBridge
from app.runtime.base import AskUserRequest, AskUserResult
from app.runtime.sandbox import RuntimePathMap
from app.services.runtime_uploads import (
    RuntimeUploadContext,
    ensure_runtime_upload_context,
    stage_ask_user_result_for_runtime,
)


class RuntimeCallContext:
    def __init__(
        self,
        *,
        user_id: str,
        workspace: Path,
        home: Path,
        on_ask_user: Callable[[AskUserRequest], Awaitable[AskUserResult]] | None,
    ) -> None:
        self.user_id = user_id
        self.workspace = workspace
        self.home = home
        self.call_id = f"call_{uuid.uuid4().hex}"
        self.call_dir = home / ".mira" / "calls" / self.call_id
        self.prompt_path = self.call_dir / "prompt.txt"
        self.path_map: RuntimePathMap | None = None
        self.upload_context: RuntimeUploadContext | None = None
        self.bridge: InternalAskUserBridge | None = None
        self._on_ask_user = on_ask_user
        self._upload_cm = None
        self._bridge_cm: InternalAskUserBridge | None = None
        self._cleanup_paths: list[Path] = []

    async def __aenter__(self) -> "RuntimeCallContext":
        self.call_dir.mkdir(parents=True, exist_ok=True)
        self._upload_cm = ensure_runtime_upload_context(self.workspace)
        self.upload_context = self._upload_cm.__enter__()
        self.path_map = RuntimePathMap.for_call(
            user_id=self.user_id,
            workspace=self.workspace,
            home=self.home,
        )
        if self._on_ask_user is not None:

            async def ask_user_callback(request: AskUserRequest) -> AskUserResult:
                assert self.upload_context is not None
                return stage_ask_user_result_for_runtime(
                    self.upload_context,
                    await self._on_ask_user(request),
                )

            self._bridge_cm = InternalAskUserBridge(ask_user_callback)
            self.bridge = await self._bridge_cm.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._bridge_cm is not None:
            await self._bridge_cm.__aexit__(exc_type, exc, tb)
        if self._upload_cm is not None:
            self._upload_cm.__exit__(exc_type, exc, tb)
        for path in self._cleanup_paths:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        shutil.rmtree(self.call_dir, ignore_errors=True)

    def bridge_env(self) -> dict[str, str]:
        if self.bridge is None:
            return {}
        return {
            "MIRA_ASK_USER_BRIDGE_URL": self.bridge.url,
            "MIRA_ASK_USER_BRIDGE_TOKEN": self.bridge.token,
        }

    def require_path_map(self) -> RuntimePathMap:
        if self.path_map is None:
            raise RuntimeError("RuntimeCallContext has not been entered")
        return self.path_map

    def register_cleanup(self, path: Path) -> None:
        self._cleanup_paths.append(path)
