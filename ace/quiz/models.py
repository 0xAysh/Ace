"""Pydantic models for LLM-structured I/O: scout (PageScan), answer (AnswerPlan), verify (VerifyResult)."""
from typing import Literal
from pydantic import BaseModel


class Question(BaseModel):
    id: str
    text: str
    options: list[str]
    kind: Literal["mcq", "truefalse", "multi", "text"]


class PageScan(BaseModel):
    platform: str
    all_on_page: bool
    has_check_button: bool
    questions: list[Question]


class Answer(BaseModel):
    question_id: str
    value: str | list[str]


class AnswerPlan(BaseModel):
    answers: list[Answer]


class VerifyResult(BaseModel):
    all_correct: bool
    issues: list[str]
    next_action: Literal["check", "next", "done"]


class NavAction(BaseModel):
    action: Literal["click", "done"]
    target: str | None = None  # exact button label from _collect_buttons(); None when action="done"
    reason: str                 # shown in debug output
