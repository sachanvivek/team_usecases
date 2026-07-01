"""
Database module - SQLite operations for Certificate Lifecycle Manager
Handles all CRUD for certificates, audit logs, payments, and workflow transitions.
"""
import sqlite3
import os
import uuid
from datetime import datetime, timedelta

from utils.config import config


class Database:
    def __init__(self):
        db_rel_path = config.get('database', 'path', fallback='data/certificates.db')
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(base_dir, db_rel_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executescript('''
            CREATE TABLE IF NOT EXISTS certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                common_name TEXT NOT NULL,
                san TEXT,
                issuer TEXT,
                serial_number TEXT,
                thumbprint TEXT,
                not_before DATETIME,
                not_after DATETIME,
                key_size INTEGER DEFAULT 2048,
                algorithm TEXT DEFAULT 'RSA',
                ca_type TEXT DEFAULT 'local',
                ca_provider TEXT,
                cert_type TEXT DEFAULT 'DV',
                status TEXT DEFAULT 'discovered',
                environment TEXT DEFAULT 'production',
                server TEXT,
                port INTEGER DEFAULT 443,
                certificate_pem TEXT,
                private_key_pem TEXT,
                csr_pem TEXT,
                requestor TEXT DEFAULT 'admin',
                approver TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                certificate_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                performed_by TEXT DEFAULT 'system',
                ai_recommendation TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (certificate_id) REFERENCES certificates(id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                certificate_id INTEGER,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                payment_method TEXT,
                transaction_id TEXT,
                status TEXT DEFAULT 'pending',
                ca_provider TEXT,
                cert_type TEXT,
                validity_years INTEGER DEFAULT 1,
                card_last_four TEXT,
                billing_email TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (certificate_id) REFERENCES certificates(id)
            );

            CREATE TABLE IF NOT EXISTS workflow_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                certificate_id INTEGER,
                from_state TEXT,
                to_state TEXT,
                triggered_by TEXT DEFAULT 'user',
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (certificate_id) REFERENCES certificates(id)
            );
        ''')
        conn.commit()
        conn.close()

    # ──────────────────────── Certificate CRUD ────────────────────────

    def certificate_exists(self, common_name: str, server: str = None, port: int = None, serial_number: str = None) -> bool:
        """Check if a certificate already exists in the inventory."""
        conn = self.get_connection()
        cursor = conn.cursor()
        if serial_number:
            cursor.execute(
                'SELECT COUNT(*) as n FROM certificates WHERE serial_number = ? AND serial_number != ""',
                (serial_number,))
            if cursor.fetchone()['n'] > 0:
                conn.close()
                return True
        if server and port:
            cursor.execute(
                'SELECT COUNT(*) as n FROM certificates WHERE common_name = ? AND server = ? AND port = ?',
                (common_name, server, port))
        else:
            cursor.execute(
                'SELECT COUNT(*) as n FROM certificates WHERE common_name = ?',
                (common_name,))
        exists = cursor.fetchone()['n'] > 0
        conn.close()
        return exists

    def add_certificate(self, cert_data: dict) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO certificates (
                common_name, san, issuer, serial_number, thumbprint,
                not_before, not_after, key_size, algorithm,
                ca_type, ca_provider, cert_type,
                status, environment, server, port,
                certificate_pem, private_key_pem, csr_pem,
                requestor, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            cert_data.get('common_name'),
            cert_data.get('san'),
            cert_data.get('issuer'),
            cert_data.get('serial_number'),
            cert_data.get('thumbprint'),
            cert_data.get('not_before'),
            cert_data.get('not_after'),
            cert_data.get('key_size', 2048),
            cert_data.get('algorithm', 'RSA'),
            cert_data.get('ca_type', 'local'),
            cert_data.get('ca_provider'),
            cert_data.get('cert_type', 'DV'),
            cert_data.get('status', 'discovered'),
            cert_data.get('environment', 'production'),
            cert_data.get('server'),
            cert_data.get('port', 443),
            cert_data.get('certificate_pem'),
            cert_data.get('private_key_pem'),
            cert_data.get('csr_pem'),
            cert_data.get('requestor', 'admin'),
            cert_data.get('notes'),
        ))
        cert_id = cursor.lastrowid
        conn.commit()
        conn.close()
        self.add_audit_log(cert_id, 'created',
                           f"Certificate record created for {cert_data.get('common_name')}")
        return cert_id

    def get_certificate(self, cert_id: int) -> dict | None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM certificates WHERE id = ?', (cert_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_certificates(self, status=None, ca_type=None, environment=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        query = 'SELECT * FROM certificates WHERE 1=1'
        params = []
        if status:
            if isinstance(status, list):
                placeholders = ','.join('?' * len(status))
                query += f' AND status IN ({placeholders})'
                params.extend(status)
            else:
                query += ' AND status = ?'
                params.append(status)
        if ca_type:
            query += ' AND ca_type = ?'
            params.append(ca_type)
        if environment:
            query += ' AND environment = ?'
            params.append(environment)
        query += ' ORDER BY created_at DESC'
        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()
        return [dict(r) for r in results]

    def update_certificate(self, cert_id: int, updates: dict):
        conn = self.get_connection()
        cursor = conn.cursor()
        set_clause = ', '.join(f'{k} = ?' for k in updates.keys())
        values = list(updates.values()) + [cert_id]
        cursor.execute(
            f'UPDATE certificates SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            values,
        )
        conn.commit()
        conn.close()

    def update_certificate_status(self, cert_id, new_status, triggered_by='user', notes=''):
        cert = self.get_certificate(cert_id)
        old_status = cert['status'] if cert else 'unknown'
        self.update_certificate(cert_id, {'status': new_status})
        self.add_workflow_transition(cert_id, old_status, new_status, triggered_by, notes)
        self.add_audit_log(
            cert_id, 'status_change',
            f"Status: {old_status} → {new_status}. {notes}",
            performed_by=triggered_by,
        )

    def delete_certificate(self, cert_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM certificates WHERE id = ?', (cert_id,))
        conn.commit()
        conn.close()

    # ──────────────────────── Audit Log ────────────────────────

    def add_audit_log(self, cert_id, action, details,
                      performed_by='system', ai_recommendation=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO audit_log
                (certificate_id, action, details, performed_by, ai_recommendation)
            VALUES (?,?,?,?,?)
        ''', (cert_id, action, details, performed_by, ai_recommendation))
        conn.commit()
        conn.close()

    def get_audit_log(self, cert_id=None, limit=100):
        conn = self.get_connection()
        cursor = conn.cursor()
        if cert_id:
            cursor.execute(
                'SELECT * FROM audit_log WHERE certificate_id = ? ORDER BY created_at DESC LIMIT ?',
                (cert_id, limit))
        else:
            cursor.execute(
                'SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?', (limit,))
        results = cursor.fetchall()
        conn.close()
        return [dict(r) for r in results]

    # ──────────────────────── Payments ────────────────────────

    def add_payment(self, payment_data: dict) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO payments (
                certificate_id, amount, currency, payment_method,
                transaction_id, status, ca_provider, cert_type,
                validity_years, card_last_four, billing_email
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            payment_data.get('certificate_id'),
            payment_data.get('amount'),
            payment_data.get('currency', 'USD'),
            payment_data.get('payment_method'),
            payment_data.get('transaction_id'),
            payment_data.get('status', 'pending'),
            payment_data.get('ca_provider'),
            payment_data.get('cert_type'),
            payment_data.get('validity_years', 1),
            payment_data.get('card_last_four'),
            payment_data.get('billing_email'),
        ))
        pid = cursor.lastrowid
        conn.commit()
        conn.close()
        return pid

    def get_payments(self, cert_id=None, status=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        query = 'SELECT * FROM payments WHERE 1=1'
        params = []
        if cert_id:
            query += ' AND certificate_id = ?'
            params.append(cert_id)
        if status:
            query += ' AND status = ?'
            params.append(status)
        query += ' ORDER BY created_at DESC'
        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()
        return [dict(r) for r in results]

    def update_payment_status(self, payment_id, status, transaction_id=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        if transaction_id:
            cursor.execute(
                'UPDATE payments SET status=?, transaction_id=? WHERE id=?',
                (status, transaction_id, payment_id))
        else:
            cursor.execute(
                'UPDATE payments SET status=? WHERE id=?', (status, payment_id))
        conn.commit()
        conn.close()

    # ──────────────────────── Workflow Transitions ────────────────────────

    def add_workflow_transition(self, cert_id, from_state, to_state,
                                triggered_by='user', notes=''):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO workflow_transitions
                (certificate_id, from_state, to_state, triggered_by, notes)
            VALUES (?,?,?,?,?)
        ''', (cert_id, from_state, to_state, triggered_by, notes))
        conn.commit()
        conn.close()

    def get_workflow_history(self, cert_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM workflow_transitions WHERE certificate_id = ? ORDER BY created_at',
            (cert_id,))
        results = cursor.fetchall()
        conn.close()
        return [dict(r) for r in results]

    # ──────────────────────── Statistics ────────────────────────

    def get_statistics(self) -> dict:
        conn = self.get_connection()
        c = conn.cursor()
        stats = {}

        c.execute('SELECT COUNT(*) as n FROM certificates')
        stats['total'] = c.fetchone()['n']

        c.execute("SELECT COUNT(*) as n FROM certificates WHERE status IN ('deployed','active','issued')")
        stats['active'] = c.fetchone()['n']

        c.execute("SELECT COUNT(*) as n FROM certificates WHERE status='expired' OR not_after < datetime('now')")
        stats['expired'] = c.fetchone()['n']

        c.execute("""SELECT COUNT(*) as n FROM certificates
                     WHERE not_after BETWEEN datetime('now') AND datetime('now','+30 days')
                       AND status NOT IN ('expired','revoked')""")
        stats['expiring_soon'] = c.fetchone()['n']

        c.execute("SELECT COUNT(*) as n FROM certificates WHERE status IN ('pending_approval','requested')")
        stats['pending_approval'] = c.fetchone()['n']

        c.execute("SELECT COUNT(*) as n FROM certificates WHERE status='pending_payment'")
        stats['pending_payment'] = c.fetchone()['n']

        c.execute("SELECT COUNT(*) as n FROM certificates WHERE status='revoked'")
        stats['revoked'] = c.fetchone()['n']

        c.execute("SELECT ca_type, COUNT(*) as cnt FROM certificates GROUP BY ca_type")
        stats['by_ca_type'] = {r['ca_type']: r['cnt'] for r in c.fetchall()}

        c.execute("SELECT status, COUNT(*) as cnt FROM certificates GROUP BY status")
        stats['by_status'] = {r['status']: r['cnt'] for r in c.fetchall()}

        c.execute("SELECT environment, COUNT(*) as cnt FROM certificates GROUP BY environment")
        stats['by_environment'] = {r['environment']: r['cnt'] for r in c.fetchall()}

        # Total payment revenue
        c.execute("SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE status='completed'")
        stats['total_payment'] = c.fetchone()['total']

        conn.close()
        return stats

    # ──────────────────────── Seed Demo Data ────────────────────────

    def seed_demo_data(self):
        """Populate the database with realistic demo certificates."""
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) as n FROM certificates')
        if c.fetchone()['n'] > 0:
            conn.close()
            return False
        conn.close()

        now = datetime.now()
        demo_certs = [
            dict(common_name='www.enterprise.com',
                 san='enterprise.com,www.enterprise.com',
                 issuer='Enterprise Internal CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=200)).isoformat(),
                 not_after=(now + timedelta(days=165)).isoformat(),
                 ca_type='local', status='deployed',
                 environment='production', server='10.0.1.100', port=443,
                 key_size=2048, algorithm='RSA'),

            dict(common_name='api.enterprise.com',
                 san='api.enterprise.com,api-v2.enterprise.com',
                 issuer='DigiCert SHA2 Extended Validation Server CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=300)).isoformat(),
                 not_after=(now + timedelta(days=65)).isoformat(),
                 ca_type='external', ca_provider='DigiCert', cert_type='EV',
                 status='deployed', environment='production',
                 server='10.0.1.101', port=443, key_size=4096, algorithm='RSA'),

            dict(common_name='mail.enterprise.com',
                 san='mail.enterprise.com,smtp.enterprise.com',
                 issuer='Enterprise Internal CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=350)).isoformat(),
                 not_after=(now + timedelta(days=15)).isoformat(),
                 ca_type='local', status='deployed',
                 environment='production', server='10.0.1.102', port=993,
                 key_size=2048, algorithm='RSA'),

            dict(common_name='dev.enterprise.com',
                 san='dev.enterprise.com',
                 issuer='Enterprise Internal CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=100)).isoformat(),
                 not_after=(now + timedelta(days=265)).isoformat(),
                 ca_type='local', status='deployed',
                 environment='development', server='10.0.2.50', port=443,
                 key_size=2048, algorithm='RSA'),

            dict(common_name='staging.enterprise.com',
                 san='staging.enterprise.com',
                 issuer='Sectigo RSA Domain Validation Secure Server CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=150)).isoformat(),
                 not_after=(now + timedelta(days=5)).isoformat(),
                 ca_type='external', ca_provider='Sectigo', cert_type='DV',
                 status='deployed', environment='staging',
                 server='10.0.3.100', port=443, key_size=2048, algorithm='RSA'),

            dict(common_name='*.enterprise.com',
                 san='*.enterprise.com,enterprise.com',
                 issuer='GlobalSign RSA OV SSL CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=400)).isoformat(),
                 not_after=(now - timedelta(days=35)).isoformat(),
                 ca_type='external', ca_provider='GlobalSign', cert_type='Wildcard',
                 status='expired', environment='production',
                 server='10.0.1.200', port=443, key_size=4096, algorithm='RSA'),

            dict(common_name='portal.enterprise.com',
                 san='portal.enterprise.com',
                 issuer='', serial_number='',
                 ca_type='external', ca_provider='DigiCert', cert_type='OV',
                 status='pending_approval', environment='production',
                 server='10.0.1.150', port=443, key_size=2048, algorithm='RSA',
                 requestor='john.doe'),

            dict(common_name='payments.enterprise.com',
                 san='payments.enterprise.com,checkout.enterprise.com',
                 issuer='', serial_number='',
                 ca_type='external', ca_provider='DigiCert', cert_type='EV',
                 status='pending_payment', environment='production',
                 server='10.0.1.160', port=443, key_size=4096, algorithm='RSA',
                 requestor='jane.smith'),

            dict(common_name='intranet.enterprise.com',
                 san='intranet.enterprise.com',
                 issuer='Enterprise Internal CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=60)).isoformat(),
                 not_after=(now + timedelta(days=305)).isoformat(),
                 ca_type='local', status='deployed',
                 environment='production', server='10.0.1.50', port=443,
                 key_size=2048, algorithm='RSA'),

            dict(common_name='vpn.enterprise.com',
                 san='vpn.enterprise.com',
                 issuer='Enterprise Internal CA',
                 serial_number=uuid.uuid4().hex[:16].upper(),
                 not_before=(now - timedelta(days=330)).isoformat(),
                 not_after=(now + timedelta(days=35)).isoformat(),
                 ca_type='local', status='deployed',
                 environment='production', server='10.0.1.10', port=443,
                 key_size=2048, algorithm='RSA'),
        ]

        for cert in demo_certs:
            self.add_certificate(cert)

        # Add some demo audit entries and a demo payment
        self.add_audit_log(2, 'payment_completed',
                           'Payment of $299.00 processed for EV certificate',
                           performed_by='system')
        self.add_payment(dict(
            certificate_id=8, amount=349.00, currency='USD',
            payment_method='Credit Card', status='pending',
            ca_provider='DigiCert', cert_type='EV', validity_years=1,
        ))

        return True

    # ──────────────────────── Reset & Re-Seed ────────────────────────

    def reset_and_seed(self):
        """Wipe ALL data from every table and re-seed comprehensive demo data
        covering every workflow stage so the demo looks realistic."""
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('DELETE FROM workflow_transitions')
        c.execute('DELETE FROM audit_log')
        c.execute('DELETE FROM payments')
        c.execute('DELETE FROM certificates')
        # Reset auto-increment counters
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('certificates','audit_log','payments','workflow_transitions')")
        conn.commit()
        conn.close()

        now = datetime.now()

        # ── 1. Deployed (healthy) ──
        self.add_certificate(dict(
            common_name='www.enterprise.com',
            san='enterprise.com,www.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=200)).isoformat(),
            not_after=(now + timedelta(days=165)).isoformat(),
            ca_type='local', status='deployed',
            environment='production', server='10.0.1.100', port=443,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='intranet.enterprise.com',
            san='intranet.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=60)).isoformat(),
            not_after=(now + timedelta(days=305)).isoformat(),
            ca_type='local', status='deployed',
            environment='production', server='10.0.1.50', port=443,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='dev.enterprise.com',
            san='dev.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=100)).isoformat(),
            not_after=(now + timedelta(days=265)).isoformat(),
            ca_type='local', status='deployed',
            environment='development', server='10.0.2.50', port=443,
            key_size=2048, algorithm='RSA'))

        # ── 2. Deployed (external CA – healthy) ──
        self.add_certificate(dict(
            common_name='api.enterprise.com',
            san='api.enterprise.com,api-v2.enterprise.com',
            issuer='DigiCert SHA2 Extended Validation Server CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=300)).isoformat(),
            not_after=(now + timedelta(days=65)).isoformat(),
            ca_type='external', ca_provider='DigiCert', cert_type='EV',
            status='deployed', environment='production',
            server='10.0.1.101', port=443, key_size=4096, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='shop.enterprise.com',
            san='shop.enterprise.com,cart.enterprise.com',
            issuer='Sectigo RSA OV Secure Server CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=120)).isoformat(),
            not_after=(now + timedelta(days=245)).isoformat(),
            ca_type='external', ca_provider='Sectigo', cert_type='OV',
            status='deployed', environment='production',
            server='10.0.1.170', port=443, key_size=2048, algorithm='RSA'))

        # ── 3. Deployed (warning – expiring <30 days) ──
        self.add_certificate(dict(
            common_name='mail.enterprise.com',
            san='mail.enterprise.com,smtp.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=350)).isoformat(),
            not_after=(now + timedelta(days=15)).isoformat(),
            ca_type='local', status='deployed',
            environment='production', server='10.0.1.102', port=993,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='vpn.enterprise.com',
            san='vpn.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=330)).isoformat(),
            not_after=(now + timedelta(days=35)).isoformat(),
            ca_type='local', status='deployed',
            environment='production', server='10.0.1.10', port=443,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='staging.enterprise.com',
            san='staging.enterprise.com',
            issuer='Sectigo RSA Domain Validation Secure Server CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=150)).isoformat(),
            not_after=(now + timedelta(days=5)).isoformat(),
            ca_type='external', ca_provider='Sectigo', cert_type='DV',
            status='deployed', environment='staging',
            server='10.0.3.100', port=443, key_size=2048, algorithm='RSA'))

        # ── 4. Expired ──
        self.add_certificate(dict(
            common_name='*.enterprise.com',
            san='*.enterprise.com,enterprise.com',
            issuer='GlobalSign RSA OV SSL CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=400)).isoformat(),
            not_after=(now - timedelta(days=35)).isoformat(),
            ca_type='external', ca_provider='GlobalSign', cert_type='Wildcard',
            status='expired', environment='production',
            server='10.0.1.200', port=443, key_size=4096, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='legacy.enterprise.com',
            san='legacy.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=500)).isoformat(),
            not_after=(now - timedelta(days=135)).isoformat(),
            ca_type='local', status='expired',
            environment='production', server='10.0.1.201', port=443,
            key_size=2048, algorithm='RSA'))

        # ── 5. Discovered (not yet managed) ──
        self.add_certificate(dict(
            common_name='wiki.enterprise.com',
            san='wiki.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=50)).isoformat(),
            not_after=(now + timedelta(days=315)).isoformat(),
            ca_type='local', status='discovered',
            environment='production', server='10.0.1.180', port=443,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='monitoring.enterprise.com',
            san='monitoring.enterprise.com,grafana.enterprise.com',
            issuer="Let's Encrypt Authority X3",
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=80)).isoformat(),
            not_after=(now + timedelta(days=10)).isoformat(),
            ca_type='external', ca_provider='Lets Encrypt', cert_type='DV',
            status='discovered', environment='production',
            server='10.0.1.190', port=443, key_size=2048, algorithm='RSA'))

        # ── 6. Requested ──
        self.add_certificate(dict(
            common_name='newapp.enterprise.com',
            san='newapp.enterprise.com',
            ca_type='local', status='requested',
            environment='development', server='10.0.2.60', port=443,
            key_size=2048, algorithm='RSA', requestor='dev.team'))

        # ── 7. Pending Approval ──
        self.add_certificate(dict(
            common_name='portal.enterprise.com',
            san='portal.enterprise.com',
            ca_type='external', ca_provider='DigiCert', cert_type='OV',
            status='pending_approval', environment='production',
            server='10.0.1.150', port=443, key_size=2048, algorithm='RSA',
            requestor='john.doe'))

        self.add_certificate(dict(
            common_name='b2b.enterprise.com',
            san='b2b.enterprise.com,partners.enterprise.com',
            ca_type='external', ca_provider='GlobalSign', cert_type='EV',
            status='pending_approval', environment='production',
            server='10.0.1.155', port=443, key_size=4096, algorithm='RSA',
            requestor='biz.ops'))

        # ── 8. Approved (local, ready for issuance) ──
        self.add_certificate(dict(
            common_name='testing.enterprise.com',
            san='testing.enterprise.com',
            ca_type='local', status='approved',
            environment='staging', server='10.0.3.110', port=443,
            key_size=2048, algorithm='RSA', requestor='qa.team'))

        # ── 9. Pending Payment (external, approved but not paid) ──
        self.add_certificate(dict(
            common_name='payments.enterprise.com',
            san='payments.enterprise.com,checkout.enterprise.com',
            ca_type='external', ca_provider='DigiCert', cert_type='EV',
            status='pending_payment', environment='production',
            server='10.0.1.160', port=443, key_size=4096, algorithm='RSA',
            requestor='jane.smith'))

        self.add_certificate(dict(
            common_name='cdn.enterprise.com',
            san='cdn.enterprise.com,static.enterprise.com',
            ca_type='external', ca_provider='Sectigo', cert_type='Wildcard',
            status='pending_payment', environment='production',
            server='10.0.1.165', port=443, key_size=2048, algorithm='RSA',
            requestor='infra.team'))

        # ── 10. Paid (external, ready for issuance) ──
        self.add_certificate(dict(
            common_name='crm.enterprise.com',
            san='crm.enterprise.com',
            ca_type='external', ca_provider='DigiCert', cert_type='OV',
            status='paid', environment='production',
            server='10.0.1.175', port=443, key_size=2048, algorithm='RSA',
            requestor='sales.ops'))

        # ── 11. Issued (ready for deployment) ──
        self.add_certificate(dict(
            common_name='reports.enterprise.com',
            san='reports.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=now.isoformat(),
            not_after=(now + timedelta(days=365)).isoformat(),
            ca_type='local', status='issued',
            environment='production', server='10.0.1.185', port=443,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='hr.enterprise.com',
            san='hr.enterprise.com',
            issuer='GoDaddy Secure Certificate Authority - G2 OV',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=now.isoformat(),
            not_after=(now + timedelta(days=365)).isoformat(),
            ca_type='external', ca_provider='GoDaddy', cert_type='OV',
            status='issued', environment='production',
            server='10.0.1.186', port=443, key_size=2048, algorithm='RSA'))

        # ── 12. Revoked ──
        self.add_certificate(dict(
            common_name='old-api.enterprise.com',
            san='old-api.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=300)).isoformat(),
            not_after=(now + timedelta(days=65)).isoformat(),
            ca_type='local', status='revoked',
            environment='production', server='10.0.1.90', port=443,
            key_size=2048, algorithm='RSA'))

        self.add_certificate(dict(
            common_name='compromised.enterprise.com',
            san='compromised.enterprise.com',
            issuer='DigiCert SHA2 Secure Server CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=200)).isoformat(),
            not_after=(now + timedelta(days=165)).isoformat(),
            ca_type='external', ca_provider='DigiCert', cert_type='DV',
            status='revoked', environment='production',
            server='10.0.1.91', port=443, key_size=2048, algorithm='RSA'))

        # ── 13. Renewal Requested ──
        self.add_certificate(dict(
            common_name='auth.enterprise.com',
            san='auth.enterprise.com,sso.enterprise.com',
            issuer='Enterprise Internal CA',
            serial_number=uuid.uuid4().hex[:16].upper(),
            not_before=(now - timedelta(days=360)).isoformat(),
            not_after=(now + timedelta(days=5)).isoformat(),
            ca_type='local', status='renewal_requested',
            environment='production', server='10.0.1.120', port=443,
            key_size=2048, algorithm='RSA'))

        # ── Demo payments ──
        # Completed payment for crm.enterprise.com (cert #20)
        self.add_payment(dict(
            certificate_id=20, amount=377.16, currency='USD',
            payment_method='Credit Card', transaction_id='TXN-A1B2C3D4E5F6',
            status='completed', ca_provider='DigiCert', cert_type='OV',
            validity_years=1, card_last_four='4242',
            billing_email='billing@enterprise.com'))

        # Pending payment for payments.enterprise.com (cert #18)
        self.add_payment(dict(
            certificate_id=18, amount=646.92, currency='USD',
            payment_method='Credit Card', status='pending',
            ca_provider='DigiCert', cert_type='EV', validity_years=1))

        # Pending payment for cdn.enterprise.com (cert #19)
        self.add_payment(dict(
            certificate_id=19, amount=430.92, currency='USD',
            payment_method='Purchase Order', status='pending',
            ca_provider='Sectigo', cert_type='Wildcard', validity_years=1))

        # ── Demo audit entries ──
        self.add_audit_log(4, 'payment_completed',
                           'Payment of $377.16 processed for EV certificate via Credit Card',
                           performed_by='payment_system')
        self.add_audit_log(23, 'revoked',
                           'Certificate revoked. Reason: Key Compromise.',
                           performed_by='security.team')
        self.add_audit_log(24, 'revoked',
                           'Certificate revoked. Reason: Superseded by new certificate.',
                           performed_by='admin')
        self.add_audit_log(25, 'renewal_requested',
                           'Renewal requested – certificate expiring in 5 days.',
                           performed_by='monitoring_system')

        # ── Demo workflow transitions for realistic history ──
        self.add_workflow_transition(14, 'requested', 'pending_approval', 'john.doe', 'Submitted for approval')
        self.add_workflow_transition(18, 'requested', 'pending_approval', 'jane.smith', 'Submitted for approval')
        self.add_workflow_transition(18, 'pending_approval', 'approved', 'admin', 'Approved')
        self.add_workflow_transition(18, 'approved', 'pending_payment', 'admin', 'Routed to payment')
        self.add_workflow_transition(20, 'requested', 'pending_approval', 'sales.ops', 'Submitted')
        self.add_workflow_transition(20, 'pending_approval', 'approved', 'admin', 'Approved')
        self.add_workflow_transition(20, 'approved', 'pending_payment', 'admin', 'Routed to payment')
        self.add_workflow_transition(20, 'pending_payment', 'paid', 'payment_system', 'Payment completed')
        self.add_workflow_transition(23, 'deployed', 'revoked', 'security.team', 'Key compromise detected')
        self.add_workflow_transition(25, 'deployed', 'renewal_requested', 'monitoring_system', 'Auto-renewal triggered')

        return True


# Singleton instance
db = Database()
