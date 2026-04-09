# LLM-Driven Quiz Navigation

**Date:** 2026-04-09  
**Status:** Approved

## Problem

The current `_navigate()` method uses hardcoded button name lists (e.g. `["Next", "Check Answer", "Try again"]`). When a platform uses a button not in the list (e.g. Pearson's "Final check"), navigation stalls. Every new platform requires manual additions. The system cannot adapt.

## Goal

Replace all hardcoded navigation logic with a screenshot + DOM driven LLM micro-loop. The LLM sees what a human sees and picks what to click — no platform-specific knowledge required.

## Scope

**In scope:**
- Replace `_navigate()`, `_detect_next_action()`, `_dismiss_dialogs()`, `_click_button_all_frames()`, `_advance_sidebar()` with `_navigate_smart()`
- New `NavAction` Pydantic model
- New `_collect_buttons()` helper (JS DOM scan)
- Update `run()` to call `_navigate_smart()` after `_select()`
- Update affected tests

**Out of scope:**
- `_scout()`, `_answer()`, `_select()`, `_verify()` — unchanged
- `PageScan`, `AnswerPlan`, `VerifyResult` models — unchanged
- Prompts for scout/answer — unchanged

## Data Model

```python
class NavAction(BaseModel):
    action: Literal["click", "done"]
    target: str | None = None  # exact button label from collected list
    reason: str                 # logged in debug output
```

`action="done"` signals a new question is visible or the quiz is finished.  
`target` must be one of the labels returned by `_collect_buttons()` — the LLM cannot invent values.

## Architecture

### `_collect_buttons() -> list[str]`

Iterates all `page.frames`. In each frame, runs JS:

```js
Array.from(document.querySelectorAll(
  'button, [role="button"], input[type="submit"], input[type="button"], a[role="button"]'
))
  .filter(el => el.offsetParent !== null && !el.disabled)
  .map(el => (el.textContent || el.value || el.getAttribute('aria-label') || '').trim())
  .filter(t => t.length > 0)
```

Returns a deduped flat list preserving order. Empty frames are skipped silently.

### `_navigate_smart()`

```
loop (max 8 iterations):
  1. screenshot = page.screenshot()
  2. buttons = await _collect_buttons()
  3. NavAction = await LLM(screenshot, buttons, NAV_PROMPT)
  4. if action == "done" → break
  5. success = await _click_by_text(NavAction.target)
  6. if not success → log warning, continue (LLM may self-correct)
  7. await page.wait_for_load_state("domcontentloaded", timeout=3s)

if loop exhausted: log warning, return (outer scout cycle re-evaluates)
```

### `_click_by_text(target: str) -> bool`

Iterates all frames. In each frame, queries all interactive elements and JS-clicks the first whose trimmed text matches `target` exactly. Returns `True` on first match, `False` if not found in any frame.

### LLM Prompt (NAV_PROMPT)

```
You are navigating a quiz. An answer was just selected.
Visible buttons: {buttons}
Looking at the screenshot, what should be clicked to advance?

Rules:
- target must be exactly one of the button labels listed above
- Return action="done" only when a new question is already visible or the quiz is finished
- If a dialog/popup is visible, dismiss it first
```

Structured output via `NavAction`.

### `run()` loop change

```python
# Before
verify = await self._verify()
next_action = verify.next_action
await self._navigate(next_action)

# After
await self._verify()          # still JS-based, result used for logging only
await self._navigate_smart()  # LLM decides everything
```

`verify.next_action` field becomes unused but the model is not changed.

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| LLM returns target not in button list | Skip click, log warning, re-iterate |
| LLM structured output parse fails | Treat as `done`, let scout cycle recover |
| Loop hits 8-iteration cap | Log warning, return — scout re-evaluates page |
| `wait_for_load_state` timeout | Ignore, continue loop |
| Frame JS evaluation error | Skip frame silently |

## Debug Output

When `--debug` is active, each iteration emits a Rich panel:
```
[NAV] buttons: ["Final check", "OK", "Skip"]
[NAV] action=click  target="Final check"  reason="Answer selected, check button visible"
[NAV] click result: True
```

## Tests

Tests to update:
- `test_navigate_next_clicks_button` → rewrite for `_navigate_smart()` micro-loop
- `test_run_completes_single_question` → mock `_navigate_smart` instead of `_navigate`

New tests:
- `test_navigate_smart_clicks_then_done` — LLM returns click then done, verify 2 iterations
- `test_navigate_smart_skips_missing_button` — LLM returns unknown target, loop continues
- `test_navigate_smart_exhausts_cap` — LLM never returns done, verify warning logged after 8 iterations
- `test_collect_buttons_deduplicates` — same button text across frames returned once
