"""
Main orchestrator: connects to browser, loops through questions,
handles interactive/auto mode and final submission gate.
"""
import asyncio
from typing import Optional

from playwright.async_api import Error as PlaywrightError
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box

from ace.browser.manager import open_browser_and_wait
from ace.browser.lock import run_lock
from ace.browser.utils import screenshot_element, wait_for_question
from ace.llm.engine import LLMEngine
from ace.platforms.base import BasePlatform, Question, QuestionType, SubmissionResult
from ace.platforms.canvas import CanvasAdapter
from ace.platforms.pearson import PearsonAdapter
from ace.platforms.generic import GenericAdapter

console = Console()

_ADAPTERS: list[type[BasePlatform]] = [PearsonAdapter, CanvasAdapter, GenericAdapter]


async def _pick_adapter(page) -> BasePlatform:
    for cls in _ADAPTERS:
        adapter = cls()
        if await adapter.is_assignment_page(page):
            return adapter
    return GenericAdapter()


def _format_question_panel(q: Question, response, is_last: bool) -> Panel:
    last_label = "  [bold red]⚠  LAST QUESTION — FINAL ANSWER[/bold red]" if is_last else ""
    conf_color = "green" if response.confidence >= 0.75 else "yellow" if response.confidence >= 0.5 else "red"

    options_text = ""
    if q.options:
        options_text = "\n" + "\n".join(f"  [dim]{o.label}.[/dim] {o.text}" for o in q.options)

    body = (
        f"[bold]Q{q.number} of {q.total}[/bold]  [{conf_color}]{response.confidence:.0%} confidence[/{conf_color}]"
        f"  [dim]{q.type.value}[/dim]{last_label}\n\n"
        f"[white]{q.text[:400]}[/white]"
        f"{options_text}\n\n"
        f"[bold green]→ Answer:[/bold green] {response.answer}\n"
        f"[dim]Reasoning: {response.reasoning[:200]}[/dim]"
    )
    if response.needs_human_review:
        body += "\n\n[bold yellow]⚠  Low confidence — please review before accepting[/bold yellow]"

    border = "red" if is_last else ("yellow" if response.needs_human_review else "cyan")
    return Panel(body, border_style=border, padding=(1, 2))


def _print_summary(answered: list[tuple[Question, str]]) -> None:
    table = Table(title="Quiz Summary", box=box.SIMPLE_HEAD, show_lines=False)
    table.add_column("Q", style="dim", width=4)
    table.add_column("Type", style="dim")
    table.add_column("Answer", style="green")
    for q, ans in answered:
        table.add_row(str(q.number), q.type.value, ans[:60])
    console.print(table)


class Orchestrator:
    def __init__(self, auto: bool = False, timed: bool = False, dry_run: bool = False) -> None:
        self.auto = auto
        self.timed = timed
        self.dry_run = dry_run
        self.engine = LLMEngine()
        self.answered: list[tuple[Question, str]] = []

    async def run(self, target_url: Optional[str] = None) -> None:
        with run_lock():
            await self._run(target_url)

    async def _run(self, target_url: Optional[str] = None) -> None:
        pw, ctx, page = await open_browser_and_wait(target_url)

        try:
            console.print("[dim]→ Checking for questions on page...[/dim]")
            adapter = await _pick_adapter(page)
            console.print(f"[dim]→ Using adapter: [bold]{adapter.name}[/bold][/dim]")
            if not await wait_for_question(page, timeout=5_000):
                console.print(
                    "[bold red]Error:[/bold red] No question detected on this page.\n"
                    "Make sure you've clicked [bold]Take Quiz / Begin Attempt[/bold] before running ace."
                )
                raise SystemExit(1)

            mode_parts = []
            if self.dry_run:
                mode_parts.append("[yellow]DRY RUN[/yellow] — answers selected in browser, submit blocked")
            if self.auto:
                mode_parts.append("[cyan]AUTO[/cyan] — filling automatically, final submit always requires approval")
            if mode_parts:
                console.print("→ Mode: " + " | ".join(mode_parts))

            console.rule("[dim]Starting quiz[/dim]")
            # Detect layout: count how many question containers are on the page right now
            q_count = 1
            if hasattr(adapter, "count_questions_on_page"):
                q_count = await adapter.count_questions_on_page(page)
            console.print(f"[dim]→ {q_count} question container(s) detected on page[/dim]")

            if q_count > 1:
                console.print("[dim]→ All-on-one-page layout — extracting all questions[/dim]")
                await self._loop_all_on_page(page, adapter)
            else:
                await self._loop(page, adapter)

        except SystemExit:
            raise
        except PlaywrightError as e:
            err = str(e)
            if any(msg in err for msg in ("closed", "Target closed", "has been closed", "Browser closed")):
                console.print(
                    "\n[bold red]Error:[/bold red] The browser tab was closed mid-session.\n"
                    f"Answered {len(self.answered)} question(s) before stopping.\n"
                    "Re-open the assignment tab and run [bold cyan]ace run[/bold cyan] again."
                )
            else:
                console.print(f"[bold red]Browser error:[/bold red] {e}")
        except RuntimeError as e:
            console.print(f"[bold red]Stopped:[/bold red] {e}")
        finally:
            # Keep the browser alive for the next run — just disconnect Playwright's wire.
            # The browser process continues running; next ace run will reconnect to it.
            try:
                await pw.stop()
            except Exception:
                pass

        cost = self.engine.cost
        if self.answered:
            console.rule("[dim]Done[/dim]")
            console.print(
                f"[dim]{len(self.answered)} question(s) answered  |  "
                f"~{cost.input_tokens + cost.output_tokens:,} tokens  |  "
                f"est. ${cost.estimate_cost_usd():.4f}[/dim]"
            )

    async def _loop_all_on_page(self, page, adapter) -> None:
        """Handle quizzes where all questions are visible on one page."""
        questions = await adapter.extract_all_questions(page)
        if not questions:
            console.print("[bold red]Error:[/bold red] No questions found on page.")
            raise SystemExit(1)

        for i, question in enumerate(questions):
            is_last = (i == len(questions) - 1)

            console.print(f"\n[bold]─── Q{question.number} of {question.total} ───[/bold]")
            if question.type == QuestionType.UNKNOWN:
                console.print(f"[yellow]  ⚠ Q{question.number} type unrecognised — answer manually.[/yellow]")

            image_bytes = None
            if question.has_image and question.image_selector:
                try:
                    image_bytes = await screenshot_element(page, question.image_selector)
                except Exception:
                    pass

            with console.status(f"[dim]Asking Claude about Q{question.number}...[/dim]"):
                response = await self.engine.answer_question(question, context_chunks=[], image_bytes=image_bytes)

            console.print(_format_question_panel(question, response, is_last))
            final_answer = response.answer

            if not self.auto or is_last:
                action = self._prompt_user(question, response, is_last)
                if action == "skip":
                    console.print("[dim]  → Skipped.[/dim]")
                    continue
                if action == "edit":
                    final_answer = self._prompt_edit()
                    console.print(f"[dim]  → Using edited answer: {final_answer[:60]}[/dim]")
            else:
                console.print(f"[green]  ✓ Q{question.number}:[/green] {final_answer[:60]}")

            await adapter.fill_answer(page, question, final_answer)
            self.answered.append((question, final_answer))

        # All questions answered — submit gate
        _print_summary(self.answered)
        if self.dry_run:
            console.print("[yellow]DRY RUN — answers selected above. Submit manually when ready.[/yellow]")
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
            result = await adapter.click_submit(page)
            if result.success:
                console.print(f"[bold green]✓ {result.message}[/bold green]")
            else:
                console.print(f"[bold red]✗ {result.message}[/bold red]")
        else:
            console.print("[dim]Submission cancelled. Submit manually in the browser.[/dim]")

    async def _loop(self, page, adapter) -> None:
        while True:
            question = await adapter.extract_current_question(page)
            is_last = await adapter.is_last_question(page)

            if is_last:
                console.print(f"\n[bold red]─── Last Question (Q{question.number}) ───[/bold red]")
            else:
                console.print(f"\n[bold]─── Q{question.number} of {question.total} ───[/bold]")

            if question.type == QuestionType.UNKNOWN:
                console.print(
                    f"[yellow]  ⚠ Q{question.number} has an unrecognised question type — you may need to answer manually.[/yellow]"
                )

            image_bytes = None
            if question.has_image and question.image_selector:
                console.print("[dim]  → Capturing question image...[/dim]")
                try:
                    image_bytes = await screenshot_element(page, question.image_selector)
                    console.print("[dim]  → Image captured.[/dim]")
                except Exception:
                    console.print("[dim]  → Could not capture question image — proceeding without it.[/dim]")

            with console.status(f"[dim]Asking Claude about Q{question.number}...[/dim]"):
                response = await self.engine.answer_question(
                    question,
                    context_chunks=[],
                    image_bytes=image_bytes,
                )

            console.print(_format_question_panel(question, response, is_last))

            final_answer = response.answer

            # ── Interactive mode OR last question ─────────────────────────────
            if not self.auto or is_last:
                action = self._prompt_user(question, response, is_last)

                if action == "skip":
                    console.print("[dim]  → Skipped.[/dim]")
                    if is_last:
                        break
                    await adapter.click_next(page)
                    await adapter.wait_for_next_question(page)
                    continue

                if action == "edit":
                    final_answer = self._prompt_edit()
                    console.print(f"[dim]  → Using edited answer: {final_answer[:60]}[/dim]")

                await adapter.fill_answer(page, question, final_answer)
                self.answered.append((question, final_answer))

                if is_last:
                    _print_summary(self.answered)
                    if self.dry_run:
                        console.print("[yellow]DRY RUN — answers selected above. Submit manually when ready.[/yellow]")
                        break
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
                        break

                    if confirm == "submit":
                        result = await adapter.click_submit(page)
                        if result.success:
                            console.print(f"[bold green]✓ {result.message}[/bold green]")
                        else:
                            console.print(f"[bold red]✗ {result.message}[/bold red]")
                    else:
                        console.print("[dim]Submission cancelled. You can submit manually in the browser.[/dim]")
                    break
                else:
                    await adapter.click_next(page)
                    await adapter.wait_for_next_question(page)

            # ── Auto mode (not last question) ─────────────────────────────────
            else:
                await adapter.fill_answer(page, question, final_answer)
                self.answered.append((question, final_answer))
                console.print(f"[green]  ✓ Q{question.number}:[/green] {final_answer[:60]}")
                await adapter.click_next(page)
                await adapter.wait_for_next_question(page)

    def _prompt_user(self, question: Question, response, is_last: bool) -> str:
        console.print("  [Enter] accept | [e] edit | [s] skip")
        try:
            raw = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)
        if raw == "e":
            return "edit"
        if raw == "s":
            return "skip"
        return "accept"

    def _prompt_edit(self) -> str:
        while True:
            try:
                val = input("  Enter your answer: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise SystemExit(0)
            if val:
                return val
            console.print("  [yellow]Answer cannot be empty. Try again.[/yellow]")
