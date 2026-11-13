"""Database connection and environment configuration."""

import os
from pathlib import Path
from typing import Optional


def get_database_url(custom_url: Optional[str] = None) -> str:
    """Resolve database connection URL from environment variables or parameter.

    Prioritizes explicit `DATABASE_URL` if present, otherwise constructs from
    individual `POSTGRES_*` environment variables. When running in Vercel or
    demonstration mode without an external database, seamlessly falls back
    to the bundled read-only SQLite demonstration snapshot.

    Returns:
        SQLAlchemy-compatible database connection URL string.
    """
    if custom_url:
        return _normalize_db_url(custom_url)

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return _normalize_db_url(env_url)

    # In Vercel serverless or demo mode without explicit external DATABASE_URL,
    # fall back to the bundled SQLite demonstration snapshot
    if os.getenv("VERCEL") == "1" or os.getenv("DEMO_MODE", "").lower() in ("true", "1", "yes"):
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        snapshot = project_root / "data" / "demo_snapshot.db"
        if snapshot.exists():
            return f"sqlite:///file:{snapshot.resolve().as_posix()}?mode=ro&uri=true"

    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "cicd_platform")

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _normalize_db_url(url: str) -> str:
    """Normalize legacy postgres:// prefix to postgresql+psycopg2:// and resolve relative SQLite paths."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)

    if url.startswith("sqlite:///"):
        raw_target = url[10:]
        if raw_target.startswith("file:"):
            # Already URI formatted
            return url

        query_part = ""
        clean_path = raw_target
        if "?" in raw_target:
            clean_path, query_part = raw_target.split("?", 1)
            query_part = f"?{query_part}"

        path_obj = Path(clean_path)
        if not path_obj.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            resolved = (project_root / path_obj).resolve()
            posix_path = resolved.as_posix()
            if not query_part:
                return f"sqlite:///file:{posix_path}?mode=ro&uri=true"
            if "uri=true" not in query_part.lower():
                separator = "&" if "?" in query_part else "?"
                return f"sqlite:///file:{posix_path}{query_part}{separator}uri=true"
            return f"sqlite:///file:{posix_path}{query_part}"

    return url
