from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.utils import now_utc


class CodexConfig(Base):
    __tablename__ = "codex_config"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="codex")
    config_ciphertext: Mapped[str] = mapped_column(Text)
    auth_ciphertext: Mapped[str] = mapped_column(Text)
    config_user_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_user_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
