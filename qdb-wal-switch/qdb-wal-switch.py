import os

import psycopg2.extras

# https://github.com/questdb/questdb/issues/3531
# usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb" QDB_WAL=True venv/bin/python3 qdb-wal-switch.py

dsn = os.environ.get("QDB_DSN", "postgresql://admin:quest@localhost:8812/qdb")
wal = bool(os.environ.get("QDB_WAL", "True"))

conn = psycopg2.connect(dsn=dsn, connect_timeout=3)

cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
cursor.execute("tables()")
tables = cursor.fetchall()

for table in tables:
    name = table['name']
    print(name)

    cursor = conn.cursor()
    cursor.execute("alter table " + name + " set type " + ("" if wal else "bypass") + " WAL;")
