#!venv/bin/python3
import os

import psycopg.rows
import psycopg.sql

# usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb" venv/bin/python3 qdb_count_rows.py

dsn = os.environ.get("QDB_DSN", "postgresql://admin:quest@localhost:8812/qdb")

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
        print(f"{name}: {count}")

    print("-" * 40)

    prefixes = ["shelly_ht_", "shelly_plugs_", "telegraf_"]
    for prefix in prefixes:
        count = sum([results[name] for name in results if name.startswith(prefix)])
        print(f"{prefix}: {count} ({count / total:.2%})")

    print("-" * 40)

    print(f"Total: {total}")
