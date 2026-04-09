"""
QuizLoop: platform-agnostic quiz solver.

3 LLM calls per page:
  1. scout()  — screenshot + text → PageScan (platform type + questions)
  2. answer() — questions → AnswerPlan (correct answers)
  3. verify() — screenshot → VerifyResult (selections confirmed + next action)

Playwright handles all clicking.
"""
import base64
import re

from playwright.async_api import Page
from rich.console import Console

from browser_use.llm.messages import (
    ContentPartImageParam,
    ContentPartTextParam,
    ImageURL,
    SystemMessage,
    UserMessage,
)

from ace.quiz.models import Answer, AnswerPlan, PageScan, Question, VerifyResult
from ace.quiz.prompts import ANSWER_PROMPT, SCOUT_PROMPT, VERIFY_PROMPT

console = Console()


class QuizLoop:
    def __init__(self, page: Page, llm) -> None:
        self.page = page
        self.llm = llm

    async def run(self) -> None:
        """Main loop. Runs until verify returns next_action='done'."""
        MAX_PAGES = 100  # safety cap

        for page_num in range(MAX_PAGES):
            console.print(f"[dim]→ Page {page_num + 1}: scanning...[/dim]")

            # 1. Scout
            scan = await self._scout()
            if not scan.questions:
                console.print("[bold red]No questions found on page. Stopping.[/bold red]")
                raise RuntimeError("No questions found on page")

            console.print(
                f"[dim]→ Platform: {scan.platform} | "
                f"{'all-on-page' if scan.all_on_page else 'one-at-a-time'} | "
                f"{len(scan.questions)} question(s)[/dim]"
            )

            # 2. Answer
            questions_to_answer = scan.questions
            answer_plan = await self._answer(questions_to_answer)

            # 3. Select
            await self._select(answer_plan, questions_to_answer)

            # 4. Verify (with retry)
            verify_result = await self._verify()
            retries = 0
            while not verify_result.all_correct and retries < 2:
                console.print(
                    f"[yellow]Verify found issues: {verify_result.issues}. Retrying...[/yellow]"
                )
                await self._select(answer_plan, questions_to_answer)
                verify_result = await self._verify()
                retries += 1

            if verify_result.issues:
                console.print(f"[yellow]Proceeding with issues: {verify_result.issues}[/yellow]")

            # 5. Navigate or stop
            if verify_result.next_action == "done":
                console.print("[dim]→ All questions answered.[/dim]")
                return

            await self._navigate(verify_result.next_action)

        raise RuntimeError(f"Quiz loop exceeded {MAX_PAGES} pages without completing")

    # ── LLM calls ─────────────────────────────────────────────────────────────

    async def _screenshot_b64(self) -> str:
        data = await self.page.screenshot(full_page=True)
        return base64.b64encode(data).decode()

    async def _page_text(self) -> str:
        try:
            return await self.page.inner_text("body")
        except Exception as e:
            console.print(f"[dim]Warning: could not extract page text: {e}[/dim]")
            return ""

    async def _scout(self) -> PageScan:
        b64 = await self._screenshot_b64()
        text = await self._page_text()
        messages = [
            SystemMessage(content=SCOUT_PROMPT),
            UserMessage(content=[
                ContentPartTextParam(text=f"Page text (first 3000 chars):\n{text[:3000]}"),
                ContentPartImageParam(
                    image_url=ImageURL(url=f"data:image/png;base64,{b64}", detail="high")
                ),
            ]),
        ]
        result = await self.llm.ainvoke(messages, output_format=PageScan)
        return result.completion

    async def _answer(self, questions: list[Question]) -> AnswerPlan:
        questions_text = "\n\n".join(
            f"[{q.id}] {q.text}\nOptions: {', '.join(q.options) if q.options else '(free text)'}"
            for q in questions
        )
        messages = [
            SystemMessage(content=ANSWER_PROMPT),
            UserMessage(content=f"Answer these questions:\n\n{questions_text}"),
        ]
        result = await self.llm.ainvoke(messages, output_format=AnswerPlan)
        return result.completion

    # ── Browser interactions ───────────────────────────────────────────────────

    async def _select(self, plan: AnswerPlan, questions: list[Question]) -> None:
        q_map = {q.id: q for q in questions}
        for ans in plan.answers:
            question = q_map.get(ans.question_id)
            if question is None:
                continue
            if question.kind in ("mcq", "truefalse"):
                await self._click_option(ans.value)
            elif question.kind == "text":
                await self._fill_text(ans.value)

    async def _click_option(self, option_text: str) -> None:
        """Click a radio/checkbox option by its label text."""
        # Strategy 1: label containing exact option text
        label = self.page.locator("label").filter(has_text=option_text).first
        try:
            await label.wait_for(state="visible", timeout=3_000)
            await label.click()
            return
        except Exception:
            pass

        # Strategy 2: any element with that exact text
        el = self.page.get_by_text(option_text, exact=True).first
        try:
            await el.wait_for(state="visible", timeout=3_000)
            await el.click()
            return
        except Exception:
            pass

        # Strategy 3: list item or option container that wraps the text
        el = self.page.locator("li, .answer, .option, .choice").filter(has_text=option_text).first
        try:
            await el.wait_for(state="visible", timeout=3_000)
            await el.click()
            return
        except Exception:
            pass

        console.print(f"[yellow]Warning: could not find option '{option_text[:60]}' to click[/yellow]")

    async def _fill_text(self, value: str) -> None:
        """Fill the first visible text input or textarea."""
        for selector in ("textarea:visible", "input[type='text']:visible", "input:not([type]):visible"):
            el = self.page.locator(selector).first
            try:
                await el.wait_for(state="visible", timeout=3_000)
                await el.fill(value)
                return
            except Exception:
                continue
        console.print(f"[yellow]Warning: could not find text input to fill[/yellow]")

    async def _verify(self) -> VerifyResult:
        b64 = await self._screenshot_b64()
        text = await self._page_text()
        messages = [
            SystemMessage(content=VERIFY_PROMPT),
            UserMessage(content=[
                ContentPartTextParam(text=f"Page text:\n{text[:2000]}"),
                ContentPartImageParam(
                    image_url=ImageURL(url=f"data:image/png;base64,{b64}", detail="high")
                ),
            ]),
        ]
        result = await self.llm.ainvoke(messages, output_format=VerifyResult)
        return result.completion

    async def _navigate(self, action: str) -> None:
        if action == "check":
            candidates = ["Check Answer", "Check My Answer", "Check", "Submit Answer"]
        elif action == "next":
            candidates = ["Next Question", "Next", "Continue", "Next >"]
        else:
            return

        for name in candidates:
            btn = self.page.get_by_role("button", name=re.compile(re.escape(name), re.IGNORECASE))
            try:
                if await btn.count() > 0:
                    await btn.click()
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass  # page transition may not reach networkidle (SPAs with long-polling)
                    return
            except Exception:
                continue

        console.print(f"[yellow]Warning: could not find '{action}' button[/yellow]")
