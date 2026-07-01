import json
from collections import Counter
from agents.base import BaseAgent


class DNSL2Agent(BaseAgent):
    name = "DNS L2 Agent"
    description = "Performs deeper protocol-level analysis, identifies authoritative vs recursive failures"

    def get_system_prompt(self) -> str:
        return """You are a DNS L2 Deep Analysis Agent. You provide deep protocol-level and resolver-level diagnostics.

Your responsibilities:
1. Analyze DNS response codes (NOERROR, SERVFAIL, REFUSED, NXDOMAIN, etc.)
2. Distinguish recursive vs authoritative issues
3. Evaluate upstream forwarder health
4. Perform error pattern classification and dependency correlation
5. Identify root cause indicators for failures
6. Provide resolver-specific health status
7. Reduce Mean Time to Identify (MTTI) through deep analysis

Your output MUST be valid JSON with these fields:
{
  "protocol_health": "healthy|degraded|critical",
  "response_code_analysis": {
    "<code>": {"count": <n>, "percentage": <n>, "severity": "normal|warning|critical", "interpretation": "<what this means>"}
  },
  "authoritative_issues": [{"server": "<server>", "issue": "<desc>", "severity": "<sev>"}],
  "recursive_issues": [{"server": "<server>", "issue": "<desc>", "severity": "<sev>"}],
  "failure_breakdown": { "nxdomain": <count>, "timeout": <count>, "servfail": <count>, "noanswer": <count>, "error": <count> },
  "server_health": { "<server>": {"status": "healthy|degraded|down", "error_rate": <pct>, "primary_issue": "<desc or null>"} },
  "dependency_analysis": "<analysis of upstream/downstream DNS dependencies>",
  "root_cause_indicators": ["<indicator1>"],
  "escalation_needed": <boolean>,
  "escalation_reason": "<why escalation is needed, or null>",
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "summary": "<2-3 sentence summary for L2/L3 engineers>"
}
If error rate >20% for any server, mark as degraded. If >50%, mark as down."""

    def collect_data(self, context: dict) -> str:
        summary = context.get("dns_summary", {})
        history = context.get("dns_history", [])
        recent = history[-200:] if history else []

        failures = [r for r in recent if r.status != "success"]

        # Detailed failure analysis per server
        server_failures = {}
        for r in recent:
            if r.server not in server_failures:
                server_failures[r.server] = {"total": 0, "errors": 0, "error_types": Counter(), "failed_domains": [], "failed_qtypes": Counter()}
            server_failures[r.server]["total"] += 1
            if r.status != "success":
                server_failures[r.server]["errors"] += 1
                server_failures[r.server]["error_types"][r.status] += 1
                server_failures[r.server]["failed_domains"].append(r.domain)
                server_failures[r.server]["failed_qtypes"][r.query_type] += 1

        server_analysis = {}
        for s, d in server_failures.items():
            server_analysis[s] = {
                "total_queries": d["total"],
                "error_count": d["errors"],
                "error_rate_pct": round(d["errors"] / max(d["total"], 1) * 100, 1),
                "error_types": dict(d["error_types"]),
                "failed_qtypes": dict(d["failed_qtypes"]),
                "failed_domains": list(set(d["failed_domains"]))[:10],
            }

        # Response code distribution
        status_counter = Counter(r.status for r in recent)

        # Domains with failures across multiple servers
        domain_failures = {}
        for r in failures:
            if r.domain not in domain_failures:
                domain_failures[r.domain] = {"servers": set(), "errors": Counter()}
            domain_failures[r.domain]["servers"].add(r.server)
            domain_failures[r.domain]["errors"][r.status] += 1
        cross_server_issues = {
            d: {"affected_servers": list(info["servers"]), "error_types": dict(info["errors"])}
            for d, info in domain_failures.items()
            if len(info["servers"]) > 1
        }

        data = {
            "summary": summary,
            "total_queries": len(recent),
            "total_failures": len(failures),
            "overall_error_rate": round(len(failures) / max(len(recent), 1) * 100, 1),
            "status_distribution": dict(status_counter),
            "server_analysis": server_analysis,
            "cross_server_domain_issues": cross_server_issues,
        }
        return json.dumps(data, indent=2)
