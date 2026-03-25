import asyncio
import typer
from rich.console import Console

console = Console()


def run_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Fill answers but do not submit."),
    url: str = typer.Option("", "--url", help="Jump directly to this assignment URL."),
) -> None:
    """Open browser, navigate to your assignment, press Enter — Ace does the rest."""
    asyncio.run(_run(dry_run=dry_run, url=url or None))


async def _run(dry_run: bool, url: str | None) -> None:
    from ace.orchestrator import Orchestrator
    orch = Orchestrator(dry_run=dry_run)
    await orch.run(target_url=url)
