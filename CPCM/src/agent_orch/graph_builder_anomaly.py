import asyncio
from langgraph.graph import StateGraph, END
from src.agent_orch.agents.anomaly_detector import AnomalyDetectorAgent
from src.agent_orch.agents.alerting import AlertingAgent
from src.agent_orch.agents.rightsizing import RightsizingAgent  # or ScalingAgent for execution

def build_anomaly_graph():
    """Builds a LangGraph workflow for anomaly detection + notification + optional scaling."""
    graph = StateGraph(dict)

    # Initialize agents
    anomaly_agent = AnomalyDetectorAgent()
    notification_agent = AlertingAgent()
    scaling_agent = RightsizingAgent()  # Or ScalingAgent if executing

    # Node definitions
    async def step_anomaly(state): 
        return anomaly_agent.run(state)

    async def step_notification(state): 
        return notification_agent.run(state)

    async def step_scaling(state):
        # Only execute scaling if anomalies exist and manual approval is yes
        if state.get("anomaly_count", 0) > 0 and state.get("manual_proceed", False):
            return scaling_agent.run(state)
        return state

    # Add nodes
    graph.add_node("anomaly_detection", step_anomaly)
    graph.add_node("notify_team", step_notification)
    graph.add_node("scaling", step_scaling)

    # Define flow
    graph.set_entry_point("anomaly_detection")
    graph.add_edge("anomaly_detection", "notify_team")
    graph.add_edge("notify_team", "scaling")
    graph.add_edge("scaling", END)

    return graph.compile()
