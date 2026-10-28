"""Database connection and schema."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

SCHEMA = Path(__file__).with_name("schema.sql")


def dsn() -> str:
    url = os.environ.get("MEALCART_DATABASE_URL")
    if not url:
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("MEALCART_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        raise RuntimeError("MEALCART_DATABASE_URL is not set (see .env.example)")
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn())


def init_schema(conn: psycopg.Connection) -> None:
    """Idempotent: every statement in schema.sql uses IF NOT EXISTS."""
    conn.execute(SCHEMA.read_text())
    conn.commit()
