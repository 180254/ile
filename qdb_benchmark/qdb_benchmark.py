#!venv/bin/python3
import os
import statistics
import time

import psycopg.rows
import psycopg.sql

# usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb" venv/bin/python3 qdb_count_rows.py

dsn = os.environ.get("QDB_DSN", "postgresql://admin:quest@localhost:8812/qdbb")

max_reps = 10
max_time_seconds = 10
sleep_time_seconds = 0.1

queries = ["tables()"]
queries2 = [bytes(q, "utf-8") for q in queries]

with (
    psycopg.Connection.connect(conninfo=dsn, connect_timeout=3) as conn,
    conn.cursor(row_factory=psycopg.rows.dict_row) as cursor,
):
    width = 10
    print(
        f"{'idx':>{width}} | "
        f"{'reps':>{width}} | "
        f"{'duration':>{width}} | "
        f"{'min':>{width}} | "
        f"{'max':>{width}} | "
        f"{'mean':>{width}} | "
        f"{'median':>{width}} | "
        f"{'stdev':>{width}} | "
        f"{'q1':>{width}} | "
        f"{'q2':>{width}} | "
        f"{'q3':>{width}} "
    )

    for i, query in enumerate(queries2):
        reps = 0
        start_time = time.time()
        results = []

        while reps < max_reps and time.time() - start_time < max_time_seconds:
            query_start_time = time.time()

            cursor.execute(query)
            cursor.fetchone()

            results.append(time.time() - query_start_time)
            reps += 1
            time.sleep(sleep_time_seconds)

        duration = time.time() - start_time
        min_time = min(results)
        max_time = max(results)
        mean_time = statistics.mean(results)
        median_time = statistics.median(results)
        stdev_time = statistics.stdev(results)
        quartiles = statistics.quantiles(results, n=4)

        print(
            f"{i:>{width}} | "
            f"{reps:>{width}} | "
            f"{duration:>{width}.3f} | "
            f"{min_time:>{width}.3f} | "
            f"{max_time:>{width}.3f} | "
            f"{mean_time:>{width}.3f} | "
            f"{median_time:>{width}.3f} | "
            f"{stdev_time:>{width}.3f} | "
            f"{quartiles[0]:>{width}.3f} | "
            f"{quartiles[1]:>{width}.3f} | "
            f"{quartiles[2]:>{width}.3f}"
        )
