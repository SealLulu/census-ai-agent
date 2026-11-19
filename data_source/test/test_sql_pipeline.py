"""Manual end-to-end SQL generation and execution test. / SQL 生成与执行手动测试。"""

from openai import OpenAI

from cold_start import load_api_key, load_snowflake_config
from data_source.census_data_service import CENSUS_DATABASE, query_census_data
from data_source.schema_retriever import retrieve_schema
from data_source.sql_agent import generate_sql
from data_source.sql_validator import validate_sql


def main() -> None:
    """Generate, validate, and execute one limited query. / 生成、校验并执行一条限量查询。"""
    question = (
        "List the 2020 median household income and Census block group "
        "for 10 block groups."
    )
    schema_context = retrieve_schema(question, year=2020, limit=20)
    api_key = load_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    snowflake = load_snowflake_config()
    generated = generate_sql(
        client=OpenAI(api_key=api_key),
        question=question,
        schema_context=schema_context,
        schema_name=snowflake.schema,
    )
    print("Generation status:", generated.status)
    if generated.status != "success":
        print("Reason:", generated.reason)
        print("Required schema:", generated.required_schema)
        return

    validation = validate_sql(
        generated.sql,
        schema_context,
        CENSUS_DATABASE,
        snowflake.schema,
    )
    print("Generated SQL:", generated.sql)
    print("Validation:", validation.model_dump())
    if not validation.is_valid:
        return

    # query_census_data validates again immediately before execution.
    # query_census_data 会在真正执行前再次校验，形成纵深防御。
    result = query_census_data(
        generated.sql,
        snowflake,
        schema_context,
    )
    print("Snowflake result:", result)


if __name__ == "__main__":
    main()
