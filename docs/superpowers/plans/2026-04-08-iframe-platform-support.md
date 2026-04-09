# Iframe-Aware Platform Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all-frame-search click/navigate/text logic with active-frame discovery + letter-index clicking so Pearson MyLab and other iframe-heavy platforms work reliably.

**Architecture:** `_active_frame()` scans all frames and returns the one with the most interactive inputs. All browser interactions (click, fill, navigate, page text) scope to this frame. `_click_option()` parses the letter prefix from the LLM's answer and clicks the Nth radio/checkbox by index, eliminating all text-matching failures.

**Tech Stack:** Python, Playwright async API, pytest-asyncio, unittest.mock

---

## File Structure

| File | Change |
|------|--------|
| `ace/quiz/loop.py` | Add `_active_frame()`, `_parse_option_letter()`; rewrite `_click_option()`, `_page_text()`, `_fill_text()`, `_navigate()`; remove `_all_frames()` |
| `tests/quiz/test_loop.py` | Add `_make_page_with_frame()` helper; add 7 new tests; update 3 existing tests |

---

### Task 1: Add `_active_frame()` and `_parse_option_letter()` helpers

**Files:**
- Modify: `ace/quiz/loop.py` (after the `_all_frames` method, around line 112)
- Modify: `tests/quiz/test_loop.py` (add helper + new tests at the bottom)

- [ ] **Step 1: Write failing tests**

Add to `tests/quiz/test_loop.py` — after the existing `_make_page` / `_make_llm` helpers, add a new helper and four tests:

```python
def _make_frame(input_count=0, body_text=""):
    """Minimal frame mock with configurable input count and body text."""
    frame = MagicMock()
    frame.evaluate = AsyncMock(return_value=input_count)
    frame.inner_text = AsyncMock(return_value=body_text)
    frame.locator = MagicMock()
    frame.get_by_role = MagicMock()
    return frame


def _make_page_with_frame(player_input_count=4, player_body="question text " * 20):
    """Page mock with a main_frame (no inputs) and one player frame (has inputs)."""
    main_frame = _make_frame(input_count=0, body_text="navigation sidebar")
    player_frame = _make_frame(input_count=player_input_count, body_text=player_body)

    page = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"fakepng")
    page.inner_text = AsyncMock(return_value="top-level body")
    page.wait_for_load_state = AsyncMock()
    page.main_frame = main_frame
    page.frames = [main_frame, player_frame]
    return page, main_frame, player_frame


@pytest.mark.asyncio
async def test_active_frame_picks_frame_with_most_inputs():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=4)
    loop = QuizLoop(page, MagicMock())
    result = await loop._active_frame()
    assert result is player_frame


@pytest.mark.asyncio
async def test_active_frame_falls_back_to_main_when_no_inputs():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=0)
    loop = QuizLoop(page, MagicMock())
    result = await loop._active_frame()
    assert result is main_frame


def test_parse_option_letter_extracts_uppercase():
    loop = QuizLoop(MagicMock(), MagicMock())
    assert loop._parse_option_letter("D. $75,000; $64,000.") == "D"
    assert loop._parse_option_letter("A. fork") == "A"
    assert loop._parse_option_letter("b. lowercase") == "B"


def test_parse_option_letter_returns_none_when_no_prefix():
    loop = QuizLoop(MagicMock(), MagicMock())
    assert loop._parse_option_letter("True") is None
    assert loop._parse_option_letter("False") is None
    assert loop._parse_option_letter("yes") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/0xayush/Projects/Ace
uv run pytest tests/quiz/test_loop.py::test_active_frame_picks_frame_with_most_inputs tests/quiz/test_loop.py::test_active_frame_falls_back_to_main_when_no_inputs tests/quiz/test_loop.py::test_parse_option_letter_extracts_uppercase tests/quiz/test_loop.py::test_parse_option_letter_returns_none_when_no_prefix -v
```

Expected: FAIL with `AttributeError: '_active_frame'` and `AttributeError: '_parse_option_letter'`

- [ ] **Step 3: Implement `_active_frame()` and `_parse_option_letter()` in `ace/quiz/loop.py`**

Add both methods after `_all_frames()` (around line 112). Keep `_all_frames()` in place for now — it gets removed in Task 3.

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/quiz/test_loop.py::test_active_frame_picks_frame_with_most_inputs tests/quiz/test_loop.py::test_active_frame_falls_back_to_main_when_no_inputs tests/quiz/test_loop.py::test_parse_option_letter_extracts_uppercase tests/quiz/test_loop.py::test_parse_option_letter_returns_none_when_no_prefix -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
uv run pytest tests/quiz/ -v
```

Expected: all existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "Add _active_frame() and _parse_option_letter() helpers"
```

---

### Task 2: Rewrite `_click_option()` to click by letter index

**Files:**
- Modify: `ace/quiz/loop.py` lines 158–226 (replace entire `_click_option` method)
- Modify: `tests/quiz/test_loop.py` (add new test, update `test_select_clicks_mcq_option`)

- [ ] **Step 1: Write failing test for index-based click**

Add to `tests/quiz/test_loop.py`:

```python
@pytest.mark.asyncio
async def test_click_option_by_letter_index():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=4)

    radio = MagicMock()
    radio.click = AsyncMock()

    inputs = MagicMock()
    inputs.count = AsyncMock(return_value=4)
    inputs.nth = MagicMock(return_value=radio)

    player_frame.locator = MagicMock(return_value=inputs)

    loop = QuizLoop(page, MagicMock())
    await loop._click_option("C. something")

    player_frame.locator.assert_called_with(
        "input[type='radio'], input[type='checkbox']"
    )
    inputs.nth.assert_called_once_with(2)  # C = index 2
    radio.click.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/quiz/test_loop.py::test_click_option_by_letter_index -v
```

Expected: FAIL — the old `_click_option` doesn't call `inputs.nth`

- [ ] **Step 3: Replace `_click_option()` in `ace/quiz/loop.py`**

Replace the entire `_click_option` method (lines 158–226) with:

```python
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
        try:
            inputs = frame.locator("input[type='radio'], input[type='checkbox']")
            if await inputs.count() > index:
                await inputs.nth(index).click()
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
```

- [ ] **Step 4: Update `test_select_clicks_mcq_option` to use frame-based setup**

Replace the existing `test_select_clicks_mcq_option` in `tests/quiz/test_loop.py` with:

```python
@pytest.mark.asyncio
async def test_select_clicks_mcq_option():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=2)

    radio = MagicMock()
    radio.click = AsyncMock()

    inputs = MagicMock()
    inputs.count = AsyncMock(return_value=2)
    inputs.nth = MagicMock(return_value=radio)

    player_frame.locator = MagicMock(return_value=inputs)

    questions = [Question(id="q1", text="X?", options=["A. fork", "B. exec"], kind="mcq")]
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])

    loop = QuizLoop(page, MagicMock())
    await loop._select(plan, questions)

    inputs.nth.assert_called_once_with(0)  # A = index 0
    radio.click.assert_awaited_once()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/quiz/test_loop.py::test_click_option_by_letter_index tests/quiz/test_loop.py::test_select_clicks_mcq_option -v
```

Expected: 2 PASSED

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/quiz/ -v
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "Rewrite _click_option() to use letter index in active frame"
```

---

### Task 3: Update `_page_text()`, `_fill_text()`, `_navigate()` — scope all to active frame

**Files:**
- Modify: `ace/quiz/loop.py` — replace `_page_text()`, `_fill_text()`, `_navigate()`; remove `_all_frames()`
- Modify: `tests/quiz/test_loop.py` — add 2 new tests, update `test_navigate_next_clicks_button` and `test_run_completes_single_question`

- [ ] **Step 1: Write failing tests for `_page_text()` fallback behaviour**

Add to `tests/quiz/test_loop.py`:

```python
@pytest.mark.asyncio
async def test_page_text_uses_active_frame_when_long():
    page, main_frame, player_frame = _make_page_with_frame(
        player_input_count=4,
        player_body="question text " * 20,  # 280 chars > 200
    )
    loop = QuizLoop(page, MagicMock())
    result = await loop._page_text()
    assert result == "question text " * 20
    player_frame.inner_text.assert_awaited_once_with("body")


@pytest.mark.asyncio
async def test_page_text_falls_back_to_main_when_sparse():
    page, main_frame, player_frame = _make_page_with_frame(
        player_input_count=4,
        player_body="nav",  # < 200 chars
    )
    page.inner_text = AsyncMock(return_value="full page content " * 20)
    loop = QuizLoop(page, MagicMock())
    result = await loop._page_text()
    assert result == "full page content " * 20
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/quiz/test_loop.py::test_page_text_uses_active_frame_when_long tests/quiz/test_loop.py::test_page_text_falls_back_to_main_when_sparse -v
```

Expected: FAIL — current `_page_text()` concatenates all frames, not active-frame-only

- [ ] **Step 3: Replace `_page_text()` in `ace/quiz/loop.py`**

Replace lines 95–108 (the entire `_page_text` method):

```python
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
```

- [ ] **Step 4: Replace `_fill_text()` in `ace/quiz/loop.py`**

Replace lines 228–239 (the entire `_fill_text` method):

```python
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
```

- [ ] **Step 5: Replace `_navigate()` in `ace/quiz/loop.py`**

Replace lines 256–305 (the entire `_navigate` method):

```python
async def _navigate(self, action: str) -> None:
    if action == "check":
        candidates = ["Check Answer", "Check My Answer", "Check", "Submit Answer"]
    elif action == "next":
        candidates = ["Next Question", "Next", "Continue", "Next >"]
    else:
        return

    pattern = re.compile(
        '|'.join(re.escape(n) for n in candidates), re.IGNORECASE
    )
    frame = await self._active_frame()

    # Playwright role-based search in active frame
    btn = frame.get_by_role("button", name=pattern)
    try:
        if await btn.count() > 0:
            await btn.first.click()
            try:
                await self.page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass  # SPAs with long-polling may never reach networkidle
            return
    except Exception:
        pass

    # JS fallback — normalized text search in active frame
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
            return
    except Exception:
        pass

    console.print(f"[yellow]Warning: could not find '{action}' button[/yellow]")
```

- [ ] **Step 6: Remove `_all_frames()` from `ace/quiz/loop.py`**

Delete lines 110–112:

```python
# DELETE these 3 lines:
def _all_frames(self):
    """Return main frame + all child frames."""
    return self.page.frames
```

- [ ] **Step 7: Update `test_navigate_next_clicks_button` in `tests/quiz/test_loop.py`**

Replace the existing test:

```python
@pytest.mark.asyncio
async def test_navigate_next_clicks_button():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=4)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.click = AsyncMock()

    player_frame.get_by_role = MagicMock(return_value=btn)

    loop = QuizLoop(page, MagicMock())
    await loop._navigate("next")

    btn.first.click.assert_awaited_once()
    page.wait_for_load_state.assert_awaited_once_with("networkidle", timeout=5_000)
```

- [ ] **Step 8: Update `test_run_completes_single_question` in `tests/quiz/test_loop.py`**

Replace the existing test:

```python
@pytest.mark.asyncio
async def test_run_completes_single_question():
    page, main_frame, player_frame = _make_page_with_frame(
        player_input_count=2,
        player_body="question text " * 20,
    )

    radio = MagicMock()
    radio.click = AsyncMock()

    inputs = MagicMock()
    inputs.count = AsyncMock(return_value=2)
    inputs.nth = MagicMock(return_value=radio)

    player_frame.locator = MagicMock(return_value=inputs)

    llm = _make_llm()
    scan = PageScan(
        platform="pearson",
        all_on_page=False,
        has_check_button=False,
        questions=[Question(id="q1", text="What?", options=["A. fork", "B. exec"], kind="mcq")],
    )
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])
    verify_done = VerifyResult(all_correct=True, issues=[], next_action="done")

    llm.ainvoke = AsyncMock(side_effect=[
        _completion(scan),
        _completion(plan),
        _completion(verify_done),
    ])

    loop = QuizLoop(page, llm)
    await loop.run()

    assert llm.ainvoke.call_count == 3  # scout + answer + verify
    inputs.nth.assert_called_with(0)    # A = index 0
    radio.click.assert_awaited()
```

- [ ] **Step 9: Run all new and updated tests**

```bash
uv run pytest tests/quiz/test_loop.py::test_page_text_uses_active_frame_when_long tests/quiz/test_loop.py::test_page_text_falls_back_to_main_when_sparse tests/quiz/test_loop.py::test_navigate_next_clicks_button tests/quiz/test_loop.py::test_run_completes_single_question -v
```

Expected: 4 PASSED

- [ ] **Step 10: Run full test suite**

```bash
uv run pytest tests/quiz/ -v
```

Expected: all tests pass (count should be 15 or more)

- [ ] **Step 11: Commit**

```bash
git add ace/quiz/loop.py tests/quiz/test_loop.py
git commit -m "Scope _page_text, _fill_text, _navigate to active frame; remove _all_frames"
```
