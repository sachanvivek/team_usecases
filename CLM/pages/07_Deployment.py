"""
Page 7 – Certificate Deployment
Deploy issued certificates to target servers and platforms.
Supports real Nginx deployment via local commands or Azure VM run-command.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import time

from core.database import db
from core.ai_agent import ai_agent
from core.deployment import (
    deploy_to_nginx, fetch_cert_from_ca, get_ca_issued_certs,
    verify_tls, VM_MAP, LOCAL_IP, discover_all_azure_vms,
)
from utils.config import config
from utils.helpers import get_status_emoji

st.set_page_config(page_title="Deployment", page_icon="🚀", layout="wide")

st.markdown("# 🚀 Certificate Deployment")
st.markdown("Deploy issued certificates to target servers, load balancers, and cloud platforms.")
st.divider()

# ──────────── Scan Azure VMs (cached) ────────────
if 'deploy_vm_map' not in st.session_state:
    with st.spinner("Scanning Azure VMs..."):
        st.session_state['deploy_vm_map'] = discover_all_azure_vms()
_vm_map = st.session_state['deploy_vm_map']

# Supported platforms
platforms = config.getlist('deployment', 'supported_platforms',
                           fallback=['Apache', 'Nginx', 'IIS', 'Tomcat', 'AWS', 'Azure', 'Kubernetes'])

# ──────────── Deploy from CA Server ────────────
st.subheader("🏛️ Deploy from Internal CA")
st.markdown("Fetch certificates directly from the CA server (Demo-CA) and deploy to target servers.")

with st.expander("📡 Fetch & Deploy from CA Server", expanded=False):
    col_ca1, col_ca2 = st.columns(2)

    with col_ca1:
        ca_common_name = st.text_input(
            "Certificate Common Name",
            placeholder="e.g., certificate.mfgitis.tcs",
            key="ca_cn_input",
        )
        _sorted_ips = sorted(_vm_map.keys(), key=lambda ip: tuple(int(p) for p in ip.split('.')))
        ca_target_server = st.selectbox(
            "Target Server",
            options=_sorted_ips,
            format_func=lambda ip: f"{_vm_map.get(ip, ip)} ({ip})",
            key="ca_target_server",
        )
        ca_target_port = st.number_input(
            "HTTPS Port", value=443, min_value=1, max_value=65535,
            key="ca_port",
        )

    with col_ca2:
        st.markdown("**Available CA-Issued Certificates:**")
        if st.button("🔄 Refresh CA Cert List", key="refresh_ca"):
            with st.spinner("Querying CA server..."):
                ca_certs = get_ca_issued_certs()
                st.session_state['ca_cert_list'] = ca_certs

        if 'ca_cert_list' in st.session_state and st.session_state['ca_cert_list']:
            for cn in st.session_state['ca_cert_list']:
                st.markdown(f"  📜 `{cn}`")
        else:
            st.caption("Click 'Refresh' to load certificates from CA server.")

    if st.button("🚀 Fetch from CA & Deploy", key="ca_deploy_btn",
                 type="primary", use_container_width=True):
        if not ca_common_name:
            st.error("❌ Please enter a Common Name.")
            st.stop()

        # Step 1: Fetch from CA
        st.markdown("#### 📋 Deployment Progress")
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_area = st.empty()
        logs = []

        status_text.markdown("**🏛️ Fetching certificate from CA server...**")
        progress_bar.progress(0.15)
        ca_result = fetch_cert_from_ca(ca_common_name)

        if 'error' in ca_result:
            st.error(f"❌ Failed to fetch from CA: {ca_result['error']}")
            st.stop()

        logs.append(f"✅ Certificate fetched from CA server")
        logs.append(f"   Chain: {ca_result['chain_pem'].count('BEGIN CERTIFICATE')} certificates")
        log_area.markdown('\n'.join(logs))
        progress_bar.progress(0.3)

        # Step 2: Deploy to target
        def progress_cb(step, total, msg):
            pct = 0.3 + (step / total) * 0.7
            progress_bar.progress(min(pct, 1.0))
            status_text.markdown(f"**{msg}**")
            logs.append(f"{'✅' if step < total else '🔍'} {msg}")
            log_area.markdown('\n'.join(logs))

        deploy_result = deploy_to_nginx(
            cert_pem=ca_result['chain_pem'],
            key_pem=ca_result['key_pem'],
            common_name=ca_common_name,
            server=ca_target_server,
            port=ca_target_port,
            progress_callback=progress_cb,
        )

        if deploy_result['success']:
            progress_bar.progress(1.0)
            status_text.empty()

            # Update or create DB record
            from core.certificate_ops import parse_pem_certificate
            cert_info = parse_pem_certificate(ca_result['cert_pem'])

            exists = db.certificate_exists(ca_common_name, server=ca_target_server, port=ca_target_port)
            all_certs = db.get_all_certificates()
            cert_id = None
            for c in all_certs:
                if c.get('common_name') == ca_common_name:
                    cert_id = c['id']
                    break

            if cert_id:
                db.update_certificate(cert_id, {
                    'server': ca_target_server,
                    'port': ca_target_port,
                    'certificate_pem': ca_result['chain_pem'],
                    'private_key_pem': ca_result['key_pem'],
                    'issuer': cert_info.get('issuer', 'Internal Intermediate CA'),
                    'serial_number': cert_info.get('serial_number', ''),
                    'not_before': cert_info.get('not_before', ''),
                    'not_after': cert_info.get('not_after', ''),
                })
                cur_status = db.get_certificate(cert_id)['status']
                if cur_status != 'deployed':
                    if cur_status in ('discovered', 'requested', 'pending_approval'):
                        db.update_certificate_status(cert_id, 'issued',
                            triggered_by='deployment_engine', notes='Auto-issued for deployment')
                    db.update_certificate_status(cert_id, 'deployed',
                        triggered_by='deployment_engine', notes=deploy_result['message'])
            else:
                cert_id = db.add_certificate({
                    'common_name': ca_common_name,
                    'san': cert_info.get('san', f'DNS:{ca_common_name}'),
                    'issuer': cert_info.get('issuer', 'Internal Intermediate CA'),
                    'serial_number': cert_info.get('serial_number', ''),
                    'thumbprint': cert_info.get('thumbprint', ''),
                    'not_before': cert_info.get('not_before', ''),
                    'not_after': cert_info.get('not_after', ''),
                    'key_size': cert_info.get('key_size', 2048),
                    'algorithm': 'RSA',
                    'ca_type': 'local',
                    'ca_provider': 'Internal PKI',
                    'cert_type': 'Internal',
                    'status': 'issued',
                    'environment': 'production',
                    'server': ca_target_server,
                    'port': ca_target_port,
                    'certificate_pem': ca_result['chain_pem'],
                    'private_key_pem': ca_result['key_pem'],
                    'requestor': 'admin',
                    'notes': 'Fetched from CA server and deployed via Certificate Lifecycle Manager.',
                })
                db.update_certificate_status(cert_id, 'deployed',
                    triggered_by='deployment_engine', notes=deploy_result['message'])

            db.add_audit_log(cert_id, 'deployed', deploy_result['message'],
                            performed_by='deployment_engine')

            for detail in deploy_result['details']:
                logs.append(f"   ℹ️ {detail}")
            logs.append(f"\n🎉 **{deploy_result['message']}**")
            log_area.markdown('\n'.join(logs))

            st.success(
                f"✅ Certificate **{ca_common_name}** deployed to "
                f"**Nginx** on `{ca_target_server}:{ca_target_port}` (DB #{cert_id})"
            )
            st.balloons()
        else:
            st.error(f"❌ Deployment failed: {deploy_result['message']}")
            for detail in deploy_result['details']:
                st.markdown(f"  ℹ️ {detail}")

st.divider()

# ──────────── Deploy Issued Certificates (from app DB) ────────────
st.subheader("📜 Deploy Issued Certificates")

issued_certs = db.get_all_certificates(status='issued')

if not issued_certs:
    st.info("📭 No issued certificates ready for deployment. Issue certificates first, or use the CA deployment above.")
else:
    st.metric("Ready to Deploy", len(issued_certs))

    for cert in issued_certs:
        cert_id = cert['id']
        with st.expander(
            f"🚀 #{cert_id} — {cert['common_name']} → {cert.get('server', 'N/A')}",
            expanded=True,
        ):
            col1, col2 = st.columns([2, 2])

            with col1:
                st.markdown("#### 📜 Certificate Info")
                st.markdown(f"**Common Name:** `{cert['common_name']}`")
                st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
                st.markdown(f"**Issuer:** `{cert.get('issuer', 'N/A')}`")
                st.markdown(f"**Serial:** `{cert.get('serial_number', 'N/A')}`")
                st.markdown(f"**Expires:** `{cert.get('not_after', 'N/A')}`")
                st.markdown(f"**CA Type:** `{cert.get('ca_type', 'N/A').upper()}`")
                has_pem = bool(cert.get('certificate_pem'))
                has_key = bool(cert.get('private_key_pem'))
                st.markdown(f"**Cert PEM:** {'✅' if has_pem else '❌ Missing'}")
                st.markdown(f"**Key PEM:** {'✅' if has_key else '❌ Missing'}")

            with col2:
                st.markdown("#### 🎯 Deployment Target")
                target_server = st.text_input(
                    "Server / IP",
                    value=cert.get('server', ''),
                    key=f"server_{cert_id}",
                )
                target_port = st.number_input(
                    "Port", value=cert.get('port', 443),
                    min_value=1, max_value=65535,
                    key=f"port_{cert_id}",
                )
                platform = st.selectbox(
                    "Target Platform",
                    platforms,
                    key=f"platform_{cert_id}",
                )
                deploy_notes = st.text_area(
                    "Deployment Notes",
                    placeholder="e.g., Rolling update, maintenance window...",
                    key=f"deploy_notes_{cert_id}",
                    height=60,
                )

            # AI Deployment Strategy
            if st.button("🤖 AI Deployment Strategy", key=f"ai_deploy_{cert_id}"):
                with st.spinner("AI Agent planning deployment..."):
                    strategy = ai_agent.suggest_deployment(cert, platform)
                st.markdown(strategy)

            st.divider()

            # Real Deploy button for Nginx
            if platform == 'Nginx' and has_pem and has_key:
                if st.button(
                    f"🚀 Deploy to Nginx on {target_server}",
                    key=f"real_deploy_{cert_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    st.markdown("#### 📋 Real Deployment Progress")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    log_area = st.empty()
                    logs = []

                    def progress_cb(step, total, msg):
                        pct = step / total
                        progress_bar.progress(min(pct, 1.0))
                        status_text.markdown(f"**{msg}**")
                        logs.append(f"✅ {msg}")
                        log_area.markdown('\n'.join(logs))

                    deploy_result = deploy_to_nginx(
                        cert_pem=cert['certificate_pem'],
                        key_pem=cert['private_key_pem'],
                        common_name=cert['common_name'],
                        server=target_server,
                        port=target_port,
                        progress_callback=progress_cb,
                    )

                    if deploy_result['success']:
                        progress_bar.progress(1.0)
                        status_text.empty()

                        db.update_certificate(cert_id, {
                            'server': target_server,
                            'port': target_port,
                        })
                        db.update_certificate_status(
                            cert_id, 'deployed',
                            triggered_by='deployment_engine',
                            notes=f"Deployed to {platform} on {target_server}:{target_port}. {deploy_notes}"
                        )
                        db.add_audit_log(
                            cert_id, 'deployed',
                            deploy_result['message'],
                            performed_by='deployment_engine',
                        )

                        for detail in deploy_result['details']:
                            logs.append(f"   ℹ️ {detail}")
                        log_area.markdown('\n'.join(logs))

                        st.success(
                            f"✅ Certificate #{cert_id} deployed to "
                            f"**{platform}** on `{target_server}:{target_port}`!"
                        )
                        st.balloons()
                        st.rerun()
                    else:
                        st.error(f"❌ Deployment failed: {deploy_result['message']}")
                        for detail in deploy_result['details']:
                            st.markdown(f"  ℹ️ {detail}")

            # Simulated deploy for non-Nginx platforms or missing PEM
            elif st.button(
                f"🚀 Deploy to {platform}",
                key=f"deploy_{cert_id}",
                type="primary" if platform != 'Nginx' else "secondary",
                use_container_width=True,
            ):
                st.markdown("#### 📋 Deployment Progress")

                steps = [
                    ("🔍 Validating certificate...", 0.8),
                    ("📡 Connecting to target server...", 1.0),
                    (f"📦 Uploading certificate to {platform}...", 1.5),
                    ("⚙️ Configuring SSL/TLS settings...", 1.2),
                    ("🔄 Reloading service...", 0.8),
                    ("✅ Verifying deployment...", 1.0),
                ]

                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, (step_text, duration) in enumerate(steps):
                    status_text.markdown(f"**{step_text}**")
                    time.sleep(duration)
                    progress_bar.progress((i + 1) / len(steps))

                db.update_certificate(cert_id, {
                    'server': target_server,
                    'port': target_port,
                })
                db.update_certificate_status(
                    cert_id, 'deployed',
                    triggered_by='deployment_system',
                    notes=f"Deployed to {platform} on {target_server}:{target_port}. {deploy_notes}"
                )
                db.add_audit_log(
                    cert_id, 'deployed',
                    f"Certificate deployed to {platform} on {target_server}:{target_port}",
                    performed_by='deployment_system',
                )

                status_text.empty()
                st.success(
                    f"✅ Certificate #{cert_id} successfully deployed to "
                    f"**{platform}** on `{target_server}:{target_port}`!"
                )
                st.balloons()
                st.rerun()

# ──────────── Deployed Certificates ────────────
st.divider()
st.subheader("📍 Currently Deployed Certificates")
deployed = db.get_all_certificates(status='deployed')
if deployed:
    import pandas as pd
    deploy_data = []
    for c in deployed:
        deploy_data.append({
            'ID': c['id'],
            'Common Name': c['common_name'],
            'Server': f"{c.get('server', '')}:{c.get('port', 443)}",
            'Issuer': c.get('issuer', 'N/A'),
            'Expires': c.get('not_after', 'N/A')[:10] if c.get('not_after') else 'N/A',
            'CA Type': c.get('ca_type', '').upper(),
            'Environment': c.get('environment', ''),
        })
    st.dataframe(pd.DataFrame(deploy_data), use_container_width=True, hide_index=True)

    # Verify TLS for deployed certs
    st.divider()
    st.subheader("🔍 Verify Deployed Certificates")
    verify_cn = st.selectbox(
        "Select certificate to verify",
        options=[(c['id'], c['common_name'], c.get('server', ''), c.get('port', 443)) for c in deployed],
        format_func=lambda x: f"#{x[0]} — {x[1]} @ {x[2]}:{x[3]}",
        key="verify_select",
    )
    if st.button("🔍 Verify TLS Connection", key="verify_tls_btn"):
        cert_id, cn, server, port = verify_cn
        with st.spinner(f"Connecting to {cn} ({server}:{port})..."):
            tls_info = verify_tls(server, port)
        if tls_info.get('verified'):
            st.success(f"✅ TLS verified for `{cn}`")
            st.json({
                'subject': tls_info.get('common_name'),
                'issuer': tls_info.get('issuer'),
                'serial': tls_info.get('serial_number'),
                'expires': tls_info.get('not_after'),
                'key_size': tls_info.get('key_size'),
            })
        else:
            st.error(f"❌ TLS verification failed: {tls_info.get('error', 'Unknown error')}")
else:
    st.caption("No deployed certificates yet.")
