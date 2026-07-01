from dataclasses import dataclass
from langgraph.graph import StateGraph, END

# Import all agents
from src.agent_orch.agents.metric_collector import MetricCollectorAgent
from src.agent_orch.agents.forecasting import ForecastingAgent
from src.agent_orch.agents.rightsizing import RightsizingAgent
from src.agent_orch.agents.anomaly_detector import AnomalyDetectorAgent
from src.agent_orch.agents.cost_analyzer import CostAnalyzerAgent
from src.agent_orch.agents.dashboard_publisher import DashboardPublisherAgent
from src.agent_orch.agents.action_executor import ScalingAgent
from src.agent_orch.agents.notification import NotificationAgent
from src.agent_orch.agents.alerting import AlertingAgent


@dataclass(frozen=True)
class WorkflowBundle:
    """Container for the pre/post approval workflows."""
    pre_approval: object
    post_approval: object


def build_graph() -> WorkflowBundle:
    """
    Build LangGraph workflows for the agent orchestrator.

    Returns:
        WorkflowBundle: compiled workflows for the pre-approval and post-approval stages.
    """
    # === Initialize agents ===
    collector = MetricCollectorAgent()
    forecaster = ForecastingAgent()
    rightsize_calculator = RightsizingAgent()
    anomaly = AnomalyDetectorAgent()
    cost_analyzer = CostAnalyzerAgent()
    notification = NotificationAgent()
    alerting = AlertingAgent()

    executor = ScalingAgent()
    dashboard = DashboardPublisherAgent()

    # === Pre-approval workflow (collect -> forecast -> analyze -> notify) ===
    pre_graph = StateGraph(dict)

    async def step_collector(state):
        return await collector.run(state)

    async def step_forecaster(state):
        return await forecaster.run(state)

    async def step_rightsizer(state):
        return await rightsize_calculator.run(state)

    async def step_anomaly(state):
        return anomaly.run(state)

    async def step_cost_analyzer(state):
        return cost_analyzer.run(state)

    async def step_notification(state):
        # Notification dispatch (Slack/email) without altering manual flags
        return notification.run(state)

    async def step_alerting(state):
        return alerting.run(state)

    pre_graph.add_node("collector", step_collector)
    pre_graph.add_node("forecaster", step_forecaster)
    pre_graph.add_node("rightsize_calculator", step_rightsizer)
    pre_graph.add_node("anomaly_detector", step_anomaly)
    pre_graph.add_node("cost_analyzer", step_cost_analyzer)
    pre_graph.add_node("alerting", step_alerting)
    pre_graph.add_node("notification", step_notification)

    pre_graph.set_entry_point("collector")
    pre_graph.add_edge("collector", "forecaster")
    pre_graph.add_edge("forecaster", "rightsize_calculator")
    pre_graph.add_edge("rightsize_calculator", "anomaly_detector")
    pre_graph.add_edge("anomaly_detector", "alerting")
    pre_graph.add_edge("alerting", "cost_analyzer")
    pre_graph.add_edge("cost_analyzer", "notification")
    pre_graph.add_edge("notification", END)

    pre_workflow = pre_graph.compile()

    # === Post-approval workflow (apply decision -> optional scaling -> dashboard) ===
    post_graph = StateGraph(dict)

    async def step_approval_gate(state):
        # Ensure manual_proceed flag exists to drive subsequent routing
        state.setdefault("manual_proceed", False)
        return state

    async def step_executor(state):
        return executor.run(state)

    async def step_dashboard(state):
        return dashboard.run(state)

    post_graph.add_node("approval_gate", step_approval_gate)
    post_graph.add_node("executor", step_executor)
    post_graph.add_node("dashboard", step_dashboard)

    post_graph.set_entry_point("approval_gate")

    def route_after_approval(state: dict) -> str:
        recommendation = state.get("recommendation") or {}
        decision = recommendation.get("decision")
        if state.get("manual_proceed") and decision and decision != "no_change":
            return "run_scaling"
        return "skip_scaling"

    post_graph.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {
            "run_scaling": "executor",
            "skip_scaling": "dashboard",
        },
    )
    post_graph.add_edge("executor", "dashboard")
    post_graph.add_edge("dashboard", END)

    post_workflow = post_graph.compile()

    return WorkflowBundle(pre_approval=pre_workflow, post_approval=post_workflow)
