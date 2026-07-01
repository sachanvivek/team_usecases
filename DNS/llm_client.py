import asyncio
import httpx
import json
import logging
from config_loader import get_config

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_BASE_DELAY = 3  # seconds

# Semaphore to limit concurrent Ollama requests (small servers can only handle 1-2)
_ollama_semaphore = asyncio.Semaphore(1)


class LLMClient:
    def __init__(self):
        cfg = get_config()
        self.provider = cfg.get("llm", "provider", fallback="ollama")
        if self.provider == "ollama":
            self.base_url = cfg.get("llm", "base_url")
            self.model = cfg.get("llm", "model")
        else:
            self.base_url = cfg.get("llm.azure_foundry", "endpoint")
            self.api_key = cfg.get("llm.azure_foundry", "api_key")
            self.model = cfg.get("llm.azure_foundry", "model")
            self.api_version = cfg.get("llm.azure_foundry", "api_version")
        self.temperature = cfg.getfloat("llm", "temperature", fallback=0.3)
        self.max_tokens = cfg.getint("llm", "max_tokens", fallback=2048)

    async def chat(self, system_prompt: str, user_message: str) -> str:
        if self.provider == "ollama":
            return await self._ollama_chat(system_prompt, user_message)
        return await self._azure_foundry_chat(system_prompt, user_message)

    async def _ollama_chat(self, system_prompt: str, user_message: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                async with _ollama_semaphore:
                    async with httpx.AsyncClient(timeout=300) as client:
                        resp = await client.post(url, json=payload)
                        if resp.status_code in (404, 429):
                            delay = RETRY_BASE_DELAY * (2 ** attempt)
                            logger.warning(f"Ollama {resp.status_code}, retry {attempt+1}/{MAX_RETRIES} in {delay}s")
                            last_err = f"HTTP {resp.status_code}"
                            await asyncio.sleep(delay)
                            continue
                        resp.raise_for_status()
                        data = resp.json()
                        return data.get("message", {}).get("content", "")
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (404, 429):
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(f"Ollama {e.response.status_code}, retry {attempt+1}/{MAX_RETRIES} in {delay}s")
                    await asyncio.sleep(delay)
                    last_err = e
                    continue
                logger.error(f"Ollama LLM call failed: {e}")
                return f"[LLM Error: {e}]"
            except Exception as e:
                logger.error(f"Ollama LLM call failed: {e}")
                return f"[LLM Error: {e}]"
        logger.error(f"Ollama LLM call failed after {MAX_RETRIES} retries: {last_err}")
        return f"[LLM Error: Failed after {MAX_RETRIES} retries - {last_err}]"

    async def _azure_foundry_chat(self, system_prompt: str, user_message: str) -> str:
        url = (
            f"{self.base_url}/openai/deployments/{self.model}"
            f"/chat/completions?api-version={self.api_version}"
        )
        headers = {"api-key": self.api_key, "Content-Type": "application/json"}
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Azure Foundry LLM call failed: {e}")
            return f"[LLM Error: {e}]"


_llm_client = None

def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
