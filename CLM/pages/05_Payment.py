"""
Page 5 – Payment Processing
Handle payments for External CA certificates with pricing, payment forms, and invoicing.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from core.database import db
from core.ai_agent import ai_agent
from core.certificate_ops import ExternalCA
from core.payment import (
    get_pricing_table, calculate_total, process_payment,
    PAYMENT_METHODS, CERT_TYPE_DESCRIPTIONS, CURRENCY,
)
from utils.helpers import get_status_emoji, generate_transaction_id

st.set_page_config(page_title="Payment", page_icon="💳", layout="wide")

st.markdown("# 💳 Payment Processing")
st.markdown("Process payments for external CA certificates. Local CA certificates are free.")
st.divider()

# ──────────── Pricing Catalog ────────────
with st.expander("📊 View Full Pricing Catalog", expanded=False):
    pricing = get_pricing_table()
    df_pricing = pd.DataFrame(pricing).drop(columns=['price_raw'])
    st.dataframe(df_pricing, use_container_width=True, hide_index=True)

st.divider()

# ──────────── Pending Payments ────────────
st.subheader("⏳ Certificates Awaiting Payment")
pending_certs = db.get_all_certificates(status='pending_payment')

if not pending_certs:
    st.info("💰 No certificates awaiting payment. External CA approvals will appear here.")
    st.stop()

for cert in pending_certs:
    cert_id = cert['id']
    provider = cert.get('ca_provider', 'DigiCert')
    cert_type = cert.get('cert_type', 'DV')

    with st.expander(
        f"💳 #{cert_id} — {cert['common_name']} [{provider} {cert_type}]",
        expanded=True,
    ):
        col_details, col_payment = st.columns([2, 3])

        with col_details:
            st.markdown("#### 📜 Certificate Details")
            st.markdown(f"**Common Name:** `{cert['common_name']}`")
            st.markdown(f"**SANs:** `{cert.get('san', 'N/A')}`")
            st.markdown(f"**Provider:** `{provider}`")
            st.markdown(f"**Type:** `{cert_type}` — {CERT_TYPE_DESCRIPTIONS.get(cert_type, '')}")
            st.markdown(f"**Environment:** `{cert.get('environment', 'N/A')}`")
            st.markdown(f"**Requested By:** `{cert.get('requestor', 'N/A')}`")

            # Cost calculation
            st.divider()
            st.markdown("#### 💰 Cost Breakdown")
            validity_years = st.selectbox(
                "Validity Period",
                [1, 2, 3],
                format_func=lambda x: f"{x} Year(s)",
                key=f"validity_{cert_id}",
            )
            cost = calculate_total(provider, cert_type, validity_years)
            st.metric("Subtotal", f"${cost['subtotal']:,.2f}")
            st.metric(f"Tax ({cost['tax_rate']*100:.0f}%)", f"${cost['tax']:,.2f}")
            st.metric("**Total**", f"${cost['total']:,.2f}", delta=None)

        with col_payment:
            st.markdown("#### 💳 Payment Form")

            payment_method = st.selectbox(
                "Payment Method",
                PAYMENT_METHODS,
                key=f"method_{cert_id}",
            )

            if payment_method == "Credit Card":
                card_number = st.text_input("Card Number",
                                            placeholder="4111 1111 1111 1111",
                                            key=f"card_{cert_id}")
                cc1, cc2 = st.columns(2)
                with cc1:
                    card_expiry = st.text_input("Expiry (MM/YY)", placeholder="12/28",
                                                key=f"expiry_{cert_id}")
                with cc2:
                    card_cvv = st.text_input("CVV", placeholder="123", type="password",
                                             key=f"cvv_{cert_id}", max_chars=4)
                billing_email = st.text_input("Billing Email",
                                              placeholder="billing@company.com",
                                              key=f"email_{cert_id}")

            elif payment_method == "Purchase Order":
                card_number = card_expiry = card_cvv = None
                po_number = st.text_input("Purchase Order Number",
                                          placeholder="PO-2026-001",
                                          key=f"po_{cert_id}")
                billing_email = st.text_input("Billing Email",
                                              placeholder="billing@company.com",
                                              key=f"email_po_{cert_id}")

            elif payment_method == "Wire Transfer":
                card_number = card_expiry = card_cvv = po_number = None
                billing_email = st.text_input("Billing Email",
                                              placeholder="billing@company.com",
                                              key=f"email_wire_{cert_id}")
                st.info("🏦 Wire transfer details will be provided after submission.")

            # AI cost recommendation
            if st.button("🤖 AI Cost Advice", key=f"ai_cost_{cert_id}"):
                with st.spinner("AI Agent analyzing pricing options..."):
                    advice = ai_agent.recommend_ca_provider({
                        'common_name': cert['common_name'],
                        'cert_type': cert_type,
                        'environment': cert.get('environment', 'production'),
                    })
                st.markdown(advice)

            st.divider()

            # Process Payment
            if st.button("💳 Process Payment", key=f"pay_{cert_id}",
                         type="primary", use_container_width=True):
                result = process_payment(
                    payment_method=payment_method,
                    amount=cost['total'],
                    card_number=card_number if payment_method == "Credit Card" else None,
                    card_expiry=card_expiry if payment_method == "Credit Card" else None,
                    card_cvv=card_cvv if payment_method == "Credit Card" else None,
                    po_number=po_number if payment_method == "Purchase Order" else None,
                    billing_email=billing_email if 'billing_email' in dir() else None,
                )

                if result['success']:
                    # Record payment
                    payment_data = {
                        'certificate_id': cert_id,
                        'amount': cost['total'],
                        'currency': CURRENCY,
                        'payment_method': payment_method,
                        'transaction_id': result['transaction_id'],
                        'status': 'completed',
                        'ca_provider': provider,
                        'cert_type': cert_type,
                        'validity_years': validity_years,
                        'card_last_four': result.get('card_last_four', ''),
                        'billing_email': billing_email if 'billing_email' in dir() else '',
                    }
                    db.add_payment(payment_data)

                    # Update certificate status
                    db.update_certificate_status(
                        cert_id, 'paid',
                        triggered_by='payment_system',
                        notes=f"Payment of ${cost['total']:,.2f} via {payment_method}. TXN: {result['transaction_id']}"
                    )
                    db.add_audit_log(
                        cert_id, 'payment_completed',
                        f"Payment of ${cost['total']:,.2f} processed. Method: {payment_method}. "
                        f"Transaction: {result['transaction_id']}",
                        performed_by='payment_system',
                    )

                    st.success(f"✅ {result['message']}")
                    st.info(f"🔑 Transaction ID: **{result['transaction_id']}**")
                    st.balloons()
                    st.rerun()
                else:
                    st.error(f"❌ {result['message']}")

# ──────────── Payment History ────────────
st.divider()
st.subheader("📜 Payment History")
payments = db.get_payments()
if payments:
    pay_data = []
    for p in payments:
        pay_data.append({
            'Transaction': p.get('transaction_id', 'N/A'),
            'Cert ID': p['certificate_id'],
            'Amount': f"${p['amount']:,.2f}",
            'Method': p.get('payment_method', 'N/A'),
            'Provider': p.get('ca_provider', 'N/A'),
            'Type': p.get('cert_type', 'N/A'),
            'Status': p.get('status', 'N/A'),
            'Date': p.get('created_at', 'N/A'),
        })
    st.dataframe(pd.DataFrame(pay_data), use_container_width=True, hide_index=True)
else:
    st.caption("No payment records yet.")
