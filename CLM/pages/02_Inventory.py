"""
Page 2 – Certificate Inventory
Complete view of all tracked certificates with filtering, details, and AI risk assessment.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from core.database import db
from core.ai_agent import ai_agent
from utils.helpers import (days_until_expiry, get_expiry_status,
                           get_status_emoji, get_status_color, WORKFLOW_STATES)

st.set_page_config(page_title="Inventory", page_icon="📋", layout="wide")

st.markdown("# 📋 Certificate Inventory")
st.markdown("Complete inventory of all managed certificates across your infrastructure.")
st.divider()

# ──────────── Filters ────────────
f1, f2, f3, f4 = st.columns(4)
with f1:
    status_filter = st.selectbox("Filter by Status", ["All"] + WORKFLOW_STATES)
with f2:
    ca_filter = st.selectbox("Filter by CA Type", ["All", "local", "external"])
with f3:
    env_filter = st.selectbox("Filter by Environment", ["All", "production", "staging", "development"])
with f4:
    search = st.text_input("🔍 Search", placeholder="domain name...")

# Fetch certificates
certs = db.get_all_certificates(
    status=None if status_filter == "All" else status_filter,
    ca_type=None if ca_filter == "All" else ca_filter,
    environment=None if env_filter == "All" else env_filter,
)

# Apply search filter
if search:
    certs = [c for c in certs if search.lower() in c.get('common_name', '').lower()
             or search.lower() in c.get('san', '').lower()
             or search.lower() in c.get('server', '').lower()]

st.divider()

# ──────────── Summary Metrics ────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Showing", len(certs))
local_count = sum(1 for c in certs if c.get('ca_type') == 'local')
m2.metric("Local CA", local_count)
m3.metric("External CA", len(certs) - local_count)
expiring = sum(1 for c in certs if (d := days_until_expiry(c.get('not_after'))) is not None and 0 < d <= 30)
m4.metric("⚠️ Expiring <30d", expiring)

st.divider()

# ──────────── Certificate Table ────────────
if certs:
    table_data = []
    for c in certs:
        d = days_until_expiry(c.get('not_after'))
        status_label, _ = get_expiry_status(d)
        table_data.append({
            'ID': c['id'],
            'Common Name': c['common_name'],
            'Status': f"{get_status_emoji(c['status'])} {c['status']}",
            'CA Type': c.get('ca_type', '').upper(),
            'Provider': c.get('ca_provider', '-'),
            'Environment': c.get('environment', ''),
            'Server': f"{c.get('server', '')}:{c.get('port', 443)}",
            'Expires': c.get('not_after', 'N/A')[:10] if c.get('not_after') else 'N/A',
            'Days Left': d if d is not None else 'N/A',
            'Health': status_label,
        })

    df = pd.DataFrame(table_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            'ID': st.column_config.NumberColumn(width="small"),
            'Days Left': st.column_config.NumberColumn(width="small"),
        },
    )

    # ──────────── Certificate Detail View ────────────
    st.divider()
    st.subheader("🔍 Certificate Details")
    cert_ids = [c['id'] for c in certs]
    cert_names = [f"#{c['id']} - {c['common_name']}" for c in certs]
    selected = st.selectbox("Select a certificate", cert_names)

    if selected:
        cert_id = int(selected.split(' - ')[0].replace('#', ''))
        cert = db.get_certificate(cert_id)

        if cert:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("#### 📜 Certificate Information")
                st.markdown(f"**Common Name:** `{cert['common_name']}`")
                st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
                st.markdown(f"**Issuer:** `{cert.get('issuer', 'N/A')}`")
                st.markdown(f"**Serial Number:** `{cert.get('serial_number', 'N/A')}`")
                st.markdown(f"**Thumbprint:** `{cert.get('thumbprint', 'N/A')}`")
                st.markdown(f"**Algorithm:** {cert.get('algorithm', 'RSA')} {cert.get('key_size', 2048)}-bit")

            with col_b:
                st.markdown("#### 🏗️ Deployment Information")
                st.markdown(f"**Status:** {get_status_emoji(cert['status'])} `{cert['status']}`")
                st.markdown(f"**CA Type:** `{cert.get('ca_type', 'N/A').upper()}`")
                st.markdown(f"**Provider:** `{cert.get('ca_provider', 'N/A')}`")
                st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")
                st.markdown(f"**Server:** `{cert.get('server', 'N/A')}:{cert.get('port', 443)}`")
                st.markdown(f"**Valid From:** `{cert.get('not_before', 'N/A')}`")
                st.markdown(f"**Expires:** `{cert.get('not_after', 'N/A')}`")

            # Workflow History
            st.markdown("#### 🔄 Workflow History")
            history = db.get_workflow_history(cert_id)
            if history:
                for h in history:
                    st.markdown(
                        f"  `{h['from_state']}` → `{h['to_state']}` "
                        f"by **{h['triggered_by']}** at {h['created_at']}"
                    )
            else:
                st.caption("No workflow transitions recorded.")

            # Audit Log
            st.markdown("#### 📝 Audit Log")
            logs = db.get_audit_log(cert_id=cert_id, limit=10)
            if logs:
                for log in logs:
                    st.markdown(f"  🔹 **{log['action']}** — {log['details'][:100]} ({log['created_at']})")
            else:
                st.caption("No audit entries.")

            # AI Risk Assessment
            st.divider()
            if st.button("🤖 AI Risk Assessment", key="risk_assess"):
                with st.spinner("AI Agent is assessing certificate risk..."):
                    assessment = ai_agent.assess_risk(cert)
                st.markdown(assessment)

            # AI Compliance Check
            if st.button("📋 AI Compliance Check", key="compliance_check"):
                with st.spinner("AI Agent is checking compliance..."):
                    compliance = ai_agent.check_compliance(cert)
                st.markdown(compliance)

else:
    st.info("No certificates found matching the current filters.")
    if st.button("📡 Go to Discovery to scan for certificates"):
        st.switch_page("pages/01_Discovery.py")
