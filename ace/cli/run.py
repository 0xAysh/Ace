import asyncio
import typer
from rich.console import Console

console = Console()


def run_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Fill answers but do not submit."),
    auto_submit: bool = typer.Option(False, "--auto-submit", "-y", help="Submit automatically without confirmation prompt."),
    url: str = typer.Option("", "--url", help="Jump directly to this assignment URL."),
) -> None:
    """Open browser, navigate to your assignment, press Enter — Ace does the rest."""
    asyncio.run(_run(dry_run=dry_run, auto_submit=auto_submit, url=url or None))


async def _run(dry_run: bool, auto_submit: bool, url: str | None) -> None:
    from ace.orchestrator import Orchestrator
    orch = Orchestrator(dry_run=dry_run, auto_submit=auto_submit)
    await orch.run(target_url=url)
