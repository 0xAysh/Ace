import typer
from rich.console import Console
from rich.table import Table

config_cmd = typer.Typer(help="Manage Ace configuration.")
console = Console()

_VALID_KEYS = {
    "provider":           ("ACE_PROVIDER",        "groq | deepseek | anthropic"),
    "groq_api_key":       ("GROQ_API_KEY",         "Groq API key (free at console.groq.com)"),
    "groq_model":         ("ACE_GROQ_MODEL",       "e.g. moonshotai/kimi-k2-instruct, qwen/qwen3-32b"),
    "deepseek_api_key":   ("DEEPSEEK_API_KEY",     "DeepSeek API key (platform.deepseek.com)"),
    "deepseek_model":     ("ACE_DEEPSEEK_MODEL",   "e.g. deepseek-chat"),
    "anthropic_api_key":  ("ANTHROPIC_API_KEY",    "Anthropic API key"),
    "anthropic_model":    ("ACE_ANTHROPIC_MODEL",  "e.g. claude-sonnet-4-5"),
}


def _env_file():
    from ace.config import ACE_DIR
    return ACE_DIR / ".env"


def _read_env() -> dict[str, str]:
    f = _env_file()
    if not f.exists():
        return {}
    result = {}
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(data: dict[str, str]) -> None:
    f = _env_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items()]
    f.write_text("\n".join(lines) + "\n")


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

    env_key = _VALID_KEYS[key][0]
    data = _read_env()
    data[env_key] = value
    _write_env(data)
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

    # Hint about active provider
    active_key = getattr(s, f"{s.provider}_api_key", "")
    if not active_key:
        console.print(f"\n[yellow]⚠  No API key set for provider '{s.provider}'.[/yellow]")
        console.print(f"   Run: [bold]ace config set {s.provider}_api_key <your-key>[/bold]")
