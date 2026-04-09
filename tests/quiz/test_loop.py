import asyncio
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ace.quiz.models import PageScan, Question, AnswerPlan, Answer, VerifyResult
from ace.quiz.loop import QuizLoop


def _make_page(screenshot_bytes=b"fakepng", body_text="Question 1\nA. fork\nB. exec"):
    page = AsyncMock()
    page.screenshot = AsyncMock(return_value=screenshot_bytes)
    page.inner_text = AsyncMock(return_value=body_text)
    return page


def _make_llm():
    llm = AsyncMock()
    return llm


def _completion(value):
    result = MagicMock()
    result.completion = value
    return result


@pytest.mark.asyncio
async def test_scout_returns_page_scan():
    page = _make_page()
    llm = _make_llm()
    scan = PageScan(
        platform="canvas",
        all_on_page=False,
        has_check_button=False,
        questions=[Question(id="q1", text="What calls fork?", options=["A. fork", "B. exec"], kind="mcq")],
    )
    llm.ainvoke = AsyncMock(return_value=_completion(scan))

    loop = QuizLoop(page, llm)
    result = await loop._scout()

    assert result.platform == "canvas"
    assert len(result.questions) == 1
    llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_answer_returns_answer_plan():
    page = _make_page()
    llm = _make_llm()
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])
    llm.ainvoke = AsyncMock(return_value=_completion(plan))

    questions = [Question(id="q1", text="What?", options=["A. fork", "B. exec"], kind="mcq")]
    loop = QuizLoop(page, llm)
    result = await loop._answer(questions)

    assert result.answers[0].value == "A. fork"
    # Answer call must NOT contain image content (no screenshot)
    call_args = llm.ainvoke.call_args
    messages = call_args[0][0]
    from browser_use.llm.messages import ContentPartImageParam
    for msg in messages:
        if hasattr(msg, 'content') and isinstance(msg.content, list):
            for part in msg.content:
                assert not isinstance(part, ContentPartImageParam)


@pytest.mark.asyncio
async def test_scout_passes_screenshot():
    page = _make_page(screenshot_bytes=b"pngdata")
    llm = _make_llm()
    scan = PageScan(platform="generic", all_on_page=False, has_check_button=False, questions=[])
    llm.ainvoke = AsyncMock(return_value=_completion(scan))

    loop = QuizLoop(page, llm)
    await loop._scout()

    call_args = llm.ainvoke.call_args
    messages = call_args[0][0]
    from browser_use.llm.messages import ContentPartImageParam
    found_image = False
    for msg in messages:
        if hasattr(msg, 'content') and isinstance(msg.content, list):
            for part in msg.content:
                if isinstance(part, ContentPartImageParam):
                    found_image = True
    assert found_image, "Scout call must include a screenshot"
