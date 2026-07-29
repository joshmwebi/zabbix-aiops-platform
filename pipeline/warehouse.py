"""Warehouse connection and loading, abstracted over the backend.

The pipeline must not care whether rows land in PostgreSQL or Snowflake.
Choosing between them is a config decision (`WAREHOUSE_TYPE`), not a code
change — the same reason ZABBIX_API_URL lives in .env rather than in source.
See ADR-007.

Both adapters expose the same three operations: ensure the bronze tables
exist, bulk-insert rows, and read/write the extraction watermark.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Sequence

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
#
# Bronze is deliberately close to what the API returned. Cleaning, typing,
# and reshaping happen in dbt (silver), so a transformation bug never means
# re-extracting from Zabbix. `_loaded_at` is the only column we add.
#
# Types are written in a dialect both backends accept.

BRONZE_TABLES = {
    # One row per item per hour: Zabbix's own pre-aggregation.
    "bronze_trends": """
        CREATE TABLE IF NOT EXISTS bronze_trends (
            itemid       BIGINT      NOT NULL,
            clock        BIGINT      NOT NULL,
            num          INTEGER,
            value_min    DOUBLE PRECISION,
            value_avg    DOUBLE PRECISION,
            value_max    DOUBLE PRECISION,
            _loaded_at   TIMESTAMP   NOT NULL
        )
    """,
    # Item and host metadata, refreshed each run. Small, so it is replaced
    # wholesale rather than appended — the current shape of the fleet.
    "bronze_items": """
        CREATE TABLE IF NOT EXISTS bronze_items (
            itemid       BIGINT      NOT NULL,
            hostid       BIGINT      NOT NULL,
            host         VARCHAR(255),
            name         VARCHAR(1024),
            key_         VARCHAR(1024),
            units        VARCHAR(64),
            value_type   INTEGER,
            delay        VARCHAR(64),
            _loaded_at   TIMESTAMP   NOT NULL
        )
    """,
    # Extraction watermark: how far each table has been loaded, so a run
    # resumes rather than re-pulling everything.
    "pipeline_state": """
        CREATE TABLE IF NOT EXISTS pipeline_state (
            stream       VARCHAR(64) NOT NULL,
            watermark    BIGINT      NOT NULL,
            updated_at   TIMESTAMP   NOT NULL
        )
    """,
}


class Warehouse(ABC):
    """Minimal interface the loader depends on."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None: ...

    @abstractmethod
    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> int: ...

    @abstractmethod
    def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> tuple | None: ...

    def ensure_schema(self) -> None:
        for ddl in BRONZE_TABLES.values():
            self.execute(ddl)

    def get_watermark(self, stream: str, default: int) -> int:
        row = self.fetch_one(
            "SELECT watermark FROM pipeline_state WHERE stream = %s", (stream,)
        )
        return int(row[0]) if row else default

    def set_watermark(self, stream: str, value: int) -> None:
        # No upsert: portable across both backends without dialect-specific
        # ON CONFLICT / MERGE syntax, and the table has one row per stream.
        self.execute("DELETE FROM pipeline_state WHERE stream = %s", (stream,))
        self.execute(
            "INSERT INTO pipeline_state (stream, watermark, updated_at) "
            "VALUES (%s, %s, CURRENT_TIMESTAMP)",
            (stream, value),
        )

    def __enter__(self):
        self.connect()
        self.ensure_schema()
        return self

    def __exit__(self, *exc):
        self.close()


class PostgresWarehouse(Warehouse):
    def __init__(self):
        self.conn = None

    def connect(self) -> None:
        import psycopg2  # imported lazily so the other backend isn't required

        self.conn = psycopg2.connect(
            host=os.environ["PG_HOST"],
            port=int(os.environ.get("PG_PORT", "5432")),
            dbname=os.environ["PG_DATABASE"],
            user=os.environ["PG_USER"],
            password=os.environ["PG_PASSWORD"],
        )
        self.conn.autocommit = True

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def execute(self, sql: str, params=None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    def fetch_one(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        if not rows:
            return 0
        from psycopg2.extras import execute_values

        cols = ", ".join(columns)
        with self.conn.cursor() as cur:
            execute_values(
                cur, f"INSERT INTO {table} ({cols}) VALUES %s", rows, page_size=5000
            )
        return len(rows)


class SnowflakeWarehouse(Warehouse):
    def __init__(self):
        self.conn = None

    def connect(self) -> None:
        import snowflake.connector

        kwargs = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "database": os.environ["SNOWFLAKE_DATABASE"],
            "schema": os.environ.get("SNOWFLAKE_SCHEMA", "BRONZE"),
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE"),
            "role": os.environ.get("SNOWFLAKE_ROLE"),
        }
        # Siemens SSO is likely; externalbrowser avoids storing a password.
        # Set SNOWFLAKE_AUTHENTICATOR=externalbrowser for interactive runs,
        # or supply a key pair / password for unattended ones.
        auth = os.environ.get("SNOWFLAKE_AUTHENTICATOR")
        if auth:
            kwargs["authenticator"] = auth
        if os.environ.get("SNOWFLAKE_PASSWORD"):
            kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
            kwargs["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
            if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
                kwargs["private_key_file_pwd"] = os.environ[
                    "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"
                ]

        self.conn = snowflake.connector.connect(
            **{k: v for k, v in kwargs.items() if v is not None}
        )

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def execute(self, sql: str, params=None) -> None:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
        finally:
            cur.close()

    def fetch_one(self, sql: str, params=None):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchone()
        finally:
            cur.close()

    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        if not rows:
            return 0
        cols = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        cur = self.conn.cursor()
        try:
            # executemany batches into a single multi-row insert.
            cur.executemany(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", rows
            )
        finally:
            cur.close()
        return len(rows)


def get_warehouse() -> Warehouse:
    """Instantiate the backend named by WAREHOUSE_TYPE."""
    kind = os.environ.get("WAREHOUSE_TYPE", "postgres").lower()
    if kind == "postgres":
        return PostgresWarehouse()
    if kind == "snowflake":
        return SnowflakeWarehouse()
    raise ValueError(
        f"Unknown WAREHOUSE_TYPE {kind!r} — expected 'postgres' or 'snowflake'"
    )
