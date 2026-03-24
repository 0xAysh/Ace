import asyncio
import typer
from rich.console import Console

console = Console()


def run_cmd(
    auto: bool = typer.Option(False, "--auto", help="Fill and click Next automatically without per-question approval."),
    timed: bool = typer.Option(False, "--timed", help="Batch all LLM calls upfront to minimize latency on timed quizzes."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Select answers in browser but do not submit."),
    url: str = typer.Option("", "--url", help="Jump directly to this assignment URL."),
) -> None:
    """Open browser, navigate to your assignment, press Enter — Ace does the rest.

    The browser opens automatically. Log in if needed, navigate to your quiz,
    click Take Quiz / Begin Attempt, then press Enter in this terminal.
    """
    asyncio.run(_run(auto=auto, timed=timed, dry_run=dry_run, url=url or None))


async def _run(auto: bool, timed: bool, dry_run: bool, url: str | None) -> None:
    from ace.orchestrator import Orchestrator
    orch = Orchestrator(auto=auto, timed=timed, dry_run=dry_run)
    await orch.run(target_url=url)
