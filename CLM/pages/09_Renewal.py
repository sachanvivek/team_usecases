"""
Page 9 – Certificate Renewal
Renew expiring or expired certificates with AI-prioritized recommendations.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from core.database import db
from core.ai_agent import ai_agent
from core.certificate_ops import generate_csr, local_ca, ExternalCA, internal_ca
from core.deployment import deploy_to_nginx
from utils.helpers import days_until_expiry, get_expiry_status, get_status_emoji
from utils.config import config

st.set_page_config(page_title="Renewal", page_icon="🔄", layout="wide")

st.markdown("# 🔄 Certificate Renewal")
st.markdown("Renew expiring, expired, or critical certificates before service disruption.")
st.divider()

# ──────────── Renewal Candidates ────────────
all_certs = db.get_all_certificates()
renewal_candidates = []
for c in all_certs:
    if c['status'] in ('revoked', 'requested', 'pending_approval', 'pending_payment'):
        continue
    d = days_until_expiry(c.get('not_after'))
    if d is not None and d <= 90:
        c['_days_left'] = d
        renewal_candidates.append(c)

# Also add certificates explicitly marked for renewal
renewal_requested = db.get_all_certificates(status='renewal_requested')
for c in renewal_requested:
    c['_days_left'] = days_until_expiry(c.get('not_after'))
    if c not in renewal_candidates:
        renewal_candidates.append(c)

renewal_candidates.sort(key=lambda x: x.get('_days_left') or 999)

if not renewal_candidates:
    st.success("🎉 No certificates need renewal at this time. All certificates are healthy!")
    st.stop()

# KPIs
expired = [c for c in renewal_candidates if (c.get('_days_left') or 0) < 0]
critical = [c for c in renewal_candidates if 0 <= (c.get('_days_left') or 0) <= 7]
warning = [c for c in renewal_candidates if 7 < (c.get('_days_left') or 0) <= 30]
attention = [c for c in renewal_candidates if 30 < (c.get('_days_left') or 0) <= 90]

k1, k2, k3, k4 = st.columns(4)
k1.metric("💀 Expired", len(expired))
k2.metric("🔴 Critical (<7d)", len(critical))
k3.metric("🟠 Warning (<30d)", len(warning))
k4.metric("🟡 Attention (<90d)", len(attention))

# ──────────── AI Renewal Prioritization ────────────
st.divider()
if st.button("🤖 AI Renewal Priority Analysis", type="primary"):
    with st.spinner("AI Agent is prioritizing renewals..."):
        analysis = ai_agent.prioritize_renewals(renewal_candidates)
    st.markdown(analysis)

st.divider()

# ──────────── Renewal Table ────────────
st.subheader("📋 Renewal Candidates")

for cert in renewal_candidates:
    cert_id = cert['id']
    d = cert.get('_days_left')
    label, color = get_expiry_status(d)
    icon = "💀" if d and d < 0 else "🔴" if d is not None and d <= 7 else "🟠" if d is not None and d <= 30 else "🟡"

    with st.expander(
        f"{icon} #{cert_id} — {cert['common_name']} — {label} "
        f"({'Expired' if d and d < 0 else f'{d} days left'})",
        expanded=(d is not None and d <= 7),
    ):
        col1, col2 = st.columns([2, 2])

        with col1:
            st.markdown(f"**Common Name:** `{cert['common_name']}`")
            st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
            st.markdown(f"**Current Issuer:** `{cert.get('issuer', 'N/A')}`")
            st.markdown(f"**CA Type:** `{cert.get('ca_type', 'N/A').upper()}`")
            if cert.get('ca_type') == 'external':
                st.markdown(f"**Provider:** `{cert.get('ca_provider', 'N/A')}`")
            st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")
            st.markdown(f"**Server:** `{cert.get('server', 'N/A')}:{cert.get('port', 443)}`")
            st.markdown(f"**Expires:** `{cert.get('not_after', 'N/A')}`")
            days_text = f"**{abs(d)} days ago**" if d and d < 0 else f"**{d} days**"
            st.markdown(f"**Days Until Expiry:** {days_text}")

        with col2:
            st.markdown("#### 🔄 Renewal Options")

            # Choose CA type for renewal
            renew_ca = st.selectbox(
                "Renew with",
                ["Same CA", "Local CA", "External CA"],
                key=f"renew_ca_{cert_id}",
            )

            if renew_ca == "Same CA":
                use_ca_type = cert.get('ca_type', 'local')
                use_provider = cert.get('ca_provider')
            elif renew_ca == "Local CA":
                use_ca_type = 'local'
                use_provider = None
            else:
                use_ca_type = 'external'
                use_provider = st.selectbox(
                    "Provider", ExternalCA.get_providers(),
                    key=f"renew_provider_{cert_id}",
                )

            new_key_size = st.selectbox("Key Size", [2048, 4096],
                                        index=0 if cert.get('key_size', 2048) == 2048 else 1,
                                        key=f"renew_ks_{cert_id}")

        # Renew button
        if st.button(f"🔄 Initiate Renewal", key=f"renew_{cert_id}",
                     type="primary", use_container_width=True):

            progress_bar = st.progress(0)
            status_text = st.empty()
            log_area = st.container()
            logs = []

            def _log(msg):
                logs.append(msg)
                log_area.markdown("\n".join(logs))

            try:
                # ── Step 1: Generate new CSR ──
                status_text.markdown("**🔑 Generating new CSR for renewal...**")
                progress_bar.progress(0.10)
                san_list = [s.strip() for s in cert.get('san', '').split(',') if s.strip()]
                csr_pem, key_pem, _ = generate_csr(
                    common_name=cert['common_name'],
                    san_list=san_list,
                    org=config.get('local_ca', 'ca_org', fallback='Enterprise Corp'),
                    country=config.get('local_ca', 'ca_country', fallback='IN'),
                    state=config.get('local_ca', 'ca_state', fallback='Tamil Nadu'),
                    city=config.get('local_ca', 'ca_city', fallback='Chennai'),
                    key_size=new_key_size,
                    algorithm=cert.get('algorithm', 'RSA'),
                )
                _log("✅ New CSR and private key generated")
                progress_bar.progress(0.20)

                if use_ca_type == 'local':
                    # ── Step 2: Sign with Internal CA (real CA server) ──
                    status_text.markdown("**🏛️ Signing with Internal CA (Demo-CA)...**")
                    progress_bar.progress(0.30)
                    validity = config.getint('local_ca', 'cert_validity_days', fallback=365)
                    result = internal_ca.sign_csr(csr_pem, cert['common_name'], validity_days=validity)

                    cert_pem = result['certificate_pem']
                    _log(f"✅ Certificate signed by Internal CA (serial: {result.get('serial_number', 'N/A')})")
                    _log(f"   Valid: {result.get('not_before', '?')} → {result.get('not_after', '?')}")
                    progress_bar.progress(0.50)

                    # ── Step 3: Update database ──
                    db.update_certificate(cert_id, {
                        'csr_pem': csr_pem,
                        'private_key_pem': key_pem,
                        'certificate_pem': cert_pem,
                        'issuer': result.get('issuer', 'Internal Intermediate CA'),
                        'serial_number': result['serial_number'],
                        'thumbprint': result['thumbprint'],
                        'not_before': result['not_before'],
                        'not_after': result['not_after'],
                        'key_size': new_key_size,
                        'ca_type': 'local',
                        'ca_provider': None,
                    })
                    _log("✅ Certificate record updated in inventory")
                    progress_bar.progress(0.55)

                    # ── Step 4: Auto-deploy if previously deployed ──
                    deploy_server = cert.get('server', '').strip()
                    deploy_port = cert.get('port', 443)

                    if deploy_server and cert['status'] in ('deployed', 'active', 'issued', 'discovered'):
                        status_text.markdown(f"**🚀 Deploying renewed certificate to {deploy_server}...**")

                        def deploy_progress(step, total, msg):
                            pct = 0.55 + (step / total) * 0.40
                            progress_bar.progress(min(pct, 0.95))
                            status_text.markdown(f"**{msg}**")
                            _log(f"{'✅' if step < total else '🔍'} {msg}")

                        deploy_result = deploy_to_nginx(
                            cert_pem=cert_pem,
                            key_pem=key_pem,
                            common_name=cert['common_name'],
                            server=deploy_server,
                            port=int(deploy_port),
                            progress_callback=deploy_progress,
                        )

                        if deploy_result['success']:
                            _log(f"✅ {deploy_result['message']}")
                            db.update_certificate_status(
                                cert_id, 'deployed',
                                triggered_by='renewal_system',
                                notes=(
                                    f"Certificate renewed via Internal CA and redeployed to "
                                    f"{deploy_server}:{deploy_port}. "
                                    f"New serial: {result['serial_number']}"
                                ),
                            )
                            progress_bar.progress(1.0)
                            status_text.markdown("**✅ Renewal and deployment complete!**")
                            st.success(
                                f"🎉 Certificate #{cert_id} (`{cert['common_name']}`) renewed and "
                                f"deployed to **{deploy_server}:{deploy_port}**. "
                                f"New expiry: {result['not_after'][:10]}"
                            )
                            with st.expander("📋 Deployment Details"):
                                for d in deploy_result.get('details', []):
                                    st.markdown(f"- {d}")
                        else:
                            _log(f"⚠️ Deployment failed: {deploy_result['message']}")
                            db.update_certificate_status(
                                cert_id, 'issued',
                                triggered_by='renewal_system',
                                notes=(
                                    f"Certificate renewed but deployment to {deploy_server} failed: "
                                    f"{deploy_result['message']}"
                                ),
                            )
                            progress_bar.progress(1.0)
                            st.warning(
                                f"✅ Certificate renewed but ⚠️ deployment to {deploy_server} failed. "
                                "Deploy manually from the Deployment page."
                            )
                    else:
                        # No server to deploy to — just mark as issued
                        db.update_certificate_status(
                            cert_id, 'issued',
                            triggered_by='renewal_system',
                            notes=f"Certificate renewed via Internal CA. New serial: {result['serial_number']}"
                        )
                        progress_bar.progress(1.0)
                        status_text.markdown("**✅ Renewal complete!**")
                        st.success(
                            f"✅ Certificate #{cert_id} renewed. New expiry: {result['not_after'][:10]}. "
                            "Deploy from the Deployment page."
                        )

                    db.add_audit_log(
                        cert_id, 'renewed',
                        f"Certificate renewed. Issuer: Internal CA, Serial: {result['serial_number']}, "
                        f"Expiry: {result['not_after'][:10]}",
                        performed_by='renewal_system',
                    )
                    st.rerun()

                else:
                    # External CA: go through approval/payment workflow
                    db.update_certificate(cert_id, {
                        'csr_pem': csr_pem,
                        'private_key_pem': key_pem,
                        'key_size': new_key_size,
                        'ca_type': 'external',
                        'ca_provider': use_provider,
                    })
                    auto_approve = config.getboolean('workflow', 'auto_approve_renewals', fallback=False)

                    if auto_approve:
                        db.update_certificate_status(
                            cert_id, 'pending_payment',
                            triggered_by='renewal_system',
                            notes=f"Renewal auto-approved. Pending payment for {use_provider}."
                        )
                        st.success(f"✅ Renewal auto-approved! Proceed to Payment page.")
                    else:
                        db.update_certificate_status(
                            cert_id, 'pending_approval',
                            triggered_by='renewal_system',
                            notes=f"Renewal request submitted for {use_provider}. Pending approval."
                        )
                        st.info(f"📝 Renewal request submitted! Proceed to Approval Center for review.")
                    st.rerun()

            except Exception as e:
                progress_bar.progress(1.0)
                status_text.markdown("**❌ Renewal failed**")
                st.error(f"❌ {e}")
                import traceback
                with st.expander("🔍 Error Details"):
                    st.code(traceback.format_exc())
