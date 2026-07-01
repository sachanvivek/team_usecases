import httpx
import configparser
from datetime import datetime
import time
import pandas as pd
import asyncio

class MasterAPI:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config.read("src/agent_orch/utils/config.ini")
        self.username = self.config["ScienceLogicCredentials"]["username"]
        self.password = self.config["ScienceLogicCredentials"]["password"]
        self.domain_url = self.config["ScienceLogicCredentials"]["domain_url"]

    async def fetch_sciencelogic_metrics(self, client, vm_id, vpu_id, uri="30d", days: int = 30):
        """
        Calls ScienceLogic API, fetches CPU/memory/disk usage, returns a DataFrame.
        """
        url = (self.domain_url + uri).replace("30d", str(days) + "d")
        resp = await client.get(url, auth=(self.username, self.password), verify=False)
        raw_data = resp.json()

        # ScienceLogic response format handling
        data = raw_data.get("data", {}).get("0", {}) or raw_data.get("data", {}).get("d_used_percent", {})
        min_data, max_data, avg_data = data.get("min", {}), data.get("max", {}), data.get("avg", {})

        rows = []
        for ts in min_data.keys():
            date = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
            rows.append({
                "vpu_id": vpu_id,
                "vm_id": vm_id,
                "dou": date,
                "min_usage": round(float(min_data.get(ts, 0)), 2),
                "max_usage": round(float(max_data.get(ts, 0)), 2),
                "avg_usage": round(float(avg_data.get(ts, 0)), 2),
                "inserted_by": "MetricCollectorAgent",
                "inserted_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_by": "MetricCollectorAgent",
                "updated_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

        return pd.DataFrame(rows)

    async def fetch_multiple(self, uris: list, days: int = 30):
        """
        Fetch multiple URIs concurrently.
        uris = list of dicts [{"vm_id": ..., "vpu_id": ..., "uri": ...}]
        """
        async with httpx.AsyncClient(verify=False) as client:
            tasks = [
                self.fetch_sciencelogic_metrics(client, u["vm_id"], u["vpu_id"], u["uri"], days)
                for u in uris
            ]
            return await asyncio.gather(*tasks)
