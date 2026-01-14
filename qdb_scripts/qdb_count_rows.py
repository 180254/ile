#!.venv/bin/python3
"""
QuestDB Row Counter - Counts rows in all tables and shows distribution.

Reports per-table counts, percentage of total, and aggregated counts by table prefix.
Usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb" .venv/bin/python3 qdb_count_rows.py
"""

import datetime
import os

import psycopg.abc
import psycopg.rows
import psycopg.sql
import psycopg.types.datetime

dsn = os.environ.get("QDB_DSN", "postgresql://admin:quest@localhost:8812/qdb")


class TimestampLoader2(psycopg.types.datetime.TimestampLoader):
    """
    Workaround for psycopg timestamp overflow error.

    QuestDB tables() function returns timestamps that may exceed Python's datetime range.
    This loader returns current time instead of failing on overflow.
    Error: psycopg.DataError: timestamp too large (after year 10K): '58006-03-31 01:53:20.000000'
    """

    def load(self, data: psycopg.abc.Buffer) -> datetime.datetime:  # noqa: ARG002
        return datetime.datetime.now(tz=datetime.UTC)


psycopg.adapters.register_loader("timestamp", TimestampLoader2)


with (
    psycopg.Connection.connect(conninfo=dsn, connect_timeout=3) as conn,
    conn.cursor(row_factory=psycopg.rows.dict_row) as cursor,
):
    cursor.execute("tables()")
    tables = cursor.fetchall()

    results = {}
    total = 0
    for table in tables:
        name = table["table_name"]

        cursor.execute(psycopg.sql.SQL("select count(*) from {}").format(psycopg.sql.Identifier(name)))
        row = cursor.fetchone()

        count = row["count()"] if row is not None else 0
        total += count

        results[name] = count

    for name, count in sorted(results.items(), key=lambda x: x[0]):
        percent = count / total if total > 0 else 0
        print(f"{name}: {count} ({percent:.2%})")

    print("-" * 40)

    # Aggregate counts by table name prefixes for summary view
    prefixes = ["shelly_plugs_", "shelly_ht_", "shelly_gen2_", "telegraf_", "telegraf_internal_"]
    for prefix in prefixes:
        count = sum(results[name] for name in results if name.startswith(prefix))
        percent = count / total if total > 0 else 0
        print(f"{prefix}: {count} ({percent:.2%})")

    print("-" * 40)

    print(f"Total: {total}")
