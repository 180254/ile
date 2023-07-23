#!venv/bin/python3
import os
import sys

import psycopg2.extras

# https://github.com/questdb/questdb/issues/3531
# usage: QDB_DSN="postgresql://useradmin:quest@localhost:8812/qdb" venv/bin/python3 qdb_wal_switch.py true

dsn = os.environ.get("QDB_DSN", "postgresql://useradmin:quest@localhost:8812/qdb")

wal = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("QDB_WAL", "True")
wal = wal.lower() in ("true", "1", "t", "y", "yes")

print("wal: " + str(wal))
print("-" * 40)

conn = psycopg2.connect(dsn=dsn, connect_timeout=3)

cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
cursor.execute("tables()")
tables = cursor.fetchall()

for table in tables:
    name = table["name"]
    print(name)

    cursor = conn.cursor()
    cursor.execute("alter table " + name + " set type " + ("" if wal else "bypass") + " WAL;")

print("-" * 40)
print("done, restart the database")
