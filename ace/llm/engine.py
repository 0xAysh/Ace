"""
LLM answering engine.
Calls Claude via instructor for structured AnswerResponse output.
Auto-escalates from Haiku to Sonnet when needed.
"""
import base64
from typing import Optional

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from rich.console import Console

from ace.llm.client import make_instructor_client, choose_model
from ace.llm.models import AnswerResponse, CostTracker
from ace.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from ace.platforms.base import Question, QuestionType
from ace.config import get_settings

console = Console()

_RETRYABLE = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


class LLMEngine:
    def __init__(self) -> None:
        self.client = make_instructor_client()
        self.cost = CostTracker()
        self.settings = get_settings()

    async def answer_question(
        self,
        question: Question,
        context_chunks: list[str],
        image_bytes: Optional[bytes] = None,
    ) -> AnswerResponse:
        model = choose_model(question.type, has_image=image_bytes is not None)
        console.print(f"[dim]  → Calling [bold]{model}[/bold]{'  [image attached]' if image_bytes else ''}...[/dim]")

        response = await self._call(question, context_chunks, image_bytes, model)

        if not response.answer.strip():
            console.print(f"[yellow]  ⚠ Empty answer from {model} — escalating to {self.settings.fallback_model}[/yellow]")
            model = self.settings.fallback_model
            response = await self._call(question, context_chunks, image_bytes, model)

        if (
            response.confidence < self.settings.confidence_threshold
            and model == self.settings.default_model
        ):
            console.print(
                f"[dim]  → Confidence {response.confidence:.0%} below threshold — escalating to {self.settings.fallback_model}[/dim]"
            )
            model = self.settings.fallback_model
            response = await self._call(question, context_chunks, image_bytes, model)

        if response.confidence < self.settings.confidence_threshold:
            response.needs_human_review = True
            console.print(f"[yellow]  ⚠ Still low confidence ({response.confidence:.0%}) after escalation — flagged for review[/yellow]")

        console.print(
            f"[dim]  → Answer: [bold green]{response.answer[:80]}[/bold green]  "
            f"confidence: {response.confidence:.0%}  "
            f"tokens: {self.cost.input_tokens + self.cost.output_tokens:,}[/dim]"
        )

        return response

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    async def _call(
        self,
        question: Question,
        context_chunks: list[str],
        image_bytes: Optional[bytes],
        model: str,
    ) -> AnswerResponse:
        user_content: list = []

        if image_bytes:
            user_content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(image_bytes).decode(),
                },
            })

        user_content.append({
            "type": "text",
            "text": build_user_prompt(question, context_chunks),
        })

        try:
            response, completion = self.client.chat.completions.create_with_completion(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": user_content}],
                system=SYSTEM_PROMPT,
                response_model=AnswerResponse,
            )
        except anthropic.AuthenticationError:
            console.print(
                "[bold red]Error:[/bold red] Invalid API key.\n"
                "Check your [bold]ANTHROPIC_API_KEY[/bold] in .env"
            )
            raise SystemExit(1)
        except anthropic.BadRequestError as e:
            console.print(f"[bold red]Error:[/bold red] Bad request to Claude API: {e}")
            raise SystemExit(1)
        except _RETRYABLE as e:
            console.print(f"[yellow]  ⚠ API error ({type(e).__name__}), retrying...[/yellow]")
            raise

        if hasattr(completion, "usage") and completion.usage:
            self.cost.add(
                completion.usage.input_tokens,
                completion.usage.output_tokens,
                model,
            )

        return response
