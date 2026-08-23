import logging
import logging.config
from pathlib import Path

from .config import get_settings

# 日志落盘到 backend/logs/backend.log，与启动方式无关、跨平台一致。
# 路径解析为 backend 项目根的相对位置（log.py 在 backend/app/ 下，..; 再 / logs）。
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "backend.log"


def setup_logging() -> None:
    level = get_settings().log_level.upper()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
                # 5 MB × 5 份滚动；不会让单次运行积累过大。
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": str(_LOG_FILE),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                "mira.request": {"handlers": ["console", "file"], "level": level, "propagate": False},
                "mira.run": {"handlers": ["console", "file"], "level": level, "propagate": False},
                "mira.agent": {"handlers": ["console", "file"], "level": level, "propagate": False},
                # 涵盖 app.runtime.codex_runtime 这类
                # 直接用 logging.getLogger(__name__) 的模块日志，让它们也落盘。
                # 不动 root，避免和 uvicorn 自身的日志配置冲突。
                "app": {"handlers": ["console", "file"], "level": level, "propagate": False},
            },
        }
    )


request_logger = logging.getLogger("mira.request")
run_logger = logging.getLogger("mira.run")
agent_logger = logging.getLogger("mira.agent")
