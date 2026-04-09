# LLM-Driven Quiz Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace five hardcoded navigation methods in `QuizLoop` with a single LLM micro-loop (`_navigate_smart`) that takes a screenshot + visible button list and decides what to click — adapting to any platform without hardcoded button names.

**Architecture:** After `_select()`, call `_navigate_smart()` which loops (max 8 iterations): screenshot + JS-collected button labels → LLM picks a button or says "done" → click by exact text match across all frames → repeat until done or cap hit. Existing `_scout()`, `_answer()`, `_select()`, and `_verify()` are unchanged.

**Tech Stack:** Python 3.12, Playwright async, Pydantic v2, browser-use LLM bindings (`llm.ainvoke` with `output_format`), pytest-asyncio, Rich console.

---

## File Map

| File | Change |
|------|--------|
| `ace/quiz/models.py` | Add `NavAction` model |
| `ace/quiz/prompts.py` | Add `NAV_PROMPT` string |
| `ace/quiz/loop.py` | Add `_NAV_MAX_STEPS`, update imports; add `_collect_buttons`, `_click_by_text`, `_navigate_smart`; update `run()` and `_verify()`; delete `_navigate`, `_detect_next_action`, `_dismiss_dialogs`, `_click_button_all_frames`, `_advance_sidebar` |
| `tests/quiz/test_loop.py` | Delete `test_navigate_next_clicks_button`; add 5 new nav tests; update 2 existing tests |

---

## Task 1: Add NavAction model and NAV_PROMPT

**Files:**
- Modify: `ace/quiz/models.py`
- Modify: `ace/quiz/prompts.py`

- [ ] **Step 1: Add NavAction to models.py**

Open `ace/quiz/models.py` and append after the `VerifyResult` class:

```python
class NavAction(BaseModel):
    action: Literal["click", "done"]
    target: str | None = None  # exact button label from _collect_buttons(); None when action="done"
    reason: str                 # shown in debug output
```

The file's existing `from typing import Literal` import already covers this.

- [ ] **Step 2: Add NAV_PROMPT to prompts.py**

Append to `ace/quiz/prompts.py`:

```python
NAV_PROMPT = """\
You are navigating a quiz page. An answer was just selected.

You are given a screenshot of the current page and a list of visible buttons.

Your task: determine what to click to advance to the next question.

Return:
- action: "click" to click a button, or "done" if a new question is already visible
  or no further navigation is needed
- target: the EXACT button label from the visible buttons list (required when action="click")
- reason: brief explanation of your choice

Rules:
- target MUST be exactly one of the visible button labels provided — do not invent labels
- If a feedback dialog or popup is visible (e.g. "That's incorrect"), dismiss it first (OK, Close, Got it)
- After dismissing, click a Check / Submit answer button if visible
- After checking, click Next / Continue to advance to the next question
- Use sidebar navigation links if visible and no Next button is present
- Return action="done" only when a new unanswered question is already visible on screen
- Do NOT click final quiz submission buttons (Submit Quiz, Finish Quiz, Turn in)
"""
```

- [ ] **Step 3: Commit**

```bash
git add ace/quiz/models.py ace/quiz/prompts.py
git commit -m "feat: add NavAction model and NAV_PROMPT for LLM-driven navigation"
```

---

## Task 2: TDD — `_collect_buttons()`

**Files:**
- Modify: `tests/quiz/test_loop.py`
- Modify: `ace/quiz/loop.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/quiz/test_loop.py`:

```python
@pytest.mark.asyncio
async def test_collect_buttons_deduplicates():
    """Same button text appearing in two frames should only appear once."""
    frame1 = MagicMock()
    frame1.evaluate = AsyncMock(return_value=["Next", "Check Answer"])
    frame1.url = "https://frame1.example.com"

    frame2 = MagicMock()
    frame2.evaluate = AsyncMock(return_value=["Next", "Submit"])  # "Next" is a dupe
    frame2.url = "https://frame2.example.com"

    page = AsyncMock()
    page.frames = [frame1, frame2]

    loop = QuizLoop(page, MagicMock())
    result = await loop._collect_buttons()

    assert result.count("Next") == 1
    assert "Check Answer" in result
    assert "Submit" in result
    assert len(result) == 3


@pytest.mark.asyncio
async def test_collect_buttons_skips_failed_frames():
    """A frame that raises during evaluate should be skipped silently."""
    frame1 = MagicMock()
    frame1.evaluate = AsyncMock(side_effect=Exception("frame detached"))
    frame1.url = "https://frame1.example.com"

    frame2 = MagicMock()
    frame2.evaluate = AsyncMock(return_value=["Next"])
    frame2.url = "https://frame2.example.com"

    page = AsyncMock()
    page.frames = [frame1, frame2]

    loop = QuizLoop(page, MagicMock())
    result = await loop._collect_buttons()

    assert result == ["Next"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/quiz/test_loop.py::test_collect_buttons_deduplicates tests/quiz/test_loop.py::test_collect_buttons_skips_failed_frames -v
```

Expected: `AttributeError: 'QuizLoop' object has no attribute '_collect_buttons'`

- [ ] **Step 3: Implement `_collect_buttons()` in loop.py**

Add after the `_active_frame` method (around line 247), before `_parse_option_letter`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/quiz/test_loop.py::test_collect_buttons_deduplicates tests/quiz/test_loop.py::test_collect_buttons_skips_failed_frames -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "feat: add _collect_buttons() with dedup across frames"
```

---

## Task 3: TDD — `_click_by_text()`

**Files:**
- Modify: `tests/quiz/test_loop.py`
- Modify: `ace/quiz/loop.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/quiz/test_loop.py`:

```python
@pytest.mark.asyncio
async def test_click_by_text_returns_true_on_match():
    """Returns True when a frame's JS click finds the button."""
    frame = MagicMock()
    frame.evaluate = AsyncMock(return_value=True)
    frame.url = "https://example.com"

    page = AsyncMock()
    page.frames = [frame]

    loop = QuizLoop(page, MagicMock())
    result = await loop._click_by_text("Check Answer")

    assert result is True
    frame.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_click_by_text_returns_false_when_not_found():
    """Returns False when no frame contains the button."""
    frame = MagicMock()
    frame.evaluate = AsyncMock(return_value=False)
    frame.url = "https://example.com"

    page = AsyncMock()
    page.frames = [frame]

    loop = QuizLoop(page, MagicMock())
    result = await loop._click_by_text("Nonexistent Button")

    assert result is False


@pytest.mark.asyncio
async def test_click_by_text_tries_all_frames():
    """Tries subsequent frames if the first returns False."""
    frame1 = MagicMock()
    frame1.evaluate = AsyncMock(return_value=False)
    frame1.url = "https://frame1.example.com"

    frame2 = MagicMock()
    frame2.evaluate = AsyncMock(return_value=True)
    frame2.url = "https://frame2.example.com"

    page = AsyncMock()
    page.frames = [frame1, frame2]

    loop = QuizLoop(page, MagicMock())
    result = await loop._click_by_text("Next")

    assert result is True
    frame1.evaluate.assert_awaited_once()
    frame2.evaluate.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/quiz/test_loop.py::test_click_by_text_returns_true_on_match tests/quiz/test_loop.py::test_click_by_text_returns_false_when_not_found tests/quiz/test_loop.py::test_click_by_text_tries_all_frames -v
```

Expected: `AttributeError: 'QuizLoop' object has no attribute '_click_by_text'`

- [ ] **Step 3: Implement `_click_by_text()` in loop.py**

Add after `_collect_buttons`, before `_parse_option_letter`:

```python
async def _click_by_text(self, target: str) -> bool:
    """Click the first button whose text exactly matches target (case-insensitive).

    Searches all frames. Returns True on first match, False if not found anywhere.
    """
    for frame in self.page.frames:
        try:
            clicked = await frame.evaluate(
                """(target) => {
                    const norm = s => s.replace(/\\s+/g, ' ').trim().toLowerCase();
                    const t = norm(target);
                    for (const el of document.querySelectorAll(
                        'button, [role="button"], input[type="submit"],'
                        + ' input[type="button"], a[role="button"]'
                    )) {
                        const label = norm(
                            el.textContent || el.value || el.getAttribute('aria-label') || ''
                        );
                        if (label === t) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                target,
            )
            if clicked:
                self._dbg(f"clicked '{target}' in {frame.url[:60]}")
                return True
        except Exception:
            continue
    self._dbg(f"_click_by_text: '{target}' not found in any frame", style="yellow")
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/quiz/test_loop.py::test_click_by_text_returns_true_on_match tests/quiz/test_loop.py::test_click_by_text_returns_false_when_not_found tests/quiz/test_loop.py::test_click_by_text_tries_all_frames -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "feat: add _click_by_text() — exact-match click across all frames"
```

---

## Task 4: TDD — `_navigate_smart()`

**Files:**
- Modify: `tests/quiz/test_loop.py`
- Modify: `ace/quiz/loop.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/quiz/test_loop.py` (add `from ace.quiz.models import NavAction` in the existing imports block at the top):

```python
# Update the existing import line:
from ace.quiz.models import PageScan, Question, AnswerPlan, Answer, VerifyResult, NavAction
```

Then add the tests:

```python
@pytest.mark.asyncio
async def test_navigate_smart_clicks_then_done():
    """LLM returns click on first iteration, done on second — verify two LLM calls and one click."""
    page = AsyncMock()
    page.frames = []
    page.screenshot = AsyncMock(return_value=b"fakepng")
    page.wait_for_load_state = AsyncMock()

    llm = _make_llm()
    loop = QuizLoop(page, llm)
    loop._collect_buttons = AsyncMock(return_value=["Check Answer", "Skip"])
    loop._click_by_text = AsyncMock(return_value=True)

    llm.ainvoke = AsyncMock(side_effect=[
        _completion(NavAction(action="click", target="Check Answer", reason="check answer visible")),
        _completion(NavAction(action="done", target=None, reason="new question loaded")),
    ])

    await loop._navigate_smart()

    assert llm.ainvoke.call_count == 2
    loop._click_by_text.assert_awaited_once_with("Check Answer")


@pytest.mark.asyncio
async def test_navigate_smart_skips_missing_button():
    """LLM returns a target not in the button list — click is skipped, loop continues."""
    page = AsyncMock()
    page.frames = []
    page.screenshot = AsyncMock(return_value=b"fakepng")
    page.wait_for_load_state = AsyncMock()

    llm = _make_llm()
    loop = QuizLoop(page, llm)
    loop._collect_buttons = AsyncMock(return_value=["Check Answer"])
    loop._click_by_text = AsyncMock(return_value=True)

    llm.ainvoke = AsyncMock(side_effect=[
        _completion(NavAction(action="click", target="Nonexistent Button", reason="hallucinated")),
        _completion(NavAction(action="done", target=None, reason="done")),
    ])

    await loop._navigate_smart()

    loop._click_by_text.assert_not_awaited()
    assert llm.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_navigate_smart_exhausts_cap():
    """LLM never returns done — loop stops after 8 iterations without raising."""
    page = AsyncMock()
    page.frames = []
    page.screenshot = AsyncMock(return_value=b"fakepng")
    page.wait_for_load_state = AsyncMock()

    llm = _make_llm()
    loop = QuizLoop(page, llm)
    loop._collect_buttons = AsyncMock(return_value=["Next"])
    loop._click_by_text = AsyncMock(return_value=True)

    llm.ainvoke = AsyncMock(return_value=_completion(
        NavAction(action="click", target="Next", reason="keep going")
    ))

    await loop._navigate_smart()  # must not raise

    assert llm.ainvoke.call_count == 8
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/quiz/test_loop.py::test_navigate_smart_clicks_then_done tests/quiz/test_loop.py::test_navigate_smart_skips_missing_button tests/quiz/test_loop.py::test_navigate_smart_exhausts_cap -v
```

Expected: `AttributeError: 'QuizLoop' object has no attribute '_navigate_smart'`

- [ ] **Step 3: Add imports and constant to loop.py**

At the top of `ace/quiz/loop.py`, update the two import lines:

```python
# Change:
from ace.quiz.models import Answer, AnswerPlan, PageScan, Question, VerifyResult
from ace.quiz.prompts import ANSWER_PROMPT, SCOUT_PROMPT

# To:
from ace.quiz.models import Answer, AnswerPlan, NavAction, PageScan, Question, VerifyResult
from ace.quiz.prompts import ANSWER_PROMPT, NAV_PROMPT, SCOUT_PROMPT
```

Add after the `console = Console()` line (around line 34):

```python
_NAV_MAX_STEPS = 8
```

- [ ] **Step 4: Implement `_navigate_smart()` in loop.py**

Add after `_click_by_text`, before `_parse_option_letter`:

```python
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

        await asyncio.sleep(0.5)
    else:
        console.print(
            "[yellow]Warning: navigation loop exhausted without completing — continuing[/yellow]"
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/quiz/test_loop.py::test_navigate_smart_clicks_then_done tests/quiz/test_loop.py::test_navigate_smart_skips_missing_button tests/quiz/test_loop.py::test_navigate_smart_exhausts_cap -v
```

Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "feat: implement _navigate_smart() LLM micro-loop"
```

---

## Task 5: Wire up — update `run()`, `_verify()`, remove old methods, update tests

**Files:**
- Modify: `ace/quiz/loop.py`
- Modify: `tests/quiz/test_loop.py`

- [ ] **Step 1: Update `run()` in loop.py**

Find this block (around line 95):
```python
            # Dismiss any leftover dialogs from previous page
            await self._dismiss_dialogs()
```
Delete those two lines entirely.

Find this block in the stuck-question branch (around line 119):
```python
                    self._dbg(f"same question {same_question_count}x — trying sidebar skip")
                    console.print("[yellow]→ Stuck on same question — skipping via sidebar[/yellow]")
                    await self._advance_sidebar()
```
Replace with:
```python
                    self._dbg(f"same question {same_question_count}x — trying smart navigation")
                    console.print("[yellow]→ Stuck on same question — trying smart navigation[/yellow]")
                    await self._navigate_smart()
```

Find this block (around line 156):
```python
            # 5. Navigate or stop
            if verify_result.next_action == "done":
                console.print("[dim]→ All questions answered.[/dim]")
                return

            await self._navigate(verify_result.next_action)
```
Replace with:
```python
            # 5. Navigate
            await self._navigate_smart()
```

- [ ] **Step 2: Update `_verify()` in loop.py**

Find this line inside `_verify()` (around line 506):
```python
        next_action = await self._detect_next_action()
```
Replace with:
```python
        next_action = "check"  # unused — _navigate_smart() handles all navigation
```

- [ ] **Step 3: Delete old methods from loop.py**

Delete the following five method definitions entirely from `loop.py`:
- `_detect_next_action` (starts around line 516, ends around line 548)
- `_click_button_all_frames` (starts around line 550, ends around line 597)
- `_advance_sidebar` (starts around line 599, ends around line 657)
- `_dismiss_dialogs` (starts around line 659, ends around line 702)
- `_navigate` (starts around line 704, ends around line 720)

- [ ] **Step 4: Update `test_run_completes_single_question` in test_loop.py**

Find:
```python
    loop._navigate = AsyncMock()
```
Replace with:
```python
    loop._navigate_smart = AsyncMock()
```

- [ ] **Step 5: Delete `test_navigate_next_clicks_button` from test_loop.py**

Remove the entire test (lines ~166–178 in the original file):
```python
@pytest.mark.asyncio
async def test_navigate_next_clicks_button():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=4)
    loop = QuizLoop(page, MagicMock())

    # _click_button_all_frames uses JS evaluate to find and click buttons
    loop._active_frame = AsyncMock(return_value=player_frame)
    # JS button search returns matched button text on success
    player_frame.evaluate = AsyncMock(return_value="next")

    await loop._navigate("next")

    player_frame.evaluate.assert_awaited_once()
```

- [ ] **Step 6: Clean up dead mock in verify tests**

In `test_verify_detects_checked_radio`, remove the now-unused line:
```python
    loop._detect_next_action = AsyncMock(return_value="check")
```

In `test_verify_detects_no_selection`, remove the now-unused line:
```python
    loop._detect_next_action = AsyncMock(return_value="check")
```

(The assertions `result.next_action == "check"` still pass because `_verify()` now hardcodes `"check"`.)

- [ ] **Step 7: Run the full test suite**

```bash
uv run pytest tests/quiz/test_loop.py -v
```

Expected: all tests pass. If any fail, check that:
- `_navigate` and `_detect_next_action` references are fully removed from loop.py
- `test_run_completes_single_question` mocks `_navigate_smart` not `_navigate`

- [ ] **Step 8: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "feat: wire up _navigate_smart; remove 5 hardcoded navigation methods"
```

---

## Task 6: Smoke test end-to-end

- [ ] **Step 1: Verify the full test suite is green**

```bash
uv run pytest -v
```

Expected: all tests pass, no import errors.

- [ ] **Step 2: Check loop.py has no references to deleted methods**

```bash
grep -n "_navigate\b\|_detect_next_action\|_dismiss_dialogs\|_click_button_all_frames\|_advance_sidebar" ace/quiz/loop.py
```

Expected: no output (or only the new `_navigate_smart` definition).

- [ ] **Step 3: Commit if any minor fixes were needed**

```bash
git add -p && git commit -m "fix: cleanup after LLM navigation wiring"
```
