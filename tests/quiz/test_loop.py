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
    import base64 as b64_module
    expected_b64 = b64_module.b64encode(b"pngdata").decode()
    found_image = False
    for msg in messages:
        if hasattr(msg, 'content') and isinstance(msg.content, list):
            for part in msg.content:
                if isinstance(part, ContentPartImageParam):
                    found_image = True
                    assert expected_b64 in part.image_url.url
    assert found_image, "Scout call must include a screenshot"


@pytest.mark.asyncio
async def test_select_clicks_mcq_option():
    page = _make_page()
    label_mock = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock(
        filter=MagicMock(return_value=MagicMock(
            first=MagicMock(
                click=AsyncMock(),
                wait_for=AsyncMock(),
            )
        ))
    ))

    llm = _make_llm()
    questions = [Question(id="q1", text="X?", options=["A. fork", "B. exec"], kind="mcq")]
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])

    loop = QuizLoop(page, llm)
    # Should not raise
    await loop._select(plan, questions)
    page.locator.assert_called()

    # Verify that click was actually called on the first successful strategy's locator
    first_locator = page.locator.return_value.filter.return_value.first
    first_locator.click.assert_called_once()


@pytest.mark.asyncio
async def test_verify_returns_verify_result():
    page = _make_page()
    llm = _make_llm()
    vr = VerifyResult(all_correct=True, issues=[], next_action="next")
    llm.ainvoke = AsyncMock(return_value=_completion(vr))

    loop = QuizLoop(page, llm)
    result = await loop._verify()

    assert result.all_correct is True
    assert result.next_action == "next"
    # Verify must include a screenshot
    call_args = llm.ainvoke.call_args
    messages = call_args[0][0]
    from browser_use.llm.messages import ContentPartImageParam
    found_image = any(
        isinstance(part, ContentPartImageParam)
        for msg in messages
        if hasattr(msg, 'content') and isinstance(msg.content, list)
        for part in msg.content
    )
    assert found_image


@pytest.mark.asyncio
async def test_navigate_next_clicks_button():
    page = _make_page()
    btn = AsyncMock()
    btn.count = AsyncMock(return_value=1)
    btn.click = AsyncMock()
    page.get_by_role = MagicMock(return_value=btn)
    page.wait_for_load_state = AsyncMock()

    llm = _make_llm()
    loop = QuizLoop(page, llm)
    await loop._navigate("next")
    btn.click.assert_called_once()


@pytest.mark.asyncio
async def test_run_completes_single_question():
    page = _make_page()
    page.locator = MagicMock(return_value=MagicMock(
        filter=MagicMock(return_value=MagicMock(
            first=MagicMock(click=AsyncMock(), wait_for=AsyncMock())
        ))
    ))
    page.get_by_role = MagicMock(return_value=MagicMock(
        count=AsyncMock(return_value=1),
        click=AsyncMock(),
    ))
    page.wait_for_load_state = AsyncMock()

    llm = _make_llm()
    scan = PageScan(
        platform="canvas",
        all_on_page=False,
        has_check_button=False,
        questions=[Question(id="q1", text="What?", options=["A. fork", "B. exec"], kind="mcq")],
    )
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])
    verify_done = VerifyResult(all_correct=True, issues=[], next_action="done")

    # ainvoke returns scan → plan → verify_done
    llm.ainvoke = AsyncMock(side_effect=[
        _completion(scan),
        _completion(plan),
        _completion(verify_done),
    ])

    loop = QuizLoop(page, llm)
    await loop.run()  # should return without error

    assert llm.ainvoke.call_count == 3  # scout + answer + verify
