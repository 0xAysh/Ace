from playwright.async_api import Page
from ace.platforms.base import BasePlatform
from ace.platforms.canvas import CanvasAdapter
from ace.platforms.generic import GenericAdapter

_ADAPTERS: list[BasePlatform] = [
    CanvasAdapter(),
    GenericAdapter(),   # always last — fallback
]


async def platform_for_page(page: Page) -> BasePlatform:
    """Return the first adapter that recognises the current page."""
    for adapter in _ADAPTERS:
        if await adapter.is_assignment_page(page):
            return adapter
    return GenericAdapter()
