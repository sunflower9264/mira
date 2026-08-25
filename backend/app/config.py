from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./data/mira.sqlite"
    jwt_secret: str
    codex_config_secret: str = ""
    jwt_ttl_days: int = 30
    admin_username: str
    admin_password: str
    data_dir: Path = Path("./data")
    runtime_dir: Path = Path("./runtime")
    cors_origins: list[str] = ["http://localhost:5173"]
    log_level: str = "INFO"
    max_skill_size_bytes: int = 10_000_000
    max_upload_bytes: int = 20_000_000
    max_wiki_bytes: int = 500_000_000
    max_input_size_bytes: int = 1_000_000
    max_resume_text_bytes: int = 8_192
    disk_warn_bytes: int = 5_000_000_000
    disk_min_free_bytes: int = 2_000_000_000
    runtime_sandbox_image: str = "mira-agent-runtime:latest"
    runtime_docker_network: str = ""
    runtime_container_memory: str = "2g"
    runtime_container_cpus: float = 2.0
    runtime_container_pids_limit: int = 256
    display_timezone: str = "Asia/Shanghai"


@lru_cache
def get_settings() -> Settings:
    return Settings()
