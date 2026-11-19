"""
Summary / 文件概要:
Verify stage-aware chat resumption after a model timeout.
验证模型超时后按照已完成阶段断点续跑。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

import chat
from services import history_service


class ChatResumeTest(unittest.TestCase):
    QUESTION = "What was the population of Texas in 2020?"

    def _resume_state(self, stage: str) -> dict[str, str]:
        return {
            "pipeline_stage": stage,
            "intent_status": history_service.INTENT_STATUS_NEW,
            "resolution": history_service.RESOLUTION_NONE,
            "rewritten_question": self.QUESTION,
            "sql": "SELECT 1",
            "sql_resp": '{"columns": ["VALUE"], "rows": [[1]]}',
        }

    def test_data_checkpoint_reuses_sql_but_reexecutes_snowflake(self) -> None:
        checkpoints: list[dict[str, str]] = []
        schema_context = {"columns": [{"table_name": "T", "column_name": "C"}]}
        with (
            patch.object(
                chat.intent_agent,
                "detect_conversation_intent",
                side_effect=AssertionError("Intent must be skipped"),
            ),
            patch.object(
                chat.schema_retriever,
                "retrieve_schema",
                return_value=schema_context,
            ),
            patch.object(
                chat.sql_agent,
                "generate_sql",
                side_effect=AssertionError("SQL Agent must be skipped"),
            ),
            patch.object(
                chat.census_data_service,
                "query_census_data",
                return_value='{"columns": ["VALUE"], "rows": [[2]]}',
            ) as snowflake_mock,
            patch.object(
                chat.answer_agent,
                "generate_answer",
                return_value=iter(["restored answer"]),
            ) as answer_mock,
        ):
            result = chat.run_chat_flow(
                client=object(),  # type: ignore[arg-type]
                user_input=self.QUESTION,
                history_messages=[],
                pending_list=[],
                snowflake_config=SimpleNamespace(schema="PUBLIC"),
                checkpoint_callback=checkpoints.append,
                resume_state=self._resume_state(
                    history_service.PIPELINE_STAGE_DATA_COMPLETE
                ),
            )

        self.assertEqual(list(result.answer_stream), ["restored answer"])
        answer_mock.assert_called_once()
        snowflake_mock.assert_called_once_with("SELECT 1", ANY, schema_context)
        self.assertEqual(
            checkpoints[-1]["pipeline_stage"],
            history_service.PIPELINE_STAGE_DATA_COMPLETE,
        )
        self.assertEqual(
            result.sql_response,
            '{"columns": ["VALUE"], "rows": [[2]]}',
        )

    def test_sql_checkpoint_reuses_sql_but_reexecutes_snowflake(self) -> None:
        schema_context = {"columns": [{"table_name": "T", "column_name": "C"}]}
        with (
            patch.object(
                chat.intent_agent,
                "detect_conversation_intent",
                side_effect=AssertionError("Intent must be skipped"),
            ),
            patch.object(
                chat.schema_retriever,
                "retrieve_schema",
                return_value=schema_context,
            ),
            patch.object(
                chat.sql_agent,
                "generate_sql",
                side_effect=AssertionError("SQL Agent must be skipped"),
            ),
            patch.object(
                chat.census_data_service,
                "query_census_data",
                return_value='{"rows": [[2]]}',
            ) as snowflake_mock,
            patch.object(
                chat.answer_agent,
                "generate_answer",
                return_value=iter(["answer"]),
            ),
        ):
            result = chat.run_chat_flow(
                client=object(),  # type: ignore[arg-type]
                user_input=self.QUESTION,
                history_messages=[],
                pending_list=[],
                snowflake_config=SimpleNamespace(schema="PUBLIC"),
                resume_state=self._resume_state(
                    history_service.PIPELINE_STAGE_SQL_COMPLETE
                ),
            )

        snowflake_mock.assert_called_once_with(
            "SELECT 1",
            ANY,
            schema_context,
        )
        self.assertEqual(result.sql, "SELECT 1")
        self.assertEqual(result.sql_response, '{"rows": [[2]]}')

    def test_intent_checkpoint_skips_intent_but_regenerates_sql(self) -> None:
        schema_context = {"columns": [{"table_name": "T", "column_name": "C"}]}
        generated = SimpleNamespace(status="success", sql="SELECT 3")
        with (
            patch.object(
                chat.intent_agent,
                "detect_conversation_intent",
                side_effect=AssertionError("Intent must be skipped"),
            ),
            patch.object(
                chat.schema_retriever,
                "retrieve_schema",
                return_value=schema_context,
            ),
            patch.object(
                chat.sql_agent,
                "generate_sql",
                return_value=generated,
            ) as sql_mock,
            patch.object(
                chat.census_data_service,
                "query_census_data",
                return_value='{"rows": [[3]]}',
            ),
            patch.object(
                chat.answer_agent,
                "generate_answer",
                return_value=iter(["answer"]),
            ),
        ):
            result = chat.run_chat_flow(
                client=object(),  # type: ignore[arg-type]
                user_input=self.QUESTION,
                history_messages=[],
                pending_list=[],
                snowflake_config=SimpleNamespace(schema="PUBLIC"),
                resume_state=self._resume_state(
                    history_service.PIPELINE_STAGE_INTENT_COMPLETE
                ),
            )

        sql_mock.assert_called_once()
        self.assertEqual(result.sql, "SELECT 3")


if __name__ == "__main__":
    unittest.main()
