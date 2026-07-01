import logging
from typing import Any, Dict, List, Optional

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.core.exceptions import HttpResponseError
from azure.mgmt.compute.models import HardwareProfile


def _get_running_services(
    compute_client: ComputeManagementClient,
    resource_group: str,
    vm_name: str,
    os_type: str,
) -> Dict[str, Any]:
    if os_type.lower() == "windows":
        script = (
            "Get-Service | "
            "Where-Object { $_.Status -eq 'Running' } | "
            "Select-Object -ExpandProperty Name"
        )
        command_id = "RunPowerShellScript"
    else:
        script = (
            "systemctl list-units --type=service --state=running --no-legend --no-pager "
            "| awk '{print $1}' "
            "| sed 's/\\.service$//'"
        )
        command_id = "RunShellScript"

    try:
        run_result = compute_client.virtual_machines.begin_run_command(
            resource_group_name=resource_group,
            vm_name=vm_name,
            parameters={"command_id": command_id, "script": [script]},
        ).result()
    except Exception as exc:
        return {
            "status": False,
            "message": f"Pre-resize running service discovery failed: {exc}",
            "checks": [],
            "checked_services": [],
            "all_started": False,
        }

    output_lines: List[str] = []
    if getattr(run_result, "value", None):
        for item in run_result.value:
            if getattr(item, "message", None):
                output_lines.extend(str(item.message).splitlines())

    services = [line.strip() for line in output_lines if line and line.strip()]
    unique_services = sorted(set(services))

    checks = [{"service": service, "status": "RUNNING"} for service in unique_services]

    return {
        "status": True,
        "message": (
            "Pre-resize running services captured successfully."
            if unique_services
            else "No running services discovered during pre-resize check."
        ),
        "checks": checks,
        "checked_services": unique_services,
        "all_started": True,
    }


def _run_post_resize_service_checks(
    compute_client: ComputeManagementClient,
    resource_group: str,
    vm_name: str,
    os_type: str,
    services_to_check: List[str],
) -> Dict[str, Any]:
    if not services_to_check:
        return {
            "status": True,
            "message": "No post-resize app/service checks requested.",
            "checks": [],
            "checked_services": [],
            "all_started": True,
        }

    if os_type.lower() == "windows":
        script = (
            "$services = @(" + ",".join([f"'{service}'" for service in services_to_check]) + ");"
            "$results = foreach ($service in $services) {"
            "$svc = Get-Service -Name $service -ErrorAction SilentlyContinue;"
            "if ($null -eq $svc) { \"$service:NOT_FOUND\" }"
            "else { \"$service:$($svc.Status)\" }"
            "};"
            "$results -join [Environment]::NewLine"
        )
        command_id = "RunPowerShellScript"
    else:
        quoted_services = " ".join([f"'{service}'" for service in services_to_check])
        script = (
            "for svc in "
            f"{quoted_services}; "
            "do "
            "if systemctl list-unit-files | grep -q \"^${svc}\\.service\"; then "
            "status=$(systemctl is-active ${svc}); "
            "echo \"${svc}:${status}\"; "
            "else echo \"${svc}:NOT_FOUND\"; "
            "fi; "
            "done"
        )
        command_id = "RunShellScript"

    try:
        run_result = compute_client.virtual_machines.begin_run_command(
            resource_group_name=resource_group,
            vm_name=vm_name,
            parameters={"command_id": command_id, "script": [script]},
        ).result()
    except Exception as exc:
        return {
            "status": False,
            "message": f"Post-resize app/service checks failed to execute: {exc}",
            "checks": [],
            "checked_services": services_to_check,
            "all_started": False,
        }

    output_lines: List[str] = []
    if getattr(run_result, "value", None):
        for item in run_result.value:
            if getattr(item, "message", None):
                output_lines.extend(str(item.message).splitlines())

    parsed_checks = []
    for line in output_lines:
        if ":" not in line:
            continue
        name, status = line.split(":", 1)
        service_name = name.strip()
        service_status = status.strip()
        if not service_name:
            continue
        parsed_checks.append({"service": service_name, "status": service_status})

    checks_map = {item["service"]: item["status"] for item in parsed_checks}
    normalized_checks = []
    for service in services_to_check:
        service_status = checks_map.get(service, "UNKNOWN")
        normalized_checks.append({"service": service, "status": service_status})

    all_started = all(
        item["status"].lower() in {"running", "active"} for item in normalized_checks
    )

    failed = [
        f"{item['service']}={item['status']}"
        for item in normalized_checks
        if item["status"].lower() not in {"running", "active"}
    ]

    if all_started:
        message = "Post-resize app/service checks passed: all services are started."
    else:
        message = (
            "Post-resize app/service checks found non-started services: "
            + ", ".join(failed)
        )

    return {
        "status": all_started,
        "message": message,
        "checks": normalized_checks,
        "checked_services": services_to_check,
        "all_started": all_started,
    }


def resize_azure_vm(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    subscription_id: str,
    resource_group: str,
    vm_name: str,
    new_vm_size: str,
    services_to_check: Optional[List[str]] = None,
):
    """
    Resize an Azure VM safely.
    Returns: dict {status: bool, message: str, precheck: dict, app_checks: dict}
    """

    try:
        # ----------------------------
        # Authenticate
        # ----------------------------
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )

        compute_client = ComputeManagementClient(
            credential,
            subscription_id
        )

        logging.info("Authenticated successfully.")

        # ----------------------------
        # Fetch VM
        # ----------------------------
        vm = compute_client.virtual_machines.get(resource_group, vm_name)

        # ----------------------------
        # Validate Size
        # ----------------------------
        available_sizes = compute_client.virtual_machines.list_available_sizes(
            resource_group,
            vm_name
        )

        size_names = [size.name for size in available_sizes]

        if new_vm_size not in size_names:
            return {
                "status": False,
                "message": f"Size {new_vm_size} not available for this VM."
            }

        os_type = "linux"
        if (
            vm.storage_profile
            and vm.storage_profile.os_disk
            and vm.storage_profile.os_disk.os_type
        ):
            os_type = str(vm.storage_profile.os_disk.os_type)

        precheck = _get_running_services(
            compute_client=compute_client,
            resource_group=resource_group,
            vm_name=vm_name,
            os_type=os_type,
        )

        precheck_services = precheck.get("checked_services", []) or []
        requested_services = services_to_check or []
        merged_services: List[str] = []
        seen_services = set()
        for service in precheck_services + requested_services:
            service_name = str(service).strip()
            if service_name and service_name not in seen_services:
                seen_services.add(service_name)
                merged_services.append(service_name)

        # ----------------------------
        # Deallocate
        # ----------------------------
        compute_client.virtual_machines.begin_deallocate(
            resource_group,
            vm_name
        ).result()

        # ----------------------------
        # Resize
        # ----------------------------
        if vm.hardware_profile is None:
            vm.hardware_profile = HardwareProfile(vm_size=new_vm_size)
        else:
            vm.hardware_profile.vm_size = new_vm_size

        compute_client.virtual_machines.begin_create_or_update(
            resource_group,
            vm_name,
            vm
        ).result()

        # ----------------------------
        # Start VM
        # ----------------------------
        compute_client.virtual_machines.begin_start(
            resource_group,
            vm_name
        ).result()

        app_checks = _run_post_resize_service_checks(
            compute_client=compute_client,
            resource_group=resource_group,
            vm_name=vm_name,
            os_type=os_type,
            services_to_check=merged_services,
        )

        app_checks["scope"] = "all_precheck_running_services"
        app_checks["requested_services"] = requested_services

        return {
            "status": True,
            "message": f"VM {vm_name} resized to {new_vm_size} successfully.",
            "precheck": precheck,
            "app_checks": app_checks,
        }

    except HttpResponseError as e:
        return {"status": False, "message": f"Azure API error: {str(e)}"}

    except Exception as e:
        return {"status": False, "message": f"Unexpected error: {str(e)}"}
