from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.utils import now_utc


class Wiki(Base):
    __tablename__ = "wikis"
    __table_args__ = (UniqueConstraint("owner_id", name="ux_wikis_owner_id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(Text, default="")
    schema: Mapped[str] = mapped_column(Text, default="")
    current_revision_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    maintainer_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WikiSource(Base):
    __tablename__ = "wiki_sources"
    __table_args__ = (Index("ix_wiki_sources_wiki_status", "wiki_id", "status"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    wiki_id: Mapped[str] = mapped_column(ForeignKey("wikis.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_relpath: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WikiRevision(Base):
    __tablename__ = "wiki_revisions"
    __table_args__ = (Index("ix_wiki_revisions_wiki_created", "wiki_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    wiki_id: Mapped[str] = mapped_column(ForeignKey("wikis.id", ondelete="CASCADE"), index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    manifest_json: Mapped[str] = mapped_column(Text)
    raw_manifest_json: Mapped[str] = mapped_column(Text, default="[]")
    tree_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WikiOperation(Base):
    __tablename__ = "wiki_operations"
    __table_args__ = (Index("ix_wiki_operations_wiki_status", "wiki_id", "status"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    wiki_id: Mapped[str] = mapped_column(ForeignKey("wikis.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunWikiSnapshot(Base):
    __tablename__ = "run_wiki_snapshots"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    wiki_id: Mapped[str] = mapped_column(String(80))
    revision_id: Mapped[str] = mapped_column(String(80))
    tree_hash: Mapped[str] = mapped_column(String(64))
    raw_manifest_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WikiAccessGrant(Base):
    __tablename__ = "wiki_access_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "app_id", "graph_sha256", name="ux_wiki_access_grant_scope"),
        Index("ix_wiki_access_grants_user_app", "user_id", "app_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    graph_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
