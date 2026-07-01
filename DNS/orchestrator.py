import asyncio
import logging
import time
import database as db
from agents import (
    DNSExperienceAgent,
    DNSRequestHandlingAgent,
    DNSL2Agent,
    AnomalyDetectionAgent,
    FailurePredictionAgent,
    MisconfigurationDetectionAgent,
    QueryLogAnalyticsAgent,
    ClientExperienceScoringAgent,
    DashboardAgent,
)
from dns_collector import DNSCollector
from remediation_engine import RemediationEngine
from llm_client import get_llm_client

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """LLM Orchestrator Agent (Central Brain) - coordinates all DNS AI agents,
    correlates technical signals with enterprise knowledge, and provides
    explainable recommendations."""

    def __init__(self):
        self.collector = DNSCollector()
        self.agents = {
            "experience": DNSExperienceAgent(),
            "request_handling": DNSRequestHandlingAgent(),
            "l2": DNSL2Agent(),
            "anomaly": AnomalyDetectionAgent(),
            "failure_prediction": FailurePredictionAgent(),
            "misconfiguration": MisconfigurationDetectionAgent(),
            "query_log": QueryLogAnalyticsAgent(),
            "client_scoring": ClientExperienceScoringAgent(),
            "dashboard": DashboardAgent(),
        }
        self.remediation = RemediationEngine()
        self.last_results: dict = {}
        self.last_full_run: float = 0
        self.is_running = False
        self._orchestrator_narrative: str = ""
        self._root_cause_analysis: dict = {}

    def collect_dns_data(self) -> list:
        return self.collector.collect_all()

    async def run_agent(self, name: str, context: dict) -> dict:
        agent = self.agents.get(name)
        if not agent:
            return {"error": f"Agent {name} not found"}
        try:
            return await agent.analyze(context)
        except Exception as e:
            logger.error(f"Agent {name} failed: {e}")
            return {"agent": name, "error": str(e), "timestamp": time.time()}

    async def run_all_agents(self) -> dict:
        self.is_running = True
        try:
            # Collect fresh DNS data
            await asyncio.to_thread(self.collect_dns_data)
            context = {
                "dns_summary": self.collector.get_summary(),
                "dns_history": self.collector.get_history(300),
            }

            # Phase 1: Run first 8 agents in parallel (data collection + analysis)
            agent_names = [n for n in self.agents if n != "dashboard"]
            tasks = [self.run_agent(n, context) for n in agent_names]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for name, result in zip(agent_names, results):
                if isinstance(result, Exception):
                    self.last_results[name] = {"agent": name, "error": str(result)}
                else:
                    self.last_results[name] = result
                    # Persist to DB
                    try:
                        db.insert_agent_result(name, result)
                    except Exception as e:
                        logger.warning(f"DB insert for {name} failed: {e}")

            # Phase 2: Run Dashboard agent with all other results (synthesis)
            context["agent_results"] = self.last_results
            dashboard_result = await self.run_agent("dashboard", context)
            self.last_results["dashboard"] = dashboard_result
            try:
                db.insert_agent_result("dashboard", dashboard_result)
            except Exception as e:
                logger.warning(f"DB insert for dashboard failed: {e}")

            # Phase 3: Central Brain - inter-agent correlation and reasoning
            await self._run_central_brain(context)

            # Phase 4: Process remediation actions -> ITSM tickets
            try:
                remediation_tickets = await self.remediation.process_agent_results(self.last_results)
                self.last_results["_remediation_tickets"] = remediation_tickets
                if remediation_tickets:
                    logger.info(f"Created {len(remediation_tickets)} remediation tickets")
            except Exception as e:
                logger.error(f"Remediation processing failed: {e}")

            self.last_full_run = time.time()
            return self.last_results
        finally:
            self.is_running = False

    async def _run_central_brain(self, context: dict):
        """Central Brain: correlate all agent findings and generate root cause analysis."""
        try:
            llm = get_llm_client()

            # Build a summary of all agent findings
            findings = []
            all_alerts = []
            all_remediation = []

            for name, result in self.last_results.items():
                if name.startswith("_") or not isinstance(result, dict):
                    continue
                if result.get("summary"):
                    findings.append(f"[{name}] {result['summary']}")
                for alert in result.get("alerts", []):
                    if isinstance(alert, dict):
                        all_alerts.append(f"[{alert.get('severity', '?')}] {alert.get('message', '')}")
                for action in result.get("remediation_actions", []):
                    if isinstance(action, dict):
                        all_remediation.append(f"[{action.get('priority', '?')}] {action.get('action', '')}")

            prompt = f"""As the DNS LLM Orchestrator (Central Brain), analyze these findings from 9 specialized DNS agents and provide:
1. A root cause narrative - what is the underlying issue (if any)?
2. Correlated insights - what patterns emerge when combining multiple agent signals?
3. Prioritized recommended actions
4. An overall health assessment

DNS Infrastructure Summary:
{context.get('dns_summary', {})}

Agent Findings:
{chr(10).join(findings) if findings else 'No findings yet.'}

Active Alerts ({len(all_alerts)}):
{chr(10).join(all_alerts[:10]) if all_alerts else 'No alerts.'}

Recommended Actions ({len(all_remediation)}):
{chr(10).join(all_remediation[:10]) if all_remediation else 'No actions.'}

Respond with valid JSON:
{{
  "root_cause_narrative": "<explain the root cause of any issues detected>",
  "correlated_insights": ["<insight combining multiple agent signals>"],
  "health_assessment": "healthy|degraded|critical",
  "confidence": <0-100>,
  "prioritized_actions": [
    {{"action": "<what to do>", "priority": "critical|high|medium|low", "rationale": "<why this is important>"}}
  ],
  "itsm_recommendations": [
    {{"type": "incident|change|problem", "summary": "<ticket summary>", "priority": "1|2|3|4", "rationale": "<why create this ticket>"}}
  ],
  "narrative": "<2-3 paragraph executive narrative about the DNS infrastructure state>"
}}"""

            system = (
                "You are the DNS LLM Orchestrator - the central reasoning and decision layer "
                "for a DNS AI monitoring platform. You correlate findings from 9 specialized agents "
                "to provide human-like reasoning at machine speed. Focus on root cause analysis "
                "and actionable recommendations."
            )

            raw = await llm.chat(system, prompt)
            try:
                from agents.base import BaseAgent
                result = BaseAgent._extract_json(raw)
                self._root_cause_analysis = result
                self._orchestrator_narrative = result.get("narrative", "")
                self.last_results["_orchestrator"] = result
                try:
                    db.insert_orchestrator_run(result)
                except Exception as e:
                    logger.warning(f"DB insert for orchestrator run failed: {e}")
            except Exception:
                self._orchestrator_narrative = raw
                self.last_results["_orchestrator"] = {"narrative": raw}

        except Exception as e:
            logger.error(f"Central Brain analysis failed: {e}")
            self.last_results["_orchestrator"] = {"error": str(e)}

    async def run_single_agent(self, name: str) -> dict:
        context = {
            "dns_summary": self.collector.get_summary(),
            "dns_history": self.collector.get_history(300),
            "agent_results": self.last_results,
        }
        result = await self.run_agent(name, context)
        self.last_results[name] = result
        return result

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "last_full_run": self.last_full_run,
            "dns_summary": self.collector.get_summary(),
            "agents": {n: a.get_status() for n, a in self.agents.items()},
            "remediation_stats": self.remediation.get_stats(),
        }

    def get_all_results(self) -> dict:
        return self.last_results

    def get_orchestrator_narrative(self) -> dict:
        return {
            "narrative": self._orchestrator_narrative,
            "root_cause": self._root_cause_analysis,
            "timestamp": self.last_full_run,
        }

    def get_dns_history_json(self, limit: int = 100) -> list:
        history = self.collector.get_history(limit)
        return [
            {
                "server": r.server, "domain": r.domain, "query_type": r.query_type,
                "response_time_ms": r.response_time_ms, "status": r.status,
                "answers": r.answers, "error": r.error, "timestamp": r.timestamp,
            }
            for r in history
        ]
