"""
Summary / 文件概要:
Validate generated Snowflake SQL with a parsed AST before database execution.
在数据库执行前，通过解析后的 AST 校验生成的 Snowflake SQL。

Functions / 包含函数:
- validate_sql: Validate read-only scope, tables, columns, and joins. / 校验只读范围、表、字段与关联。
"""

from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.errors import ParseError


MAX_SQL_RESULT_ROWS = 1000


class SQLValidationResult(BaseModel):
    """SQL validation outcome. / SQL 校验结果。"""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    normalized_sql: str = ""
    errors: list[str] = Field(default_factory=list)


_FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Use,
    exp.Set,
    exp.Into,
    exp.Copy,
    exp.Execute,
)


def _schema_permissions(
    schema_context: dict[str, Any],
) -> tuple[set[str], dict[str, set[str]]]:
    """Build table/column allowlists from retrieval output. / 从检索结果构造白名单。"""
    allowed_tables: set[str] = set()
    columns_by_table: dict[str, set[str]] = {}

    for column in schema_context.get("columns", []):
        table_name = str(column.get("table_name", ""))
        column_name = str(column.get("column_name", ""))
        if not table_name or not column_name:
            continue
        allowed_tables.add(table_name)
        columns_by_table.setdefault(table_name, set()).update(
            {column_name, "CENSUS_BLOCK_GROUP"}
        )

    for table_name, columns in schema_context.get(
        "relationship_tables", {}
    ).items():
        if not table_name:
            continue
        allowed_tables.add(str(table_name))
        columns_by_table.setdefault(str(table_name), set()).update(
            str(column.get("column_name", ""))
            for column in columns
            if column.get("column_name")
        )

    return allowed_tables, columns_by_table


def _relationship_column_names(value: Any) -> set[str]:
    """Parse Catalog relationship key names. / 解析 Catalog 关系键字段名。"""
    return {
        item.strip().strip('"')
        for item in str(value).split(",")
        if item.strip()
    }


def _is_verified_join(
    join: exp.Join,
    alias_to_table: dict[str, str],
    schema_context: dict[str, Any],
) -> bool:
    """Check a JOIN against verified Catalog relationships. / 按已验证 Catalog 关系校验 JOIN。"""
    condition = join.args.get("on")
    if condition is None:
        return False
    joined_table = join.this.name if isinstance(join.this, exp.Table) else ""
    condition_columns = list(condition.find_all(exp.Column))
    condition_column_names = {column.name for column in condition_columns}
    condition_tables = {
        alias_to_table.get(column.table, column.table)
        for column in condition_columns
        if column.table
    }
    if joined_table:
        condition_tables.add(joined_table)

    for relationship in schema_context.get("relationships", []):
        if str(relationship.get("verification_status", "")).lower() != "verified":
            continue
        source_table = str(relationship.get("source_table", ""))
        target_table = str(relationship.get("target_table", ""))
        relationship_tables = {source_table, target_table}
        if not relationship_tables.issubset(condition_tables):
            continue
        required_columns = _relationship_column_names(
            relationship.get("source_columns", "")
        ) | _relationship_column_names(relationship.get("target_columns", ""))
        if required_columns and required_columns.issubset(condition_column_names):
            return True
    return False


def validate_sql(
    sql: str,
    schema_context: dict[str, Any],
    database: str,
    schema_name: str = "PUBLIC",
) -> SQLValidationResult:
    """Validate generated Snowflake SQL. / 校验生成的 Snowflake SQL。"""
    if not isinstance(sql, str) or not sql.strip():
        return SQLValidationResult(is_valid=False, errors=["SQL is empty."])

    try:
        statements = sqlglot.parse(sql, read="snowflake")
    except ParseError as error:
        return SQLValidationResult(
            is_valid=False,
            errors=[f"Snowflake SQL parse error: {error}"],
        )
    if len(statements) != 1:
        return SQLValidationResult(
            is_valid=False,
            errors=["Exactly one SQL statement is allowed."],
        )

    tree = statements[0]
    errors: list[str] = []
    if tree.find(exp.Select) is None:
        errors.append("Only a SELECT query is allowed.")
    for forbidden_type in _FORBIDDEN_EXPRESSIONS:
        if tree.find(forbidden_type) is not None:
            errors.append(
                f"Forbidden SQL operation: {forbidden_type.__name__}."
            )
    if tree.find(exp.Star) is not None:
        errors.append("Wildcard columns are not allowed; select explicit fields.")

    limit_node = tree.args.get("limit")
    if limit_node is None:
        errors.append(f"Every query must include LIMIT {MAX_SQL_RESULT_ROWS} or less.")
    else:
        try:
            limit_value = int(limit_node.expression.name)
        except (AttributeError, TypeError, ValueError):
            errors.append("LIMIT must be a positive integer literal.")
        else:
            if not 1 <= limit_value <= MAX_SQL_RESULT_ROWS:
                errors.append(
                    f"LIMIT must be between 1 and {MAX_SQL_RESULT_ROWS}."
                )

    allowed_tables, columns_by_table = _schema_permissions(schema_context)
    if not allowed_tables:
        errors.append("Schema context contains no allowed tables.")

    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    alias_to_table: dict[str, str] = {}
    referenced_tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        table_name = table.name
        if table_name in cte_names:
            continue
        referenced_tables.add(table_name)
        alias_to_table[table.alias_or_name] = table_name
        alias_to_table[table_name] = table_name
        if table_name not in allowed_tables:
            errors.append(f"Table is not present in retrieved schema: {table_name}.")
        if table.catalog.upper() != database.upper():
            errors.append(f"Table must use database {database}: {table.sql()}.")
        if table.db.upper() != schema_name.upper():
            errors.append(f"Table must use schema {schema_name}: {table.sql()}.")

    selected_aliases = {
        expression.alias
        for expression in tree.expressions
        if getattr(expression, "alias", "")
    }
    all_allowed_columns = set().union(*columns_by_table.values()) if columns_by_table else set()
    for column in tree.find_all(exp.Column):
        column_name = column.name
        if column_name in selected_aliases:
            continue
        qualifier = column.table
        if qualifier:
            table_name = alias_to_table.get(qualifier)
            if table_name and column_name not in columns_by_table.get(table_name, set()):
                errors.append(
                    f"Column {column_name} is not allowed for table {table_name}."
                )
        elif column_name not in all_allowed_columns:
            errors.append(f"Column is not present in retrieved schema: {column_name}.")

    # Candidate relationships are discovery metadata only. Execution requires a
    # Catalog relationship explicitly promoted to verification_status=verified.
    # 候选关系仅用于发现；只有明确标记 verified 的 Catalog 关系才允许执行 JOIN。
    for join in tree.find_all(exp.Join):
        if not _is_verified_join(join, alias_to_table, schema_context):
            errors.append("JOIN is not backed by a verified Catalog relationship.")

    if not referenced_tables:
        errors.append("The query does not reference a retrieved Census table.")

    # Preserve first occurrence order while removing duplicate diagnostics.
    errors = list(dict.fromkeys(errors))
    return SQLValidationResult(
        is_valid=not errors,
        normalized_sql=tree.sql(dialect="snowflake") if not errors else "",
        errors=errors,
    )
