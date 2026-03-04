"""ofkms products -- List supported OpenFrame products"""
import asyncio

import click
import httpx

from cli.client import APIError, OFKMSClient
from cli.config import ConfigManager
from cli.display import console, show_error, show_products


@click.command()
def products():
    """List supported OpenFrame products."""
    asyncio.run(_products())


async def _products():
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url, cfg.api_key)
    try:
        with console.status("Loading products..."):
            resp = await client.products()
        show_products(resp)
    except httpx.ConnectError:
        show_error(
            f"Cannot connect to {cfg.api_url}",
            hint="Check: ofkms config show",
        )
    except APIError as e:
        show_error(str(e), hint=e.hint)
