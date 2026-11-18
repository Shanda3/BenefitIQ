"""Database engine, schema bootstrap, and sessions."""

from __future__ import annotations

import time
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from benefitiq.config import database_url
from benefitiq.models import Base

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,
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
    _ensure_search_indexes(engine)


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
