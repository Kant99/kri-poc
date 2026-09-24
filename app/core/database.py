"""Database Engine and Session Management Module.

Provides SQLAlchemy 2.x declarative Base, SessionLocal factory, and dependency injection helpers.
"""

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


def init_db() -> None:
    """Initialize all database tables and add any missing columns safely."""
    # Import all models to ensure they are registered with Base.metadata
    from app.models import kri, financial, audit  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Safe SQLite column migration for new optional columns
    if settings.database_url.startswith("sqlite"):
        try:
            with engine.connect() as conn:
                # Check kris table columns
                k_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(kris)").fetchall()]
                if k_cols:
                    if "owner" not in k_cols:
                        conn.exec_driver_sql("ALTER TABLE kris ADD COLUMN owner VARCHAR(100) DEFAULT 'Internal Audit'")
                    if "note" not in k_cols:
                        conn.exec_driver_sql("ALTER TABLE kris ADD COLUMN note TEXT")

                # Check kri_thresholds table columns
                t_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(kri_thresholds)").fetchall()]
                if t_cols:
                    if "key" not in t_cols:
                        conn.exec_driver_sql("ALTER TABLE kri_thresholds ADD COLUMN key VARCHAR(100)")
                    if "unit" not in t_cols:
                        conn.exec_driver_sql("ALTER TABLE kri_thresholds ADD COLUMN unit VARCHAR(50)")

                # Check kri_schedules table columns
                s_cols = [c[1] for c in conn.exec_driver_sql("PRAGMA table_info(kri_schedules)").fetchall()]
                if s_cols:
                    if "custom_date" not in s_cols:
                        conn.exec_driver_sql("ALTER TABLE kri_schedules ADD COLUMN custom_date VARCHAR(50)")
                conn.commit()
        except Exception:
            pass
