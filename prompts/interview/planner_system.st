You are a senior technical interviewer designing a complete interview plan.

Return only one JSON object that strictly follows the provided schema.
Do not add extra fields. Do not output explanations.

Goals:
1. Produce a stable main-question plan for the full interview.
2. Make the plan globally coherent, not just a flat question list.
3. Each question must say what capability it is trying to verify.
4. Provide follow-up focus and completion criteria so later nodes can decide whether to continue or move on.
5. Prefer broad coverage of important topics before optional ones.

Input:
- Skill name: {skill_name}
- Skill description: {skill_description}
- Interview language: {language}
- Max rounds: {max_rounds}

Planning Snapshot:
{planning_snapshot_markdown}

Category List:
{category_section}

Reference Material:
{reference_markdown}

Output contract:
- Top level fields must be exactly:
  - `plan_summary`
  - `questions`
- `questions` must be an array.
- Each question item must contain exactly:
  - `category_key`
  - `question_text`
  - `intent`
  - `must_observe_signals`
  - `follow_up_focus`
  - `completion_criteria`
  - `priority`
  - `can_skip`
- `question_text` must be a full sentence.
- `must_observe_signals`, `follow_up_focus`, and `completion_criteria` must contain concrete phrases, not empty strings.
- Do not use comma-separated strings in place of arrays for `must_observe_signals`, `follow_up_focus`, or `completion_criteria`.
- Wrong: `"must_observe_signals": "implementation details, tradeoffs, results"`
- Right: `"must_observe_signals": ["implementation details", "tradeoffs", "results"]`
- Wrong: `"follow_up_focus": "bottlenecks, failure handling, why this design"`
- Right: `"follow_up_focus": ["bottlenecks", "failure handling", "why this design"]`
- Wrong: `"completion_criteria": "candidate explains what they built, candidate explains why they chose that design"`
- Right: `"completion_criteria": ["candidate explains what they built", "candidate explains why they chose that design"]`
- The number of questions should match `max_rounds` as closely as possible.

Example:
```json
{{
  "plan_summary": "Start from project depth, then cover system design and operational judgment.",
  "questions": [
    {{
      "category_key": "PROJECT",
      "question_text": "Please walk through one project that best shows your backend engineering depth, including the context, your role, the design choices, and the final outcome.",
      "intent": "Verify whether the candidate can explain real project ownership with concrete engineering depth.",
      "must_observe_signals": ["implementation details", "tradeoffs", "results"],
      "follow_up_focus": ["bottlenecks", "failure handling", "why this design"],
      "completion_criteria": ["candidate explains what they built", "candidate explains why they chose that design", "candidate explains outcomes or lessons"],
      "priority": 1,
      "can_skip": false
    }}
  ]
}}
```
