"""
Launches a persistent Chromium instance with remote debugging enabled.
The browser profile is stored at ~/.ace/browser-profile/ so cookies and
sessions survive between runs.
"""
import asyncio
import socket
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from ace.config import BROWSER_PROFILE_DIR, DEBUG_PORT_FILE, get_settings

console = Console()


def _find_free_port(start: int, end: int) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {start}–{end}")


async def _get_chromium_path() -> Path:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        return Path(p.chromium.executable_path)


async def launch_browser(headless: bool = False) -> None:
    settings = get_settings()

    try:
        port = _find_free_port(settings.debug_port_start, settings.debug_port_end)
    except RuntimeError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise SystemExit(1)

    try:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        DEBUG_PORT_FILE.write_text(str(port))
    except PermissionError:
        console.print(
            f"[bold red]Error:[/bold red] Cannot write to {BROWSER_PROFILE_DIR}\n"
            "Check your home directory permissions."
        )
        raise SystemExit(1)

    chromium = await _get_chromium_path()

    args = [
        str(chromium),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={BROWSER_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
    ]
    if headless:
        args.append("--headless=new")

    console.print(Panel(
        f"[bold green]Ace Browser launching on port {port}[/bold green]\n\n"
        "1. Navigate to your assignment and click [bold]Take Quiz / Begin Attempt[/bold]\n"
        "2. Come back to this terminal and run [bold cyan]ace run[/bold cyan]\n\n"
        "[dim]Profile: ~/.ace/browser-profile/ (cookies persist between sessions)[/dim]",
        title="[bold]ace launch[/bold]",
        border_style="green",
    ))

    proc = await asyncio.create_subprocess_exec(*args)
    try:
        await proc.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        proc.terminate()
        console.print("\n[dim]Browser closed.[/dim]")
    finally:
        try:
            if DEBUG_PORT_FILE.exists():
                DEBUG_PORT_FILE.unlink()
        except OSError:
            pass  # stale port file — non-fatal
