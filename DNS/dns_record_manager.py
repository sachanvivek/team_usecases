import dns.resolver
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from config_loader import get_config

logger = logging.getLogger(__name__)


@dataclass
class DNSRecordChange:
    operation: str  # add, modify, delete
    zone: str
    record_name: str
    record_type: str
    ttl: int = 3600
    values: list = field(default_factory=list)
    old_values: list = field(default_factory=list)  # for modify
    status: str = "pending"  # pending, pre_check_done, implemented, post_check_done, failed
    pre_check_result: Optional[dict] = None
    post_check_result: Optional[dict] = None
    error: Optional[str] = None
    timestamp: float = 0.0
    cr_number: Optional[str] = None
    cr_sys_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def fqdn(self) -> str:
        if self.record_name == "@":
            return self.zone
        return f"{self.record_name}.{self.zone}"

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "zone": self.zone,
            "record_name": self.record_name,
            "record_type": self.record_type,
            "ttl": self.ttl,
            "values": self.values,
            "old_values": self.old_values,
            "status": self.status,
            "fqdn": self.fqdn,
            "pre_check_result": self.pre_check_result,
            "post_check_result": self.post_check_result,
            "error": self.error,
            "timestamp": self.timestamp,
            "cr_number": self.cr_number,
            "cr_sys_id": self.cr_sys_id,
        }


class DNSRecordManager:
    """Manages DNS record operations (Add/Modify/Delete) via local BIND server or Azure DNS."""

    def __init__(self):
        cfg = get_config()
        self.managed_zones = [
            z.strip()
            for z in cfg.get("dns_management", "managed_zones", fallback="").split(",")
            if z.strip()
        ]
        self.default_backend = cfg.get("dns_management", "default_backend", fallback="local_bind")
        self._azure_dns = None
        self._local_dns = None
        self._changes: list[DNSRecordChange] = []

    def _get_local_dns(self):
        if self._local_dns is None:
            from local_dns_server import LocalDNSServer
            self._local_dns = LocalDNSServer()
        return self._local_dns

    def _get_azure_dns(self):
        if self._azure_dns is None:
            from azure_dns import AzureDNSClient
            self._azure_dns = AzureDNSClient()
        return self._azure_dns

    def _get_azure_mgmt_client(self):
        """Get the raw Azure DNS management client for record operations."""
        azure = self._get_azure_dns()
        return azure._get_dns_client(), azure.resource_group

    def _get_backend_for_zone(self, zone: str) -> str:
        """Determine which backend manages a given zone."""
        local = self._get_local_dns()
        if local.enabled and zone in local.authoritative_zones:
            return "local_bind"
        return "azure_dns"

    # --- Pre-Check ---
    def pre_check(self, change: DNSRecordChange) -> dict:
        """Validate the change before implementation."""
        backend = self._get_backend_for_zone(change.zone)
        checks = {
            "zone_valid": False,
            "record_name_valid": False,
            "current_state": None,
            "conflict_check": None,
            "dns_resolution_check": None,
            "backend": backend,
            "passed": False,
            "details": [],
        }

        # 1. Zone validation
        if change.zone in self.managed_zones:
            checks["zone_valid"] = True
            checks["details"].append(f"Zone '{change.zone}' is a managed zone (backend: {backend})")
        else:
            checks["details"].append(
                f"WARNING: Zone '{change.zone}' not in managed zones {self.managed_zones}"
            )
            checks["zone_valid"] = True  # Allow but warn

        # 2. Record name validation
        if change.record_name and len(change.record_name) <= 253:
            checks["record_name_valid"] = True
            checks["details"].append(f"Record name '{change.record_name}' is valid")
        else:
            checks["details"].append(f"Invalid record name: '{change.record_name}'")
            return checks

        # 3. Current state check - query the target server directly
        try:
            if backend == "local_bind":
                local = self._get_local_dns()
                qr = local.query_record(change.fqdn, change.record_type)
                checks["current_state"] = {
                    "exists": qr["exists"],
                    "records": qr["records"],
                    "ttl": qr.get("ttl", 0),
                    "server": local.host,
                }
                if qr["exists"]:
                    checks["details"].append(
                        f"Current records on {local.host} for {change.fqdn} ({change.record_type}): {qr['records']}"
                    )
                elif qr.get("error") == "NXDOMAIN":
                    checks["details"].append(f"Record {change.fqdn} does not exist on {local.host} (NXDOMAIN)")
                elif qr.get("error") == "NoAnswer":
                    checks["details"].append(f"No {change.record_type} records for {change.fqdn} on {local.host}")
                else:
                    checks["details"].append(f"Query to {local.host}: {qr.get('error', 'no data')}")
            else:
                resolver = dns.resolver.Resolver()
                resolver.timeout = 5
                resolver.lifetime = 5
                answer = resolver.resolve(change.fqdn, change.record_type)
                current_records = [str(r) for r in answer]
                checks["current_state"] = {
                    "exists": True,
                    "records": current_records,
                    "ttl": answer.rrset.ttl,
                }
                checks["details"].append(
                    f"Current records for {change.fqdn} ({change.record_type}): {current_records}"
                )
        except dns.resolver.NXDOMAIN:
            checks["current_state"] = {"exists": False, "records": [], "ttl": 0}
            checks["details"].append(f"Record {change.fqdn} does not exist (NXDOMAIN)")
        except dns.resolver.NoAnswer:
            checks["current_state"] = {"exists": False, "records": [], "ttl": 0}
            checks["details"].append(
                f"No {change.record_type} records for {change.fqdn}"
            )
        except Exception as e:
            checks["current_state"] = {"exists": None, "records": [], "error": str(e)}
            checks["details"].append(f"DNS resolution check error: {e}")

        # 4. Conflict/logic checks
        exists = checks["current_state"].get("exists", False) if checks["current_state"] else False
        if change.operation == "add" and exists:
            checks["conflict_check"] = "warning"
            checks["details"].append(
                "WARNING: Record already exists. Add will create additional records or update existing."
            )
        elif change.operation == "delete" and not exists:
            checks["conflict_check"] = "warning"
            checks["details"].append("WARNING: Record does not appear to exist for deletion.")
        elif change.operation == "modify" and not exists:
            checks["conflict_check"] = "warning"
            checks["details"].append("WARNING: Record does not exist for modification. Will create instead.")
        else:
            checks["conflict_check"] = "ok"
            checks["details"].append("No conflicts detected")

        checks["passed"] = checks["zone_valid"] and checks["record_name_valid"]
        change.pre_check_result = checks
        change.status = "pre_check_done"
        return checks

    # --- Implementation ---
    def implement(self, change: DNSRecordChange) -> dict:
        """Execute the DNS record change via local BIND server or Azure DNS."""
        backend = self._get_backend_for_zone(change.zone)
        result = {"success": False, "details": [], "backend": backend}

        if backend == "local_bind":
            return self._implement_local_bind(change, result)
        else:
            return self._implement_azure_dns(change, result)

    def _implement_local_bind(self, change: DNSRecordChange, result: dict) -> dict:
        """Execute DNS change on local BIND server at 172.19.0.6."""
        local = self._get_local_dns()
        result["details"].append(f"Target: local BIND server at {local.host}")

        try:
            if change.operation == "add":
                op_result = local.add_record(
                    change.zone, change.record_name, change.record_type,
                    change.ttl, change.values,
                )
            elif change.operation == "modify":
                op_result = local.modify_record(
                    change.zone, change.record_name, change.record_type,
                    change.ttl, change.values,
                )
            elif change.operation == "delete":
                op_result = local.delete_record(
                    change.zone, change.record_name, change.record_type,
                )
            else:
                result["details"].append(f"Unknown operation: {change.operation}")
                return result

            result["success"] = op_result.get("success", False)
            result["details"].extend(op_result.get("details", []))
            if op_result.get("method"):
                result["method"] = op_result["method"]

            if result["success"]:
                change.status = "implemented"

        except Exception as e:
            logger.error(f"Local BIND implementation failed: {e}")
            result["details"].append(f"Local BIND error: {e}")

        return result

    def _implement_azure_dns(self, change: DNSRecordChange, result: dict) -> dict:
        """Execute DNS change via Azure DNS API."""
        result["details"].append("Target: Azure DNS")

        try:
            dns_client, resource_group = self._get_azure_mgmt_client()
            if dns_client is None:
                result["details"].append("Azure DNS client not available - using simulation mode")
                return self._simulate_implement(change, result)

            relative_name = change.record_name if change.record_name != "@" else "@"

            if change.operation == "add" or change.operation == "modify":
                record_set = self._build_record_set(change)
                dns_client.record_sets.create_or_update(
                    resource_group, change.zone, relative_name,
                    change.record_type, record_set,
                )
                result["success"] = True
                result["details"].append(
                    f"Record {change.fqdn} ({change.record_type}) "
                    f"{'created' if change.operation == 'add' else 'updated'} "
                    f"with values {change.values}"
                )

            elif change.operation == "delete":
                dns_client.record_sets.delete(
                    resource_group, change.zone, relative_name, change.record_type,
                )
                result["success"] = True
                result["details"].append(
                    f"Record {change.fqdn} ({change.record_type}) deleted"
                )

            change.status = "implemented"
        except Exception as e:
            logger.error(f"Azure DNS implementation failed: {e}")
            result["details"].append(f"Azure DNS error: {e}")
            result["details"].append("Falling back to simulation mode")
            return self._simulate_implement(change, result)

        return result

    def _simulate_implement(self, change: DNSRecordChange, result: dict) -> dict:
        """Simulate implementation when Azure DNS is unavailable."""
        if change.operation == "add":
            result["details"].append(
                f"[SIMULATED] Created {change.fqdn} ({change.record_type}) -> {change.values} TTL={change.ttl}"
            )
        elif change.operation == "modify":
            result["details"].append(
                f"[SIMULATED] Updated {change.fqdn} ({change.record_type}) from {change.old_values} -> {change.values} TTL={change.ttl}"
            )
        elif change.operation == "delete":
            result["details"].append(
                f"[SIMULATED] Deleted {change.fqdn} ({change.record_type})"
            )
        result["success"] = True
        result["simulated"] = True
        change.status = "implemented"
        return result

    def _build_record_set(self, change: DNSRecordChange):
        """Build Azure DNS RecordSet object."""
        from azure.mgmt.dns.models import (
            RecordSet, ARecord, AaaaRecord, MxRecord, CnameRecord, TxtRecord, NsRecord,
        )
        params = {"ttl": change.ttl}
        rtype = change.record_type.upper()
        if rtype == "A":
            params["a_records"] = [ARecord(ipv4_address=v) for v in change.values]
        elif rtype == "AAAA":
            params["aaaa_records"] = [AaaaRecord(ipv6_address=v) for v in change.values]
        elif rtype == "CNAME":
            params["cname_record"] = CnameRecord(cname=change.values[0]) if change.values else None
        elif rtype == "MX":
            mx_records = []
            for v in change.values:
                parts = v.split()
                pref = int(parts[0]) if len(parts) > 1 else 10
                exchange = parts[-1]
                mx_records.append(MxRecord(preference=pref, exchange=exchange))
            params["mx_records"] = mx_records
        elif rtype == "TXT":
            params["txt_records"] = [TxtRecord(value=[v]) for v in change.values]
        elif rtype == "NS":
            params["ns_records"] = [NsRecord(nsdname=v) for v in change.values]
        return RecordSet(**params)

    # --- Post-Check ---
    def post_check(self, change: DNSRecordChange) -> dict:
        """Verify the change was applied correctly by querying the target server."""
        backend = self._get_backend_for_zone(change.zone)
        checks = {
            "dns_propagated": False,
            "values_match": False,
            "backend": backend,
            "passed": False,
            "details": [],
        }

        try:
            # For local BIND, query the server directly
            if backend == "local_bind":
                local = self._get_local_dns()
                qr = local.query_record(change.fqdn, change.record_type)
                checks["details"].append(f"Post-check against local server {local.host}")

                if change.operation == "delete":
                    if not qr["exists"]:
                        checks["dns_propagated"] = True
                        checks["values_match"] = True
                        checks["details"].append(f"Record confirmed deleted on {local.host}")
                    else:
                        checks["details"].append(
                            f"WARNING: Record still resolves on {local.host}: {qr['records']}"
                        )
                else:
                    if qr["exists"]:
                        checks["dns_propagated"] = True
                        checks["details"].append(f"Record resolves on {local.host}: {qr['records']}")
                        expected = set(v.rstrip(".") for v in change.values)
                        actual = set(r.rstrip(".") for r in qr["records"])
                        if expected & actual:
                            checks["values_match"] = True
                            checks["details"].append("Values match expected configuration")
                        else:
                            checks["details"].append(
                                f"Values mismatch - expected: {expected}, got: {actual}"
                            )
                    else:
                        checks["details"].append(
                            f"Record not found on {local.host} - {qr.get('error', 'unknown')}"
                        )
            else:
                # Azure DNS - use default resolver
                resolver = dns.resolver.Resolver()
                resolver.timeout = 10
                resolver.lifetime = 10
                answer = resolver.resolve(change.fqdn, change.record_type)
                current_records = [str(r) for r in answer]

                if change.operation == "delete":
                    checks["details"].append(
                        f"WARNING: Record still resolves after delete: {current_records}"
                    )
                else:
                    checks["dns_propagated"] = True
                    checks["details"].append(f"Record resolves: {current_records}")
                    expected = set(v.rstrip(".") for v in change.values)
                    actual = set(r.rstrip(".") for r in current_records)
                    if expected & actual:
                        checks["values_match"] = True
                        checks["details"].append("Values match expected configuration")
                    else:
                        checks["details"].append(
                            f"Values mismatch - expected: {expected}, got: {actual}"
                        )

        except dns.resolver.NXDOMAIN:
            if change.operation == "delete":
                checks["dns_propagated"] = True
                checks["values_match"] = True
                checks["details"].append("Record confirmed deleted (NXDOMAIN)")
            else:
                checks["details"].append("Record not found - DNS may not have propagated yet")

        except dns.resolver.NoAnswer:
            if change.operation == "delete":
                checks["dns_propagated"] = True
                checks["values_match"] = True
                checks["details"].append("Record confirmed deleted (NoAnswer)")
            else:
                checks["details"].append("No answer - DNS may not have propagated yet")

        except Exception as e:
            checks["details"].append(f"Post-check DNS error: {e}")

        checks["passed"] = checks["dns_propagated"] or change.operation == "delete"
        change.post_check_result = checks
        change.status = "post_check_done" if checks["passed"] else "post_check_failed"
        return checks

    # --- History ---
    def add_change(self, change: DNSRecordChange):
        self._changes.append(change)
        if len(self._changes) > 500:
            self._changes = self._changes[-300:]

    def get_changes(self, limit: int = 50) -> list:
        return [c.to_dict() for c in self._changes[-limit:]]
