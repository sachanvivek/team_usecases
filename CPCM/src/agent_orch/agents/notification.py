import json
import requests
from src.agent_orch.agents.base_agent import BaseAgent
from src.agent_orch.utils.notifier import send_notification

class NotificationAgent(BaseAgent):
    def __init__(self, manual_confirm_api: str = None):
        """
        manual_confirm_api: optional API endpoint that returns "yes" to proceed
        """
        super().__init__(name="NotificationAgent")
        self.manual_confirm_api = manual_confirm_api

    def run(self, state: dict) -> dict:
        # Validate required input
        self.validate_input(state, required_keys=["recommendation", "server_id", "resource_type"])

        recommendation = state["recommendation"]
        server_id = state["server_id"]
        resource_name = state["resource_type"]

        # Build notification content
        decision = recommendation.get("decision", "no_action")
        scale_percent = recommendation.get("scale_percent", 0)
        reason = recommendation.get("reason", "")

        message = {
            "server_id": server_id,
            "resource_name": resource_name,
            "decision": decision,
            "scale_percent": scale_percent,
            "reason": reason
        }

        # Send notification
        try:
            send_notification(
                title=f"[Rightsizing] {server_id} - {resource_name}",
                message=json.dumps(message, indent=2),
                level="info"
            )
            state["notification_status"] = "sent"
        except Exception as e:
            self.logger.error(f"Notification sending failed: {e}")
            state["notification_status"] = f"failed: {str(e)}"

        proceed = None
        if self.manual_confirm_api:
            try:
                resp = requests.get(self.manual_confirm_api, timeout=30)
                proceed = resp.text.strip().lower() == "yes"
                state["manual_confirm_status"] = resp.text.strip()
            except Exception as e:
                proceed = False
                state["manual_confirm_status"] = f"error: {str(e)}"

        if proceed is not None:
            state["manual_proceed"] = proceed
        self.attach_result(state, key="notification", value=message)
        return state

    def manual_approve(self, state: dict, server_id: str, resource_type: str, approval: str) -> dict:
        """
        Manual approval method called by API for manual scaling approval.
        """
        message = {
            "server_id": server_id,
            "resource_name": resource_type,
            "approval": approval,
        }

        # Log or notify approval
        try:
            send_notification(
                title=f"[Manual Approval] {server_id} - {resource_type}",
                message=json.dumps(message, indent=2),
                level="info"
            )
            state["manual_approval_notification_status"] = "sent"
        except Exception as e:
            state["manual_approval_notification_status"] = f"failed: {str(e)}"

        return state
