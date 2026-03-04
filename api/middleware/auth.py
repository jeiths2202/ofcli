"""API Key Authentication Middleware — PostgreSQL-backed"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.db import validate_api_key


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {
        "/v1/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/",
        "/v1/admin/login",
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key"},
            )

        user = await validate_api_key(api_key)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key"},
            )

        request.state.user = user
        return await call_next(request)
