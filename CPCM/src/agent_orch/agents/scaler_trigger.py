from src.agent_orch.agents.base_agent import BaseAgent

class ScalerTriggerAgent(BaseAgent):
    def __init__(self, name="scaler_trigger"):
        super().__init__(name)

    def run(self, state: dict) -> dict:
        state["scaling_trigger"] = "need to implement this method in ScalerTriggerAgent"
        return state
