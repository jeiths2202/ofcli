"""
OFKMS v2.0 - CLI Entry Point
Interactive Knowledge Management System for TmaxSoft OpenFrame
"""
import asyncio
import logging
import os
import sys

# Windows UTF-8 support
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # Enable VT100 escape sequences on Windows 10+
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        kernel32.SetConsoleOutputCP(65001)  # UTF-8 code page
    except Exception:
        pass

from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from app.agents.orchestrator import Orchestrator
from app.core.config import get_settings
from app.models.response import FinalResponse, VerificationLevel

console = Console(force_terminal=True, force_jupyter=False)


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False, markup=True)],
    )
    # 외부 라이브러리 로그 억제
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def display_response(resp: FinalResponse):
    """FinalResponse를 Rich 포맷으로 출력"""

    # ─── 메인 응답 ───
    console.print()
    console.print(Panel(
        Markdown(resp.answer),
        title=f"[bold green]Answer[/] ({resp.answer_language})",
        border_style="green" if resp.overall_confidence >= 0.5 else "yellow",
        padding=(1, 2),
    ))

    # ─── 메타 정보 테이블 ───
    meta = Table(show_header=False, box=None, padding=(0, 2))
    meta.add_column(style="bold cyan")
    meta.add_column()
    meta.add_row("Confidence", f"{resp.overall_confidence:.1%}")
    meta.add_row("Intent", resp.query_intent)
    meta.add_row("Product", resp.product)
    meta.add_row("Phases", ", ".join(str(p) for p in resp.phases_executed))
    meta.add_row("Fallback", "Yes" if resp.fallback_used else "No")
    meta.add_row("Total Time", f"{resp.total_time_ms}ms")
    console.print(meta)

    # ─── Phase 타이밍 ───
    if resp.phase_times:
        timing = Table(title="Phase Timing", show_header=True, header_style="bold")
        timing.add_column("Phase", style="cyan")
        timing.add_column("Time (ms)", justify="right")
        for name, ms in resp.phase_times.items():
            timing.add_row(name, str(ms))
        console.print(timing)

    # ─── 출처 ───
    if resp.sources:
        src_table = Table(title="Sources", show_header=True, header_style="bold")
        src_table.add_column("Document", style="cyan")
        src_table.add_column("Page", justify="right")
        src_table.add_column("Score", justify="right")
        src_table.add_column("Type")
        for s in resp.sources:
            src_table.add_row(
                s.doc_name,
                str(s.page or "-"),
                f"{s.score:.2f}",
                s.source_type,
            )
        console.print(src_table)

    # ─── 검증 결과 (verbose) ───
    if resp.verification:
        verified = sum(1 for v in resp.verification if v.level == VerificationLevel.VERIFIED)
        inferred = sum(1 for v in resp.verification if v.level == VerificationLevel.INFERRED)
        unverified = sum(1 for v in resp.verification if v.level == VerificationLevel.UNVERIFIED)
        console.print(
            f"\n[dim]Verification: "
            f"[green]{verified} verified[/] / "
            f"[yellow]{inferred} inferred[/] / "
            f"[red]{unverified} unverified[/][/dim]"
        )


async def health_check():
    """인프라 서비스 연결 확인"""
    import httpx

    settings = get_settings()
    checks = {
        "LLM (Qwen3)": f"{settings.LLM_BASE_URL}/models",
        "BGE-M3": f"{settings.BGE_M3_BASE_URL}/v1/embeddings",
        "OFCode": f"{settings.OFCODE_BASE_URL}/health",
    }
    table = Table(title="Infrastructure Health", show_header=True, header_style="bold")
    table.add_column("Service")
    table.add_column("Status", justify="center")

    async with httpx.AsyncClient(timeout=5) as client:
        for name, url in checks.items():
            try:
                if "embeddings" in url:
                    r = await client.post(url, json={"input": "test"})
                else:
                    r = await client.get(url)
                if r.status_code < 400:
                    table.add_row(name, "[green]OK[/]")
                else:
                    table.add_row(name, f"[yellow]{r.status_code}[/]")
            except Exception:
                table.add_row(name, "[red]FAIL[/]")

    console.print(table)


async def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    setup_logging(verbose)

    console.print(Panel(
        "[bold]OFKMS v2.0[/] - TmaxSoft OpenFrame Knowledge Management System\n"
        "Type your question, or use commands:\n"
        "  [cyan]/help[/]    - Show available commands\n"
        "  [cyan]/clear[/]   - Clear context (reset pipeline)\n"
        "  [cyan]/health[/]  - Check infrastructure status\n"
        "  [cyan]/verbose[/] - Toggle debug logging\n"
        "  [cyan]/quit[/]    - Exit",
        border_style="blue",
    ))

    orchestrator = Orchestrator()

    try:
        while True:
            try:
                query = console.input("\n[bold blue]>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not query:
                continue

            if query.lower() in ("/quit", "/exit", "/q"):
                break

            if query.lower() in ("/help", "/h", "/?"):
                console.print(Panel(
                    "[cyan]/help[/]    - Show this help\n"
                    "[cyan]/clear[/]   - Clear context (reinitialize pipeline)\n"
                    "[cyan]/health[/]  - Check infrastructure service status\n"
                    "[cyan]/verbose[/] - Toggle debug logging ON/OFF\n"
                    "[cyan]/quit[/]    - Exit ([dim]/exit, /q[/dim])",
                    title="[bold]Commands[/]",
                    border_style="cyan",
                ))
                continue

            if query.lower() == "/clear":
                await orchestrator.close()
                orchestrator = Orchestrator()
                console.print("[green]Context cleared. Pipeline reinitialized.[/]")
                continue

            if query.lower() == "/health":
                await health_check()
                continue

            if query.lower() == "/verbose":
                current = logging.getLogger().level
                new_level = logging.DEBUG if current != logging.DEBUG else logging.INFO
                logging.getLogger().setLevel(new_level)
                console.print(f"[dim]Verbose: {'ON' if new_level == logging.DEBUG else 'OFF'}[/dim]")
                continue

            # 파이프라인 실행
            try:
                with console.status("[bold cyan]Processing...", spinner="dots"):
                    response = await orchestrator.execute(query)
                display_response(response)
            except Exception as e:
                console.print(f"[bold red]Error:[/] {e}")
                if verbose:
                    console.print_exception()
    finally:
        await orchestrator.close()
        console.print("\n[dim]Bye.[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
