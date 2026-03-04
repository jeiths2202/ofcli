"""ofkms login / ofkms keys -- Authentication and API key management"""
import asyncio
from typing import Optional

import click
import httpx

from cli.client import APIError, OFKMSClient
from cli.config import ConfigManager
from cli.display import console, show_error, show_key_created, show_keys, show_success


@click.command()
@click.option("--username", "-u", prompt=True)
@click.option("--password", "-p", prompt=True, hide_input=True)
def login(username: str, password: str):
    """Login and verify credentials."""
    asyncio.run(_login(username, password))


async def _login(username: str, password: str):
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url)
    try:
        resp = await client.login(username, password)
        show_success(f"Login successful: {resp['username']} ({resp['role']})")

        if cfg.api_key:
            console.print(f"  [dim]Current API key: {cfg.api_key[:16]}...[/]")
        else:
            console.print()
            console.print("  Set your API key to start using the CLI:")
            console.print("  [bold]ofkms config set api-key <your-api-key>[/]")
            console.print()
    except httpx.ConnectError:
        show_error(
            f"Cannot connect to {cfg.api_url}",
            hint="Check: ofkms config set api-url <url>",
        )
    except APIError as e:
        show_error(str(e), hint=e.hint)


# ── API Key Management ──


@click.group()
def keys():
    """Manage API keys."""


@keys.command("list")
def keys_list():
    """List your API keys."""
    asyncio.run(_keys_list())


async def _keys_list():
    cfg = ConfigManager.load()
    if not cfg.api_key:
        show_error("API key not set", hint="Run: ofkms config set api-key <key>")
        return

    client = OFKMSClient(cfg.api_url, cfg.api_key)
    try:
        resp = await client.list_keys()
        show_keys(resp)
    except httpx.ConnectError:
        show_error(f"Cannot connect to {cfg.api_url}")
    except APIError as e:
        show_error(str(e), hint=e.hint)


@keys.command("create")
@click.option("--name", "-n", default=None, help="Key name for identification")
def keys_create(name: Optional[str]):
    """Create a new API key."""
    asyncio.run(_keys_create(name))


async def _keys_create(name: Optional[str]):
    cfg = ConfigManager.load()
    if not cfg.api_key:
        show_error("API key not set", hint="Run: ofkms config set api-key <key>")
        return

    client = OFKMSClient(cfg.api_url, cfg.api_key)
    try:
        resp = await client.create_key(name)
        show_key_created(resp)

        if click.confirm("  Save this key as default?"):
            cfg.api_key = resp["api_key"]
            ConfigManager.save(cfg)
            show_success("Key saved to config")
    except httpx.ConnectError:
        show_error(f"Cannot connect to {cfg.api_url}")
    except APIError as e:
        show_error(str(e), hint=e.hint)


@keys.command("revoke")
@click.argument("key_id", type=int)
def keys_revoke(key_id: int):
    """Revoke an API key by ID."""
    asyncio.run(_keys_revoke(key_id))


async def _keys_revoke(key_id: int):
    cfg = ConfigManager.load()
    if not cfg.api_key:
        show_error("API key not set", hint="Run: ofkms config set api-key <key>")
        return

    client = OFKMSClient(cfg.api_url, cfg.api_key)
    try:
        await client.revoke_key(key_id)
        show_success(f"Key {key_id} revoked")
    except httpx.ConnectError:
        show_error(f"Cannot connect to {cfg.api_url}")
    except APIError as e:
        show_error(str(e), hint=e.hint)
