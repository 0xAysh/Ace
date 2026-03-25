"""
Orchestrator: opens the browser, runs the browser-use Agent, gates final submission.
"""
import asyncio
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from ace.browser.manager import open_browser_and_wait
from ace.browser.lock import run_lock
from ace.browser.utils import wait_for_question
from ace.config import DEBUG_PORT_FILE, get_settings

console = Console()

TASK = """\
Complete ALL questions in this academic assignment that is already open in the browser.

For EACH question:
1. Read the question and all available answer options carefully
2. Determine the correct answer using your knowledge
3. Select the answer — click the correct radio button for MCQ/True-False, or type into the text field
4. After selecting, look for a "Check Answer" or "Check My Answer" button and click it if present
5. If a popup or warning dialog appears (e.g. "Are you sure you want to continue?"), click "No" to go back and check the answer properly
6. Click "Next", "Next Question", or "Continue" to move to the next question
7. Repeat for every question until all are answered

You are DONE when you have answered all questions and see only a Submit/Finish button with no more Next buttons.
DO NOT click Submit, Finish, Done, or any final submission button — stop just before that point.
"""


async def _stop_browser(browser) -> None:
    try:
        await browser.stop()
    except Exception:
        try:
            await browser.kill()
        except Exception:
            pass


def _make_llm(settings):
    provider = settings.provider.lower()
    if provider == "groq":
        from browser_use import ChatGroq
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set. Run: ace config set groq_api_key <key>")
        return ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key)
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


def _make_browser(port: int) -> "Browser":
    from browser_use import Browser
    return Browser(
        cdp_url=f"http://127.0.0.1:{port}",
        headless=False,
        disable_security=True,
        cross_origin_iframes=True,
        keep_alive=True,
    )


class Orchestrator:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    async def run(self, target_url: Optional[str] = None) -> None:
        with run_lock():
            await self._run(target_url)

    async def _run(self, target_url: Optional[str] = None) -> None:
        from browser_use import Agent
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

            port = int(DEBUG_PORT_FILE.read_text().strip())
            settings = get_settings()

            # Close our playwright connection — browser-use will make its own
            await pw.stop()
            pw = None

            console.rule("[dim]Starting[/dim]")

            llm = _make_llm(settings)
            console.print(f"[dim]→ Provider: {settings.provider} / {getattr(settings, settings.provider + '_model', '')}[/dim]")

            task = TASK
            if self.dry_run:
                task += "\nIMPORTANT: This is a dry run — do NOT click Submit or Finish under any circumstances."

            browser = _make_browser(port)
            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
                use_vision=True,
                use_thinking=False,
            )

            try:
                result = await agent.run()
                summary = str(result)
            finally:
                await _stop_browser(browser)

            console.rule("[dim]Done[/dim]")
            console.print(f"[dim]{summary[:300]}[/dim]")

            if self.dry_run:
                console.print("[yellow]DRY RUN complete — submit manually in the browser when ready.[/yellow]")
                return

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

            if confirm == "submit":
                console.print("[dim]→ Clicking Submit in browser...[/dim]")
                browser2 = _make_browser(port)
                submit_agent = Agent(
                    task="Click the final Submit or Finish button to submit the assignment now.",
                    llm=llm,
                    browser=browser2,
                )
                try:
                    await submit_agent.run()
                finally:
                    await browser2.close()
            else:
                console.print("[dim]Submission cancelled. Submit manually in the browser.[/dim]")

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
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
