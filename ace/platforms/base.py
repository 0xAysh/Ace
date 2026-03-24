"""Base platform interface and shared data models."""
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from playwright.async_api import Page


class QuestionType(str, Enum):
    MCQ = "mcq"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    ESSAY = "essay"
    FILL_IN_BLANK = "fill_in_blank"
    MATCHING = "matching"
    NUMERIC = "numeric"
    UNKNOWN = "unknown"


class QuestionOption(BaseModel):
    label: str          # e.g. "A", "B", "True", "False"
    text: str           # display text
    selector: str       # CSS selector to click this option


class Question(BaseModel):
    id: str
    number: int         # 1-based question number
    total: int          # total questions in this quiz
    type: QuestionType
    text: str           # clean question text (MathJax resolved)
    options: list[QuestionOption] = []
    input_selector: str = ""    # for text-based answers
    has_image: bool = False
    image_selector: str = ""    # CSS selector of the image element
    points: Optional[float] = None
    raw_html: str = ""          # original HTML for debugging


class SubmissionResult(BaseModel):
    success: bool
    message: str = ""
    score: Optional[str] = None


class BasePlatform(ABC):
    name: str = "generic"

    @abstractmethod
    async def is_assignment_page(self, page: Page) -> bool:
        """Return True if the current page is a quiz/assignment we can handle."""

    @abstractmethod
    async def extract_current_question(self, page: Page) -> Question:
        """Extract the currently visible question from the page."""

    @abstractmethod
    async def is_last_question(self, page: Page) -> bool:
        """Return True if this is the last question (Submit button visible)."""

    @abstractmethod
    async def fill_answer(self, page: Page, question: Question, answer: str) -> None:
        """Fill in the answer for the given question."""

    @abstractmethod
    async def click_next(self, page: Page) -> None:
        """Click the Next Question button and wait for the next question to load."""

    @abstractmethod
    async def click_submit(self, page: Page) -> SubmissionResult:
        """Click the final Submit button and return the result."""

    @abstractmethod
    async def wait_for_next_question(self, page: Page) -> None:
        """Wait until the next question has loaded after clicking Next."""
