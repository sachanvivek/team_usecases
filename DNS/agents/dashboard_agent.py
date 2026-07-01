import json
from agents.base import BaseAgent


class DashboardAgent(BaseAgent):
    name = "Dashboard Agent"
    description = "Produces role-specific views: Ops, Network, Security, Leadership"

    def get_system_prompt(self) -> str:
        return """You are a DNS Dashboard & Insight Agent. You transform data and agent findings into actionable visual insights.

Your responsibilities:
1. Generate role-specific dashboards (Ops, Network, Security, Leadership)
2. Surface recommendations and trends from all agent outputs
3. Create executive summaries using LLM-driven narrative generation
4. Provide context-aware insight narratives
5. Accelerate decision-making with clear, prioritized action items
6. Consolidate all alerts and remediation actions across agents
7. Generate ITSM ticket recommendations for critical issues

Your output MUST be valid JSON with these fields:
{
  "executive_summary": "<1-3 sentence overall status narrative for leadership>",
  "overall_health": "healthy|warning|critical",
  "health_score": <0-100>,
  "role_views": {
    "ops": {
      "status": "<operational status>",
      "key_metrics": ["<metric1>", "<metric2>"],
      "action_items": ["<action1>"],
      "priority_issues": ["<issue requiring immediate attention>"]
    },
    "network": {
      "status": "<network health status>",
      "key_metrics": ["<metric1>"],
      "action_items": ["<action1>"],
      "topology_notes": "<any network topology observations>"
    },
    "security": {
      "status": "<security posture>",
      "key_metrics": ["<metric1>"],
      "action_items": ["<action1>"],
      "threat_summary": "<brief threat landscape>"
    },
    "leadership": {
      "status": "<business impact status>",
      "key_metrics": ["<business-relevant metric1>"],
      "action_items": ["<strategic action1>"],
      "sla_summary": "<SLA compliance status>",
      "risk_outlook": "<forward-looking risk assessment>"
    }
  },
  "active_alerts": [
    {"severity": "info|warning|critical", "message": "<msg>", "source_agent": "<agent name>", "recommended_action": "<action>"}
  ],
  "consolidated_remediation": [
    {"action": "<what to do>", "priority": "low|medium|high|critical", "source_agent": "<agent>",
     "target": "<server/zone>", "auto_remediate": <boolean>, "itsm_category": "incident|change",
     "itsm_priority": "1|2|3|4"}
  ],
  "trend_narrative": "<paragraph describing overall DNS infrastructure trends>",
  "insight_narratives": [
    {"topic": "<topic>", "narrative": "<1-2 sentence insight>", "importance": "low|medium|high"}
  ],
  "summary": "<2-3 sentence summary>"
}
Consolidate and deduplicate alerts from all agents. Prioritize by severity.
For leadership view, translate technical metrics into business language."""

    def collect_data(self, context: dict) -> str:
        agent_results = context.get("agent_results", {})
        summary = context.get("dns_summary", {})

        # Consolidate all agent outputs
        data = {"dns_summary": summary, "agent_results": {}}
        all_alerts = []
        all_remediation = []

        for name, result in agent_results.items():
            if result:
                safe = {k: v for k, v in result.items() if k not in ("raw_analysis",)}
                data["agent_results"][name] = safe

                # Collect alerts from each agent
                for alert in result.get("alerts", []):
                    if isinstance(alert, dict):
                        alert["source_agent"] = name
                        all_alerts.append(alert)

                # Collect remediation actions
                for action in result.get("remediation_actions", []):
                    if isinstance(action, dict):
                        action["source_agent"] = name
                        all_remediation.append(action)

                # Also collect from anomalies, misconfigurations, predictions
                for anom in result.get("anomalies", []):
                    if isinstance(anom, dict) and anom.get("severity") in ("high", "critical"):
                        all_alerts.append({
                            "severity": anom["severity"], "message": anom.get("description", ""),
                            "source_agent": name
                        })
                for mc in result.get("misconfigurations", []):
                    if isinstance(mc, dict):
                        all_alerts.append({
                            "severity": mc.get("severity", "medium"),
                            "message": f"Misconfiguration: {mc.get('description', '')}",
                            "source_agent": name
                        })

        # Sort alerts by severity
        severity_order = {"critical": 0, "high": 1, "warning": 2, "medium": 3, "info": 4, "low": 5}
        all_alerts.sort(key=lambda a: severity_order.get(a.get("severity", "info"), 4))
        all_remediation.sort(key=lambda a: severity_order.get(a.get("priority", "medium"), 3))

        data["consolidated_alerts"] = all_alerts[:20]
        data["consolidated_remediation"] = all_remediation[:15]

        return json.dumps(data, indent=2, default=str)
