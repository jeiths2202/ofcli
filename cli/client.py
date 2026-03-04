"""OFKMS API Client — httpx wrapper for all REST API calls"""
import json
from typing import AsyncGenerator, Optional

import httpx


class APIError(Exception):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.hint = hint


class AuthenticationError(APIError):
    pass


class ServerError(APIError):
    pass


def _parse_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"detail": resp.text or f"HTTP {resp.status_code}"}


def _check_response(resp: httpx.Response) -> dict:
    if resp.status_code == 401:
        raise AuthenticationError(
            "API key is invalid or missing",
            hint="Run: ofkms login  or  ofkms config set api-key <key>",
        )
    if resp.status_code == 403:
        detail = _parse_json(resp).get("detail", "Permission denied")
        raise APIError(f"Forbidden: {detail}")
    if resp.status_code == 404:
        detail = _parse_json(resp).get("detail", "Not found")
        raise APIError(f"Not found: {detail}")
    if resp.status_code >= 500:
        detail = _parse_json(resp).get("detail", "Internal server error")
        raise ServerError(
            f"Server error: {detail}",
            hint="Run: ofkms health",
        )
    if resp.status_code >= 400:
        detail = _parse_json(resp).get("detail", resp.text)
        raise APIError(f"Request failed ({resp.status_code}): {detail}")
    try:
        return resp.json()
    except Exception:
        raise ServerError(
            f"Invalid response from server (status {resp.status_code})",
            hint="The API server may not be running. Check: ofkms health",
        )


class OFKMSClient:
    def __init__(self, api_url: str, api_key: Optional[str] = None, timeout: float = 120):
        self._base = api_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    # ── Public endpoints (no auth) ──

    async def health(self) -> dict:
        """GET /v1/health"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{self._base}/v1/health")
            return _check_response(resp)

    async def login(self, username: str, password: str) -> dict:
        """POST /v1/admin/login"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{self._base}/v1/admin/login",
                json={"username": username, "password": password},
            )
            return _check_response(resp)

    # ── Authenticated endpoints ──

    async def products(self) -> dict:
        """GET /v1/products"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{self._base}/v1/products", headers=self._headers())
            return _check_response(resp)

    async def query(
        self,
        query: str,
        language: Optional[str] = None,
        product: Optional[str] = None,
        include_sources: bool = True,
        include_phases: bool = False,
    ) -> dict:
        """POST /v1/query"""
        body: dict = {
            "query": query,
            "include_sources": include_sources,
            "include_phases": include_phases,
        }
        if language:
            body["language"] = language
        if product:
            body["product"] = product

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(
                f"{self._base}/v1/query",
                json=body,
                headers=self._headers(),
            )
            return _check_response(resp)

    async def query_stream(
        self,
        query: str,
        language: Optional[str] = None,
        product: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """POST /v1/query/stream → SSE events

        Yields: {"event": "phase"|"answer"|"done"|"error", "data": {...}}
        """
        body: dict = {"query": query, "include_sources": True}
        if language:
            body["language"] = language
        if product:
            body["product"] = product

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            async with c.stream(
                "POST",
                f"{self._base}/v1/query/stream",
                json=body,
                headers=self._headers(),
            ) as resp:
                if resp.status_code == 401:
                    raise AuthenticationError(
                        "API key is invalid or missing",
                        hint="Run: ofkms login  or  ofkms config set api-key <key>",
                    )
                if resp.status_code >= 400:
                    await resp.aread()
                    raise ServerError(f"Stream request failed ({resp.status_code})")

                event_type: Optional[str] = None
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        raw = line[5:].strip()
                        if raw:
                            data = json.loads(raw)
                            yield {"event": event_type or "message", "data": data}

    # ── API Key management ──

    async def create_key(self, name: Optional[str] = None) -> dict:
        """POST /v1/admin/keys"""
        body = {"name": name} if name else {}
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{self._base}/v1/admin/keys",
                json=body,
                headers=self._headers(),
            )
            return _check_response(resp)

    async def list_keys(self) -> dict:
        """GET /v1/admin/keys"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{self._base}/v1/admin/keys", headers=self._headers())
            return _check_response(resp)

    async def revoke_key(self, key_id: int) -> dict:
        """DELETE /v1/admin/keys/{key_id}"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.delete(
                f"{self._base}/v1/admin/keys/{key_id}",
                headers=self._headers(),
            )
            return _check_response(resp)
