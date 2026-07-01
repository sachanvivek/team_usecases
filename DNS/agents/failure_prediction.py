import json
import time
import statistics
from collections import Counter
from agents.base import BaseAgent


class FailurePredictionAgent(BaseAgent):
    name = "Failure Prediction Agent"
    description = "Forecasts outages and saturation risks, generates probability-based alerts"

    def get_system_prompt(self) -> str:
        return """You are a DNS Failure Prediction Agent. You forecast probable DNS failures before they occur.

Your responsibilities:
1. Predict resolver saturation based on load trends
2. Forecast upstream or Anycast node failures from degradation patterns
3. Use time-series analysis of historical data to detect worsening trends
4. Generate probability-based risk scoring for each server/domain
5. Provide predictive alerts with lead time estimates
6. Enable proactive remediation and shift-left incident management
7. Suggest auto-remediable actions vs manual escalations

Your output MUST be valid JSON with these fields:
{
  "predictions": [
    {"target": "<server or domain>", "risk": "low|medium|high|critical", "probability": <0-100>,
     "predicted_issue": "<what will happen>", "timeframe": "<when, e.g. '15-30 minutes'>",
     "evidence": "<what data supports this prediction>",
     "recommended_action": "<preventive action>",
     "auto_remediate": <boolean>}
  ],
  "overall_risk": "low|medium|high|critical",
  "risk_score": <0-100>,
  "trending_issues": [{"issue": "<desc>", "trend": "worsening|stable|improving", "rate_of_change": "<desc>"}],
  "capacity_analysis": {
    "servers": { "<server>": {"load_pct": <estimated 0-100>, "headroom": "sufficient|limited|critical"} },
    "overall": "<brief capacity summary>"
  },
  "failure_timeline": [{"timeframe": "<when>", "probability": <0-100>, "scenario": "<what could happen>"}],
  "alerts": [{"severity": "info|warning|critical", "message": "<msg>"}],
  "remediation_actions": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change"}
  ],
  "recommended_actions": ["<action1>"],
  "summary": "<2-3 sentence summary>"
}
A worsening error rate trend + increasing latency = high risk prediction. Be specific about timeframes."""

    def collect_data(self, context: dict) -> str:
        history = context.get("dns_history", [])
        recent = history[-300:] if history else []
        now = time.time()

        # Time-windowed trend analysis
        windows = [
            ("last_1min", 60), ("last_2min", 120), ("last_5min", 300),
            ("last_10min", 600), ("last_15min", 900),
        ]
        window_stats = {}
        for name, secs in windows:
            w = [r for r in recent if now - r.timestamp <= secs]
            if w:
                errs = sum(1 for r in w if r.status != "success")
                times = [r.response_time_ms for r in w]
                window_stats[name] = {
                    "count": len(w), "errors": errs,
                    "error_rate": round(errs / len(w) * 100, 1),
                    "avg_ms": round(sum(times) / len(times), 2),
                    "max_ms": round(max(times), 2),
                }
            else:
                window_stats[name] = {"count": 0, "errors": 0, "error_rate": 0, "avg_ms": 0, "max_ms": 0}

        # Per-server trend (split into 3 equal time segments)
        server_trends = {}
        for r in recent:
            if r.server not in server_trends:
                server_trends[r.server] = {"early": [], "mid": [], "late": []}
        if recent:
            third = len(recent) // 3
            for i, r in enumerate(recent):
                segment = "early" if i < third else "mid" if i < 2 * third else "late"
                server_trends[r.server][segment].append(r)

        server_trend_analysis = {}
        for server, segments in server_trends.items():
            seg_stats = {}
            for seg_name, records in segments.items():
                if records:
                    errs = sum(1 for r in records if r.status != "success")
                    times = [r.response_time_ms for r in records]
                    seg_stats[seg_name] = {
                        "count": len(records),
                        "error_rate": round(errs / len(records) * 100, 1),
                        "avg_ms": round(sum(times) / len(times), 2),
                    }
            server_trend_analysis[server] = seg_stats

        # Error type progression
        error_progression = {}
        for r in recent:
            if r.status != "success":
                if r.status not in error_progression:
                    error_progression[r.status] = []
                error_progression[r.status].append(r.timestamp)

        data = {
            "trend_windows": window_stats,
            "server_trends": server_trend_analysis,
            "total_history": len(history),
            "total_recent": len(recent),
            "summary": context.get("dns_summary", {}),
            "error_types_seen": list(error_progression.keys()),
            "error_counts_by_type": {k: len(v) for k, v in error_progression.items()},
        }
        return json.dumps(data, indent=2)
