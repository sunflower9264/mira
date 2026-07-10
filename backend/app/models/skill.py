from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.utils import now_utc


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    archive_name: Mapped[str] = mapped_column(String(255))
    archive_size: Mapped[int] = mapped_column(Integer)
    archive_path: Mapped[str] = mapped_column(Text)
    archive_md5: Mapped[str] = mapped_column(String(32), default="", server_default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    planning_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
