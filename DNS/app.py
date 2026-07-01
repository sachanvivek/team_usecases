import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from pydantic import BaseModel
from typing import Optional

from config_loader import load_config
from orchestrator import AgentOrchestrator
from azure_dns import AzureDNSClient
from cr_workflow import CRWorkflowEngine
from dns_record_manager import DNSRecordChange
from agents.chat_assistant import ChatAssistant
import database as db
from local_dns_server import LocalDNSServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

cfg = load_config()
orchestrator = AgentOrchestrator()
azure_dns = AzureDNSClient()
workflow_engine = CRWorkflowEngine()
chat_assistant = ChatAssistant()
local_dns = LocalDNSServer()
scheduler = AsyncIOScheduler()

# LLM analysis mode: "manual" (default) or "auto"
llm_mode = "manual"


async def scheduled_collection():
    try:
        await asyncio.to_thread(orchestrator.collect_dns_data)
        logger.info("Scheduled DNS data collection completed")
    except Exception as e:
        logger.error(f"Scheduled collection failed: {e}")


async def scheduled_analysis():
    if llm_mode != "auto":
        return  # Skip LLM analysis in manual mode
    try:
        await orchestrator.run_all_agents()
        logger.info("Scheduled agent analysis completed")
    except Exception as e:
        logger.error(f"Scheduled analysis failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    poll_interval = cfg.getint("azure_dns", "poll_interval_seconds", fallback=30)
    analysis_interval = cfg.getint("agents", "analysis_interval_seconds", fallback=60)
    # Initialize database
    db.init_db()
    scheduler.add_job(scheduled_collection, "interval", seconds=poll_interval, id="dns_collect")
    scheduler.add_job(scheduled_analysis, "interval", seconds=analysis_interval, id="agent_analysis")
    scheduler.start()

    async def _initial_collection_task():
        try:
            await asyncio.to_thread(orchestrator.collect_dns_data)
            logger.info("Initial DNS data collection completed")
        except Exception as e:
            logger.error(f"Initial DNS collection failed: {e}")

    # Initial collection (DNS data only, no LLM) without blocking startup
    asyncio.create_task(_initial_collection_task())

    total_stored = db.get_total_query_count()
    logger.info(f"DNS Monitoring Platform started (LLM mode: manual, DB: {total_stored} stored queries)")
    yield
    scheduler.shutdown()


app = FastAPI(title="DNS AI Monitoring Platform", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- Pages ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


# --- API ---
@app.get("/api/status")
async def api_status():
    return orchestrator.get_status()


@app.get("/api/dns/summary")
async def api_dns_summary():
    return orchestrator.collector.get_summary()


@app.get("/api/dns/history")
async def api_dns_history(limit: int = 100):
    return orchestrator.get_dns_history_json(limit)


@app.post("/api/dns/collect")
async def api_dns_collect():
    results = await asyncio.to_thread(orchestrator.collect_dns_data)
    return {"collected": len(results), "summary": orchestrator.collector.get_summary()}


@app.post("/api/agents/run-all")
async def api_run_all_agents():
    if orchestrator.is_running:
        return JSONResponse({"error": "Analysis already running"}, status_code=409)
    results = await orchestrator.run_all_agents()
    return {"agents_run": len(results), "results": results}


@app.post("/api/agents/run/{agent_name}")
async def api_run_single_agent(agent_name: str):
    if agent_name not in orchestrator.agents:
        return JSONResponse({"error": f"Agent '{agent_name}' not found"}, status_code=404)
    result = await orchestrator.run_single_agent(agent_name)
    return result


@app.get("/api/agents/results")
async def api_agent_results():
    return orchestrator.get_all_results()


@app.get("/api/orchestrator/narrative")
async def api_orchestrator_narrative():
    return orchestrator.get_orchestrator_narrative()


@app.get("/api/agents/results/{agent_name}")
async def api_agent_result(agent_name: str):
    results = orchestrator.get_all_results()
    if agent_name not in results:
        return JSONResponse({"error": "No results yet"}, status_code=404)
    return results[agent_name]


@app.get("/api/azure/zones")
async def api_azure_zones():
    return azure_dns.get_zone_summary()


@app.get("/api/llm/mode")
async def api_llm_mode():
    return {"mode": llm_mode}


@app.post("/api/llm/mode/{mode}")
async def api_set_llm_mode(mode: str):
    global llm_mode
    if mode not in ("manual", "auto"):
        return JSONResponse({"error": "Mode must be 'manual' or 'auto'"}, status_code=400)
    llm_mode = mode
    logger.info(f"LLM mode changed to: {mode}")
    return {"mode": llm_mode}


@app.get("/api/config")
async def api_config():
    return {
        "llm_provider": cfg.get("llm", "provider", fallback="ollama"),
        "llm_model": cfg.get("llm", "model", fallback=""),
        "dns_servers": cfg.get("dns_targets", "servers", fallback=""),
        "test_domains": cfg.get("dns_targets", "test_domains", fallback=""),
        "azure_dns_enabled": cfg.getboolean("azure_dns", "enabled", fallback=False),
        "servicenow_instance": cfg.get("servicenow", "instance", fallback=""),
        "managed_zones": cfg.get("dns_management", "managed_zones", fallback=""),
        "default_backend": cfg.get("dns_management", "default_backend", fallback="azure_dns"),
        "auto_implement": cfg.getboolean("dns_management", "auto_implement", fallback=True),
        "local_dns_enabled": cfg.getboolean("local_dns_server", "enabled", fallback=False),
        "local_dns_host": cfg.get("local_dns_server", "host", fallback=""),
        "local_dns_zones": cfg.get("local_dns_server", "authoritative_zones", fallback=""),
        "auto_remediation": cfg.getboolean("remediation", "auto_remediation", fallback=False),
        "auto_approve_cr": cfg.getboolean("dns_management", "auto_approve_cr", fallback=False),
    }


# =============================================================================
# Chat Assistant API
# =============================================================================

class ChatMessage(BaseModel):
    message: str
    session_id: str = "default"


class ConfirmAction(BaseModel):
    session_id: str = "default"
    action: str  # add, modify, delete
    record_name: str
    zone: str
    record_type: str
    ttl: int = 3600
    values: list[str] = []
    old_values: list[str] = []
    reason: str = ""


@app.post("/api/chat")
async def api_chat(msg: ChatMessage):
    """Process a chat message through the AI assistant."""
    context = {
        "managed_zones": workflow_engine.dns_mgr.managed_zones,
        "local_dns_server": local_dns.host if local_dns.enabled else None,
        "local_dns_zones": local_dns.authoritative_zones if local_dns.enabled else [],
        "active_workflows": workflow_engine.get_active_workflows(),
        "recent_changes": workflow_engine.dns_mgr.get_changes(10),
    }
    result = await chat_assistant.chat(msg.session_id, msg.message, context)
    return result


@app.post("/api/chat/confirm")
async def api_chat_confirm(action: ConfirmAction):
    """Confirm a DNS change action and create a CR workflow."""
    change = DNSRecordChange(
        operation=action.action,
        zone=action.zone,
        record_name=action.record_name,
        record_type=action.record_type.upper(),
        ttl=action.ttl,
        values=action.values,
        old_values=action.old_values,
    )
    workflow = await workflow_engine.start_workflow(change)
    return workflow.to_dict()


@app.get("/api/chat/history/{session_id}")
async def api_chat_history(session_id: str):
    return chat_assistant.get_history(session_id)


@app.delete("/api/chat/session/{session_id}")
async def api_chat_clear(session_id: str):
    chat_assistant.clear_session(session_id)
    return {"success": True}


# =============================================================================
# CR Workflow API
# =============================================================================

@app.get("/api/workflows")
async def api_workflows():
    return workflow_engine.get_all_workflows()


@app.get("/api/workflows/active")
async def api_active_workflows():
    return workflow_engine.get_active_workflows()


@app.get("/api/workflows/{workflow_id}")
async def api_workflow_detail(workflow_id: str):
    wf = workflow_engine.get_workflow(workflow_id)
    if not wf:
        return JSONResponse({"error": "Workflow not found"}, status_code=404)
    return wf


# =============================================================================
# DNS Record Management API
# =============================================================================

@app.get("/api/dns/changes")
async def api_dns_changes(limit: int = 50):
    return workflow_engine.dns_mgr.get_changes(limit)


@app.get("/api/dns/managed-zones")
async def api_managed_zones():
    return {"zones": workflow_engine.dns_mgr.managed_zones}


# =============================================================================
# Local DNS Server API
# =============================================================================

@app.get("/api/local-dns/status")
async def api_local_dns_status():
    return local_dns.get_server_status()


@app.get("/api/local-dns/zones")
async def api_local_dns_zones():
    return local_dns.list_zones()


@app.get("/api/local-dns/zones/{zone_name}/records")
async def api_local_dns_zone_records(zone_name: str):
    return local_dns.list_zone_records(zone_name)


@app.post("/api/local-dns/setup-zones")
async def api_local_dns_setup():
    """Ensure all authoritative zones exist on the local BIND server."""
    return local_dns.setup_authoritative_zones()


@app.get("/api/local-dns/query")
async def api_local_dns_query(fqdn: str, rtype: str = "A"):
    return local_dns.query_record(fqdn, rtype)


# =============================================================================
# Remediation API
# =============================================================================

@app.get("/api/remediation/tickets")
async def api_remediation_tickets():
    return orchestrator.remediation.get_all_tickets()


@app.get("/api/remediation/active")
async def api_remediation_active():
    return orchestrator.remediation.get_active_tickets()


@app.get("/api/remediation/stats")
async def api_remediation_stats():
    return orchestrator.remediation.get_stats()


@app.get("/api/remediation/tickets/{ticket_id}")
async def api_remediation_ticket(ticket_id: str):
    ticket = orchestrator.remediation.get_ticket(ticket_id)
    if not ticket:
        return JSONResponse({"error": "Ticket not found"}, status_code=404)
    return ticket


# =============================================================================
# Historical Data API (from SQLite DB)
# =============================================================================

@app.get("/api/history/dns/summary")
async def api_history_dns_summary(hours: float = 1.0):
    """Get DNS query summary from the database for the last N hours."""
    return db.get_dns_summary(hours)


@app.get("/api/history/dns/trend")
async def api_history_dns_trend(hours: float = 24, bucket_minutes: int = 30):
    """Get time-bucketed DNS trend data for charts."""
    return db.get_dns_trend(hours, bucket_minutes)


@app.get("/api/history/dns/total")
async def api_history_dns_total():
    """Get total query count stored in DB."""
    return {"total_queries": db.get_total_query_count()}


@app.get("/api/history/agents/{agent_name}")
async def api_history_agent(agent_name: str, limit: int = 20):
    """Get historical results for a specific agent."""
    return db.get_agent_history(agent_name, limit)


@app.get("/api/history/agents/latest")
async def api_history_agents_latest():
    """Get the most recent result for each agent (from DB)."""
    return db.get_latest_agent_results()


@app.get("/api/history/orchestrator")
async def api_history_orchestrator(limit: int = 10):
    """Get historical orchestrator analysis runs."""
    return db.get_orchestrator_history(limit)
