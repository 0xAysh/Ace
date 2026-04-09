import pytest
from ace.quiz.models import Question, PageScan, Answer, AnswerPlan, VerifyResult


def test_question_mcq():
    q = Question(id="q1", text="What is X?", options=["A. foo", "B. bar"], kind="mcq")
    assert q.id == "q1"
    assert len(q.options) == 2


def test_question_text():
    q = Question(id="q1", text="Explain X", options=[], kind="text")
    assert q.kind == "text"
    assert q.options == []


def test_page_scan():
    scan = PageScan(
        platform="canvas",
        all_on_page=False,
        has_check_button=True,
        questions=[Question(id="q1", text="X?", options=["A", "B"], kind="mcq")],
    )
    assert scan.platform == "canvas"
    assert len(scan.questions) == 1


def test_answer_plan():
    plan = AnswerPlan(answers=[Answer(question_id="q1", value="A. fork")])
    assert plan.answers[0].question_id == "q1"


def test_verify_result_done():
    v = VerifyResult(all_correct=True, issues=[], next_action="done")
    assert v.next_action == "done"


def test_verify_result_invalid_action():
    with pytest.raises(Exception):
        VerifyResult(all_correct=True, issues=[], next_action="invalid")
