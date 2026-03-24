from ace.platforms.base import Question, QuestionType


SYSTEM_PROMPT = (
    "You are an expert academic assistant. Answer quiz questions accurately and concisely. "
    "Follow the output format exactly."
)


def build_user_prompt(question: Question, context_chunks: list[str]) -> str:
    lines: list[str] = []

    lines.append(f"Q{question.number}/{question.total} [{question.type.value}]")
    lines.append(question.text.strip())

    if question.options:
        lines.append("")
        for opt in question.options:
            lines.append(f"{opt.label}. {opt.text}")

    if context_chunks:
        lines.append("\n--- Context ---")
        lines.extend(context_chunks[:3])

    lines.append("")
    lines.append(_type_instruction(question))

    return "\n".join(lines)


def _type_instruction(question: Question) -> str:
    if question.type == QuestionType.MCQ:
        return "Reply with ONLY the letter of the correct answer (e.g. A)."
    if question.type == QuestionType.TRUE_FALSE:
        return "Reply with ONLY True or False."
    if question.type == QuestionType.SHORT_ANSWER:
        return "Reply with a concise 1-3 sentence answer."
    if question.type == QuestionType.ESSAY:
        return "Reply with a well-structured essay answer."
    if question.type == QuestionType.FILL_IN_BLANK:
        return "Reply with ONLY the word or phrase that fills the blank."
    if question.type == QuestionType.NUMERIC:
        return "Reply with ONLY the numeric value (include units if needed)."
    if question.type == QuestionType.MATCHING:
        return "Reply with matches in format: 1-A, 2-C, 3-B etc."
    return "Answer the question accurately."
