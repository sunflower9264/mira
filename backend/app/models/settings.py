from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.utils import now_utc


class SettingsRow(Base):
    __tablename__ = "settings"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    supported_models_json: Mapped[str] = mapped_column(Text)
    skills_json: Mapped[str] = mapped_column(Text)
    mcp_servers_json: Mapped[str] = mapped_column(Text)
    workspace_git_allowed_hosts_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
