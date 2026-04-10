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
import time
from pathlib import Path

from playwright.async_api import Page
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from browser_use.llm.messages import (
    ContentPartImageParam,
    ContentPartTextParam,
    ImageURL,
    SystemMessage,
    UserMessage,
)

from ace.quiz.models import Answer, AnswerPlan, NavAction, PageScan, Question, VerifyResult
from ace.quiz.prompts import ANSWER_PROMPT, NAV_PROMPT, SCOUT_PROMPT

console = Console()

_NAV_MAX_STEPS = 8
_NAV_SLEEP_S: float = 0.5


class QuizLoop:
    def __init__(self, page: Page, llm, debug: bool = False) -> None:
        self.page = page
        self.llm = llm
        self.debug = debug
        self._session_dir: Path | None = None
        self._page_num = 0

    def _dbg(self, msg: str, style: str = "dim cyan") -> None:
        if self.debug:
            console.print(f"  [{style}]DBG {msg}[/{style}]")

    def _dbg_panel(self, title: str, content: str, border: str = "dim cyan") -> None:
        if self.debug:
            console.print(Panel(
                Text(content[:2000], style="dim"),
                title=f"[bold]{title}[/bold]",
                border_style=border,
                expand=False,
            ))

    def _session_path(self) -> Path:
        if self._session_dir is None:
            from ace.config import SESSIONS_DIR
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            self._session_dir = SESSIONS_DIR / f"debug-{int(time.time())}"
            self._session_dir.mkdir(exist_ok=True)
            self._dbg(f"session dir: {self._session_dir}")
        return self._session_dir

    async def _save_screenshot(self, label: str) -> str | None:
        if not self.debug:
            return None
        try:
            path = self._session_path() / f"p{self._page_num}-{label}.png"
            await self.page.screenshot(path=str(path), full_page=True)
            self._dbg(f"screenshot saved: {path.name}")
            return str(path)
        except Exception as e:
            self._dbg(f"screenshot failed: {e}", style="yellow")
            return None

    async def run(self) -> None:
        """Main loop: scout → answer → select → verify → navigate_smart. Repeats until scout returns no questions."""
        MAX_PAGES = 100  # safety cap

        if self.debug:
            console.print(Panel(
                "[bold]Debug mode ON[/bold] — logging LLM I/O, DOM state, click results",
                border_style="cyan",
            ))

        prev_question_text: str | None = None
        same_question_count = 0

        for page_num in range(MAX_PAGES):
            self._page_num = page_num + 1
            console.print(f"[dim]→ Page {self._page_num}: scanning...[/dim]")

            # 1. Scout
            t0 = time.monotonic()
            scan = await self._scout()
            self._dbg(f"scout took {time.monotonic() - t0:.1f}s")

            if not scan.questions:
                console.print("[bold green]→ No more questions found — done.[/bold green]")
                return

            console.print(
                f"[dim]→ Platform: {scan.platform} | "
                f"{'all-on-page' if scan.all_on_page else 'one-at-a-time'} | "
                f"{len(scan.questions)} question(s)[/dim]"
            )

            # Detect stuck on same question (already answered or can't advance)
            current_q_text = scan.questions[0].text[:100] if scan.questions else ""
            if current_q_text == prev_question_text:
                same_question_count += 1
                if same_question_count >= 2:
                    self._dbg(f"same question {same_question_count}x — trying smart navigation")
                    console.print("[yellow]→ Stuck on same question — trying smart navigation[/yellow]")
                    await self._navigate_smart()
                    await asyncio.sleep(1)
                    if same_question_count >= 4:
                        console.print("[yellow]→ Cannot advance past this question — stopping[/yellow]")
                        return
                    continue
            else:
                same_question_count = 0
                prev_question_text = current_q_text

            # 2. Answer
            questions_to_answer = scan.questions
            t0 = time.monotonic()
            answer_plan = await self._answer(questions_to_answer)
            self._dbg(f"answer took {time.monotonic() - t0:.1f}s")

            # 3. Select
            await self._select(answer_plan, questions_to_answer)

            # 4. Verify (with retry)
            await self._save_screenshot("after-select")
            verify_result = await self._verify()
            retries = 0
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

            # 5. Navigate
            await self._navigate_smart()

        raise RuntimeError(f"Quiz loop exceeded {MAX_PAGES} pages without completing")

    # ── LLM calls ─────────────────────────────────────────────────────────────

    async def _screenshot_b64(self) -> str:
        data = await self.page.screenshot(full_page=True)
        return base64.b64encode(data).decode()

    async def _page_text(self) -> str:
        """Extract body text from the frame with the most content.

        Tries the active frame first, then scans ALL frames and picks the
        one with the longest body text. This ensures we get the actual quiz
        content even when it's buried in a nested iframe (Pearson Player).
        """
        # Try active frame first
        frame = await self._active_frame()
        try:
            text = await frame.inner_text("body")
            if len(text) > 200:
                return text
        except Exception:
            pass

        # Scan all frames for the richest content
        best_text = ""
        for f in self.page.frames:
            try:
                t = await f.inner_text("body")
                if len(t) > len(best_text):
                    best_text = t
                    self._dbg(f"text from {f.url[:50]}: {len(t)} chars")
            except Exception:
                continue

        if best_text:
            return best_text

        # Last resort: top-level page
        try:
            return await self.page.inner_text("body")
        except Exception:
            return ""

    async def _active_frame(self):
        """Return the frame with the most quiz-relevant inputs, or main_frame if none.

        Excludes cookie-consent inputs (OneTrust ot-group-id-*), hidden inputs,
        and other non-quiz elements to avoid picking the wrong frame on Pearson.
        """
        best_frame = self.page.main_frame
        best_count = 0
        for frame in self.page.frames:
            try:
                count = await frame.evaluate(
                    """() => {
                        // Count quiz-relevant inputs only
                        let n = 0;
                        for (const el of document.querySelectorAll(
                            'input[type="radio"], input[type="checkbox"],'
                            + ' input[type="text"], textarea,'
                            + ' [role="radio"], [role="checkbox"], [role="option"]'
                        )) {
                            // Skip cookie-consent / tracking inputs (OneTrust etc.)
                            const name = el.name || '';
                            if (name.startsWith('ot-group-id')) continue;
                            // Skip hidden inputs (not quiz-relevant)
                            if (el.type === 'hidden') continue;
                            n++;
                        }
                        return n;
                    }"""
                )
                if count > best_count:
                    self._dbg(f"frame candidate: {frame.url[:60]} quiz-inputs={count}")
                    best_count = count
                    best_frame = frame
            except Exception:
                pass
        if best_count == 0:
            self._dbg("no quiz inputs in any frame — using main_frame")
        else:
            self._dbg(f"active frame: {best_frame.url[:60]} ({best_count} quiz-inputs)")
        return best_frame

    async def _collect_buttons(self) -> list[str]:
        """Collect visible button labels from all frames, deduplicating across frames."""
        seen: set[str] = set()
        results: list[str] = []
        for frame in self.page.frames:
            try:
                texts = await frame.evaluate(
                    """() => Array.from(document.querySelectorAll(
                        'button, [role="button"], input[type="submit"],'
                        + ' input[type="button"], a[role="button"]'
                    ))
                    .filter(el => el.offsetParent !== null && !el.disabled)
                    .map(el => (el.textContent || el.value || el.getAttribute('aria-label') || '').trim())
                    .filter(t => t.length > 0)"""
                )
                for t in (texts or []):
                    if t not in seen:
                        seen.add(t)
                        results.append(t)
            except Exception:
                continue
        return results

    async def _click_by_text(self, target: str) -> bool:
        """Click the first button whose text matches target (case-insensitive).

        Pass 1: exact match. Pass 2 (fallback): button label contains the target
        text — handles LLM stripping numeric prefixes from sidebar items.
        Searches all frames. Returns True on first match, False if not found.
        """
        _JS = """(cfg) => {
            const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
            const t = norm(cfg.target);
            const fuzzy = cfg.fuzzy;
            for (const el of document.querySelectorAll(
                'button, [role="button"], input[type="submit"],'
                + ' input[type="button"], a[role="button"]'
            )) {
                const label = norm(
                    el.textContent || el.value || el.getAttribute('aria-label') || ''
                );
                const match = fuzzy ? label.includes(t) : label === t;
                if (match && t.length >= (fuzzy ? 8 : 1)) {
                    el.click();
                    return label;
                }
            }
            return false;
        }"""
        for fuzzy in (False, True):
            for frame in self.page.frames:
                try:
                    result = await frame.evaluate(_JS, {"target": target, "fuzzy": fuzzy})
                    if result:
                        self._dbg(
                            f"clicked '{target}' (fuzzy={fuzzy}) → matched '{result}' "
                            f"in {frame.url[:60]}"
                        )
                        return True
                except Exception:
                    continue
        self._dbg(f"_click_by_text: '{target}' not found in any frame", style="yellow")
        return False

    async def _navigate_smart(self) -> None:
        """LLM micro-loop: screenshot + visible buttons → click → repeat until done.

        Each iteration: take screenshot, collect all visible button labels from all
        frames, ask LLM what to click. LLM returns NavAction(action="click", target=...)
        or NavAction(action="done"). Loop exits on "done" or after _NAV_MAX_STEPS.
        """
        for step in range(_NAV_MAX_STEPS):
            b64 = await self._screenshot_b64()
            buttons = await self._collect_buttons()

            self._dbg(f"[NAV step {step + 1}/{_NAV_MAX_STEPS}] buttons: {buttons}")

            if not buttons:
                self._dbg("[NAV] no buttons found — treating as done")
                break

            try:
                messages = [
                    SystemMessage(content=NAV_PROMPT),
                    UserMessage(content=[
                        ContentPartTextParam(
                            text="Visible buttons:\n" + "\n".join(f"- {b}" for b in buttons)
                        ),
                        ContentPartImageParam(
                            image_url=ImageURL(
                                url=f"data:image/png;base64,{b64}", detail="high"
                            )
                        ),
                    ]),
                ]
                result = await self.llm.ainvoke(messages, output_format=NavAction)
                nav = result.completion
            except Exception as e:
                self._dbg(f"[NAV] LLM parse failed: {e} — treating as done", style="yellow")
                break

            self._dbg(
                f"[NAV] action={nav.action}  target={nav.target!r}  reason={nav.reason}"
            )

            if self.debug:
                self._dbg_panel(
                    f"NAV step {step + 1}",
                    f"buttons: {buttons}\n"
                    f"action={nav.action}  target={nav.target!r}\n"
                    f"reason={nav.reason}",
                    border="magenta",
                )

            if nav.action == "done":
                break

            if nav.target is None or nav.target not in buttons:
                console.print(
                    f"[yellow]Warning: LLM nav target '{nav.target}' not in button list — skipping[/yellow]"
                )
                continue

            success = await self._click_by_text(nav.target)
            self._dbg(f"[NAV] click result: {success}")

            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass

            await asyncio.sleep(_NAV_SLEEP_S)
        else:
            console.print(
                "[yellow]Warning: navigation loop exhausted without completing — continuing[/yellow]"
            )

    def _parse_option_letter(self, option_text: str) -> str | None:
        """Extract uppercase letter from 'A. foo' → 'A', or None for 'True'."""
        m = re.match(r'^([A-Za-z])\.\s', option_text)
        return m.group(1).upper() if m else None

    async def _scout(self) -> PageScan:
        await self._save_screenshot("scout")
        b64 = await self._screenshot_b64()
        text = await self._page_text()

        self._dbg(f"page text length: {len(text)} chars")
        self._dbg_panel("Page text (first 500 chars)", text[:500])

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
        scan = result.completion

        if self.debug:
            self._dbg_panel("Scout response", (
                f"platform:   {scan.platform}\n"
                f"all_on_page: {scan.all_on_page}\n"
                f"has_check:  {scan.has_check_button}\n"
                f"questions:  {len(scan.questions)}\n"
                + "\n".join(
                    f"  [{q.id}] ({q.kind}) {q.text[:80]}\n"
                    f"    options: {q.options}"
                    for q in scan.questions
                )
            ), border="green")

        return scan

    async def _answer(self, questions: list[Question]) -> AnswerPlan:
        questions_text = "\n\n".join(
            f"[{q.id}] {q.text}\nOptions: {', '.join(q.options) if q.options else '(free text)'}"
            for q in questions
        )

        self._dbg_panel("Answer prompt (questions)", questions_text)

        messages = [
            SystemMessage(content=ANSWER_PROMPT),
            UserMessage(content=f"Answer these questions:\n\n{questions_text}"),
        ]
        result = await self.llm.ainvoke(messages, output_format=AnswerPlan)
        plan = result.completion

        if self.debug:
            self._dbg_panel("Answer plan", "\n".join(
                f"  {a.question_id} → {a.value}"
                for a in plan.answers
            ), border="green")

        return plan

    # ── Browser interactions ───────────────────────────────────────────────────

    async def _select(self, plan: AnswerPlan, questions: list[Question]) -> None:
        q_map = {q.id: q for q in questions}
        for ans in plan.answers:
            question = q_map.get(ans.question_id)
            if question is None:
                self._dbg(f"skip {ans.question_id}: not in question map", style="yellow")
                continue
            self._dbg(f"selecting {ans.question_id} ({question.kind}): {ans.value}")
            if question.kind in ("mcq", "truefalse"):
                await self._click_option(ans.value if isinstance(ans.value, str) else ans.value[0])
            elif question.kind == "multi":
                values = ans.value if isinstance(ans.value, list) else [ans.value]
                for v in values:
                    await self._click_option(v)
            elif question.kind == "text":
                await self._fill_text(ans.value if isinstance(ans.value, str) else ans.value[0])
        # Brief pause for framework state to settle after all clicks
        await asyncio.sleep(0.3)

    async def _click_option(self, option_text: str) -> None:
        """Click a radio/checkbox option.

        Stage 1: parse the letter prefix (e.g. 'D') and click the Nth
        answer option's visible container in the active frame. Finds the
        input, then clicks the closest interactive ancestor (label, li, div)
        so framework event handlers (Angular/React) fire properly.

        Stage 2 (fallback): JS normalized-text search in the active frame.
        Used for options without a letter prefix (e.g. 'True', 'False').
        """
        frame = await self._active_frame()
        letter = self._parse_option_letter(option_text)

        if letter:
            index = ord(letter) - ord('A')
            self._dbg(f"click strategy: index-based (letter={letter}, index={index})")
            try:
                result = await frame.evaluate(
                    """(index) => {
                        const inputs = [...document.querySelectorAll(
                            'input[type="radio"], input[type="checkbox"]'
                        )].filter(el => !el.name.startsWith('ot-group-id') && el.type !== 'hidden');
                        if (!inputs[index]) return { clicked: false, reason: 'no input at index ' + index, total: inputs.length };
                        const inp = inputs[index];

                        // Find the closest visible clickable ancestor — this is
                        // what framework UIs (Pearson, Canvas) bind their handlers to.
                        const container = inp.closest(
                            'label, li, [role="radio"], [role="option"], ' +
                            'div[class*="answer"], div[class*="choice"], div[class*="option"], ' +
                            'div[class*="Answer"], div[class*="Choice"], div[class*="Option"]'
                        );

                        let clickTarget;
                        if (container && container !== document.body) {
                            container.click();
                            clickTarget = container.tagName + (container.className ? '.' + container.className.split(' ')[0] : '');
                        } else {
                            inp.click();
                            clickTarget = 'input-direct';
                        }

                        // Ensure framework picks up the change
                        inp.checked = true;
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        return { clicked: true, target: clickTarget, total: inputs.length };
                    }""",
                    index,
                )
                if isinstance(result, dict):
                    self._dbg(f"click result: {result}")
                    if result.get("clicked"):
                        return
                elif result:
                    return
            except Exception as e:
                self._dbg(f"index click error: {e}", style="yellow")

        # JS fallback: normalize whitespace, search labels/roles in active frame
        self._dbg(f"click strategy: text-search ('{option_text[:40]}')")
        try:
            result = await frame.evaluate(
                """(text) => {
                    const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
                    const target = norm(text);
                    // Strip letter prefix for matching (e.g. "A. fork" → "fork")
                    const stripped = target.replace(/^[a-z]\\.\\s*/, '');
                    const sels = [
                        'label', '[role="radio"]', '[role="checkbox"]',
                        '[role="option"]', 'li',
                        '[class*="answer"]', '[class*="choice"]', '[class*="option"]'
                    ];
                    for (const sel of sels) {
                        for (const el of document.querySelectorAll(sel)) {
                            const t = norm(el.textContent);
                            if (t.includes(target) || t.includes(stripped)) {
                                el.click();
                                // Also fire events on any child input
                                const inp = el.querySelector('input[type="radio"], input[type="checkbox"]');
                                if (inp) {
                                    inp.checked = true;
                                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                                }
                                return { clicked: true, sel: sel, tag: el.tagName };
                            }
                        }
                    }
                    return { clicked: false, reason: 'no text match' };
                }""",
                option_text,
            )
            if isinstance(result, dict):
                self._dbg(f"text-search result: {result}")
                if result.get("clicked"):
                    return
            elif result:
                return
        except Exception as e:
            self._dbg(f"text-search error: {e}", style="yellow")

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
                self._dbg(f"filled '{selector}' with '{value[:40]}'")
                return
            except Exception:
                continue
        console.print("[yellow]Warning: could not find text input to fill[/yellow]")

    async def _verify(self) -> VerifyResult:
        """Verify selections via JS DOM inspection — no LLM, no sidebar confusion."""
        frame = await self._active_frame()

        try:
            state = await frame.evaluate("""() => {
                const isQuizInput = el => !el.name.startsWith('ot-group-id') && el.type !== 'hidden';
                const rc = [...document.querySelectorAll('input[type="radio"]:checked')]
                            .filter(isQuizInput).length;
                const cc = [...document.querySelectorAll('input[type="checkbox"]:checked')]
                            .filter(isQuizInput).length;
                const tf = [...document.querySelectorAll('input[type="text"], textarea')]
                            .filter(el => el.value.trim()).length;
                // Also detect framework-managed selections (Pearson, etc.)
                const ariaChecked = document.querySelectorAll(
                    '[role="radio"][aria-checked="true"], [role="checkbox"][aria-checked="true"]'
                ).length;
                const cssSelected = document.querySelectorAll(
                    '.selected, .checked, ' +
                    '[class*="selected"], [class*="Selected"]'
                ).length;
                // Gather all radio/checkbox details for debug
                const allInputs = [...document.querySelectorAll('input[type="radio"], input[type="checkbox"]')]
                    .map((el, i) => ({
                        i, type: el.type, checked: el.checked, id: el.id || '',
                        name: el.name || '', visible: el.offsetParent !== null
                    }));
                return { rc, cc, tf, ariaChecked, cssSelected, allInputs };
            }""")
        except Exception:
            state = {"rc": 0, "cc": 0, "tf": 0, "ariaChecked": 0, "cssSelected": 0, "allInputs": []}

        if self.debug:
            inputs_info = state.get("allInputs", [])
            detail_lines = [f"  [{i['i']}] {i['type']} checked={i['checked']} visible={i['visible']} name={i['name']}" for i in inputs_info[:10]]
            self._dbg_panel("Verify DOM state", (
                f"radio:checked   = {state['rc']}\n"
                f"checkbox:checked = {state['cc']}\n"
                f"text filled     = {state['tf']}\n"
                f"aria-checked    = {state.get('ariaChecked', 0)}\n"
                f"css selected    = {state.get('cssSelected', 0)}\n"
                f"inputs ({len(inputs_info)}):\n" + "\n".join(detail_lines)
            ), border="yellow")

        has_selection = (
            state["rc"] > 0 or state["cc"] > 0 or state["tf"] > 0
            or state.get("ariaChecked", 0) > 0 or state.get("cssSelected", 0) > 0
        )

        issues: list[str] = []
        if not has_selection:
            issues.append("no answer selected in active frame")

        next_action = "check"  # unused — _navigate_smart() handles all navigation

        result = VerifyResult(
            all_correct=has_selection,
            issues=issues,
            next_action=next_action,
        )
        self._dbg(f"verify result: correct={result.all_correct} next={result.next_action} issues={result.issues}")
        return result

