"""Database Engine and Session Management Module.

Provides SQLAlchemy 2.x declarative Base, SessionLocal factory, and dependency injection helpers.
"""

import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.core.config import settings

# SQLite requires check_same_thread=False when used across threads in FastAPI
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for yielding database session with automatic cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sqlite_add_missing_columns(conn, table: str, columns) -> None:
    """Add any missing columns to a SQLite table, one statement at a time.

    Unlike the previous blanket ``except Exception: pass``, a genuine failure is logged and
    re-raised: a silent migration failure surfaces much later as a confusing runtime error.
    """
    existing = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()]
    if not existing:
        return
    for column_name, ddl in columns:
        if column_name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    """Initialize all database tables and add any missing columns safely."""
    # Import all models to ensure they are registered with Base.metadata
    from app.models import kri, financial, audit  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Safe SQLite column migration for new optional columns
    if settings.database_url.startswith("sqlite"):
        try:
            with engine.connect() as conn:
                _sqlite_add_missing_columns(
                    conn,
                    "kris",
                    [
                        ("owner", "owner VARCHAR(100) DEFAULT 'Internal Audit'"),
                        ("note", "note TEXT"),
                    ],
                )
                _sqlite_add_missing_columns(
                    conn,
                    "kri_thresholds",
                    [
                        ("key", "key VARCHAR(100)"),
                        ("unit", "unit VARCHAR(50)"),
                    ],
                )
                _sqlite_add_missing_columns(
                    conn,
                    "kri_schedules",
                    [("custom_date", "custom_date VARCHAR(50)")],
                )
                _sqlite_add_missing_columns(
                    conn,
                    "data_sources",
                    [
                        ("is_queryable", "is_queryable BOOLEAN DEFAULT 0"),
                        ("availability_note", "availability_note TEXT"),
                    ],
                )
                _sqlite_add_missing_columns(
                    conn,
                    "kri_test_steps",
                    [
                        ("operation", "operation VARCHAR(64)"),
                        ("parameters", "parameters JSON"),
                        ("data_source_id", "data_source_id INTEGER REFERENCES data_sources(id)"),
                        ("entity_code", "entity_code VARCHAR(64)"),
                        ("expected_output", "expected_output TEXT"),
                        ("content_hash", "content_hash VARCHAR(64)"),
                    ],
                )
                _sqlite_add_missing_columns(
                    conn,
                    "execution_plans",
                    [
                        ("source", "source VARCHAR(16) DEFAULT 'RULES'"),
                        ("plan_hash", "plan_hash VARCHAR(64)"),
                        ("steps_hash", "steps_hash VARCHAR(64)"),
                        ("interpreter_meta", "interpreter_meta JSON"),
                        ("created_by", "created_by VARCHAR(100)"),
                    ],
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover - surfaced loudly, never swallowed
            logging.getLogger(__name__).error("SQLite schema migration failed: %s", exc)
            raise
