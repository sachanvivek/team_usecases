"""
Hyper-V VM Resize Module
========================
Connects to a remote Hyper-V host via WinRM (PowerShell Remoting) and changes
the VM's RAM.  The host must have WinRM enabled and the credentials must have
Hyper-V management permissions.

Requirements:
    pip install pywinrm
"""

import configparser
import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import winrm
except ImportError:
    winrm = None  # Handled at call-time with a friendly error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "utils" / "config.ini"


def _load_hyperv_config() -> Dict[str, str]:
    """Read the [HYPERV] section from config.ini."""
    config = configparser.ConfigParser()
    config.read(str(_CONFIG_PATH))

    if "HYPERV" not in config:
        raise RuntimeError(
            f"[HYPERV] section not found in {_CONFIG_PATH}. "
            "Add host, username, and password."
        )

    section = config["HYPERV"]
    return {
        "host": section.get("host", "").strip(),
        "username": section.get("username", "").strip(),
        "password": section.get("password", "").strip(),
        "default_ram_mb": int(section.get("default_ram_mb", "1024")),
    }


# ---------------------------------------------------------------------------
# PowerShell execution via WinRM
# ---------------------------------------------------------------------------
def _run_ps(session, script: str) -> Dict[str, Any]:
    """Run a PowerShell script on the remote host and return parsed output."""
    result = session.run_ps(script)
    stdout = result.std_out.decode("utf-8", errors="replace").strip()
    stderr = result.std_err.decode("utf-8", errors="replace").strip()
    return {
        "status_code": result.status_code,
        "stdout": stdout,
        "stderr": stderr,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def resize_hyperv_vm(
    vm_name: str,
    new_ram_mb: Optional[int] = None,
    host: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resize a Hyper-V VM's RAM.

    Steps
    -----
    1. Connect to the Hyper-V host via WinRM.
    2. Verify the VM exists.
    3. Capture current RAM and running services (pre-check).
    4. Stop (shut down) the VM.
    5. Set the new static RAM.
    6. Start the VM.
    7. Capture running services (post-check).

    Parameters
    ----------
    vm_name : str
        Name of the Hyper-V VM to resize.
    new_ram_mb : int, optional
        New RAM in MB.  Falls back to config default_ram_mb (1024).
    host, username, password : str, optional
        Override values from config.ini.

    Returns
    -------
    dict  with keys: status (bool), message (str), precheck (dict),
          app_checks (dict), old_ram_mb (int), new_ram_mb (int).
    """

    if winrm is None:
        return {
            "status": False,
            "message": (
                "pywinrm is not installed.  Run:  pip install pywinrm"
            ),
        }

    # ---- Load config / apply overrides ----
    try:
        cfg = _load_hyperv_config()
    except Exception as exc:
        return {"status": False, "message": str(exc)}

    hv_host = host or cfg["host"]
    hv_user = username or cfg["username"]
    hv_pass = password or cfg["password"]
    ram_mb = new_ram_mb if new_ram_mb is not None else cfg["default_ram_mb"]

    if not hv_host:
        return {"status": False, "message": "Hyper-V host address is not configured."}

    endpoint = f"http://{hv_host}:5985/wsman"

    try:
        # ---- Connect ----
        session = winrm.Session(
            endpoint,
            auth=(hv_user, hv_pass),
            transport="ntlm",
            server_cert_validation="ignore",
        )
        logger.info("Connected to Hyper-V host %s", hv_host)

        # ---- Verify VM exists ----
        verify = _run_ps(session, f'Get-VM -Name "{vm_name}" | Select-Object Name, State, MemoryStartup')
        if verify["status_code"] != 0 or not verify["stdout"]:
            return {
                "status": False,
                "message": f"VM '{vm_name}' not found on Hyper-V host {hv_host}: {verify['stderr']}",
            }

        # ---- Pre-check: capture current RAM ----
        ram_query = _run_ps(
            session,
            f'(Get-VM -Name "{vm_name}").MemoryStartup / 1MB',
        )
        old_ram_mb = 0
        try:
            old_ram_mb = int(float(ram_query["stdout"]))
        except (ValueError, TypeError):
            pass

        # ---- Pre-check: running services inside VM ----
        precheck_script = (
            f'Invoke-Command -VMName "{vm_name}" -Credential '
            f'(New-Object PSCredential("{hv_user}", (ConvertTo-SecureString "{hv_pass}" -AsPlainText -Force))) '
            '-ScriptBlock { Get-Service | Where-Object { $_.Status -eq "Running" } | '
            "Select-Object -ExpandProperty Name } -ErrorAction SilentlyContinue"
        )
        pre_result = _run_ps(session, precheck_script)
        precheck_services = [
            s.strip() for s in pre_result["stdout"].splitlines() if s.strip()
        ]
        precheck = {
            "status": True,
            "message": (
                "Pre-resize running services captured."
                if precheck_services
                else "Could not capture pre-resize services (VM may be off or unreachable)."
            ),
            "checks": [{"service": s, "status": "RUNNING"} for s in precheck_services],
            "checked_services": precheck_services,
        }

        # ---- Stop VM ----
        stop = _run_ps(session, f'Stop-VM -Name "{vm_name}" -Force -ErrorAction Stop')
        if stop["status_code"] != 0:
            return {
                "status": False,
                "message": f"Failed to stop VM '{vm_name}': {stop['stderr']}",
                "precheck": precheck,
            }
        logger.info("VM %s stopped.", vm_name)

        # ---- Set RAM ----
        ram_bytes = ram_mb * 1024 * 1024
        set_ram = _run_ps(
            session,
            f'Set-VM -Name "{vm_name}" -MemoryStartupBytes {ram_bytes} -ErrorAction Stop',
        )
        if set_ram["status_code"] != 0:
            # Try to restart the VM even if RAM change failed
            _run_ps(session, f'Start-VM -Name "{vm_name}"')
            return {
                "status": False,
                "message": f"Failed to set RAM to {ram_mb} MB: {set_ram['stderr']}",
                "precheck": precheck,
            }
        logger.info("VM %s RAM set to %d MB.", vm_name, ram_mb)

        # ---- Start VM ----
        start = _run_ps(session, f'Start-VM -Name "{vm_name}" -ErrorAction Stop')
        if start["status_code"] != 0:
            return {
                "status": False,
                "message": f"RAM changed but failed to start VM: {start['stderr']}",
                "precheck": precheck,
                "old_ram_mb": old_ram_mb,
                "new_ram_mb": ram_mb,
            }
        logger.info("VM %s started.", vm_name)

        # ---- Post-check: running services ----
        post_result = _run_ps(session, precheck_script)
        postcheck_services = [
            s.strip() for s in post_result["stdout"].splitlines() if s.strip()
        ]
        app_checks = {
            "status": True,
            "message": (
                "Post-resize service check completed."
                if postcheck_services
                else "Could not capture post-resize services."
            ),
            "checks": [{"service": s, "status": "RUNNING"} for s in postcheck_services],
            "checked_services": postcheck_services,
        }

        return {
            "status": True,
            "message": (
                f"Hyper-V VM '{vm_name}' RAM changed from {old_ram_mb} MB to {ram_mb} MB successfully."
            ),
            "precheck": precheck,
            "app_checks": app_checks,
            "old_ram_mb": old_ram_mb,
            "new_ram_mb": ram_mb,
        }

    except Exception as exc:
        logger.exception("Hyper-V resize failed for VM %s", vm_name)
        return {"status": False, "message": f"Hyper-V resize error: {exc}"}
