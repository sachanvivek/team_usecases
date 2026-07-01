"""
Certificate operations – CSR generation, local CA cert signing,
external CA simulation, certificate parsing, and discovery helpers.
Uses the `cryptography` library for real PKI operations.
"""
import os
import ssl
import socket
import hashlib
import uuid
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.backends import default_backend

from utils.config import config
from utils.helpers import generate_serial_number


# ─────────────────────── Key Generation ───────────────────────

def generate_private_key(key_size: int = 2048, algorithm: str = 'RSA'):
    """Generate RSA or EC private key."""
    if algorithm.upper() == 'EC':
        return ec.generate_private_key(ec.SECP256R1(), default_backend())
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend(),
    )


def serialize_private_key(private_key) -> str:
    """Return PEM-encoded private key as string."""
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode('utf-8')


# ─────────────────────── CSR Generation ───────────────────────

def generate_csr(common_name: str, san_list: list = None,
                 org: str = None, country: str = None,
                 state: str = None, city: str = None,
                 key_size: int = 2048, algorithm: str = 'RSA'):
    """Generate a CSR and private key. Returns (csr_pem, key_pem, key_obj)."""
    key = generate_private_key(key_size, algorithm)

    name_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    if org:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    if country:
        name_attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country))
    if state:
        name_attrs.append(x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, state))
    if city:
        name_attrs.append(x509.NameAttribute(NameOID.LOCALITY_NAME, city))

    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name(name_attrs)
    )

    # Add SANs
    if san_list:
        sans = []
        for s in san_list:
            s = s.strip()
            if s:
                sans.append(x509.DNSName(s))
        if sans:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(sans), critical=False,
            )

    csr = builder.sign(key, hashes.SHA256(), default_backend())

    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode('utf-8')
    key_pem = serialize_private_key(key)

    return csr_pem, key_pem, key


# ─────────────────────── Local CA ───────────────────────

class LocalCA:
    """Self-signed internal Certificate Authority."""

    def __init__(self):
        self.ca_name = config.get('local_ca', 'ca_name', fallback='Enterprise Internal CA')
        self.ca_org = config.get('local_ca', 'ca_org', fallback='Enterprise Corp')
        self.ca_country = config.get('local_ca', 'ca_country', fallback='US')
        self.ca_state = config.get('local_ca', 'ca_state', fallback='California')
        self.ca_city = config.get('local_ca', 'ca_city', fallback='San Francisco')
        self.ca_validity = config.getint('local_ca', 'ca_validity_days', fallback=3650)
        self.cert_validity = config.getint('local_ca', 'cert_validity_days', fallback=365)
        self.key_size = config.getint('local_ca', 'key_size', fallback=2048)

        self._ca_key = None
        self._ca_cert = None
        self._ensure_ca()

    def _ensure_ca(self):
        """Create the root CA key & self-signed cert (in-memory for demo)."""
        self._ca_key = rsa.generate_private_key(65537, 4096, default_backend())
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, self.ca_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, self.ca_org),
            x509.NameAttribute(NameOID.COUNTRY_NAME, self.ca_country),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, self.ca_state),
            x509.NameAttribute(NameOID.LOCALITY_NAME, self.ca_city),
        ])
        self._ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self._ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=self.ca_validity))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(self._ca_key, hashes.SHA256(), default_backend())
        )

    def sign_csr(self, csr_pem: str, validity_days: int = None) -> dict:
        """Sign a CSR with the internal CA. Returns cert metadata dict."""
        validity_days = validity_days or self.cert_validity
        csr = x509.load_pem_x509_csr(csr_pem.encode('utf-8'), default_backend())

        # Extract SANs from CSR
        san_ext = None
        try:
            san_ext = csr.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            )
        except x509.ExtensionNotFound:
            pass

        builder = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=validity_days))
        )

        if san_ext:
            builder = builder.add_extension(san_ext.value, critical=False)

        cert = builder.sign(self._ca_key, hashes.SHA256(), default_backend())
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')

        cn = ""
        for attr in cert.subject:
            if attr.oid == NameOID.COMMON_NAME:
                cn = attr.value
                break

        return {
            'certificate_pem': cert_pem,
            'issuer': self.ca_name,
            'serial_number': format(cert.serial_number, 'X'),
            'thumbprint': cert.fingerprint(hashes.SHA256()).hex().upper(),
            'not_before': cert.not_valid_before_utc.isoformat() if hasattr(cert, 'not_valid_before_utc') else cert.not_valid_before.isoformat(),
            'not_after': cert.not_valid_after_utc.isoformat() if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after.isoformat(),
            'common_name': cn,
        }

    def get_ca_cert_pem(self) -> str:
        return self._ca_cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')


# ─────────────────────── External CA (Simulated) ───────────────────────

class ExternalCA:
    """Simulated external CA – mimics DigiCert / Sectigo / GlobalSign / etc."""

    PROVIDERS = {
        'DigiCert': {
            'issuer_tpl': 'DigiCert SHA2 {type} Server CA',
            'pricing': {'DV': 218, 'OV': 349, 'EV': 599, 'Wildcard': 798, 'Multi-Domain': 399},
        },
        'Sectigo': {
            'issuer_tpl': 'Sectigo RSA {type} Secure Server CA',
            'pricing': {'DV': 76, 'OV': 199, 'EV': 249, 'Wildcard': 399, 'Multi-Domain': 179},
        },
        'GlobalSign': {
            'issuer_tpl': 'GlobalSign RSA {type} SSL CA',
            'pricing': {'DV': 249, 'OV': 349, 'EV': 599, 'Wildcard': 849, 'Multi-Domain': 449},
        },
        'GoDaddy': {
            'issuer_tpl': 'GoDaddy Secure Certificate Authority - G2 {type}',
            'pricing': {'DV': 63, 'OV': 135, 'EV': 199, 'Wildcard': 295, 'Multi-Domain': 170},
        },
        'Lets Encrypt': {
            'issuer_tpl': "Let's Encrypt Authority X3",
            'pricing': {'DV': 0, 'OV': 0, 'EV': 0, 'Wildcard': 0, 'Multi-Domain': 0},
        },
    }

    @classmethod
    def get_providers(cls) -> list:
        return list(cls.PROVIDERS.keys())

    @classmethod
    def get_pricing(cls, provider: str) -> dict:
        return cls.PROVIDERS.get(provider, {}).get('pricing', {})

    @classmethod
    def get_price(cls, provider: str, cert_type: str, validity_years: int = 1) -> float:
        base = cls.PROVIDERS.get(provider, {}).get('pricing', {}).get(cert_type, 0)
        return round(base * validity_years, 2)

    @classmethod
    def issue_certificate(cls, csr_pem: str, provider: str,
                          cert_type: str, validity_days: int = 365) -> dict:
        """Simulate external CA issuing a certificate (uses local crypto for demo)."""
        csr = x509.load_pem_x509_csr(csr_pem.encode(), default_backend())

        # Generate a temporary issuer key for simulation
        issuer_key = rsa.generate_private_key(65537, 4096, default_backend())
        provider_info = cls.PROVIDERS.get(provider, cls.PROVIDERS['DigiCert'])
        issuer_cn = provider_info['issuer_tpl'].format(type=cert_type)

        issuer_name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, provider),
        ])

        san_ext = None
        try:
            san_ext = csr.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            )
        except x509.ExtensionNotFound:
            pass

        builder = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(issuer_name)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=validity_days))
        )

        if san_ext:
            builder = builder.add_extension(san_ext.value, critical=False)

        cert = builder.sign(issuer_key, hashes.SHA256(), default_backend())
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')

        cn = ""
        for attr in cert.subject:
            if attr.oid == NameOID.COMMON_NAME:
                cn = attr.value
                break

        return {
            'certificate_pem': cert_pem,
            'issuer': issuer_cn,
            'serial_number': format(cert.serial_number, 'X'),
            'thumbprint': cert.fingerprint(hashes.SHA256()).hex().upper(),
            'not_before': cert.not_valid_before_utc.isoformat() if hasattr(cert, 'not_valid_before_utc') else cert.not_valid_before.isoformat(),
            'not_after': cert.not_valid_after_utc.isoformat() if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after.isoformat(),
            'common_name': cn,
            'ca_provider': provider,
        }


# ─────────────────────── Certificate Parsing ───────────────────────

def parse_pem_certificate(pem_data: str) -> dict:
    """Parse a PEM certificate and return metadata."""
    try:
        cert = x509.load_pem_x509_certificate(pem_data.encode(), default_backend())
        cn = ""
        org = ""
        for attr in cert.subject:
            if attr.oid == NameOID.COMMON_NAME:
                cn = attr.value
            elif attr.oid == NameOID.ORGANIZATION_NAME:
                org = attr.value

        issuer_cn = ""
        for attr in cert.issuer:
            if attr.oid == NameOID.COMMON_NAME:
                issuer_cn = attr.value

        san_list = []
        try:
            san_ext = cert.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            )
            san_list = san_ext.value.get_values_for_type(x509.DNSName)
        except Exception:
            pass

        nb = cert.not_valid_before_utc if hasattr(cert, 'not_valid_before_utc') else cert.not_valid_before
        na = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after

        return {
            'common_name': cn,
            'organization': org,
            'issuer': issuer_cn,
            'serial_number': format(cert.serial_number, 'X'),
            'thumbprint': cert.fingerprint(hashes.SHA256()).hex().upper(),
            'not_before': nb.isoformat(),
            'not_after': na.isoformat(),
            'san': ','.join(san_list),
            'key_size': cert.public_key().key_size if hasattr(cert.public_key(), 'key_size') else 256,
        }
    except Exception as e:
        return {'error': str(e)}


# ─────────────────────── Network Discovery ───────────────────────

def scan_host_certificate(host: str, port: int = 443, timeout: int = 5) -> dict | None:
    """Connect to a host and retrieve its TLS certificate details."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)
                pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
                info = parse_pem_certificate(pem_cert)
                info['server'] = host
                info['port'] = port
                info['certificate_pem'] = pem_cert
                return info
    except Exception as e:
        return {'error': str(e), 'server': host, 'port': port}


def discover_certificates(targets: list, ports: list = None,
                          timeout: int = 5) -> list:
    """Scan multiple host:port combinations and return discovered certs."""
    if ports is None:
        ports_str = config.get('discovery', 'default_ports', fallback='443')
        ports = [int(p.strip()) for p in ports_str.split(',')]
    if timeout is None:
        timeout = config.getint('discovery', 'scan_timeout', fallback=5)

    results = []
    for target in targets:
        target = target.strip()
        if not target:
            continue
        for port in ports:
            info = scan_host_certificate(target, port, timeout)
            if info and 'error' not in info:
                results.append(info)
    return results


# Singleton local CA
local_ca = LocalCA()


# ─────────────────────── Internal CA (Real PKI via Demo-CA) ───────────────────────

class InternalCA:
    """
    Real Internal CA – signs CSRs using the Intermediate CA on Demo-CA (172.19.0.11)
    via Azure VM run-command.
    """

    CA_VM = 'Demo-CA'
    CA_BASE = '/home/linuxadmin/internal-ca/intermediate-ca'
    CA_CONF = f'{CA_BASE}/openssl.cnf'
    ISSUED_DIR = f'{CA_BASE}/certs/issued'
    INTER_CERT = f'{CA_BASE}/certs/intermediate.cert.pem'
    ROOT_CERT = '/home/linuxadmin/internal-ca/root-ca/certs/ca.cert.pem'

    def __init__(self):
        # Lazy import to avoid circular dependency
        pass

    def _run_remote(self, script: str, timeout: int = 120):
        """Run a script on Demo-CA via Azure VM run-command."""
        from core.deployment import _run_remote, _ensure_az_login
        _ensure_az_login()
        return _run_remote(self.CA_VM, script, timeout)

    def sign_csr(self, csr_pem: str, common_name: str, validity_days: int = 825) -> dict:
        """
        Sign a CSR using the real Intermediate CA on Demo-CA.
        Returns dict with cert details including fullchain.
        """
        import base64

        # Step 1: Upload CSR to CA server
        csr_b64 = base64.b64encode(csr_pem.encode()).decode()
        issued_dir = f'{self.ISSUED_DIR}/{common_name}'

        upload_script = (
            f"mkdir -p {issued_dir} && "
            f"echo '{csr_b64}' | base64 -d > {issued_dir}/{common_name}.csr.pem && "
            f"echo CSR_UPLOADED"
        )
        rc, out, err = self._run_remote(upload_script)
        if 'CSR_UPLOADED' not in out:
            raise Exception(f"Failed to upload CSR to CA server: {err or out}")

        # Step 2: Allow re-issuance for the same subject (unique_subject = no)
        self._run_remote(
            f"sed -i 's/unique_subject\\s*=\\s*yes/unique_subject = no/' "
            f"{self.CA_BASE}/index.txt.attr 2>/dev/null; "
            f"grep -q 'unique_subject' {self.CA_BASE}/index.txt.attr 2>/dev/null || "
            f"echo 'unique_subject = no' > {self.CA_BASE}/index.txt.attr"
        )

        # Step 3: Sign the CSR with openssl ca
        sign_script = (
            f"cd {self.CA_BASE} && "
            f"openssl ca -config {self.CA_CONF} "
            f"-extensions server_cert "
            f"-days {validity_days} "
            f"-notext -md sha256 "
            f"-in {issued_dir}/{common_name}.csr.pem "
            f"-out {issued_dir}/{common_name}.cert.pem "
            f"-batch 2>&1 && "
            f"echo CERT_SIGNED"
        )
        rc, out, err = self._run_remote(sign_script, timeout=60)
        if 'CERT_SIGNED' not in out:
            raise Exception(f"CA signing failed: {out} {err}")

        # Step 4: Fetch the signed certificate
        rc, cert_out, err = self._run_remote(
            f"cat {issued_dir}/{common_name}.cert.pem"
        )
        if '-----BEGIN CERTIFICATE-----' not in cert_out:
            raise Exception(f"Failed to fetch signed cert: {err or cert_out}")
        server_cert_pem = cert_out.strip()

        # Step 5: Fetch intermediate cert
        rc, inter_out, err = self._run_remote(f"cat {self.INTER_CERT}")
        if '-----BEGIN CERTIFICATE-----' not in inter_out:
            raise Exception(f"Failed to fetch intermediate cert: {err or inter_out}")
        intermediate_pem = inter_out.strip()

        # Step 6: Fetch root cert
        rc, root_out, err = self._run_remote(f"cat {self.ROOT_CERT}")
        if '-----BEGIN CERTIFICATE-----' not in root_out:
            raise Exception(f"Failed to fetch root cert: {err or root_out}")
        root_pem = root_out.strip()

        # Step 7: Build fullchain (server + intermediate + root)
        fullchain_pem = server_cert_pem + '\n' + intermediate_pem + '\n' + root_pem + '\n'

        # Step 8: Save fullchain on CA server
        fullchain_b64 = base64.b64encode(fullchain_pem.encode()).decode()
        self._run_remote(
            f"echo '{fullchain_b64}' | base64 -d > {issued_dir}/{common_name}.fullchain.pem"
        )

        # Step 8: Parse the signed certificate for metadata
        info = parse_pem_certificate(server_cert_pem)
        if 'error' in info:
            raise Exception(f"Failed to parse signed cert: {info['error']}")

        return {
            'certificate_pem': fullchain_pem,
            'server_cert_pem': server_cert_pem,
            'intermediate_pem': intermediate_pem,
            'root_pem': root_pem,
            'issuer': info.get('issuer', 'Internal Intermediate CA'),
            'serial_number': info.get('serial_number', ''),
            'thumbprint': info.get('thumbprint', ''),
            'not_before': info.get('not_before', ''),
            'not_after': info.get('not_after', ''),
            'common_name': info.get('common_name', common_name),
            'san': info.get('san', ''),
            'key_size': info.get('key_size', 2048),
        }

    def get_ca_info(self) -> dict:
        """Get information about the CA."""
        rc, out, err = self._run_remote(
            f"openssl x509 -in {self.INTER_CERT} -noout -subject -issuer -dates"
        )
        return {
            'name': 'Internal Intermediate CA',
            'type': 'Internal PKI',
            'info': out.strip() if rc == 0 else 'Unavailable',
        }

    def revoke_cert(self, common_name: str, reason: str = 'unspecified') -> dict:
        """
        Revoke a certificate on the real CA server using openssl ca -revoke,
        then regenerate the CRL.

        Args:
            common_name: The CN of the certificate to revoke.
            reason: One of: unspecified, keyCompromise, CACompromise,
                    affiliationChanged, superseded, cessationOfOperation.

        Returns:
            dict with 'success', 'message', and optional 'crl_pem'.
        """
        # Map human-readable reasons to OpenSSL reason codes
        reason_map = {
            'key compromise': 'keyCompromise',
            'ca compromise': 'CACompromise',
            'affiliation changed': 'affiliationChanged',
            'superseded': 'superseded',
            'cessation of operation': 'cessationOfOperation',
            'certificate hold': 'certificateHold',
            'privilege withdrawn': 'privilegeWithdrawn',
        }
        openssl_reason = reason_map.get(reason.lower(), 'unspecified')

        issued_dir = f'{self.ISSUED_DIR}/{common_name}'
        cert_path = f'{issued_dir}/{common_name}.cert.pem'
        crl_path = f'{self.CA_BASE}/crl/intermediate.crl.pem'

        # Step 1: Verify the certificate file exists on the CA server
        rc, out, err = self._run_remote(f"test -f {cert_path} && echo EXISTS")
        if 'EXISTS' not in out:
            return {
                'success': False,
                'message': f"Certificate file not found on CA server: {cert_path}",
            }

        # Step 2: Revoke the certificate with openssl ca
        revoke_script = (
            f"cd {self.CA_BASE} && "
            f"openssl ca -config {self.CA_CONF} "
            f"-revoke {cert_path} "
            f"-crl_reason {openssl_reason} "
            f"-batch 2>&1 && "
            f"echo CERT_REVOKED"
        )
        rc, out, err = self._run_remote(revoke_script, timeout=60)
        # "already revoked" is also acceptable
        if 'CERT_REVOKED' not in out and 'already revoked' not in out.lower():
            return {
                'success': False,
                'message': f"CA revocation failed: {out} {err}",
            }

        # Step 3: Regenerate the CRL
        crl_script = (
            f"cd {self.CA_BASE} && "
            f"mkdir -p {self.CA_BASE}/crl && "
            f"openssl ca -config {self.CA_CONF} "
            f"-gencrl -out {crl_path} 2>&1 && "
            f"echo CRL_GENERATED"
        )
        rc, crl_out, crl_err = self._run_remote(crl_script, timeout=60)
        crl_ok = 'CRL_GENERATED' in crl_out

        # Step 4: Fetch the updated CRL (optional, for reporting)
        crl_pem = None
        if crl_ok:
            rc, crl_content, _ = self._run_remote(f"cat {crl_path}")
            if '-----BEGIN X509 CRL-----' in crl_content:
                crl_pem = crl_content.strip()

        return {
            'success': True,
            'message': (
                f"Certificate '{common_name}' revoked on CA server "
                f"(reason: {openssl_reason}). "
                + ("CRL updated." if crl_ok else "Warning: CRL regeneration failed.")
            ),
            'crl_updated': crl_ok,
            'crl_pem': crl_pem,
        }


# Singleton internal CA
internal_ca = InternalCA()
