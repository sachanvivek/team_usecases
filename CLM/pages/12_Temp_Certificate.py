"""
Page 12 – Temporary Certificate (Quick Issue & Deploy)
Generate a short-lived (10-day default) certificate from the Internal CA
and deploy it directly to a target server in one step.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import time

from core.database import db
from core.certificate_ops import (
    generate_csr, serialize_private_key, internal_ca, parse_pem_certificate,
)
from core.deployment import deploy_to_nginx, VM_MAP, LOCAL_IP, discover_all_azure_vms
from utils.config import config

st.set_page_config(page_title="Temp Certificate", page_icon="⏱️", layout="wide")

st.markdown("# ⏱️ Temporary Certificate – Quick Issue & Deploy")
st.markdown(
    "Generate a **short-lived** certificate from the Internal CA and deploy it "
    "directly to a target Nginx server. No approval or payment required."
)
st.divider()

# ──────────── Configuration Form ────────────
col1, col2 = st.columns(2)

with col1:
    st.markdown("#### 📝 Certificate Details")
    common_name = st.text_input(
        "Common Name (FQDN)",
        placeholder="e.g., temp-app.mfgitis.tcs",
        key="temp_cn",
    )
    san_input = st.text_input(
        "Subject Alternative Names (comma-separated, optional)",
        placeholder="e.g., alias1.mfgitis.tcs, alias2.mfgitis.tcs",
        key="temp_san",
    )
    validity_days = st.number_input(
        "Validity (days)", value=10, min_value=1, max_value=30, step=1,
        help="Temporary certificates are limited to 30 days max.",
        key="temp_validity",
    )
    org = config.get('local_ca', 'ca_org', fallback='Enterprise Corp')
    country = config.get('local_ca', 'ca_country', fallback='IN')
    state = config.get('local_ca', 'ca_state', fallback='Tamil Nadu')
    city = config.get('local_ca', 'ca_city', fallback='Chennai')

with col2:
    st.markdown("#### 🖥️ Target Server")

    # Scan Azure for all VMs (cached in session state)
    if 'azure_vm_map' not in st.session_state:
        with st.spinner("Scanning Azure VMs..."):
            st.session_state['azure_vm_map'] = discover_all_azure_vms()

    azure_vms = st.session_state['azure_vm_map']

    if st.button("🔄 Rescan Azure VMs", key="rescan_vms"):
        with st.spinner("Scanning Azure VMs..."):
            st.session_state['azure_vm_map'] = discover_all_azure_vms()
            azure_vms = st.session_state['azure_vm_map']
        st.success(f"Found {len(azure_vms)} VM(s)")

    server_options = sorted(azure_vms.keys(), key=lambda ip: tuple(int(p) for p in ip.split('.')))
    target_server = st.selectbox(
        "Deploy To",
        options=server_options,
        format_func=lambda ip: f"{azure_vms.get(ip, ip)} ({ip})",
        key="temp_server",
    )
    target_port = st.number_input(
        "HTTPS Port", value=443, min_value=1, max_value=65535,
        key="temp_port",
    )
    requested_by = st.text_input("Requested By", value="admin", key="temp_requestor")

    st.info(
        f"🔑 **CA:** Internal Intermediate CA (Demo-CA)  \n"
        f"⏳ **Validity:** {validity_days} day(s)  \n"
        f"🚀 **Flow:** CSR → Sign → Deploy (all automatic)"
    )

st.divider()

# ──────────── Issue & Deploy ────────────
if st.button(
    "⚡ Generate & Deploy Temporary Certificate",
    type="primary",
    use_container_width=True,
):
    if not common_name:
        st.error("❌ Please enter a Common Name.")
        st.stop()

    # Progress tracking
    progress_bar = st.progress(0)
    status_text = st.empty()
    log_area = st.container()
    logs = []

    def _log(msg):
        logs.append(msg)
        log_area.markdown("\n".join(logs))

    try:
        # ── Step 1: Generate CSR & private key ──
        status_text.markdown("**🔐 Generating CSR and private key...**")
        progress_bar.progress(0.10)

        san_list = [s.strip() for s in san_input.split(",") if s.strip()] if san_input else None
        # Always include the CN in SAN list
        if san_list is None:
            san_list = [common_name]
        elif common_name not in san_list:
            san_list.insert(0, common_name)

        csr_pem, key_pem, _ = generate_csr(
            common_name=common_name,
            san_list=san_list,
            org=org, country=country, state=state, city=city,
        )
        _log("✅ CSR and private key generated")
        progress_bar.progress(0.20)

        # ── Step 2: Sign with Internal CA ──
        status_text.markdown("**🏛️ Signing certificate with Internal CA...**")
        progress_bar.progress(0.30)

        sign_result = internal_ca.sign_csr(csr_pem, common_name, validity_days=validity_days)

        cert_pem = sign_result['certificate_pem']
        _log(f"✅ Certificate signed by Internal CA (serial: {sign_result.get('serial_number', 'N/A')})")
        _log(f"   Valid: {sign_result.get('not_before', '?')} → {sign_result.get('not_after', '?')}")
        progress_bar.progress(0.50)

        # ── Step 3: Deploy to target server ──
        status_text.markdown(f"**🚀 Deploying to {VM_MAP.get(target_server, target_server)} ({target_server})...**")

        def deploy_progress(step, total, msg):
            pct = 0.50 + (step / total) * 0.45
            progress_bar.progress(min(pct, 0.95))
            status_text.markdown(f"**{msg}**")
            _log(f"{'✅' if step < total else '🔍'} {msg}")

        deploy_result = deploy_to_nginx(
            cert_pem=cert_pem,
            key_pem=key_pem,
            common_name=common_name,
            server=target_server,
            port=target_port,
            progress_callback=deploy_progress,
        )

        progress_bar.progress(1.0)

        if deploy_result['success']:
            status_text.markdown("**✅ Temporary certificate deployed successfully!**")
            _log(f"✅ {deploy_result['message']}")

            # ── Step 4: Save to database ──
            cert_id = db.add_certificate({
                'common_name': common_name,
                'san': ', '.join(san_list) if san_list else common_name,
                'issuer': sign_result.get('issuer', 'Internal Intermediate CA'),
                'serial_number': sign_result.get('serial_number', ''),
                'thumbprint': sign_result.get('thumbprint', ''),
                'not_before': sign_result.get('not_before', ''),
                'not_after': sign_result.get('not_after', ''),
                'key_size': sign_result.get('key_size', 2048),
                'algorithm': 'RSA',
                'ca_type': 'local',
                'ca_provider': None,
                'status': 'deployed',
                'server': target_server,
                'port': target_port,
                'certificate_pem': cert_pem,
                'private_key_pem': key_pem,
                'environment': 'production',
                'requested_by': requested_by,
            })

            db.add_audit_log(
                cert_id, 'temp_cert_deployed',
                f"Temporary {validity_days}-day certificate created and deployed to "
                f"{target_server}:{target_port}. Reason: quick-issue.",
                performed_by=requested_by,
            )

            _log(f"✅ Saved to inventory (Certificate #{cert_id})")

            # Summary card
            st.divider()
            st.success(
                f"🎉 **Temporary certificate `{common_name}` is live!**\n\n"
                f"- **Server:** {VM_MAP.get(target_server, target_server)} ({target_server}:{target_port})\n"
                f"- **Expires:** {sign_result.get('not_after', 'N/A')} ({validity_days} days)\n"
                f"- **Serial:** {sign_result.get('serial_number', 'N/A')}\n"
                f"- **Cert ID:** #{cert_id}"
            )
            st.warning(
                f"⏳ This certificate expires in **{validity_days} day(s)**. "
                "Remember to renew or replace it before expiry."
            )

            # Deployment details
            with st.expander("📋 Deployment Details"):
                for d in deploy_result.get('details', []):
                    st.markdown(f"- {d}")
        else:
            status_text.markdown("**❌ Deployment failed**")
            st.error(f"❌ {deploy_result['message']}")
            for d in deploy_result.get('details', []):
                st.write(f"  - {d}")

    except Exception as e:
        progress_bar.progress(1.0)
        status_text.markdown("**❌ Error**")
        st.error(f"❌ Failed: {e}")
        import traceback
        with st.expander("🔍 Error Details"):
            st.code(traceback.format_exc())
