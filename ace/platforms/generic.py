"""
Generic platform adapter — falls back to LLM-guided DOM detection
for unknown or unsupported platforms.
"""
import re
from playwright.async_api import Page
from rich.console import Console

from ace.browser.utils import markdown_snapshot, human_delay
from ace.platforms.base import BasePlatform, Question, QuestionOption, QuestionType, SubmissionResult

console = Console()


class GenericAdapter(BasePlatform):
    name = "generic"

    async def is_assignment_page(self, page: Page) -> bool:
        return True  # always matches as fallback

    async def extract_current_question(self, page: Page) -> Question:
        """Use page text heuristics to extract the question."""
        snapshot = await markdown_snapshot(page)

        # Very basic: treat the whole visible content as the question text
        # The LLM engine will make sense of it
        body_text = await page.inner_text("body")
        body_text = re.sub(r"\s{2,}", " ", body_text.strip())

        # Detect any inputs
        radios = await page.query_selector_all("input[type='radio']")
        checkboxes = await page.query_selector_all("input[type='checkbox']")
        textareas = await page.query_selector_all("textarea")
        text_inputs = await page.query_selector_all("input[type='text']")

        options: list[QuestionOption] = []
        q_type = QuestionType.UNKNOWN
        input_sel = ""

        if radios:
            q_type = QuestionType.MCQ
            labels = ["A", "B", "C", "D", "E", "F"]
            for i, r in enumerate(radios[:6]):
                val = await r.get_attribute("value") or str(i)
                rid = await r.get_attribute("id") or ""
                lbl = await page.query_selector(f"label[for='{rid}']")
                text = (await lbl.inner_text()).strip() if lbl else val
                options.append(QuestionOption(
                    label=labels[i],
                    text=text,
                    selector=f"input[type='radio'][value='{val}']",
                ))
        elif textareas:
            q_type = QuestionType.SHORT_ANSWER
            tid = await textareas[0].get_attribute("id") or ""
            input_sel = f"#{tid}" if tid else "textarea"
        elif text_inputs:
            q_type = QuestionType.FILL_IN_BLANK
            iid = await text_inputs[0].get_attribute("id") or ""
            input_sel = f"#{iid}" if iid else "input[type='text']"

        return Question(
            id="generic_q",
            number=1,
            total=1,
            type=q_type,
            text=body_text[:3000],
            options=options,
            input_selector=input_sel,
        )

    async def is_last_question(self, page: Page) -> bool:
        submit_hints = ["submit", "finish", "complete", "done"]
        btns = await page.query_selector_all("button, input[type='submit']")
        for btn in btns:
            text = (await btn.inner_text()).lower().strip()
            if any(h in text for h in submit_hints):
                return True
        return False

    async def fill_answer(self, page: Page, question: Question, answer: str) -> None:
        await human_delay(300, 700)
        if question.type in (QuestionType.MCQ, QuestionType.TRUE_FALSE) and question.options:
            answer_lower = answer.strip().lower()
            for opt in question.options:
                if opt.label.lower() == answer_lower or opt.text.lower() == answer_lower:
                    el = await page.query_selector(opt.selector)
                    if el:
                        await el.click()
                        return
        elif question.input_selector:
            el = await page.query_selector(question.input_selector)
            if el:
                await el.fill(answer)

    async def click_next(self, page: Page) -> None:
        next_hints = ["next", "continue", "proceed"]
        btns = await page.query_selector_all("button, a")
        for btn in btns:
            text = (await btn.inner_text()).lower().strip()
            if any(h in text for h in next_hints):
                await human_delay(400, 800)
                await btn.click()
                return

    async def click_submit(self, page: Page) -> SubmissionResult:
        submit_hints = ["submit", "finish", "complete"]
        btns = await page.query_selector_all("button, input[type='submit']")
        for btn in btns:
            text = (await btn.inner_text()).lower().strip()
            if any(h in text for h in submit_hints):
                await human_delay(400, 800)
                await btn.click()
                return SubmissionResult(success=True, message="Submitted.")
        return SubmissionResult(success=False, message="Submit button not found.")

    async def wait_for_next_question(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
