#!.venv/bin/python3
"""
QuestDB Benchmark - Measures query execution time statistics.

Runs queries repeatedly and reports min, max, mean, median, stdev, and quartiles.
Configure the 'queries' list below to test different SQL statements.

Usage: QDB_DSN="postgresql://admin:quest@localhost:8812/qdb" .venv/bin/python3 qdb_benchmark.py
"""

import os
import statistics
import time

import psycopg.rows
import psycopg.sql

dsn = os.environ.get("QDB_DSN", "postgresql://admin:quest@localhost:8812/qdb")

# Benchmark configuration
max_reps_per_query = 10
max_time_per_query_seconds = 20

statement_timeout_seconds = 120
sleep_time_seconds = 0.1

# List of queries to benchmark - modify this list to test different statements
queries = ["tables()"]
queries2 = [bytes(q, "utf-8") for q in queries]

with (
    psycopg.Connection.connect(
        conninfo=dsn,
        connect_timeout=3,
        options=f"-c statement_timeout={statement_timeout_seconds * 1000}",
    ) as conn,
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
        results: list[float] = []

        while reps < max_reps_per_query and time.time() - start_time < max_time_per_query_seconds:
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
        stdev_time = statistics.stdev(results) if len(results) > 1 else 0.0
        quartiles_n = 4
        quartiles = statistics.quantiles(results, n=quartiles_n) if len(results) > quartiles_n - 1 else [0.0, 0.0, 0.0]

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
