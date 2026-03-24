"""Browser utility helpers."""
import asyncio
import base64
import random
from typing import Optional

from markdownify import markdownify
from playwright.async_api import Page, Frame


async def human_delay(min_ms: int = 400, max_ms: int = 1200) -> None:
    """Wait a random human-like delay."""
    await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)


async def markdown_snapshot(page_or_frame: Page | Frame) -> str:
    """Convert current page/frame content to clean Markdown."""
    html = await page_or_frame.content()
    md = markdownify(html, heading_style="ATX", strip=["script", "style", "nav", "footer", "header"])
    # Collapse excessive blank lines
    import re
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


async def screenshot_element(page: Page, selector: str) -> Optional[bytes]:
    """Screenshot a specific element and return PNG bytes, or None if not found."""
    try:
        el = await page.query_selector(selector)
        if el:
            return await el.screenshot()
    except Exception:
        pass
    return None


async def screenshot_region(page: Page, x: int, y: int, width: int, height: int) -> bytes:
    return await page.screenshot(clip={"x": x, "y": y, "width": width, "height": height})


def png_to_base64(png_bytes: bytes) -> str:
    return base64.standard_b64encode(png_bytes).decode()


async def wait_for_question(page: Page, timeout: int = 5_000) -> bool:
    """
    Check if the current page (or any of its frames) has answerable question inputs.
    """
    combined = (
        "input[type='radio'], input[type='checkbox'], "
        "input[type='text'], input[type='number'], "
        "textarea, [data-question-id], .question_holder, .quiz-question"
    )
    # Check top-level page first
    try:
        await page.wait_for_selector(combined, state="visible", timeout=timeout)
        return True
    except Exception:
        pass
    # Check all frames (e.g. Pearson embeds questions inside iframes)
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
