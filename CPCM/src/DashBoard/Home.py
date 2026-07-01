import streamlit as st

st.set_page_config(
    page_title="CPCM – Predictive Capacity Management",
    layout="wide"
)

# =====================================================
# HERO SECTION
# =====================================================
st.title("CPCM – Centralize Predictive Capacity Management")
st.subheader("AI-Driven Intelligence for Proactive IT Operations")

st.markdown(
    """
    CPCM is an enterprise-grade, AI-powered platform designed to **predict, optimize,
    and automate capacity decisions** across on-prem, cloud, hybrid, and edge
    environments.  
    It enables IT organizations to move from **reactive firefighting**
    to **predictive and autonomous operations**.
    """
)

st.markdown("---")

# =====================================================
# WHAT IS CPCM
# =====================================================
st.markdown("## 🧠 What is CPCM?")
st.markdown(
    """
    CPCM continuously analyzes infrastructure utilization patterns, forecasts future
    demand, detects anomalies, and recommends intelligent scaling actions **before**
    performance degradation or cost overruns occur.

    It acts as a **single source of truth** for capacity decisions across the enterprise.
    """
)

# =====================================================
# KEY CAPABILITIES
# =====================================================
st.markdown("## ⚙️ Key Capabilities")

c1, c2, c3 = st.columns(3)

with c1:
    st.markdown(
        """
        **🔮 Predictive Forecasting**  
        Multi-model ML forecasting (ARIMA, ML, GenAI-ready) to predict future demand.
        """
    )

with c2:
    st.markdown(
        """
        **📏 Intelligent Rightsizing**  
        Scale-up, scale-down, or no-change recommendations based on real usage.
        """
    )

with c3:
    st.markdown(
        """
        **💰 Cost Impact Analysis**  
        Quantifies savings or additional cost *before* executing capacity changes.
        """
    )

c4, c5, c6 = st.columns(3)

with c4:
    st.markdown(
        """
        **🚨 Anomaly Detection**  
        Detects spikes, drops, and abnormal behavior in workloads.
        """
    )

with c5:
    st.markdown(
        """
        **🤖 Agent-Driven Automation**  
        Modular AI agents collaborate to detect, decide, and act.
        """
    )

with c6:
    st.markdown(
        """
        **🛡️ Governance & Approval**  
        Manual approvals, audit trails, and compliance-ready workflows.
        """
    )

st.markdown("---")

# =====================================================
# BUSINESS VALUE
# =====================================================
st.markdown("## 📈 Business Value")

st.markdown(
    """
    - 🚀 Reduced MTTR through proactive capacity planning  
    - 💰 Lower infrastructure and cloud costs  
    - 📊 Improved performance stability  
    - 🤖 Higher automation and operational maturity  
    - 🔍 End-to-end visibility across IT environments
    """
)

st.markdown("---")

# =====================================================
# ARCHITECTURE HIGHLIGHTS
# =====================================================
st.markdown("## 🏗️ Architecture Highlights")

st.markdown(
    """
    - Modular AI agent orchestration  
    - Model-agnostic forecasting engine  
    - API-first and cloud-native design  
    - Real-time dashboards and analytics  
    - ITSM, automation, and cloud platform integrations
    """
)

st.markdown("---")

# =====================================================
# AWARDS & INDUSTRY RECOGNITION
# =====================================================
st.markdown("## 🏆 Awards & Industry Recognition")

a1, a2, a3 = st.columns(3)

with a1:
    st.markdown(
        """
        ### 🥇 CII AI National Awards 2025  
        **Centralized Predictive Capacity Management**

        Winner under **Best AI-Based Solution** category for innovative
        AI-driven infrastructure capacity optimization.

        **Organized by:** CII  
        **Year:** 2025
        """
    )

with a2:
    st.markdown(
        """
        ### 🥈 AIGS Innovation Awards 2025  
        **GenAI / Agentic AI Innovation**

        Qualified and recognized for innovation presented by **ECM IT**
        in the **GenAI / Agentic AI** category.

        **Date:** Oct 29, 2025
        """
    )

with a3:
    st.markdown(
        """
        ### 🥉 TCS AI & Data Symposium FY26  
        **CPCM Accepted**

        CPCM accepted in **AI Symposium 26**
        and selected for publication in **TATA Digital Store**.
        """
    )

st.markdown("---")

# =====================================================
# CUSTOMER VOICES
# =====================================================
st.markdown("## 💬 Customer Voices")

v1, v2, v3, v4 = st.columns(4)

with v1:
    st.markdown(
        """
        > **“Good piece of job, very good piece of prediction made.
        Presented here in future phases.
        Nice pilot.
        Thanks to whole team.”**

        **— On-Prem Service Owner**
        """
    )

with v2:
    st.markdown(
        """
        > **“Appreciated the AI solution, looks promising to move on further.”**

        **— DC Engineering Lead**
        """
    )

with v3:
    st.markdown(
        """
        > **“It was a great work, very promising solution.
        I really like the approach.”**

        **— AI Architect**
        """
    )

with v4:
    st.markdown(
        """
        > **“TCS AI results seem to be bit more work,
        and accuracy is good.”**

        **— AI Innovation Manager**
        """
    )

st.markdown("---")

# =====================================================
# HIGHLIGHT BANNER
# =====================================================
st.markdown(
    """
    <div style="
        background-color:#0f172a;
        padding:18px;
        border-radius:12px;
        color:#e5e7eb;
        text-align:center;
        font-size:17px;
        font-weight:600;">
        Showcased at the Client’s Acceleration Innovation Event,
        highlighting CPCM’s role in AI-driven IT infrastructure
        resource optimization
    </div>
    """,
    unsafe_allow_html=True
)

st.markdown("---")

# =====================================================
# FOOTER
# =====================================================
st.caption(
    "CPCM | Centralized & Comprehensive Predictive Capacity Management | Tata Consultancy Services"
)
