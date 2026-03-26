"""
Browser manager.

Strategy:
  1. If a saved debug port exists and the browser is still alive → reconnect via CDP.
  2. Otherwise → launch a fresh Chromium subprocess with a debug port, then connect.

The browser is kept alive between runs so the user doesn't have to re-navigate.
"""
import asyncio
import socket
import subprocess
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from rich.console import Console
from rich.prompt import IntPrompt

from ace.config import BROWSER_PROFILE_DIR, DEBUG_PORT_FILE, get_settings

console = Console()

_SKIP_URLS = ("about:blank", "", "chrome://newtab/", "about:newtab")
_SKIP_SCHEMES = ("chrome://", "chrome-extension://", "devtools://", "about:")


# ── Port helpers ──────────────────────────────────────────────────────────────

def _read_saved_port() -> Optional[int]:
    try:
        return int(DEBUG_PORT_FILE.read_text().strip())
    except Exception:
        return None


def _find_free_port(start: int = 9222, end: int = 9232) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port  # port is free
    raise RuntimeError("No free port found in range 9222-9232.")


async def _is_browser_alive(port: int) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
            return r.status_code == 200
    except Exception:
        return False


# ── Singleton locks ───────────────────────────────────────────────────────────

def _clear_profile_locks() -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lf = BROWSER_PROFILE_DIR / name
        try:
            if lf.is_symlink() or lf.exists():
                lf.unlink()
        except OSError:
            pass


# ── Chromium path ─────────────────────────────────────────────────────────────

async def _chromium_executable() -> str:
    pw = await async_playwright().start()
    path = pw.chromium.executable_path
    await pw.stop()
    return path


# ── Page selection helpers ────────────────────────────────────────────────────

async def _real_url(page: Page) -> str:
    try:
        return await asyncio.wait_for(page.evaluate("window.location.href"), timeout=2.0)
    except Exception:
        return page.url or ""


def _is_usable(url: str) -> bool:
    return bool(url) and url not in _SKIP_URLS and not any(url.startswith(s) for s in _SKIP_SCHEMES)


async def _resolve_urls(pages: list[Page]) -> dict[Page, str]:
    urls = await asyncio.gather(*(_real_url(p) for p in pages))
    return dict(zip(pages, urls))


async def _pick_page(ctx: BrowserContext, target_url: Optional[str] = None) -> Page:
    pages = [p for p in ctx.pages if not p.is_closed()]
    if not pages:
        page = await ctx.new_page()
        pages = [page]

    if target_url:
        page = pages[0]
        console.print(f"[dim]→ Navigating to: {target_url[:80]}[/dim]")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
        return page

    url_map = await _resolve_urls(pages)
    usable = [p for p in pages if _is_usable(url_map[p])]

    if not usable:
        console.print(
            "\n[bold green]Browser is open.[/bold green]\n"
            "Navigate to your assignment and click [bold]Take Quiz / Begin Attempt[/bold].\n"
            "Then come back here and press [bold]Enter[/bold]."
        )
        try:
            input("\n  Press Enter when you're on the question page... ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)
        await asyncio.sleep(0.5)
        pages = [p for p in ctx.pages if not p.is_closed()]
        url_map = await _resolve_urls(pages)
        usable = [p for p in pages if _is_usable(url_map[p])]

    if not usable:
        console.print(
            "[bold red]Error:[/bold red] Browser is still on a blank page.\n"
            "Navigate to your assignment first, then re-run [bold cyan]ace run[/bold cyan]."
        )
        raise SystemExit(1)

    if len(usable) == 1:
        page = usable[0]
        console.print(f"[dim]→ Using tab: {url_map[page][:80]}[/dim]")
        return page

    console.print("\n[bold]Multiple tabs open — which has your assignment?[/bold]")
    for i, p in enumerate(usable, 1):
        try:
            title = await asyncio.wait_for(p.title(), timeout=2.0)
        except Exception:
            title = url_map[p]
        console.print(f"  [cyan]{i}[/cyan]. {title[:60]} — [dim]{url_map[p][:60]}[/dim]")
    choice = IntPrompt.ask("Tab number", choices=[str(i) for i in range(1, len(usable) + 1)])
    page = usable[choice - 1]
    console.print(f"[dim]→ Using tab: {url_map[page][:80]}[/dim]")
    return page


# ── Main entry point ──────────────────────────────────────────────────────────

async def open_browser_and_wait(
    target_url: Optional[str] = None,
) -> tuple[Playwright, BrowserContext, Page]:
    """
    Returns (playwright, context, page) ready for interaction.
    Reuses an existing browser if one is running; launches a new one otherwise.
    The caller should NOT close the browser after finishing — leave it alive for the next run.
    """
    settings = get_settings()
    port = _read_saved_port()

    if port and await _is_browser_alive(port):
        console.print(f"[dim]→ Reconnecting to existing browser on port {port}...[/dim]")
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", timeout=8_000
            )
        except Exception as e:
            console.print(f"[dim]→ Could not reconnect ({e}) — launching fresh browser...[/dim]")
            await pw.stop()
            return await _launch_fresh(target_url, settings)

        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await _pick_page(ctx, target_url)
        return pw, ctx, page

    return await _launch_fresh(target_url, settings)


async def _launch_fresh(target_url, settings) -> tuple[Playwright, BrowserContext, Page]:
    _clear_profile_locks()

    port = _find_free_port(settings.debug_port_start, settings.debug_port_end)
    chromium = await _chromium_executable()

    console.print("[dim]→ Launching browser...[/dim]")
    subprocess.Popen(
        [
            chromium,
            f"--user-data-dir={BROWSER_PROFILE_DIR}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for browser to be ready
    for _ in range(20):
        if await _is_browser_alive(port):
            break
        await asyncio.sleep(0.3)
    else:
        raise RuntimeError("Browser did not start in time.")

    DEBUG_PORT_FILE.write_text(str(port))
    console.print(f"[dim]→ Browser ready on port {port}[/dim]")

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=8_000)
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()

    if not target_url:
        console.print(
            "\n[bold green]Browser is open.[/bold green]\n"
            "Navigate to your assignment and click [bold]Take Quiz / Begin Attempt[/bold].\n"
            "Then come back here and press [bold]Enter[/bold]."
        )
        try:
            input("\n  Press Enter when you're on the question page... ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)
        await asyncio.sleep(0.5)

    page = await _pick_page(ctx, target_url)
    return pw, ctx, page
