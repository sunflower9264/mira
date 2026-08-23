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
    source_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rerun_from_node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_from_node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_version: Mapped[int] = mapped_column(Integer, default=2)


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
    reused_from_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reused_from_step_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    agent_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    pre_checkpoint_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    post_checkpoint_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


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


class RunAgentBranch(Base):
    __tablename__ = "run_agent_branches"
    __table_args__ = (Index("ix_run_agent_branches_run_state", "run_id", "state"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    parent_branch_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    fork_node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_checkpoint_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    provider_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fork_from_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_relpath: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunWorkspaceCheckpoint(Base):
    __tablename__ = "run_workspace_checkpoints"
    __table_args__ = (Index("ix_run_workspace_checkpoints_run_step", "run_id", "step_id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    branch_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), default="post_node")
    snapshot_relpath: Mapped[str] = mapped_column(Text)
    tree_hash: Mapped[str] = mapped_column(String(64))
    provider_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RunAgentOperation(Base):
    __tablename__ = "run_agent_operations"
    __table_args__ = (Index("ix_run_agent_operations_run_status", "run_id", "status"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    branch_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="running")
    provider_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
