SCOUT_PROMPT = """\
You are analyzing a quiz or assignment page screenshot.

Examine the screenshot and page text carefully and return structured data about what you see.

Return:
- platform: the LMS name ("canvas", "pearson", "blackboard", "moodle", "generic")
- all_on_page: true if ALL quiz questions are visible in the main content area at once; false if only one question is shown at a time
- has_check_button: true if a "Check answer", "Check Answer", "Check My Answer", or "Submit Answer" button is currently visible
- questions: list of every question in the MAIN CONTENT AREA only (ignore sidebar/navigation lists), each with:
  - id: "q1", "q2", etc. (sequential)
  - text: the full question text
  - options: list of answer option texts exactly as shown (e.g. ["A. fork", "B. exec", "C. malloc"]); empty list for free-text questions
  - kind: "mcq" for single-select multiple choice, "truefalse" for true/false, "multi" for multi-select (checkboxes, select all that apply), "text" for free-text input

If there are no questions visible on the page, return an empty questions list.
"""

ANSWER_PROMPT = """\
You are answering academic quiz questions. Use your knowledge to determine the correct answer for each question.

Return a JSON object with a single key "answers" containing an array of objects, one per question:
- question_id: the id (e.g. "q1")
- value: for mcq/truefalse, the EXACT option text as provided (e.g. "A. fork"); for multi, a JSON array of EXACT option texts for all correct answers (e.g. ["A. fork", "C. malloc"]); for text questions, your answer string

Be precise. Think carefully before answering. The value must match one of the provided options exactly for mcq/truefalse questions.
"""

VERIFY_PROMPT = """\
You are verifying that quiz answers have been correctly selected on screen.

IMPORTANT: Focus ONLY on the main question content area (the question being actively answered).
IGNORE any question list, sidebar navigation, or question index panel — those show overall assignment
progress and are NOT the questions you need to verify.

Examine the updated screenshot carefully. For each question in the main content area:
- Check if the answer is visually selected (radio button filled in, checkbox checked, text entered in field)
- Note any answer that appears unselected or wrong

Return:
- all_correct: true if every question in the main content area has an answer selected
- issues: list of strings describing problems, e.g. ["q1: no option selected", "q1: text field is empty"]
  (leave empty if all_correct is true)
- next_action:
  - "check" if a "Check answer" / "Check Answer" / "Check My Answer" button is visible at the bottom of the page
  - "next" if a Next / Next Question / Continue button is visible
  - "done" if a Submit / Finish / Done button is visible AND all visible questions appear correctly answered
"""

NAV_PROMPT = """\
You are navigating a quiz page. An answer was just selected.

You are given a screenshot of the current page and a list of visible buttons.

Your task: determine what to click to advance to the next question.

Return:
- action: "click" to click a button, or "done" if a new question is already visible
  or no further navigation is needed
- target: copy the EXACT button label text from the visible buttons list — character for character
- reason: brief explanation of your choice

PRIORITY ORDER (check each in order, pick the first that applies):
1. If a Yes/No confirmation dialog is visible (e.g. "You haven't submitted your answer — do you want
   to leave?") → click "No" to dismiss it and stay on the current question
2. If any other dialog/popup is visible → click its dismiss button (OK, Close, Got it)
3. If a button containing "check" is visible (e.g. "Final check", "Check Answer", "Check My Answer",
   "Check answer") → click it to submit the answer
4. If a "Next", "Continue", or "Next Question" button is visible → click it
5. If the sidebar shows question items → click the NEXT unanswered question item
6. If a new question is already loaded → return action="done"

CRITICAL: target must be copied EXACTLY from the visible buttons list — including any number
prefixes and score suffixes (e.g. "5Checkpoint 1 Key Term Quiz 6Question0/1 pt"). Do NOT
shorten, paraphrase, or reconstruct the label. Paste it verbatim.

Do NOT click final quiz submission buttons (Submit Quiz, Finish Quiz, Turn in).
"""
