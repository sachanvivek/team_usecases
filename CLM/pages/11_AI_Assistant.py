"""
Page 11 – AI Assistant
Interactive AI chat for certificate management questions, policy advice, and troubleshooting.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from core.database import db
from core.ai_agent import ai_agent

st.set_page_config(page_title="AI Assistant", page_icon="🤖", layout="wide")

st.markdown("# 🤖 AI Certificate Assistant")
st.markdown("Chat with the AI agent about certificate management, security best practices, and troubleshooting.")
st.divider()

# AI Status
if ai_agent.is_available():
    st.success("🟢 AI Agent is online and ready (Ollama llama3.2)")
else:
    st.warning(
        "🟡 AI Agent cannot reach Ollama. Make sure Ollama is running with:\n\n"
        "```bash\nollama run llama3.2\n```"
    )

st.divider()

# ──────────── Quick Actions ────────────
st.subheader("⚡ Quick Actions")
qa1, qa2, qa3, qa4 = st.columns(4)

with qa1:
    if st.button("📊 Inventory Analysis", use_container_width=True):
        st.session_state['quick_action'] = 'inventory'

with qa2:
    if st.button("🔄 Renewal Priorities", use_container_width=True):
        st.session_state['quick_action'] = 'renewal'

with qa3:
    if st.button("📋 Compliance Report", use_container_width=True):
        st.session_state['quick_action'] = 'compliance'

with qa4:
    if st.button("💡 Best Practices", use_container_width=True):
        st.session_state['quick_action'] = 'best_practices'

# Handle quick actions
if 'quick_action' in st.session_state:
    action = st.session_state.pop('quick_action')
    stats = db.get_statistics()

    if action == 'inventory':
        with st.spinner("🧠 Analyzing inventory..."):
            result = ai_agent.analyze_inventory(stats)
        st.markdown("### 📊 Inventory Analysis")
        st.markdown(result)

    elif action == 'renewal':
        all_certs = db.get_all_certificates()
        from utils.helpers import days_until_expiry
        expiring = [c for c in all_certs
                    if (d := days_until_expiry(c.get('not_after'))) is not None and d <= 90
                    and c['status'] not in ('revoked',)]
        if expiring:
            with st.spinner("🧠 Prioritizing renewals..."):
                result = ai_agent.prioritize_renewals(expiring)
            st.markdown("### 🔄 Renewal Priorities")
            st.markdown(result)
        else:
            st.success("No certificates expiring within 90 days!")

    elif action == 'compliance':
        all_certs = db.get_all_certificates()
        if all_certs:
            with st.spinner("🧠 Checking compliance for all certificates..."):
                # Check first 3 certificates for demo
                for c in all_certs[:3]:
                    st.markdown(f"#### 📋 {c['common_name']}")
                    result = ai_agent.check_compliance(c)
                    st.markdown(result)
                    st.divider()
        else:
            st.info("No certificates to check.")

    elif action == 'best_practices':
        with st.spinner("🧠 Generating best practices guide..."):
            result = ai_agent.chat(
                "Provide a comprehensive SSL/TLS certificate management best practices guide "
                "for an enterprise environment. Cover key management, rotation policies, "
                "monitoring, compliance, and automation recommendations."
            )
        st.markdown("### 💡 Certificate Management Best Practices")
        st.markdown(result)

st.divider()

# ──────────── Chat Interface ────────────
st.subheader("💬 Chat with AI Agent")

# Initialize chat history
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []

# Context toggle
include_context = st.checkbox("Include certificate inventory context", value=True)

# Display chat history
for msg in st.session_state.chat_history:
    with st.chat_message(msg['role'], avatar="🧑‍💻" if msg['role'] == 'user' else "🤖"):
        st.markdown(msg['content'])

# Chat input
user_input = st.chat_input("Ask about certificates, security, compliance...")

if user_input:
    # Add user message
    st.session_state.chat_history.append({'role': 'user', 'content': user_input})
    with st.chat_message("user", avatar="🧑‍💻"):
        st.markdown(user_input)

    # Build context
    context = None
    if include_context:
        stats = db.get_statistics()
        all_certs = db.get_all_certificates()
        cert_list = "\n".join(
            f"- {c['common_name']} | Status: {c['status']} | CA: {c.get('ca_type','')} | "
            f"Expires: {c.get('not_after', 'N/A')[:10] if c.get('not_after') else 'N/A'}"
            for c in all_certs[:15]
        )
        context = (
            f"Certificate Inventory Summary:\n"
            f"Total: {stats['total']}, Active: {stats['active']}, "
            f"Expiring: {stats['expiring_soon']}, Expired: {stats['expired']}\n\n"
            f"Certificates:\n{cert_list}"
        )

    # Get AI response
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Thinking..."):
            response = ai_agent.chat(user_input, context=context)
        st.markdown(response)

    st.session_state.chat_history.append({'role': 'assistant', 'content': response})

# Clear chat button
if st.session_state.chat_history:
    if st.button("🗑️ Clear Chat History"):
        st.session_state.chat_history = []
        st.rerun()

# ──────────── Example Prompts ────────────
st.divider()
st.subheader("💡 Example Questions")
examples = [
    "What certificates are expiring soon and what should I do?",
    "Explain the difference between DV, OV, and EV certificates",
    "How do I deploy a certificate to Nginx?",
    "What are the PCI DSS requirements for SSL/TLS certificates?",
    "Should I use RSA 2048 or 4096 for production certificates?",
    "How often should certificates be rotated?",
    "What's the best CA provider for an enterprise environment?",
    "How do I automate certificate renewal?",
]
for ex in examples:
    st.code(ex, language="text")
