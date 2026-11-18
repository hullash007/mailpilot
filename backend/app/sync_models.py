"""Candidate sync tables. Not imported by deployed app startup.

Call Base.metadata.create_all only in an explicit, reviewed migration after
importing this module. Existing tables/columns are not rewritten.
"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .models import JSONType


class MailboxSync(Base):
    __tablename__ = 'mailbox_sync'
    account_id: Mapped[int] = mapped_column(ForeignKey('gmail_accounts.id'), primary_key=True)
    generation: Mapped[int] = mapped_column(default=1)
    completed_generation: Mapped[int] = mapped_column(default=0)
    revision: Mapped[int] = mapped_column(default=0)
    scope: Mapped[str] = mapped_column(String(64), default='non_spam_trash')
    phase: Mapped[str] = mapped_column(String(24), default='initial')
    retry_attempts: Mapped[int] = mapped_column(default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_exhausted: Mapped[bool] = mapped_column(default=False)
    pending_page: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    token_restarts: Mapped[int] = mapped_column(default=0)
    blocked: Mapped[bool] = mapped_column(default=False)
    page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    anchor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str] = mapped_column(String(40), default='')


class SyncRun(Base):
    __tablename__ = 'sync_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('gmail_accounts.id'), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    phase: Mapped[str] = mapped_column(String(24))
    outcome: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str] = mapped_column(String(40), default='')
    inserted: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    remote_deleted: Mapped[int] = mapped_column(default=0)


class MessageRemoteState(Base):
    __tablename__ = 'message_remote_state'
    __table_args__ = (UniqueConstraint('account_id', 'gmail_message_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('gmail_accounts.id'), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    excluded: Mapped[bool] = mapped_column(default=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default='')


class ThreadSnapshot(Base):
    __tablename__ = 'thread_snapshots'
    __table_args__ = (UniqueConstraint('account_id', 'gmail_thread_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('gmail_accounts.id'), index=True)
    gmail_thread_id: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    message_count: Mapped[int] = mapped_column()
    observed_generation: Mapped[int] = mapped_column(default=0)
    normalized_fingerprint: Mapped[str] = mapped_column(String(64), default='')
    provider_history_id: Mapped[str] = mapped_column(String(64), default='0')
    message_ids: Mapped[list] = mapped_column(JSONType, default=list)
    availability: Mapped[str] = mapped_column(String(24), default='available')
    excluded_count: Mapped[int] = mapped_column(default=0)
    # Full provider response at fetched_at, not a claim of current completeness.
