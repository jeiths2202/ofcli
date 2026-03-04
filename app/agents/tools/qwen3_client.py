"""Qwen3 32B vLLM 클라이언트 (OpenAI 호환)"""
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Qwen3Client:
    """Qwen3 32B via vLLM — OpenAI-compatible API"""

    def __init__(self):
        s = get_settings()
        self._base_url = s.LLM_BASE_URL
        self._model = s.LLM_MODEL
        self._timeout = s.LLM_TIMEOUT
        self._temperature = s.LLM_TEMPERATURE
        self._max_tokens = s.LLM_MAX_TOKENS

    async def chat(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature or self._temperature,
            "max_tokens": max_tokens or self._max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(f"{self._base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""

    async def chat_stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> AsyncGenerator[str, None]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature or self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            async with c.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        delta = json.loads(raw)["choices"][0]["delta"]
                        token = delta.get("content", "")
                        if token:
                            yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                resp = await c.get(f"{self._base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False
