import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table

config_cmd = typer.Typer(help="Manage Ace configuration.")
console = Console()

# Maps friendly key name → env var name written to ~/.ace/.env
_VALID_KEYS: dict[str, str] = {
    "provider":          "ACE_PROVIDER",
    "groq_api_key":      "GROQ_API_KEY",
    "groq_model":        "ACE_GROQ_MODEL",
    "deepseek_api_key":  "DEEPSEEK_API_KEY",
    "deepseek_model":    "ACE_DEEPSEEK_MODEL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_model":   "ACE_ANTHROPIC_MODEL",
}


def _env_file() -> Path:
    from ace.config import ACE_DIR
    return ACE_DIR / ".env"


@config_cmd.command("set")
def set_config(
    key: str = typer.Argument(help="Setting name"),
    value: str = typer.Argument(help="Value to set"),
) -> None:
    """Set a configuration value. Example: ace config set provider groq"""
    key = key.lower().strip()
    if key not in _VALID_KEYS:
        console.print(f"[red]Unknown key '{key}'.[/red] Valid keys: {', '.join(_VALID_KEYS)}")
        raise typer.Exit(1)

    from dotenv import set_key
    env_key = _VALID_KEYS[key]
    f = _env_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.touch(exist_ok=True)
    set_key(str(f), env_key, value)
    console.print(f"[green]✓[/green] {env_key}={value[:6]}{'...' if len(value) > 6 else ''}")


@config_cmd.command("show")
def show() -> None:
    """Show current configuration."""
    from ace.config import get_settings
    s = get_settings()

    table = Table(title="Ace Configuration", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("provider", s.provider)
    table.add_row("groq_model", s.groq_model)
    table.add_row("deepseek_model", s.deepseek_model)
    table.add_row("anthropic_model", s.anthropic_model)

    def _mask(k: str) -> str:
        return f"{k[:8]}..." if k else "[red]NOT SET[/red]"

    table.add_row("groq_api_key", _mask(s.groq_api_key))
    table.add_row("deepseek_api_key", _mask(s.deepseek_api_key))
    table.add_row("anthropic_api_key", _mask(s.anthropic_api_key))

    console.print(table)

    active_key = getattr(s, f"{s.provider}_api_key", "")
    if not active_key:
        console.print(f"\n[yellow]⚠  No API key set for provider '{s.provider}'.[/yellow]")
        console.print(f"   Run: [bold]ace config set {s.provider}_api_key <your-key>[/bold]")
