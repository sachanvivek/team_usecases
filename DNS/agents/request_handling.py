import json
import time
from collections import Counter
from agents.base import BaseAgent


class DNSRequestHandlingAgent(BaseAgent):
    name = "DNS Request Handling Agent"
    description = "Observes query types, volumes, recursion behavior, detects abnormal request patterns"

    def get_system_prompt(self) -> str:
        return """You are a DNS Request Handling Analysis Agent. You observe and analyze real-time DNS request behavior across resolvers.

Your responsibilities:
1. Monitor query volume and QTYPE distribution
2. Track recursive vs authoritative behavior patterns
3. Identify abnormal request surges or patterns
4. Detect early signs of misbehaving clients or attacks (e.g., query floods, unusual QTYPE spikes)
5. Provide pattern recognition for query spikes and baseline deviation
6. Suggest remediation for abnormal patterns

Your output MUST be valid JSON with these fields:
{
  "total_queries": <number>,
  "queries_per_minute": <estimated rate>,
  "query_type_distribution": { "<type>": {"count": <n>, "percentage": <n>} },
  "domain_distribution": { "<domain>": <count> },
  "server_load": { "<server>": {"queries": <n>, "load_status": "normal|elevated|high|overloaded"} },
  "volume_status": "normal|elevated|high|critical",
  "surge_detected": <boolean>,
  "abnormal_patterns": [{"pattern": "<desc>", "severity": "low|medium|high", "evidence": "<details>"}],
  "recursion_analysis": "<analysis of recursive vs authoritative query behavior>",
  "risk_level": "low|medium|high|critical",
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "summary": "<2-3 sentence summary>"
}
Flag any server handling >50% of queries as a single point of failure risk."""

    def collect_data(self, context: dict) -> str:
        summary = context.get("dns_summary", {})
        history = context.get("dns_history", [])
        recent = history[-200:] if history else []

        type_counter = Counter(r.query_type for r in recent)
        domain_counter = Counter(r.domain for r in recent)
        server_counter = Counter(r.server for r in recent)
        status_counter = Counter(r.status for r in recent)

        # Time-based analysis
        now = time.time()
        last_minute = [r for r in recent if now - r.timestamp <= 60]
        last_5min = [r for r in recent if now - r.timestamp <= 300]

        # Per-server query distribution
        server_query_types = {}
        for r in recent:
            if r.server not in server_query_types:
                server_query_types[r.server] = Counter()
            server_query_types[r.server][r.query_type] += 1

        data = {
            "summary": summary,
            "query_type_distribution": dict(type_counter.most_common(20)),
            "domain_distribution": dict(domain_counter.most_common(20)),
            "server_distribution": dict(server_counter.most_common(10)),
            "status_distribution": dict(status_counter),
            "total_recent": len(recent),
            "queries_last_minute": len(last_minute),
            "queries_last_5min": len(last_5min),
            "server_query_types": {s: dict(c) for s, c in server_query_types.items()},
            "unique_domains": len(set(r.domain for r in recent)),
            "unique_servers": len(set(r.server for r in recent)),
            "sample_queries": [
                {"server": r.server, "domain": r.domain, "type": r.query_type,
                 "status": r.status, "time_ms": r.response_time_ms}
                for r in recent[-30:]
            ],
        }
        return json.dumps(data, indent=2)
