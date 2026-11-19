"""Database engine, schema bootstrap, and sessions."""

from __future__ import annotations

import time
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from benefitiq.config import database_url
from benefitiq.constants import EMBEDDING_DIM
from benefitiq.models import Base

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        from benefitiq.config import pilot_mode

        pilot = pilot_mode()
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,
            pool_size=2 if pilot else 5,
            max_overflow=0 if pilot else 10,
            connect_args={"connect_timeout": 5},
        )
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def reset_engine() -> None:
    """Drop cached connections. Tests call this after changing DATABASE_URL."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope() -> Session:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(*, drop: bool = False) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    if drop:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _ensure_source_columns(engine)
    _ensure_embedding_width(engine)
    _ensure_search_indexes(engine)


def _ensure_source_columns(engine) -> None:
    """Add version columns on a database created before policy versions existed."""
    statements = [
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS status varchar(32) NOT NULL DEFAULT 'current'",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS check_state varchar(32) NOT NULL DEFAULT 'ok'",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS document_number varchar(80)",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS segment varchar(400)",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS topics varchar(400)",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS date_basis varchar(32)",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS stated_on date",
        "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_checked_at timestamptz",
        "ALTER TABLE sources ALTER COLUMN source_id TYPE varchar(200)",
        "ALTER TABLE sources ALTER COLUMN family_id TYPE varchar(200)",
        "ALTER TABLE chunks ALTER COLUMN chunk_id TYPE varchar(320)",
        "ALTER TABLE chunks ALTER COLUMN source_id TYPE varchar(200)",
        "ALTER TABLE chunks ALTER COLUMN section_anchor TYPE varchar(200)",
        "ALTER TABLE code_rows ALTER COLUMN source_id TYPE varchar(200)",
        "ALTER TABLE code_rows ALTER COLUMN chunk_id TYPE varchar(320)",
    ]
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.sources')")).scalar()
        if not exists:
            return
        for statement in statements:
            conn.execute(text(statement))


def _ensure_embedding_width(engine) -> None:
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.chunks')")).scalar()
        if not exists:
            return
        kind = conn.execute(
            text(
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = 'chunks'
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """
            )
        ).scalar()
    expected = f"vector({EMBEDDING_DIM})"
    if kind and kind != expected:
        raise RuntimeError(
            f"chunks.embedding is {kind}; this build expects {expected}. "
            "Drop the local database and run init-db again."
        )


def _ensure_search_indexes(engine) -> None:
    statements = [
        """
        ALTER TABLE chunks
        ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED
        """,
        "CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING GIN (tsv)",
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_embedding
        ON chunks USING hnsw (embedding vector_cosine_ops)
        """,
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def wait_for_db(*, attempts: int = 30, delay_seconds: float = 1.0) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - retry until the server accepts connections
            last_error = exc
            time.sleep(delay_seconds)
    raise RuntimeError("database did not become ready") from last_error
