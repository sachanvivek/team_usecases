from abc import ABC, abstractmethod
import logging
import pandas as pd

class BaseAgent(ABC):
    """All agents must follow this interface"""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agent.{name}")

    @abstractmethod
    def run(self, state: dict) -> dict:
        """
        Every agent must implement this.

        - state: dict that holds workflow data
        - Must return updated state dict

        Convention:
        - Collector puts `pd.DataFrame` into state["data"]
        - Forecasting puts results into state["forecasts"]
        - Rightsizer puts recommendation into state["recommendation"]
        - etc.
        """
        pass

    def validate_input(self, state: dict, required_keys: list):
        """Helper: ensure required keys exist in state."""
        for key in required_keys:
            if key not in state:
                if key == "anomalies":
                    state["anomalies"] = []
                    continue
                raise ValueError(f"{self.name}: Missing required key '{key}' in state")

    def attach_dataframe(self, state: dict, df: pd.DataFrame, key: str = "data") -> dict:
        """Helper: attach a DataFrame to state in a consistent way."""
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"{self.name}: Expected DataFrame, got {type(df)}")
        state[key] = df
        return state
    
    def attach_result(self, state: dict, key: str, value) -> dict:
        """
        Generic helper: attach any kind of result to the state.
        Example:
        - self.attach_result(state, "forecasts", forecasts_dict)
        - self.attach_result(state, "errors", errors_dict)
        - self.attach_result(state, "recommendation", recommendation_string)
        """
        state[key] = value
        return state
