import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import boto3
import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


@lru_cache(maxsize=8)
def _secret(arn: str) -> str | dict[str, Any]:
    value = boto3.client("secretsmanager", region_name=get_settings().aws_region).get_secret_value(
        SecretId=arn
    )["SecretString"]
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else value
    except json.JSONDecodeError:
        return value


def _connect() -> psycopg.Connection[dict[str, Any]]:
    settings = get_settings()
    if settings.database_secret_arn:
        secret = _secret(settings.database_secret_arn)
        if not isinstance(secret, dict):
            raise RuntimeError("The configured database secret must be a JSON object")
        return psycopg.connect(
            host=settings.database_host,
            dbname=secret.get("dbname", "loan_engine"),
            user=secret["username"],
            password=secret["password"],
            port=5432,
            sslmode="require" if settings.app_env == "production" else "prefer",
            row_factory=dict_row,
            connect_timeout=5,
        )
    return psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5)


@contextmanager
def connection() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """Open a short-lived connection; each context commits atomically or rolls back."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def all_rows(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall()) if cur.description else []


def one_row(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() if cur.description else None


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def executemany(sql: str, rows: Sequence[Sequence[Any]]) -> None:
    if not rows:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, rows)
