"""Database engine and session management."""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


# Columns added to `emails` after the first Phase 1 schema. create_all never
# alters existing tables, so ensure_schema() upgrades them idempotently. The
# statements only add columns (nullable or defaulted): existing accounts,
# encrypted tokens, emails and OAuth state rows are preserved untouched.
_EMAIL_COLUMN_UPGRADES = {
    "is_sample": "BOOLEAN NOT NULL DEFAULT FALSE",
    "category": "VARCHAR(32) NOT NULL DEFAULT ''",
    "priority": "VARCHAR(16) NOT NULL DEFAULT ''",
    "classified_at": "TIMESTAMP NULL",
}


def ensure_schema(engine) -> None:
    """Idempotently add post-Phase-1 columns/indexes to an existing database."""
    if not inspect(engine).has_table("emails"):
        return
    existing = {c["name"] for c in inspect(engine).get_columns("emails")}
    with engine.begin() as conn:
        for name, ddl in _EMAIL_COLUMN_UPGRADES.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE emails ADD COLUMN {name} {ddl}"))
        for ix, col in (("ix_emails_category", "category"), ("ix_emails_priority", "priority")):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ix} ON emails ({col})"))


def make_engine(url: str | None = None):
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
