import typer
from rich.console import Console
from rich.table import Table

config_cmd = typer.Typer(help="Manage Ace configuration.")
console = Console()


@config_cmd.command("show")
def show() -> None:
    """Show current configuration."""
    from ace.config import get_settings
    s = get_settings()
    table = Table(title="Ace Configuration", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("default_model", s.default_model)
    table.add_row("fallback_model", s.fallback_model)
    table.add_row("confidence_threshold", str(s.confidence_threshold))
    table.add_row("max_context_chunks", str(s.max_context_chunks))
    api_key_display = f"{s.anthropic_api_key[:8]}..." if s.anthropic_api_key else "[red]NOT SET[/red]"
    table.add_row("anthropic_api_key", api_key_display)
    console.print(table)
