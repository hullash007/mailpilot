"""Explicit local-controls fixture schema. Importing never creates tables."""
from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ControlBase(DeclarativeBase):
    pass


class ControlClock(ControlBase):
    __tablename__ = 'candidate_control_clock'
    id: Mapped[int] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(default=0)


class SubjectFence(ControlBase):
    __tablename__ = 'candidate_subject_fences'
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    generation: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20))


class PendingLogin(ControlBase):
    __tablename__ = 'candidate_control_logins'
    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ControlOperation(ControlBase):
    __tablename__ = 'candidate_control_operations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255))
    session_generation: Mapped[int] = mapped_column()
    generation: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    # No mail, address, tokens, provider response, error detail or permanent-retention promise.
    local_result: Mapped[str] = mapped_column(String(20))
    remote_result: Mapped[str] = mapped_column(String(20), default='not_attempted')
