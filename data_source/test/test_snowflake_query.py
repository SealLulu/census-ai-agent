"""
Summary / 文件概要:
Run one fixed, validated Snowflake query without calling GPT.
不调用 GPT，执行一条固定且经过校验的 Snowflake 查询。

Functions / 包含函数:
- main: Load configuration, validate SQL, and print query results. / 加载配置、校验 SQL 并输出结果。
"""

import json

from cold_start import load_snowflake_config
from data_source.census_data_service import CENSUS_DATABASE, query_census_data
from data_source.schema_retriever import retrieve_schema


SAMPLE_QUESTION = "2020 median household income"
def main() -> None:
    """Execute the fixed query through the safe connector. / 通过安全 Connector 执行固定查询。"""
    config = load_snowflake_config()
    schema_context = retrieve_schema(SAMPLE_QUESTION, years=[2020], limit=20)
    sample_sql = f"""
SELECT
    t.CENSUS_BLOCK_GROUP,
    t."B19013e1" AS MEDIAN_HOUSEHOLD_INCOME
FROM {CENSUS_DATABASE}.{config.schema}."2020_CBG_B19" AS t
WHERE t."B19013e1" IS NOT NULL
LIMIT 10
""".strip()

    # query_census_data performs AST validation before opening Snowflake.
    # query_census_data 会先完成 AST 校验，通过后才连接 Snowflake。
    serialized_result = query_census_data(
        sql=sample_sql,
        config=config,
        schema_context=schema_context,
    )
    print(json.dumps(json.loads(serialized_result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
