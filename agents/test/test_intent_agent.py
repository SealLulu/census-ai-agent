"""
Summary / 文件概要:
Verify deterministic clarification for materially ambiguous income metrics.
验证对含义不同的收入指标执行确定性澄清。
"""

import json
import unittest
from types import SimpleNamespace

from agents.intent_agent import (
    RESOLUTION_NEEDS_CLARIFICATION,
    RESOLUTION_NONE,
    STATUS_NEW,
    detect_conversation_intent,
)


class _FakeModels:
    def __init__(self, standalone_question: str) -> None:
        self.standalone_question = standalone_question

    def generate_content(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            text=json.dumps(
                {
                    "status": STATUS_NEW,
                    "resolution": RESOLUTION_NONE,
                    "standalone_question": self.standalone_question,
                    "response_text": "",
                }
            )
        )


class _FakeClient:
    def __init__(self, standalone_question: str) -> None:
        self.models = _FakeModels(standalone_question)


class IncomeClarificationTest(unittest.TestCase):
    def test_bare_household_income_requires_clarification(self) -> None:
        question = (
            "Compare population and household income between California "
            "and Texas in 2020."
        )
        result = detect_conversation_intent(
            client=_FakeClient(question),  # type: ignore[arg-type]
            history_messages=[],
            pending_list=[],
            user_input=question,
        )
        self.assertEqual(result.resolution, RESOLUTION_NEEDS_CLARIFICATION)
        self.assertIn("aggregate household income", result.response_text)
        self.assertIn("median household income", result.response_text)

    def test_explicit_median_household_income_does_not_trigger_guard(self) -> None:
        question = (
            "Compare population and median household income between "
            "California and Texas in 2020."
        )
        result = detect_conversation_intent(
            client=_FakeClient(question),  # type: ignore[arg-type]
            history_messages=[],
            pending_list=[],
            user_input=question,
        )
        self.assertEqual(result.resolution, RESOLUTION_NONE)


if __name__ == "__main__":
    unittest.main()
