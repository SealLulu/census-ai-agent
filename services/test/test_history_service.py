"""
Summary / 文件概要:
Test exact completed-answer lookup in per-user JSONL history.
测试用户隔离 JSONL 历史中的完全一致已完成回答检索。
"""

import unittest

from services import history_service


class ExactHistoryLookupTest(unittest.TestCase):
    """Verify strict and safe history reuse. / 验证严格且安全的历史复用。"""

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
        result = history_service.find_exact_completed_record(
            [first, latest],
            "  Population in California?  ",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["chat_id"], "latest")

    def test_reuses_new_completed_record_without_persisted_sql_data(self) -> None:
        completed = history_service.build_history_record(
            chat_id="completed-without-data",
            user_input="Population in California?",
            valid_status=history_service.VALID_STATUS_VALID,
            response="Completed answer",
            sql="SELECT 1",
            sql_resp="",
            intent_status=history_service.INTENT_STATUS_NEW,
            resolution=history_service.RESOLUTION_NONE,
            pipeline_stage=history_service.PIPELINE_STAGE_ANSWER_COMPLETE,
        )
        result = history_service.find_exact_completed_record(
            [completed],
            completed["text"],
        )
        self.assertIs(result, completed)

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
        result = history_service.find_exact_completed_record(
            [failed, pending],
            "Population in Texas?",
        )
        self.assertIsNone(result)

    def test_does_not_search_outside_loaded_limit(self) -> None:
        older_match = history_service.build_history_record(
            chat_id="outside-limit",
            user_input="Population in Washington state in 2019?",
            valid_status=history_service.VALID_STATUS_VALID,
            response="Older answer",
            sql="SELECT 1",
            sql_resp="",
            intent_status=history_service.INTENT_STATUS_NEW,
            resolution=history_service.RESOLUTION_NONE,
        )
        loaded_tail = [
            history_service.build_history_record(
                chat_id="inside-limit",
                user_input="A different question",
                valid_status=history_service.VALID_STATUS_VALID,
                response="Different answer",
                sql="SELECT 2",
                sql_resp='{"rows": [[2]]}',
                intent_status=history_service.INTENT_STATUS_NEW,
                resolution=history_service.RESOLUTION_NONE,
            )
        ]
        self.assertIsNone(
            history_service.find_exact_completed_record(
                loaded_tail,
                older_match["text"],
            )
        )

    def test_model_timeout_is_not_reused_as_completed_answer(self) -> None:
        failed = history_service.build_history_record(
            chat_id="failed-answer",
            user_input="Population in Texas?",
            valid_status=history_service.VALID_STATUS_VALID,
            response="The AI model is temporarily busy.",
            sql="SELECT 1",
            sql_resp="",
            intent_status=history_service.INTENT_STATUS_NEW,
            resolution=history_service.RESOLUTION_NONE,
            pipeline_stage=history_service.PIPELINE_STAGE_DATA_COMPLETE,
            failure_type=history_service.FAILURE_TYPE_MODEL_UNAVAILABLE,
        )
        self.assertIsNone(
            history_service.find_exact_completed_record(
                [failed],
                failed["text"],
            )
        )


class ResumableHistoryLookupTest(unittest.TestCase):
    """Verify narrow, immediately-previous timeout resumption. / 验证严格断点续跑。"""

    def _timeout_record(
        self,
        *,
        stage: str = history_service.PIPELINE_STAGE_DATA_COMPLETE,
        failure_type: str = history_service.FAILURE_TYPE_MODEL_UNAVAILABLE,
    ) -> dict[str, object]:
        return history_service.build_history_record(
            chat_id="timeout",
            user_input="Population in Texas?",
            valid_status=history_service.VALID_STATUS_VALID,
            rewritten_question="What was the population of Texas in 2020?",
            response="The AI model is temporarily busy.",
            sql="SELECT 1",
            sql_resp="",
            intent_status=history_service.INTENT_STATUS_NEW,
            resolution=history_service.RESOLUTION_NONE,
            pipeline_stage=stage,
            failure_type=failure_type,
        )

    def test_resumes_latest_exact_model_timeout(self) -> None:
        record = self._timeout_record()
        self.assertIs(
            history_service.find_resumable_record(
                [record],
                " Population in Texas? ",
            ),
            record,
        )

    def test_does_not_resume_older_or_different_question(self) -> None:
        record = self._timeout_record()
        newer = history_service.build_history_record(
            chat_id="newer",
            user_input="Different question",
            valid_status=history_service.VALID_STATUS_VALID,
            response="Different answer",
        )
        self.assertIsNone(
            history_service.find_resumable_record(
                [record, newer],
                record["text"],
            )
        )
        self.assertIsNone(
            history_service.find_resumable_record(
                [record],
                "Population in California?",
            )
        )

    def test_does_not_resume_rate_limit_or_service_failure(self) -> None:
        for failure_type in (
            history_service.FAILURE_TYPE_MODEL_RATE_LIMIT,
            history_service.FAILURE_TYPE_SERVICE,
        ):
            with self.subTest(failure_type=failure_type):
                record = self._timeout_record(failure_type=failure_type)
                self.assertIsNone(
                    history_service.find_resumable_record(
                        [record],
                        record["text"],
                    )
                )

    def test_technical_failure_is_hidden_from_model_but_visible_in_ui(self) -> None:
        record = self._timeout_record()
        self.assertEqual(
            history_service.records_to_model_messages([record]),
            [],
        )
        self.assertEqual(
            history_service.records_to_display_messages([record]),
            [
                {"role": "user", "content": record["text"]},
                {"role": "assistant", "content": record["response"]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
