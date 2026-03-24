import asyncio
import typer
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def debug_cmd() -> None:
    """Show what Ace sees on the current browser tab (for troubleshooting)."""
    asyncio.run(_debug())


async def _debug() -> None:
    from ace.config import DEBUG_PORT_FILE, get_settings
    from playwright.async_api import async_playwright
    import httpx

    settings = get_settings()
    port = None
    try:
        port = int(DEBUG_PORT_FILE.read_text().strip())
    except Exception:
        pass
    if port is None:
        import socket
        for p in range(settings.debug_port_start, settings.debug_port_end + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", p)) == 0:
                    port = p
                    break
    if port is None:
        console.print("[red]No Ace browser found. Run ace run first.[/red]")
        return

    console.print(f"[dim]Browser on port {port}[/dim]")

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=8_000)
        ctx = browser.contexts[0] if browser.contexts else None
        if not ctx or not ctx.pages:
            console.print("[red]No pages found.[/red]")
            return

        for pi, page in enumerate(ctx.pages):
            try:
                real_url = await asyncio.wait_for(page.evaluate("window.location.href"), timeout=2.0)
            except Exception:
                real_url = page.url or "(blank)"

            console.print(f"\n[bold cyan]Tab {pi + 1}:[/bold cyan] {real_url[:100]}")

            if real_url.startswith(("about:", "chrome://")):
                continue

            # Top-level page stats
            try:
                info = await asyncio.wait_for(page.evaluate("""() => ({
                    radios:    document.querySelectorAll("input[type='radio']").length,
                    texts:     document.querySelectorAll("input[type='text']").length,
                    textareas: document.querySelectorAll("textarea").length,
                    iframes:   document.querySelectorAll("iframe").length,
                    bodyLen:   document.body?.innerText?.length ?? 0,
                    snippet:   document.body?.innerText?.slice(0, 400) ?? "",
                })"""), timeout=3.0)
                console.print(
                    f"  [dim]top-level:[/dim] "
                    f"radio={info['radios']} text={info['texts']} "
                    f"textarea={info['textareas']} iframes={info['iframes']} "
                    f"bodyLen={info['bodyLen']}"
                )
                if info["snippet"]:
                    console.print(f"  [dim]{info['snippet'][:300]}[/dim]")
            except Exception as e:
                console.print(f"  [yellow]Could not read top-level: {e}[/yellow]")

            # All frames
            frames = [f for f in page.frames if f != page.main_frame]
            if frames:
                console.print(f"\n  [bold]{len(frames)} iframe(s):[/bold]")
                for fi, frame in enumerate(frames):
                    try:
                        finfo = await asyncio.wait_for(frame.evaluate("""() => ({
                            radios:    document.querySelectorAll("input[type='radio']").length,
                            texts:     document.querySelectorAll("input[type='text']").length,
                            textareas: document.querySelectorAll("textarea").length,
                            bodyLen:   document.body?.innerText?.length ?? 0,
                            snippet:   document.body?.innerText?.slice(0, 500) ?? "",
                        })"""), timeout=2.0)
                        has_inputs = finfo["radios"] or finfo["texts"] or finfo["textareas"]
                        marker = "[green]← HAS INPUTS[/green]" if has_inputs else ""
                        console.print(
                            f"    frame {fi+1}: {frame.url[:80]} {marker}\n"
                            f"      radio={finfo['radios']} text={finfo['texts']} "
                            f"textarea={finfo['textareas']} bodyLen={finfo['bodyLen']}"
                        )
                        if finfo["snippet"]:
                            console.print(f"      [dim]{finfo['snippet'][:400]}[/dim]")
                    except Exception as e:
                        console.print(f"    frame {fi+1}: {frame.url[:60]} — [yellow]{e}[/yellow]")

        await browser.close()
    finally:
        await pw.stop()
