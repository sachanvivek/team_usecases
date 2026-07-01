import json
import math
from collections import Counter
from agents.base import BaseAgent


class QueryLogAnalyticsAgent(BaseAgent):
    name = "Query Log Analytics Agent"
    description = "Analyzes query intent, NXDOMAIN trends, suspicious domains"

    def get_system_prompt(self) -> str:
        return """You are a DNS Query Log Analytics Agent. You perform deep query-level intelligence and security analytics.

Your responsibilities:
1. NXDOMAIN trend analysis - detect increasing NXDOMAIN rates suggesting DGA or misconfigured apps
2. DGA (Domain Generation Algorithm) / DNS tunneling detection - look for high-entropy domain names
3. Domain reputation scoring based on query patterns
4. Suspicious domain lists based on unusual query patterns
5. Statistical analysis of query distributions for attack pattern visibility
6. Early detection of DNS-based attacks (exfiltration, tunneling, cache poisoning)
7. Suggest security remediation actions

Your output MUST be valid JSON with these fields:
{
  "query_volume": <number>,
  "unique_domains": <number>,
  "top_domains": [{"domain": "<domain>", "count": <n>, "reputation": "clean|suspicious|malicious"}],
  "top_query_types": [{"type": "<type>", "count": <n>, "percentage": <n>}],
  "nxdomain_analysis": {
    "count": <n>, "rate_pct": <n>, "unique_domains": <n>,
    "trend": "increasing|stable|decreasing",
    "suspicious_nxdomains": ["<domain1>"],
    "possible_cause": "<DGA|misconfiguration|normal>"
  },
  "suspicious_patterns": [
    {"pattern": "<desc>", "risk": "low|medium|high|critical",
     "evidence": "<details>", "domains_involved": ["<domain>"]}
  ],
  "entropy_analysis": {
    "avg_domain_entropy": <n>,
    "high_entropy_domains": ["<domain with entropy >3.5>"],
    "dga_likelihood": "none|low|medium|high"
  },
  "security_flags": [{"flag": "<desc>", "severity": "info|warning|critical", "action": "<recommended action>"}],
  "tunneling_indicators": {"detected": <boolean>, "evidence": "<desc or null>"},
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<domain/server>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "summary": "<2-3 sentence summary>"
}
Flag any domain with entropy >4.0 as potential DGA. NXDOMAIN rate >15% is suspicious."""

    def collect_data(self, context: dict) -> str:
        history = context.get("dns_history", [])
        recent = history[-300:] if history else []

        domain_counter = Counter(r.domain for r in recent)
        type_counter = Counter(r.query_type for r in recent)
        status_counter = Counter(r.status for r in recent)
        nxdomains = [r.domain for r in recent if r.status == "nxdomain"]

        # Domain entropy calculation (Shannon entropy)
        domain_entropies = {}
        for domain in set(r.domain for r in recent):
            entropy = self._shannon_entropy(domain)
            domain_entropies[domain] = round(entropy, 3)

        high_entropy = {d: e for d, e in domain_entropies.items() if e > 3.5}

        # Per-domain query patterns
        domain_patterns = {}
        for r in recent:
            if r.domain not in domain_patterns:
                domain_patterns[r.domain] = {"qtypes": Counter(), "servers": set(), "statuses": Counter()}
            domain_patterns[r.domain]["qtypes"][r.query_type] += 1
            domain_patterns[r.domain]["servers"].add(r.server)
            domain_patterns[r.domain]["statuses"][r.status] += 1

        domain_analysis = {}
        for d, p in domain_patterns.items():
            domain_analysis[d] = {
                "query_types": dict(p["qtypes"]),
                "server_count": len(p["servers"]),
                "status_distribution": dict(p["statuses"]),
                "entropy": domain_entropies.get(d, 0),
            }

        # NXDOMAIN rate analysis
        nxdomain_rate = round(len(nxdomains) / max(len(recent), 1) * 100, 1)

        data = {
            "total_queries": len(recent),
            "unique_domains": len(set(r.domain for r in recent)),
            "domain_counts": dict(domain_counter.most_common(20)),
            "type_counts": dict(type_counter.most_common(10)),
            "status_counts": dict(status_counter),
            "nxdomain_domains": list(set(nxdomains)),
            "nxdomain_rate_pct": nxdomain_rate,
            "domain_entropies": dict(sorted(domain_entropies.items(), key=lambda x: -x[1])[:15]),
            "high_entropy_domains": high_entropy,
            "domain_analysis": {d: domain_analysis[d] for d in list(domain_analysis.keys())[:15]},
        }
        return json.dumps(data, indent=2)

    @staticmethod
    def _shannon_entropy(text: str) -> float:
        """Calculate Shannon entropy of a string."""
        if not text:
            return 0
        freq = Counter(text)
        length = len(text)
        return -sum((c / length) * math.log2(c / length) for c in freq.values())
