#!venv/bin/python3
import os

import psycopg2.extras

# usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb venv/bin/python3 qdb_count_rows.py

dsn = os.environ.get("QDB_DSN", "postgresql://admin:quest@localhost:8812/qdb")

conn = psycopg2.connect(dsn=dsn, connect_timeout=3)

cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
cursor.execute("tables()")
tables = cursor.fetchall()

total = 0
for table in tables:
    name = table["name"]

    cursor2 = conn.cursor()
    cursor2.execute("select count(*) from %s", (name,))
    cursor2fetchone = cursor2.fetchone()

    count = cursor2fetchone[0] if cursor2fetchone is not None else 0
    total += count

    print(f"{name}: {count}")

print("-" * 40)
print(f"Total: {total}")
