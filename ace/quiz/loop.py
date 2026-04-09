"""
QuizLoop: platform-agnostic quiz solver.

2 LLM calls per page:
  1. scout()  — screenshot + text → PageScan (platform type + questions)
  2. answer() — questions → AnswerPlan (correct answers)

Verify uses JS DOM inspection (no LLM) — immune to sidebar confusion.
Playwright handles all clicking.
"""
import asyncio
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
from ace.quiz.prompts import ANSWER_PROMPT, SCOUT_PROMPT

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
                console.print("[bold green]→ No more questions found — done.[/bold green]")
                return

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
        """Extract body text from the active frame.

        Falls back to the top-level page body if the active frame returns
        fewer than 200 chars (e.g. it only contains sidebar navigation).
        """
        frame = await self._active_frame()
        try:
            text = await frame.inner_text("body")
            if len(text) > 200:
                return text
        except Exception:
            pass
        # Sparse active frame — fall back to main page body
        try:
            return await self.page.inner_text("body")
        except Exception:
            return ""

    async def _active_frame(self):
        """Return the frame with the most interactive inputs, or main_frame if none."""
        best_frame = self.page.main_frame
        best_count = 0
        for frame in self.page.frames:
            try:
                count = await frame.evaluate(
                    "() => document.querySelectorAll("
                    "'input[type=\"radio\"], input[type=\"checkbox\"],"
                    " input[type=\"text\"], textarea').length"
                )
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
        """Click a radio/checkbox option.

        Stage 1: parse the letter prefix (e.g. 'D') and click the Nth
        input[type=radio/checkbox] in the active frame — immune to text
        formatting differences (whitespace, encoding, double spaces).

        Stage 2 (fallback): JS normalized-text search in the active frame.
        Used for options without a letter prefix (e.g. 'True', 'False').
        """
        frame = await self._active_frame()
        letter = self._parse_option_letter(option_text)

        if letter:
            index = ord(letter) - ord('A')
            # Use JS click — bypasses Playwright actionability checks (hidden inputs)
            try:
                clicked = await frame.evaluate(
                    """(index) => {
                        const inputs = document.querySelectorAll(
                            'input[type="radio"], input[type="checkbox"]'
                        );
                        if (inputs[index]) {
                            inputs[index].click();
                            // Also click the parent label if present (custom styled UIs)
                            const label = inputs[index].closest('label') ||
                                          document.querySelector('label[for="' + inputs[index].id + '"]');
                            if (label) label.click();
                            return true;
                        }
                        return false;
                    }""",
                    index,
                )
                if clicked:
                    return
            except Exception:
                pass

        # JS fallback: normalize whitespace, search labels/roles in active frame
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

        console.print(
            f"[yellow]Warning: could not find option '{option_text[:60]}' to click[/yellow]"
        )

    async def _fill_text(self, value: str) -> None:
        """Fill the first visible text input or textarea in the active frame."""
        frame = await self._active_frame()
        for selector in ("textarea", "input[type='text']", "input:not([type])"):
            el = frame.locator(selector).first
            try:
                await el.wait_for(state="visible", timeout=1_500)
                await el.fill(value)
                return
            except Exception:
                continue
        console.print("[yellow]Warning: could not find text input to fill[/yellow]")

    async def _verify(self) -> VerifyResult:
        """Verify selections via JS DOM inspection — no LLM, no sidebar confusion."""
        frame = await self._active_frame()

        try:
            state = await frame.evaluate("""() => {
                const rc = document.querySelectorAll('input[type="radio"]:checked').length;
                const cc = document.querySelectorAll('input[type="checkbox"]:checked').length;
                const tf = [...document.querySelectorAll('input[type="text"], textarea')]
                            .filter(el => el.value.trim()).length;
                return { rc, cc, tf };
            }""")
        except Exception:
            state = {"rc": 0, "cc": 0, "tf": 0}

        has_selection = state["rc"] > 0 or state["cc"] > 0 or state["tf"] > 0

        issues: list[str] = []
        if not has_selection:
            issues.append("no answer selected in active frame")

        next_action = await self._detect_next_action()

        return VerifyResult(
            all_correct=has_selection,
            issues=issues,
            next_action=next_action,
        )

    async def _detect_next_action(self) -> str:
        """Scan all frames for Check/Next/Done buttons (disabled buttons excluded)."""
        check_kw = ["check answer", "check my answer", "submit answer"]
        next_kw = ["next question", "next", "continue"]

        for frame in self.page.frames:
            try:
                found = await frame.evaluate(
                    """(cfg) => {
                        const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
                        const btns = [...document.querySelectorAll(
                            'button:not([disabled]), [role="button"]:not([aria-disabled="true"]), input[type="submit"]:not([disabled])'
                        )];
                        const texts = btns.map(el => norm(el.textContent || el.value || ''));
                        return {
                            check: texts.some(t => cfg.c.some(n => t.includes(n))),
                            next:  texts.some(t => cfg.n.some(n => t.includes(n))),
                        };
                    }""",
                    {"c": check_kw, "n": next_kw},
                )
                if found["check"]:
                    return "check"
                if found["next"]:
                    return "next"
            except Exception:
                pass

        return "check"  # Default: assume check button exists (safe fallback)

    async def _click_button_all_frames(self, candidates: list[str]) -> bool:
        """Try to click a button matching any candidate text, searching all frames."""
        pattern = re.compile(
            '|'.join(re.escape(n) for n in candidates), re.IGNORECASE
        )

        active_frame = await self._active_frame()
        frames = [active_frame]
        for frame in self.page.frames:
            if frame not in frames:
                frames.append(frame)

        for frame in frames:
            btn = frame.get_by_role("button", name=pattern)
            try:
                if await btn.count() > 0:
                    await btn.first.click()
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    return True
            except Exception:
                pass

            try:
                clicked = await frame.evaluate(
                    """(names) => {
                        const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
                        for (const el of document.querySelectorAll(
                            'button, [role="button"], input[type="submit"]'
                        )) {
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
                    return True
            except Exception:
                pass

        return False

    async def _advance_sidebar(self) -> None:
        """Click the next question in a sidebar question list (Pearson, etc.)."""
        frame = await self._active_frame()
        try:
            advanced = await frame.evaluate("""() => {
                // Find list containers that look like question lists (items contain "X/Y pt")
                for (const container of document.querySelectorAll(
                    'ol, ul, [role="list"], [role="tablist"], nav'
                )) {
                    const items = [...container.querySelectorAll(
                        'li, [role="listitem"], [role="tab"]'
                    )];
                    if (items.length < 2) continue;
                    if (!items.some(i => /\\d+\\/\\d+\\s*pt/.test(i.textContent || ''))) continue;

                    let selectedIdx = -1;
                    for (let i = 0; i < items.length; i++) {
                        const cls = (items[i].className || '').toLowerCase();
                        if (cls.includes('selected') || cls.includes('active') ||
                            cls.includes('current') ||
                            items[i].getAttribute('aria-selected') === 'true' ||
                            /\\bselected\\b/i.test(items[i].textContent || '')) {
                            selectedIdx = i;
                            break;
                        }
                    }

                    // Click next item after selected
                    if (selectedIdx >= 0 && selectedIdx + 1 < items.length) {
                        items[selectedIdx + 1].click();
                        return true;
                    }
                    // No selected item: click first un-completed item
                    if (selectedIdx < 0) {
                        for (const item of items) {
                            if (/0\\/\\d+\\s*pt/.test(item.textContent || '')) {
                                item.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }""")
            if advanced:
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    await asyncio.sleep(1)
        except Exception:
            pass

    async def _navigate(self, action: str) -> None:
        if action == "check":
            candidates = ["Check answer", "Check Answer", "Check My Answer", "Check", "Submit Answer"]
            clicked = await self._click_button_all_frames(candidates)
            if clicked:
                await asyncio.sleep(1.5)  # Wait for Pearson feedback animation
                await self._advance_sidebar()
            else:
                console.print("[yellow]Warning: could not find 'check' button[/yellow]")
        elif action == "next":
            candidates = ["Next Question", "Next", "Continue", "Next >"]
            clicked = await self._click_button_all_frames(candidates)
            if not clicked:
                # Fallback: sidebar navigation (Pearson has no Next button)
                await self._advance_sidebar()
