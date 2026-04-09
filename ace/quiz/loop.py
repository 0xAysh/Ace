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
            # Retry re-selects the same answers (click failures, not reasoning errors).
            # If the answer plan itself is wrong, manual review is needed.
            while not verify_result.all_correct and retries < 2:
                console.print(
                    f"[yellow]Verify found issues: {verify_result.issues}. Retrying...[/yellow]"
                )
                await self._select(answer_plan, questions_to_answer)
                verify_result = await self._verify()
                retries += 1

            if not verify_result.all_correct:
                console.print(f"[yellow]Warning: proceeding after retry exhaustion — answers may be wrong: {verify_result.issues}[/yellow]")
            elif verify_result.issues:
                console.print(f"[dim]Minor issues noted (proceeding): {verify_result.issues}[/dim]")

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
        """Extract text from the page including iframe content."""
        parts = []
        try:
            parts.append(await self.page.inner_text("body"))
        except Exception:
            pass
        # Also collect text from iframes (Pearson MyLab, etc.)
        for frame in self.page.frames[1:]:  # skip main frame
            try:
                parts.append(await frame.inner_text("body"))
            except Exception:
                pass
        return "\n".join(parts)

    def _all_frames(self):
        """Return main frame + all child frames."""
        return self.page.frames

    async def _active_frame(self):
        """Return the frame with the most interactive inputs, or main_frame if none."""
        best_frame = self.page.main_frame
        best_count = 0
        for frame in self.page.frames:
            try:
                count = await frame.evaluate("""
                    () => document.querySelectorAll(
                        'input[type="radio"], input[type="checkbox"],
                         input[type="text"], textarea'
                    ).length
                """)
                if count > best_count:
                    best_count = count
                    best_frame = frame
            except Exception:
                pass
        return best_frame

    def _parse_option_letter(self, option_text: str) -> str | None:
        """Extract uppercase letter from 'A. foo' → 'A', or None for 'True'."""
        m = re.match(r'^([A-Za-z])\.\s', option_text)
        return m.group(1).upper() if m else None

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
                await self._click_option(ans.value if isinstance(ans.value, str) else ans.value[0])
            elif question.kind == "multi":
                values = ans.value if isinstance(ans.value, list) else [ans.value]
                for v in values:
                    await self._click_option(v)
            elif question.kind == "text":
                await self._fill_text(ans.value if isinstance(ans.value, str) else ans.value[0])

    async def _click_option(self, option_text: str) -> None:
        """Click a radio/checkbox option by its label text.

        Searches main frame + all iframes. Handles platforms like Pearson MyLab
        that render double spaces between the letter and answer text.
        """
        # Also try without the "A. " / "D. " letter prefix — more lenient match
        stripped = re.sub(r'^[A-Za-z]\.\s+', '', option_text)
        texts = [option_text] if stripped == option_text else [option_text, stripped]

        # Regex that collapses any whitespace run — handles "D.  $75,000" vs "D. $75,000"
        ws_pattern = re.compile(
            r'\s+'.join(re.escape(w) for w in option_text.split()), re.IGNORECASE
        )

        selectors = [
            "label",
            "[role='radio'], [role='checkbox'], [role='option']",
            "li, .answer, .option, .choice, .answer-choice, .response",
        ]

        for frame in self._all_frames():
            # Playwright locator strategies (exact text + stripped prefix)
            for text in texts:
                for sel in selectors:
                    el = frame.locator(sel).filter(has_text=text).first
                    try:
                        await el.wait_for(state="visible", timeout=1_000)
                        await el.click()
                        return
                    except Exception:
                        pass

            # Regex strategy — whitespace-flexible (catches Pearson double-space)
            for sel in selectors:
                el = frame.locator(sel).filter(has_text=ws_pattern).first
                try:
                    await el.wait_for(state="visible", timeout=1_000)
                    await el.click()
                    return
                except Exception:
                    pass

            # JS fallback — normalize all whitespace in the DOM and compare
            try:
                clicked = await frame.evaluate(
                    """(text) => {
                        const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
                        const target = norm(text);
                        const sels = ['label', '[role="radio"]', '[role="checkbox"]',
                                      '[role="option"]', 'li'];
                        for (const sel of sels) {
                            for (const el of document.querySelectorAll(sel)) {
                                if (norm(el.textContent).includes(target)) {
                                    el.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    }""",
                    option_text,
                )
                if clicked:
                    return
            except Exception:
                pass

        console.print(f"[yellow]Warning: could not find option '{option_text[:60]}' to click[/yellow]")

    async def _fill_text(self, value: str) -> None:
        """Fill the first visible text input or textarea, searching all frames."""
        for frame in self._all_frames():
            for selector in ("textarea", "input[type='text']", "input:not([type])"):
                el = frame.locator(selector).first
                try:
                    await el.wait_for(state="visible", timeout=1_500)
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

        pattern = re.compile('|'.join(re.escape(n) for n in candidates), re.IGNORECASE)

        for frame in self._all_frames():
            # Playwright role-based
            btn = frame.get_by_role("button", name=pattern)
            try:
                if await btn.count() > 0:
                    await btn.first.click()
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

            # JS fallback — find button by normalized text
            try:
                clicked = await frame.evaluate(
                    """(names) => {
                        const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
                        for (const el of document.querySelectorAll('button, [role="button"], input[type="submit"]')) {
                            const t = norm(el.textContent || el.value || '');
                            if (names.some(n => t.includes(n.toLowerCase()))) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""",
                    candidates,
                )
                if clicked:
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

        console.print(f"[yellow]Warning: could not find '{action}' button[/yellow]")
