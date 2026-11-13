"""SQLAlchemy session and engine lifecycle management."""

from typing import Generator, Optional
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .config import get_database_url

Base = declarative_base()

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Obtain or initialize the global SQLAlchemy engine instance."""
    global _engine, _session_factory
    target_url = get_database_url(db_url)

    # Check if engine needs initialization or re-pointing
    if _engine is None or db_url is not None:
        connect_args = {}
        if target_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            if "mode=ro" in target_url.lower() or "uri=true" in target_url.lower():
                connect_args["uri"] = True

        _engine = create_engine(
            target_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    return _engine


def reset_engine() -> None:
    """Reset the cached engine and sessionmaker singleton."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory(db_url: Optional[str] = None) -> sessionmaker:
    """Obtain the sessionmaker factory for creating database sessions."""
    get_engine(db_url)
    assert _session_factory is not None
    return _session_factory


def init_db(engine: Optional[Engine] = None) -> None:
    """Create all database tables defined in the metadata.

    Gracefully skips table creation when running against a read-only SQLite snapshot.
    """
    target_engine = engine or get_engine()
    if "mode=ro" in str(getattr(target_engine, "url", "")).lower():
        return
    Base.metadata.create_all(bind=target_engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session with automatic cleanup."""
    factory = get_session_factory()
    db: Session = factory()
    try:
        yield db
    finally:
        db.close()
