"""
Connects to the already-running Ace browser via Chrome DevTools Protocol.
Uses CDP /json/list to show the user what tabs are open, but controls
the already-open page directly — never navigates (which would break quiz sessions).
"""
import asyncio
import socket
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, Page, Playwright
from rich.console import Console
from rich.prompt import IntPrompt

from ace.config import DEBUG_PORT_FILE, get_settings

console = Console()

_SKIP_SCHEMES = ("chrome://", "chrome-extension://", "devtools://", "about:")


def _read_port() -> Optional[int]:
    if DEBUG_PORT_FILE.exists():
        try:
            return int(DEBUG_PORT_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _scan_for_port(start: int, end: int) -> Optional[int]:
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return port
        except OSError:
            continue
    return None


async def _list_tabs(port: int) -> list[dict]:
    """Return open user tabs via CDP HTTP API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{port}/json/list", timeout=3.0)
        resp.raise_for_status()
        tabs = resp.json()
    return [
        t for t in tabs
        if t.get("type") == "page"
        and not any(t.get("url", "").startswith(s) for s in _SKIP_SCHEMES)
        and t.get("url", "")
    ]


async def _real_url(page: Page) -> str:
    """Get the actual current URL from the page via JS (page.url may be stale on attach)."""
    try:
        return await asyncio.wait_for(
            page.evaluate("window.location.href"),
            timeout=3.0,
        )
    except Exception:
        return page.url or ""


async def get_assignment_page(target_url: Optional[str] = None) -> tuple[Playwright, Browser, Page]:
    settings = get_settings()

    port = _read_port()
    if port is None:
        console.print("[dim]→ Scanning for Ace browser...[/dim]")
        port = _scan_for_port(settings.debug_port_start, settings.debug_port_end)
    if port is None:
        console.print(
            "[bold red]Error:[/bold red] No Ace browser found.\n"
            "Run [bold cyan]ace launch[/bold cyan] first, then navigate to your assignment."
        )
        raise SystemExit(1)

    console.print(f"[dim]→ Connecting to browser on port {port}...[/dim]")
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}",
            timeout=8_000,
        )
    except Exception:
        await pw.stop()
        console.print(
            "[bold red]Error:[/bold red] Could not connect to the Ace browser.\n"
            "Make sure [bold cyan]ace launch[/bold cyan] is still running."
        )
        raise SystemExit(1)

    # The existing browser tab is in contexts[0].pages[0] —
    # page.url may be empty on attach, so we read it via JS.
    ctx = browser.contexts[0] if browser.contexts else None
    if not ctx or not ctx.pages:
        await browser.close()
        await pw.stop()
        console.print(
            "[bold red]Error:[/bold red] No open tabs found.\n"
            "Navigate to your assignment in the Ace browser, then run [bold cyan]ace run[/bold cyan]."
        )
        raise SystemExit(1)

    pages = [p for p in ctx.pages if not p.is_closed()]

    if target_url:
        page = pages[0]
        console.print(f"[dim]→ Navigating to: {target_url[:80]}[/dim]")
        try:
            await page.goto(target_url, wait_until="commit", timeout=20_000)
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] Could not load: {target_url} ({type(e).__name__}: {e})")
            await browser.close()
            await pw.stop()
            raise SystemExit(1)
        return pw, browser, page

    # Use CDP /json/list to show real tab titles, but control via the Playwright page object
    try:
        tabs = await _list_tabs(port)
    except Exception:
        tabs = []

    if not tabs:
        await browser.close()
        await pw.stop()
        console.print(
            "[bold red]Error:[/bold red] No open tabs found.\n"
            "Navigate to your assignment in the Ace browser, then run [bold cyan]ace run[/bold cyan]."
        )
        raise SystemExit(1)

    if len(tabs) == 1:
        chosen = tabs[0]
    else:
        console.print(f"\n[bold]Multiple tabs open — which has your assignment?[/bold]")
        for i, t in enumerate(tabs, 1):
            title = (t.get("title") or t.get("url", ""))[:60]
            url = t.get("url", "")[:60]
            console.print(f"  [cyan]{i}[/cyan]. {title} — [dim]{url}[/dim]")
        choice = IntPrompt.ask("Tab number", choices=[str(i) for i in range(1, len(tabs) + 1)])
        chosen = tabs[choice - 1]

    chosen_url = chosen.get("url", "")
    title = chosen.get("title") or chosen_url
    console.print(f"[dim]→ Loading tab: [bold]{title[:60]}[/bold][/dim]")
    console.print(f"[dim]  {chosen_url[:80]}[/dim]")

    # Navigate the Playwright page to the chosen URL.
    # wait_until="commit" returns as soon as navigation starts (not full load),
    # so Canvas quiz sessions are preserved — wait_for_question() handles the rest.
    page = pages[0]
    try:
        await page.goto(chosen_url, wait_until="commit", timeout=20_000)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Could not load tab ({type(e).__name__}: {e})")
        await browser.close()
        await pw.stop()
        raise SystemExit(1)

    return pw, browser, page
