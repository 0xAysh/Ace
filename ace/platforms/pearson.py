"""
Pearson MyLab adapter.
Question frame: tdx.acs.pearson.com/Player/Player.aspx
All inputs and navigation live inside this frame.
"""
import asyncio
import re
from typing import Optional

from playwright.async_api import Page, Frame
from rich.console import Console

from ace.browser.utils import human_delay
from ace.platforms.base import BasePlatform, Question, QuestionOption, QuestionType, SubmissionResult

console = Console()

_INPUT_SEL = "input[type='radio'], input[type='text'], input[type='number'], textarea"


class PearsonAdapter(BasePlatform):
    name = "pearson"

    def __init__(self) -> None:
        self._q_number = 1  # track internally; sidebar "Selected" position is unreliable

    async def is_assignment_page(self, page: Page) -> bool:
        return "pearson.com" in page.url or "mylab" in page.url.lower()

    async def _question_frame(self, page: Page) -> Optional[Frame]:
        """The Player frame is always at Player/Player.aspx under tdx.acs.pearson.com."""
        for frame in page.frames:
            if "Player/Player.aspx" in frame.url or (
                "tdx.acs.pearson.com" in frame.url and "Player" in frame.url
            ):
                return frame
        # Fallback: frame with the most inputs
        best, best_n = None, 0
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                n = await asyncio.wait_for(
                    frame.evaluate(f"() => document.querySelectorAll('{_INPUT_SEL}').length"),
                    timeout=2.0,
                )
                if n > best_n:
                    best_n, best = n, frame
            except Exception:
                continue
        return best if best_n > 0 else None

    async def _get_total(self, frame: Frame) -> int:
        """Parse total from 'My score: 0/36 pts' or 'Completed: X of 36'."""
        try:
            body = await asyncio.wait_for(frame.inner_text("body"), timeout=3.0)
            m = re.search(r"My score:\s*[\d.]+\s*/\s*(\d+)\s*pts", body)
            if m:
                return int(m.group(1))
            m = re.search(r"Completed:\s*\d+\s+of\s+(\d+)", body)
            if m:
                return int(m.group(1))
            # Count sidebar question items
            items = await frame.query_selector_all("[class*='question-list'] li, [class*='questionList'] li, [id*='questionList'] li")
            if items:
                return len(items)
        except Exception:
            pass
        return 0

    async def _question_text(self, frame: Frame) -> str:
        """
        Extract only the question body, excluding the sidebar navigation list.
        Try known Pearson selectors; fall back to stripping sidebar + getting remainder.
        """
        for sel in [
            "[id*='questionBody']", "[class*='questionBody']",
            "[id*='QuestionText']", "[class*='question-text']",
            "[class*='questionText']", "[class*='question-stem']",
            "[class*='questionStem']", "[id*='questionContent']",
            "[class*='questionContent']", ".problem-body",
            "[data-automation-id='question-body']",
        ]:
            try:
                el = await frame.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 10:
                        return text
            except Exception:
                continue

        # Fallback: strip nav/sidebar/list elements, return what's left
        try:
            text = await frame.evaluate("""() => {
                const clone = document.body.cloneNode(true);
                // Remove sidebar question list and navigation chrome
                clone.querySelectorAll(
                    'nav, [class*="sidebar"], [class*="nav-"], [class*="questionNav"], ' +
                    '[id*="questionNav"], [id*="questionList"], [class*="questionList"], ' +
                    'ul, ol, header, footer, button, [role="navigation"]'
                ).forEach(e => e.remove());
                const text = clone.innerText.replace(/\\s{2,}/g, ' ').trim();
                return text.slice(0, 2000);
            }""")
            return text or ""
        except Exception:
            return ""

    async def extract_current_question(self, page: Page) -> Question:
        console.print("[dim]  → Extracting question from Pearson Player...[/dim]")

        frame = await self._question_frame(page)
        if not frame:
            raise RuntimeError(
                "Pearson Player frame not found. "
                "Make sure the assignment is open and loaded in the browser."
            )
        console.print(f"[dim]  → Frame: {frame.url[:60]}[/dim]")

        total = await self._get_total(frame)
        number = self._q_number
        if total == 0:
            total = number
        console.print(f"[dim]  → Q{number} of {total}[/dim]")

        q_text = await self._question_text(frame)
        q_text = re.sub(r"\s{2,}", " ", q_text).strip()

        q_type, options, input_sel = await self._classify(frame)
        console.print(
            f"[dim]  → {q_type.value}"
            + (f" | {len(options)} options" if options else f" | {input_sel}")
            + "[/dim]"
        )

        return Question(
            id=f"pearson_q{number}",
            number=number,
            total=total,
            type=q_type,
            text=q_text[:3000],
            options=options,
            input_selector=input_sel,
        )

    async def _classify(self, frame: Frame) -> tuple[QuestionType, list[QuestionOption], str]:
        radios = await frame.query_selector_all("input[type='radio']")
        if radios:
            options: list[QuestionOption] = []
            labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
            for i, radio in enumerate(radios[:8]):
                radio_id = await radio.get_attribute("id") or ""
                radio_value = await radio.get_attribute("value") or str(i)
                text = ""
                if radio_id:
                    lbl = await frame.query_selector(f"label[for='{radio_id}']")
                    if lbl:
                        text = (await lbl.inner_text()).strip()
                if not text:
                    try:
                        text = await radio.evaluate("""el => {
                            const lbl = el.closest('label');
                            if (lbl) { const c = lbl.cloneNode(true); c.querySelectorAll('input').forEach(e=>e.remove()); return c.innerText.trim(); }
                            let n = el.nextSibling;
                            while (n) {
                                if (n.nodeType===3 && n.textContent.trim()) return n.textContent.trim();
                                if (n.nodeType===1 && n.innerText?.trim()) return n.innerText.trim();
                                n = n.nextSibling;
                            }
                            return '';
                        }""")
                    except Exception:
                        pass
                if not text:
                    text = radio_value
                letter = labels[i] if i < len(labels) else str(i + 1)
                sel = f"#{radio_id}" if radio_id else f"input[type='radio'][value='{radio_value}']"
                options.append(QuestionOption(label=letter, text=text, selector=sel))
            texts = {o.text.lower() for o in options}
            if texts <= {"true", "false"}:
                return QuestionType.TRUE_FALSE, options, ""
            return QuestionType.MCQ, options, ""

        textarea = await frame.query_selector("textarea")
        if textarea:
            tid = await textarea.get_attribute("id") or ""
            return QuestionType.SHORT_ANSWER, [], (f"#{tid}" if tid else "textarea")

        text_input = await frame.query_selector("input[type='text']")
        if text_input:
            iid = await text_input.get_attribute("id") or ""
            return QuestionType.FILL_IN_BLANK, [], (f"#{iid}" if iid else "input[type='text']")

        num_input = await frame.query_selector("input[type='number']")
        if num_input:
            return QuestionType.NUMERIC, [], "input[type='number']"

        return QuestionType.UNKNOWN, [], ""

    async def is_last_question(self, page: Page) -> bool:
        frame = await self._question_frame(page)
        if not frame:
            return False
        total = await self._get_total(frame)
        return total > 0 and self._q_number >= total

    async def fill_answer(self, page: Page, question: Question, answer: str) -> None:
        frame = await self._question_frame(page)
        if not frame:
            console.print("[yellow]  ⚠ No question frame — cannot fill[/yellow]")
            return
        await human_delay(300, 800)

        if question.type in (QuestionType.MCQ, QuestionType.TRUE_FALSE):
            answer_clean = answer.strip().lower()
            for opt in question.options:
                if opt.label.lower() == answer_clean or answer_clean.startswith(opt.label.lower() + "."):
                    el = await frame.query_selector(opt.selector)
                    if el:
                        console.print(f"[dim]  → Selecting {opt.label}: {opt.text[:60]}[/dim]")
                        await el.click()
                        return
            for opt in question.options:
                if opt.text.lower() == answer_clean or answer_clean in opt.text.lower():
                    el = await frame.query_selector(opt.selector)
                    if el:
                        console.print(f"[dim]  → Selecting {opt.label} (text): {opt.text[:60]}[/dim]")
                        await el.click()
                        return
            console.print(f"[yellow]  ⚠ No match for '{answer}' — options: {[o.label+'. '+o.text[:25] for o in question.options]}[/yellow]")

        elif question.type in (QuestionType.SHORT_ANSWER, QuestionType.ESSAY,
                               QuestionType.FILL_IN_BLANK, QuestionType.NUMERIC):
            el = await frame.query_selector(question.input_selector)
            if el:
                await el.click()
                await el.fill(answer)
            else:
                console.print(f"[yellow]  ⚠ Input not found: {question.input_selector}[/yellow]")

    async def click_next(self, page: Page) -> None:
        frame = await self._question_frame(page)
        if not frame:
            raise RuntimeError("No question frame found.")
        console.print("[dim]  → Clicking Next on Pearson...[/dim]")

        # Try common Next button patterns
        next_hints = ("next", "continue", "save and continue", "submit and continue")
        btns = await frame.query_selector_all("button, input[type='submit'], a[role='button'], [role='button']")
        for btn in btns:
            try:
                text = (await btn.inner_text()).strip().lower()
                if any(h in text for h in next_hints) and "prev" not in text:
                    await human_delay(400, 900)
                    await btn.click()
                    self._q_number += 1
                    return
            except Exception:
                continue

        # Fallback: click next question in sidebar by number
        try:
            items = await frame.query_selector_all("[id*='questionList'] li, [class*='questionList'] li, [class*='question-list'] li")
            if items and self._q_number < len(items):
                await human_delay(400, 800)
                await items[self._q_number].click()  # 0-indexed: current is _q_number-1, next is _q_number
                self._q_number += 1
                return
        except Exception:
            pass

        raise RuntimeError("Next button not found on Pearson page.")

    async def click_submit(self, page: Page) -> SubmissionResult:
        frame = await self._question_frame(page)
        root = frame or page.main_frame
        submit_hints = ("submit", "finish", "done", "complete")
        btns = await root.query_selector_all("button, input[type='submit'], a[role='button']")
        for btn in btns:
            try:
                text = (await btn.inner_text()).strip().lower()
                if any(h in text for h in submit_hints):
                    await human_delay(500, 1000)
                    await btn.click()
                    return SubmissionResult(success=True, message="Submitted.")
            except Exception:
                continue
        return SubmissionResult(success=False, message="Submit button not found.")

    async def wait_for_next_question(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        await asyncio.sleep(0.8)

    async def count_questions_on_page(self, page: Page) -> int:
        return 1  # Pearson always shows one question at a time
