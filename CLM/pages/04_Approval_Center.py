"""
Page 4 – Approval Center
AI-assisted certificate request approval workflow.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from core.database import db
from core.ai_agent import ai_agent
from utils.helpers import get_status_emoji
from utils.config import config

st.set_page_config(page_title="Approval Center", page_icon="✅", layout="wide")

st.markdown("# ✅ Approval Center")
st.markdown("Review and approve/reject pending certificate requests with AI-powered recommendations.")
st.divider()

# Fetch pending certificates
pending = db.get_all_certificates(status=['pending_approval', 'requested'])

if not pending:
    st.info("🎉 No pending certificate requests! All caught up.")
    st.caption("New requests will appear here after being submitted from the Request page.")
    st.stop()

st.metric("Pending Requests", len(pending))
st.divider()

# ──────────── Review Each Request ────────────
for cert in pending:
    cert_id = cert['id']
    with st.expander(
        f"{get_status_emoji(cert['status'])} #{cert_id} — {cert['common_name']} "
        f"[{cert.get('ca_type', '').upper()}] — {cert.get('environment', '')}",
        expanded=True,
    ):
        col_info, col_action = st.columns([3, 2])

        with col_info:
            st.markdown("#### 📋 Request Details")
            st.markdown(f"**Common Name:** `{cert['common_name']}`")
            st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
            st.markdown(f"**CA Type:** `{cert.get('ca_type', 'N/A').upper()}`")
            if cert.get('ca_type') == 'external':
                st.markdown(f"**Provider:** `{cert.get('ca_provider', 'N/A')}`")
                st.markdown(f"**Cert Type:** `{cert.get('cert_type', 'N/A')}`")
            st.markdown(f"**Key Size:** `{cert.get('key_size', 'N/A')} {cert.get('algorithm', 'RSA')}`")
            st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")
            st.markdown(f"**Server:** `{cert.get('server', 'N/A')}:{cert.get('port', 443)}`")
            st.markdown(f"**Requested By:** `{cert.get('requestor', 'N/A')}`")
            st.markdown(f"**Notes:** {cert.get('notes', 'None')}")
            st.markdown(f"**Submitted:** {cert.get('created_at', 'N/A')}")

            if cert.get('csr_pem'):
                with st.expander("📄 View CSR"):
                    st.code(cert['csr_pem'], language="text")

        #with col_action:
            #st.markdown("#### 🤖 AI Recommendation")

            # AI Recommendation
            #if st.button("🧠 Get AI Recommendation", key=f"ai_rec_{cert_id}"):
            #    with st.spinner("AI Agent is analyzing the request..."):
            #        recommendation = ai_agent.recommend_approval(cert)
            #    st.session_state[f'ai_rec_{cert_id}'] = recommendation

            if f'ai_rec_{cert_id}' in st.session_state:
                st.markdown(st.session_state[f'ai_rec_{cert_id}'])

            st.divider()
            st.markdown("#### ⚡ Actions")

            approver_name = st.text_input("Approver Name", value="admin",
                                          key=f"approver_{cert_id}")
            approval_notes = st.text_area("Approval Notes", height=60,
                                          key=f"notes_{cert_id}",
                                          placeholder="Optional notes for audit trail...")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("✅ Approve", key=f"approve_{cert_id}",
                             type="primary", use_container_width=True):
                    # Determine next status based on CA type
                    if cert.get('ca_type') == 'external':
                        require_payment = config.getboolean('workflow', 'require_payment_for_external', fallback=True)
                        next_status = 'pending_payment'
                    else:
                        next_status = 'approved'

                    db.update_certificate(cert_id, {'approver': approver_name})
                    db.update_certificate_status(
                        cert_id, next_status,
                        triggered_by=approver_name,
                        notes=f"Approved. {approval_notes}"
                    )
                    ai_rec = st.session_state.get(f'ai_rec_{cert_id}', '')
                    db.add_audit_log(cert_id, 'approved',
                                     f"Approved by {approver_name}. {approval_notes}",
                                     performed_by=approver_name,
                                     ai_recommendation=ai_rec[:500] if ai_rec else None)
                    st.success(f"✅ Certificate #{cert_id} approved! Next: **{next_status.replace('_',' ').title()}**")
                    st.rerun()

            with btn_col2:
                if st.button("❌ Reject", key=f"reject_{cert_id}",
                             use_container_width=True):
                    db.update_certificate_status(
                        cert_id, 'rejected',
                        triggered_by=approver_name,
                        notes=f"Rejected. {approval_notes}"
                    )
                    db.add_audit_log(cert_id, 'rejected',
                                     f"Rejected by {approver_name}. {approval_notes}",
                                     performed_by=approver_name)
                    st.error(f"❌ Certificate #{cert_id} rejected.")
                    st.rerun()

