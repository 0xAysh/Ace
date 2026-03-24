"""
Canvas LMS adapter — handles both paginated (one question per page)
and all-on-one-page classic quiz layouts.
"""
import re
from typing import Optional

from playwright.async_api import Page, ElementHandle
from rich.console import Console

from ace.browser.utils import human_delay
from ace.platforms.base import BasePlatform, Question, QuestionOption, QuestionType, SubmissionResult

console = Console()

NEXT_BTN_SELECTORS = [
    "#next-question",
    "a#next-question-link",
    "button[data-action='next-question']",
    "button:has-text('Next Question')",
    "input[value='Next Question']",
    "[data-testid='next-question-button']",
]
SUBMIT_BTN_SELECTORS = [
    "#submit_quiz_button",
    "button[data-action='submit-quiz']",
    "input[value='Submit Quiz']",
    "button:has-text('Submit Quiz')",
    "button:has-text('Submit Assessment')",
    "[data-testid='submit-quiz-button']",
]


class CanvasAdapter(BasePlatform):
    name = "canvas"

    async def is_assignment_page(self, page: Page) -> bool:
        url = page.url
        return "instructure.com" in url or "canvas" in url.lower()

    async def count_questions_on_page(self, page: Page) -> int:
        """Return how many question containers are currently visible in the DOM."""
        for sel in ["[data-question-id]", ".question_holder .question", ".question_holder"]:
            containers = await page.query_selector_all(sel)
            if containers:
                return len(containers)
        return 1

    async def _inject_mathjax_fix(self, page: Page) -> None:
        try:
            await page.evaluate("""() => {
                document.querySelectorAll('mjx-math').forEach(el => {
                    const latex = el.getAttribute('data-semantic-content') || el.textContent;
                    const span = document.createElement('span');
                    span.textContent = '$' + latex + '$';
                    el.replaceWith(span);
                });
            }""")
        except Exception:
            pass

    async def _get_total_questions(self, page: Page) -> int:
        try:
            count = await page.evaluate(
                "() => window.ENV?.quiz?.question_count || window.ENV?.QUIZ?.question_count || null"
            )
            if count:
                return int(count)
        except Exception:
            pass
        try:
            items = await page.query_selector_all("#question_list li.list_question, .question-list li")
            if items:
                return len(items)
        except Exception:
            pass
        try:
            containers = await page.query_selector_all("[data-question-id]")
            if len(containers) > 1:
                return len(containers)
        except Exception:
            pass
        try:
            body = await page.inner_text("body")
            m = re.search(r"[Qq]uestion\s+\d+\s+of\s+(\d+)", body)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return 0

    async def _radio_label_text(self, page: Page, radio: ElementHandle) -> str:
        """Get the display text for a radio button, not its internal value."""
        radio_id = await radio.get_attribute("id") or ""

        # 1. label[for=id]
        if radio_id:
            lbl = await page.query_selector(f"label[for='{radio_id}']")
            if lbl:
                text = (await lbl.inner_text()).strip()
                if text:
                    return text

        # 2. Ancestor <label> (radio inside label)
        try:
            text = await radio.evaluate("""el => {
                const lbl = el.closest('label');
                if (!lbl) return '';
                const clone = lbl.cloneNode(true);
                clone.querySelectorAll('input').forEach(e => e.remove());
                return clone.innerText.trim();
            }""")
            if text:
                return text
        except Exception:
            pass

        # 3. Sibling text nodes / elements
        try:
            text = await radio.evaluate("""el => {
                let n = el.nextSibling;
                while (n) {
                    if (n.nodeType === 3 && n.textContent.trim()) return n.textContent.trim();
                    if (n.nodeType === 1 && n.innerText?.trim()) return n.innerText.trim();
                    n = n.nextSibling;
                }
                return '';
            }""")
            if text:
                return text
        except Exception:
            pass

        # 4. aria-label
        aria = await radio.get_attribute("aria-label") or ""
        if aria:
            return aria

        # Fallback: value (numeric ID — not useful, but better than nothing)
        return await radio.get_attribute("value") or ""

    async def _classify_from_container(
        self, page: Page, container: ElementHandle
    ) -> tuple[QuestionType, list[QuestionOption], str]:
        radios = await container.query_selector_all("input[type='radio']")
        if radios:
            options: list[QuestionOption] = []
            labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
            for i, radio in enumerate(radios[:8]):
                radio_id = await radio.get_attribute("id") or ""
                radio_value = await radio.get_attribute("value") or str(i)
                text = await self._radio_label_text(page, radio)
                letter = labels[i] if i < len(labels) else str(i + 1)
                # Use id-based selector when available (more robust for multi-question pages)
                sel = f"#{radio_id}" if radio_id else f"input[type='radio'][value='{radio_value}']"
                options.append(QuestionOption(label=letter, text=text, selector=sel))
            texts = {o.text.lower() for o in options}
            if texts <= {"true", "false"}:
                return QuestionType.TRUE_FALSE, options, ""
            return QuestionType.MCQ, options, ""

        textarea = await container.query_selector(
            "textarea.question_input, textarea[name*='answer'], textarea"
        )
        if textarea:
            ta_id = await textarea.get_attribute("id") or ""
            sel = f"#{ta_id}" if ta_id else "textarea"
            try:
                maxlen = int(await textarea.get_attribute("maxlength") or "9999")
            except (ValueError, TypeError):
                maxlen = 9999
            return (QuestionType.ESSAY if maxlen > 500 else QuestionType.SHORT_ANSWER), [], sel

        text_input = await container.query_selector(
            "input[type='text'].question_input, input[type='text'][name*='answer'], input[type='text']"
        )
        if text_input:
            iid = await text_input.get_attribute("id") or ""
            return QuestionType.FILL_IN_BLANK, [], (f"#{iid}" if iid else "input[type='text']")

        num_input = await container.query_selector("input[type='number']")
        if num_input:
            return QuestionType.NUMERIC, [], "input[type='number']"

        return QuestionType.UNKNOWN, [], ""

    async def _extract_from_container(
        self, page: Page, container: ElementHandle, number: int, total: int
    ) -> Question:
        q_id = await container.get_attribute("data-question-id") or await container.get_attribute("id") or f"q{number}"

        # Question text — only the text div, not the answers
        q_text = await container.evaluate("""el => {
            const textEl = el.querySelector('.question_text, [class*="question_text"]');
            if (textEl) return textEl.innerText.trim();
            // Fallback: clone and strip answers
            const clone = el.cloneNode(true);
            clone.querySelectorAll('.answers, .answer, input, label, .correct_answer').forEach(e => e.remove());
            return clone.innerText.trim();
        }""")
        q_text = re.sub(r"\s{2,}", " ", (q_text or "").strip())

        q_type, options, input_sel = await self._classify_from_container(page, container)

        # Image detection
        has_image, img_sel = False, ""
        img = await container.query_selector("img:not([src*='bullet']):not([src*='icon']):not([src*='spacer'])")
        if img:
            has_image = True
            img_sel = f"[data-question-id='{q_id}'] img"

        return Question(
            id=q_id,
            number=number,
            total=total,
            type=q_type,
            text=q_text[:3000],
            options=options,
            input_selector=input_sel,
            has_image=has_image,
            image_selector=img_sel,
        )

    async def extract_current_question(self, page: Page) -> Question:
        """Extract the single visible question (paginated mode)."""
        console.print("[dim]  → Extracting question from Canvas...[/dim]")
        await self._inject_mathjax_fix(page)

        total = await self._get_total_questions(page)

        container = None
        for sel in ["[data-question-id]", ".question_holder .question", ".question_holder"]:
            container = await page.query_selector(sel)
            if container:
                console.print(f"[dim]  → Container: {sel}[/dim]")
                break

        if not container:
            raise RuntimeError("Could not find question container on page.")

        # Question number from .question_name
        number = 1
        try:
            name_el = await page.query_selector(".question_name, .question-number, .header .name")
            if name_el:
                m = re.search(r"(\d+)", await name_el.inner_text())
                if m:
                    number = int(m.group(1))
        except Exception:
            pass

        if total == 0:
            total = number

        q = await self._extract_from_container(page, container, number, total)
        console.print(f"[dim]  → Q{q.number} of {q.total} | {q.type.value}" + (f" | {len(q.options)} options" if q.options else "") + "[/dim]")
        return q

    async def extract_all_questions(self, page: Page) -> list[Question]:
        """Extract all questions from an all-on-one-page quiz."""
        console.print("[dim]  → Extracting all questions from page...[/dim]")
        await self._inject_mathjax_fix(page)

        containers = []
        for sel in ["[data-question-id]", ".question_holder .question", ".question_holder"]:
            containers = await page.query_selector_all(sel)
            if containers:
                console.print(f"[dim]  → Question selector: {sel} ({len(containers)} found)[/dim]")
                break

        total = len(containers)
        questions: list[Question] = []
        for i, container in enumerate(containers, 1):
            q = await self._extract_from_container(page, container, i, total)
            console.print(f"[dim]  → Q{i}/{total}: {q.type.value}" + (f" ({len(q.options)} opts)" if q.options else "") + "[/dim]")
            questions.append(q)

        console.print(f"[dim]  → Found {total} questions[/dim]")
        return questions

    async def is_last_question(self, page: Page) -> bool:
        """Only True when Next button is absent and Submit is visible."""
        for sel in NEXT_BTN_SELECTORS:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                return False
        for sel in SUBMIT_BTN_SELECTORS:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                return True
        return False

    async def fill_answer(self, page: Page, question: Question, answer: str) -> None:
        await human_delay(300, 800)

        if question.type in (QuestionType.MCQ, QuestionType.TRUE_FALSE):
            answer_clean = answer.strip().lower()
            # Exact label match (e.g. "A", "b")
            for opt in question.options:
                if opt.label.lower() == answer_clean or answer_clean.startswith(opt.label.lower() + "."):
                    el = await page.query_selector(opt.selector)
                    if el:
                        console.print(f"[dim]  → Selecting {opt.label}: {opt.text[:60]}[/dim]")
                        await el.click()
                        return
            # Text match
            for opt in question.options:
                if opt.text.lower() == answer_clean or answer_clean in opt.text.lower():
                    el = await page.query_selector(opt.selector)
                    if el:
                        console.print(f"[dim]  → Selecting {opt.label} (text): {opt.text[:60]}[/dim]")
                        await el.click()
                        return
            console.print(
                f"[yellow]  ⚠ No match for '{answer}' among: "
                f"{[o.label + '. ' + o.text[:25] for o in question.options]}[/yellow]"
            )

        elif question.type in (QuestionType.SHORT_ANSWER, QuestionType.ESSAY,
                               QuestionType.FILL_IN_BLANK, QuestionType.NUMERIC):
            el = await page.query_selector(question.input_selector)
            if el:
                console.print(f"[dim]  → Filling: {question.input_selector}[/dim]")
                await el.click()
                await el.fill(answer)
            else:
                console.print(f"[yellow]  ⚠ Input not found: {question.input_selector}[/yellow]")

    async def click_next(self, page: Page) -> None:
        console.print("[dim]  → Clicking Next Question...[/dim]")
        for sel in NEXT_BTN_SELECTORS:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await human_delay(400, 900)
                await btn.click()
                return
        raise RuntimeError("Next Question button not found.")

    async def click_submit(self, page: Page) -> SubmissionResult:
        console.print("[dim]  → Clicking Submit Quiz...[/dim]")
        for sel in SUBMIT_BTN_SELECTORS:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await human_delay(500, 1000)
                await btn.click()
                try:
                    confirm = await page.wait_for_selector(
                        "button:has-text('Submit'), input[value='Submit Quiz']",
                        timeout=3000,
                    )
                    if confirm:
                        console.print("[dim]  → Confirming dialog...[/dim]")
                        await human_delay(300, 600)
                        await confirm.click()
                except Exception:
                    pass
                return SubmissionResult(success=True, message="Quiz submitted successfully.")
        return SubmissionResult(success=False, message="Submit button not found.")

    async def wait_for_next_question(self, page: Page) -> None:
        console.print("[dim]  → Waiting for next question...[/dim]")
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        url = page.url.lower()
        if any(x in url for x in ("login", "sign_in", "saml")):
            raise RuntimeError("Session expired — log in and re-run ace run.")

        combined = (
            "[data-question-id], .question_holder, "
            "input[type='radio'], input[type='text'], input[type='number'], textarea"
        )
        try:
            await page.wait_for_selector(combined, state="visible", timeout=8_000)
            console.print("[dim]  → Next question loaded.[/dim]")
        except Exception:
            raise RuntimeError("Next question did not load.")
