import httpx
import logging
import time
from typing import Optional
from config_loader import get_config

logger = logging.getLogger(__name__)


class ServiceNowClient:
    """Client for ServiceNow Change Request (CR) lifecycle management."""

    def __init__(self):
        cfg = get_config()
        self.instance = cfg.get("servicenow", "instance").rstrip("/")
        self.username = cfg.get("servicenow", "username")
        self.password = cfg.get("servicenow", "password")
        self.assignment_group = cfg.get("servicenow", "assignment_group", fallback="DNS Operations")
        self.category = cfg.get("servicenow", "category", fallback="Network")
        self.subcategory = cfg.get("servicenow", "subcategory", fallback="DNS")
        self.cr_type = cfg.get("servicenow", "cr_type", fallback="normal")
        self._table_api = f"{self.instance}/api/now/table"

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "Accept": "application/json"}

    def _auth(self) -> tuple:
        return (self.username, self.password)

    async def create_change_request(
        self,
        short_description: str,
        description: str,
        justification: str,
        implementation_plan: str,
        backout_plan: str,
        test_plan: str,
        risk: str = "moderate",
        impact: str = "2",
        priority: str = "3",
    ) -> dict:
        """Create a new Change Request in ServiceNow."""
        url = f"{self._table_api}/change_request"
        payload = {
            "short_description": short_description,
            "description": description,
            "justification": justification,
            "implementation_plan": implementation_plan,
            "backout_plan": backout_plan,
            "test_plan": test_plan,
            "type": self.cr_type,
            "category": self.category,
            "subcategory": self.subcategory,
            "assignment_group": self.assignment_group,
            "risk": risk,
            "impact": impact,
            "priority": priority,
            "state": "-5",  # New
        }
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.post(
                    url, json=payload, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})
                logger.info(f"CR created: {result.get('number')} (sys_id: {result.get('sys_id')})")
                return {
                    "success": True,
                    "sys_id": result.get("sys_id"),
                    "number": result.get("number"),
                    "state": result.get("state"),
                    "short_description": result.get("short_description"),
                }
        except Exception as e:
            logger.error(f"Failed to create CR: {e}")
            return {"success": False, "error": str(e)}

    async def get_change_request(self, sys_id: str) -> dict:
        """Get CR details by sys_id."""
        url = f"{self._table_api}/change_request/{sys_id}"
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.get(url, headers=self._headers(), auth=self._auth())
                resp.raise_for_status()
                result = resp.json().get("result", {})
                return {
                    "success": True,
                    "sys_id": result.get("sys_id"),
                    "number": result.get("number"),
                    "state": result.get("state"),
                    "approval": result.get("approval"),
                    "short_description": result.get("short_description"),
                    "close_code": result.get("close_code"),
                    "close_notes": result.get("close_notes"),
                }
        except Exception as e:
            logger.error(f"Failed to get CR {sys_id}: {e}")
            return {"success": False, "error": str(e)}

    async def get_cr_by_number(self, number: str) -> dict:
        """Get CR details by CHG number."""
        url = f"{self._table_api}/change_request?sysparm_query=number={number}&sysparm_limit=1"
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.get(url, headers=self._headers(), auth=self._auth())
                resp.raise_for_status()
                results = resp.json().get("result", [])
                if not results:
                    return {"success": False, "error": f"CR {number} not found"}
                r = results[0]
                return {
                    "success": True,
                    "sys_id": r.get("sys_id"),
                    "number": r.get("number"),
                    "state": r.get("state"),
                    "approval": r.get("approval"),
                    "short_description": r.get("short_description"),
                }
        except Exception as e:
            logger.error(f"Failed to get CR {number}: {e}")
            return {"success": False, "error": str(e)}

    async def check_approval_status(self, sys_id: str) -> dict:
        """Check if CR is approved."""
        cr = await self.get_change_request(sys_id)
        if not cr.get("success"):
            return cr
        approval = cr.get("approval", "")
        state = cr.get("state", "")
        # ServiceNow states: -5=New, -4=Assess, -3=Authorize, -2=Scheduled,
        # -1=Implement, 0=Review, 3=Closed, 4=Cancelled
        # Approval: not yet requested, requested, approved, rejected
        is_approved = approval == "approved" or state == "-1"
        return {
            "success": True,
            "sys_id": sys_id,
            "number": cr.get("number"),
            "state": state,
            "approval": approval,
            "is_approved": is_approved,
            "is_rejected": approval == "rejected" or state == "4",
        }

    async def move_to_implement(self, sys_id: str) -> dict:
        """Move CR to Implement state."""
        url = f"{self._table_api}/change_request/{sys_id}"
        payload = {"state": "-1"}  # Implement
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.patch(
                    url, json=payload, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})
                return {"success": True, "state": result.get("state"), "number": result.get("number")}
        except Exception as e:
            logger.error(f"Failed to move CR to implement: {e}")
            return {"success": False, "error": str(e)}

    async def move_to_review(self, sys_id: str) -> dict:
        """Move CR to Review state."""
        url = f"{self._table_api}/change_request/{sys_id}"
        payload = {"state": "0"}  # Review
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.patch(
                    url, json=payload, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})
                return {"success": True, "state": result.get("state"), "number": result.get("number")}
        except Exception as e:
            logger.error(f"Failed to move CR to review: {e}")
            return {"success": False, "error": str(e)}

    async def close_change_request(
        self, sys_id: str, close_code: str = "successful", close_notes: str = ""
    ) -> dict:
        """Close a CR with close code and notes."""
        url = f"{self._table_api}/change_request/{sys_id}"
        payload = {
            "state": "3",  # Closed
            "close_code": close_code,
            "close_notes": close_notes,
        }
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.patch(
                    url, json=payload, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})
                logger.info(f"CR {result.get('number')} closed: {close_code}")
                return {
                    "success": True,
                    "number": result.get("number"),
                    "state": result.get("state"),
                    "close_code": close_code,
                }
        except Exception as e:
            logger.error(f"Failed to close CR {sys_id}: {e}")
            return {"success": False, "error": str(e)}

    async def auto_approve_cr(self, sys_id: str) -> dict:
        """Auto-approve a CR by moving through all required states and approving approver records.
        Flow: New(-5) -> Assess(-4) -> Authorize(-3) -> approve approvers -> Scheduled(-2) -> Implement(-1)
        """
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                url = f"{self._table_api}/change_request/{sys_id}"
                # Move through states: -5 -> -4 -> -3
                for state in ["-4", "-3"]:
                    resp = await client.patch(
                        url, json={"state": state},
                        headers=self._headers(), auth=self._auth()
                    )
                    resp.raise_for_status()

                # Approve all sysapproval_approver records for this CR
                approver_url = (
                    f"{self._table_api}/sysapproval_approver"
                    f"?sysparm_query=sysapproval={sys_id}^state=requested"
                )
                resp = await client.get(
                    approver_url, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                approvers = resp.json().get("result", [])
                for approver in approvers:
                    aid = approver.get("sys_id")
                    if aid:
                        await client.patch(
                            f"{self._table_api}/sysapproval_approver/{aid}",
                            json={"state": "approved"},
                            headers=self._headers(), auth=self._auth()
                        )

                # Move to Scheduled(-2) then Implement(-1)
                for state in ["-2", "-1"]:
                    resp = await client.patch(
                        url, json={"state": state},
                        headers=self._headers(), auth=self._auth()
                    )
                    resp.raise_for_status()

                result = resp.json().get("result", {})
                logger.info(f"CR {result.get('number')} auto-approved and moved to Implement")
                return {
                    "success": True,
                    "number": result.get("number"),
                    "state": result.get("state"),
                }
        except Exception as e:
            logger.error(f"Auto-approve failed for {sys_id}: {e}")
            return {"success": False, "error": str(e)}

    async def resolve_incident(self, sys_id: str, close_notes: str = "",
                                close_code: str = "Solved (Permanently)") -> dict:
        """Resolve a ServiceNow incident."""
        url = f"{self._table_api}/incident/{sys_id}"
        payload = {
            "state": "6",  # Resolved
            "close_code": close_code,
            "close_notes": close_notes or "Auto-resolved by DNS AI Monitoring Platform",
        }
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.patch(
                    url, json=payload, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})
                logger.info(f"Incident {result.get('number')} resolved")
                return {"success": True, "number": result.get("number")}
        except Exception as e:
            logger.error(f"Failed to resolve incident {sys_id}: {e}")
            return {"success": False, "error": str(e)}

    async def add_work_note(self, sys_id: str, note: str) -> dict:
        """Add a work note to the CR."""
        url = f"{self._table_api}/change_request/{sys_id}"
        payload = {"work_notes": note}
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.patch(
                    url, json=payload, headers=self._headers(), auth=self._auth()
                )
                resp.raise_for_status()
                return {"success": True}
        except Exception as e:
            logger.error(f"Failed to add work note: {e}")
            return {"success": False, "error": str(e)}
