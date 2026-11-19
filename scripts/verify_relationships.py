"""
Summary / 文件概要:
Create a new Census Schema Catalog and verify every 2019/2020
``cbg_to_geography`` relationship against read-only Snowflake data.
创建新的 Census Schema Catalog，并使用只读 Snowflake 数据批量验证 2019/2020
年度的全部 ``cbg_to_geography`` 关系。

Functions / 包含函数:
- verify_relationships: Validate relationships and write a new Catalog. / 验证关系并生成新 Catalog。
- main: Parse command-line options and print a validation summary. / 解析命令行并输出验证摘要。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import snowflake.connector


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cold_start import SnowflakeConfig, load_snowflake_config
from data_source.census_data_service import CENSUS_DATABASE


SOURCE_CATALOG = ROOT_DIR / "data_source" / "census_schema_catalog.sqlite"
VERIFIED_CATALOG = (
    ROOT_DIR / "data_source" / "census_schema_catalog_verified.sqlite"
)
VALID_YEARS = (2019, 2020)
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$]+$")


@dataclass(frozen=True)
class RelationshipCheck:
    """Store one Snowflake relationship result. / 保存一条 Snowflake 关系检查结果。"""

    relationship_id: int
    source_table: str
    target_table: str
    passed: bool
    statistics: dict[str, int]
    reason: str


def _quote_identifier(identifier: str) -> str:
    """Validate and quote a Snowflake identifier. / 校验并引用 Snowflake 标识符。"""
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(f"Unsafe Snowflake identifier in Catalog: {identifier!r}")
    return f'"{identifier}"'


def _decode_columns(value: str) -> list[str]:
    """Decode a Catalog relationship-column list. / 解析 Catalog 关系字段列表。"""
    text = (value or "").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = [item.strip() for item in text.split(",")]
    if isinstance(decoded, str):
        decoded = [decoded]
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) and item.strip() for item in decoded
    ):
        raise ValueError(f"Invalid relationship columns: {value!r}")
    return [item.strip() for item in decoded]


def _connection_parameters(config: SnowflakeConfig) -> dict[str, Any]:
    """Build read-only validation connection settings. / 构造只读验证连接参数。"""
    parameters: dict[str, Any] = {
        "account": config.account,
        "user": config.user,
        "warehouse": config.warehouse,
        "database": CENSUS_DATABASE,
        "schema": config.schema,
        "login_timeout": 10,
        "network_timeout": 60,
        "client_session_keep_alive": False,
        "session_parameters": {
            "STATEMENT_TIMEOUT_IN_SECONDS": 60,
            "QUERY_TAG": "census-ai-agent-relationship-validation",
        },
    }
    if config.role:
        parameters["role"] = config.role
    connector_major = int(snowflake.connector.__version__.split(".", 1)[0])
    if connector_major >= 5:
        parameters["enable_server_session_keep_alive_auto_detection"] = False
    if config.auth_method == "oauth":
        parameters.update(authenticator="oauth", token=config.token)
    elif config.auth_method == "programmatic_access_token":
        parameters.update(
            authenticator="PROGRAMMATIC_ACCESS_TOKEN",
            token=config.token,
        )
    else:
        parameters["password"] = config.password
    return parameters


def _validation_sql(
    source_table: str,
    target_table: str,
    source_columns: list[str],
    target_columns: list[str],
    schema_name: str,
) -> str:
    """Build one read-only uniqueness and coverage query. / 构造唯一性及覆盖率只读查询。"""
    if not source_columns or len(source_columns) != len(target_columns):
        raise ValueError("Relationship keys must be non-empty and have equal lengths.")

    source_select = ", ".join(
        f"{_quote_identifier(column)} AS k{index}"
        for index, column in enumerate(source_columns)
    )
    target_select = ", ".join(
        f"{_quote_identifier(column)} AS k{index}"
        for index, column in enumerate(target_columns)
    )
    key_names = [f"k{index}" for index in range(len(source_columns))]
    group_by = ", ".join(key_names)
    source_nulls = " OR ".join(f"s.{key} IS NULL" for key in key_names)
    target_nulls = " OR ".join(f"t.{key} IS NULL" for key in key_names)
    join_condition = " AND ".join(
        f"s.{key} = t.{key}" for key in key_names
    )

    database = _quote_identifier(CENSUS_DATABASE)
    schema = _quote_identifier(schema_name)
    source = _quote_identifier(source_table)
    target = _quote_identifier(target_table)
    return f"""
WITH source_keys AS (
    SELECT {source_select}
    FROM {database}.{schema}.{source}
),
target_keys AS (
    SELECT {target_select}
    FROM {database}.{schema}.{target}
)
SELECT
    (SELECT COUNT(*) FROM source_keys) AS source_rows,
    (SELECT COUNT(*) FROM source_keys AS s WHERE {source_nulls}) AS source_null_rows,
    (
        SELECT COUNT(*) FROM (
            SELECT {group_by}
            FROM source_keys
            GROUP BY {group_by}
            HAVING COUNT(*) > 1
        )
    ) AS source_duplicate_keys,
    (SELECT COUNT(*) FROM target_keys) AS target_rows,
    (SELECT COUNT(*) FROM target_keys AS t WHERE {target_nulls}) AS target_null_rows,
    (
        SELECT COUNT(*) FROM (
            SELECT {group_by}
            FROM target_keys
            GROUP BY {group_by}
            HAVING COUNT(*) > 1
        )
    ) AS target_duplicate_keys,
    (
        SELECT COUNT(*)
        FROM source_keys AS s
        WHERE NOT EXISTS (
            SELECT 1 FROM target_keys AS t WHERE {join_condition}
        )
    ) AS unmatched_source_rows
""".strip()


def _check_relationship(
    cursor: Any,
    relationship: sqlite3.Row,
    schema_name: str,
) -> RelationshipCheck:
    """Validate one candidate relationship. / 验证一条候选关系。"""
    source_columns = _decode_columns(relationship["source_columns"])
    target_columns = _decode_columns(relationship["target_columns"])
    sql = _validation_sql(
        source_table=relationship["source_table"],
        target_table=relationship["target_table"],
        source_columns=source_columns,
        target_columns=target_columns,
        schema_name=schema_name,
    )
    cursor.execute(sql, timeout=60)
    names = [description.name.lower() for description in cursor.description]
    statistics = {
        name: int(value)
        for name, value in zip(names, cursor.fetchone(), strict=True)
    }
    failed_checks = [
        name
        for name in (
            "source_null_rows",
            "source_duplicate_keys",
            "target_null_rows",
            "target_duplicate_keys",
            "unmatched_source_rows",
        )
        if statistics[name] != 0
    ]
    passed = not failed_checks
    reason = (
        "Verified: source and target keys are non-null and unique, and every "
        "source key has exactly one target match."
        if passed
        else "Not verified; failed checks: " + ", ".join(failed_checks) + "."
    )
    return RelationshipCheck(
        relationship_id=int(relationship["relationship_id"]),
        source_table=relationship["source_table"],
        target_table=relationship["target_table"],
        passed=passed,
        statistics=statistics,
        reason=reason,
    )


def verify_relationships(
    source_catalog: Path = SOURCE_CATALOG,
    output_catalog: Path = VERIFIED_CATALOG,
) -> list[RelationshipCheck]:
    """Create and validate a new Catalog. / 创建并验证新的 Catalog。"""
    source_catalog = source_catalog.resolve()
    output_catalog = output_catalog.resolve()
    if not source_catalog.is_file():
        raise FileNotFoundError(f"Source Catalog was not found: {source_catalog}")
    if source_catalog == output_catalog:
        raise ValueError("The output Catalog must differ from the source Catalog.")

    output_catalog.parent.mkdir(parents=True, exist_ok=True)
    temporary_catalog = output_catalog.with_suffix(output_catalog.suffix + ".tmp")
    if temporary_catalog.exists():
        temporary_catalog.unlink()
    shutil.copy2(source_catalog, temporary_catalog)

    catalog = sqlite3.connect(temporary_catalog)
    catalog.row_factory = sqlite3.Row
    snowflake_connection = None
    cursor = None
    checks: list[RelationshipCheck] = []
    try:
        relationship_columns = {
            row[1] for row in catalog.execute("PRAGMA table_info(relationships)")
        }
        required_columns = {
            "source_table",
            "target_table",
            "source_columns",
            "target_columns",
            "relation_kind",
            "year",
            "verification_status",
            "notes",
        }
        missing = required_columns - relationship_columns
        if missing:
            raise ValueError(
                "Catalog relationships table is missing: "
                + ", ".join(sorted(missing))
            )

        relationships = catalog.execute(
            """
            SELECT
                rowid AS relationship_id,
                source_table,
                target_table,
                source_columns,
                target_columns,
                verification_status
            FROM relationships
            WHERE relation_kind = 'cbg_to_geography'
              AND year IN (?, ?)
            ORDER BY year, source_table, target_table
            """,
            VALID_YEARS,
        ).fetchall()
        if not relationships:
            raise ValueError("No 2019/2020 cbg_to_geography relationships were found.")

        config = load_snowflake_config()
        snowflake_connection = snowflake.connector.connect(
            **_connection_parameters(config)
        )
        cursor = snowflake_connection.cursor()
        for index, relationship in enumerate(relationships, start=1):
            print(
                f"[{index}/{len(relationships)}] Validating "
                f"{relationship['source_table']} -> {relationship['target_table']}"
            )
            try:
                check = _check_relationship(cursor, relationship, config.schema)
            except Exception as error:
                check = RelationshipCheck(
                    relationship_id=int(relationship["relationship_id"]),
                    source_table=relationship["source_table"],
                    target_table=relationship["target_table"],
                    passed=False,
                    statistics={},
                    reason=f"Validation query failed: {type(error).__name__}: {error}",
                )
            checks.append(check)
            if check.passed:
                notes = check.reason + " Statistics: " + json.dumps(
                    check.statistics,
                    sort_keys=True,
                )
                catalog.execute(
                    """
                    UPDATE relationships
                    SET verification_status = 'verified', notes = ?
                    WHERE rowid = ?
                    """,
                    (notes, check.relationship_id),
                )
            print("  PASS" if check.passed else f"  FAIL: {check.reason}")

        catalog.commit()
        catalog.close()
        catalog = None
        os.replace(temporary_catalog, output_catalog)
        return checks
    except Exception:
        if catalog is not None:
            catalog.rollback()
        if temporary_catalog.exists():
            temporary_catalog.unlink()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if snowflake_connection is not None:
            snowflake_connection.close()
        if catalog is not None:
            catalog.close()


def main() -> None:
    """Run Catalog relationship verification. / 运行 Catalog 关系验证。"""
    parser = argparse.ArgumentParser(
        description=(
            "Create a new Catalog with verified 2019/2020 CBG-to-geography joins."
        )
    )
    parser.add_argument("--source", type=Path, default=SOURCE_CATALOG)
    parser.add_argument("--output", type=Path, default=VERIFIED_CATALOG)
    arguments = parser.parse_args()
    checks = verify_relationships(arguments.source, arguments.output)
    passed = sum(check.passed for check in checks)
    print(
        f"Completed: {passed}/{len(checks)} relationships verified. "
        f"New Catalog: {arguments.output.resolve()}"
    )


if __name__ == "__main__":
    main()
