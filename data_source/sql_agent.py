"""
Summary / 文件概要:
Generate one Snowflake SELECT statement from a natural-language Census question
and retrieved Schema Catalog context. This module never executes SQL.
根据 Census 自然语言问题与检索到的 Schema Catalog 上下文生成一条 Snowflake
SELECT 语句；本模块绝不执行 SQL。

Functions / 包含函数:
- generate_sql: Generate a validated SQLResult. / 生成并校验 SQLResult。
"""

import json
from typing import Any, Literal

from google.genai import Client, types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from data_source.census_data_service import CENSUS_DATABASE
from services.llm_service import generate_content_with_fallback


SQL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "census_sql_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["success", "insufficient_schema"],
                },
                "sql": {"type": "string"},
                "reason": {"type": "string"},
                "required_schema": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["status", "sql", "reason", "required_schema"],
            "additionalProperties": False,
        },
    },
}


class SQLResult(BaseModel):
    """Structured Text-to-SQL result. / 结构化 Text-to-SQL 结果。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "insufficient_schema"]
    sql: str = ""
    reason: str = ""
    required_schema: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "SQLResult":
        """Enforce status-specific fields. / 校验不同状态对应的字段。"""
        self.sql = self.sql.strip()
        self.reason = self.reason.strip()
        self.required_schema = [
            item.strip() for item in self.required_schema if item.strip()
        ]
        if self.status == "success" and not self.sql:
            raise ValueError("success requires a non-empty SQL statement.")
        if self.status == "insufficient_schema":
            if self.sql:
                raise ValueError("insufficient_schema must not return SQL.")
            if not self.reason:
                raise ValueError("insufficient_schema requires a reason.")
        return self


def _system_prompt(database: str, schema_name: str) -> str:
    """Build the Snowflake SQL contract. / 构造 Snowflake SQL 生成约束。"""
    return f"""You are an expert Snowflake SQL generator for US Census data.

Database: {database}
Schema: {schema_name}

Generate SQL using ONLY the provided schema context.

Return exactly one JSON object with these fields:
{{
  "status": "success|insufficient_schema",
  "sql": "",
  "reason": "",
  "required_schema": []
}}

Rules:
1. Return one read-only SELECT query, or report insufficient_schema.
2. Never invent tables, columns, joins, filters, or metric definitions.
3. Use fully qualified table names: database.schema.table.
4. Double-quote every exact table or column identifier that starts with a digit
   or contains lowercase letters. In particular, a year-prefixed table must be
   written as database.schema."2020_CBG_B01"; database.schema.2020_CBG_B01 is
   invalid Snowflake SQL. Aliases may remain unquoted.
5. Use exact table and column names from the provided schema context.
6. JOIN tables only when the provided context verifies the relationship.
   A relationship is verified only when verification_status is exactly "verified".
   Never JOIN a relationship marked "candidate_unverified"; report
   insufficient_schema instead when such a JOIN would be required.
7. Never aggregate a metric unless its aggregation semantics are known.
8. Do not infer a national, state, or county statistic by averaging block-group values.
9. Use Estimate fields for ordinary metric questions. Use MarginOfError fields only
   when the question explicitly requests them.
10. Do not execute SQL and do not include Markdown code fences.
11. Every query must include a positive integer LIMIT no greater than 1000.
12. For success, return SQL and leave reason/required_schema empty.
13. For insufficient_schema, return empty SQL, explain why, and list the missing
    tables, columns, join keys, metric semantics, or filters in required_schema.
14. For a multi-year question, use the matching year-specific tables for every
    requested year. Prefer UNION ALL with an explicit census_year label when the
    same metric is comparable across years. Never JOIN different years unless a
    verified cross-year relationship is provided.
15. The reason and required_schema fields must always be written in English.
16. When geographic_context provides verified geographic filters, apply their
    exact column/value pairs directly to the specified CBG_GEOMETRY_WKT table.
    Do not JOIN METADATA_CBG_FIPS_CODES merely to resolve a state or county
    name when requires_fips_join is false. For a multi-year context list, apply
    the matching geometry-table filters independently to each year. Within one
    context, AND filters inside each filter_groups item and OR the groups when
    filter_group_operator is OR. Never guess a geographic literal.
    geography_filters is legacy compatibility context.
17. Prefer the shortest verified JOIN path. Do not add a FIPS lookup table when
    the required geographic filter can be applied directly to verified geometry
    columns. Use only fields and relationships returned by Schema Retrieval.
18. If unsupported_geographies is non-empty, return insufficient_schema rather
    than silently broadening the request to a state or county.
19. Respect each Census indicator's statistical meaning. Never SUM or AVG
    medians, rates, or percentages to manufacture a state, county, or city value.
    If an appropriate aggregate field or weighting method is not provided,
    return insufficient_schema.
20. A Census indicator ID is not a physical Snowflake table name. For example,
    B01001 does not mean a table named 2020_CBG_B01001. Use only the exact
    metrics[].physical_table and table_name values supplied by Schema Retrieval.
21. Before using a metric, require metrics[].complete to be true and use every
    field in its required_columns according to column_roles. If the group is
    incomplete, return insufficient_schema instead of substituting a nearby field.
22. Composite metrics must follow aggregation_semantics. For an age-65-plus
    percentage, SUM all numerator fields, divide by the denominator with NULLIF,
    and do not omit sex-specific age bands.
23. A multi-metric question must cover every requested metric. Never silently
    answer only one metric; report insufficient_schema when any required metric
    or verified join is unavailable.
24. median_household_income is non-additive. Do not SUM or AVG block-group
    medians to manufacture a state or county median.
25. aggregate_household_income with aggregation_semantics=additive_currency may
    be summed across non-overlapping block groups after applying the verified
    geography filter. Do not confuse it with median_household_income.
26. The word "households" is a Census metric concept, not a county reference.
    For a housing_tenure metric with additive_count_members, SUM the verified
    owner-occupied and renter-occupied member columns independently. A state
    filter such as STATE_FIPS='06' is complete by itself and never requires a
    county-name lookup or METADATA_CBG_FIPS_CODES JOIN.
"""


def _quoted_identifier(identifier: str) -> str:
    """Quote one Catalog-provided Snowflake identifier. / 引用 Catalog 中的标识符。"""
    return '"' + identifier.replace('"', '""') + '"'


def _verified_cbg_relationship(
    schema_context: dict[str, Any],
    metric_table: str,
    geography_table: str,
) -> bool:
    """Require the exact verified CBG relationship. / 要求精确且已验证的 CBG 关系。"""
    for relationship in schema_context.get("relationships", []):
        if relationship.get("verification_status") != "verified":
            continue
        source_table = str(relationship.get("source_table", ""))
        target_table = str(relationship.get("target_table", ""))
        if {source_table, target_table} != {metric_table, geography_table}:
            continue
        source_columns = str(relationship.get("source_columns", ""))
        target_columns = str(relationship.get("target_columns", ""))
        if source_columns == target_columns == "CENSUS_BLOCK_GROUP":
            return True
    return False


def _recover_complete_housing_tenure_query(
    result: SQLResult,
    schema_context: dict[str, Any],
    schema_name: str,
) -> SQLResult | None:
    """Recover a fully specified tenure query rejected by an unstable model.

    Recovery requires one complete housing-tenure metric, one Catalog-verified
    direct geography context, and one verified CBG JOIN. It never guesses a
    table, column, filter, or relationship.
    / 仅在住房产权指标、地域字段和 CBG JOIN 均已验证时恢复模型误拒绝的查询。
    """
    if result.status != "insufficient_schema":
        return None

    metrics = schema_context.get("metrics", [])
    if len(metrics) != 1:
        return None
    metric = metrics[0]
    if (
        metric.get("metric_id") != "housing_tenure"
        or metric.get("aggregation_semantics") != "additive_count_members"
        or metric.get("complete") is not True
    ):
        return None

    metric_table = str(metric.get("physical_table", ""))
    member_columns = metric.get("column_roles", {}).get("members", [])
    if not metric_table or len(member_columns) != 2:
        return None

    catalog_columns = {
        str(column.get("column_name", "")): column
        for column in schema_context.get("columns", [])
        if column.get("table_name") == metric_table
    }
    if not all(column in catalog_columns for column in member_columns):
        return None
    owner_columns = [
        column
        for column in member_columns
        if "owner occupied"
        in str(catalog_columns[column].get("field_path", "")).lower()
    ]
    renter_columns = [
        column
        for column in member_columns
        if "renter occupied"
        in str(catalog_columns[column].get("field_path", "")).lower()
    ]
    if len(owner_columns) != 1 or len(renter_columns) != 1:
        return None

    geography_context = schema_context.get("geographic_context")
    if not isinstance(geography_context, dict):
        return None
    if (
        geography_context.get("requires_fips_join") is not False
        or geography_context.get("catalog_columns_verified") is not True
    ):
        return None
    geography_table = str(geography_context.get("table", ""))
    filter_groups = geography_context.get("filter_groups", [])
    if not geography_table or len(filter_groups) != 1:
        return None
    filters = filter_groups[0].get("filters", [])
    if not filters:
        return None

    geography_columns = {
        str(column.get("column_name", ""))
        for column in schema_context.get("relationship_tables", {}).get(
            geography_table, []
        )
    }
    if "CENSUS_BLOCK_GROUP" not in geography_columns:
        return None
    if any(str(item.get("column", "")) not in geography_columns for item in filters):
        return None
    if not _verified_cbg_relationship(
        schema_context, metric_table, geography_table
    ):
        return None

    where_terms: list[str] = []
    for item in filters:
        column = _quoted_identifier(str(item["column"]))
        value = str(item["value"]).replace("'", "''")
        where_terms.append(f"g.{column} = '{value}'")

    owner_column = _quoted_identifier(owner_columns[0])
    renter_column = _quoted_identifier(renter_columns[0])
    sql = (
        f"SELECT SUM(h.{owner_column}) AS owner_occupied_households, "
        f"SUM(h.{renter_column}) AS renter_occupied_households "
        f"FROM {CENSUS_DATABASE}.{schema_name}.{_quoted_identifier(metric_table)} AS h "
        f"JOIN {CENSUS_DATABASE}.{schema_name}.{_quoted_identifier(geography_table)} AS g "
        f"ON h.{_quoted_identifier('CENSUS_BLOCK_GROUP')} = "
        f"g.{_quoted_identifier('CENSUS_BLOCK_GROUP')} "
        f"WHERE {' AND '.join(where_terms)} LIMIT 1000"
    )
    return SQLResult(status="success", sql=sql)


def generate_sql(
    client: Client,
    question: str,
    schema_context: dict[str, Any],
    schema_name: str = "PUBLIC",
) -> SQLResult:
    """Generate SQL without executing it. / 生成 SQL，但不执行。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string.")
    if not isinstance(schema_context, dict) or not schema_context.get("columns"):
        return SQLResult(
            status="insufficient_schema",
            reason="Schema Retrieval returned no relevant Census fields.",
            required_schema=["relevant Census table and column definitions"],
        )
    if not isinstance(schema_name, str) or not schema_name.strip():
        raise ValueError("schema_name must be a non-empty string.")

    completion = generate_content_with_fallback(
        client,
        contents=(
            f"Question:\n{question.strip()}\n\n"
            "Retrieved schema context:\n"
            + json.dumps(schema_context, ensure_ascii=False, default=str)
        ),
        config=types.GenerateContentConfig(
            system_instruction=_system_prompt(
                CENSUS_DATABASE, schema_name.strip()
            ),
            response_mime_type="application/json",
            response_json_schema=SQL_RESPONSE_FORMAT["json_schema"]["schema"],
            temperature=0,
            max_output_tokens=2048,
            automatic_continuation=False,
        ),
    )
    content = completion.text or ""
    try:
        result = SQLResult.model_validate_json(content)
    except ValidationError as error:
        result = SQLResult(
            status="insufficient_schema",
            reason="The SQL Agent returned an invalid structured response.",
            required_schema=[str(error)],
        )
    recovered = _recover_complete_housing_tenure_query(
        result,
        schema_context,
        schema_name.strip(),
    )
    return recovered or result
