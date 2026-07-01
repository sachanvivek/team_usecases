import json
import requests
from src.agent_orch.agents.base_agent import BaseAgent
from src.agent_orch.utils.notifier import send_notification  # Slack/Email

class AlertingAgent(BaseAgent):
    def __init__(self, manual_confirm_api: str = None, send_notifications: bool = False):
        """
        manual_confirm_api: optional API endpoint that returns "yes" to proceed
        send_notifications: when True, send Slack/email alerts from this agent
        """
        super().__init__(name="AnomalyNotificationAgent")
        self.manual_confirm_api = manual_confirm_api
        self.send_notifications = send_notifications

    def run(self, state: dict) -> dict:
        # Validate required input
        self.validate_input(state, required_keys=["server_id", "resource_type"])

        anomalies = state.get("anomalies", [])
        server_id = state["server_id"]
        resource_type = state["resource_type"]

        # Build notification content
        message = {
            "server_id": server_id,
            "resource_type": resource_type,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies
        }

        # Log and optionally send notification
        self.logger.info(f"Anomaly Notification message: {json.dumps(message, indent=2)}")
        if self.send_notifications:
            try:
                send_notification(
                    title=f"[Anomaly Alert] {server_id} - {resource_type}",
                    message=json.dumps(message, indent=2),
                    level="warning"
                )
                state["notification_status"] = "sent"
            except Exception as e:
                self.logger.error(f"Notification sending failed: {e}")
                state["notification_status"] = f"failed: {str(e)}"

        # --- Manual approval via API ---
        proceed = True
        if self.manual_confirm_api:
            try:
                resp = requests.get(self.manual_confirm_api, timeout=30)
                proceed = resp.text.strip().lower() == "yes"
                state["manual_confirm_status"] = resp.text.strip()
            except Exception as e:
                proceed = False
                state["manual_confirm_status"] = f"error: {str(e)}"

        state["manual_proceed"] = proceed

        # Attach notification result to state
        self.attach_result(state, key="notification", value=message)
        return state
