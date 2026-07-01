import configparser
import time
import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class ServiceNowChangeRequestClient:
    """Client for creating and managing ServiceNow Change Requests for VM resize operations."""

    def __init__(self, config_path: str = "src/agent_orch/utils/config.ini"):
        config = configparser.ConfigParser()
        config.read(config_path)

        self.enabled = config.getboolean("SERVICENOW", "enabled", fallback=False)
        self.instance_url = config.get("SERVICENOW", "instance_url", fallback="").rstrip("/")
        self.username = config.get("SERVICENOW", "username", fallback="")
        self.password = config.get("SERVICENOW", "password", fallback="")
        self.verify_ssl = config.getboolean("SERVICENOW", "verify_ssl", fallback=True)
        self.timeout = config.getint("SERVICENOW", "timeout", fallback=30)

        # Change Request specific config
        self.cr_api_path = config.get(
            "SERVICENOW_CHANGE_REQUEST", "api_path",
            fallback="/api/now/table/change_request",
        )
        self.default_type = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_type", fallback="normal",
        )
        self.default_risk = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_risk", fallback="moderate",
        )
        self.default_impact = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_impact", fallback="3",
        )
        self.default_priority = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_priority", fallback="3",
        )
        self.default_category = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_category", fallback="Hardware",
        )
        self.default_assignment_group = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_assignment_group", fallback="",
        )
        self.default_requested_by = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_requested_by", fallback="",
        )
        self.default_cmdb_ci = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_cmdb_ci", fallback="",
        )

        # Change Request state mapping
        self.state_new = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_new", fallback="-5",
        )
        self.state_assess = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_assess", fallback="-4",
        )
        self.state_authorize = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_authorize", fallback="-3",
        )
        self.state_scheduled = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_scheduled", fallback="-2",
        )
        self.state_implement = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_implement", fallback="-1",
        )
        self.state_review = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_review", fallback="0",
        )
        self.state_closed = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_closed", fallback="3",
        )
        self.state_cancelled = config.get(
            "SERVICENOW_CHANGE_REQUEST", "state_cancelled", fallback="4",
        )

        self.auto_close_on_success = config.getboolean(
            "SERVICENOW_CHANGE_REQUEST", "auto_close_on_success", fallback=True,
        )
        self.default_close_code = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_close_code", fallback="successful",
        )
        self.default_close_notes = config.get(
            "SERVICENOW_CHANGE_REQUEST", "default_close_notes",
            fallback="Change completed successfully by automated agent.",
        )

        # Approval polling settings
        self.approval_poll_interval = config.getint(
            "SERVICENOW_CHANGE_REQUEST", "approval_poll_interval", fallback=30,
        )
        self.approval_poll_timeout = config.getint(
            "SERVICENOW_CHANGE_REQUEST", "approval_poll_timeout", fallback=3600,
        )
        # States considered "approved" (Scheduled or later, but before Closed)
        self.approved_states = [
            self.state_scheduled,   # -2
            self.state_implement,   # -1
            self.state_review,      # 0
        ]

    # ------------------------------------------------------------------ helpers
    def _base_response(self, status: bool, message: str, **extra: Any) -> Dict[str, Any]:
        response = {"status": status, "message": message}
        response.update(extra)
        return response

    def _validate(self) -> Optional[str]:
        if not self.enabled:
            return "ServiceNow integration is disabled."
        if not self.instance_url or not self.username or not self.password:
            return "ServiceNow configuration is incomplete."
        return None

    @property
    def change_request_endpoint(self) -> str:
        return f"{self.instance_url}{self.cr_api_path}"

    def _patch_change_request(
        self, change_sys_id: str, payload: Dict[str, Any]
    ) -> requests.Response:
        response = requests.patch(
            f"{self.change_request_endpoint}/{change_sys_id}",
            auth=(self.username, self.password),
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response

    def _add_work_note(self, change_sys_id: str, work_note: str) -> Dict[str, Any]:
        """Add a work note to a Change Request via the work_notes field.

        Tries PATCH with only work_notes (no state change) which is more
        likely to succeed even when state-transition PATCHes are blocked.
        If that also fails, logs the failure and returns status info.
        """
        payload = {"work_notes": work_note}
        try:
            response = self._patch_change_request(
                change_sys_id=change_sys_id, payload=payload
            )
            result = response.json().get("result", {})
            return self._base_response(
                True,
                "Work note added to Change Request.",
                change_sys_id=result.get("sys_id", change_sys_id),
                change_number=result.get("number"),
            )
        except Exception as exc:
            return self._base_response(
                False,
                f"Failed to add work note to Change Request: {exc}",
                change_sys_id=change_sys_id,
            )

    def _transition_through_states(
        self, change_sys_id: str, target_state: str, work_notes: str = ""
    ) -> Dict[str, Any]:
        """Walk the Change Request through each required state up to target_state.

        ServiceNow enforces the state model:
          New(-5) → Assess(-4) → Authorize(-3) → Scheduled(-2) → Implement(-1)
          → Review(0) → Closed(3)

        A direct jump (e.g. New → Implement) returns 403. This method steps
        through each intermediate state so the transition is accepted.
        """
        ordered_states = [
            self.state_new,       # -5
            self.state_assess,    # -4
            self.state_authorize, # -3
            self.state_scheduled, # -2
            self.state_implement, # -1
            self.state_review,    # 0
            self.state_closed,    # 3
        ]

        # Find the index of the target state
        try:
            target_idx = ordered_states.index(str(target_state))
        except ValueError:
            # Target state not in the standard ordered list; attempt direct update
            return self.update_change_request(
                change_sys_id=change_sys_id,
                work_notes=work_notes,
                state=target_state,
            )

        # Get current state of the CR to know where to start
        current_idx = 0  # default: start from New
        try:
            get_resp = requests.get(
                f"{self.change_request_endpoint}/{change_sys_id}",
                auth=(self.username, self.password),
                headers={"Accept": "application/json"},
                params={"sysparm_fields": "state"},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            if get_resp.status_code == 200:
                current_state = get_resp.json().get("result", {}).get("state", self.state_new)
                if str(current_state) in ordered_states:
                    current_idx = ordered_states.index(str(current_state))
        except Exception:
            pass  # fallback: assume we are at New

        if current_idx >= target_idx:
            # Already at or past the target state; just add work notes if any
            if work_notes:
                return self._add_work_note(change_sys_id, work_notes)
            return self._base_response(
                True,
                f"Change Request already at or past target state ({target_state}).",
                change_sys_id=change_sys_id,
            )

        last_result: Dict[str, Any] = {}
        # Step through each intermediate state
        for idx in range(current_idx + 1, target_idx + 1):
            next_state = ordered_states[idx]
            # Only attach work_notes on the final transition
            notes = work_notes if idx == target_idx else f"Auto-transitioning to state {next_state}."
            payload: Dict[str, Any] = {"state": next_state, "work_notes": notes}

            # For the final Closed state, include close_code and close_notes
            if next_state == self.state_closed:
                payload["close_code"] = self.default_close_code
                payload["close_notes"] = self.default_close_notes

            try:
                response = self._patch_change_request(
                    change_sys_id=change_sys_id, payload=payload
                )
                result = response.json().get("result", {})
                last_result = self._base_response(
                    True,
                    f"Change Request transitioned to state {next_state}.",
                    change_sys_id=result.get("sys_id", change_sys_id),
                    change_number=result.get("number"),
                    state=result.get("state"),
                )
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                # If 403, try adding work notes as a fallback and stop transitions
                if status_code == 403:
                    fallback = self._add_work_note(change_sys_id, work_notes) if work_notes else {}
                    return self._base_response(
                        False,
                        (
                            f"Permission denied transitioning CR to state {next_state}. "
                            f"Work note added: {fallback.get('status', False)}."
                        ),
                        change_sys_id=change_sys_id,
                        status_code=status_code,
                        permission_denied=True,
                        work_note_fallback=fallback,
                    )
                return self._base_response(
                    False,
                    f"Failed to transition CR to state {next_state}: {exc}",
                    change_sys_id=change_sys_id,
                    status_code=status_code,
                )
            except Exception as exc:
                return self._base_response(
                    False,
                    f"Failed to transition CR to state {next_state}: {exc}",
                    change_sys_id=change_sys_id,
                )

        return last_result or self._base_response(
            True, "State transitions completed.", change_sys_id=change_sys_id
        )

    # ------------------------------------------------ state query & approval
    def get_change_request_state(
        self, change_sys_id: str
    ) -> Dict[str, Any]:
        """Fetch the current state and approval status of a Change Request."""
        validation_error = self._validate()
        if validation_error:
            return self._base_response(False, validation_error)

        try:
            resp = requests.get(
                f"{self.change_request_endpoint}/{change_sys_id}",
                auth=(self.username, self.password),
                headers={"Accept": "application/json"},
                params={"sysparm_fields": "state,number,approval,short_description"},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
            return self._base_response(
                True,
                "Change Request state retrieved.",
                change_sys_id=change_sys_id,
                change_number=result.get("number"),
                state=result.get("state"),
                approval=result.get("approval"),
            )
        except Exception as exc:
            return self._base_response(
                False,
                f"Failed to get Change Request state: {exc}",
                change_sys_id=change_sys_id,
            )

    def is_cr_approved(self, change_sys_id: str) -> Dict[str, Any]:
        """Check whether the CR has been approved (reached Scheduled state or later)."""
        state_result = self.get_change_request_state(change_sys_id)
        if not state_result.get("status"):
            return self._base_response(
                False,
                f"Cannot determine approval: {state_result.get('message')}",
                change_sys_id=change_sys_id,
            )

        current_state = str(state_result.get("state", ""))
        approval_field = str(state_result.get("approval", "")).lower()
        approved = current_state in self.approved_states or approval_field == "approved"

        # Also consider Closed/Cancelled as terminal (not waiting)
        cancelled = current_state in [self.state_closed, self.state_cancelled]

        return self._base_response(
            True,
            "Approval status checked.",
            change_sys_id=change_sys_id,
            change_number=state_result.get("change_number"),
            current_state=current_state,
            approval_field=approval_field,
            approved=approved,
            cancelled=cancelled,
        )

    def wait_for_cr_approval(
        self,
        change_sys_id: str,
        change_number: str = "",
        poll_interval: Optional[int] = None,
        poll_timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Poll ServiceNow until the Change Request is approved or times out.

        Returns a dict with:
          - approved: True if CR reached an approved state
          - timed_out: True if polling exceeded the timeout
          - cancelled: True if CR was cancelled/closed before approval
        """
        interval = poll_interval or self.approval_poll_interval
        timeout = poll_timeout or self.approval_poll_timeout
        cr_label = change_number or change_sys_id

        logger.info(
            f"Waiting for CR {cr_label} approval "
            f"(poll every {interval}s, timeout {timeout}s)..."
        )

        elapsed = 0
        while elapsed < timeout:
            check = self.is_cr_approved(change_sys_id)
            if not check.get("status"):
                logger.warning(f"Polling error for CR {cr_label}: {check.get('message')}")
                # Keep trying; transient network errors should not abort
                time.sleep(interval)
                elapsed += interval
                continue

            if check.get("approved"):
                logger.info(f"CR {cr_label} approved (state={check.get('current_state')}).")
                return self._base_response(
                    True,
                    f"Change Request {cr_label} approved.",
                    change_sys_id=change_sys_id,
                    change_number=change_number,
                    approved=True,
                    timed_out=False,
                    cancelled=False,
                    current_state=check.get("current_state"),
                )

            if check.get("cancelled"):
                logger.warning(f"CR {cr_label} was cancelled/closed before approval.")
                return self._base_response(
                    False,
                    f"Change Request {cr_label} was cancelled or closed before approval.",
                    change_sys_id=change_sys_id,
                    change_number=change_number,
                    approved=False,
                    timed_out=False,
                    cancelled=True,
                    current_state=check.get("current_state"),
                )

            logger.info(
                f"CR {cr_label} not yet approved (state={check.get('current_state')}). "
                f"Elapsed {elapsed}s / {timeout}s. Retrying in {interval}s..."
            )
            time.sleep(interval)
            elapsed += interval

        # Timed out
        logger.warning(f"CR {cr_label} approval timed out after {timeout}s.")
        return self._base_response(
            False,
            f"Change Request {cr_label} approval timed out after {timeout}s.",
            change_sys_id=change_sys_id,
            change_number=change_number,
            approved=False,
            timed_out=True,
            cancelled=False,
        )

    def submit_for_approval(
        self, change_sys_id: str, work_notes: str = ""
    ) -> Dict[str, Any]:
        """Move CR from New to Assess state so it enters the approval workflow."""
        return self._transition_through_states(
            change_sys_id=change_sys_id,
            target_state=self.state_assess,
            work_notes=work_notes or "Change Request submitted for approval.",
        )

    # --------------------------------------------------------- public methods
    def create_change_request(
        self,
        short_description: str,
        description: str,
        justification: str = "",
        implementation_plan: str = "",
        risk_impact_analysis: str = "",
        backout_plan: str = "",
        test_plan: str = "",
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a ServiceNow Change Request for a planned VM resize."""
        validation_error = self._validate()
        if validation_error:
            return self._base_response(False, validation_error)

        payload: Dict[str, Any] = {
            "short_description": short_description,
            "description": description,
            "type": self.default_type,
            "risk": self.default_risk,
            "impact": self.default_impact,
            "priority": self.default_priority,
            "category": self.default_category,
            "state": self.state_new,
        }

        if justification:
            payload["justification"] = justification
        if implementation_plan:
            payload["implementation_plan"] = implementation_plan
        if risk_impact_analysis:
            payload["risk_impact_analysis"] = risk_impact_analysis
        if backout_plan:
            payload["backout_plan"] = backout_plan
        if test_plan:
            payload["test_plan"] = test_plan

        if self.default_requested_by:
            payload["requested_by"] = self.default_requested_by
        if self.default_assignment_group:
            payload["assignment_group"] = self.default_assignment_group
        if self.default_cmdb_ci:
            payload["cmdb_ci"] = self.default_cmdb_ci

        if extra_fields:
            payload.update(extra_fields)

        try:
            response = requests.post(
                self.change_request_endpoint,
                auth=(self.username, self.password),
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            result = response.json().get("result", {})

            return self._base_response(
                True,
                "ServiceNow Change Request created successfully.",
                change_sys_id=result.get("sys_id"),
                change_number=result.get("number"),
            )
        except Exception as exc:
            return self._base_response(
                False, f"Failed to create ServiceNow Change Request: {exc}"
            )

    def update_change_request(
        self,
        change_sys_id: str,
        work_notes: str,
        state: Optional[str] = None,
        close_code: Optional[str] = None,
        close_notes: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing Change Request with work notes and optional state transition."""
        validation_error = self._validate()
        if validation_error:
            return self._base_response(False, validation_error)

        if not change_sys_id:
            return self._base_response(False, "Missing change_sys_id for ServiceNow update.")

        payload: Dict[str, Any] = {"work_notes": work_notes}
        if state:
            payload["state"] = state
        if close_code:
            payload["close_code"] = close_code
        if close_notes:
            payload["close_notes"] = close_notes
        if extra_fields:
            payload.update(extra_fields)

        try:
            response = self._patch_change_request(
                change_sys_id=change_sys_id, payload=payload
            )
            result = response.json().get("result", {})
            return self._base_response(
                True,
                "ServiceNow Change Request updated successfully.",
                change_sys_id=result.get("sys_id", change_sys_id),
                change_number=result.get("number"),
                state=result.get("state"),
            )
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            return self._base_response(
                False,
                f"Failed to update ServiceNow Change Request: {exc}",
                change_sys_id=change_sys_id,
                status_code=status_code,
            )
        except Exception as exc:
            return self._base_response(
                False,
                f"Failed to update ServiceNow Change Request: {exc}",
                change_sys_id=change_sys_id,
            )

    def move_to_implement(self, change_sys_id: str, work_notes: str = "") -> Dict[str, Any]:
        """Transition the Change Request to Implement state, stepping through required states."""
        return self._transition_through_states(
            change_sys_id=change_sys_id,
            target_state=self.state_implement,
            work_notes=work_notes or "Moving to Implement state. VM resize operation starting.",
        )

    def close_change_request(
        self,
        change_sys_id: str,
        close_code: Optional[str] = None,
        close_notes: Optional[str] = None,
        work_notes: str = "",
    ) -> Dict[str, Any]:
        """Close the Change Request by walking through all required states up to Closed."""
        # Store close_code/close_notes so _transition_through_states uses them on the final step
        if close_code:
            self.default_close_code = close_code
        if close_notes:
            self.default_close_notes = close_notes

        return self._transition_through_states(
            change_sys_id=change_sys_id,
            target_state=self.state_closed,
            work_notes=work_notes or "Change Request closed. VM resize completed.",
        )

    def cancel_change_request(
        self,
        change_sys_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Cancel the Change Request if resize fails or is aborted."""
        return self.update_change_request(
            change_sys_id=change_sys_id,
            work_notes=reason or "Change Request cancelled due to resize failure.",
            state=self.state_cancelled,
        )

    def update_change_after_resize(
        self,
        change_sys_id: str,
        resize_status: bool,
        resize_message: str,
        work_note: str,
    ) -> Dict[str, Any]:
        """
        Post-resize handler: transition to Closed on success (walking through
        each state), or add work notes as fallback if transitions are blocked.
        """
        if resize_status and self.auto_close_on_success:
            close_result = self.close_change_request(
                change_sys_id=change_sys_id,
                close_code=self.default_close_code,
                close_notes=resize_message or self.default_close_notes,
                work_notes=work_note,
            )
            if close_result.get("status"):
                return close_result

            # If state transitions failed (e.g. 403), fallback to just adding work notes
            if close_result.get("permission_denied"):
                fallback = self._add_work_note(change_sys_id, work_note)
                return self._base_response(
                    fallback.get("status", False),
                    (
                        "CR state transitions blocked (403). "
                        f"Work note fallback: {fallback.get('message', 'N/A')}."
                    ),
                    change_sys_id=change_sys_id,
                    permission_denied=True,
                    work_note_fallback=fallback,
                )

        # Resize failed or auto-close disabled — try moving to Implement, fallback to work notes
        implement_result = self._transition_through_states(
            change_sys_id=change_sys_id,
            target_state=self.state_implement,
            work_notes=work_note,
        )
        if implement_result.get("status"):
            return implement_result

        # Final fallback: just add work notes
        return self._add_work_note(change_sys_id, work_note)


class ServiceNowIncidentClient:
    def __init__(self, config_path: str = "src/agent_orch/utils/config.ini"):
        config = configparser.ConfigParser()
        config.read(config_path)

        self.enabled = config.getboolean("SERVICENOW", "enabled", fallback=False)
        self.instance_url = config.get("SERVICENOW", "instance_url", fallback="").rstrip("/")
        self.username = config.get("SERVICENOW", "username", fallback="")
        self.password = config.get("SERVICENOW", "password", fallback="")
        self.api_path = config.get("SERVICENOW", "api_path", fallback="/api/now/table/incident")
        self.verify_ssl = config.getboolean("SERVICENOW", "verify_ssl", fallback=True)
        self.timeout = config.getint("SERVICENOW", "timeout", fallback=30)

        self.default_caller = config.get("SERVICENOW", "default_caller", fallback="")
        self.default_urgency = config.get("SERVICENOW", "default_urgency", fallback="3")
        self.default_impact = config.get("SERVICENOW", "default_impact", fallback="3")
        self.default_category = config.get("SERVICENOW", "default_category", fallback="inquiry")
        self.default_subcategory = config.get("SERVICENOW", "default_subcategory", fallback="performance")
        self.default_assignment_group = config.get("SERVICENOW", "default_assignment_group", fallback="")
        self.default_cmdb_ci = config.get("SERVICENOW", "default_cmdb_ci", fallback="")

        self.auto_resolve_on_success = config.getboolean(
            "SERVICENOW", "auto_resolve_on_success", fallback=True
        )
        self.resolved_state = config.get("SERVICENOW", "resolved_state", fallback="6")
        self.in_progress_state = config.get("SERVICENOW", "in_progress_state", fallback="2")
        self.default_close_code = config.get(
            "SERVICENOW", "default_close_code", fallback="Solved (Permanently)"
        )
        self.default_close_notes = config.get(
            "SERVICENOW", "default_close_notes", fallback="Resolution notes"
        )
        self.resolution_code_field = config.get(
            "SERVICENOW", "resolution_code_field", fallback="close_code"
        )
        self.default_resolution_code = config.get(
            "SERVICENOW", "default_resolution_code", fallback=self.default_close_code
        )
        self.resolution_notes_field = config.get(
            "SERVICENOW", "resolution_notes_field", fallback="close_notes"
        )
        self.default_resolution_notes = config.get(
            "SERVICENOW", "default_resolution_notes", fallback=self.default_close_notes
        )
        self.resolution_state = config.get(
            "SERVICENOW", "resolution_state", fallback=self.resolved_state
        )

    def _base_response(self, status: bool, message: str, **extra: Any) -> Dict[str, Any]:
        response = {"status": status, "message": message}
        response.update(extra)
        return response

    def _validate(self) -> Optional[str]:
        if not self.enabled:
            return "ServiceNow integration is disabled."
        if not self.instance_url or not self.username or not self.password:
            return "ServiceNow configuration is incomplete."
        return None

    @property
    def incident_endpoint(self) -> str:
        return f"{self.instance_url}{self.api_path}"

    def _patch_incident(self, incident_sys_id: str, payload: Dict[str, Any]) -> requests.Response:
        response = requests.patch(
            f"{self.incident_endpoint}/{incident_sys_id}",
            auth=(self.username, self.password),
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response

    def update_incident(
        self,
        incident_sys_id: str,
        work_notes: str,
        state: Optional[str] = None,
        close_code: Optional[str] = None,
        close_notes: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        validation_error = self._validate()
        if validation_error:
            return self._base_response(False, validation_error)

        if not incident_sys_id:
            return self._base_response(False, "Missing incident sys_id for ServiceNow update.")

        payload: Dict[str, Any] = {"work_notes": work_notes}
        if state:
            payload["state"] = state
        if close_code:
            payload["close_code"] = close_code
        if close_notes:
            payload["close_notes"] = close_notes
        if extra_fields:
            payload.update(extra_fields)

        try:
            response = self._patch_incident(incident_sys_id=incident_sys_id, payload=payload)
            result = response.json().get("result", {})
            return self._base_response(
                True,
                "ServiceNow incident updated successfully.",
                incident_sys_id=result.get("sys_id", incident_sys_id),
                incident_number=result.get("number"),
                state=result.get("state"),
                close_code=result.get("close_code"),
            )
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None

            if status_code == 403:
                return self._base_response(
                    False,
                    "Credentials lack permission to update incidents. Ticket created but not auto-resolved.",
                    incident_sys_id=incident_sys_id,
                    status_code=status_code,
                    permission_denied=True,
                )

            return self._base_response(
                False,
                f"Failed to update ServiceNow incident: {exc}",
                incident_sys_id=incident_sys_id,
                status_code=status_code,
            )
        except Exception as exc:
            return self._base_response(
                False,
                f"Failed to update ServiceNow incident: {exc}",
                incident_sys_id=incident_sys_id,
            )

    def resolve_incident(self, incident_sys_id: str, resolution: str) -> Dict[str, Any]:
        close_code = self.default_close_code or "Resolved"
        close_notes = self.default_close_notes or resolution
        resolution_code = self.default_resolution_code or close_code
        resolution_notes = self.default_resolution_notes or close_notes
        state = self.resolution_state or self.resolved_state

        work_notes = f"Automated resolution: {resolution}"

        extra_fields: Dict[str, Any] = {}
        if self.resolution_code_field and self.resolution_code_field != "close_code":
            extra_fields[self.resolution_code_field] = resolution_code
        if self.resolution_notes_field and self.resolution_notes_field != "close_notes":
            extra_fields[self.resolution_notes_field] = resolution_notes

        return self.update_incident(
            incident_sys_id=incident_sys_id,
            work_notes=work_notes,
            state=state,
            close_code=close_code if state == "7" else None,
            close_notes=close_notes,
            extra_fields=extra_fields or None,
        )

    def create_incident(self, short_description: str, description: str) -> Dict[str, Any]:
        validation_error = self._validate()
        if validation_error:
            return self._base_response(False, validation_error)

        payload: Dict[str, Any] = {
            "short_description": short_description,
            "description": description,
            "urgency": self.default_urgency,
            "impact": self.default_impact,
            "category": self.default_category,
            "subcategory": self.default_subcategory,
        }

        if self.default_caller:
            payload["caller_id"] = self.default_caller
        if self.default_assignment_group:
            payload["assignment_group"] = self.default_assignment_group
        if self.default_cmdb_ci:
            payload["cmdb_ci"] = self.default_cmdb_ci

        try:
            response = requests.post(
                self.incident_endpoint,
                auth=(self.username, self.password),
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            result = response.json().get("result", {})

            return self._base_response(
                True,
                "ServiceNow incident created successfully.",
                incident_sys_id=result.get("sys_id"),
                incident_number=result.get("number"),
            )
        except Exception as exc:
            return self._base_response(False, f"Failed to create ServiceNow incident: {exc}")

    def update_incident_after_resize(
        self,
        incident_sys_id: str,
        resize_status: bool,
        resize_message: str,
        work_note: str,
    ) -> Dict[str, Any]:
        if resize_status and self.auto_resolve_on_success:
            resolution_text = resize_message or self.default_resolution_notes
            resolve_result = self.resolve_incident(
                incident_sys_id=incident_sys_id,
                resolution=resolution_text,
            )

            if resolve_result.get("status"):
                return resolve_result

            if resolve_result.get("permission_denied"):
                fallback_fields: Dict[str, Any] = {"comments": resize_message} if resize_message else {}
                return self.update_incident(
                    incident_sys_id=incident_sys_id,
                    work_notes=work_note,
                    extra_fields=fallback_fields or None,
                )

            return resolve_result

        extra_fields = {"comments": resize_message} if resize_message else None
        return self.update_incident(
            incident_sys_id=incident_sys_id,
            work_notes=work_note,
            state=self.in_progress_state,
            extra_fields=extra_fields,
        )
