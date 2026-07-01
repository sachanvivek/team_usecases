"""
Helper utilities for the Certificate Lifecycle Manager
"""
from datetime import datetime, timedelta
import hashlib
import uuid


def days_until_expiry(not_after_str):
    """Calculate days until certificate expiry"""
    if not not_after_str:
        return None
    try:
        if isinstance(not_after_str, str):
            not_after = datetime.fromisoformat(not_after_str.replace('Z', '+00:00').replace('+00:00', ''))
        else:
            not_after = not_after_str
        delta = not_after - datetime.now()
        return delta.days
    except Exception:
        return None


def get_expiry_status(days_remaining):
    """Return status label and color based on days remaining"""
    if days_remaining is None:
        return "Unknown", "gray"
    elif days_remaining < 0:
        return "Expired", "red"
    elif days_remaining <= 7:
        return "Critical", "red"
    elif days_remaining <= 30:
        return "Warning", "orange"
    elif days_remaining <= 90:
        return "Attention", "yellow"
    else:
        return "Healthy", "green"


def generate_transaction_id():
    """Generate a unique transaction ID for payments"""
    return f"TXN-{uuid.uuid4().hex[:12].upper()}"


def generate_serial_number():
    """Generate a certificate serial number"""
    return uuid.uuid4().hex[:16].upper()


def format_date(date_str):
    """Format a date string for display"""
    if not date_str:
        return "N/A"
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str)
        else:
            dt = date_str
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(date_str)


def get_status_emoji(status):
    """Return emoji for certificate status"""
    status_emojis = {
        'discovered': '🔍',
        'requested': '📝',
        'pending_approval': '⏳',
        'approved': '✅',
        'rejected': '❌',
        'pending_payment': '💳',
        'paid': '💰',
        'issued': '📜',
        'deployed': '🚀',
        'active': '✅',
        'expiring_soon': '⚠️',
        'expired': '💀',
        'revoked': '🚫',
        'renewal_requested': '🔄',
    }
    return status_emojis.get(status, '❓')


def get_status_color(status):
    """Return color for certificate status"""
    status_colors = {
        'discovered': '#3498db',
        'requested': '#9b59b6',
        'pending_approval': '#f39c12',
        'approved': '#2ecc71',
        'rejected': '#e74c3c',
        'pending_payment': '#e67e22',
        'paid': '#27ae60',
        'issued': '#2ecc71',
        'deployed': '#1abc9c',
        'active': '#2ecc71',
        'expiring_soon': '#f39c12',
        'expired': '#e74c3c',
        'revoked': '#c0392b',
        'renewal_requested': '#3498db',
    }
    return status_colors.get(status, '#95a5a6')


WORKFLOW_STATES = [
    'discovered',
    'requested',
    'pending_approval',
    'approved',
    'rejected',
    'pending_payment',
    'paid',
    'issued',
    'deployed',
    'expired',
    'revoked',
    'renewal_requested',
]

VALID_TRANSITIONS = {
    'discovered': ['requested', 'revoked'],
    'requested': ['pending_approval'],
    'pending_approval': ['approved', 'rejected'],
    'approved': ['pending_payment', 'issued'],  # pending_payment for external CA
    'rejected': ['requested'],
    'pending_payment': ['paid', 'rejected'],
    'paid': ['issued'],
    'issued': ['deployed'],
    'deployed': ['revoked', 'renewal_requested', 'expired'],
    'expired': ['renewal_requested', 'revoked'],
    'revoked': [],
    'renewal_requested': ['pending_approval'],
}
