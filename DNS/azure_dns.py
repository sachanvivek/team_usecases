import logging
from config_loader import get_config

logger = logging.getLogger(__name__)


class AzureDNSClient:
    def __init__(self):
        cfg = get_config()
        self.enabled = cfg.getboolean("azure_dns", "enabled", fallback=False)
        if not self.enabled:
            return
        self.subscription_id = cfg.get("azure", "subscription_ids").split(",")[0].strip()
        self.tenant_id = cfg.get("azure", "tenant_id")
        self.client_id = cfg.get("azure", "client_id")
        self.client_secret = cfg.get("azure", "client_secret")
        self.resource_group = cfg.get("azure", "resource_group")
        self.zones = [z.strip() for z in cfg.get("azure_dns", "zones", fallback="").split(",") if z.strip()]
        self._dns_client = None
        self._credential = None

    def _get_credential(self):
        if self._credential is None:
            try:
                from azure.identity import ClientSecretCredential
                self._credential = ClientSecretCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                )
            except Exception as e:
                logger.error(f"Azure credential init failed: {e}")
        return self._credential

    def _get_dns_client(self):
        if self._dns_client is None:
            try:
                from azure.mgmt.dns import DnsManagementClient
                cred = self._get_credential()
                if cred:
                    self._dns_client = DnsManagementClient(cred, self.subscription_id)
            except Exception as e:
                logger.error(f"Azure DNS client init failed: {e}")
        return self._dns_client

    def list_zones(self) -> list:
        if not self.enabled:
            return []
        client = self._get_dns_client()
        if not client:
            return []
        try:
            zones = list(client.zones.list_by_resource_group(self.resource_group))
            return [{"name": z.name, "type": z.zone_type, "records": z.number_of_record_sets} for z in zones]
        except Exception as e:
            logger.error(f"Failed to list Azure DNS zones: {e}")
            return []

    def list_record_sets(self, zone_name: str) -> list:
        if not self.enabled:
            return []
        client = self._get_dns_client()
        if not client:
            return []
        try:
            records = list(client.record_sets.list_by_dns_zone(self.resource_group, zone_name))
            return [
                {"name": r.name, "type": r.type.split("/")[-1], "ttl": r.ttl, "fqdn": r.fqdn}
                for r in records
            ]
        except Exception as e:
            logger.error(f"Failed to list records for zone {zone_name}: {e}")
            return []

    def get_zone_summary(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "message": "Azure DNS not enabled"}
        zones = self.list_zones()
        return {
            "enabled": True,
            "zone_count": len(zones),
            "zones": zones,
            "resource_group": self.resource_group,
        }
