from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.utils import now_utc


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    agent_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    content_ciphertext: Mapped[str] = mapped_column(Text)
    user_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
