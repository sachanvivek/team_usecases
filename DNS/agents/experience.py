import json
import statistics
from agents.base import BaseAgent


class DNSExperienceAgent(BaseAgent):
    name = "DNS Experience Agent"
    description = "Measures end-user DNS resolution time, tracks geo/ISP-wise experience, generates experience scores"

    def get_system_prompt(self) -> str:
        return """You are a DNS Experience Analysis Agent. You continuously measure end-user DNS resolution experience and convert raw timing data into actionable experience scores.

Your responsibilities:
1. Capture DNS response time from client perspective
2. Measure resolution success/failure rates per server
3. Track performance variations across different DNS servers (simulating geo/ISP impact)
4. Generate experience scores and early warning alerts
5. Detect user impact before tickets are raised
6. Recommend remediation actions for degraded experience

Your output MUST be valid JSON with these fields:
{
  "overall_score": <0-100 experience score>,
  "avg_resolution_ms": <number>,
  "p95_resolution_ms": <number>,
  "server_scores": { "<server>": {"score": <0-100>, "avg_ms": <n>, "success_rate": <n>, "tier": "excellent|good|fair|poor|critical"} },
  "performance_tier": "excellent|good|fair|poor|critical",
  "geo_analysis": "<analysis of performance differences across servers simulating geo/ISP variation>",
  "degradation_detected": <boolean>,
  "degraded_servers": ["<server1>"],
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>", "affected_server": "<server>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "recommendations": ["<recommendation1>"],
  "trend": "improving|stable|degrading",
  "summary": "<2-3 sentence executive summary of DNS experience>"
}
Scoring: 90-100=excellent, 70-89=good, 50-69=fair, 30-49=poor, <30=critical
For any server with avg response >200ms or success rate <90%, flag as degraded and suggest remediation."""

    def collect_data(self, context: dict) -> str:
        summary = context.get("dns_summary", {})
        history = context.get("dns_history", [])
        recent = history[-100:] if history else []

        # Per-server experience metrics
        server_data = {}
        for r in recent:
            if r.server not in server_data:
                server_data[r.server] = {"times": [], "success": 0, "fail": 0, "statuses": []}
            server_data[r.server]["times"].append(r.response_time_ms)
            server_data[r.server]["statuses"].append(r.status)
            if r.status == "success":
                server_data[r.server]["success"] += 1
            else:
                server_data[r.server]["fail"] += 1

        server_metrics = {}
        for s, d in server_data.items():
            total = d["success"] + d["fail"]
            times = d["times"]
            sorted_times = sorted(times)
            server_metrics[s] = {
                "avg_ms": round(sum(times) / max(len(times), 1), 2),
                "min_ms": round(min(times), 2) if times else 0,
                "max_ms": round(max(times), 2) if times else 0,
                "p50_ms": round(sorted_times[len(sorted_times) // 2], 2) if sorted_times else 0,
                "p95_ms": round(sorted_times[int(len(sorted_times) * 0.95)], 2) if sorted_times else 0,
                "success_rate": round(d["success"] / max(total, 1) * 100, 1),
                "total_queries": total,
                "error_types": dict(set((s, d["statuses"].count(s)) for s in set(d["statuses"]) if s != "success")),
            }

        # Overall statistics
        all_times = [r.response_time_ms for r in recent if r.status == "success"]
        overall_stats = {}
        if all_times:
            sorted_all = sorted(all_times)
            overall_stats = {
                "mean_ms": round(statistics.mean(all_times), 2),
                "median_ms": round(statistics.median(all_times), 2),
                "stdev_ms": round(statistics.stdev(all_times), 2) if len(all_times) > 1 else 0,
                "p95_ms": round(sorted_all[int(len(sorted_all) * 0.95)], 2),
            }

        data = {
            "summary": summary,
            "server_metrics": server_metrics,
            "overall_statistics": overall_stats,
            "total_queries": len(recent),
            "sample_queries": [
                {"server": r.server, "domain": r.domain, "type": r.query_type,
                 "time_ms": r.response_time_ms, "status": r.status}
                for r in recent[-30:]
            ],
        }
        return json.dumps(data, indent=2)
