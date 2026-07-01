import dns.resolver
import json
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Optional
from config_loader import get_config
import database as db

logger = logging.getLogger(__name__)


@dataclass
class DNSQueryResult:
    server: str
    domain: str
    query_type: str
    response_time_ms: float
    status: str  # "success", "nxdomain", "timeout", "servfail", "error"
    answers: list = field(default_factory=list)
    error: Optional[str] = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class DNSCollector:
    def __init__(self):
        cfg = get_config()
        self.servers = [s.strip() for s in cfg.get("dns_targets", "servers").split(",")]
        self.test_domains = [d.strip() for d in cfg.get("dns_targets", "test_domains").split(",")]
        self.query_types = [q.strip() for q in cfg.get("dns_targets", "query_types").split(",")]
        self._history: list[DNSQueryResult] = []
        self._pending_db_writes: list[dict] = []
        # Load recent history from DB on startup
        self._load_from_db()

    def _load_from_db(self):
        """Load recent queries from DB to warm the in-memory history."""
        try:
            rows = db.get_dns_queries(limit=500, since=time.time() - 3600)
            for r in reversed(rows):  # DB returns DESC, we want ASC
                self._history.append(DNSQueryResult(
                    server=r["server"], domain=r["domain"], query_type=r["query_type"],
                    response_time_ms=r["response_time_ms"], status=r["status"],
                    answers=json.loads(r["answers"]) if r.get("answers") else [],
                    error=r.get("error"), timestamp=r["timestamp"],
                ))
            if self._history:
                logger.info(f"Loaded {len(self._history)} queries from DB")
        except Exception as e:
            logger.warning(f"Could not load history from DB: {e}")

    def _flush_to_db(self):
        """Write pending queries to database."""
        if not self._pending_db_writes:
            return
        try:
            db.insert_dns_queries_batch(self._pending_db_writes)
            self._pending_db_writes = []
        except Exception as e:
            logger.error(f"DB write failed: {e}")

    def query(self, server: str, domain: str, qtype: str = "A") -> DNSQueryResult:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [server]
        resolver.timeout = 5
        resolver.lifetime = 5
        start = time.time()
        try:
            answer = resolver.resolve(domain, qtype)
            elapsed = (time.time() - start) * 1000
            records = [str(r) for r in answer]
            result = DNSQueryResult(
                server=server, domain=domain, query_type=qtype,
                response_time_ms=round(elapsed, 2), status="success", answers=records,
            )
        except dns.resolver.NXDOMAIN:
            elapsed = (time.time() - start) * 1000
            result = DNSQueryResult(
                server=server, domain=domain, query_type=qtype,
                response_time_ms=round(elapsed, 2), status="nxdomain", error="NXDOMAIN",
            )
        except dns.resolver.NoAnswer:
            elapsed = (time.time() - start) * 1000
            result = DNSQueryResult(
                server=server, domain=domain, query_type=qtype,
                response_time_ms=round(elapsed, 2), status="noanswer", error="No answer",
            )
        except dns.exception.Timeout:
            result = DNSQueryResult(
                server=server, domain=domain, query_type=qtype,
                response_time_ms=5000, status="timeout", error="Timeout",
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            result = DNSQueryResult(
                server=server, domain=domain, query_type=qtype,
                response_time_ms=round(elapsed, 2), status="error", error=str(e),
            )
        self._history.append(result)
        if len(self._history) > 5000:
            self._history = self._history[-3000:]
        # Queue for DB persistence
        self._pending_db_writes.append({
            "server": result.server, "domain": result.domain,
            "query_type": result.query_type, "response_time_ms": result.response_time_ms,
            "status": result.status, "answers": json.dumps(result.answers),
            "error": result.error, "timestamp": result.timestamp,
        })
        return result

    def collect_all(self) -> list[DNSQueryResult]:
        results = []
        for server in self.servers:
            for domain in self.test_domains:
                for qtype in self.query_types[:3]:  # A, AAAA, MX by default
                    results.append(self.query(server, domain, qtype))
        # Flush batch to DB
        self._flush_to_db()
        return results

    def get_history(self, limit: int = 200) -> list[DNSQueryResult]:
        return self._history[-limit:]

    def get_summary(self) -> dict:
        if not self._history:
            return {"total_queries": 0}
        recent = self._history[-200:]
        success = [r for r in recent if r.status == "success"]
        avg_time = sum(r.response_time_ms for r in success) / max(len(success), 1)
        status_counts = {}
        for r in recent:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        server_times = {}
        for r in recent:
            if r.server not in server_times:
                server_times[r.server] = []
            server_times[r.server].append(r.response_time_ms)
        server_avg = {s: round(sum(t)/len(t), 2) for s, t in server_times.items()}
        return {
            "total_queries": len(self._history),
            "recent_count": len(recent),
            "avg_response_ms": round(avg_time, 2),
            "success_rate": round(len(success) / max(len(recent), 1) * 100, 1),
            "status_counts": status_counts,
            "server_avg_ms": server_avg,
        }
