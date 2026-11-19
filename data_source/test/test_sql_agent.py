"""Manual smoke test for Schema Retrieval and Text-to-SQL. / 手动冒烟测试。"""

from cold_start import load_gemini_api_key
from data_source.schema_retriever import retrieve_schema
from data_source.sql_agent import generate_sql
from services.llm_service import create_gemini_client


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
