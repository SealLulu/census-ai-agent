"""Manual smoke test for Schema Retrieval and Text-to-SQL. / 手动冒烟测试。"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cold_start import load_gemini_api_key
from data_source.schema_retriever import retrieve_schema
from data_source.sql_agent import generate_sql
from data_source.sql_validator import validate_sql
from services.llm_service import create_gemini_client


class SQLAgentRecoveryTest(unittest.TestCase):
    """Verify narrow recovery from false model refusals."""

    @patch("data_source.sql_agent.generate_content_with_fallback")
    def test_recovers_complete_state_housing_tenure_query(
        self,
        generate_mock,
    ) -> None:
        generate_mock.return_value = SimpleNamespace(
            text=json.dumps(
                {
                    "status": "insufficient_schema",
                    "sql": "",
                    "reason": (
                        "A candidate FIPS relationship is not verified for a county lookup."
                    ),
                    "required_schema": ["verified FIPS relationship"],
                }
            )
        )
        question = (
            "How many households in California were owner-occupied versus "
            "renter-occupied in 2020?"
        )
        schema_context = retrieve_schema(question, years=[2020], limit=20)

        result = generate_sql(
            client=object(),
            question=question,
            schema_context=schema_context,
            schema_name="PUBLIC",
        )

        self.assertEqual(result.status, "success")
        self.assertIn('SUM(h."B25003e2")', result.sql)
        self.assertIn('SUM(h."B25003e3")', result.sql)
        self.assertIn('g."STATE_FIPS" = \'06\'', result.sql)
        self.assertNotIn("FIPS_CODES", result.sql)
        validation = validate_sql(
            result.sql,
            schema_context,
            "US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET",
        )
        self.assertTrue(validation.is_valid, validation.errors)


def main() -> None:
    """Generate SQL for one question without executing it. / 仅生成 SQL，不执行。"""
    question = "What is the median household income in 2020?"
    schema_context = retrieve_schema(question, years=[2020], limit=20)
    api_key = load_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    result = generate_sql(
        client=create_gemini_client(api_key),
        question=question,
        schema_context=schema_context,
        schema_name="PUBLIC",
    )
    print("Status:", result.status)
    print("SQL:", result.sql)
    print("Reason:", result.reason)
    print("Required schema:", result.required_schema)


if __name__ == "__main__":
    main()
