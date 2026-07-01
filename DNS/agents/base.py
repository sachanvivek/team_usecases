import json
import logging
import time
from abc import ABC, abstractmethod
from llm_client import get_llm_client

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    name: str = "BaseAgent"
    description: str = ""

    def __init__(self):
        self.last_result = None
        self.last_run_time = 0
        self.run_count = 0
        self._alert_history: list[dict] = []

    @abstractmethod
    def get_system_prompt(self) -> str:
        pass

    @abstractmethod
    def collect_data(self, context: dict) -> str:
        pass

    def get_remediation_actions(self) -> list[dict]:
        """Extract remediation actions from the last result."""
        if not self.last_result:
            return []
        actions = self.last_result.get("remediation_actions", [])
        if not actions:
            # Fall back to recommendations
            recs = self.last_result.get("recommended_actions", [])
            if not recs:
                recs = self.last_result.get("recommendations", [])
            for rec in recs:
                if isinstance(rec, str):
                    actions.append({"action": rec, "priority": "medium", "auto_remediate": False})
                elif isinstance(rec, dict):
                    actions.append(rec)
        return actions

    def get_alerts(self) -> list[dict]:
        """Extract alerts from the last result."""
        if not self.last_result:
            return []
        alerts = self.last_result.get("alerts", [])
        if not alerts:
            alerts = self.last_result.get("active_alerts", [])
        # Add anomalies as alerts too
        anomalies = self.last_result.get("anomalies", [])
        for anom in anomalies:
            if isinstance(anom, dict):
                alerts.append({
                    "severity": anom.get("severity", "medium"),
                    "message": anom.get("description", str(anom)),
                    "type": anom.get("type", "anomaly"),
                    "agent": self.name,
                })
        # Add misconfigurations as alerts
        misconfigs = self.last_result.get("misconfigurations", [])
        for mc in misconfigs:
            if isinstance(mc, dict):
                alerts.append({
                    "severity": mc.get("severity", "medium"),
                    "message": mc.get("description", str(mc)),
                    "type": "misconfiguration",
                    "agent": self.name,
                })
        # Add predictions as alerts
        predictions = self.last_result.get("predictions", [])
        for pred in predictions:
            if isinstance(pred, dict) and pred.get("risk") in ("high", "critical"):
                alerts.append({
                    "severity": pred.get("risk", "medium"),
                    "message": pred.get("predicted_issue", str(pred)),
                    "type": "prediction",
                    "agent": self.name,
                })
        return alerts

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Parse JSON from LLM response, stripping markdown code fences if present."""
        text = raw.strip()
        if text.startswith("```"):
            first_nl = text.index("\n")
            text = text[first_nl + 1:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip()
        return json.loads(text)

    async def analyze(self, context: dict) -> dict:
        data_str = self.collect_data(context)
        llm = get_llm_client()
        raw = await llm.chat(self.get_system_prompt(), data_str)
        self.run_count += 1
        self.last_run_time = time.time()
        try:
            result = self._extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            result = {"raw_analysis": raw}
        result["agent"] = self.name
        result["timestamp"] = self.last_run_time
        self.last_result = result
        # Track alerts
        alerts = self.get_alerts()
        if alerts:
            self._alert_history.extend(alerts)
            if len(self._alert_history) > 100:
                self._alert_history = self._alert_history[-50:]
        return result

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "run_count": self.run_count,
            "last_run_time": self.last_run_time,
            "has_result": self.last_result is not None,
            "alert_count": len(self._alert_history),
        }
