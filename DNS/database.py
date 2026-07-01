"""SQLite local database for DNS AI Monitoring Platform.

Stores DNS query results, agent analysis results, and remediation tickets
persistently so data survives restarts and enriches analysis with history.
"""
import json
import logging
import sqlite3
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "dns_monitor.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dns_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server TEXT NOT NULL,
            domain TEXT NOT NULL,
            query_type TEXT NOT NULL,
            response_time_ms REAL NOT NULL,
            status TEXT NOT NULL,
            answers TEXT,
            error TEXT,
            timestamp REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_dns_queries_ts ON dns_queries(timestamp);
        CREATE INDEX IF NOT EXISTS idx_dns_queries_server ON dns_queries(server);
        CREATE INDEX IF NOT EXISTS idx_dns_queries_status ON dns_queries(status);

        CREATE TABLE IF NOT EXISTS agent_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            result_json TEXT NOT NULL,
            summary TEXT,
            alert_count INTEGER DEFAULT 0,
            remediation_count INTEGER DEFAULT 0,
            timestamp REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_agent_results_ts ON agent_results(timestamp);
        CREATE INDEX IF NOT EXISTS idx_agent_results_agent ON agent_results(agent_name);

        CREATE TABLE IF NOT EXISTS remediation_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL UNIQUE,
            source_agent TEXT,
            action TEXT,
            priority TEXT,
            target TEXT,
            itsm_category TEXT,
            auto_remediate INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            snow_number TEXT,
            snow_sys_id TEXT,
            created_at REAL NOT NULL,
            completed_at REAL DEFAULT 0,
            result TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_rem_tickets_status ON remediation_tickets(status);

        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            narrative TEXT,
            root_cause TEXT,
            health_assessment TEXT,
            confidence INTEGER,
            result_json TEXT,
            timestamp REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orch_runs_ts ON orchestrator_runs(timestamp);
    """)
    conn.commit()
    logger.info(f"Database initialized: {DB_PATH}")


# ---------------------------------------------------------------------------
# DNS Queries
# ---------------------------------------------------------------------------

def insert_dns_query(server: str, domain: str, query_type: str,
                     response_time_ms: float, status: str,
                     answers: list = None, error: str = None,
                     timestamp: float = None):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO dns_queries (server, domain, query_type, response_time_ms, status, answers, error, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (server, domain, query_type, response_time_ms, status,
         json.dumps(answers or []), error, timestamp or time.time())
    )
    conn.commit()


def insert_dns_queries_batch(queries: list[dict]):
    """Batch insert DNS query results."""
    conn = _get_conn()
    conn.executemany(
        "INSERT INTO dns_queries (server, domain, query_type, response_time_ms, status, answers, error, timestamp) "
        "VALUES (:server, :domain, :query_type, :response_time_ms, :status, :answers, :error, :timestamp)",
        queries
    )
    conn.commit()


def get_dns_queries(limit: int = 200, since: float = 0) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM dns_queries WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
        (since, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_dns_summary(hours: float = 1.0) -> dict:
    """Get summary stats for DNS queries in the last N hours."""
    conn = _get_conn()
    since = time.time() - (hours * 3600)
    rows = conn.execute(
        "SELECT server, domain, query_type, response_time_ms, status FROM dns_queries WHERE timestamp > ?",
        (since,)
    ).fetchall()
    if not rows:
        return {"total_queries": 0, "period_hours": hours}

    total = len(rows)
    success = [r for r in rows if r["status"] == "success"]
    avg_ms = sum(r["response_time_ms"] for r in success) / max(len(success), 1)

    status_counts = {}
    server_times = {}
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        s = r["server"]
        if s not in server_times:
            server_times[s] = []
        server_times[s].append(r["response_time_ms"])

    server_avg = {s: round(sum(t) / len(t), 2) for s, t in server_times.items()}

    return {
        "total_queries": total,
        "period_hours": hours,
        "avg_response_ms": round(avg_ms, 2),
        "success_rate": round(len(success) / total * 100, 1),
        "status_counts": status_counts,
        "server_avg_ms": server_avg,
    }


def get_dns_trend(hours: float = 24, bucket_minutes: int = 30) -> list[dict]:
    """Get time-bucketed DNS stats for trend charts."""
    conn = _get_conn()
    since = time.time() - (hours * 3600)
    bucket_secs = bucket_minutes * 60
    rows = conn.execute(
        "SELECT response_time_ms, status, timestamp FROM dns_queries WHERE timestamp > ? ORDER BY timestamp",
        (since,)
    ).fetchall()
    if not rows:
        return []

    buckets = {}
    for r in rows:
        bucket_key = int(r["timestamp"] // bucket_secs) * bucket_secs
        if bucket_key not in buckets:
            buckets[bucket_key] = {"total": 0, "success": 0, "total_ms": 0}
        buckets[bucket_key]["total"] += 1
        if r["status"] == "success":
            buckets[bucket_key]["success"] += 1
            buckets[bucket_key]["total_ms"] += r["response_time_ms"]

    return [
        {
            "timestamp": ts,
            "total": b["total"],
            "success_rate": round(b["success"] / b["total"] * 100, 1) if b["total"] else 0,
            "avg_ms": round(b["total_ms"] / max(b["success"], 1), 2),
        }
        for ts, b in sorted(buckets.items())
    ]


def get_total_query_count() -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM dns_queries").fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Agent Results
# ---------------------------------------------------------------------------

def insert_agent_result(agent_name: str, result: dict):
    conn = _get_conn()
    summary = result.get("summary", "")
    alerts = result.get("alerts", [])
    remediation = result.get("remediation_actions", [])
    conn.execute(
        "INSERT INTO agent_results (agent_name, result_json, summary, alert_count, remediation_count, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (agent_name, json.dumps(result), summary, len(alerts), len(remediation), time.time())
    )
    conn.commit()


def get_agent_history(agent_name: str, limit: int = 20) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM agent_results WHERE agent_name = ? ORDER BY timestamp DESC LIMIT ?",
        (agent_name, limit)
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["result_json"] = json.loads(d["result_json"])
        results.append(d)
    return results


def get_latest_agent_results() -> dict:
    """Get the most recent result for each agent."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT agent_name, result_json, timestamp FROM agent_results "
        "WHERE id IN (SELECT MAX(id) FROM agent_results GROUP BY agent_name)"
    ).fetchall()
    results = {}
    for r in rows:
        results[r["agent_name"]] = json.loads(r["result_json"])
    return results


# ---------------------------------------------------------------------------
# Orchestrator Runs
# ---------------------------------------------------------------------------

def insert_orchestrator_run(result: dict):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO orchestrator_runs (narrative, root_cause, health_assessment, confidence, result_json, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            result.get("narrative", ""),
            result.get("root_cause_narrative", ""),
            result.get("health_assessment", ""),
            result.get("confidence", 0),
            json.dumps(result),
            time.time(),
        )
    )
    conn.commit()


def get_orchestrator_history(limit: int = 10) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM orchestrator_runs ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["result_json"] = json.loads(d["result_json"])
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Remediation Tickets
# ---------------------------------------------------------------------------

def upsert_remediation_ticket(ticket: dict):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO remediation_tickets "
        "(ticket_id, source_agent, action, priority, target, itsm_category, auto_remediate, status, snow_number, snow_sys_id, created_at, completed_at, result) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ticket_id) DO UPDATE SET "
        "status=excluded.status, snow_number=excluded.snow_number, snow_sys_id=excluded.snow_sys_id, "
        "completed_at=excluded.completed_at, result=excluded.result",
        (
            ticket["ticket_id"], ticket.get("source_agent"), ticket.get("action"),
            ticket.get("priority"), ticket.get("target"), ticket.get("itsm_category"),
            1 if ticket.get("auto_remediate") else 0, ticket.get("status", "pending"),
            ticket.get("snow_number"), ticket.get("snow_sys_id"),
            ticket.get("created_at", time.time()), ticket.get("completed_at", 0),
            ticket.get("result"),
        )
    )
    conn.commit()


def has_recent_open_ticket(action: str, target: str, hours: float = 24.0) -> bool:
    """Check if a recent open (non-completed/failed) ticket exists for the same action+target."""
    conn = _get_conn()
    since = time.time() - (hours * 3600)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM remediation_tickets "
        "WHERE action = ? AND target = ? AND created_at > ? AND status NOT IN ('completed', 'failed', 'skipped')",
        (action, target, since)
    ).fetchone()
    return row["cnt"] > 0 if row else False


def get_remediation_tickets(limit: int = 100) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM remediation_tickets ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_old_data(days: int = 30):
    """Remove data older than N days."""
    conn = _get_conn()
    cutoff = time.time() - (days * 86400)
    conn.execute("DELETE FROM dns_queries WHERE timestamp < ?", (cutoff,))
    conn.execute("DELETE FROM agent_results WHERE timestamp < ?", (cutoff,))
    conn.execute("DELETE FROM orchestrator_runs WHERE timestamp < ?", (cutoff,))
    conn.commit()
    logger.info(f"Cleaned up data older than {days} days")
