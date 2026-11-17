"""Phase 1 persistence models.

Every Gmail message is stored exactly once: `Email.gmail_message_id` carries a
uniqueness constraint and ingestion skips already-seen IDs.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_address: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    google_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # OAuth token payload (access/refresh token, expiry). Encrypt at rest
    # before any write scopes are introduced.
    credentials_json: Mapped[dict] = mapped_column(JSONType, default=dict)
    history_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    threads: Mapped[list["Thread"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("account_id", "gmail_thread_id", name="uq_thread_account_gmailid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("gmail_accounts.id"), index=True)
    gmail_thread_id: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(998), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped[GmailAccount] = relationship(back_populates="threads")
    emails: Mapped[list["Email"]] = relationship(back_populates="thread", cascade="all, delete-orphan")


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id"), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(998), default="")
    sender: Mapped[str] = mapped_column(String(320), default="", index=True)
    recipients: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    headers: Mapped[dict] = mapped_column(JSONType, default=dict)
    label_ids: Mapped[list] = mapped_column(JSONType, default=list)
    internal_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    thread: Mapped[Thread] = relationship(back_populates="emails")
