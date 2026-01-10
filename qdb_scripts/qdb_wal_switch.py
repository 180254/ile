#!.venv/bin/python3
import os
import sys

import psycopg.rows
import psycopg.sql

# https://github.com/questdb/questdb/issues/3531
# usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb" .venv/bin/python3 qdb_wal_switch.py true

dsn = os.environ.get("QDB_DSN", "postgresql://useradmin:quest@localhost:8812/qdb")

wal_arg_index = 1
min_arg_len = 2

wal_cfg = sys.argv[wal_arg_index] if len(sys.argv) >= min_arg_len else os.environ.get("QDB_WAL", "true")

if wal_cfg not in ("true", "false", "check"):
    print("WAL must be 'true', 'false' or 'check'.")
    sys.exit(1)

print("wal: " + str(wal_cfg))
print("-" * 40)

with (
    psycopg.Connection.connect(conninfo=dsn, connect_timeout=3) as conn,
    conn.cursor(row_factory=psycopg.rows.dict_row) as cursor,
):
    if wal_cfg == "check":
        cursor.execute("wal_tables()")
        wal_tables = cursor.fetchall()

        for entry in wal_tables:
            entry_line = " | ".join(f"{k}={v}" for k, v in entry.items())
            print(entry_line)

    else:
        wal = wal_cfg == "true"

        cursor.execute("tables()")
        tables = cursor.fetchall()

        for table in tables:
            name = table["table_name"]
            print(name)

            stmt = psycopg.sql.SQL("alter table {tbl} set type {mode}WAL;").format(
                tbl=psycopg.sql.Identifier(name), mode=psycopg.sql.SQL("" if wal else "bypass ")
            )
            cursor.execute(stmt)

        print("-" * 40)
        print("done, restart the database to apply WAL mode changes")
