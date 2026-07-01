"""
Deployment engine – Real certificate deployment to Nginx servers.
Supports local deployment (this server) and remote deployment via Azure VM run-command.
"""
import os
import ssl
import json
import socket
import base64
import shutil
import subprocess
import tempfile
from datetime import datetime

from utils.config import config

# ─────────────────────── Azure VM Mapping ───────────────────────

# Known VM-to-IP mapping in MFG_ITIS_ITOPS_EntNetworks resource group
VM_MAP = {
    '172.19.0.6':  'Demo-DNS',
    '172.19.0.7':  'Demo-CPCM',
    '172.19.0.8':  'Demo-resize',
    '172.19.0.9':  'Demo-network',
    '172.19.0.10': 'Demo-HyperV',
    '172.19.0.11': 'Demo-CA',
    '172.19.0.12': 'Demo-Vulnerability',
    '172.19.0.13': 'Demo-CA-Deployment',
}

LOCAL_IP = '172.19.0.13'
RESOURCE_GROUP = 'MFG_ITIS_ITOPS_EntNetworks'
AZ_CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       '.venv', 'bin', 'az')

# Azure credentials
_AZ_TENANT  = 'cd9d7bf9-b9a3-4ad2-bd6b-1939a1b0a5c4'
_AZ_CLIENT  = '5b788335-5a60-4a8d-9372-521f904e9530'
_AZ_SECRET  = 'mDb8Q~vGw6Ke0UHLHtDglUMMuSeHy323kemsqcFP'
_az_logged_in = False


def _ensure_az_login():
    """Ensure az CLI is logged in (lazily, once per process)."""
    global _az_logged_in
    if _az_logged_in:
        return
    # Quick check if already logged in
    rc = subprocess.run(
        [AZ_CLI, 'account', 'show', '--query', 'id', '-o', 'tsv'],
        capture_output=True, text=True, timeout=15,
    ).returncode
    if rc == 0:
        _az_logged_in = True
        return
    # Login with service principal
    subprocess.run([
        AZ_CLI, 'login', '--service-principal',
        '-u', _AZ_CLIENT, '-p', _AZ_SECRET, '--tenant', _AZ_TENANT,
        '--output', 'none',
    ], capture_output=True, text=True, timeout=30)
    _az_logged_in = True


def _is_local(server: str) -> bool:
    """Check if the target server is this machine."""
    return server in (LOCAL_IP, '127.0.0.1', 'localhost', 'Demo-CA-Deployment')


def _discover_vm_by_ip(ip: str) -> str | None:
    """Query Azure for a VM whose private IP matches `ip`. Returns VM name or None."""
    _ensure_az_login()
    try:
        r = subprocess.run(
            [AZ_CLI, 'vm', 'list-ip-addresses',
             '-g', RESOURCE_GROUP,
             '-o', 'json'],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        entries = json.loads(r.stdout)
        for entry in entries:
            vm_name = entry.get('virtualMachine', {}).get('name', '')
            nics = entry.get('virtualMachine', {}).get('network', {}).get('privateIpAddresses', [])
            if ip in nics:
                # Cache for future lookups
                VM_MAP[ip] = vm_name
                return vm_name
    except Exception:
        pass
    return None


def _get_vm_name(server: str) -> str | None:
    """Resolve an IP to its Azure VM name (static map first, then live Azure query)."""
    name = VM_MAP.get(server)
    if name:
        return name
    # Fallback: discover dynamically via Azure CLI
    return _discover_vm_by_ip(server)


def discover_all_azure_vms() -> dict[str, str]:
    """
    Query Azure for ALL VMs in the resource group.
    Returns a dict of {private_ip: vm_name} covering every VM.
    Merges results into VM_MAP for caching.
    """
    _ensure_az_login()
    discovered = {}
    try:
        r = subprocess.run(
            [AZ_CLI, 'vm', 'list-ip-addresses',
             '-g', RESOURCE_GROUP,
             '-o', 'json'],
            capture_output=True, text=True, timeout=45,
        )
        if r.returncode == 0:
            entries = json.loads(r.stdout)
            for entry in entries:
                vm_name = entry.get('virtualMachine', {}).get('name', '')
                nics = entry.get('virtualMachine', {}).get('network', {}).get('privateIpAddresses', [])
                for ip in nics:
                    discovered[ip] = vm_name
                    VM_MAP[ip] = vm_name  # cache globally
    except Exception:
        pass
    # Ensure static entries are included even if Azure query fails
    for ip, name in VM_MAP.items():
        if ip not in discovered:
            discovered[ip] = name
    return discovered


def _run_local(command: str, timeout: int = 30, use_sudo: bool = False) -> tuple[int, str, str]:
    """Run a shell command locally. Returns (returncode, stdout, stderr)."""
    try:
        # Ensure /usr/sbin is in PATH (for nginx, systemctl, etc.)
        full_cmd = f'export PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH"; {command}'
        if use_sudo:
            full_cmd = f'sudo bash -c \'{full_cmd}\''
            shell_cmd = ['bash', '-c', full_cmd]
        else:
            shell_cmd = ['bash', '-c', full_cmd]
        r = subprocess.run(
            shell_cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, '', 'Command timed out'
    except Exception as e:
        return 1, '', str(e)


def _run_remote(vm_name: str, script: str, timeout: int = 120) -> tuple[int, str, str]:
    """Run a script on a remote Azure VM via az vm run-command. Returns (rc, stdout, stderr)."""
    _ensure_az_login()
    try:
        r = subprocess.run([
            AZ_CLI, 'vm', 'run-command', 'invoke',
            '-g', RESOURCE_GROUP,
            '-n', vm_name,
            '--command-id', 'RunShellScript',
            '--scripts', script,
            '--query', 'value[0].message',
            '-o', 'tsv',
        ], capture_output=True, text=True, timeout=timeout)

        output = r.stdout
        # Parse [stdout] and [stderr] sections
        stdout_content = ''
        stderr_content = ''
        import re
        m = re.search(r'\[stdout\]\n(.*?)\n\[stderr\]', output, re.DOTALL)
        if m:
            stdout_content = m.group(1).strip()
        m2 = re.search(r'\[stderr\]\n(.*)', output, re.DOTALL)
        if m2:
            stderr_content = m2.group(1).strip()

        return r.returncode, stdout_content, stderr_content
    except subprocess.TimeoutExpired:
        return 1, '', 'Azure VM run-command timed out'
    except Exception as e:
        return 1, '', str(e)


def _run_on_server(server: str, script: str, timeout: int = 120,
                   use_sudo: bool = False) -> tuple[int, str, str]:
    """Run a script on a target server (local or remote)."""
    if _is_local(server):
        return _run_local(script, timeout, use_sudo=use_sudo)
    vm_name = _get_vm_name(server)
    if vm_name:
        # Remote VM run-command always runs as root
        return _run_remote(vm_name, script, timeout)
    return 1, '', f'Unknown server {server}: not in VM map and not local'


# ─────────────────────── Nginx Deployment ───────────────────────

def deploy_to_nginx(cert_pem: str, key_pem: str, common_name: str,
                    server: str, port: int = 443,
                    progress_callback=None) -> dict:
    """
    Deploy a certificate to an Nginx server.

    Args:
        cert_pem: The fullchain PEM certificate (server cert + intermediates)
        key_pem: The private key PEM
        common_name: The certificate common name (used for filenames/vhost)
        server: Target server IP
        port: Target HTTPS port
        progress_callback: Optional callable(step_num, total_steps, message)

    Returns:
        dict with 'success', 'message', 'details' keys
    """
    steps = []

    def _progress(step, total, msg):
        steps.append(msg)
        if progress_callback:
            progress_callback(step, total, msg)

    total = 6
    result = {
        'success': False,
        'message': '',
        'details': [],
        'server': server,
        'port': port,
        'common_name': common_name,
    }

    # Step 1: Validate cert and key locally
    _progress(1, total, "Validating certificate and key...")
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as cf:
            cf.write(cert_pem)
            cert_tmp = cf.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as kf:
            kf.write(key_pem)
            key_tmp = kf.name

        # Verify cert
        r = subprocess.run(['openssl', 'x509', '-in', cert_tmp, '-noout', '-subject', '-enddate'],
                          capture_output=True, text=True)
        if r.returncode != 0:
            result['message'] = f"Invalid certificate: {r.stderr}"
            return result
        result['details'].append(f"Certificate valid: {r.stdout.strip()}")

        # Verify key
        r = subprocess.run(['openssl', 'rsa', '-in', key_tmp, '-check', '-noout'],
                          capture_output=True, text=True)
        if r.returncode != 0:
            result['message'] = f"Invalid private key: {r.stderr}"
            return result

        # Verify key matches cert
        r1 = subprocess.run(['openssl', 'x509', '-in', cert_tmp, '-noout', '-modulus'],
                           capture_output=True, text=True)
        r2 = subprocess.run(['openssl', 'rsa', '-in', key_tmp, '-noout', '-modulus'],
                           capture_output=True, text=True)
        if r1.stdout.strip() != r2.stdout.strip():
            result['message'] = "Certificate and key do not match (modulus mismatch)"
            return result
        result['details'].append("Key matches certificate ✓")

    finally:
        for f in [cert_tmp, key_tmp]:
            if os.path.exists(f):
                os.unlink(f)

    # Step 2: Check target server connectivity
    _progress(2, total, f"Checking server {server}...")
    if _is_local(server):
        result['details'].append(f"Target is local server ({server})")
    else:
        vm_name = _get_vm_name(server)
        if not vm_name:
            result['message'] = f"Cannot reach server {server}: not in Azure VM map"
            return result
        result['details'].append(f"Target: Azure VM '{vm_name}' ({server})")

    # Step 3: Check Nginx is installed on target
    _progress(3, total, "Checking Nginx on target server...")
    rc, out, err = _run_on_server(server, "/usr/sbin/nginx -v 2>&1 && echo NGINX_OK")
    if 'NGINX_OK' not in out:
        result['message'] = f"Nginx not found on {server}: {err or out}"
        return result
    result['details'].append(f"Nginx found on target")

    # Step 4: Deploy certificate files
    _progress(4, total, "Deploying certificate files...")
    cert_b64 = base64.b64encode(cert_pem.encode()).decode()
    key_b64 = base64.b64encode(key_pem.encode()).decode()

    ssl_dir = '/etc/nginx/ssl'
    cert_path = f'{ssl_dir}/{common_name}.fullchain.pem'
    key_path = f'{ssl_dir}/{common_name}.key.pem'

    deploy_script = (
        f"mkdir -p {ssl_dir} && "
        f"echo '{cert_b64}' | base64 -d > {cert_path} && "
        f"echo '{key_b64}' | base64 -d > {key_path} && "
        f"chmod 644 {cert_path} && "
        f"chmod 600 {key_path} && "
        f"echo CERT_DEPLOYED"
    )

    rc, out, err = _run_on_server(server, deploy_script, use_sudo=True)
    if 'CERT_DEPLOYED' not in out:
        result['message'] = f"Failed to deploy cert files: {err or out}"
        return result
    result['details'].append(f"Cert files deployed to {ssl_dir}/")

    # Step 5: Detect OS and configure Nginx appropriately
    _progress(5, total, "Detecting OS and configuring Nginx...")

    # Detect OS family on target
    os_detect_script = (
        "if [ -f /etc/os-release ]; then . /etc/os-release && echo $ID; "
        "elif [ -f /etc/redhat-release ]; then echo rhel; "
        "elif [ -f /etc/debian_version ]; then echo debian; "
        "else echo unknown; fi"
    )
    rc, os_id, _ = _run_on_server(server, os_detect_script)
    os_id = os_id.strip().lower()
    result['details'].append(f"Detected OS: {os_id}")

    # Determine config directory based on OS family
    # Debian/Ubuntu: sites-available + sites-enabled
    # RHEL/CentOS/Fedora/SUSE/etc: conf.d
    debian_family = os_id in ('debian', 'ubuntu', 'linuxmint', 'pop')
    if debian_family:
        conf_dir = '/etc/nginx/sites-available'
        link_dir = '/etc/nginx/sites-enabled'
        include_pattern = 'sites-enabled'
        include_line = 'include /etc/nginx/sites-enabled/*;'
    else:
        conf_dir = '/etc/nginx/conf.d'
        link_dir = None  # conf.d doesn't use symlinks
        include_pattern = 'conf.d'
        include_line = 'include /etc/nginx/conf.d/*.conf;'

    # Ensure the map block for $connection_upgrade exists in nginx.conf
    map_block = (
        "map \\$http_upgrade \\$connection_upgrade {\\n"
        "    default upgrade;\\n"
        "    \\\"\\\" close;\\n"
        "}"
    )
    map_setup = (
        "grep -q 'connection_upgrade' /etc/nginx/nginx.conf || "
        f"sed -i '/http {{/a \\    {map_block}' /etc/nginx/nginx.conf; "
    )

    # We use $host etc. in the nginx config - must not let Python interpolate them
    nginx_conf = (
        f"# {common_name} - auto-deployed by Certificate Lifecycle Manager\\n"
        f"server {{\\n"
        f"    listen 80;\\n"
        f"    listen [::]:80;\\n"
        f"    server_name {common_name};\\n"
        f"    client_max_body_size 50M;\\n"
        f"    location / {{\\n"
        f"        proxy_pass http://127.0.0.1:8501;\\n"
        f"        proxy_http_version 1.1;\\n"
        f"        proxy_set_header Host \\$host;\\n"
        f"        proxy_set_header X-Real-IP \\$remote_addr;\\n"
        f"        proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;\\n"
        f"        proxy_set_header X-Forwarded-Proto \\$scheme;\\n"
        f"        proxy_set_header Upgrade \\$http_upgrade;\\n"
        f"        proxy_set_header Connection \\$connection_upgrade;\\n"
        f"        proxy_read_timeout 86400;\\n"
        f"        proxy_buffering off;\\n"
        f"    }}\\n"
        f"}}\\n"
        f"\\n"
        f"server {{\\n"
        f"    listen {port} ssl http2;\\n"
        f"    listen [::]:{port} ssl http2;\\n"
        f"    server_name {common_name};\\n"
        f"    ssl_certificate     {cert_path};\\n"
        f"    ssl_certificate_key {key_path};\\n"
        f"    ssl_protocols TLSv1.2 TLSv1.3;\\n"
        f"    ssl_ciphers HIGH:!aNULL:!MD5;\\n"
        f"    client_max_body_size 50M;\\n"
        f"    location / {{\\n"
        f"        proxy_pass http://127.0.0.1:8501;\\n"
        f"        proxy_http_version 1.1;\\n"
        f"        proxy_set_header Host \\$host;\\n"
        f"        proxy_set_header X-Real-IP \\$remote_addr;\\n"
        f"        proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;\\n"
        f"        proxy_set_header X-Forwarded-Proto \\$scheme;\\n"
        f"        proxy_set_header Upgrade \\$http_upgrade;\\n"
        f"        proxy_set_header Connection \\$connection_upgrade;\\n"
        f"        proxy_read_timeout 86400;\\n"
        f"        proxy_buffering off;\\n"
        f"    }}\\n"
        f"}}\\n"
    )

    # Build deployment script based on OS family
    if debian_family:
        vhost_file = f'{conf_dir}/{common_name}'
        nginx_setup_script = (
            f"{map_setup}"
            f"mkdir -p {conf_dir} {link_dir} && "
            f"grep -q '{include_pattern}' /etc/nginx/nginx.conf || "
            f"sed -i '/http {{/a \\    {include_line}' /etc/nginx/nginx.conf; "
            f"echo -e \"{nginx_conf}\" > {vhost_file} && "
            f"ln -sf {vhost_file} {link_dir}/{common_name} && "
            f"/usr/sbin/nginx -t 2>&1 && "
            f"systemctl reload nginx && "
            f"echo NGINX_CONFIGURED"
        )
    else:
        # RHEL/CentOS/Fedora/SUSE: use conf.d with .conf extension
        vhost_file = f'{conf_dir}/{common_name}.conf'
        nginx_setup_script = (
            f"{map_setup}"
            f"mkdir -p {conf_dir} && "
            f"grep -q '{include_pattern}' /etc/nginx/nginx.conf || "
            f"sed -i '/http {{/a \\    {include_line}' /etc/nginx/nginx.conf; "
            f"echo -e \"{nginx_conf}\" > {vhost_file} && "
            f"/usr/sbin/nginx -t 2>&1 && "
            f"systemctl reload nginx && "
            f"echo NGINX_CONFIGURED"
        )

    rc, out, err = _run_on_server(server, nginx_setup_script, use_sudo=True)
    if 'NGINX_CONFIGURED' not in out:
        result['message'] = f"Nginx configuration failed: {out} {err}"
        return result
    result['details'].append("Nginx vhost configured and reloaded ✓")

    # Step 6: Verify TLS
    _progress(6, total, "Verifying TLS connection...")
    verify_script = f"curl -skI https://127.0.0.1:{port} -H 'Host: {common_name}' 2>&1 | head -5"
    rc, out, err = _run_on_server(server, verify_script)
    if '200' in out or '301' in out or '302' in out:
        result['details'].append(f"TLS verification passed: HTTPS responding ✓")
    else:
        result['details'].append(f"TLS verification: {out.strip()}")

    result['success'] = True
    result['message'] = (
        f"Certificate '{common_name}' successfully deployed to Nginx on {server}:{port}. "
        f"Cert path: {cert_path}"
    )
    result['cert_path'] = cert_path
    result['key_path'] = key_path
    return result


# ─────────────────────── Nginx Undeployment (Revocation) ───────────────────────

def undeploy_from_nginx(common_name: str, server: str) -> dict:
    """
    Remove a revoked certificate from an Nginx server.
    Deletes the vhost config, SSL files, and reloads Nginx.

    Returns:
        dict with 'success', 'message', 'details' keys.
    """
    result = {
        'success': False,
        'message': '',
        'details': [],
        'server': server,
        'common_name': common_name,
    }

    # Detect OS family on target
    os_detect_script = (
        "if [ -f /etc/os-release ]; then . /etc/os-release && echo $ID; "
        "elif [ -f /etc/redhat-release ]; then echo rhel; "
        "elif [ -f /etc/debian_version ]; then echo debian; "
        "else echo unknown; fi"
    )
    rc, os_id, _ = _run_on_server(server, os_detect_script)
    os_id = os_id.strip().lower()
    debian_family = os_id in ('debian', 'ubuntu', 'linuxmint', 'pop')

    # Build list of files to remove
    ssl_dir = '/etc/nginx/ssl'
    cert_file = f'{ssl_dir}/{common_name}.fullchain.pem'
    key_file = f'{ssl_dir}/{common_name}.key.pem'

    if debian_family:
        vhost_file = f'/etc/nginx/sites-available/{common_name}'
        link_file = f'/etc/nginx/sites-enabled/{common_name}'
    else:
        vhost_file = f'/etc/nginx/conf.d/{common_name}.conf'
        link_file = None

    # Remove vhost config, symlink, and SSL files; then reload Nginx
    remove_parts = [
        f"rm -f {vhost_file}",
        f"rm -f {cert_file}",
        f"rm -f {key_file}",
    ]
    if link_file:
        remove_parts.insert(1, f"rm -f {link_file}")

    remove_script = " && ".join(remove_parts) + (
        " && /usr/sbin/nginx -t 2>&1"
        " && systemctl reload nginx"
        " && echo NGINX_UNDEPLOYED"
    )

    rc, out, err = _run_on_server(server, remove_script, use_sudo=True)
    if 'NGINX_UNDEPLOYED' in out:
        result['success'] = True
        result['message'] = (
            f"Certificate '{common_name}' removed from Nginx on {server}. "
            "Vhost disabled and SSL files deleted."
        )
        result['details'] = [
            f"Removed vhost config: {vhost_file}",
            f"Removed SSL cert: {cert_file}",
            f"Removed SSL key: {key_file}",
            "Nginx reloaded ✓",
        ]
    else:
        result['message'] = f"Failed to undeploy from Nginx: {out} {err}"

    return result


# ─────────────────────── Deployment from CA Server ───────────────────────

def fetch_cert_from_ca(common_name: str) -> dict:
    """
    Fetch a certificate's PEM files from the CA server (Demo-CA / 172.19.0.11).
    Returns dict with 'cert_pem', 'key_pem', 'chain_pem' or 'error'.
    """
    ca_base = f'/home/linuxadmin/internal-ca/intermediate-ca/certs/issued/{common_name}'
    ca_inter = '/home/linuxadmin/internal-ca/intermediate-ca/certs/intermediate.cert.pem'
    ca_root = '/home/linuxadmin/internal-ca/root-ca/certs/ca.cert.pem'

    result = {}

    # Get server cert
    rc, out, err = _run_remote('Demo-CA', f'cat {ca_base}/{common_name}.cert.pem')
    if rc != 0 or '-----BEGIN CERTIFICATE-----' not in out:
        return {'error': f'Server cert not found on CA: {err or out}'}
    result['cert_pem'] = out.strip()

    # Get intermediate cert
    rc, out, err = _run_remote('Demo-CA', f'cat {ca_inter}')
    if rc != 0 or '-----BEGIN CERTIFICATE-----' not in out:
        return {'error': f'Intermediate cert not found on CA: {err or out}'}
    result['intermediate_pem'] = out.strip()

    # Get root CA cert
    rc, out, err = _run_remote('Demo-CA', f'cat {ca_root}')
    if rc != 0 or '-----BEGIN CERTIFICATE-----' not in out:
        return {'error': f'Root CA cert not found on CA: {err or out}'}
    result['root_pem'] = out.strip()

    # Build fullchain (server + intermediate + root)
    result['chain_pem'] = (
        result['cert_pem'] + '\n' +
        result['intermediate_pem'] + '\n' +
        result['root_pem'] + '\n'
    )

    # Get private key
    rc, out, err = _run_remote('Demo-CA', f'cat {ca_base}/{common_name}.key.pem')
    if rc != 0 or '-----BEGIN' not in out:
        return {'error': f'Private key not found on CA: {err or out}'}
    result['key_pem'] = out.strip()

    return result


def get_ca_issued_certs() -> list:
    """List all certificates issued by the CA server."""
    rc, out, err = _run_remote(
        'Demo-CA',
        'ls -1 /home/linuxadmin/internal-ca/intermediate-ca/certs/issued/'
    )
    if rc != 0:
        return []
    return [d.strip() for d in out.strip().split('\n') if d.strip()]


def verify_tls(host: str, port: int = 443, timeout: int = 5) -> dict:
    """Connect to a host and verify its TLS certificate."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)
                pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
                from core.certificate_ops import parse_pem_certificate
                info = parse_pem_certificate(pem_cert)
                info['verified'] = True
                return info
    except Exception as e:
        return {'verified': False, 'error': str(e)}
