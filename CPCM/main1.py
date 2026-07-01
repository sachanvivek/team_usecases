from fastapi import FastAPI
from pydantic import BaseModel
from src.agent_orch.graph_builder import build_graph

# Initialize FastAPI app
app = FastAPI(title="Agent Orchestrator API")

# Build workflow from graph_builder
workflows = build_graph()
workflow = workflows.pre_approval

# Request schema (input validation)
class PipelineRequest(BaseModel):
    server_id: str
    resource_type: str

# Root endpoint (health check)
@app.get("/")
def health_check():
    return {"status": "ok", "message": "Agent Orchestrator API is running"}

# Run full pipeline
@app.post("/pipeline")
async def run_pipeline(req: PipelineRequest):
    state = {"server_id": req.server_id, "resource_type": req.resource_type}
    result = await workflow.ainvoke(state)  # async invoke
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run('main:app', host="0.0.0", port=8000, log_level="info")