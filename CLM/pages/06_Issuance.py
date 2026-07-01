"""
Page 6 – Certificate Issuance
Issue certificates using Local CA or External CA after approval (and payment for external).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from core.database import db
from core.ai_agent import ai_agent
from core.certificate_ops import local_ca, internal_ca, ExternalCA, generate_csr
from utils.helpers import get_status_emoji
from utils.config import config

st.set_page_config(page_title="Issuance", page_icon="🏛️", layout="wide")

st.markdown("# 🏛️ Certificate Issuance")
st.markdown("Issue certificates from Local CA or External CA for approved requests.")
st.divider()

# ──────────── Ready-to-Issue Certificates ────────────
# Local CA: approved → issue directly
# External CA: paid → issue
approved_local = db.get_all_certificates(status='approved', ca_type='local')
paid_external = db.get_all_certificates(status='paid', ca_type='external')
ready = approved_local + paid_external

if not ready:
    st.info("📭 No certificates ready for issuance.")
    st.markdown("""
    **Certificates become ready for issuance when:**
    - **Local CA:** Request is approved
    - **External CA:** Request is approved AND payment is completed
    """)
    st.stop()

st.metric("Ready to Issue", len(ready))

# Separate by CA type
tab_local, tab_external = st.tabs(["🏠 Local CA", "🌐 External CA"])

with tab_local:
    if approved_local:
        for cert in approved_local:
            cert_id = cert['id']
            with st.expander(
                f"🏠 #{cert_id} — {cert['common_name']} [LOCAL CA]",
                expanded=True,
            ):
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown(f"**Common Name:** `{cert['common_name']}`")
                    st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
                    st.markdown(f"**Key Size:** {cert.get('key_size', 2048)}-bit {cert.get('algorithm', 'RSA')}")
                    st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")
                    st.markdown(f"**Server:** `{cert.get('server', 'N/A')}:{cert.get('port', 443)}`")
                    st.markdown(f"**Approved by:** `{cert.get('approver', 'N/A')}`")

                with col2:
                    validity = config.getint('local_ca', 'cert_validity_days', fallback=825)
                    st.metric("CA", "Internal Intermediate CA")
                    st.metric("PKI", "Demo-CA (172.19.0.11)")
                    st.metric("Validity", f"{validity} days")
                    st.metric("Cost", "Free")

                if st.button(f"🏛️ Issue Certificate", key=f"issue_local_{cert_id}",
                             type="primary", use_container_width=True):
                    # Check if CSR exists
                    if not cert.get('csr_pem'):
                        st.warning("No CSR found. Generating CSR...")
                        san_list = [s.strip() for s in cert.get('san', '').split(',') if s.strip()]
                        csr_pem, key_pem, _ = generate_csr(
                            common_name=cert['common_name'],
                            san_list=san_list,
                            org='Internal PKI',
                            key_size=cert.get('key_size', 2048),
                            algorithm=cert.get('algorithm', 'RSA'),
                        )
                        db.update_certificate(cert_id, {
                            'csr_pem': csr_pem,
                            'private_key_pem': key_pem,
                        })
                        cert['csr_pem'] = csr_pem

                    with st.spinner("🔑 Signing certificate with Internal CA on Demo-CA..."):
                        try:
                            result = internal_ca.sign_csr(
                                cert['csr_pem'],
                                common_name=cert['common_name'],
                                validity_days=validity,
                            )
                            db.update_certificate(cert_id, {
                                'certificate_pem': result['certificate_pem'],
                                'private_key_pem': cert.get('private_key_pem', ''),
                                'issuer': result['issuer'],
                                'serial_number': result['serial_number'],
                                'thumbprint': result['thumbprint'],
                                'not_before': result['not_before'],
                                'not_after': result['not_after'],
                            })
                            db.update_certificate_status(
                                cert_id, 'issued',
                                triggered_by='internal_ca',
                                notes=f"Certificate issued by {result['issuer']} (Demo-CA). "
                                      f"Serial: {result['serial_number']}"
                            )
                            st.success(f"✅ Certificate #{cert_id} issued by Internal CA!")
                            st.info(f"Issuer: `{result['issuer']}` | Serial: `{result['serial_number']}`")
                            st.info(f"Valid: `{result['not_before'][:10]}` → `{result['not_after'][:10]}`")

                            with st.expander("📄 View Certificate PEM"):
                                st.code(result['certificate_pem'], language="text")

                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Issuance failed: {e}")
    else:
        st.info("No Local CA certificates ready for issuance.")

with tab_external:
    if paid_external:
        for cert in paid_external:
            cert_id = cert['id']
            provider = cert.get('ca_provider', 'DigiCert')
            cert_type = cert.get('cert_type', 'DV')

            with st.expander(
                f"🌐 #{cert_id} — {cert['common_name']} [{provider} {cert_type}]",
                expanded=True,
            ):
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown(f"**Common Name:** `{cert['common_name']}`")
                    st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
                    st.markdown(f"**Provider:** `{provider}`")
                    st.markdown(f"**Cert Type:** `{cert_type}`")
                    st.markdown(f"**Key Size:** {cert.get('key_size', 2048)}-bit")
                    st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")

                    # Show payment info
                    payments = db.get_payments(cert_id=cert_id)
                    if payments:
                        latest = payments[0]
                        st.markdown(f"**Payment:** ${latest['amount']:,.2f} via {latest.get('payment_method', 'N/A')} "
                                    f"(TXN: {latest.get('transaction_id', 'N/A')})")

                with col2:
                    st.metric("CA Provider", provider)
                    st.metric("Type", cert_type)
                    st.metric("Payment", "✅ Completed")

                if st.button(f"🌐 Issue Certificate", key=f"issue_ext_{cert_id}",
                             type="primary", use_container_width=True):
                    if not cert.get('csr_pem'):
                        st.warning("No CSR found. Generating CSR...")
                        san_list = [s.strip() for s in cert.get('san', '').split(',') if s.strip()]
                        csr_pem, key_pem, _ = generate_csr(
                            common_name=cert['common_name'],
                            san_list=san_list,
                            key_size=cert.get('key_size', 2048),
                            algorithm=cert.get('algorithm', 'RSA'),
                        )
                        db.update_certificate(cert_id, {
                            'csr_pem': csr_pem,
                            'private_key_pem': key_pem,
                        })
                        cert['csr_pem'] = csr_pem

                    with st.spinner(f"🌐 Requesting certificate from {provider}..."):
                        try:
                            result = ExternalCA.issue_certificate(
                                cert['csr_pem'], provider, cert_type, 365
                            )
                            db.update_certificate(cert_id, {
                                'certificate_pem': result['certificate_pem'],
                                'issuer': result['issuer'],
                                'serial_number': result['serial_number'],
                                'thumbprint': result['thumbprint'],
                                'not_before': result['not_before'],
                                'not_after': result['not_after'],
                            })
                            db.update_certificate_status(
                                cert_id, 'issued',
                                triggered_by=f'external_ca_{provider}',
                                notes=f"Certificate issued by {result['issuer']}. "
                                      f"Serial: {result['serial_number']}"
                            )
                            st.success(f"✅ Certificate #{cert_id} issued by {provider}!")
                            st.info(f"Serial: `{result['serial_number']}`")

                            with st.expander("📄 View Certificate PEM"):
                                st.code(result['certificate_pem'], language="text")

                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Issuance failed: {e}")
    else:
        st.info("No External CA certificates ready for issuance.")

# ──────────── Recently Issued ────────────
st.divider()
st.subheader("📜 Recently Issued Certificates")
issued = db.get_all_certificates(status='issued')
if issued:
    for c in issued[:5]:
        st.markdown(
            f"  🏛️ **#{c['id']}** — `{c['common_name']}` | "
            f"Issuer: {c.get('issuer', 'N/A')} | "
            f"Expires: {c.get('not_after', 'N/A')[:10] if c.get('not_after') else 'N/A'}"
        )
else:
    st.caption("No recently issued certificates.")
