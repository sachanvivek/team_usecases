"""
Page 3 – Request Certificate
Create new certificate requests with CSR generation.
Supports both Local CA and External CA paths.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from core.database import db
from core.ai_agent import ai_agent
from core.certificate_ops import generate_csr, ExternalCA
from core.payment import CERT_TYPE_DESCRIPTIONS
from utils.config import config

st.set_page_config(page_title="Request Certificate", page_icon="📝", layout="wide")

st.markdown("# 📝 Request New Certificate")
st.markdown("Submit a new certificate request. Generates a CSR and routes through the approval workflow.")
st.divider()

# ──────────── Request Form ────────────
col_form, col_preview = st.columns([3, 2])

with col_form:
    st.subheader("📋 Certificate Request Form")

    common_name = st.text_input("Common Name (CN) *", placeholder="www.example.com")
    san_input = st.text_input("Subject Alternative Names (comma-separated)",
                              placeholder="example.com, www.example.com, api.example.com")

    c1, c2 = st.columns(2)
    with c1:
        ca_type = st.selectbox("CA Type *", ["local", "external"])
        key_size = st.selectbox("Key Size", [2048, 4096], index=0)
        algorithm = st.selectbox("Algorithm", ["RSA", "EC"])
        environment = st.selectbox("Environment", ["production", "staging", "development"])
    with c2:
        if ca_type == "external":
            ca_provider = st.selectbox("CA Provider", ExternalCA.get_providers())
            cert_type = st.selectbox("Certificate Type",
                                     list(CERT_TYPE_DESCRIPTIONS.keys()),
                                     format_func=lambda x: f"{x} — {CERT_TYPE_DESCRIPTIONS[x]}")
            validity_years = st.selectbox("Validity Period", [1, 2, 3],
                                          format_func=lambda x: f"{x} Year(s)")
        else:
            ca_provider = None
            cert_type = "Internal"
            validity_years = 1
            st.info("🏠 Local CA certificates are issued by your internal Enterprise CA "
                    "at no cost.")
            cert_validity = config.getint('local_ca', 'cert_validity_days', fallback=365)
            st.metric("Validity", f"{cert_validity} days")

    org = st.text_input("Organization",
                        value=config.get('local_ca', 'ca_org', fallback='Enterprise Corp'))
    c3, c4 = st.columns(2)
    with c3:
        country = st.text_input("Country", value=config.get('local_ca', 'ca_country', fallback='US'), max_chars=2)
        state = st.text_input("State", value=config.get('local_ca', 'ca_state', fallback=''))
    with c4:
        city = st.text_input("City", value=config.get('local_ca', 'ca_city', fallback=''))
        requestor = st.text_input("Requestor Name", value="admin")

    server = st.text_input("Target Server (IP or hostname)", placeholder="10.0.1.100")
    port = st.number_input("Target Port", value=443, min_value=1, max_value=65535)
    notes = st.text_area("Notes", placeholder="Reason for request, business justification...", height=80)

with col_preview:
    st.subheader("📄 Request Preview")

    st.markdown(f"""
    | Field | Value |
    |-------|-------|
    | **Common Name** | `{common_name or '(required)'}` |
    | **SANs** | `{san_input or 'None'}` |
    | **CA Type** | `{ca_type.upper()}` |
    | **Provider** | `{ca_provider or 'Internal CA'}` |
    | **Cert Type** | `{cert_type}` |
    | **Key Size** | `{key_size}-bit {algorithm}` |
    | **Environment** | `{environment}` |
    | **Server** | `{server or 'N/A'}:{port}` |
    | **Requestor** | `{requestor}` |
    """)

    if ca_type == "external" and ca_provider:
        price = ExternalCA.get_price(ca_provider, cert_type, validity_years)
        st.divider()
        st.markdown("#### 💰 Estimated Cost")
        if price > 0:
            tax = round(price * 0.08, 2)
            st.metric("Subtotal", f"${price:,.2f}")
            st.metric("Tax (8%)", f"${tax:,.2f}")
            st.metric("Total", f"${price + tax:,.2f}")
        else:
            st.success("🆓 Free (Let's Encrypt)")

    # AI Recommendation
    st.divider()
    if common_name and st.button("🤖 AI Pre-Check"):
        with st.spinner("AI Agent reviewing request..."):
            req_info = {
                'common_name': common_name,
                'san': san_input,
                'ca_type': ca_type,
                'ca_provider': ca_provider,
                'cert_type': cert_type,
                'key_size': key_size,
                'algorithm': algorithm,
                'environment': environment,
            }
            recommendation = ai_agent.recommend_ca_provider(req_info)
        st.markdown(recommendation)

# ──────────── Submit Request ────────────
st.divider()
submit = st.button("🚀 Submit Certificate Request", type="primary", use_container_width=True)

if submit:
    if not common_name:
        st.error("❌ Common Name is required.")
        st.stop()

    san_list = [s.strip() for s in san_input.split(',') if s.strip()] if san_input else []
    if common_name not in san_list:
        san_list.insert(0, common_name)

    with st.spinner("🔑 Generating CSR and private key..."):
        try:
            csr_pem, key_pem, _ = generate_csr(
                common_name=common_name,
                san_list=san_list,
                org=org,
                country=country if country else None,
                state=state if state else None,
                city=city if city else None,
                key_size=key_size,
                algorithm=algorithm,
            )
        except Exception as e:
            st.error(f"❌ CSR generation failed: {e}")
            st.stop()

    # Determine initial status
    requires_approval = config.getboolean('workflow', 'require_approval', fallback=True)
    initial_status = 'pending_approval' if requires_approval else 'approved'

    cert_data = {
        'common_name': common_name,
        'san': ','.join(san_list),
        'ca_type': ca_type,
        'ca_provider': ca_provider,
        'cert_type': cert_type,
        'status': initial_status,
        'environment': environment,
        'server': server,
        'port': port,
        'key_size': key_size,
        'algorithm': algorithm,
        'csr_pem': csr_pem,
        'private_key_pem': key_pem,
        'requestor': requestor,
        'notes': notes,
    }

    cert_id = db.add_certificate(cert_data)
    db.add_audit_log(cert_id, 'csr_generated',
                     f"CSR generated for {common_name} ({algorithm} {key_size}-bit)",
                     performed_by=requestor)

    st.success(f"✅ Certificate request submitted! ID: **#{cert_id}**")
    st.info(f"📌 Status: **{initial_status.replace('_', ' ').title()}** — "
            f"Proceed to the Approval Center to review this request.")

    with st.expander("📄 View Generated CSR"):
        st.code(csr_pem, language="text")
