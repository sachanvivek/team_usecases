import json
import statistics
import time
from collections import Counter
from agents.base import BaseAgent


class AnomalyDetectionAgent(BaseAgent):
    name = "Anomaly Detection Agent"
    description = "ML-based detection of deviations, correlates multi-signal inputs"

    def get_system_prompt(self) -> str:
        return """You are a DNS Anomaly Detection Agent acting as the central ML anomaly engine across DNS signals.

Your responsibilities:
1. Correlate logs, metrics, and events from multiple signals
2. Detect unknown and zero-day anomalies using statistical analysis
3. Apply unsupervised ML concepts (seasonality, clustering, outlier detection)
4. Perform multi-signal correlation across servers, domains, and query types
5. Generate anomaly alerts with confidence scores
6. Eliminate rule-based alert fatigue by focusing on truly anomalous behavior
7. Suggest remediation for detected anomalies

Your output MUST be valid JSON with these fields:
{
  "anomalies_detected": <number>,
  "anomalies": [
    {"type": "latency_spike|error_surge|traffic_anomaly|pattern_deviation|correlation_anomaly",
     "severity": "low|medium|high|critical",
     "confidence": <0-100>,
     "description": "<detailed description>",
     "affected_server": "<server or 'all'>",
     "affected_domain": "<domain or null>",
     "evidence": "<statistical evidence>",
     "baseline_value": "<normal value>",
     "observed_value": "<anomalous value>"}
  ],
  "statistical_summary": {
    "mean_ms": <n>, "stddev_ms": <n>, "p50_ms": <n>, "p95_ms": <n>, "p99_ms": <n>,
    "outlier_count": <n>, "outlier_percentage": <n>
  },
  "correlation_insights": [{"insight": "<desc>", "signals": ["<signal1>", "<signal2>"], "confidence": <0-100>}],
  "threat_level": "none|low|medium|high|critical",
  "baseline_status": "normal|deviation_detected|significant_shift",
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "summary": "<2-3 sentence summary>"
}
Use 2-sigma rule for outlier detection. Any metric >3 sigma from mean is critical anomaly."""

    def collect_data(self, context: dict) -> str:
        history = context.get("dns_history", [])
        recent = history[-300:] if history else []
        now = time.time()

        # Overall timing statistics
        times = [r.response_time_ms for r in recent if r.status == "success"]
        overall_stats = {}
        outliers = []
        if len(times) > 2:
            mean = statistics.mean(times)
            stdev = statistics.stdev(times)
            sorted_t = sorted(times)
            p50 = sorted_t[len(sorted_t) // 2]
            p95 = sorted_t[int(len(sorted_t) * 0.95)]
            p99 = sorted_t[int(len(sorted_t) * 0.99)] if len(sorted_t) > 10 else p95
            outliers = [{"value": round(t, 2), "sigma": round(abs(t - mean) / max(stdev, 0.01), 2)}
                        for t in times if abs(t - mean) > 2 * stdev]
            overall_stats = {
                "mean": round(mean, 2), "stdev": round(stdev, 2),
                "p50": round(p50, 2), "p95": round(p95, 2), "p99": round(p99, 2),
                "outlier_count": len(outliers), "total_samples": len(times),
                "outlier_pct": round(len(outliers) / len(times) * 100, 1),
            }

        # Per-server statistics
        server_data = {}
        for r in recent:
            if r.server not in server_data:
                server_data[r.server] = {"times": [], "errors": 0, "statuses": Counter()}
            server_data[r.server]["times"].append(r.response_time_ms)
            server_data[r.server]["statuses"][r.status] += 1
            if r.status != "success":
                server_data[r.server]["errors"] += 1

        server_stats = {}
        for s, d in server_data.items():
            t = d["times"]
            server_stats[s] = {
                "avg_ms": round(sum(t) / max(len(t), 1), 2),
                "min_ms": round(min(t), 2) if t else 0,
                "max_ms": round(max(t), 2) if t else 0,
                "stdev_ms": round(statistics.stdev(t), 2) if len(t) > 1 else 0,
                "errors": d["errors"],
                "queries": len(t),
                "error_rate": round(d["errors"] / max(len(t), 1) * 100, 1),
                "status_distribution": dict(d["statuses"]),
            }

        # Time-window comparison for trend detection
        mid = len(recent) // 2
        first_half = [r.response_time_ms for r in recent[:mid] if r.status == "success"]
        second_half = [r.response_time_ms for r in recent[mid:] if r.status == "success"]
        trend = {}
        if first_half and second_half:
            trend = {
                "first_half_avg": round(statistics.mean(first_half), 2),
                "second_half_avg": round(statistics.mean(second_half), 2),
                "change_pct": round((statistics.mean(second_half) - statistics.mean(first_half)) / max(statistics.mean(first_half), 0.01) * 100, 1),
            }

        # Error rate over time windows
        windows = {"last_1min": 60, "last_5min": 300, "last_15min": 900}
        window_stats = {}
        for name, secs in windows.items():
            w = [r for r in recent if now - r.timestamp <= secs]
            if w:
                errs = sum(1 for r in w if r.status != "success")
                window_stats[name] = {
                    "queries": len(w),
                    "errors": errs,
                    "error_rate": round(errs / len(w) * 100, 1),
                    "avg_ms": round(sum(r.response_time_ms for r in w) / len(w), 2),
                }

        data = {
            "overall_statistics": overall_stats,
            "server_stats": server_stats,
            "trend_comparison": trend,
            "time_window_stats": window_stats,
            "top_outliers": outliers[:10],
            "total_queries": len(recent),
        }
        return json.dumps(data, indent=2)
