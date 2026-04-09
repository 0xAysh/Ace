"""
QuizLoop: platform-agnostic quiz solver.

3 LLM calls per page:
  1. scout()  — screenshot + text → PageScan (platform type + questions)
  2. answer() — questions → AnswerPlan (correct answers)
  3. verify() — screenshot → VerifyResult (selections confirmed + next action)

Playwright handles all clicking.
"""
import base64

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
