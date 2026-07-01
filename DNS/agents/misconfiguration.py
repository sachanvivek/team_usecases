import json
from collections import Counter
from agents.base import BaseAgent


class MisconfigurationDetectionAgent(BaseAgent):
    name = "Misconfiguration Detection Agent"
    description = "Detects DNSSEC, TTL, zone, or forwarding misconfigurations"

    def get_system_prompt(self) -> str:
        return """You are a DNS Misconfiguration Detection Agent. You identify configuration errors and policy violations in DNS infrastructure.

Your responsibilities:
1. DNSSEC validation checks - detect missing or broken DNSSEC chains
2. TTL anomalies - detect unusually low or high TTLs that could cause issues
3. Zone transfer and forwarding validation
4. Detect servers responding differently to the same queries (split-brain)
5. Identify NXDOMAIN patterns that suggest misconfigured zones
6. Check for missing record types (e.g., no MX, missing NS delegation)
7. Policy-based validation and knowledge-based reasoning
8. Suggest remediation steps for each misconfiguration

Your output MUST be valid JSON with these fields:
{
  "misconfigurations": [
    {"type": "dnssec|ttl|zone|forwarding|delegation|split_brain|missing_record|policy_violation",
     "severity": "low|medium|high|critical",
     "description": "<detailed description>",
     "affected": "<server, zone, or domain>",
     "evidence": "<what data shows this>",
     "fix": "<recommended fix>",
     "auto_fixable": <boolean>}
  ],
  "config_health_score": <0-100>,
  "checks_performed": [{"check": "<check name>", "result": "pass|warn|fail", "detail": "<brief detail>"}],
  "split_brain_detected": <boolean>,
  "split_brain_details": "<details or null>",
  "policy_violations": [{"policy": "<policy name>", "violation": "<desc>", "severity": "<sev>"}],
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "summary": "<2-3 sentence summary>"
}
If same domain returns different answers on different servers = potential split-brain issue (critical).
NoAnswer responses for standard record types suggest misconfiguration."""

    def collect_data(self, context: dict) -> str:
        history = context.get("dns_history", [])
        recent = history[-200:] if history else []

        # Categorize errors
        nxdomains = [r for r in recent if r.status == "nxdomain"]
        timeouts = [r for r in recent if r.status == "timeout"]
        no_answers = [r for r in recent if r.status == "noanswer"]
        servfails = [r for r in recent if r.status == "servfail"]

        # Server-specific error patterns
        server_errors = {}
        for r in recent:
            if r.status != "success":
                key = r.server
                if key not in server_errors:
                    server_errors[key] = {"total": 0, "by_type": Counter(), "by_qtype": Counter(), "domains": []}
                server_errors[key]["total"] += 1
                server_errors[key]["by_type"][r.status] += 1
                server_errors[key]["by_qtype"][r.query_type] += 1
                server_errors[key]["domains"].append(r.domain)

        server_error_analysis = {}
        for s, d in server_errors.items():
            server_error_analysis[s] = {
                "total_errors": d["total"],
                "error_types": dict(d["by_type"]),
                "error_qtypes": dict(d["by_qtype"]),
                "affected_domains": list(set(d["domains"]))[:10],
            }

        # Cross-server comparison for split-brain detection
        domain_server_responses = {}
        for r in recent:
            if r.status == "success" and r.answers:
                key = (r.domain, r.query_type)
                if key not in domain_server_responses:
                    domain_server_responses[key] = {}
                domain_server_responses[key][r.server] = sorted(r.answers)

        # Detect split-brain: same domain different answers
        split_brain_candidates = {}
        for (domain, qtype), server_answers in domain_server_responses.items():
            unique_answers = set(tuple(a) for a in server_answers.values())
            if len(unique_answers) > 1:
                split_brain_candidates[f"{domain}/{qtype}"] = {
                    s: a for s, a in server_answers.items()
                }

        # NoAnswer analysis by query type
        noanswer_by_qtype = Counter(r.query_type for r in no_answers)

        data = {
            "total_queries": len(recent),
            "nxdomain_count": len(nxdomains),
            "timeout_count": len(timeouts),
            "noanswer_count": len(no_answers),
            "servfail_count": len(servfails),
            "nxdomain_domains": list(set(r.domain for r in nxdomains)),
            "timeout_servers": list(set(r.server for r in timeouts)),
            "noanswer_by_qtype": dict(noanswer_by_qtype),
            "server_error_analysis": server_error_analysis,
            "split_brain_candidates": split_brain_candidates,
            "unique_servers": list(set(r.server for r in recent)),
        }
        return json.dumps(data, indent=2)
