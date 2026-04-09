"""
Orchestrator: opens the browser, runs QuizLoop, gates final submission.
"""
import re
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from ace.browser.manager import open_browser_and_wait
from ace.browser.lock import run_lock
from ace.browser.utils import wait_for_question
from ace.config import get_settings
from ace.quiz import QuizLoop

console = Console()


def _make_llm(settings):
    provider = settings.provider.lower()
    if provider == "groq":
        from ace.llm.groq_compat import GroqCompat
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set. Run: ace config set groq_api_key <key>")
        return GroqCompat(model=settings.groq_model, api_key=settings.groq_api_key)
    elif provider == "deepseek":
        from browser_use import ChatDeepSeek
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set. Run: ace config set deepseek_api_key <key>")
        return ChatDeepSeek(model=settings.deepseek_model, api_key=settings.deepseek_api_key)
    else:  # anthropic
        from browser_use import ChatAnthropic
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set. Run: ace config set anthropic_api_key <key>")
        return ChatAnthropic(model=settings.anthropic_model, api_key=settings.anthropic_api_key)


class Orchestrator:
    def __init__(self, dry_run: bool = False, auto_submit: bool = False) -> None:
        self.dry_run = dry_run
        self.auto_submit = auto_submit

    async def run(self, target_url: Optional[str] = None) -> None:
        with run_lock():
            await self._run(target_url)

    async def _run(self, target_url: Optional[str] = None) -> None:
        from playwright.async_api import Error as PlaywrightError

        pw, ctx, page = await open_browser_and_wait(target_url)

        try:
            if self.dry_run:
                console.print("→ Mode: [yellow]DRY RUN[/yellow] — answers filled, submit blocked")

            console.print("[dim]→ Checking for questions on page...[/dim]")
            if not await wait_for_question(page, timeout=8_000):
                console.print(
                    "[bold red]Error:[/bold red] No question detected on this page.\n"
                    "Make sure you've opened the assignment before running ace."
                )
                raise SystemExit(1)

            settings = get_settings()
            console.rule("[dim]Starting[/dim]")

            llm = _make_llm(settings)
            active_model = {
                "groq": settings.groq_model,
                "deepseek": settings.deepseek_model,
                "anthropic": settings.anthropic_model,
            }.get(settings.provider, "")
            console.print(f"[dim]→ Provider: {settings.provider} / {active_model}[/dim]")

            quiz_loop = QuizLoop(page, llm)

            if self.dry_run:
                await quiz_loop.run()
                console.rule("[dim]Done[/dim]")
                console.print("[yellow]DRY RUN complete — submit manually in the browser when ready.[/yellow]")
                return

            await quiz_loop.run()
            console.rule("[dim]Done[/dim]")

            if not self.auto_submit:
                console.print(Panel(
                    "[bold]All questions answered.[/bold]\n\n"
                    "Type [bold red]submit[/bold red] to confirm final submission, "
                    "or [bold]Ctrl+C[/bold] to cancel.",
                    border_style="red",
                    title="⚠  Final Submission",
                ))
                try:
                    confirm = input("  Type 'submit' to confirm: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Submission cancelled.[/dim]")
                    return
                if confirm != "submit":
                    console.print("[dim]Submission cancelled. Submit manually in the browser.[/dim]")
                    return

            console.print("[dim]→ Submitting...[/dim]")
            submit_locator = page.get_by_role(
                "button",
                name=re.compile(r"submit|finish|done", re.IGNORECASE),
            )
            if await submit_locator.count() > 0:
                await submit_locator.first.click()
                console.print("[green]Submitted.[/green]")
            else:
                console.print("[yellow]Submit button not found — submit manually in the browser.[/yellow]")

        except SystemExit:
            raise
        except PlaywrightError as e:
            err = str(e)
            if any(m in err for m in ("closed", "Target closed", "has been closed")):
                console.print("[bold red]Error:[/bold red] Browser tab closed mid-session.")
            else:
                console.print(f"[bold red]Browser error:[/bold red] {e}")
        except RuntimeError as e:
            console.print(f"[bold red]Stopped:[/bold red] {e}")
        finally:
            try:
                await pw.stop()
            except Exception:
                pass
