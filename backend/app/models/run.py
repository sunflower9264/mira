from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.utils import now_utc


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_owner_app_started_id", "owner_id", "app_id", "started_at", "id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    inputs_json: Mapped[str] = mapped_column(Text)
    graph_json: Mapped[str] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_from_node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (Index("ix_steps_run_ordering_id", "run_id", "ordering", "id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(120), index=True)
    # ordering 由 create_run_record 按 graph 拓扑顺序写入；用作 GET /api/runs/{id}
    # 与 SSE 帧的稳定排序键，避免依赖 uuid id 字典序。
    ordering: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    input_json: Mapped[str] = mapped_column(Text, default="null")
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class StepLog(Base):
    __tablename__ = "step_logs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    level: Mapped[str] = mapped_column(String(24), default="info")
    text: Mapped[str] = mapped_column(Text)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
