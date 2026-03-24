import asyncio
import typer
from rich.console import Console

console = Console()


def launch_cmd(
    headless: bool = typer.Option(False, "--headless", help="Run browser without UI (for debugging only)"),
) -> None:
    """Launch the Ace browser. Navigate to your assignment, then run [bold]ace run[/bold]."""
    asyncio.run(_launch(headless))


async def _launch(headless: bool) -> None:
    from ace.browser.launcher import launch_browser
    await launch_browser(headless=headless)
