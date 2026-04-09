SCOUT_PROMPT = """\
You are analyzing a quiz or assignment page screenshot.

Examine the screenshot and page text carefully and return structured data about what you see.

Return:
- platform: the LMS name ("canvas", "pearson", "blackboard", "moodle", "generic")
- all_on_page: true if ALL quiz questions are visible on this single page at once; false if only one question is shown at a time with a Next button
- has_check_button: true if a "Check Answer", "Check My Answer", or "Submit Answer" button is currently visible
- questions: list of every visible question, each with:
  - id: "q1", "q2", etc. (sequential)
  - text: the full question text
  - options: list of answer option texts exactly as shown (e.g. ["A. fork", "B. exec", "C. malloc"]); empty list for free-text questions
  - kind: "mcq" for multiple choice, "truefalse" for true/false, "text" for free-text input

If there are no questions visible on the page, return an empty questions list.
"""

ANSWER_PROMPT = """\
You are answering academic quiz questions. Use your knowledge to determine the correct answer for each question.

Return a JSON object with a single key "answers" containing an array of objects, one per question:
- question_id: the id (e.g. "q1")
- value: for mcq/truefalse, the EXACT option text as provided (e.g. "A. fork"); for text questions, your answer

Be precise. Think carefully before answering. The value must match one of the provided options exactly for mcq/truefalse questions.
"""

VERIFY_PROMPT = """\
You are verifying that quiz answers have been correctly selected on screen.

Examine the updated screenshot carefully. For each question visible:
- Check if the answer is visually selected (radio button filled in, checkbox checked, text entered in field)
- Note any answer that appears unselected or wrong

Return:
- all_correct: true if every visible answer appears correctly selected
- issues: list of strings describing problems, e.g. ["q2: option B appears unselected", "q3: text field is empty"]
- next_action:
  - "check" if a Check Answer / Check My Answer button is visible and should be clicked
  - "next" if a Next / Next Question / Continue button is visible, OR if a Submit button is visible but some questions appear unanswered or unselected
  - "done" if a Submit / Finish / Done button is visible AND all visible questions appear correctly answered
"""
