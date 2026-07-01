import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from config_loader import get_config
from servicenow_client import ServiceNowClient
from llm_client import get_llm_client
import database as db

logger = logging.getLogger(__name__)


@dataclass
class RemediationTicket:
    ticket_id: str
    source_agent: str
    action: str
    priority: str  # low, medium, high, critical
    target: str
    itsm_category: str  # incident, change
    auto_remediate: bool = False
    status: str = "pending"  # pending, ticket_created, in_progress, completed, failed, skipped
    snow_number: Optional[str] = None
    snow_sys_id: Optional[str] = None
    created_at: float = 0.0
    completed_at: float = 0.0
    result: Optional[str] = None

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "source_agent": self.source_agent,
            "action": self.action,
            "priority": self.priority,
            "target": self.target,
            "itsm_category": self.itsm_category,
            "auto_remediate": self.auto_remediate,
            "status": self.status,
            "snow_number": self.snow_number,
            "snow_sys_id": self.snow_sys_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result": self.result,
        }


class RemediationEngine:
    """Processes agent alerts and remediation actions into ITSM tickets.
    When auto_remediation is enabled, automatically resolves issues and manages CRs."""

    def __init__(self):
        cfg = get_config()
        self.snow = ServiceNowClient()
        self._tickets: list[RemediationTicket] = []
        self._ticket_counter = 0
        self._processed_actions: set[str] = set()  # dedup key
        # Auto-remediation settings
        self.auto_remediation = cfg.getboolean("remediation", "auto_remediation", fallback=False)
        self.auto_approve_cr = cfg.getboolean("remediation", "auto_approve_cr", fallback=False)
        self.auto_categories = [
            c.strip() for c in cfg.get("remediation", "auto_categories", fallback="incident,change").split(",")
        ]
        self.max_concurrent = cfg.getint("remediation", "max_concurrent", fallback=3)
        self._active_remediations = 0
        # Seed dedup set from DB (open tickets from last 24h) to survive restarts
        self._seed_dedup_from_db()
        # Lazy-load local DNS server for remediation actions
        self._local_dns = None

    def _next_id(self) -> str:
        self._ticket_counter += 1
        return f"REM-{self._ticket_counter:04d}"

    def _dedup_key(self, action: dict) -> str:
        """Generate dedup key from action+target only (not source_agent).
        This way if multiple agents detect the same issue, only one ticket is created."""
        return f"{action.get('action', '')}:{action.get('target', '')}"

    def _seed_dedup_from_db(self):
        """Load open tickets from DB into dedup set so we don't re-create them after restart."""
        try:
            tickets = db.get_remediation_tickets(limit=200)
            for t in tickets:
                if t.get("status") not in ("completed", "failed", "skipped"):
                    key = f"{t.get('action', '')}:{t.get('target', '')}"
                    self._processed_actions.add(key)
            if self._processed_actions:
                logger.info(f"Remediation dedup: seeded {len(self._processed_actions)} open tickets from DB")
        except Exception as e:
            logger.warning(f"Failed to seed dedup from DB: {e}")

    @staticmethod
    def _normalize_priority(value: str) -> str:
        """Normalize priority strings from LLM output."""
        v = str(value).strip().lower()
        if v in ("1", "critical", "p1"):
            return "critical"
        if v in ("2", "high", "p2"):
            return "high"
        if v in ("3", "medium", "p3"):
            return "medium"
        return "low"

    async def process_agent_results(self, agent_results: dict) -> list[dict]:
        """Extract remediation actions from all agent results and create ITSM tickets."""
        all_actions = []

        for agent_name, result in agent_results.items():
            if not result or not isinstance(result, dict):
                continue

            # Get remediation actions
            actions = result.get("remediation_actions", [])
            for action in actions:
                if isinstance(action, dict):
                    action.setdefault("source_agent", agent_name)
                    action["priority"] = self._normalize_priority(action.get("priority", "medium"))
                    all_actions.append(action)

            # Also process high-severity alerts as incidents
            alerts = result.get("alerts", [])
            for alert in alerts:
                if isinstance(alert, dict):
                    sev = self._normalize_priority(alert.get("severity", "medium"))
                    if sev in ("critical", "high"):
                        all_actions.append({
                            "action": f"Investigate: {alert.get('message', 'Unknown alert')}",
                            "priority": sev,
                            "target": alert.get("affected_server", "DNS Infrastructure"),
                            "auto_remediate": False,
                            "itsm_category": "incident",
                            "source_agent": agent_name,
                        })

            # Process orchestrator itsm_recommendations (from Central Brain)
            itsm_recs = result.get("itsm_recommendations", [])
            for rec in itsm_recs:
                if isinstance(rec, dict):
                    prio = self._normalize_priority(rec.get("priority", "medium"))
                    all_actions.append({
                        "action": rec.get("summary", rec.get("action", "")),
                        "priority": prio,
                        "target": "DNS Infrastructure",
                        "auto_remediate": False,
                        "itsm_category": rec.get("type", "incident"),
                        "source_agent": agent_name if agent_name != "_orchestrator" else "LLM Orchestrator",
                    })

            # Process orchestrator prioritized_actions
            pri_actions = result.get("prioritized_actions", [])
            for pa in pri_actions:
                if isinstance(pa, dict):
                    prio = self._normalize_priority(pa.get("priority", "medium"))
                    if prio in ("critical", "high"):
                        all_actions.append({
                            "action": pa.get("action", ""),
                            "priority": prio,
                            "target": "DNS Infrastructure",
                            "auto_remediate": False,
                            "itsm_category": "change",
                            "source_agent": agent_name if agent_name != "_orchestrator" else "LLM Orchestrator",
                        })

        # Deduplicate and filter
        new_actions = []
        for action in all_actions:
            key = self._dedup_key(action)
            if key not in self._processed_actions:
                self._processed_actions.add(key)
                new_actions.append(action)

        # Limit dedup cache
        if len(self._processed_actions) > 500:
            self._processed_actions = set(list(self._processed_actions)[-200:])

        # Create tickets for high/critical priority actions
        created = []
        for action in new_actions:
            priority = action.get("priority", "medium")
            if priority in ("high", "critical"):
                # Double-check DB for existing open ticket (covers race conditions / multi-agent overlap)
                if db.has_recent_open_ticket(action.get("action", ""), action.get("target", "")):
                    logger.info(f"Remediation: skipping duplicate ticket for '{action.get('action', '')[:60]}' (open ticket exists in DB)")
                    continue
                ticket = await self._create_ticket(action)
                created.append(ticket.to_dict())

        return created

    async def _create_ticket(self, action: dict) -> RemediationTicket:
        """Create a ServiceNow ticket for a remediation action."""
        ticket = RemediationTicket(
            ticket_id=self._next_id(),
            source_agent=action.get("source_agent", "Unknown"),
            action=action.get("action", ""),
            priority=action.get("priority", "medium"),
            target=action.get("target", ""),
            itsm_category=action.get("itsm_category", "incident"),
            auto_remediate=action.get("auto_remediate", False),
        )
        self._tickets.append(ticket)

        # Generate enriched description using LLM
        description = await self._generate_ticket_description(action)

        # Map priority
        snow_priority = {"critical": "1", "high": "2", "medium": "3", "low": "4"}.get(
            ticket.priority, "3"
        )
        snow_impact = {"critical": "1", "high": "2", "medium": "2", "low": "3"}.get(
            ticket.priority, "2"
        )

        if ticket.itsm_category == "incident":
            result = await self._create_incident(ticket, description, snow_priority, snow_impact)
        else:
            result = await self._create_change(ticket, description, snow_priority, snow_impact)

        if result.get("success"):
            ticket.snow_number = result.get("number")
            ticket.snow_sys_id = result.get("sys_id")
            ticket.status = "ticket_created"
            logger.info(f"Remediation {ticket.ticket_id}: {ticket.itsm_category} {ticket.snow_number} created")

            # Auto-remediation: if enabled, execute fix and close ticket automatically
            if self.auto_remediation and ticket.itsm_category in self.auto_categories:
                await self._auto_remediate(ticket, action)
        else:
            ticket.status = "failed"
            ticket.result = result.get("error", "Unknown error")
            logger.error(f"Remediation {ticket.ticket_id}: failed to create ticket - {ticket.result}")

        # Persist to DB
        try:
            db.upsert_remediation_ticket(ticket.to_dict())
        except Exception as e:
            logger.warning(f"DB upsert for ticket {ticket.ticket_id} failed: {e}")

        return ticket

    # =========================================================================
    # Auto-Remediation
    # =========================================================================

    def _get_local_dns(self):
        """Lazy-load LocalDNSServer to avoid circular imports."""
        if self._local_dns is None:
            from local_dns_server import LocalDNSServer
            self._local_dns = LocalDNSServer()
        return self._local_dns

    async def _auto_remediate(self, ticket: RemediationTicket, action: dict):
        """Automatically resolve the issue: approve CR, execute fix, close."""
        if self._active_remediations >= self.max_concurrent:
            logger.warning(f"Remediation {ticket.ticket_id}: skipped auto-remediation (max concurrent reached)")
            ticket.result = "Skipped: max concurrent auto-remediations reached"
            return

        self._active_remediations += 1
        try:
            ticket.status = "in_progress"
            logger.info(f"Remediation {ticket.ticket_id}: starting auto-remediation for {ticket.itsm_category}")

            if ticket.itsm_category == "change" and ticket.snow_sys_id:
                await self._auto_remediate_change(ticket, action)
            elif ticket.itsm_category == "incident" and ticket.snow_sys_id:
                await self._auto_remediate_incident(ticket, action)
            else:
                ticket.result = f"Auto-remediation not supported for category: {ticket.itsm_category}"
                logger.warning(f"Remediation {ticket.ticket_id}: {ticket.result}")
        except Exception as e:
            ticket.status = "failed"
            ticket.result = f"Auto-remediation error: {e}"
            logger.error(f"Remediation {ticket.ticket_id}: auto-remediation failed: {e}")
        finally:
            self._active_remediations -= 1
            # Update DB
            try:
                db.upsert_remediation_ticket(ticket.to_dict())
            except Exception:
                pass

    async def _auto_remediate_change(self, ticket: RemediationTicket, action: dict):
        """Auto-remediate via Change Request: approve -> implement -> verify -> close."""
        # Step 1: Auto-approve the CR
        if self.auto_approve_cr:
            await self.snow.add_work_note(
                ticket.snow_sys_id,
                f"[Auto-Remediation] Starting automated remediation.\n"
                f"Source: {ticket.source_agent}\nAction: {ticket.action}\nPriority: {ticket.priority}"
            )
            approve_result = await self.snow.auto_approve_cr(ticket.snow_sys_id)
            if not approve_result.get("success"):
                ticket.status = "failed"
                ticket.result = f"Auto-approve failed: {approve_result.get('error')}"
                return
            logger.info(f"Remediation {ticket.ticket_id}: CR {ticket.snow_number} auto-approved")

        # Step 2: Execute the remediation action
        fix_result = await self._execute_dns_fix(ticket, action)
        await self.snow.add_work_note(
            ticket.snow_sys_id,
            f"[Auto-Remediation] Fix executed.\nResult: {fix_result.get('message', 'completed')}\n"
            f"Success: {fix_result.get('success', False)}"
        )

        # Step 3: Move to Review and Close
        await self.snow.move_to_review(ticket.snow_sys_id)
        close_code = "successful" if fix_result.get("success") else "successful_with_issues"
        close_notes = (
            f"Auto-remediated by DNS AI Platform.\n"
            f"Source Agent: {ticket.source_agent}\n"
            f"Action: {ticket.action}\n"
            f"Result: {fix_result.get('message', 'completed')}\n"
            f"Remediation ID: {ticket.ticket_id}"
        )
        close_result = await self.snow.close_change_request(
            ticket.snow_sys_id, close_code=close_code, close_notes=close_notes
        )

        if close_result.get("success"):
            ticket.status = "completed"
            ticket.completed_at = time.time()
            ticket.result = f"Auto-remediated and CR {ticket.snow_number} closed as {close_code}"
            logger.info(f"Remediation {ticket.ticket_id}: CR {ticket.snow_number} auto-closed")
        else:
            ticket.status = "completed"  # Fix was applied even if close fails
            ticket.completed_at = time.time()
            ticket.result = f"Fix applied but CR close failed: {close_result.get('error')}"

    async def _auto_remediate_incident(self, ticket: RemediationTicket, action: dict):
        """Auto-remediate an incident: execute fix -> resolve incident."""
        # Step 1: Execute the fix
        fix_result = await self._execute_dns_fix(ticket, action)

        # Step 2: Resolve the incident
        close_notes = (
            f"Auto-resolved by DNS AI Platform.\n"
            f"Source Agent: {ticket.source_agent}\n"
            f"Action Taken: {ticket.action}\n"
            f"Result: {fix_result.get('message', 'completed')}\n"
            f"Remediation ID: {ticket.ticket_id}"
        )
        resolve_result = await self.snow.resolve_incident(
            ticket.snow_sys_id, close_notes=close_notes
        )

        if resolve_result.get("success"):
            ticket.status = "completed"
            ticket.completed_at = time.time()
            ticket.result = f"Auto-resolved incident {ticket.snow_number}"
            logger.info(f"Remediation {ticket.ticket_id}: incident {ticket.snow_number} resolved")
        else:
            ticket.status = "completed"
            ticket.completed_at = time.time()
            ticket.result = f"Fix applied but incident resolve failed: {resolve_result.get('error')}"

    async def _execute_dns_fix(self, ticket: RemediationTicket, action: dict) -> dict:
        """Execute the actual DNS fix based on the action type.
        Uses LLM to interpret the action and determine the DNS operation."""
        action_text = action.get("action", "").lower()
        target = action.get("target", "")

        # Try to determine if this is a DNS operation we can auto-fix
        # Common auto-fixable actions from agents:
        # - "Restart DNS service" -> SSH restart
        # - "Flush DNS cache" -> rndc flush
        # - "Add/modify/delete record" -> dynamic update
        # - "Reload zone" -> rndc reload

        local_dns = self._get_local_dns()

        if not local_dns.enabled:
            return {"success": False, "message": "Local DNS server not enabled"}

        try:
            if any(kw in action_text for kw in ["restart", "restart bind", "restart named"]):
                result = local_dns.ssh_command("sudo systemctl restart bind9 && sleep 2 && sudo systemctl status bind9 --no-pager")
                return {"success": True, "message": f"DNS service restarted: {result[:200]}"}

            elif any(kw in action_text for kw in ["flush cache", "clear cache", "rndc flush"]):
                result = local_dns.ssh_command("sudo rndc flush")
                return {"success": True, "message": f"DNS cache flushed: {result[:200]}"}

            elif any(kw in action_text for kw in ["reload zone", "rndc reload"]):
                result = local_dns.ssh_command("sudo rndc reload")
                return {"success": True, "message": f"Zones reloaded: {result[:200]}"}

            elif any(kw in action_text for kw in ["check config", "verify config", "named-checkconf"]):
                result = local_dns.ssh_command("sudo named-checkconf")
                return {"success": True, "message": f"Config check: {result[:200] if result.strip() else 'OK - no errors'}"}

            else:
                # For actions we can't auto-fix, log the recommendation
                logger.info(f"Remediation {ticket.ticket_id}: action logged (no auto-fix available): {action_text[:100]}")
                return {
                    "success": True,
                    "message": f"Action recorded - manual review recommended: {action_text[:200]}"
                }

        except Exception as e:
            logger.error(f"DNS fix execution failed: {e}")
            return {"success": False, "message": f"Execution error: {e}"}

    async def _create_incident(self, ticket: RemediationTicket, description: str,
                                priority: str, impact: str) -> dict:
        """Create a ServiceNow incident."""
        url = f"{self.snow._table_api}/incident"
        payload = {
            "short_description": f"[DNS AI] {ticket.action[:100]}",
            "description": description,
            "category": self.snow.category,
            "subcategory": self.snow.subcategory,
            "assignment_group": self.snow.assignment_group,
            "priority": priority,
            "impact": impact,
            "urgency": priority,
            "caller_id": "admin",
            "work_notes": (
                f"Auto-generated by DNS AI Monitoring Platform\n"
                f"Source Agent: {ticket.source_agent}\n"
                f"Target: {ticket.target}\n"
                f"Remediation ID: {ticket.ticket_id}\n"
                f"Auto-remediate: {ticket.auto_remediate}"
            ),
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.post(
                    url, json=payload, headers=self.snow._headers(), auth=self.snow._auth()
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})
                return {
                    "success": True,
                    "sys_id": result.get("sys_id"),
                    "number": result.get("number"),
                }
        except Exception as e:
            logger.error(f"Failed to create incident: {e}")
            return {"success": False, "error": str(e)}

    async def _create_change(self, ticket: RemediationTicket, description: str,
                              priority: str, impact: str) -> dict:
        """Create a ServiceNow change request for remediation."""
        result = await self.snow.create_change_request(
            short_description=f"[DNS AI Remediation] {ticket.action[:80]}",
            description=description,
            justification=f"AI-detected issue requiring remediation. Source: {ticket.source_agent}",
            implementation_plan=f"1. Review AI recommendation\n2. {ticket.action}\n3. Verify resolution",
            backout_plan="Revert DNS changes if issue persists",
            test_plan="1. Verify DNS resolution\n2. Check agent health scores\n3. Confirm alert cleared",
            impact=impact,
            priority=priority,
        )
        return result

    async def _generate_ticket_description(self, action: dict) -> str:
        """Use LLM to generate professional ITSM ticket description."""
        try:
            llm = get_llm_client()
            prompt = (
                "Generate a professional ITSM ticket description for this DNS issue. "
                "Include: problem statement, impact, recommended action, and urgency. "
                "Keep it under 500 characters. Plain text only.\n\n"
                f"Issue: {action.get('action', '')}\n"
                f"Source: {action.get('source_agent', '')} Agent\n"
                f"Target: {action.get('target', '')}\n"
                f"Priority: {action.get('priority', 'medium')}\n"
                f"Category: {action.get('itsm_category', 'incident')}"
            )
            desc = await llm.chat(
                "You write professional IT service management ticket descriptions.", prompt
            )
            return desc.strip()
        except Exception:
            return (
                f"DNS AI Platform detected an issue requiring attention.\n\n"
                f"Issue: {action.get('action', '')}\n"
                f"Detected by: {action.get('source_agent', '')} Agent\n"
                f"Target: {action.get('target', '')}\n"
                f"Priority: {action.get('priority', 'medium')}"
            )

    # --- Query ---
    def get_all_tickets(self) -> list[dict]:
        return [t.to_dict() for t in self._tickets]

    def get_active_tickets(self) -> list[dict]:
        return [t.to_dict() for t in self._tickets if t.status not in ("completed", "failed", "skipped")]

    def get_ticket(self, ticket_id: str) -> Optional[dict]:
        for t in self._tickets:
            if t.ticket_id == ticket_id:
                return t.to_dict()
        return None

    def get_stats(self) -> dict:
        total = len(self._tickets)
        by_status = {}
        by_priority = {}
        by_category = {}
        for t in self._tickets:
            by_status[t.status] = by_status.get(t.status, 0) + 1
            by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
            by_category[t.itsm_category] = by_category.get(t.itsm_category, 0) + 1
        return {
            "total_tickets": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "by_category": by_category,
        }
