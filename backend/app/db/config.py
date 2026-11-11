"""Database connection and environment configuration."""

import os
from typing import Optional


def get_database_url(custom_url: Optional[str] = None) -> str:
    """Resolve database connection URL from environment variables or parameter.

    Prioritizes explicit `DATABASE_URL` if present, otherwise constructs from
    individual `POSTGRES_*` environment variables.

    Returns:
        SQLAlchemy-compatible database connection URL string.
    """
    if custom_url:
        return _normalize_db_url(custom_url)

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return _normalize_db_url(env_url)

    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "cicd_platform")

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _normalize_db_url(url: str) -> str:
    """Normalize legacy postgres:// prefix to postgresql+psycopg2://."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url
