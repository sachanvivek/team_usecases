import json
import logging
from llm_client import get_llm_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a DNS Operations Chat Assistant. You help users manage DNS records on an internal BIND9 DNS server (172.19.0.6) and Azure DNS through natural language.

You can help with:
1. ADD a DNS record (A, AAAA, CNAME, MX, TXT, NS, PTR, SRV)
2. MODIFY an existing DNS record
3. DELETE a DNS record
4. QUERY/CHECK current DNS records on the local server
5. LIST records in a zone
6. STATUS of pending Change Requests and workflows

The internal DNS server at 172.19.0.6 is a BIND9 server managing the internal zones.
Each change creates a ServiceNow Change Request. After CR approval, the system automatically:
  1. Pre-checks current DNS state on the target server
  2. Implements the change via DNS dynamic update (nsupdate) on the BIND server
  3. Post-checks the record directly on the server
  4. Closes the CR

IMPORTANT: For any DNS change operation (add/modify/delete), respond with ONLY valid JSON in this format:
{
  "action": "add|modify|delete",
  "record_name": "<hostname, e.g. 'web01' or 'mail' or '@' for zone apex>",
  "zone": "<zone, e.g. 'example.com' or 'internal.corp'>",
  "record_type": "<A|AAAA|CNAME|MX|TXT|NS|PTR|SRV>",
  "ttl": <TTL in seconds, default 3600>,
  "values": ["<value1>", "<value2>"],
  "old_values": ["<old value1>"],
  "reason": "<brief reason for the change>",
  "confirmation_message": "<human-readable summary of what will be done>"
}

For queries/status checks or general questions, respond with:
{
  "action": "query|status|info",
  "message": "<your response to the user>"
}

Rules:
- Always confirm the details before proceeding
- If information is missing, ask for it with action "info"
- For MX records, values should be in "priority exchange" format, e.g. "10 mail.example.com"
- For CNAME, only one value is allowed
- For PTR records, the name is the reverse IP octets (e.g. "50.1.0.10" for 10.0.1.50)
- For SRV records, values are "priority weight port target"
- Default TTL is 3600 unless specified
- If zone is not provided, ask for it - suggest the managed zones
- Be helpful and suggest corrections for common mistakes
- When a user asks to "check" or "query" a record, use action "query" """


class ChatAssistant:
    """LLM-powered chat assistant for DNS record operations."""

    def __init__(self):
        self._conversation_history: dict[str, list] = {}

    async def chat(self, session_id: str, user_message: str, context: dict = None) -> dict:
        """Process a chat message and return structured response."""
        llm = get_llm_client()

        # Build conversation context
        history = self._conversation_history.get(session_id, [])
        history.append({"role": "user", "content": user_message})

        # Add system context about current state
        context_str = ""
        if context:
            if context.get("managed_zones"):
                context_str += f"\nManaged DNS zones: {context['managed_zones']}"
            if context.get("local_dns_server"):
                context_str += f"\nLocal DNS server: {context['local_dns_server']}"
            if context.get("local_dns_zones"):
                context_str += f"\nLocal server zones: {json.dumps(context['local_dns_zones'])}"
            if context.get("active_workflows"):
                context_str += f"\nActive workflows: {json.dumps(context['active_workflows'], indent=2)}"
            if context.get("recent_changes"):
                context_str += f"\nRecent changes: {json.dumps(context['recent_changes'], indent=2)}"

        # Build the full prompt with conversation history
        conv_text = ""
        for msg in history[-10:]:  # Keep last 10 messages
            conv_text += f"\n{msg['role'].upper()}: {msg['content']}"

        full_prompt = f"""Current context:{context_str}

Conversation history:{conv_text}

Respond to the latest user message. Remember to output ONLY valid JSON."""

        raw_response = await llm.chat(SYSTEM_PROMPT, full_prompt)

        # Parse the response
        try:
            text = raw_response.strip()
            if text.startswith("```"):
                first_nl = text.index("\n")
                text = text[first_nl + 1:]
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3].rstrip()
            result = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            result = {"action": "info", "message": raw_response}

        # Store assistant response in history
        history.append({"role": "assistant", "content": json.dumps(result)})
        self._conversation_history[session_id] = history[-20:]

        return result

    def clear_session(self, session_id: str):
        self._conversation_history.pop(session_id, None)

    def get_history(self, session_id: str) -> list:
        return self._conversation_history.get(session_id, [])
