"""
Page 1 – Certificate Discovery
Scan networks, hosts, or domains to discover existing SSL/TLS certificates.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import ipaddress
from datetime import datetime

from core.database import db
from core.ai_agent import ai_agent
from core.certificate_ops import discover_certificates, scan_host_certificate
from utils.helpers import days_until_expiry, get_expiry_status
from utils.config import config

# ──────────── Known internal CA identifiers ────────────
INTERNAL_CA_KEYWORDS = [
    'enterprise internal',
    'enterprise corp',
    'internal ca',
    'intermediate ca',
    'private ca',
    'self-signed',
    'localhost',
]

def _detect_ca_type(issuer: str) -> str:
    """Determine if a certificate is from a local/internal CA or an external/public CA."""
    if not issuer:
        return 'external'
    issuer_lower = issuer.lower()
    for keyword in INTERNAL_CA_KEYWORDS:
        if keyword in issuer_lower:
            return 'local'
    # If issuer is non-empty and doesn't match internal keywords, it's external
    return 'external'

def _detect_ca_provider(issuer: str) -> str | None:
    """Extract the CA provider name from the issuer string."""
    if not issuer:
        return None
    providers = {
        'digicert': 'DigiCert',
        'sectigo': 'Sectigo',
        'comodo': 'Sectigo',
        'globalSign': 'GlobalSign',
        'globalsign': 'GlobalSign',
        'godaddy': 'GoDaddy',
        'go daddy': 'GoDaddy',
        "let's encrypt": 'Lets Encrypt',
        'letsencrypt': 'Lets Encrypt',
        'r3': 'Lets Encrypt',
        'r10': 'Lets Encrypt',
        'r11': 'Lets Encrypt',
        'e5': 'Lets Encrypt',
        'e6': 'Lets Encrypt',
        'amazon': 'AWS',
        'microsoft': 'Microsoft',
        'google trust': 'Google',
        'baltimore': 'DigiCert',
        'thawte': 'DigiCert',
        'geotrust': 'DigiCert',
        'rapidssl': 'DigiCert',
        'entrust': 'Entrust',
        'starfield': 'GoDaddy',
    }
    issuer_lower = issuer.lower()
    for key, provider in providers.items():
        if key in issuer_lower:
            return provider
    return None

def _build_cert_data(cert_info: dict) -> dict:
    """Build a certificate data dict for DB insertion with proper CA detection."""
    issuer = cert_info.get('issuer', '')
    ca_type = _detect_ca_type(issuer)
    ca_provider = _detect_ca_provider(issuer) if ca_type == 'external' else None
    return {
        'common_name': cert_info.get('common_name', 'Unknown'),
        'san': cert_info.get('san', ''),
        'issuer': issuer,
        'serial_number': cert_info.get('serial_number', ''),
        'thumbprint': cert_info.get('thumbprint', ''),
        'not_before': cert_info.get('not_before', ''),
        'not_after': cert_info.get('not_after', ''),
        'key_size': cert_info.get('key_size', 2048),
        'algorithm': 'RSA',
        'ca_type': ca_type,
        'ca_provider': ca_provider,
        'status': 'discovered',
        'server': cert_info.get('server', ''),
        'port': cert_info.get('port', 443),
        'certificate_pem': cert_info.get('certificate_pem', ''),
        'environment': 'production',
    }

st.set_page_config(page_title="Discovery", page_icon="📡", layout="wide")

st.markdown("# 📡 Certificate Discovery")
st.markdown("Scan your infrastructure to discover SSL/TLS certificates across servers and endpoints.")
st.divider()

# ──────────── Scan Configuration ────────────
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("🎯 Scan Targets")
    scan_mode = st.radio(
        "Scan Mode",
        ["Manual Host Entry", "Network Range", "Well-Known Sites"],
        horizontal=True,
    )

    if scan_mode == "Manual Host Entry":
        targets_text = st.text_area(
            "Enter hostnames or IPs (one per line)",
            placeholder="example.com\n10.0.1.100\napi.mycompany.com",
            height=120,
        )
    elif scan_mode == "Network Range":
        network_range = st.text_input(
            "Enter network range (CIDR notation)",
            value="10.0.1.0/24",
            placeholder="e.g. 192.168.1.0/24",
        )
        max_hosts = st.number_input("Max hosts to scan", min_value=1, max_value=1024, value=256)
        targets_text = ""
    else:
        targets_text = "google.com\ngithub.com\nmicrosoft.com"
        st.code(targets_text, language="text")

with col2:
    st.subheader("⚙️ Scan Settings")
    default_ports = config.get('discovery', 'default_ports', fallback='443')
    ports_input = st.text_input("Ports to scan (comma-separated)", value=default_ports)
    timeout = st.slider("Timeout per host (seconds)", 1, 30, 5)
    scan_button = st.button("🚀 Start Discovery Scan", type="primary", use_container_width=True)

st.divider()

# ──────────── Initialize session state for results ────────────
if 'discovery_results' not in st.session_state:
    st.session_state.discovery_results = []
if 'added_to_inventory' not in st.session_state:
    st.session_state.added_to_inventory = set()

# ──────────── Execute Scan ────────────
if scan_button:
    ports = [int(p.strip()) for p in ports_input.split(',') if p.strip().isdigit()]
    st.session_state.added_to_inventory = set()  # reset on new scan

    if scan_mode == "Network Range":
        # Parse CIDR and scan each host IP
        try:
            network = ipaddress.ip_network(network_range, strict=False)
        except ValueError as e:
            st.error(f"Invalid network range: {e}")
            st.stop()

        host_ips = [str(ip) for ip in network.hosts()]
        if len(host_ips) > max_hosts:
            host_ips = host_ips[:max_hosts]

        st.info(f"🔄 Scanning network range **{network_range}** — {len(host_ips)} host(s) on ports {ports} ...")
        results = []
        progress = st.progress(0)
        scanned = 0
        found_count = 0
        for ip in host_ips:
            for port in ports:
                info = scan_host_certificate(ip, port, timeout)
                if info and 'error' not in info:
                    results.append(info)
                    found_count += 1
            scanned += 1
            progress.progress(scanned / len(host_ips))
        progress.empty()
        st.session_state.discovery_results = results
        if results:
            st.success(f"✅ Found **{found_count}** certificate(s) across {len(host_ips)} hosts.")
        else:
            st.warning(f"No certificates found in range {network_range}.")
    else:
        targets = [t.strip() for t in targets_text.strip().split('\n') if t.strip()]
        if not targets:
            st.warning("Please enter at least one target host.")
            st.stop()

        with st.spinner(f"🔍 Scanning {len(targets)} target(s) on ports {ports}..."):
            results = []
            progress = st.progress(0)
            for i, target in enumerate(targets):
                for port in ports:
                    info = scan_host_certificate(target, port, timeout)
                    if info and 'error' not in info:
                        results.append(info)
                    elif info and 'error' in info:
                        st.warning(f"⚠️ {target}:{port} — {info['error']}")
                progress.progress((i + 1) / len(targets))
            st.session_state.discovery_results = results

# ──────────── Display persisted results ────────────
results = st.session_state.discovery_results

if results:
    st.success(f"✅ Discovered {len(results)} certificate(s)!")

    for idx, cert_info in enumerate(results):
        d = days_until_expiry(cert_info.get('not_after'))
        status_label, status_color = get_expiry_status(d)

        with st.expander(
            f"🔒 {cert_info.get('common_name', 'Unknown')} — "
            f"{cert_info.get('server', '?')}:{cert_info.get('port', 443)} "
            f"[{status_label}]",
            expanded=(idx == 0),
        ):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Common Name:** {cert_info.get('common_name', 'N/A')}")
            c1.markdown(f"**SANs:** {cert_info.get('san', 'N/A')}")
            c1.markdown(f"**Issuer:** {cert_info.get('issuer', 'N/A')}")

            c2.markdown(f"**Serial:** {cert_info.get('serial_number', 'N/A')}")
            c2.markdown(f"**Key Size:** {cert_info.get('key_size', 'N/A')} bits")
            c2.markdown(f"**Valid From:** {cert_info.get('not_before', 'N/A')}")

            c3.markdown(f"**Expires:** {cert_info.get('not_after', 'N/A')}")
            c3.markdown(f"**Days Left:** {d if d is not None else 'N/A'}")
            c3.markdown(f"**Status:** :{status_color}[{status_label}]")

            # Show "already added" or the add button
            if idx in st.session_state.added_to_inventory:
                st.info(f"✅ Already added to inventory")
            else:
                if st.button(f"➕ Add to Inventory", key=f"add_{idx}"):
                    # Check for duplicates in DB
                    cn = cert_info.get('common_name', 'Unknown')
                    srv = cert_info.get('server', '')
                    prt = cert_info.get('port', 443)
                    sn = cert_info.get('serial_number', '')
                    if db.certificate_exists(cn, srv, prt, sn):
                        st.warning(f"⚠️ Certificate `{cn}` on `{srv}:{prt}` already exists in inventory. Skipped.")
                        st.session_state.added_to_inventory.add(idx)
                        st.rerun()
                    else:
                        cert_data = _build_cert_data(cert_info)
                        cert_id = db.add_certificate(cert_data)
                        st.session_state.added_to_inventory.add(idx)
                        st.success(f"✅ Added to inventory with ID #{cert_id}")
                        st.rerun()

# ──────────── AI Analysis ────────────
st.divider()
st.subheader("🤖 AI Discovery Assistant")
if st.button("🧠 Ask AI to analyze discovered certificates"):
    all_discovered = db.get_all_certificates(status='discovered')
    if all_discovered:
        with st.spinner("AI Agent is analyzing discovered certificates..."):
            certs_summary = "\n".join(
                f"- {c['common_name']} on {c.get('server','')}:{c.get('port',443)}"
                for c in all_discovered
            )
            analysis = ai_agent.chat(
                f"Analyze these recently discovered certificates and recommend next steps:\n{certs_summary}",
                context="These certificates were just discovered via network scanning."
            )
        st.markdown(analysis)
    else:
        st.info("No discovered certificates to analyze. Run a scan first!")
