"""Admin — User & API Key Management (PostgreSQL-backed)"""
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.db import get_pool, hash_api_key, hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin"])


# ── Helpers ──


def _get_user(request: Request) -> dict:
    """Extract authenticated user from request.state (set by middleware)."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _require_admin(request: Request) -> dict:
    """Extract user and verify admin role."""
    user = _get_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Request / Response Models ──


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    message: str
    user_id: int
    username: str
    role: str


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8)
    role: str = Field("user", pattern=r"^(admin|user)$")


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: str
    key_count: int


class UserListResponse(BaseModel):
    count: int
    users: list[UserInfo]


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=100)
    password: Optional[str] = Field(None, min_length=8)
    role: Optional[str] = Field(None, pattern=r"^(admin|user)$")
    is_active: Optional[bool] = None


class KeyCreateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)


class KeyCreateResponse(BaseModel):
    api_key: str
    key_id: int
    name: Optional[str] = None
    message: str


class KeyInfo(BaseModel):
    id: int
    key_prefix: str
    name: Optional[str]
    is_active: bool
    created_at: str
    last_used_at: Optional[str]
    user_id: int
    username: str


class KeyListResponse(BaseModel):
    count: int
    keys: list[KeyInfo]


# ══════════════════════════════════════════
#  LOGIN
# ══════════════════════════════════════════


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Verify user credentials."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, username, password_hash, role, is_active FROM ofkms_users WHERE username = $1",
        req.username,
    )
    if row is None or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account deactivated")

    return LoginResponse(
        message="Login successful",
        user_id=row["id"],
        username=row["username"],
        role=row["role"],
    )


# ══════════════════════════════════════════
#  USER MANAGEMENT (admin only)
# ══════════════════════════════════════════


@router.post("/users", status_code=201)
async def create_user(req: UserCreateRequest, request: Request):
    """Create a new user (admin only)."""
    _require_admin(request)
    pool = get_pool()

    exists = await pool.fetchval(
        "SELECT 1 FROM ofkms_users WHERE username = $1", req.username
    )
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")

    pw_hash = hash_password(req.password)
    user_id = await pool.fetchval(
        """
        INSERT INTO ofkms_users (username, password_hash, role)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        req.username,
        pw_hash,
        req.role,
    )
    logger.info("User created: %s (id=%d, role=%s)", req.username, user_id, req.role)
    return {"message": "User created", "user_id": user_id, "username": req.username}


@router.get("/users", response_model=UserListResponse)
async def list_users(request: Request):
    """List all users with key counts (admin only)."""
    _require_admin(request)
    pool = get_pool()

    rows = await pool.fetch(
        """
        SELECT u.id, u.username, u.role, u.is_active, u.created_at,
               COUNT(k.id) FILTER (WHERE k.is_active = TRUE) AS key_count
        FROM ofkms_users u
        LEFT JOIN ofkms_api_keys k ON k.user_id = u.id
        GROUP BY u.id
        ORDER BY u.id
        """
    )
    users = [
        UserInfo(
            id=r["id"],
            username=r["username"],
            role=r["role"],
            is_active=r["is_active"],
            created_at=r["created_at"].isoformat(),
            key_count=r["key_count"],
        )
        for r in rows
    ]
    return UserListResponse(count=len(users), users=users)


@router.patch("/users/{user_id}")
async def update_user(user_id: int, req: UserUpdateRequest, request: Request):
    """Update a user (admin only)."""
    _require_admin(request)
    pool = get_pool()

    existing = await pool.fetchrow("SELECT id FROM ofkms_users WHERE id = $1", user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")

    updates: list[str] = []
    params: list = []
    idx = 1

    if req.username is not None:
        dup = await pool.fetchval(
            "SELECT 1 FROM ofkms_users WHERE username = $1 AND id != $2",
            req.username,
            user_id,
        )
        if dup:
            raise HTTPException(status_code=409, detail="Username already exists")
        updates.append(f"username = ${idx}")
        params.append(req.username)
        idx += 1
    if req.password is not None:
        updates.append(f"password_hash = ${idx}")
        params.append(hash_password(req.password))
        idx += 1
    if req.role is not None:
        updates.append(f"role = ${idx}")
        params.append(req.role)
        idx += 1
    if req.is_active is not None:
        updates.append(f"is_active = ${idx}")
        params.append(req.is_active)
        idx += 1

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append(f"updated_at = NOW()")
    params.append(user_id)
    sql = f"UPDATE ofkms_users SET {', '.join(updates)} WHERE id = ${idx}"
    await pool.execute(sql, *params)

    logger.info("User updated: id=%d", user_id)
    return {"message": "User updated", "user_id": user_id}


@router.delete("/users/{user_id}")
async def deactivate_user(user_id: int, request: Request):
    """Deactivate a user and revoke all their keys (admin only)."""
    admin = _require_admin(request)

    if admin["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    pool = get_pool()
    existing = await pool.fetchrow("SELECT id FROM ofkms_users WHERE id = $1", user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")

    await pool.execute(
        "UPDATE ofkms_users SET is_active = FALSE, updated_at = NOW() WHERE id = $1",
        user_id,
    )
    await pool.execute(
        "UPDATE ofkms_api_keys SET is_active = FALSE WHERE user_id = $1", user_id
    )

    logger.info("User deactivated: id=%d (keys revoked)", user_id)
    return {"message": "User deactivated", "user_id": user_id}


# ══════════════════════════════════════════
#  API KEY MANAGEMENT
# ══════════════════════════════════════════


@router.post("/keys", response_model=KeyCreateResponse, status_code=201)
async def create_key(request: Request, req: KeyCreateRequest = None):
    """Create a new API key for the authenticated user."""
    user = _get_user(request)
    pool = get_pool()

    raw_key = "ofkms-" + secrets.token_urlsafe(32)
    key_h = hash_api_key(raw_key)
    name = req.name if req else None

    key_id = await pool.fetchval(
        """
        INSERT INTO ofkms_api_keys (user_id, key_hash, key_prefix, name)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        user["user_id"],
        key_h,
        raw_key[:12],
        name,
    )

    logger.info("API key created: %s... (user=%s, id=%d)", raw_key[:12], user["username"], key_id)
    return KeyCreateResponse(
        api_key=raw_key,
        key_id=key_id,
        name=name,
        message="Key created. Store it securely — it cannot be retrieved again.",
    )


@router.get("/keys", response_model=KeyListResponse)
async def list_keys(request: Request, user_id: Optional[int] = None):
    """List API keys. Regular users see own keys; admins can filter by user_id."""
    user = _get_user(request)
    pool = get_pool()

    if user["role"] == "admin" and user_id is not None:
        target_user_id = user_id
    else:
        target_user_id = user["user_id"]

    rows = await pool.fetch(
        """
        SELECT k.id, k.key_prefix, k.name, k.is_active, k.created_at, k.last_used_at,
               k.user_id, u.username
        FROM ofkms_api_keys k
        JOIN ofkms_users u ON u.id = k.user_id
        WHERE k.user_id = $1
        ORDER BY k.id
        """,
        target_user_id,
    )
    keys = [
        KeyInfo(
            id=r["id"],
            key_prefix=r["key_prefix"],
            name=r["name"],
            is_active=r["is_active"],
            created_at=r["created_at"].isoformat(),
            last_used_at=r["last_used_at"].isoformat() if r["last_used_at"] else None,
            user_id=r["user_id"],
            username=r["username"],
        )
        for r in rows
    ]
    return KeyListResponse(count=len(keys), keys=keys)


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: int, request: Request):
    """Revoke (deactivate) an API key. Owner or admin can revoke."""
    user = _get_user(request)
    pool = get_pool()

    row = await pool.fetchrow(
        "SELECT id, user_id, is_active FROM ofkms_api_keys WHERE id = $1", key_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    if not row["is_active"]:
        raise HTTPException(status_code=400, detail="Key already revoked")

    # Owner or admin can revoke
    if row["user_id"] != user["user_id"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to revoke this key")

    await pool.execute(
        "UPDATE ofkms_api_keys SET is_active = FALSE WHERE id = $1", key_id
    )
    logger.info("API key revoked: id=%d (by user=%s)", key_id, user["username"])
    return {"message": "Key revoked", "key_id": key_id}
