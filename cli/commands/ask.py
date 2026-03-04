"""ofkms ask -- Query OpenFrame knowledge base"""
import asyncio
from typing import Optional

import click
import httpx

from cli.client import APIError, OFKMSClient
from cli.config import ConfigManager
from cli.display import (
    console,
    show_answer,
    show_error,
    show_stream_answer,
    show_stream_done,
    show_stream_error,
    show_stream_phase,
)


@click.command()
@click.argument("query")
@click.option("--stream/--no-stream", default=None, help="Enable/disable streaming (default: config)")
@click.option("--product", "-p", default=None, help="Product filter (e.g. mvs_openframe_7.1)")
@click.option("--lang", "-l", default=None, help="Response language: ja|ko|en")
@click.option("--phases", is_flag=True, help="Include phase timing details")
def ask(query: str, stream: Optional[bool], product: Optional[str], lang: Optional[str], phases: bool):
    """Ask a question about OpenFrame products."""
    asyncio.run(_ask(query, stream, product, lang, phases))


async def _ask(
    query: str,
    stream: Optional[bool],
    product: Optional[str],
    lang: Optional[str],
    phases: bool,
):
    cfg = ConfigManager.load()

    if not cfg.api_key:
        show_error("API key not set", hint="Run: ofkms config set api-key <key>")
        return

    use_stream = stream if stream is not None else cfg.stream
    language = lang or cfg.default_language
    prod = product or cfg.default_product
    client = OFKMSClient(cfg.api_url, cfg.api_key)

    try:
        if use_stream:
            await _ask_stream(client, query, language, prod)
        else:
            await _ask_sync(client, query, language, prod, phases)
    except httpx.ConnectError:
        show_error(
            f"Cannot connect to {cfg.api_url}",
            hint="Check: ofkms config show  and  ofkms health",
        )
    except httpx.ReadTimeout:
        show_error("Request timed out", hint="The server may be under heavy load. Try again.")
    except APIError as e:
        show_error(str(e), hint=e.hint)


async def _ask_sync(
    client: OFKMSClient,
    query: str,
    language: Optional[str],
    product: Optional[str],
    phases: bool,
):
    with console.status("[cyan]Searching...[/]"):
        resp = await client.query(
            query,
            language=language,
            product=product,
            include_sources=True,
            include_phases=phases,
        )
    show_answer(resp)


async def _ask_stream(
    client: OFKMSClient,
    query: str,
    language: Optional[str],
    product: Optional[str],
):
    console.print()
    async for event in client.query_stream(query, language=language, product=product):
        evt = event.get("event")
        data = event.get("data", {})
        if evt == "phase":
            show_stream_phase(data)
        elif evt == "answer":
            show_stream_answer(data)
        elif evt == "done":
            show_stream_done(data)
        elif evt == "error":
            show_stream_error(data)
