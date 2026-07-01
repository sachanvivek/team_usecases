"""
Page 10 – Certificate Revocation
Revoke compromised or no-longer-needed certificates with AI impact analysis.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from core.database import db
from core.ai_agent import ai_agent
from core.certificate_ops import internal_ca
from core.deployment import undeploy_from_nginx
from utils.helpers import get_status_emoji, days_until_expiry

st.set_page_config(page_title="Revocation", page_icon="🚫", layout="wide")

st.markdown("# 🚫 Certificate Revocation")
st.markdown("Revoke certificates that are compromised, no longer needed, or need to be replaced.")
st.divider()

# ──────────── Revocation Reasons ────────────
REVOCATION_REASONS = [
    "Key Compromise",
    "CA Compromise",
    "Affiliation Changed",
    "Superseded",
    "Cessation of Operation",
    "Certificate Hold",
    "Privilege Withdrawn",
    "Other",
]

# ──────────── Active Certificates (eligible for revocation) ────────────
eligible_statuses = ['deployed', 'active', 'issued', 'discovered']
all_certs = db.get_all_certificates()
eligible = [c for c in all_certs if c['status'] in eligible_statuses]

if not eligible:
    st.info("No active certificates eligible for revocation.")
    st.stop()

st.metric("Active Certificates", len(eligible))

# ──────────── Already Revoked ────────────
revoked = db.get_all_certificates(status='revoked')
if revoked:
    with st.expander(f"📜 Previously Revoked Certificates ({len(revoked)})"):
        rev_data = []
        for c in revoked:
            rev_data.append({
                'ID': c['id'],
                'Common Name': c['common_name'],
                'Issuer': c.get('issuer', 'N/A')[:30],
                'Server': c.get('server', ''),
                'Revoked At': c.get('updated_at', 'N/A'),
            })
        st.dataframe(pd.DataFrame(rev_data), use_container_width=True, hide_index=True)

st.divider()

# ──────────── Revocation Interface ────────────
st.subheader("⚠️ Revoke a Certificate")

# Certificate selector
cert_options = {f"#{c['id']} — {c['common_name']} [{c['status']}]": c['id']
                for c in eligible}
selected_label = st.selectbox("Select Certificate to Revoke", list(cert_options.keys()))
selected_id = cert_options[selected_label]
cert = db.get_certificate(selected_id)

if cert:
    col1, col2 = st.columns([2, 2])

    with col1:
        st.markdown("#### 📜 Certificate Details")
        st.markdown(f"**Common Name:** `{cert['common_name']}`")
        st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
        st.markdown(f"**Issuer:** `{cert.get('issuer', 'N/A')}`")
        st.markdown(f"**Serial:** `{cert.get('serial_number', 'N/A')}`")
        st.markdown(f"**Status:** {get_status_emoji(cert['status'])} `{cert['status']}`")
        st.markdown(f"**CA Type:** `{cert.get('ca_type', 'N/A').upper()}`")
        st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")
        st.markdown(f"**Server:** `{cert.get('server', 'N/A')}:{cert.get('port', 443)}`")
        d = days_until_expiry(cert.get('not_after'))
        if d is not None:
            st.markdown(f"**Days Until Expiry:** {d}")

    with col2:
        st.markdown("#### ⚠️ Revocation Form")
        reason = st.selectbox("Revocation Reason", REVOCATION_REASONS)
        details = st.text_area(
            "Additional Details",
            placeholder="Describe why this certificate needs to be revoked...",
            height=100,
        )
        performed_by = st.text_input("Performed By", value="admin")

        # AI Impact Analysis
        st.divider()
        if st.button("🤖 AI Impact Analysis", key="ai_impact"):
            with st.spinner("AI Agent analyzing revocation impact..."):
                impact = ai_agent.analyze_revocation_impact(cert)
            st.session_state['revocation_impact'] = impact

        if 'revocation_impact' in st.session_state:
            st.markdown(st.session_state['revocation_impact'])

    # Revocation confirmation
    st.divider()
    st.warning(
        f"⚠️ **Warning:** Revoking certificate `{cert['common_name']}` on "
        f"`{cert.get('server', 'N/A')}` will immediately invalidate it. "
        "Services using this certificate will be affected."
    )

    confirm = st.checkbox(
        f"I confirm I want to revoke certificate #{cert['id']} ({cert['common_name']})",
        key="confirm_revoke",
    )

    if st.button("🚫 Revoke Certificate", type="primary",
                 use_container_width=True, disabled=not confirm):

        # Revoke on the CA server first (for internal CA certs)
        ca_revoked = False
        ca_type = cert.get('ca_type', '').lower()
        if ca_type in ('internal', 'local', ''):
            with st.spinner("Revoking certificate on CA server..."):
                try:
                    ca_result = internal_ca.revoke_cert(cert['common_name'], reason)
                    if ca_result['success']:
                        st.success(f"✅ CA Server: {ca_result['message']}")
                        ca_revoked = True
                    else:
                        st.warning(f"⚠️ CA Server revocation issue: {ca_result['message']}")
                except Exception as e:
                    st.warning(f"⚠️ Could not reach CA server: {e}")

        # Remove certificate from the deployed server
        server_removed = False
        deploy_server = cert.get('server', '')
        if deploy_server and cert['status'] in ('deployed', 'active'):
            with st.spinner(f"Removing certificate from {deploy_server}..."):
                try:
                    undeploy_result = undeploy_from_nginx(
                        cert['common_name'], deploy_server
                    )
                    if undeploy_result['success']:
                        st.success(f"✅ Server: {undeploy_result['message']}")
                        for d in undeploy_result.get('details', []):
                            st.write(f"  - {d}")
                        server_removed = True
                    else:
                        st.warning(f"⚠️ Server removal issue: {undeploy_result['message']}")
                except Exception as e:
                    st.warning(f"⚠️ Could not remove cert from server: {e}")

        # Update local database
        revoke_notes = f"Reason: {reason}. {details}"
        if ca_revoked:
            revoke_notes += " [Revoked on CA server, CRL updated]"
        if server_removed:
            revoke_notes += f" [Removed from {deploy_server}]"

        db.update_certificate_status(
            cert['id'], 'revoked',
            triggered_by=performed_by,
            notes=revoke_notes,
        )
        db.add_audit_log(
            cert['id'], 'revoked',
            f"Certificate revoked. {revoke_notes}",
            performed_by=performed_by,
        )
        st.error(f"🚫 Certificate #{cert['id']} ({cert['common_name']}) has been revoked.")
        st.info("💡 Consider requesting a replacement certificate from the Request page.")

        # Clean up session state
        if 'revocation_impact' in st.session_state:
            del st.session_state['revocation_impact']

        st.rerun()
