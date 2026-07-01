from src.agent_orch.agents.base_agent import BaseAgent
from src.agent_orch.agents.azure_vm_resize import resize_azure_vm
from src.agent_orch.agents.hyperv_vm_resize import resize_hyperv_vm, _load_hyperv_config
from src.agent_orch.utils.servicenow import ServiceNowChangeRequestClient
from src.agent_orch.utils.DBConnect import DBConnect
import numpy as np

def convert_numpy_types(obj):
    """Recursively convert non-serializable types to JSON-serializable types."""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif hasattr(obj, '__dict__'):
        return convert_numpy_types(obj.__dict__)
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)

class ScalingAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="ScalingAgent")
        self.servicenow = ServiceNowChangeRequestClient()
        self.db = DBConnect()

    def _get_vm_details(self, vm_id):
        """Fetch and validate VM details from vm_details table.
        Returns (vm_detail_dict, error_message) — error_message is None on success.
        """
        vm_detail_rows = self.db.select(
            raw_query=(
                "SELECT hypervisor, VM_name, resize_type "
                "FROM vm_details WHERE vm_id = %s"
            ),
            params=(vm_id,),
        )

        if not vm_detail_rows:
            return None, f"No VM details found for vm_id={vm_id}."

        vm_detail = vm_detail_rows[0]
        hypervisor = (vm_detail.get("hypervisor") or "").strip()
        resize_type = (vm_detail.get("resize_type") or "").strip()
        vm_name = (vm_detail.get("VM_name") or "").strip()

        """if hypervisor.lower() != "azure":
            return None, (
                f"Hypervisor is '{hypervisor}' (not Azure) for vm_id={vm_id}. "
                "Auto resize is only supported on Azure."
            )"""
        if resize_type.lower() != "auto":
            return None, (
                f"Resize type is '{resize_type}' (not Auto) for vm_id={vm_id}. "
                "Only Auto resize VMs are eligible for automated scaling."
            )
        if not vm_name:
            return None, f"VM_name is empty for vm_id={vm_id}."

        return {"hypervisor": hypervisor, "resize_type": resize_type, "vm_name": vm_name}, None

    def _get_azure_config(self):
        """Return Azure credentials and defaults."""
        return {
            "tenant_id": "cd9d7bf9-b9a3-4ad2-bd6b-1939a1b0a5c4",
            "client_id": "5b788335-5a60-4a8d-9372-521f904e9530",
            "client_secret": "mDb8Q~vGw6Ke0UHLHtDglUMMuSeHy323kemsqcFP",
            "subscription_id": "ee43fc90-3743-4416-b125-2bbf485d758b",
            "resource_group": "MFG_ITIS_ITOPS_EntNetworks",
            "suggested_size": "Standard_B1ms",
        }

    def _get_hyperv_config(self):
        """Return Hyper-V host config from config.ini."""
        return _load_hyperv_config()

    # -----------------------------------------------------------------
    # Step 1: Create CR (called from /create_cr API)
    # -----------------------------------------------------------------
    def create_change_request(self, state: dict) -> dict:
        """Create a ServiceNow Change Request and submit it for approval.
        Does NOT wait for approval or perform the resize.
        """
        recommendation = state.get("recommendation", {})
        decision = recommendation.get("decision", "unknown")
        vm_id = state.get("server_id")

        vm_info, error = self._get_vm_details(vm_id)
        if error:
            state["feedback"] = f"[ScalingAgent] {error} Skipping CR creation."
            state["cr_created"] = False
            return state

        vm_name = vm_info["vm_name"]
        hypervisor = vm_info["hypervisor"]

        # Build description fields based on hypervisor type
        if hypervisor.lower() == "hyperv":
            hv_cfg = self._get_hyperv_config()
            resize_target = f"RAM -> {hv_cfg['default_ram_mb']} MB"
            action_label = "Hyper-V VM RAM resize"
            impl_plan = (
                "1. Connect to Hyper-V host via WinRM\n"
                "2. Capture running services (pre-resize check)\n"
                "3. Stop (shut down) VM\n"
                "4. Set new RAM value\n"
                "5. Start VM\n"
                "6. Verify all services are running (post-resize check)"
            )
        else:
            azure = self._get_azure_config()
            resize_target = f"Size -> {azure['suggested_size']}"
            action_label = "Azure VM resize"
            impl_plan = (
                "1. Capture running services (pre-resize check)\n"
                "2. Deallocate VM\n"
                "3. Apply new VM size\n"
                "4. Start VM\n"
                "5. Verify all services are running (post-resize check)"
            )

        services_to_check = (
            state.get("apps_to_check")
            or state.get("services_to_check")
            or recommendation.get("apps_to_check")
            or recommendation.get("services_to_check")
            or []
        )
        requested_services = [
            str(s).strip() for s in services_to_check if str(s).strip()
        ]
        requested_service_list = ", ".join(requested_services) if requested_services else "None provided"

        change_create_result = self.servicenow.create_change_request(
            short_description=f"VM Resize Change Request for {vm_name}",
            description=(
                f"Server ID: {vm_id}\n"
                f"Hypervisor: {hypervisor}\n"
                f"Decision: {decision}\n"
                f"Requested Resize: {vm_name} {resize_target}\n"
                "Precheck Scope: all running services/apps on VM\n"
                f"Requested app/service list: {requested_service_list}\n"
                "Post-check Scope: same as precheck running services (plus requested list, if any)\n"
                f"Action: {action_label} initiated by ScalingAgent."
            ),
            justification=(
                f"Rightsizing analysis recommends '{decision}' for VM {vm_name}. "
                f"Forecasted resource utilization requires resizing ({resize_target})."
            ),
            implementation_plan=impl_plan,
            risk_impact_analysis=(
                "Risk: Moderate - VM will be temporarily unavailable during resize.\n"
                "Impact: Services on the VM will experience downtime during the operation.\n"
                "Mitigation: Pre/post service checks ensure all services are restored."
            ),
            backout_plan=(
                f"Revert VM {vm_name} to its original size if post-resize "
                "service checks fail or critical services do not start."
            ),
            test_plan=(
                "Post-resize validation:\n"
                "1. Verify VM is running\n"
                "2. Check all pre-resize services are active\n"
                "3. Validate requested app/service list status"
            ),
        )

        change_sys_id = change_create_result.get("change_sys_id")
        change_number = change_create_result.get("change_number")

        if not change_create_result.get("status"):
            state["feedback"] = (
                f"[ScalingAgent] CR creation failed: {change_create_result.get('message')}"
            )
            state["cr_created"] = False
            return state

        # Submit for approval (New → Assess)
        submit_result = self.servicenow.submit_for_approval(
            change_sys_id=change_sys_id,
            work_notes=(
                f"Submitting VM resize CR for approval: {vm_name} {resize_target}. "
                f"Requested services to check: {requested_service_list}."
            ),
        )

        state["cr_created"] = True
        state["change_sys_id"] = change_sys_id
        state["change_number"] = change_number
        state["vm_name"] = vm_name
        state["feedback"] = (
            f"[ScalingAgent] Change Request {change_number} created and submitted for approval."
        )
        if not submit_result.get("status"):
            state["feedback"] += (
                f" (Warning: submit for approval issue: {submit_result.get('message')})"
            )
        return state

    # -----------------------------------------------------------------
    # Step 2: Check CR approval (called from /check_cr_approval API)
    # -----------------------------------------------------------------
    def check_cr_approval(self, state: dict) -> dict:
        """Check whether the CR has been approved in ServiceNow."""
        change_sys_id = state.get("change_sys_id")
        change_number = state.get("change_number", "")

        if not change_sys_id:
            state["cr_approved"] = False
            state["cr_approval_message"] = "No Change Request found. Create one first."
            return state

        check = self.servicenow.is_cr_approved(change_sys_id)

        if not check.get("status"):
            state["cr_approved"] = False
            state["cr_approval_message"] = f"Cannot check approval: {check.get('message')}"
            return state

        if check.get("cancelled"):
            state["cr_approved"] = False
            state["cr_cancelled"] = True
            state["cr_approval_message"] = f"CR {change_number} was cancelled or closed."
            return state

        if check.get("approved"):
            state["cr_approved"] = True
            state["cr_approval_message"] = f"CR {change_number} is approved."
        else:
            state["cr_approved"] = False
            state["cr_approval_message"] = (
                f"CR {change_number} is still pending approval "
                f"(current state: {check.get('current_state')})."
            )

        return state

    # -----------------------------------------------------------------
    # Step 3: Implement (called from /implement_resize API)
    # -----------------------------------------------------------------
    def implement_resize(self, state: dict) -> dict:
        """Move CR to Implement, perform VM resize (Azure or Hyper-V), close/update the CR."""
        change_sys_id = state.get("change_sys_id")
        change_number = state.get("change_number", "")
        vm_id = state.get("server_id")
        recommendation = state.get("recommendation", {})
        decision = recommendation.get("decision", "unknown")

        vm_info, error = self._get_vm_details(vm_id)
        if error:
            state["feedback"] = f"[ScalingAgent] {error} Skipping resize."
            state["scaling_executed"] = False
            return state

        vm_name = vm_info["vm_name"]
        hypervisor = vm_info["hypervisor"]

        services_to_check = (
            state.get("apps_to_check")
            or state.get("services_to_check")
            or recommendation.get("apps_to_check")
            or recommendation.get("services_to_check")
            or []
        )
        requested_services = [
            str(s).strip() for s in services_to_check if str(s).strip()
        ]
        requested_service_list = ", ".join(requested_services) if requested_services else "None provided"

        # Determine resize target label for feedback
        if hypervisor.lower() == "hyperv":
            hv_cfg = self._get_hyperv_config()
            resize_label = f"RAM -> {hv_cfg['default_ram_mb']} MB"
        else:
            azure = self._get_azure_config()
            resize_label = azure["suggested_size"]

        state["feedback"] = f"[ScalingAgent] Implementing resize for VM {vm_name} ({resize_label})"

        # Move CR to Implement
        if change_sys_id:
            implement_result = self.servicenow.move_to_implement(
                change_sys_id=change_sys_id,
                work_notes=(
                    f"CR approved. Starting VM resize: {vm_name} {resize_label}. "
                    f"Requested services to check: {requested_service_list}."
                ),
            )
            if implement_result.get("status"):
                state["feedback"] += " | CR moved to Implement."
            else:
                state["feedback"] += (
                    f" | Failed to move CR to Implement: {implement_result.get('message')}"
                )

        # ---- Execute resize based on hypervisor ----
        if hypervisor.lower() == "hyperv":
            resize_result = resize_hyperv_vm(
                vm_name=vm_name,
                new_ram_mb=hv_cfg["default_ram_mb"],
                host=hv_cfg["host"],
                username=hv_cfg["username"],
                password=hv_cfg["password"],
            )
        else:
            azure = self._get_azure_config()
            resize_result = resize_azure_vm(
                tenant_id=azure["tenant_id"],
                client_id=azure["client_id"],
                client_secret=azure["client_secret"],
                subscription_id=azure["subscription_id"],
                resource_group=azure["resource_group"],
                vm_name=vm_name,
                new_vm_size=azure["suggested_size"],
                services_to_check=services_to_check,
            )

        precheck_result = resize_result.get("precheck", {}) or {}
        precheck_checks = precheck_result.get("checks", []) or []
        precheck_apps = [
            f"{item.get('service')}={item.get('status')}"
            for item in precheck_checks
            if item.get("service")
        ]
        precheck_app_list = ", ".join(precheck_apps) if precheck_apps else "No precheck results"
        precheck_message = precheck_result.get(
            "message", "Pre-resize app/service checks not available."
        )

        app_check_result = resize_result.get("app_checks", {}) or {}
        app_checks = app_check_result.get("checks", []) or []
        postcheck_apps = [
            f"{item.get('service')}={item.get('status')}"
            for item in app_checks
            if item.get("service")
        ]
        postcheck_app_list = ", ".join(postcheck_apps) if postcheck_apps else "No post-check results"

        if app_checks:
            app_check_details = postcheck_app_list
        elif services_to_check:
            app_check_details = "No service status details returned."
        else:
            app_check_details = "No app/service checks requested."

        app_check_message = app_check_result.get(
            "message", "Post-resize app/service checks not available."
        )

        # Close/update CR after resize
        change_update_result = {"status": False, "message": "No Change Request update attempted."}
        if change_sys_id:
            change_update_result = self.servicenow.update_change_after_resize(
                change_sys_id=change_sys_id,
                resize_status=resize_result.get("status", False),
                resize_message=(
                    f"{resize_result.get('message', 'Resize operation completed.')} "
                    f"{precheck_message} "
                    f"{app_check_message} "
                    f"Precheck app list: {precheck_app_list}. "
                    f"Post-check app list: {postcheck_app_list}."
                ),
                work_note=(
                    f"Resize attempt for VM {vm_name} ({resize_label}). "
                    f"Outcome: {resize_result.get('message', 'No message returned.')} "
                    f"Precheck status: {precheck_message} "
                    f"Precheck app list: {precheck_app_list}. "
                    f"Post-check app list: {app_check_details}."
                ),
            )

        scaling_result = convert_numpy_types({
            "vm_name": vm_name,
            "hypervisor": hypervisor,
            "scaled_to": resize_label,
            "decision": decision,
            "resize_response": resize_result,
            "servicenow_change_create": {
                "change_sys_id": change_sys_id,
                "change_number": change_number,
            },
            "servicenow_change_update": change_update_result,
        })

        self.attach_result(state, key="scaling_result", value=scaling_result)
        state["scaling_executed"] = resize_result.get("status", False)

        if resize_result.get("status"):
            state["feedback"] += " | Resize completed successfully."
        else:
            state["feedback"] += f" | Resize failed: {resize_result.get('message')}"

        state["feedback"] += f" | Precheck status: {precheck_message}"
        state["feedback"] += f" | App check status: {app_check_message}"

        if change_sys_id:
            if change_update_result.get("status"):
                state["feedback"] += " | ServiceNow Change Request updated/closed."
            else:
                state["feedback"] += (
                    f" | ServiceNow CR update failed: {change_update_result.get('message')}"
                )

        return state

    # -----------------------------------------------------------------
    # Legacy run() — used by the LangGraph post-approval workflow.
    # Now delegates to create_change_request only (no auto-poll).
    # -----------------------------------------------------------------
    def run(self, state: dict) -> dict:
        recommendation = state.get("recommendation", {})
        if not recommendation:
            state["feedback"] = "No recommendation found. Skipping scaling."
            state["scaling_executed"] = False
            return state

        if state.get("manual_proceed", False) is not True:
            state["feedback"] = "Manual proceed not approved. Skipping scaling."
            state["scaling_executed"] = False
            return state

        decision = recommendation.get("decision")
        if decision == "no_change":
            state["feedback"] = "Decision is no change. Skipping scaling."
            state["scaling_executed"] = False
            return state

        # Create the CR and submit for approval; the dashboard
        # will handle check-approval + implement via separate API calls.
        return self.create_change_request(state)
