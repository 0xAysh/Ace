# Quiz Loop Design

**Date:** 2026-04-07  
**Status:** Approved

## Problem

The current implementation runs a single browser-use `Agent` with a generic TASK string. This causes:
- 10+ LLM calls per page (one per browser action) → slow
- No page-level awareness → poor accuracy (model doesn't see all options before picking)
- Platform-specific behaviour baked into the prompt → brittle

## Goal

Replace the browser-use agent loop with a custom `QuizLoop` that:
1. Detects platform type on first look
2. Batches all visible questions into one LLM answer call
3. Confirms selections with a verify step before navigating
4. Handles all platforms (all-on-page, one-at-a-time, check-then-next) generically

---

## Architecture

`Orchestrator._run()` currently calls `Agent(task=TASK, ...).run()`. That call is replaced with `QuizLoop(page, llm).run()`. Everything outside it — browser setup, submit gate, CLI — is unchanged.

```
Orchestrator._run()
  └── QuizLoop(page, llm).run()
        ├── scout(page)            → PageScan (platform type + all visible questions)
        ├── answer(questions)      → AnswerPlan (correct answer per question)
        ├── select(page, answers)  → playwright clicks
        ├── verify(page)           → VerifyResult (confirm selections + next action)
        └── navigate(page, action) → click Check / Next / detect done
```

---

## New Files

| File | Purpose |
|---|---|
| `ace/quiz/models.py` | Pydantic models for LLM I/O |
| `ace/quiz/prompts.py` | The 3 LLM prompt strings |
| `ace/quiz/loop.py` | `QuizLoop` — the main loop |

`ace/quiz/__init__.py` exposes `QuizLoop`.

---

## Data Models (`ace/quiz/models.py`)

```python
class Question(BaseModel):
    id: str                          # "q1", "q2", ...
    text: str                        # full question body
    options: list[str]               # ["A. fork", "B. exec", ...]; empty for free-text
    kind: Literal["mcq", "truefalse", "text"]

class PageScan(BaseModel):
    platform: str                    # "canvas", "pearson", "blackboard", "generic", etc.
    all_on_page: bool                # True = all questions visible at once
    has_check_button: bool           # True = must click Check before Next
    questions: list[Question]

class Answer(BaseModel):
    question_id: str
    value: str                       # exact option text for mcq/truefalse; free text otherwise

class AnswerPlan(BaseModel):
    answers: list[Answer]

class VerifyResult(BaseModel):
    all_correct: bool
    issues: list[str]                # e.g. ["q3 appears unselected"]
    next_action: Literal["check", "next", "done"]
    # done = only Submit/Finish visible, no more questions to answer
```

---

## LLM Calls (`ace/quiz/prompts.py`)

Three calls per page (or per question on one-at-a-time platforms):

**1. Scout** — takes screenshot + page text, returns `PageScan`  
Prompt instructs the model to: identify the platform, list every visible question with its options and type, flag whether Check button exists.

**2. Answer** — takes list of `Question`, returns `AnswerPlan`  
Prompt instructs the model to: apply knowledge to each question and return the exact option text (or free-text answer) for each.  
No screenshot — pure knowledge call. This keeps vision tokens out of the answer step.

**3. Verify** — takes updated screenshot + page text, returns `VerifyResult`  
Prompt instructs the model to: confirm each selected answer matches the plan, list any discrepancies, and declare the next action.

---

## Loop Logic (`ace/quiz/loop.py`)

```
QuizLoop.run():
  platform_scan = None

  while True:
    screenshot + page_text → scout() → PageScan
    
    if platform_scan is None:
      platform_scan = result   # remember platform for the session

    if all_on_page:
      answer_plan = answer(all questions)
      select(all answers)
    else:
      answer_plan = answer([current question])
      select(answer)

    screenshot → verify() → VerifyResult

    # Retry loop (max 2 retries if verify finds issues)
    retries = 0
    while not verify_result.all_correct and retries < 2:
      re-select flagged answers
      screenshot → verify() → VerifyResult
      retries += 1

    if next_action == "check":
      click Check button
      wait for page response
    elif next_action == "next":
      click Next button
      wait for page load
    elif next_action == "done":
      break  # back to Orchestrator submit gate
```

---

## Error Handling

| Situation | Behaviour |
|---|---|
| Scout finds no questions | Raise `RuntimeError("No questions found on page")` → surfaced to user |
| Verify still wrong after 2 retries | Log warning, move to next_action anyway |
| Navigation button not found | Re-run verify on current screenshot to re-evaluate state |
| LLM call fails | Propagates up — existing `GroqCompat` retry logic handles rate limits |

---

## Model

Default: `meta-llama/llama-4-scout-17b-16e-instruct`

- Supports vision natively on Groq → screenshots work in scout + verify calls
- In `JsonSchemaModels` in browser-use → `GroqCompat` uses native json_schema path, no fallback needed
- 750 t/s on Groq free tier — fastest available vision model

`GroqCompat` wrapper remains for users who switch to `qwen/qwen3-32b` manually (text-only, json_object fallback).

---

## Changes to Existing Files

**`ace/orchestrator.py`:**
- Remove `TASK` string constant
- Remove `from browser_use import Agent`
- Replace `agent = Agent(task=task, ...)` + `agent.run()` with `await QuizLoop(page, llm).run()`
- `browser` object still created for CDP connection; passed to `QuizLoop`

**`ace/config/settings.py`:**
- Default `groq_model` already set to `meta-llama/llama-4-scout-17b-16e-instruct` ✓
