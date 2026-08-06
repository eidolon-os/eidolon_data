#!/usr/bin/env python3
"""Measure the SQLite write profiles used by the Eidolon OS data split.

This is a local diagnostic, not a product SLA. It deliberately uses the
stdlib SQLite driver so the result isolates transaction/fsync behaviour from
HTTP, ORM, LLM, and media-pipeline latency.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sqlite3
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median


@dataclass(frozen=True)
class Measurement:
    name: str
    operations: int
    commits: int
    elapsed_s: float
    operations_per_s: float
    commit_p50_ms: float
    commit_p95_ms: float
    commit_max_ms: float


def _connect(path: Path, *, synchronous: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA synchronous={synchronous}")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA wal_autocheckpoint=1000")
    return connection


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)]


def _measurement(
    name: str,
    *,
    operations: int,
    commit_latencies_s: list[float],
    elapsed_s: float,
) -> Measurement:
    latencies_ms = [value * 1_000 for value in commit_latencies_s]
    return Measurement(
        name=name,
        operations=operations,
        commits=len(commit_latencies_s),
        elapsed_s=round(elapsed_s, 6),
        operations_per_s=round(operations / elapsed_s, 2),
        commit_p50_ms=round(median(latencies_ms), 3),
        commit_p95_ms=round(_percentile(latencies_ms, 0.95), 3),
        commit_max_ms=round(max(latencies_ms), 3),
    )


def benchmark_system_data(path: Path, iterations: int) -> Measurement:
    connection = _connect(path, synchronous="FULL")
    connection.executescript(
        """
        CREATE TABLE owners (owner_id TEXT PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE audit_outbox (
            outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            owner_id TEXT NOT NULL,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )
    latencies: list[float] = []
    started = time.perf_counter()
    for number in range(iterations):
        commit_started = time.perf_counter()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO owners VALUES (?, ?)",
            (f"owner-{number}", f"Owner {number}"),
        )
        connection.execute(
            "INSERT INTO audit_outbox(event_id, owner_id, action, payload_json) "
            "VALUES (?, ?, 'owner.created', '{}')",
            (f"system-event-{number}", f"owner-{number}"),
        )
        connection.commit()
        latencies.append(time.perf_counter() - commit_started)
    elapsed = time.perf_counter() - started
    connection.close()
    return _measurement(
        "system_data_full_one_mutation_plus_outbox",
        operations=iterations,
        commit_latencies_s=latencies,
        elapsed_s=elapsed,
    )


def benchmark_agent_runtime(path: Path, iterations: int) -> Measurement:
    connection = _connect(path, synchronous="FULL")
    connection.executescript(
        """
        CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL);
        CREATE TABLE turns (
            turn_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            seq INTEGER NOT NULL,
            trace_json TEXT NOT NULL
        );
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL REFERENCES turns(turn_id),
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL
        );
        INSERT INTO conversations VALUES ('conversation-benchmark', 'owner-benchmark');
        """
    )
    payload = json.dumps({"spans": [{"name": "llm", "duration_ms": 800}]})
    latencies: list[float] = []
    started = time.perf_counter()
    for number in range(iterations):
        turn_id = f"turn-{number}"
        commit_started = time.perf_counter()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO turns VALUES (?, 'conversation-benchmark', ?, ?)",
            (turn_id, number + 1, payload),
        )
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            (
                (f"message-{number}-user", turn_id, 1, "user", "hello" * 40),
                (f"message-{number}-assistant", turn_id, 2, "assistant", "reply" * 80),
            ),
        )
        connection.commit()
        latencies.append(time.perf_counter() - commit_started)
    elapsed = time.perf_counter() - started
    connection.close()
    return _measurement(
        "agent_full_one_turn_plus_two_messages",
        operations=iterations,
        commit_latencies_s=latencies,
        elapsed_s=elapsed,
    )


def benchmark_audit_index(path: Path, events: int, batch_size: int) -> Measurement:
    connection = _connect(path, synchronous="NORMAL")
    connection.execute(
        "CREATE TABLE audit_events ("
        "ingest_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, "
        "producer TEXT NOT NULL, owner_id TEXT, action TEXT NOT NULL, payload_json TEXT NOT NULL)"
    )
    latencies: list[float] = []
    started = time.perf_counter()
    for batch_start in range(0, events, batch_size):
        rows = [
            (
                f"audit-event-{number}",
                "eidolon-agent",
                "owner-benchmark",
                "job.completed",
                "{}",
            )
            for number in range(batch_start, min(batch_start + batch_size, events))
        ]
        commit_started = time.perf_counter()
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "INSERT INTO audit_events(event_id, producer, owner_id, action, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
        latencies.append(time.perf_counter() - commit_started)
    elapsed = time.perf_counter() - started
    connection.close()
    return _measurement(
        f"audit_index_normal_batches_of_{batch_size}",
        operations=events,
        commit_latencies_s=latencies,
        elapsed_s=elapsed,
    )


def benchmark_shared_writer_contention(path: Path, iterations: int, writers: int) -> Measurement:
    seed = _connect(path, synchronous="FULL")
    seed.execute("CREATE TABLE writes (writer INTEGER NOT NULL, seq INTEGER NOT NULL)")
    seed.close()

    def write_partition(writer: int) -> list[float]:
        connection = _connect(path, synchronous="FULL")
        latencies: list[float] = []
        for number in range(iterations // writers):
            commit_started = time.perf_counter()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO writes VALUES (?, ?)", (writer, number))
            connection.commit()
            latencies.append(time.perf_counter() - commit_started)
        connection.close()
        return latencies

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=writers) as executor:
        partitions = list(executor.map(write_partition, range(writers)))
    elapsed = time.perf_counter() - started
    latencies = [latency for partition in partitions for latency in partition]
    return _measurement(
        f"shared_full_database_{writers}_writers",
        operations=len(latencies),
        commit_latencies_s=latencies,
        elapsed_s=elapsed,
    )


def _run_separate_authorities(
    root: Path,
    system_iterations: int,
    agent_iterations: int,
    audit_events: int,
    audit_batch_size: int,
) -> tuple[list[Measurement], float]:
    jobs: tuple[Callable[[], Measurement], ...] = (
        lambda: benchmark_system_data(root / "parallel-system.sqlite3", system_iterations),
        lambda: benchmark_agent_runtime(root / "parallel-agent.sqlite3", agent_iterations),
        lambda: benchmark_audit_index(
            root / "parallel-audit.sqlite3", audit_events, audit_batch_size
        ),
    )
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as executor:
        measurements = list(executor.map(lambda job: job(), jobs))
    return measurements, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-iterations", type=int, default=300)
    parser.add_argument("--agent-iterations", type=int, default=1_000)
    parser.add_argument("--audit-events", type=int, default=10_000)
    parser.add_argument("--audit-batch-size", type=int, default=100)
    parser.add_argument("--shared-writers", type=int, default=4)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="eidolon-sqlite-profile-") as temp:
        root = Path(temp)
        sequential = [
            benchmark_system_data(root / "system.sqlite3", args.system_iterations),
            benchmark_agent_runtime(root / "agent.sqlite3", args.agent_iterations),
            benchmark_audit_index(
                root / "audit.sqlite3", args.audit_events, args.audit_batch_size
            ),
            benchmark_shared_writer_contention(
                root / "shared.sqlite3", args.agent_iterations, args.shared_writers
            ),
        ]
        parallel, parallel_elapsed = _run_separate_authorities(
            root,
            args.system_iterations,
            args.agent_iterations,
            args.audit_events,
            args.audit_batch_size,
        )

    report = {
        "disclaimer": "local diagnostic only; not a product or target-hardware SLA",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        },
        "sequential": [asdict(item) for item in sequential],
        "separate_authorities_parallel": {
            "wall_elapsed_s": round(parallel_elapsed, 6),
            "measurements": [asdict(item) for item in parallel],
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
