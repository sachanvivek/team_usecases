import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from config_loader import get_config
from servicenow_client import ServiceNowClient
from dns_record_manager import DNSRecordManager, DNSRecordChange
from llm_client import get_llm_client

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    name: str
    status: str = "pending"  # pending, running, completed, failed, skipped
    result: Optional[dict] = None
    timestamp: float = 0.0
    message: str = ""


@dataclass
class CRWorkflow:
    """Tracks the full lifecycle of a DNS change through ServiceNow CR."""
    workflow_id: str
    change: DNSRecordChange
    steps: list = field(default_factory=list)
    status: str = "created"  # created, cr_created, awaiting_approval, approved, implementing, completed, failed, cancelled
    cr_number: Optional[str] = None
    cr_sys_id: Optional[str] = None
    created_at: float = 0.0
    completed_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()
        if not self.steps:
            self.steps = [
                WorkflowStep(name="Create CR"),
                WorkflowStep(name="Await Approval"),
                WorkflowStep(name="Pre-Check"),
                WorkflowStep(name="Implement Change"),
                WorkflowStep(name="Post-Check"),
                WorkflowStep(name="Close CR"),
            ]

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "change": self.change.to_dict(),
            "steps": [
                {"name": s.name, "status": s.status, "message": s.message, "timestamp": s.timestamp}
                for s in self.steps
            ],
            "status": self.status,
            "cr_number": self.cr_number,
            "cr_sys_id": self.cr_sys_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    def get_step(self, name: str) -> Optional[WorkflowStep]:
        for s in self.steps:
            if s.name == name:
                return s
        return None


class CRWorkflowEngine:
    """Orchestrates the full CR lifecycle: Create -> Approve -> Pre-Check -> Implement -> Post-Check -> Close."""

    def __init__(self):
        cfg = get_config()
        self.snow = ServiceNowClient()
        self.dns_mgr = DNSRecordManager()
        self.auto_implement = cfg.getboolean("dns_management", "auto_implement", fallback=True)
        self.auto_approve = cfg.getboolean("dns_management", "auto_approve_cr", fallback=False)
        self.poll_interval = cfg.getint("servicenow", "approval_poll_interval", fallback=30)
        self._workflows: dict[str, CRWorkflow] = {}
        self._workflow_counter = 0
        self._polling_tasks: dict[str, asyncio.Task] = {}

    def _next_id(self) -> str:
        self._workflow_counter += 1
        return f"WF-{self._workflow_counter:04d}"

    async def start_workflow(self, change: DNSRecordChange) -> CRWorkflow:
        """Start a full CR workflow for a DNS change."""
        wf_id = self._next_id()
        wf = CRWorkflow(workflow_id=wf_id, change=change)
        self._workflows[wf_id] = wf
        self.dns_mgr.add_change(change)

        # Step 1: Create the CR
        await self._step_create_cr(wf)

        if wf.status == "failed":
            return wf

        # Step 2: Start polling for approval in background
        task = asyncio.create_task(self._approval_and_execute_pipeline(wf))
        self._polling_tasks[wf_id] = task

        return wf

    async def _step_create_cr(self, wf: CRWorkflow):
        """Step 1: Create Change Request in ServiceNow."""
        step = wf.get_step("Create CR")
        step.status = "running"
        step.timestamp = time.time()

        change = wf.change
        op_desc = {
            "add": f"Add DNS {change.record_type} record '{change.record_name}.{change.zone}' with values {change.values}",
            "modify": f"Modify DNS {change.record_type} record '{change.record_name}.{change.zone}' from {change.old_values} to {change.values}",
            "delete": f"Delete DNS {change.record_type} record '{change.record_name}.{change.zone}'",
        }

        short_desc = f"DNS {change.operation.upper()}: {change.record_name}.{change.zone} ({change.record_type})"
        description = op_desc.get(change.operation, f"DNS change: {change.operation}")
        # Determine target server
        backend = self.dns_mgr._get_backend_for_zone(change.zone)
        if backend == "local_bind":
            local = self.dns_mgr._get_local_dns()
            target_server = f"local BIND server at {local.host}"
        else:
            target_server = "Azure DNS"

        impl_plan = (
            f"1. Pre-check: Verify current DNS state for {change.fqdn} on {target_server}\n"
            f"2. Execute: {description} on {target_server}\n"
            f"3. Post-check: Verify DNS propagation and record correctness on {target_server}\n"
            f"4. TTL: {change.ttl} seconds\n"
            f"5. Target: {target_server}"
        )
        backout_plan = self._generate_backout_plan(change)
        test_plan = (
            f"1. Resolve {change.fqdn} ({change.record_type}) on {target_server} before change\n"
            f"2. Apply change via {'DNS dynamic update (nsupdate)' if backend == 'local_bind' else 'Azure DNS API'}\n"
            f"3. Resolve {change.fqdn} ({change.record_type}) on {target_server} after change\n"
            f"4. Verify values match expected: {change.values}"
        )

        result = await self.snow.create_change_request(
            short_description=short_desc,
            description=description,
            justification=f"DNS record {change.operation} required for {change.fqdn}",
            implementation_plan=impl_plan,
            backout_plan=backout_plan,
            test_plan=test_plan,
        )

        if result.get("success"):
            wf.cr_number = result["number"]
            wf.cr_sys_id = result["sys_id"]
            change.cr_number = result["number"]
            change.cr_sys_id = result["sys_id"]
            wf.status = "cr_created"
            step.status = "completed"
            step.message = f"CR {result['number']} created successfully"
            step.result = result
            logger.info(f"Workflow {wf.workflow_id}: CR {result['number']} created")
        else:
            wf.status = "failed"
            step.status = "failed"
            step.message = f"Failed to create CR: {result.get('error')}"
            step.result = result

    def _generate_backout_plan(self, change: DNSRecordChange) -> str:
        if change.operation == "add":
            return f"Delete the newly created {change.record_type} record for {change.fqdn}"
        elif change.operation == "modify":
            return f"Revert {change.fqdn} ({change.record_type}) back to original values: {change.old_values}"
        elif change.operation == "delete":
            return f"Recreate {change.record_type} record for {change.fqdn} with original values"
        return "Revert DNS change manually"

    async def _approval_and_execute_pipeline(self, wf: CRWorkflow):
        """Background pipeline: Auto-approve or poll for approval, then execute the change."""
        # Step 2: Await Approval
        step = wf.get_step("Await Approval")
        step.status = "running"
        step.timestamp = time.time()
        wf.status = "awaiting_approval"

        approved = False

        # Auto-approve if enabled in config
        if self.auto_approve:
            step.message = f"Auto-approving CR {wf.cr_number}..."
            logger.info(f"Workflow {wf.workflow_id}: auto-approving CR {wf.cr_number}")
            try:
                approve_result = await self.snow.auto_approve_cr(wf.cr_sys_id)
                if approve_result.get("success"):
                    approved = True
                    step.status = "completed"
                    step.message = f"CR {wf.cr_number} auto-approved"
                    wf.status = "approved"
                    logger.info(f"Workflow {wf.workflow_id}: CR {wf.cr_number} auto-approved successfully")
                    await self.snow.add_work_note(
                        wf.cr_sys_id,
                        f"CR auto-approved by DNS AI Platform.\nWorkflow: {wf.workflow_id}"
                    )
                else:
                    logger.warning(f"Auto-approve failed: {approve_result.get('error')}, falling back to polling")
                    step.message = f"Auto-approve failed ({approve_result.get('error')}), polling for manual approval..."
            except Exception as e:
                logger.warning(f"Auto-approve error: {e}, falling back to polling")
                step.message = f"Auto-approve error: {e}, polling for manual approval..."

        # Fall back to polling if not auto-approved
        if not approved:
            step.message = f"Polling for CR {wf.cr_number} approval every {self.poll_interval}s"
            max_polls = 480  # ~4 hours at 30s interval
            for i in range(max_polls):
                await asyncio.sleep(self.poll_interval)
                try:
                    status = await self.snow.check_approval_status(wf.cr_sys_id)
                    if status.get("is_approved"):
                        approved = True
                        step.status = "completed"
                        step.message = f"CR {wf.cr_number} approved"
                        wf.status = "approved"
                        logger.info(f"Workflow {wf.workflow_id}: CR {wf.cr_number} approved")
                        await self.snow.add_work_note(
                            wf.cr_sys_id,
                            f"CR approved. Automated implementation starting.\nWorkflow: {wf.workflow_id}"
                        )
                        break
                    elif status.get("is_rejected"):
                        step.status = "failed"
                        step.message = f"CR {wf.cr_number} rejected"
                        wf.status = "cancelled"
                        logger.info(f"Workflow {wf.workflow_id}: CR {wf.cr_number} rejected")
                        return
                    else:
                        step.message = (
                            f"Polling... approval='{status.get('approval', 'unknown')}' "
                            f"state='{status.get('state', 'unknown')}' (poll {i+1})"
                        )
                except Exception as e:
                    logger.error(f"Approval poll error: {e}")
                    step.message = f"Poll error: {e} (retrying)"

        if not approved:
            step.status = "failed"
            step.message = "Approval timeout exceeded"
            wf.status = "failed"
            return

        if not self.auto_implement:
            wf.status = "approved"
            return

        # Move CR to Implement state
        await self.snow.move_to_implement(wf.cr_sys_id)

        # Step 3: Pre-Check
        await self._step_pre_check(wf)
        if wf.status == "failed":
            await self.snow.add_work_note(wf.cr_sys_id, f"Pre-check FAILED:\n{wf.get_step('Pre-Check').message}")
            return

        # Step 4: Implement
        await self._step_implement(wf)
        if wf.status == "failed":
            await self.snow.add_work_note(wf.cr_sys_id, f"Implementation FAILED:\n{wf.get_step('Implement Change').message}")
            return

        # Step 5: Post-Check
        await self._step_post_check(wf)

        # Move CR to Review
        await self.snow.move_to_review(wf.cr_sys_id)

        # Step 6: Close CR
        await self._step_close_cr(wf)

    async def _step_pre_check(self, wf: CRWorkflow):
        """Step 3: Pre-check DNS state."""
        step = wf.get_step("Pre-Check")
        step.status = "running"
        step.timestamp = time.time()
        wf.status = "implementing"

        result = self.dns_mgr.pre_check(wf.change)
        step.result = result

        if result.get("passed"):
            step.status = "completed"
            step.message = "Pre-check passed: " + "; ".join(result.get("details", []))
            await self.snow.add_work_note(
                wf.cr_sys_id,
                f"PRE-CHECK PASSED\n" + "\n".join(f"- {d}" for d in result.get("details", []))
            )
        else:
            step.status = "failed"
            step.message = "Pre-check failed: " + "; ".join(result.get("details", []))
            wf.status = "failed"

    async def _step_implement(self, wf: CRWorkflow):
        """Step 4: Implement the DNS change."""
        step = wf.get_step("Implement Change")
        step.status = "running"
        step.timestamp = time.time()

        result = self.dns_mgr.implement(wf.change)
        step.result = result

        if result.get("success"):
            step.status = "completed"
            step.message = "Implementation successful: " + "; ".join(result.get("details", []))
            await self.snow.add_work_note(
                wf.cr_sys_id,
                f"IMPLEMENTATION COMPLETED\n" + "\n".join(f"- {d}" for d in result.get("details", []))
            )
        else:
            step.status = "failed"
            step.message = "Implementation failed: " + "; ".join(result.get("details", []))
            wf.status = "failed"

    async def _step_post_check(self, wf: CRWorkflow):
        """Step 5: Post-check DNS propagation."""
        step = wf.get_step("Post-Check")
        step.status = "running"
        step.timestamp = time.time()

        # Wait a moment for DNS propagation
        await asyncio.sleep(5)

        result = self.dns_mgr.post_check(wf.change)
        step.result = result

        if result.get("passed"):
            step.status = "completed"
            step.message = "Post-check passed: " + "; ".join(result.get("details", []))
        else:
            step.status = "completed"  # Don't fail workflow on post-check
            step.message = "Post-check warning: " + "; ".join(result.get("details", []))

        await self.snow.add_work_note(
            wf.cr_sys_id,
            f"POST-CHECK {'PASSED' if result.get('passed') else 'WARNING'}\n"
            + "\n".join(f"- {d}" for d in result.get("details", []))
        )

    async def _step_close_cr(self, wf: CRWorkflow):
        """Step 6: Close the CR."""
        step = wf.get_step("Close CR")
        step.status = "running"
        step.timestamp = time.time()

        post_check = wf.get_step("Post-Check")
        close_code = "successful" if post_check.status == "completed" else "successful_with_issues"

        # Generate close notes using LLM
        close_notes = await self._generate_close_notes(wf)

        result = await self.snow.close_change_request(
            wf.cr_sys_id, close_code=close_code, close_notes=close_notes
        )

        if result.get("success"):
            step.status = "completed"
            step.message = f"CR {wf.cr_number} closed as '{close_code}'"
            wf.status = "completed"
            wf.completed_at = time.time()
            logger.info(f"Workflow {wf.workflow_id}: CR {wf.cr_number} closed successfully")
        else:
            step.status = "failed"
            step.message = f"Failed to close CR: {result.get('error')}"
            # Workflow is still considered completed since the change was applied
            wf.status = "completed"
            wf.completed_at = time.time()

    async def _generate_close_notes(self, wf: CRWorkflow) -> str:
        """Use LLM to generate professional close notes."""
        try:
            llm = get_llm_client()
            prompt = (
                "Generate concise professional close notes for this DNS Change Request. "
                "Include: what was done, pre-check results, implementation status, post-check results.\n\n"
                f"Change: {wf.change.operation.upper()} {wf.change.fqdn} ({wf.change.record_type})\n"
                f"Values: {wf.change.values}\n"
                f"Pre-check: {wf.get_step('Pre-Check').message}\n"
                f"Implementation: {wf.get_step('Implement Change').message}\n"
                f"Post-check: {wf.get_step('Post-Check').message}\n"
                f"\nKeep it under 500 characters. Plain text only, no JSON."
            )
            notes = await llm.chat("You write professional IT change management close notes.", prompt)
            return notes.strip()
        except Exception:
            return (
                f"DNS {wf.change.operation} completed for {wf.change.fqdn} ({wf.change.record_type}). "
                f"Pre-check: {wf.get_step('Pre-Check').status}. "
                f"Post-check: {wf.get_step('Post-Check').status}."
            )

    # --- Query ---
    def get_workflow(self, workflow_id: str) -> Optional[dict]:
        wf = self._workflows.get(workflow_id)
        return wf.to_dict() if wf else None

    def get_all_workflows(self) -> list:
        return [wf.to_dict() for wf in self._workflows.values()]

    def get_active_workflows(self) -> list:
        return [
            wf.to_dict()
            for wf in self._workflows.values()
            if wf.status not in ("completed", "failed", "cancelled")
        ]
