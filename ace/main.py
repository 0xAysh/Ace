import os
os.environ.setdefault("NODE_NO_WARNINGS", "1")

import typer
from rich.console import Console

from ace.config import ensure_dirs

app = typer.Typer(
    name="ace",
    help="Automated academic assignment tool — opens browser, reads your quiz, answers it, you approve.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _load_commands() -> None:
    from ace.cli.run import run_cmd
    from ace.cli.config import config_cmd
    from ace.cli.debug import debug_cmd

    app.command("run")(run_cmd)
    app.command("debug")(debug_cmd)
    app.add_typer(config_cmd, name="config")


@app.command("help")
def help_cmd() -> None:
    """Show all commands and options."""
    from ace.config import get_settings
    s = get_settings()
    console.print(f"""
[bold cyan]ace[/bold cyan] — AI assignment agent

[bold]COMMANDS[/bold]

  [bold green]ace run[/bold green]                     Open browser, go to assignment, press Enter
  [bold green]ace run --dry-run[/bold green]           Fill answers but don't submit
  [bold green]ace run --url [/bold green][dim]<url>[/dim]          Jump straight to an assignment URL

  [bold green]ace config show[/bold green]             Show current provider, model, API keys
  [bold green]ace config set provider[/bold green]     [dim]groq | deepseek | anthropic[/dim]
  [bold green]ace config set groq_api_key[/bold green]      [dim]gsk_...[/dim]   console.groq.com (free)
  [bold green]ace config set deepseek_api_key[/bold green]  [dim]sk-...[/dim]    platform.deepseek.com
  [bold green]ace config set anthropic_api_key[/bold green] [dim]sk-ant-...[/dim]
  [bold green]ace config set groq_model[/bold green]        [dim]{s.groq_model}[/dim]
  [bold green]ace config set deepseek_model[/bold green]    [dim]{s.deepseek_model}[/dim]
  [bold green]ace config set anthropic_model[/bold green]   [dim]{s.anthropic_model}[/dim]

  [bold green]ace debug[/bold green]                   Show what Ace sees in the current browser tab

[bold]CONFIG[/bold] stored in [dim]~/.ace/.env[/dim]
""")


_load_commands()


@app.callback()
def main() -> None:
    ensure_dirs()


if __name__ == "__main__":
    app()
