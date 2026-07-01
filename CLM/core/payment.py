"""
Payment processing module for external CA certificates.
Simulates credit card, purchase order, and wire transfer payments.
"""
import uuid
from datetime import datetime

from utils.config import config
from utils.helpers import generate_transaction_id
from core.certificate_ops import ExternalCA


# ─────────────────────── Pricing Catalog ───────────────────────

CERT_TYPE_DESCRIPTIONS = {
    'DV': 'Domain Validation – basic encryption, fastest issuance',
    'OV': 'Organization Validation – business identity verified',
    'EV': 'Extended Validation – highest trust, green bar',
    'Wildcard': 'Wildcard – secures unlimited subdomains',
    'Multi-Domain': 'Multi-Domain (SAN) – multiple domains on one cert',
}

PAYMENT_METHODS = []
if config.getboolean('payment', 'enable_credit_card', fallback=True):
    PAYMENT_METHODS.append('Credit Card')
if config.getboolean('payment', 'enable_purchase_order', fallback=True):
    PAYMENT_METHODS.append('Purchase Order')
if config.getboolean('payment', 'enable_wire_transfer', fallback=True):
    PAYMENT_METHODS.append('Wire Transfer')

TAX_RATE = config.getfloat('payment', 'tax_rate', fallback=0.08)
CURRENCY = config.get('payment', 'currency', fallback='USD')


def get_pricing_table() -> list[dict]:
    """Return full pricing table across all providers and cert types."""
    rows = []
    for provider in ExternalCA.get_providers():
        prices = ExternalCA.get_pricing(provider)
        for ctype, price in prices.items():
            rows.append({
                'Provider': provider,
                'Type': ctype,
                'Description': CERT_TYPE_DESCRIPTIONS.get(ctype, ''),
                '1 Year': f"${price:,.2f}",
                '2 Years': f"${price * 2:,.2f}",
                '3 Years': f"${price * 3:,.2f}",
                'price_raw': price,
            })
    return rows


def calculate_total(provider: str, cert_type: str,
                    validity_years: int = 1) -> dict:
    """Calculate subtotal, tax, and total for a certificate purchase."""
    subtotal = ExternalCA.get_price(provider, cert_type, validity_years)
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2)
    return {
        'subtotal': subtotal,
        'tax': tax,
        'tax_rate': TAX_RATE,
        'total': total,
        'currency': CURRENCY,
    }


def process_payment(payment_method: str, amount: float,
                    card_number: str = None, card_expiry: str = None,
                    card_cvv: str = None, po_number: str = None,
                    billing_email: str = None) -> dict:
    """Simulate payment processing. Returns transaction result."""
    txn_id = generate_transaction_id()

    # Simulate processing delay / validation
    result = {
        'success': True,
        'transaction_id': txn_id,
        'amount': amount,
        'currency': CURRENCY,
        'payment_method': payment_method,
        'timestamp': datetime.now().isoformat(),
        'message': '',
    }

    if payment_method == 'Credit Card':
        if not card_number or len(card_number.replace(' ', '')) < 13:
            result['success'] = False
            result['message'] = 'Invalid card number.'
            return result
        result['card_last_four'] = card_number.replace(' ', '')[-4:]
        result['message'] = f'Payment of ${amount:,.2f} processed successfully via Credit Card ending in {result["card_last_four"]}.'

    elif payment_method == 'Purchase Order':
        if not po_number:
            result['success'] = False
            result['message'] = 'Purchase Order number is required.'
            return result
        result['po_number'] = po_number
        result['message'] = f'Purchase Order {po_number} for ${amount:,.2f} submitted. Payment pending approval.'
        result['status'] = 'pending_po_approval'

    elif payment_method == 'Wire Transfer':
        result['wire_reference'] = f"WIRE-{uuid.uuid4().hex[:8].upper()}"
        result['message'] = (
            f'Wire transfer of ${amount:,.2f} initiated. '
            f'Reference: {result["wire_reference"]}. '
            'Funds typically clear within 2-3 business days.'
        )
        result['status'] = 'pending_wire'

    return result
