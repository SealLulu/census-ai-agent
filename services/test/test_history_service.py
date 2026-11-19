"""
Summary / 文件概要:
Test exact completed-answer lookup in per-user JSONL history.
测试用户隔离 JSONL 历史中的完全一致已完成回答检索。
"""

import json
from io import StringIO
import unittest
from unittest.mock import MagicMock, patch

from services import history_service


class ExactHistoryLookupTest(unittest.TestCase):
    """Verify strict and safe history reuse. / 验证严格且安全的历史复用。"""

    def _history_stream(self, records: list[dict]) -> StringIO:
        return StringIO(
            "".join(json.dumps(record) + "\n" for record in records)
        )

    def test_returns_latest_exact_successful_answer(self) -> None:
        first = history_service.build_history_record(
            chat_id="first",
            user_input="Population in California?",
            valid_status=history_service.VALID_STATUS_VALID,
            response="First answer",
            sql="SELECT 1",
            sql_resp='{"rows": [[1]]}',
            intent_status=history_service.INTENT_STATUS_NEW,
            resolution=history_service.RESOLUTION_NONE,
        )
        latest = history_service.build_history_record(
            chat_id="latest",
            user_input="Population in California?",
            valid_status=history_service.VALID_STATUS_VALID,
            response="Latest answer",
            sql="SELECT 2",
            sql_resp='{"rows": [[2]]}',
            intent_status=history_service.INTENT_STATUS_NEW,
            resolution=history_service.RESOLUTION_NONE,
        )
        stream = self._history_stream([first, latest])
        history_path = MagicMock()
        history_path.open.return_value = stream
        with patch.object(
            history_service,
            "get_chat_history_path",
            return_value=history_path,
        ):
            result = history_service.find_exact_completed_record(
                "alice",
                "  Population in California?  ",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["chat_id"], "latest")

    def test_does_not_reuse_failed_or_pending_records(self) -> None:
        failed = history_service.build_history_record(
            chat_id="failed",
            user_input="Population in Texas?",
            valid_status=history_service.VALID_STATUS_VALID,
            response="The service is currently unavailable.",
        )
        pending = history_service.build_history_record(
            chat_id="pending",
            user_input="Population in Texas?",
            valid_status=history_service.VALID_STATUS_VALID,
            response_status=history_service.RESPONSE_STATUS_PENDING,
        )
        stream = self._history_stream([failed, pending])
        history_path = MagicMock()
        history_path.open.return_value = stream
        with patch.object(
            history_service,
            "get_chat_history_path",
            return_value=history_path,
        ):
            result = history_service.find_exact_completed_record(
                "alice",
                "Population in Texas?",
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
