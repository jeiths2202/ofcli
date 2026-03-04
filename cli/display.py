"""Rich output formatting for OFKMS CLI"""
import io
import sys
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

# Force UTF-8 output on Windows to avoid cp932 encoding errors with CJK chars
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)


def show_error(message: str, hint: Optional[str] = None) -> None:
    console.print(f"[bold red]Error:[/] {message}")
    if hint:
        console.print(f"[dim]Hint: {hint}[/]")


def show_success(message: str) -> None:
    console.print(f"[bold green]OK:[/] {message}")


def show_answer(response: dict) -> None:
    """Display query response with markdown, metadata, sources."""
    answer = response.get("answer", "")
    console.print()
    console.print(Panel(Markdown(answer), title="OFKMS Answer", border_style="cyan"))

    # Metadata line
    confidence = response.get("confidence", 0)
    pct = int(confidence * 100)
    bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
    intent = response.get("intent", "?")
    product = response.get("product", "?")
    usage = response.get("usage", {})
    time_s = usage.get("total_time_ms", 0) / 1000
    fallback = usage.get("fallback_used", False)

    console.print(f"  Confidence: {confidence:.2f} [{bar}] {pct}%")
    meta_parts = [f"Intent: {intent}", f"Product: {product}", f"Time: {time_s:.1f}s"]
    if fallback:
        meta_parts.append("[yellow]Fallback used[/]")
    console.print("  " + " | ".join(meta_parts))

    # Sources table
    sources = response.get("sources")
    if sources:
        console.print()
        tbl = Table(title="Sources", show_lines=False, padding=(0, 1))
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("Document")
        tbl.add_column("Page", justify="right")
        tbl.add_column("Score", justify="right")
        tbl.add_column("Type")
        for i, s in enumerate(sources, 1):
            tbl.add_row(
                str(i),
                s.get("document", "?"),
                str(s.get("page", "-")),
                f"{s.get('score', 0):.4f}",
                s.get("type", "?"),
            )
        console.print(tbl)

    # Phase times (if included)
    phase_times = usage.get("phase_times")
    if phase_times:
        console.print()
        ptbl = Table(title="Phase Times", show_lines=False, padding=(0, 1))
        ptbl.add_column("Phase")
        ptbl.add_column("Time (ms)", justify="right")
        for name, ms in phase_times.items():
            ptbl.add_row(name, str(ms))
        console.print(ptbl)

    console.print()


def show_stream_phase(data: dict) -> None:
    """Display a completed phase during streaming."""
    phase = data.get("phase", "?")
    name = data.get("name", "?")
    time_ms = data.get("time_ms", 0)
    console.print(f"  [green]OK[/] Phase {phase}: {name} ({time_ms}ms)")


def show_stream_answer(data: dict) -> None:
    """Display the final answer from streaming."""
    answer = data.get("answer", "")
    console.print()
    console.print(Panel(Markdown(answer), title="OFKMS Answer", border_style="cyan"))

    confidence = data.get("confidence", 0)
    intent = data.get("intent", "?")
    product = data.get("product", "?")
    pct = int(confidence * 100)
    console.print(f"  Confidence: {confidence:.2f} ({pct}%) | Intent: {intent} | Product: {product}")


def show_stream_done(data: dict) -> None:
    """Display total time after streaming completes."""
    total_ms = data.get("total_time_ms", 0)
    console.print(f"  [dim]Total: {total_ms / 1000:.1f}s[/]")
    console.print()


def show_stream_error(data: dict) -> None:
    """Display streaming error."""
    error = data.get("error", "Unknown error")
    show_error(f"Stream error: {error}")


def show_health(response: dict) -> None:
    """Display health check results."""
    status = response.get("status", "unknown")
    version = response.get("version", "?")
    services = response.get("services", {})

    color = {"healthy": "green", "degraded": "yellow", "unhealthy": "red"}.get(status, "white")
    console.print(f"\n  OFKMS v{version} -- Service Health\n")

    tbl = Table(show_lines=False, padding=(0, 1))
    tbl.add_column("Service", min_width=14)
    tbl.add_column("Status", justify="center")
    tbl.add_column("Latency", justify="right")

    for name, info in services.items():
        s = info.get("status", "?")
        lat = info.get("latency_ms", 0)
        if s == "ok":
            s_text = "[green]OK[/]"
            lat_text = f"{lat}ms"
        else:
            s_text = "[red]ERR[/]"
            lat_text = "[dim]--[/]"
        tbl.add_row(name, s_text, lat_text)

    console.print(tbl)

    ok_count = sum(1 for v in services.values() if v.get("status") == "ok")
    console.print(f"\n  Overall: [{color}]{status}[/] ({ok_count}/{len(services)} services ok)\n")


def show_products(response: dict) -> None:
    """Display product list."""
    products = response.get("products", [])
    console.print(f"\n  Supported Products ({len(products)})\n")

    tbl = Table(show_lines=False, padding=(0, 1))
    tbl.add_column("ID", min_width=22)
    tbl.add_column("Name")
    tbl.add_column("Keywords")

    for p in products:
        keywords = ", ".join(p.get("keywords", [])[:6])
        if len(p.get("keywords", [])) > 6:
            keywords += ", ..."
        tbl.add_row(p.get("id", "?"), p.get("name", "?"), keywords)

    console.print(tbl)
    console.print()


def show_keys(response: dict) -> None:
    """Display API keys list."""
    keys = response.get("keys", [])
    count = response.get("count", len(keys))
    console.print(f"\n  API Keys ({count})\n")

    tbl = Table(show_lines=False, padding=(0, 1))
    tbl.add_column("ID", justify="right")
    tbl.add_column("Prefix")
    tbl.add_column("Name")
    tbl.add_column("Active", justify="center")
    tbl.add_column("Created")
    tbl.add_column("Last Used")

    for k in keys:
        active = "[green]Yes[/]" if k.get("is_active") else "[red]No[/]"
        created = k.get("created_at", "?")[:10]
        last_used = (k.get("last_used_at") or "--")[:10]
        tbl.add_row(
            str(k.get("id", "?")),
            k.get("key_prefix", "?"),
            k.get("name") or "[dim]--[/]",
            active,
            created,
            last_used,
        )

    console.print(tbl)
    console.print()


def show_key_created(response: dict) -> None:
    """Display newly created API key."""
    api_key = response.get("api_key", "?")
    key_id = response.get("key_id", "?")
    name = response.get("name") or "--"

    console.print(f"\n  [bold green]API Key Created[/]")
    console.print(f"  ID:   {key_id}")
    console.print(f"  Name: {name}")
    console.print(f"  Key:  [bold yellow]{api_key}[/]")
    console.print(f"\n  [dim]Store it securely -- it cannot be retrieved again.[/]\n")
