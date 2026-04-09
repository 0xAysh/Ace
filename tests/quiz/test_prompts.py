from ace.quiz.prompts import SCOUT_PROMPT, ANSWER_PROMPT, VERIFY_PROMPT


def test_prompts_are_non_empty_strings():
    for prompt in (SCOUT_PROMPT, ANSWER_PROMPT, VERIFY_PROMPT):
        assert isinstance(prompt, str)
        assert len(prompt) > 50


def test_scout_prompt_mentions_platform():
    assert "platform" in SCOUT_PROMPT.lower()


def test_answer_prompt_mentions_correct():
    assert "correct" in ANSWER_PROMPT.lower()


def test_verify_prompt_mentions_next_action():
    assert "next_action" in VERIFY_PROMPT
