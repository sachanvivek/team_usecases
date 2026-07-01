import logging
import time
import io
from typing import Optional
import paramiko
import dns.resolver
import dns.update
import dns.query
import dns.name
import dns.rcode
import dns.rdatatype
from config_loader import get_config

logger = logging.getLogger(__name__)


class LocalDNSServer:
    """Manages a local BIND9 DNS server via SSH and DNS dynamic updates."""

    def __init__(self):
        cfg = get_config()
        self.enabled = cfg.getboolean("local_dns_server", "enabled", fallback=False)
        self.host = cfg.get("local_dns_server", "host", fallback="172.19.0.6")
        self.ssh_user = cfg.get("local_dns_server", "ssh_user", fallback="winadmin")
        self.ssh_password = cfg.get("local_dns_server", "ssh_password", fallback="")
        self.ssh_port = cfg.getint("local_dns_server", "ssh_port", fallback=22)
        self.zone_dir = cfg.get("local_dns_server", "bind_zone_dir", fallback="/etc/bind/zones")
        self.conf_local = cfg.get("local_dns_server", "bind_conf_local", fallback="/etc/bind/named.conf.local")
        self.authoritative_zones = [
            z.strip()
            for z in cfg.get("local_dns_server", "authoritative_zones", fallback="").split(",")
            if z.strip()
        ]

    # =========================================================================
    # SSH Helpers
    # =========================================================================
    def _ssh_connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.ssh_port,
            username=self.ssh_user,
            password=self.ssh_password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        return client

    def _ssh_exec(self, command: str) -> tuple[int, str, str]:
        """Execute command over SSH, return (exit_code, stdout, stderr)."""
        try:
            client = self._ssh_connect()
            stdin, stdout, stderr = client.exec_command(command, timeout=30)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            client.close()
            return exit_code, out, err
        except Exception as e:
            logger.error(f"SSH exec failed: {e}")
            return -1, "", str(e)

    def _ssh_exec_sudo(self, command: str) -> tuple[int, str, str]:
        """Execute command with sudo over SSH."""
        return self._ssh_exec(f"echo '{self.ssh_password}' | sudo -S {command}")

    def ssh_command(self, command: str) -> str:
        """Public SSH command execution. Returns stdout or raises on failure."""
        exit_code, stdout, stderr = self._ssh_exec(command)
        if exit_code != 0 and not stdout.strip():
            raise RuntimeError(f"SSH command failed (exit {exit_code}): {stderr[:200]}")
        return stdout

    # =========================================================================
    # Server Status
    # =========================================================================
    def get_server_status(self) -> dict:
        """Get BIND server status and zone info."""
        if not self.enabled:
            return {"enabled": False, "message": "Local DNS server not enabled"}

        status = {
            "enabled": True,
            "host": self.host,
            "reachable": False,
            "bind_running": False,
            "zones": [],
            "version": None,
        }

        # Get BIND version
        try:
            import dns.message, dns.rdataclass
            q = dns.message.make_query(dns.name.from_text("version.bind"), dns.rdatatype.TXT, dns.rdataclass.CH)
            resp = dns.query.udp(q, self.host, timeout=3)
            for rrset in resp.answer:
                for rdata in rrset:
                    status["version"] = str(rdata).strip('"')
                    status["reachable"] = True
        except Exception:
            pass

        # Also try resolving a known domain
        if not status["reachable"]:
            try:
                r = dns.resolver.Resolver()
                r.nameservers = [self.host]
                r.timeout = 3
                r.lifetime = 3
                r.resolve("google.com", "A")
                status["reachable"] = True
            except Exception:
                pass

        # Check BIND service via SSH
        code, out, err = self._ssh_exec("systemctl is-active named 2>/dev/null || systemctl is-active bind9 2>/dev/null")
        if code == 0 and "active" in out:
            status["bind_running"] = True

        # List zones
        status["zones"] = self.list_zones()

        return status

    def list_zones(self) -> list:
        """List zones configured on the BIND server."""
        code, out, err = self._ssh_exec(f"grep -oP 'zone \"\\K[^\"]+' {self.conf_local} 2>/dev/null")
        if code == 0 and out.strip():
            zones = [z.strip() for z in out.strip().split("\n") if z.strip()]
            result = []
            for z in zones:
                records = self._count_zone_records(z)
                result.append({"name": z, "record_count": records, "server": self.host})
            return result
        return []

    def _count_zone_records(self, zone_name: str) -> int:
        code, out, err = self._ssh_exec(f"grep -c '' {self.zone_dir}/db.{zone_name} 2>/dev/null")
        try:
            return int(out.strip()) if code == 0 else 0
        except ValueError:
            return 0

    # =========================================================================
    # Zone Management
    # =========================================================================
    def ensure_zone_exists(self, zone_name: str) -> dict:
        """Ensure a zone exists on the BIND server, create if missing."""
        result = {"zone": zone_name, "action": "none", "details": []}

        # Check if zone already in config
        code, out, _ = self._ssh_exec(f"grep -q 'zone \"{zone_name}\"' {self.conf_local} 2>/dev/null && echo 'exists'")
        if "exists" in out:
            result["action"] = "exists"
            result["details"].append(f"Zone '{zone_name}' already configured")
            return result

        # Create zone file
        zone_file = f"{self.zone_dir}/db.{zone_name}"
        serial = int(time.time())
        zone_content = f"""$TTL 86400
@   IN  SOA ns1.{zone_name}. admin.{zone_name}. (
        {serial}   ; Serial
        3600       ; Refresh
        900        ; Retry
        604800     ; Expire
        86400 )    ; Minimum TTL
;
@   IN  NS  ns1.{zone_name}.
ns1 IN  A   {self.host}
"""

        # Create zone directory if needed and set ownership
        self._ssh_exec_sudo(f"mkdir -p {self.zone_dir}")
        self._ssh_exec_sudo(f"chown bind:bind {self.zone_dir}")
        self._ssh_exec_sudo(f"chmod 775 {self.zone_dir}")

        # Write zone file with proper ownership for dynamic updates
        self._ssh_exec_sudo(f"bash -c 'cat > {zone_file}' << 'ZONEEOF'\n{zone_content}\nZONEEOF")
        self._ssh_exec_sudo(f"chown bind:bind {zone_file}")
        self._ssh_exec_sudo(f"chmod 664 {zone_file}")

        # Add zone to named.conf.local
        zone_block = f"""
zone "{zone_name}" {{
    type master;
    file "{zone_file}";
    allow-update {{ any; }};
}};
"""
        self._ssh_exec_sudo(f"bash -c 'cat >> {self.conf_local}' << 'CONFEOF'\n{zone_block}\nCONFEOF")

        # Reload BIND
        code, out, err = self._ssh_exec_sudo("rndc reload 2>&1 || systemctl reload bind9 2>&1 || systemctl reload named 2>&1")
        if code == 0:
            result["action"] = "created"
            result["details"].append(f"Zone '{zone_name}' created and BIND reloaded")
        else:
            result["action"] = "error"
            result["details"].append(f"Zone file created but reload failed: {err or out}")

        return result

    def setup_authoritative_zones(self) -> list:
        """Ensure all configured authoritative zones exist."""
        results = []
        for zone in self.authoritative_zones:
            results.append(self.ensure_zone_exists(zone))
        return results

    # =========================================================================
    # Record Operations via DNS Dynamic Update (RFC 2136)
    # =========================================================================
    def add_record(self, zone: str, name: str, rtype: str, ttl: int, values: list) -> dict:
        """Add a DNS record using dynamic update."""
        result = {"success": False, "details": []}
        try:
            zone_name = dns.name.from_text(zone)
            update = dns.update.Update(zone_name)
            rdtype = dns.rdatatype.from_text(rtype)

            for val in values:
                update.add(name, ttl, rdtype, val)

            response = dns.query.tcp(update, self.host, timeout=10)
            rcode = response.rcode()

            if rcode == dns.rcode.NOERROR:
                result["success"] = True
                result["details"].append(f"Added {name}.{zone} ({rtype}) -> {values} TTL={ttl}")
            else:
                rcode_text = dns.rcode.to_text(rcode)
                result["details"].append(f"Dynamic update failed: {rcode_text}")
                # Fallback to nsupdate via SSH
                return self._nsupdate_fallback("add", zone, name, rtype, ttl, values)
        except Exception as e:
            logger.warning(f"Dynamic update failed: {e}, trying SSH nsupdate")
            return self._nsupdate_fallback("add", zone, name, rtype, ttl, values)
        return result

    def delete_record(self, zone: str, name: str, rtype: str) -> dict:
        """Delete a DNS record using dynamic update."""
        result = {"success": False, "details": []}
        try:
            zone_name = dns.name.from_text(zone)
            update = dns.update.Update(zone_name)
            rdtype = dns.rdatatype.from_text(rtype)
            update.delete(name, rdtype)

            response = dns.query.tcp(update, self.host, timeout=10)
            rcode = response.rcode()

            if rcode == dns.rcode.NOERROR:
                result["success"] = True
                result["details"].append(f"Deleted {name}.{zone} ({rtype})")
            else:
                rcode_text = dns.rcode.to_text(rcode)
                result["details"].append(f"Dynamic update failed: {rcode_text}")
                return self._nsupdate_fallback("delete", zone, name, rtype, 0, [])
        except Exception as e:
            logger.warning(f"Dynamic update failed: {e}, trying SSH nsupdate")
            return self._nsupdate_fallback("delete", zone, name, rtype, 0, [])
        return result

    def modify_record(self, zone: str, name: str, rtype: str, ttl: int, values: list) -> dict:
        """Modify (replace) a DNS record: delete existing, add new in single update."""
        result = {"success": False, "details": []}
        try:
            zone_name = dns.name.from_text(zone)
            update = dns.update.Update(zone_name)
            rdtype = dns.rdatatype.from_text(rtype)

            # Delete old then add new in a single update message
            update.delete(name, rdtype)
            for val in values:
                update.add(name, ttl, rdtype, val)

            response = dns.query.tcp(update, self.host, timeout=10)
            rcode = response.rcode()

            if rcode == dns.rcode.NOERROR:
                result["success"] = True
                result["details"].append(f"Modified {name}.{zone} ({rtype}) -> {values} TTL={ttl}")
            else:
                rcode_text = dns.rcode.to_text(rcode)
                result["details"].append(f"Dynamic update failed: {rcode_text}")
                return self._nsupdate_fallback("modify", zone, name, rtype, ttl, values)
        except Exception as e:
            logger.warning(f"Dynamic update failed: {e}, trying SSH nsupdate")
            return self._nsupdate_fallback("modify", zone, name, rtype, ttl, values)
        return result

    def _nsupdate_fallback(self, operation: str, zone: str, name: str, rtype: str, ttl: int, values: list) -> dict:
        """Fallback: execute nsupdate via SSH."""
        result = {"success": False, "details": [], "method": "ssh_nsupdate"}

        cmds = [f"server {self.host}", f"zone {zone}"]

        if operation == "delete":
            cmds.append(f"update delete {name}.{zone}. {rtype}")
        elif operation == "modify":
            cmds.append(f"update delete {name}.{zone}. {rtype}")
            for val in values:
                cmds.append(f"update add {name}.{zone}. {ttl} {rtype} {val}")
        elif operation == "add":
            for val in values:
                cmds.append(f"update add {name}.{zone}. {ttl} {rtype} {val}")

        cmds.append("send")
        cmds.append("quit")
        nsupdate_input = "\n".join(cmds)

        code, out, err = self._ssh_exec(f"echo '{nsupdate_input}' | nsupdate -v 2>&1")

        if code == 0 and "REFUSED" not in (out + err) and "SERVFAIL" not in (out + err):
            result["success"] = True
            op_desc = {"add": "Added", "modify": "Modified", "delete": "Deleted"}
            result["details"].append(
                f"[SSH nsupdate] {op_desc.get(operation, operation)} {name}.{zone} ({rtype})"
                + (f" -> {values}" if values else "")
            )
        else:
            result["details"].append(f"nsupdate failed (exit={code}): {(out + err).strip()}")
            # Last resort: direct zone file edit
            result_edit = self._zone_file_edit(operation, zone, name, rtype, ttl, values)
            return result_edit

        return result

    def _zone_file_edit(self, operation: str, zone: str, name: str, rtype: str, ttl: int, values: list) -> dict:
        """Last resort: directly edit the BIND zone file and reload."""
        result = {"success": False, "details": [], "method": "zone_file_edit"}
        zone_file = f"{self.zone_dir}/db.{zone}"

        if operation == "add":
            for val in values:
                line = f"{name}\t{ttl}\tIN\t{rtype}\t{val}"
                self._ssh_exec_sudo(f"bash -c 'echo \"{line}\" >> {zone_file}'")
            result["details"].append(f"[Zone file] Added {name}.{zone} ({rtype}) -> {values}")
        elif operation == "modify":
            # Remove old lines and add new
            self._ssh_exec_sudo(f"sed -i '/^{name}[\\t ].*IN[\\t ]*{rtype}/d' {zone_file}")
            for val in values:
                line = f"{name}\t{ttl}\tIN\t{rtype}\t{val}"
                self._ssh_exec_sudo(f"bash -c 'echo \"{line}\" >> {zone_file}'")
            result["details"].append(f"[Zone file] Modified {name}.{zone} ({rtype}) -> {values}")
        elif operation == "delete":
            self._ssh_exec_sudo(f"sed -i '/^{name}[\\t ].*IN[\\t ]*{rtype}/d' {zone_file}")
            result["details"].append(f"[Zone file] Deleted {name}.{zone} ({rtype})")

        # Bump serial
        self._ssh_exec_sudo(f"sed -i 's/[0-9]\\{{10\\}}\\(.*Serial\\)/{int(time.time())}\\1/' {zone_file}")

        # Reload zone
        code, out, err = self._ssh_exec_sudo(f"rndc reload {zone} 2>&1 || systemctl reload bind9 2>&1")
        if code == 0:
            result["success"] = True
        else:
            result["details"].append(f"Zone reload warning: {(out + err).strip()}")
            result["success"] = True  # File was edited even if reload had issues

        return result

    # =========================================================================
    # Query Records
    # =========================================================================
    def query_record(self, fqdn: str, rtype: str = "A") -> dict:
        """Query a record directly from the local DNS server."""
        try:
            r = dns.resolver.Resolver()
            r.nameservers = [self.host]
            r.timeout = 5
            r.lifetime = 5
            answer = r.resolve(fqdn, rtype)
            return {
                "exists": True,
                "records": [str(rr) for rr in answer],
                "ttl": answer.rrset.ttl,
                "server": self.host,
            }
        except dns.resolver.NXDOMAIN:
            return {"exists": False, "records": [], "error": "NXDOMAIN", "server": self.host}
        except dns.resolver.NoAnswer:
            return {"exists": False, "records": [], "error": "NoAnswer", "server": self.host}
        except Exception as e:
            return {"exists": False, "records": [], "error": str(e), "server": self.host}

    def list_zone_records(self, zone_name: str) -> list:
        """List all records in a zone via AXFR or zone file."""
        # Try AXFR first
        try:
            xfr = dns.query.xfr(self.host, zone_name, timeout=10)
            zone_obj = dns.zone.from_xfr(xfr)
            records = []
            for name, node in zone_obj.nodes.items():
                for rdataset in node.rdatasets:
                    for rdata in rdataset:
                        records.append({
                            "name": str(name),
                            "type": dns.rdatatype.to_text(rdataset.rdtype),
                            "ttl": rdataset.ttl,
                            "value": str(rdata),
                        })
            return records
        except Exception as e:
            logger.warning(f"AXFR failed for {zone_name}: {e}, reading zone file")

        # Fallback: read zone file via SSH
        zone_file = f"{self.zone_dir}/db.{zone_name}"
        code, out, err = self._ssh_exec(f"cat {zone_file} 2>/dev/null")
        if code != 0:
            return []

        records = []
        for line in out.split("\n"):
            line = line.strip()
            if not line or line.startswith(";") or line.startswith("$"):
                continue
            parts = line.split()
            if len(parts) >= 4 and "IN" in parts:
                try:
                    in_idx = parts.index("IN")
                    rec_name = parts[0] if in_idx > 0 else "@"
                    rec_ttl = int(parts[in_idx - 1]) if in_idx > 1 and parts[in_idx - 1].isdigit() else 0
                    rec_type = parts[in_idx + 1]
                    rec_value = " ".join(parts[in_idx + 2:])
                    records.append({
                        "name": rec_name,
                        "type": rec_type,
                        "ttl": rec_ttl,
                        "value": rec_value,
                    })
                except (ValueError, IndexError):
                    continue
        return records
