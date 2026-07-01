import asyncio
import configparser
import time
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth

from src.agent_orch.utils import DBConnect
#from src.agent_orch.utils import science_logic_api_import


class MetricCollectorAgent:
    def __init__(self):
        self.db = DBConnect.DBConnect()
        #self.sl = science_logic_api_import.science_logic_api_import()

    async def run(self, state: dict):
        """
        Collects VM usage metrics from ScienceLogic API and stores them in DB.
        If state contains a `server_id`, will only fetch for that server.
        """
        print("[MetricCollectorAgent] Starting metric collection...")

        # Query servers from DB
        if "server_id" in state:
            query = f"SELECT vpu_id, vm_id, uri FROM vm_primary_uri WHERE vm_id='{state['server_id']}'"
            results = self.db.select(raw_query=query)
        else:
            results = self.db.select(
                table="vm_primary_uri",
                columns=["vpu_id", "vm_id", "uri"]
            )

        for row in results:
            vpu_id = row["vpu_id"]
            vm_id = row["vm_id"]
            uri = row["uri"]

            try:
                print(f"[MetricCollectorAgent] Fetching data for VM: {vm_id}, VPU: {vpu_id}")
                await self.get_server_data(vm_id, vpu_id, uri, 400, "vm_usage_details")
                print("--------------------------------------------------------------")
            except Exception as e:
                print(f"[MetricCollectorAgent] ERROR for VM {vm_id}: {e}")
                print("[MetricCollectorAgent] Skipping and continuing to next server...")
                continue  # skip this one and move on

        self.db.close()
        print("[MetricCollectorAgent] Metric collection completed.")

        return state  # Pass state to next agent

    async def get_server_data(self, server_id, vpu_id, uri, days, table):
        """
        Fetch server metrics from ScienceLogic API and store in DB.
        """
        config = configparser.ConfigParser()
        config.read("src/agent_orch/utils/config.ini")

        Username = config["ScienceLogicCredentials"]["username"]
        Password = config["ScienceLogicCredentials"]["password"]
        url = config["ScienceLogicCredentials"]["domain_url"] + uri
        url = url.replace("30d", f"{days}d")

        response = await asyncio.to_thread(
            requests.get, url, auth=HTTPBasicAuth(Username, Password), verify=False
        )
        raw_data = response.json()

        data = raw_data.get("data", {}).get("0", {})
        if not data:
            data = raw_data.get("data", {}).get("d_used_percent", {})

        min_data = data.get("min", {})
        max_data = data.get("max", {})
        avg_data = data.get("avg", {})

        db = DBConnect.DBConnect()

        for ts in set(min_data.keys()):
            date = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
            min_val = round(float(min_data.get(ts, 0)), 2)
            max_val = round(float(max_data.get(ts, 0)), 2)
            avg_val = round(float(avg_data.get(ts, 0)), 2)

            in_data = {
                "vpu_id": vpu_id,
                "dou": date,
                "min_usage": min_val,
                "max_usage": max_val,
                "avg_usage": avg_val,
                "inserted_by": "Server",
                "inserted_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_by": "Server",
                "updated_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            db.insert_ignore(table, in_data, unique_columns=["vpu_id", "dou"])
            print(f"{date} | {server_id} | {min_val} | {max_val} | {avg_val}")

        db.close()
