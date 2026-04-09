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
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=2)
    # _active_frame count=2, then JS index click returns True
    player_frame.evaluate = AsyncMock(side_effect=[2, True])

    questions = [Question(id="q1", text="X?", options=["A. fork", "B. exec"], kind="mcq")]
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])

    loop = QuizLoop(page, MagicMock())
    await loop._select(plan, questions)

    # Second evaluate call is the JS click with index=0 (A)
    assert player_frame.evaluate.call_count == 2
    assert player_frame.evaluate.call_args_list[1][0][1] == 0  # index 0 for A


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


@pytest.mark.asyncio
async def test_run_completes_single_question():
    page, main_frame, player_frame = _make_page_with_frame(
        player_input_count=2,
        player_body="question text " * 20,
    )
    # _active_frame is called multiple times; JS click returns True
    player_frame.evaluate = AsyncMock(side_effect=[2, True, 2, 2])

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


@pytest.mark.asyncio
async def test_run_raises_if_no_questions():
    page = _make_page()
    llm = _make_llm()
    empty_scan = PageScan(platform="generic", all_on_page=False, has_check_button=False, questions=[])
    llm.ainvoke = AsyncMock(return_value=_completion(empty_scan))

    loop = QuizLoop(page, llm)
    with pytest.raises(RuntimeError, match="No questions found on page"):
        await loop.run()


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


@pytest.mark.asyncio
async def test_click_option_by_letter_index():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=4)
    # JS evaluate returns True (clicked successfully) for index=2 (C)
    player_frame.evaluate = AsyncMock(side_effect=[4, True])  # _active_frame count, then click result

    loop = QuizLoop(page, MagicMock())
    await loop._click_option("C. something")

    # Second evaluate call should be the index click with index=2
    assert player_frame.evaluate.call_count == 2
    call_args = player_frame.evaluate.call_args_list[1]
    assert call_args[0][1] == 2  # index argument = 2 for letter C


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


@pytest.mark.asyncio
async def test_fill_text_uses_active_frame():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=4)

    textarea = MagicMock()
    textarea.wait_for = AsyncMock()
    textarea.fill = AsyncMock()

    inputs_locator = MagicMock()
    inputs_locator.first = textarea

    player_frame.locator = MagicMock(return_value=inputs_locator)

    loop = QuizLoop(page, MagicMock())
    await loop._fill_text("my answer")

    player_frame.locator.assert_called()
    textarea.fill.assert_awaited_once_with("my answer")
