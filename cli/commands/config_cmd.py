"""ofkms config -- CLI configuration management"""
import click
from rich.table import Table

from cli.config import VALID_KEYS, ConfigManager
from cli.display import console, show_error, show_success


@click.group()
def config():
    """Manage CLI configuration."""


@config.command("show")
def config_show():
    """Show current configuration."""
    cfg = ConfigManager.load()

    tbl = Table(title="OFKMS CLI Config", show_lines=False, padding=(0, 1))
    tbl.add_column("Key", style="cyan")
    tbl.add_column("Value")

    tbl.add_row("api-url", cfg.api_url)
    tbl.add_row("api-key", (cfg.api_key[:16] + "...") if cfg.api_key else "[dim](not set)[/]")
    tbl.add_row("default-language", cfg.default_language or "[dim](auto)[/]")
    tbl.add_row("default-product", cfg.default_product or "[dim](all)[/]")
    tbl.add_row("stream", str(cfg.stream))

    console.print()
    console.print(tbl)
    console.print()


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a configuration value.

    Valid keys: api-url, api-key, default-language, default-product, stream
    """
    if key not in VALID_KEYS:
        show_error(f"Invalid key: {key}", hint=f"Valid keys: {', '.join(sorted(VALID_KEYS))}")
        return
    ConfigManager.set(key, value)
    display_value = (value[:16] + "...") if key == "api-key" and len(value) > 16 else value
    show_success(f"{key} = {display_value}")


@config.command("reset")
def config_reset():
    """Reset configuration to defaults."""
    if click.confirm("Reset all settings to defaults?"):
        ConfigManager.reset()
        show_success("Config reset to defaults")
