# Iframe-Aware Platform Support Design

**Date:** 2026-04-08
**Status:** Approved

## Problem

The current QuizLoop treats every page as a flat surface: one screenshot, one body text blob, and a strategy of searching all frames on every click. This breaks on iframe-heavy platforms like Pearson MyLab, which embeds the entire quiz player in a cross-origin iframe (`tdx.acs.pearson.com/Player/Player.aspx`).

Three specific failures on Pearson:

1. **Click fails** — The LLM extracts option text from the visual screenshot (e.g. `"D. $75,000; $64,000."`) but the DOM in the player iframe has double spaces (`"D.  $75,000; $64,000."`). All text-based matching strategies fail, including the JS fallback.
2. **Navigation fails** — The "Check answer" button lives inside the player iframe. Searching all frames in the wrong order means it's often missed.
3. **Page text is wrong** — `_page_text()` concatenates text from all frames. The player iframe body returns sidebar navigation text (2012 chars), not the question text. The LLM relies almost entirely on the screenshot.

## Goal

Make QuizLoop reliable on any iframe-wrapped LMS platform — Pearson today, others tomorrow — by introducing the concept of an **active frame** and clicking by **letter index** rather than by text.

## Architecture

```
Each loop iteration:
  _active_frame()   ← find the frame with the most interactive inputs
  _page_text()      ← extract body text from active frame only
  _scout()          ← full-page screenshot + active-frame text → PageScan
  _answer()         ← no change
  _select()         ← click ops scoped to active frame, by index
  _navigate()       ← button search scoped to active frame
  _verify()         ← no change (screenshot-based)
```

`_active_frame()` is called fresh at the start of every loop iteration, so platforms that swap the player frame on each question (like Pearson) always get the right frame.

## Active Frame Discovery (`_active_frame`)

Scans all frames via a single JS evaluate per frame. Counts:
- `input[type='radio']`
- `input[type='checkbox']`
- `input[type='text']`
- `textarea`

Returns the frame with the highest total count. Falls back to `page.main_frame` if all counts are zero. No LLM call — pure DOM scan, ~5ms.

## Click Strategy (`_click_option`)

Replaces the current 6-strategy text-matching waterfall with a two-stage approach:

**Stage 1 — Click by letter index (MCQ/truefalse/multi)**

Parse the letter prefix from the LLM's answer value:
```
"D. $75,000; $64,000."  →  letter="D"  →  index=3
"A. fork"               →  letter="A"  →  index=0
"True" / "False"        →  no letter   →  skip to Stage 2
```

Find all `input[type='radio'], input[type='checkbox']` in the active frame. Click `inputs.nth(index)`. No text matching — immune to whitespace, encoding, or formatting differences.

**Stage 2 — JS normalized text fallback**

Used when no letter prefix is found (True/False without prefix, generic options) or when index click fails. Searches only the active frame with whitespace-normalized `includes()` matching. Same JS logic as current but scoped to one frame.

**Multi questions:** parse each letter in the list, call Stage 1 for each.

**Text questions:** `frame.locator("textarea, input[type='text']").first.fill(value)` in active frame.

## Navigation (`_navigate`)

Scoped to active frame only. Tries:
1. Playwright `get_by_role("button", name=pattern)` in active frame
2. JS fallback with normalized text match in active frame

Handles Pearson's "Check answer" (lowercase a) automatically since the active frame is the player frame.

## Page Text (`_page_text`)

Extracts `inner_text("body")` from the active frame only instead of concatenating all frames. If the active frame returns less than 200 chars (sidebar/nav only), falls back to main frame text. This gives the scout LLM better signal.

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| No frame has any inputs | Fall back to main frame for all operations |
| Active frame changes between questions | Re-discovered each loop iteration — always fresh |
| Letter index out of range (more options than expected) | Fall through to JS text fallback |
| JS fallback also fails | Log warning, continue (existing behaviour) |

## Files Changed

| File | Change |
|------|--------|
| `ace/quiz/loop.py` | Add `_active_frame()`, `_parse_option_letter()`; rewrite `_click_option()`; update `_page_text()`, `_fill_text()`, `_navigate()` to use active frame |

No changes to models, prompts, orchestrator, or CLI.

## Testing

- `tests/quiz/test_loop.py` — add tests for `_active_frame()` (mock frames with different input counts), `_parse_option_letter()` (letter/no-letter cases), `_click_option()` (index path + fallback path)
- Existing tests must still pass
