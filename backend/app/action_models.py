"""Synthetic candidate model. Not registered with the deployed Base."""
from datetime import datetime
from sqlalchemy import String,Text,DateTime,UniqueConstraint
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
class ActionBase(DeclarativeBase):pass
class ActionDecision(ActionBase):
    __tablename__='action_decisions_candidate'
    __table_args__=(UniqueConstraint('account_id','source_email_id','detector_version','evidence_fingerprint',name='uq_action_exact_evidence'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    account_id:Mapped[int]=mapped_column(index=True)
    source_email_id:Mapped[int]=mapped_column(index=True)
    detector_version:Mapped[str]=mapped_column(String(32))
    evidence_fingerprint:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(16))
    revision:Mapped[int]=mapped_column(default=1)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
