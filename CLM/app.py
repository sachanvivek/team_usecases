"""
Certificate Lifecycle Manager – Main Dashboard
================================================
Streamlit multi-page app entry point.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

from core.database import db
from core.ai_agent import ai_agent
from utils.helpers import days_until_expiry, get_expiry_status, get_status_emoji

# ──────────────── Page Config ────────────────
st.set_page_config(
    page_title="Certificate Lifecycle Manager",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────── Custom CSS ────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem; font-weight: 700;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-header { color: #8e99a4; font-size: 1rem; margin-bottom: 2rem; }
    .metric-card {
        background: linear-gradient(135deg, #667eea22, #764ba222);
        border-radius: 12px; padding: 1.2rem; border: 1px solid #667eea44;
    }
    .workflow-step {
        text-align: center; padding: 0.5rem;
        border-radius: 8px; font-size: 0.85rem; font-weight: 600;
    }
    .stMetric > div { background: #254ea1; border-radius: 10px; padding: 12px; }
</style>
""", unsafe_allow_html=True)

# ──────────────── Seed demo data ────────────────
db.seed_demo_data()

# ──────────────── Sidebar ────────────────
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/ssl-security.png", width=60)
    st.markdown("### 🔐 Cert Lifecycle Manager")
    st.caption("AI-Powered Certificate Management")
    st.divider()

    # AI Status indicator
    ai_status = ai_agent.is_available()
    if ai_status:
        st.success("🤖 AI Agent: Online", icon="✅")
    else:
        st.warning("🤖 AI Agent: Offline", icon="⚠️")

    st.divider()
    st.markdown("#### 📋 Workflow Stages")
    st.markdown("""
    1. 📡 **Discovery** – Scan & find certs
    2. 📋 **Inventory** – Track all certs
    3. 📝 **Request** – New cert requests
    4. ✅ **Approval** – AI-assisted review
    5. 💳 **Payment** – External CA billing
    6. 🏛️ **Issuance** – Generate certs
    7. 🚀 **Deployment** – Deploy to servers
    8. 📊 **Monitoring** – Health checks
    9. 🔄 **Renewal** – Renew expiring certs
    10. 🚫 **Revocation** – Revoke certs
    11. 🤖 **AI Assistant** – Chat with AI
    """)

# ──────────────── Main Dashboard ────────────────
st.markdown('<p class="main-header">🔐 Certificate Lifecycle Manager</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">AI-Powered End-to-End Certificate Management — From Discovery to Deployment</p>', unsafe_allow_html=True)

# KPI Row
stats = db.get_statistics()
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("📊 Total Certificates", stats['total'])
k2.metric("✅ Active", stats['active'])
k3.metric("⚠️ Expiring Soon", stats['expiring_soon'])
k4.metric("💀 Expired", stats['expired'])
k5.metric("⏳ Pending Approval", stats['pending_approval'])
k6.metric("💳 Pending Payment", stats['pending_payment'])

st.divider()

# ──────────────── Workflow Diagram ────────────────
st.subheader("🔄 Certificate Lifecycle Workflow")
wf_cols = st.columns(10)
workflow_stages = [
    ("📡", "Discovery", "#3498db"),
    ("📋", "Inventory", "#2ecc71"),
    ("📝", "Request", "#9b59b6"),
    ("✅", "Approval", "#f39c12"),
    ("💳", "Payment", "#e67e22"),
    ("🏛️", "Issuance", "#1abc9c"),
    ("🚀", "Deploy", "#2980b9"),
    ("📊", "Monitor", "#27ae60"),
    ("🔄", "Renewal", "#8e44ad"),
    ("🚫", "Revoke", "#e74c3c"),
]
for i, (icon, label, color) in enumerate(workflow_stages):
    with wf_cols[i]:
        st.markdown(
            f'<div class="workflow-step" style="background:{color}22;border:2px solid {color}">'
            f'{icon}<br>{label}</div>',
            unsafe_allow_html=True,
        )

st.divider()

# ──────────────── Charts Row ────────────────
col_left, col_mid, col_right = st.columns(3)

with col_left:
    st.subheader("📊 Certificates by Status")
    if stats['by_status']:
        fig_status = px.pie(
            names=list(stats['by_status'].keys()),
            values=list(stats['by_status'].values()),
            color_discrete_sequence=px.colors.qualitative.Set2,
            hole=0.4,
        )
        fig_status.update_layout(
            margin=dict(t=20, b=20, l=20, r=20), height=300,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='white'),
        )
        st.plotly_chart(fig_status, use_container_width=True)
    else:
        st.info("No certificate data yet.")

with col_mid:
    st.subheader("🏛️ By CA Type")
    if stats['by_ca_type']:
        fig_ca = px.pie(
            names=[k or 'Unknown' for k in stats['by_ca_type'].keys()],
            values=list(stats['by_ca_type'].values()),
            color_discrete_sequence=['#667eea', '#764ba2', '#f093fb'],
            hole=0.4,
        )
        fig_ca.update_layout(
            margin=dict(t=20, b=20, l=20, r=20), height=300,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='white'),
        )
        st.plotly_chart(fig_ca, use_container_width=True)
    else:
        st.info("No certificate data yet.")

with col_right:
    st.subheader("🌐 By Environment")
    if stats['by_environment']:
        fig_env = px.pie(
            names=[k or 'Unknown' for k in stats['by_environment'].keys()],
            values=list(stats['by_environment'].values()),
            color_discrete_sequence=['#e74c3c', '#f39c12', '#2ecc71'],
            hole=0.4,
        )
        fig_env.update_layout(
            margin=dict(t=20, b=20, l=20, r=20), height=300,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='white'),
        )
        st.plotly_chart(fig_env, use_container_width=True)
    else:
        st.info("No certificate data yet.")

st.divider()

# ──────────────── Expiry Timeline ────────────────
st.subheader("⏰ Certificate Expiry Timeline")
all_certs = db.get_all_certificates()
if all_certs:
    timeline_data = []
    for c in all_certs:
        d = days_until_expiry(c.get('not_after'))
        if d is not None:
            status_label, _ = get_expiry_status(d)
            timeline_data.append({
                'Certificate': c['common_name'],
                'Days Until Expiry': d,
                'Status': status_label,
                'Environment': c.get('environment', 'N/A'),
            })
    if timeline_data:
        df_timeline = pd.DataFrame(timeline_data).sort_values('Days Until Expiry')
        color_map = {'Expired': '#e74c3c', 'Critical': '#e74c3c',
                     'Warning': '#f39c12', 'Attention': '#f1c40f',
                     'Healthy': '#2ecc71', 'Unknown': '#95a5a6'}
        fig_tl = px.bar(
            df_timeline, x='Certificate', y='Days Until Expiry',
            color='Status', color_discrete_map=color_map,
            text='Days Until Expiry',
        )
        fig_tl.update_layout(
            height=350, margin=dict(t=20, b=20),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='white'),
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#333'),
        )
        st.plotly_chart(fig_tl, use_container_width=True)

st.divider()

# ──────────────── Recent Activity ────────────────
st.subheader("📜 Recent Activity")
logs = db.get_audit_log(limit=15)
if logs:
    for log in logs:
        emoji = "🔵"
        action = log.get('action', '')
        if 'created' in action:
            emoji = "🟢"
        elif 'status_change' in action:
            emoji = "🔄"
        elif 'payment' in action:
            emoji = "💳"
        elif 'revoke' in action:
            emoji = "🔴"
        st.markdown(
            f"{emoji} **{log.get('action', 'N/A')}** — "
            f"{log.get('details', '')[:120]} "
            f"<small style='color:#888'>({log.get('created_at', '')})</small>",
            unsafe_allow_html=True,
        )
else:
    st.info("No activity recorded yet.")

# ──────────────── AI Inventory Analysis ────────────────
st.divider()
st.subheader("🤖 AI Inventory Analysis")
if st.button("🧠 Run AI Analysis on Inventory", type="primary"):
    with st.spinner("AI Agent is analyzing your certificate inventory..."):
        analysis = ai_agent.analyze_inventory(stats)
    st.markdown(analysis)

# ──────────────── Reset Demo Data ────────────────
st.divider()
st.subheader("🔄 Demo Controls")
reset_col1, reset_col2 = st.columns([3, 1])
with reset_col1:
    st.caption(
        "Reset the entire database and re-populate with fresh demo certificates "
        "across every workflow stage: Discovered, Requested, Pending Approval, "
        "Approved, Pending Payment, Paid, Issued, Deployed, Expired, Revoked, "
        "and Renewal Requested."
    )
with reset_col2:
    if st.button("🗑️ Reset & Re-Seed Demo", type="secondary", use_container_width=True):
        db.reset_and_seed()
        st.toast("✅ Database cleared and re-seeded with fresh demo data!", icon="🎉")
        st.rerun()

st.divider()
st.caption("🔐 Certificate Lifecycle Manager v2.0 | Powered by Ollama llama3.2 AI Agent")
