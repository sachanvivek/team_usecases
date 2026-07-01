import pandas as pd
from datetime import datetime
from src.agent_orch.agents.base_agent import BaseAgent
from src.agent_orch.utils.DBConnect import DBConnect

class AnomalyDetectorAgent(BaseAgent):
    def __init__(self, threshold_percent=20):
        """
        threshold_percent: the allowed deviation percentage between forecasted and live usage
        """
        super().__init__(name="AnomalyDetectorAgent")
        self.db = DBConnect()
        self.threshold_percent = threshold_percent

    def run(self, state: dict) -> dict:
        self.validate_input(state, required_keys=["server_id", "resource_type"])

        server_id = state["server_id"]
        resource_type = state["resource_type"]

        # === Step 1: Fetch live usage from vm_usage_details ===
        live_query = f"""
            SELECT vu.dou, vu.avg_usage
            FROM vm_usage_details vu
            INNER JOIN vm_primary_uri vp ON vp.vpu_id = vu.vpu_id
            WHERE vp.vm_id = '{server_id}' 
              AND vp.resource_name = '{resource_type}'
            ORDER BY vu.dou DESC
            LIMIT 30;
        """
        live_data = pd.DataFrame(self.db.select(raw_query=live_query))
        if live_data.empty:
            self.logger.warning(f"No live data found for server {server_id} and resource {resource_type}")
            self.attach_result(state, "anomalies", [])
            self.attach_result(state, "anomaly_count", 0)
            return state
        live_data['dou'] = pd.to_datetime(live_data['dou'])

        # === Step 2: Fetch forecasted values from forecast_results ===
        forecast_query = f"""
            SELECT max(fr.p_avg_usage) AS forecasted_usage, fr.dop
            FROM forecast_results fr
            INNER JOIN forecast_runs fru ON fru.run_id = fr.run_id
            INNER JOIN vm_primary_uri vp ON vp.vpu_id = fru.vpu_id
            WHERE vp.vm_id = '{server_id}' 
              AND vp.resource_name = '{resource_type}' group by fr.dop
            ORDER BY fr.dop ASC
            LIMIT 60;
        """
        forecast_data = pd.DataFrame(self.db.select(raw_query=forecast_query))
        if forecast_data.empty:
            self.logger.warning(f"No forecast data found for server {server_id} and resource {resource_type}")
            self.attach_result(state, "anomalies", [])
            self.attach_result(state, "anomaly_count", 0)
            return state
        forecast_data['dop'] = pd.to_datetime(forecast_data['dop'])

        # Merge live and forecasted data
        df = pd.merge(live_data, forecast_data, left_on='dou', right_on='dop', how='inner')

        # === Step 3: Detect anomalies ===
        anomalies = []
        for idx, row in df.iterrows():
            live_val = row['avg_usage']
            forecast_val = row['forecasted_usage']
            deviation = abs(live_val - forecast_val) / (forecast_val + live_val) * 100
            if deviation > self.threshold_percent:
                anomalies.append({
                    "date": row['dou'].strftime("%Y-%m-%d"),
                    "live_usage": live_val,
                    "forecasted_usage": forecast_val,
                    "deviation_percent": round(deviation, 2)
                })

        # === Step 4: Attach result to state ===
        self.attach_result(state, "anomalies", anomalies)
        self.attach_result(state, "anomaly_count", len(anomalies))

        self.logger.info(f"Detected {len(anomalies)} anomalies for server {server_id} ({resource_type})")
        return state
