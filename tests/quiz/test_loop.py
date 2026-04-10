import asyncio
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ace.quiz.models import PageScan, Question, AnswerPlan, Answer, VerifyResult, NavAction
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
async def test_verify_detects_checked_radio():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=2)
    loop = QuizLoop(page, MagicMock())
    loop._active_frame = AsyncMock(return_value=player_frame)
    player_frame.evaluate = AsyncMock(return_value={"rc": 1, "cc": 0, "tf": 0})

    result = await loop._verify()

    assert result.all_correct is True
    assert result.issues == []
    assert result.next_action == "check"


@pytest.mark.asyncio
async def test_verify_detects_no_selection():
    page, main_frame, player_frame = _make_page_with_frame(player_input_count=2)
    loop = QuizLoop(page, MagicMock())
    loop._active_frame = AsyncMock(return_value=player_frame)
    player_frame.evaluate = AsyncMock(return_value={"rc": 0, "cc": 0, "tf": 0})

    result = await loop._verify()

    assert result.all_correct is False
    assert len(result.issues) == 1



@pytest.mark.asyncio
async def test_run_completes_single_question():
    page, main_frame, player_frame = _make_page_with_frame(
        player_input_count=2,
        player_body="question text " * 20,
    )

    llm = _make_llm()
    scan = PageScan(
        platform="pearson",
        all_on_page=False,
        has_check_button=True,
        questions=[Question(id="q1", text="What?", options=["A. fork", "B. exec"], kind="mcq")],
    )
    empty_scan = PageScan(platform="pearson", all_on_page=False, has_check_button=False, questions=[])
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])

    # 1st scout → question, answer, 2nd scout → empty (done)
    llm.ainvoke = AsyncMock(side_effect=[
        _completion(scan),
        _completion(plan),
        _completion(empty_scan),
    ])

    loop = QuizLoop(page, llm)
    loop._active_frame = AsyncMock(return_value=player_frame)
    loop._click_option = AsyncMock()
    loop._verify = AsyncMock(return_value=VerifyResult(all_correct=True, issues=[], next_action="check"))
    loop._navigate_smart = AsyncMock()

    await loop.run()

    assert llm.ainvoke.call_count == 3  # scout + answer + scout(empty)


@pytest.mark.asyncio
async def test_run_completes_if_no_questions():
    page = _make_page()
    llm = _make_llm()
    empty_scan = PageScan(platform="generic", all_on_page=False, has_check_button=False, questions=[])
    llm.ainvoke = AsyncMock(return_value=_completion(empty_scan))

    loop = QuizLoop(page, llm)
    await loop.run()  # Should return gracefully, not raise


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
async def test_page_text_falls_back_to_richest_frame():
    """When the active frame has sparse text, _page_text scans all frames
    and returns the one with the most content."""
    page, main_frame, player_frame = _make_page_with_frame(
        player_input_count=4,
        player_body="nav",  # < 200 chars
    )
    # main_frame has short text, but a third "content" frame has long text
    content_frame = _make_frame(input_count=0, body_text="full page content " * 20)
    page.frames = [main_frame, player_frame, content_frame]
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


@pytest.mark.asyncio
async def test_navigate_smart_clicks_then_done():
    """LLM returns click on first iteration, done on second — verify two LLM calls.
    The pre-step also clicks the check button, so _click_by_text is called twice total."""
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
    # Pre-step clicks "Check Answer" + LLM loop clicks "Check Answer" = 2 total
    assert loop._click_by_text.await_count == 2


@pytest.mark.asyncio
async def test_navigate_smart_skips_missing_button():
    """LLM returns a target not in the button list — LLM click is skipped, loop continues.
    The pre-step still clicks the check button deterministically (1 click total)."""
    page = AsyncMock()
    page.frames = []
    page.screenshot = AsyncMock(return_value=b"fakepng")
    page.wait_for_load_state = AsyncMock()

    llm = _make_llm()
    loop = QuizLoop(page, llm)
    # Use a non-check button so the pre-step does NOT fire, keeping LLM behavior isolated
    loop._collect_buttons = AsyncMock(return_value=["Next Question"])
    loop._click_by_text = AsyncMock(return_value=True)

    llm.ainvoke = AsyncMock(side_effect=[
        _completion(NavAction(action="click", target="Nonexistent Button", reason="hallucinated")),
        _completion(NavAction(action="done", target=None, reason="done")),
    ])

    await loop._navigate_smart()

    # Pre-step: no check button → no pre-click. LLM target not in list → no LLM click.
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

    with patch("ace.quiz.loop._NAV_SLEEP_S", 0.0):
        await loop._navigate_smart()  # must not raise

    assert llm.ainvoke.call_count == 8
