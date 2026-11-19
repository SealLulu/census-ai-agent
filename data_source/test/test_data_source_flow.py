"""
Summary / 文件概要:
Run the complete Census data-source pipeline for one natural-language question:
Schema Retrieval, GPT Text-to-SQL, SQL validation, and Snowflake execution.
针对一个自然语言问题运行完整 Census 数据源流程：Schema 检索、GPT Text-to-SQL、
SQL 校验及 Snowflake 执行。

Functions / 包含函数:
- parse_args: Read the test question and retrieval options. / 读取测试问题与检索参数。
- print_schema_summary: Show compact Schema matches. / 显示精简的 Schema 命中结果。
- main: Execute and print every data-source stage. / 执行并打印数据源各阶段。
"""

import argparse
import json
import re
from typing import Any

from cold_start import load_gemini_api_key, load_snowflake_config
from data_source.census_data_service import CENSUS_DATABASE, query_census_data
from data_source.schema_retriever import retrieve_schema
from data_source.sql_agent import generate_sql
from data_source.sql_validator import validate_sql
from services.llm_service import create_gemini_client


DEFAULT_QUESTION = (
    "List the 2020 median household income and Census block group "
    "for 10 block groups."
)


def parse_args() -> argparse.Namespace:
    """Read test inputs. / 读取测试输入。"""
    parser = argparse.ArgumentParser(
        description="Run Schema Retrieval -> Text-to-SQL -> validation -> Snowflake."
    )
    parser.add_argument(
        "--question",
        default=DEFAULT_QUESTION,
        help="Natural-language Census question.",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help=(
            "Catalog years used by Schema Retrieval, for example "
            "--years 2019 2020. Defaults to years in the question, or 2020."
        ),
    )
    parser.add_argument(
        "--schema-limit",
        type=int,
        default=20,
        help="Maximum Schema fields returned per requested year.",
    )
    return parser.parse_args()


def print_schema_summary(schema_context: dict[str, Any]) -> None:
    """Print compact retrieved fields without dumping the Catalog. / 精简输出检索字段。"""
    print("\n[2/5] Schema Retrieval")
    print("Years:", schema_context.get("years", []))
    print("Search terms:", schema_context.get("search_terms", []))
    print("Tables:")
    for table in schema_context.get("tables", []):
        print(
            "  - "
            f"{table.get('table_name')} | role={table.get('role')} | "
            f"purpose={table.get('purpose') or table.get('table_title') or 'N/A'}"
        )
    print("Matched columns:")
    for column in schema_context.get("columns", []):
        print(
            "  - "
            f"{column.get('table_name')}.{column.get('column_name')} | "
            f"{column.get('kind')} | {column.get('field_path')}"
        )
    print("Candidate relationships:")
    for relationship in schema_context.get("relationships", []):
        print(
            "  - "
            f"{relationship.get('source_table')} -> "
            f"{relationship.get('target_table')} | "
            f"{relationship.get('relation_kind')} | "
            f"{relationship.get('verification_status')}"
        )
    print("Relationship policy:", schema_context.get("relationship_policy", {}))
    print("Warnings:")
    for warning in schema_context.get("warnings", []):
        print("  -", warning)


def main() -> None:
    """Run and print the complete data-source flow. / 运行并打印完整数据源流程。"""
    args = parse_args()
    question = args.question.strip()
    if not question:
        raise ValueError("--question must not be empty.")

    print("[1/5] Natural-language question")
    print(question)

    question_years = re.findall(r"\b(?:19|20)\d{2}\b", question)
    years = args.years or list(
        dict.fromkeys(int(year) for year in question_years)
    ) or [2020]

    schema_context = retrieve_schema(
        question,
        years=years,
        limit=args.schema_limit,
    )
    print_schema_summary(schema_context)

    api_key = load_gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    snowflake = load_snowflake_config()

    generated = generate_sql(
        client=create_gemini_client(api_key),
        question=question,
        schema_context=schema_context,
        schema_name=snowflake.schema,
    )
    print("\n[3/5] GPT Text-to-SQL")
    print("Status:", generated.status)
    if generated.status != "success":
        print("Reason:", generated.reason)
        print("Required schema:", generated.required_schema)
        return
    print(generated.sql)

    validation = validate_sql(
        sql=generated.sql,
        schema_context=schema_context,
        database=CENSUS_DATABASE,
        schema_name=snowflake.schema,
    )
    print("\n[4/5] SQL Validator")
    print("Valid:", validation.is_valid)
    if not validation.is_valid:
        print("Errors:")
        for error in validation.errors:
            print("  -", error)
        return
    print("Normalized SQL:")
    print(validation.normalized_sql)

    # query_census_data validates the SQL again immediately before execution.
    # query_census_data 会在执行前再次校验 SQL，形成纵深防御。
    serialized_result = query_census_data(
        sql=generated.sql,
        config=snowflake,
        schema_context=schema_context,
    )
    print("\n[5/5] Snowflake result")
    print(
        json.dumps(
            json.loads(serialized_result),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
