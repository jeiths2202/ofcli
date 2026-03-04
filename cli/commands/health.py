"""ofkms health -- Check OFKMS API and infrastructure health"""
import asyncio

import click
import httpx

from cli.client import APIError, OFKMSClient
from cli.config import ConfigManager
from cli.display import console, show_error, show_health


@click.command()
def health():
    """Check OFKMS API and infrastructure health."""
    asyncio.run(_health())


async def _health():
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url)
    try:
        with console.status("Checking services..."):
            resp = await client.health()
        show_health(resp)
    except httpx.ConnectError:
        show_error(
            f"Cannot connect to {cfg.api_url}",
            hint="Check: ofkms config show",
        )
    except APIError as e:
        show_error(str(e), hint=e.hint)
