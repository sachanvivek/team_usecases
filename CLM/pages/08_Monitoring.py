"""
Page 8 – Certificate Monitoring
Monitor certificate health, expiry, and compliance with dashboards and alerts.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

from core.database import db
from core.ai_agent import ai_agent
from utils.helpers import days_until_expiry, get_expiry_status, get_status_emoji
from utils.config import config

st.set_page_config(page_title="Monitoring", page_icon="📊", layout="wide")

st.markdown("# 📊 Certificate Monitoring")
st.markdown("Real-time monitoring of certificate health, expiry alerts, and compliance status.")
st.divider()

warning_days = config.getint('monitoring', 'warning_days', fallback=30)
critical_days = config.getint('monitoring', 'critical_days', fallback=7)

# ──────────── Health Overview ────────────
all_certs = db.get_all_certificates()
active_certs = [c for c in all_certs if c['status'] in ('deployed', 'active', 'issued')]

if not all_certs:
    st.info("No certificates to monitor. Add certificates through Discovery or Request.")
    st.stop()

# Categorize by health
health_counts = {'Healthy': 0, 'Attention': 0, 'Warning': 0, 'Critical': 0, 'Expired': 0}
health_certs = {'Healthy': [], 'Attention': [], 'Warning': [], 'Critical': [], 'Expired': []}

for c in active_certs:
    d = days_until_expiry(c.get('not_after'))
    label, _ = get_expiry_status(d)
    if label in health_counts:
        health_counts[label] += 1
        health_certs[label].append(c)

# KPIs
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("✅ Healthy", health_counts['Healthy'])
k2.metric("🟡 Attention (<90d)", health_counts['Attention'])
k3.metric("🟠 Warning (<30d)", health_counts['Warning'])
k4.metric("🔴 Critical (<7d)", health_counts['Critical'])
k5.metric("💀 Expired", health_counts['Expired'])

# Health gauge
total_active = len(active_certs) or 1
health_score = round(
    (health_counts['Healthy'] + health_counts['Attention'] * 0.7) / total_active * 100
)
st.divider()

col_gauge, col_pie = st.columns(2)

with col_gauge:
    st.subheader("🎯 Overall Health Score")
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=health_score,
        title={'text': "Certificate Health", 'font': {'color': 'white'}},
        gauge={
            'axis': {'range': [0, 100], 'tickcolor': 'white'},
            'bar': {'color': '#2ecc71' if health_score > 70 else '#f39c12' if health_score > 40 else '#e74c3c'},
            'steps': [
                {'range': [0, 40], 'color': 'rgba(231,76,60,0.2)'},
                {'range': [40, 70], 'color': 'rgba(243,156,18,0.2)'},
                {'range': [70, 100], 'color': 'rgba(46,204,113,0.2)'},
            ],
            'threshold': {
                'line': {'color': 'white', 'width': 3},
                'thickness': 0.75,
                'value': health_score,
            },
        },
        number={'font': {'color': 'white'}},
    ))
    fig_gauge.update_layout(
        height=300, margin=dict(t=40, b=20),
        paper_bgcolor='rgba(0,0,0,0)', font=dict(color='white'),
    )
    st.plotly_chart(fig_gauge, use_container_width=True)

with col_pie:
    st.subheader("📊 Health Distribution")
    labels = [k for k, v in health_counts.items() if v > 0]
    values = [v for v in health_counts.values() if v > 0]
    colors_map = {'Healthy': '#2ecc71', 'Attention': '#f1c40f',
                  'Warning': '#f39c12', 'Critical': '#e74c3c', 'Expired': '#95a5a6'}
    colors = [colors_map.get(l, '#333') for l in labels]

    if labels:
        fig_pie = px.pie(names=labels, values=values,
                         color_discrete_sequence=colors, hole=0.4)
        fig_pie.update_layout(
            height=300, margin=dict(t=20, b=20),
            paper_bgcolor='rgba(0,0,0,0)', font=dict(color='white'),
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No active certificates to chart.")

st.divider()

# ──────────── Alerts ────────────
st.subheader("🚨 Active Alerts")

alerts = health_certs['Critical'] + health_certs['Warning'] + health_certs['Expired']
if alerts:
    for c in alerts:
        d = days_until_expiry(c.get('not_after'))
        label, color = get_expiry_status(d)
        icon = "🔴" if label in ('Critical', 'Expired') else "🟠"

        if label == 'Expired':
            msg = f"{icon} **EXPIRED** — `{c['common_name']}` on `{c.get('server','?')}` expired {abs(d)} days ago!"
        elif label == 'Critical':
            msg = f"{icon} **CRITICAL** — `{c['common_name']}` on `{c.get('server','?')}` expires in **{d} days**!"
        else:
            msg = f"{icon} **WARNING** — `{c['common_name']}` on `{c.get('server','?')}` expires in **{d} days**."
        if label == 'Warning':
            st.warning(msg)
        else:
            st.error(msg)
else:
    st.success("🎉 No active alerts! All certificates are healthy.")

st.divider()

# ──────────── Expiry Timeline Chart ────────────
st.subheader("📅 Expiry Timeline")
timeline_data = []
for c in active_certs:
    d = days_until_expiry(c.get('not_after'))
    if d is not None:
        label, _ = get_expiry_status(d)
        timeline_data.append({
            'Certificate': f"{c['common_name']} ({c.get('server', '')})",
            'Days Until Expiry': d,
            'Health': label,
            'CA Type': c.get('ca_type', '').upper(),
            'Environment': c.get('environment', ''),
        })

if timeline_data:
    df_tl = pd.DataFrame(timeline_data).sort_values('Days Until Expiry')
    color_map = {'Expired': '#e74c3c', 'Critical': '#e74c3c',
                 'Warning': '#f39c12', 'Attention': '#f1c40f',
                 'Healthy': '#2ecc71'}
    fig = px.bar(df_tl, x='Certificate', y='Days Until Expiry',
                 color='Health', color_discrete_map=color_map,
                 text='Days Until Expiry',
                 hover_data=['CA Type', 'Environment'])
    fig.add_hline(y=critical_days, line_dash="dash", line_color="#e74c3c",
                  annotation_text=f"Critical ({critical_days}d)")
    fig.add_hline(y=warning_days, line_dash="dash", line_color="#f39c12",
                  annotation_text=f"Warning ({warning_days}d)")
    fig.update_layout(
        height=400, margin=dict(t=20, b=20),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='white'),
        xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#333'),
    )
    st.plotly_chart(fig, use_container_width=True)

# ──────────── Certificate Details Table ────────────
st.divider()
st.subheader("📋 Monitored Certificates")
if active_certs:
    table = []
    for c in active_certs:
        d = days_until_expiry(c.get('not_after'))
        label, _ = get_expiry_status(d)
        table.append({
            'ID': c['id'],
            'Common Name': c['common_name'],
            'Server': f"{c.get('server', '')}:{c.get('port', 443)}",
            'Issuer': c.get('issuer', 'N/A')[:30],
            'Expires': c.get('not_after', 'N/A')[:10] if c.get('not_after') else 'N/A',
            'Days Left': d if d is not None else 'N/A',
            'Health': f"{get_status_emoji(c['status'])} {label}",
            'CA': c.get('ca_type', '').upper(),
            'Env': c.get('environment', ''),
        })
    st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

# ──────────── AI Monitoring Analysis ────────────
st.divider()
st.subheader("🤖 AI Monitoring Analysis")
if st.button("🧠 AI Health Check Analysis", type="primary"):
    with st.spinner("AI Agent analyzing certificate health..."):
        stats = db.get_statistics()
        analysis = ai_agent.analyze_inventory(stats)
    st.markdown(analysis)
