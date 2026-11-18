"""Database engine and session management."""
from sqlalchemy import create_engine, inspect, text
from threading import Lock
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
    # Phase 3: LLM suggestions live apart from the authoritative rule columns.
    "ai_category": "VARCHAR(32) NOT NULL DEFAULT ''",
    "ai_priority": "VARCHAR(16) NOT NULL DEFAULT ''",
    "ai_classified_at": "TIMESTAMP NULL",
    "classification_source": "VARCHAR(16) NOT NULL DEFAULT 'rules'",
    "ai_status": "VARCHAR(16) NOT NULL DEFAULT 'none'",
    "ai_attempts": "INTEGER NOT NULL DEFAULT 0",
    "ai_attempted_at": "TIMESTAMP NULL",
    "ai_next_attempt_at": "TIMESTAMP NULL",
    "ai_model": "VARCHAR(128) NOT NULL DEFAULT ''",
    "ai_prompt_version": "VARCHAR(32) NOT NULL DEFAULT ''",
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
        for ix, col in (("ix_emails_category", "category"), ("ix_emails_priority", "priority"), ("ix_emails_ai_status", "ai_status")):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ix} ON emails ({col})"))


def make_engine(url: str | None = None):
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


_default_lock = Lock()
_default_pair = None


def _defaults():
    """Publish one engine/sessionmaker pair atomically, never a partial pair."""
    global _default_pair
    with _default_lock:
        if _default_pair is None:
            engine = make_engine()
            sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            _default_pair = (engine, sessions)
        return _default_pair


def default_engine():
    """Resolve legacy settings only when the legacy database is actually used."""
    return _defaults()[0]


def default_sessions():
    return _defaults()[1]


def __getattr__(name):
    # Preserve explicit legacy imports without eager setup for Base/get_db users.
    if name == 'engine':
        return default_engine()
    if name == 'SessionLocal':
        return default_sessions()
    raise AttributeError(name)


def get_db():
    db = default_sessions()()
    try:
        yield db
    finally:
        db.close()
