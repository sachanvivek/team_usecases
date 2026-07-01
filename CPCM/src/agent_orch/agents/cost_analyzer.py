from datetime import datetime
from src.agent_orch.agents.base_agent import BaseAgent

class CostAnalyzerAgent(BaseAgent):
    def __init__(self, name="cost_analyzer"):
        super().__init__(name)

    def run(self, state: dict) -> dict:
        self.validate_input(state, ["forecasts", "recommendation"])

        recommendation = state["recommendation"]
        resource_type = state.get("resource_type", "CPU")
        total_size = recommendation.get("total_size", 100)  # assume default 100 units
        scale_action = recommendation.get("decision", "no_action")
        scale_percent = recommendation.get("scale_percent", 0)

        # --- Simulate cost parameters (in $ per unit per hour) ---
        unit_cost_map = {
            "CPU": 0.05,     # $0.05 per vCPU per hour
            "Memory": 0.01,  # $0.01 per MB per hour
            "Disk": 0.001    # $0.001 per GB per hour
        }
        unit_cost = unit_cost_map.get(resource_type, 0.05)
        baseline_hours = 24 * 30  # assume 30-day monthly cost
        base_cost = total_size * unit_cost * baseline_hours

        if scale_action == "scale_down":
            new_size = total_size * (1 - (scale_percent / 100))
        elif scale_action == "scale_up":
            new_size = total_size * (1 + (scale_percent / 100))
        else:
            new_size = total_size

        new_cost = new_size * unit_cost * baseline_hours
        cost_difference = base_cost - new_cost
        percentage_change = (abs(cost_difference) / base_cost) * 100 if base_cost else 0

        cost_summary = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "resource_type": resource_type,
            "total_size": total_size,
            "unit_cost": unit_cost,
            "scale_action": scale_action,
            "scale_percent": scale_percent,
            "base_cost": round(base_cost, 2),
            "new_cost": round(new_cost, 2),
            "savings" if scale_action == "scale_down" else "additional_cost": round(abs(cost_difference), 2),
            "percentage_change": round(percentage_change, 2),
        }

        self.attach_result(state, "cost_impact", cost_summary)
        return state
