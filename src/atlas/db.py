from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(db_url: str) -> Engine:
    # SQLAlchemy uses the driver specified by the URL scheme.
    # Normalize `postgresql://` to `postgresql+psycopg://` (psycopg3) for convenience.
    url = db_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://")

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=900,  # Rotate connections every 15 min to avoid stale handles
    )


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)

