"""PostgreSQL connection pool + user/API key auth tables"""
import hashlib
import logging
import secrets
from typing import Optional

import asyncpg
from passlib.hash import bcrypt

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

# ── DDL ──

_DDL = """
CREATE TABLE IF NOT EXISTS ofkms_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ofkms_api_keys (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES ofkms_users(id) ON DELETE CASCADE,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_prefix VARCHAR(16) NOT NULL,
    name VARCHAR(200),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ofkms_api_keys_key_hash
    ON ofkms_api_keys(key_hash) WHERE is_active = TRUE;
"""

# ── Hashing helpers ──


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def hash_password(password: str) -> str:
    """Bcrypt hash for user passwords."""
    return bcrypt.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against bcrypt hash."""
    return bcrypt.verify(plain, hashed)


# ── Pool lifecycle ──


async def init_db() -> None:
    """Create connection pool, run DDL, seed defaults."""
    global _pool
    s = get_settings()
    _pool = await asyncpg.create_pool(
        host=s.POSTGRES_HOST,
        port=s.POSTGRES_PORT,
        database=s.POSTGRES_DB,
        user=s.POSTGRES_USER,
        password=s.POSTGRES_PASSWORD,
        min_size=s.DB_POOL_MIN_SIZE,
        max_size=s.DB_POOL_MAX_SIZE,
    )
    async with _pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("Auth tables ready (ofkms_users, ofkms_api_keys)")

    await _seed_default_admin()
    await _migrate_env_keys()


async def close_db() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the module-level pool (raises if not initialised)."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_db() first")
    return _pool


# ── Middleware hot path ──


async def validate_api_key(raw_key: str) -> Optional[dict]:
    """
    Validate an API key. Returns user dict or None.
    Updates last_used_at on success.
    """
    pool = get_pool()
    key_h = hash_api_key(raw_key)
    row = await pool.fetchrow(
        """
        SELECT k.id AS key_id, u.id AS user_id, u.username, u.role, u.is_active
        FROM ofkms_api_keys k
        JOIN ofkms_users u ON u.id = k.user_id
        WHERE k.key_hash = $1 AND k.is_active = TRUE
        """,
        key_h,
    )
    if row is None:
        return None
    if not row["is_active"]:
        return None

    # Fire-and-forget last_used_at update (don't block the request)
    await pool.execute(
        "UPDATE ofkms_api_keys SET last_used_at = NOW() WHERE id = $1", row["key_id"]
    )

    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "key_id": row["key_id"],
    }


# ── Seeding ──

_DEFAULT_ADMIN_PASSWORD = "SecureAdm1nP@ss2024!"


async def _seed_default_admin() -> None:
    """If no users exist, create default admin + initial API key."""
    pool = get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM ofkms_users")
    if count > 0:
        return

    pw_hash = hash_password(_DEFAULT_ADMIN_PASSWORD)
    admin_id = await pool.fetchval(
        """
        INSERT INTO ofkms_users (username, password_hash, role)
        VALUES ($1, $2, 'admin')
        RETURNING id
        """,
        "admin",
        pw_hash,
    )

    raw_key = "ofkms-" + secrets.token_urlsafe(32)
    key_h = hash_api_key(raw_key)
    await pool.execute(
        """
        INSERT INTO ofkms_api_keys (user_id, key_hash, key_prefix, name)
        VALUES ($1, $2, $3,'default-admin-key')
        """,
        admin_id,
        key_h,
        raw_key[:12],
    )

    logger.info("=" * 60)
    logger.info("Default admin user created")
    logger.info("  Username : admin")
    logger.info("  Password : %s", _DEFAULT_ADMIN_PASSWORD)
    logger.info("  API Key  : %s", raw_key)
    logger.info("  *** Store these credentials securely! ***")
    logger.info("=" * 60)


async def _migrate_env_keys() -> None:
    """One-time import of existing .env API_KEYS under admin user."""
    s = get_settings()
    if not s.API_KEYS:
        return

    pool = get_pool()
    admin = await pool.fetchrow(
        "SELECT id FROM ofkms_users WHERE username = 'admin' AND role = 'admin'"
    )
    if admin is None:
        return

    env_keys = [k.strip() for k in s.API_KEYS.split(",") if k.strip()]
    migrated = 0
    for raw_key in env_keys:
        key_h = hash_api_key(raw_key)
        exists = await pool.fetchval(
            "SELECT 1 FROM ofkms_api_keys WHERE key_hash = $1", key_h
        )
        if exists:
            continue
        await pool.execute(
            """
            INSERT INTO ofkms_api_keys (user_id, key_hash, key_prefix, name)
            VALUES ($1, $2, $3, $4)
            """,
            admin["id"],
            key_h,
            raw_key[:12],
            "migrated-from-env",
        )
        migrated += 1

    if migrated:
        logger.info("Migrated %d API key(s) from .env to database", migrated)
