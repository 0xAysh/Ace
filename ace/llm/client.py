"""
Anthropic SDK client with automatic model escalation.
Default: Haiku (fast + cheap)
Escalates to Sonnet when: has image, low confidence, or essay question.
"""
import httpx
import instructor
import anthropic
from rich.console import Console

from ace.config import get_settings
from ace.platforms.base import QuestionType

console = Console()


def make_instructor_client() -> instructor.Instructor:
    settings = get_settings()

    if not settings.anthropic_api_key:
        console.print(
            "[bold red]Error:[/bold red] ANTHROPIC_API_KEY is not set.\n"
            "Add it to your [bold].env[/bold] file:\n\n"
            "  [cyan]ANTHROPIC_API_KEY=sk-ant-...[/cyan]\n\n"
            "Get your key at https://console.anthropic.com"
        )
        raise SystemExit(1)

    raw = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    return instructor.from_anthropic(raw)


def choose_model(
    question_type: QuestionType,
    has_image: bool,
    force_fallback: bool = False,
) -> str:
    settings = get_settings()
    if force_fallback or has_image or question_type == QuestionType.ESSAY:
        return settings.fallback_model
    return settings.default_model
