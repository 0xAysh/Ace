"""Browser utility helpers."""
from playwright.async_api import Page


async def wait_for_question(page: Page, timeout: int = 5_000) -> bool:
    """Check if the current page (or any of its frames) has answerable question inputs."""
    combined = (
        "input[type='radio'], input[type='checkbox'], "
        "input[type='text'], input[type='number'], "
        "textarea, [data-question-id], .question_holder, .quiz-question"
    )
    try:
        await page.wait_for_selector(combined, state="visible", timeout=timeout)
        return True
    except Exception:
        pass
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            el = await frame.query_selector(combined)
            if el:
                return True
        except Exception:
            continue
    return False
