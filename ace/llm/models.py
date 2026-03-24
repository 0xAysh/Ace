from pydantic import BaseModel, Field


class AnswerResponse(BaseModel):
    answer: str = Field(description=(
        "The final answer. For MCQ: just the option letter (A/B/C/D). "
        "For True/False: 'True' or 'False'. "
        "For short answer/essay: the full answer text. "
        "For fill-in-blank or numeric: just the value."
    ))
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your confidence in this answer from 0.0 to 1.0."
    )
    reasoning: str = Field(
        description="Your chain-of-thought reasoning. This is NOT shown to the grader."
    )
    needs_human_review: bool = Field(
        default=False,
        description="Set True if you are uncertain and the user should verify before submitting."
    )


class CostTracker(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def add(self, inp: int, out: int, model: str) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.model = model

    def estimate_cost_usd(self) -> float:
        """Rough estimate based on Haiku pricing."""
        # Haiku: $0.80/M input, $4/M output
        return (self.input_tokens * 0.80 + self.output_tokens * 4.0) / 1_000_000
