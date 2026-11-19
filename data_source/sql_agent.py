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
from services.llm_service import GEMINI_MODEL_NAME


MODEL_NAME = GEMINI_MODEL_NAME

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
4. Double-quote identifiers that start with digits or require quoting.
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
"""


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

    completion = client.models.generate_content(
        model=MODEL_NAME,
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
        ),
    )
    content = completion.text or ""
    try:
        return SQLResult.model_validate_json(content)
    except ValidationError as error:
        return SQLResult(
            status="insufficient_schema",
            reason="The SQL Agent returned an invalid structured response.",
            required_schema=[str(error)],
        )
