"""
AI Agent module – wraps Ollama (llama3.2) for certificate lifecycle intelligence.
Provides risk assessment, approval recommendations, deployment strategies,
renewal prioritization, and general certificate Q&A.
"""
import json
import requests
from utils.config import config


class AIAgent:
    def __init__(self):
        self.base_url = config.get('ollama', 'base_url', fallback='http://localhost:11434')
        self.model = config.get('ollama', 'model', fallback='llama3.2')
        self.timeout = config.getint('ollama', 'timeout', fallback=120)
        self.temperature = config.getfloat('ollama', 'temperature', fallback=0.7)

    # ────────────── low-level Ollama call ──────────────

    def _call_ollama(self, prompt: str, system_prompt: str = None) -> str:
        """Send a prompt to Ollama and return the response text."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
            },
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "No response from model.")
        except requests.ConnectionError:
            return ("⚠️ Cannot connect to Ollama. Please ensure Ollama is running "
                    f"at {self.base_url} with model '{self.model}' loaded.\n\n"
                    "Start it with: `ollama run llama3.2`")
        except requests.Timeout:
            return "⚠️ Ollama request timed out. Try again or increase timeout in config.ini."
        except Exception as e:
            return f"⚠️ AI Agent error: {str(e)}"

    def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # ────────────── Certificate-specific intelligence ──────────────

    SYSTEM_PROMPT = (
        "You are an expert Certificate Lifecycle Management AI agent. "
        "You help enterprises manage their SSL/TLS certificates from discovery "
        "to deployment. You provide actionable advice about certificate security, "
        "compliance, risk assessment, and best practices. "
        "Be concise, professional, and provide specific recommendations. "
        "Format responses with clear sections and bullet points when appropriate."
    )

    def assess_risk(self, certificate: dict) -> str:
        """Evaluate the risk level of a certificate."""
        prompt = f"""Analyze the following SSL/TLS certificate and provide a risk assessment.
Rate the overall risk as LOW, MEDIUM, HIGH, or CRITICAL.

Certificate Details:
- Common Name: {certificate.get('common_name', 'N/A')}
- Subject Alternative Names: {certificate.get('san', 'N/A')}
- Issuer: {certificate.get('issuer', 'N/A')}
- Not Before: {certificate.get('not_before', 'N/A')}
- Not After: {certificate.get('not_after', 'N/A')}
- Key Size: {certificate.get('key_size', 'N/A')} bits
- Algorithm: {certificate.get('algorithm', 'N/A')}
- CA Type: {certificate.get('ca_type', 'N/A')}
- Status: {certificate.get('status', 'N/A')}
- Environment: {certificate.get('environment', 'N/A')}
- Server: {certificate.get('server', 'N/A')}:{certificate.get('port', 443)}

Provide:
1. Risk Level (LOW/MEDIUM/HIGH/CRITICAL)
2. Key findings (3-5 bullet points)
3. Recommended actions
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def recommend_approval(self, certificate: dict) -> str:
        """Recommend whether to approve or reject a certificate request."""
        prompt = f"""Review this certificate request and recommend APPROVE or REJECT.

Certificate Request:
- Common Name: {certificate.get('common_name', 'N/A')}
- SANs: {certificate.get('san', 'N/A')}
- Key Size: {certificate.get('key_size', 'N/A')} bits
- Algorithm: {certificate.get('algorithm', 'N/A')}
- CA Type: {certificate.get('ca_type', 'N/A')} ({'Internal CA' if certificate.get('ca_type') == 'local' else f"External CA - {certificate.get('ca_provider', 'N/A')}"})
- Certificate Type: {certificate.get('cert_type', 'N/A')}
- Environment: {certificate.get('environment', 'N/A')}
- Requested By: {certificate.get('requestor', 'N/A')}

Provide:
1. Recommendation: APPROVE or REJECT
2. Confidence Score (0-100%)
3. Reasoning (3-5 bullet points)
4. Any conditions or suggestions
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def suggest_deployment(self, certificate: dict, target_platform: str) -> str:
        """Suggest deployment strategy for a certificate."""
        prompt = f"""Provide deployment instructions for this certificate.

Certificate: {certificate.get('common_name', 'N/A')}
Target Platform: {target_platform}
Environment: {certificate.get('environment', 'N/A')}
Server: {certificate.get('server', 'N/A')}

Provide:
1. Step-by-step deployment instructions for {target_platform}
2. Pre-deployment checklist
3. Post-deployment verification steps
4. Rollback plan
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def analyze_inventory(self, stats: dict) -> str:
        """Analyze the certificate inventory and provide insights."""
        prompt = f"""Analyze the following certificate inventory and provide insights.

Inventory Summary:
- Total Certificates: {stats.get('total', 0)}
- Active: {stats.get('active', 0)}
- Expired: {stats.get('expired', 0)}
- Expiring Soon (30 days): {stats.get('expiring_soon', 0)}
- Pending Approval: {stats.get('pending_approval', 0)}
- Pending Payment: {stats.get('pending_payment', 0)}
- Revoked: {stats.get('revoked', 0)}
- By CA Type: {stats.get('by_ca_type', {})}
- By Environment: {stats.get('by_environment', {})}

Provide:
1. Overall health assessment
2. Top 3 risks
3. Recommended immediate actions
4. Optimization suggestions
5. Compliance observations
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def recommend_ca_provider(self, requirements: dict) -> str:
        """Recommend the best CA provider based on requirements."""
        prompt = f"""Recommend the best Certificate Authority provider for these requirements.

Requirements:
- Domain: {requirements.get('common_name', 'N/A')}
- Certificate Type: {requirements.get('cert_type', 'DV')}
- Environment: {requirements.get('environment', 'production')}
- Budget: {requirements.get('budget', 'flexible')}
- Validation Level Needed: {requirements.get('cert_type', 'DV')}
- Wildcard: {'Yes' if requirements.get('wildcard') else 'No'}

Available Providers: DigiCert, Sectigo, GlobalSign, GoDaddy, Let's Encrypt

Provide:
1. Recommended provider and why
2. Estimated cost comparison
3. Pros and cons of top 2-3 options
4. Alternative for cost savings
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def prioritize_renewals(self, certificates: list) -> str:
        """Prioritize which certificates should be renewed first."""
        cert_summaries = []
        for c in certificates[:10]:
            cert_summaries.append(
                f"  - {c.get('common_name')} | Expires: {c.get('not_after','N/A')} | "
                f"Env: {c.get('environment','N/A')} | CA: {c.get('ca_type','N/A')}"
            )
        certs_text = "\n".join(cert_summaries)

        prompt = f"""Prioritize the following certificates for renewal.
Consider: environment criticality, expiry date, CA type, and business impact.

Certificates:
{certs_text}

Provide:
1. Prioritized renewal order with reasoning
2. Risk level for each
3. Estimated time to complete renewals
4. Recommendation for automation
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def check_compliance(self, certificate: dict) -> str:
        """Check certificate compliance against industry standards."""
        prompt = f"""Check this certificate against industry compliance standards.

Certificate:
- Common Name: {certificate.get('common_name', 'N/A')}
- Key Size: {certificate.get('key_size', 'N/A')} bits
- Algorithm: {certificate.get('algorithm', 'N/A')}
- CA Type: {certificate.get('ca_type', 'N/A')}
- Validity Period: {certificate.get('not_before', 'N/A')} to {certificate.get('not_after', 'N/A')}
- Certificate Type: {certificate.get('cert_type', 'N/A')}

Standards to check:
- PCI DSS
- NIST guidelines
- CA/Browser Forum baseline requirements
- Industry best practices

Provide:
1. Compliance status for each standard
2. Any violations found
3. Remediation recommendations
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def chat(self, user_message: str, context: str = None) -> str:
        """General certificate-related chat."""
        ctx_block = ""
        if context:
            ctx_block = f"\n\nContext:\n{context}\n"

        prompt = f"""{ctx_block}
User Question: {user_message}

Provide a helpful, accurate, and concise answer about certificate management.
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)

    def analyze_revocation_impact(self, certificate: dict) -> str:
        """Analyze the impact of revoking a certificate."""
        prompt = f"""Analyze the impact of revoking this certificate.

Certificate:
- Common Name: {certificate.get('common_name', 'N/A')}
- SANs: {certificate.get('san', 'N/A')}
- Environment: {certificate.get('environment', 'N/A')}
- Server: {certificate.get('server', 'N/A')}:{certificate.get('port', 443)}
- CA Type: {certificate.get('ca_type', 'N/A')}
- Status: {certificate.get('status', 'N/A')}

Provide:
1. Impact assessment (services affected)
2. Risk level of immediate revocation
3. Recommended preparation steps before revocation
4. Post-revocation actions needed
"""
        return self._call_ollama(prompt, self.SYSTEM_PROMPT)


# Singleton
ai_agent = AIAgent()
