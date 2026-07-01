import json
import statistics
from agents.base import BaseAgent


class ClientExperienceScoringAgent(BaseAgent):
    name = "Client Experience Scoring Agent"
    description = "Aggregates performance, availability, errors into CX scores"

    def get_system_prompt(self) -> str:
        return """You are a Client Experience Scoring Agent. You provide a business-friendly score of DNS service quality.

Your responsibilities:
1. Aggregate performance, availability, and error metrics into composite scores
2. Normalize scores across regions/servers and applications/domains
3. Generate weighted scoring models (performance 40%, availability 30%, reliability 20%, consistency 10%)
4. Trend analysis - is the experience improving or degrading?
5. SLA risk indicators - are we at risk of breaching SLA thresholds?
6. Bridge technical metrics to business impact
7. Support leadership-level reporting with clear CX scorecards

Your output MUST be valid JSON with these fields:
{
  "overall_cx_score": <0-100>,
  "cx_grade": "A+|A|A-|B+|B|B-|C+|C|C-|D|F",
  "dimensions": {
    "performance": {"score": <0-100>, "grade": "A|B|C|D|F", "detail": "<key metric driving this score>"},
    "availability": {"score": <0-100>, "grade": "A|B|C|D|F", "detail": "<key metric>"},
    "reliability": {"score": <0-100>, "grade": "A|B|C|D|F", "detail": "<key metric>"},
    "consistency": {"score": <0-100>, "grade": "A|B|C|D|F", "detail": "<key metric>"}
  },
  "server_cx_scores": {
    "<server>": {"score": <0-100>, "grade": "<grade>", "performance_ms": <n>, "availability_pct": <n>}
  },
  "domain_cx_scores": {
    "<domain>": {"score": <0-100>, "availability_pct": <n>}
  },
  "trend": "improving|stable|degrading",
  "trend_detail": "<what is changing and why>",
  "sla_status": {
    "at_risk": <boolean>,
    "current_availability": <pct>,
    "target_availability": 99.9,
    "risk_detail": "<detail or null>"
  },
  "impact_summary": "<business-friendly description of current DNS service impact on users>",
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "summary": "<2-3 sentence executive summary suitable for leadership>"
}
Scoring: A+=97-100, A=93-96, A-=90-92, B+=87-89, B=83-86, B-=80-82, C+=77-79, C=73-76, C-=70-72, D=60-69, F=<60
SLA target: 99.9% availability. Flag at-risk if <99.5%."""

    def collect_data(self, context: dict) -> str:
        summary = context.get("dns_summary", {})
        history = context.get("dns_history", [])
        recent = history[-300:] if history else []

        # Per-server metrics
        server_data = {}
        for r in recent:
            if r.server not in server_data:
                server_data[r.server] = {"times": [], "success": 0, "fail": 0}
            server_data[r.server]["times"].append(r.response_time_ms)
            if r.status == "success":
                server_data[r.server]["success"] += 1
            else:
                server_data[r.server]["fail"] += 1

        server_metrics = {}
        for s, d in server_data.items():
            total = d["success"] + d["fail"]
            times = d["times"]
            server_metrics[s] = {
                "avg_ms": round(sum(times) / max(len(times), 1), 2),
                "p95_ms": round(sorted(times)[int(len(times) * 0.95)], 2) if times else 0,
                "stdev_ms": round(statistics.stdev(times), 2) if len(times) > 1 else 0,
                "availability_pct": round(d["success"] / max(total, 1) * 100, 2),
                "total_queries": total,
                "success": d["success"],
                "failures": d["fail"],
            }

        # Per-domain metrics
        domain_data = {}
        for r in recent:
            if r.domain not in domain_data:
                domain_data[r.domain] = {"success": 0, "fail": 0, "times": []}
            domain_data[r.domain]["times"].append(r.response_time_ms)
            if r.status == "success":
                domain_data[r.domain]["success"] += 1
            else:
                domain_data[r.domain]["fail"] += 1

        domain_metrics = {}
        for d, info in domain_data.items():
            total = info["success"] + info["fail"]
            domain_metrics[d] = {
                "avg_ms": round(sum(info["times"]) / max(len(info["times"]), 1), 2),
                "availability_pct": round(info["success"] / max(total, 1) * 100, 2),
                "total_queries": total,
            }

        # Overall consistency (stdev of response times)
        all_success_times = [r.response_time_ms for r in recent if r.status == "success"]
        consistency = {}
        if len(all_success_times) > 1:
            consistency = {
                "stdev_ms": round(statistics.stdev(all_success_times), 2),
                "cv_pct": round(statistics.stdev(all_success_times) / max(statistics.mean(all_success_times), 0.01) * 100, 1),
            }

        data = {
            "overall_summary": summary,
            "server_metrics": server_metrics,
            "domain_metrics": domain_metrics,
            "consistency_metrics": consistency,
            "total_queries": len(recent),
            "total_success": sum(1 for r in recent if r.status == "success"),
            "total_failures": sum(1 for r in recent if r.status != "success"),
            "overall_availability_pct": round(sum(1 for r in recent if r.status == "success") / max(len(recent), 1) * 100, 2),
        }
        return json.dumps(data, indent=2)
