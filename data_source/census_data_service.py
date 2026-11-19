"""
Summary / 文件概要:
Execute generated read-only SQL against the Snowflake-hosted US Open Census
Data & Neighborhood Insights - Free Dataset and serialize returned rows.
通过 Snowflake 执行生成的只读 SQL，查询 US Open Census Data & Neighborhood
Insights - Free Dataset，并序列化返回结果。

Functions / 包含函数:
- query_census_data: Execute one safe SELECT query. / 执行一条安全的 SELECT 查询。
"""

import json
import logging
from typing import Any

import snowflake.connector
from snowflake.connector.errors import Error as SnowflakeError

from cold_start import SnowflakeConfig
from data_source.sql_validator import validate_sql


CENSUS_DATABASE = "US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET"
DATASET_NAME = "US Open Census Data & Neighborhood Insights - Free Dataset"
QUERY_TIMEOUT_SECONDS = 30
CONNECTION_TIMEOUT_SECONDS = 10
MAX_RESULT_ROWS = 1000
LOGGER = logging.getLogger(__name__)
# Keep successful SQL execution logs visible in local and Streamlit deployments.
# 确保本地及 Streamlit 部署环境都能看到正常执行的 SQL 日志。
LOGGER.setLevel(logging.INFO)


class CensusDataQueryError(RuntimeError):
    """Represent a safe-to-handle Census query failure. / 表示可安全处理的数据查询失败。"""


def _column_name(description: Any) -> str:
    """Read a connector column name. / 读取 Snowflake 返回的列名。"""
    name = getattr(description, "name", None)
    if name is not None:
        return str(name)
    return str(description[0])


def query_census_data(
    sql: str,
    config: SnowflakeConfig,
    schema_context: dict[str, Any],
) -> str:
    """Validate, execute, and serialize one query. / 校验、执行并序列化一条查询。"""
    validation = validate_sql(
        sql=sql,
        schema_context=schema_context,
        database=CENSUS_DATABASE,
        schema_name=config.schema,
    )
    if not validation.is_valid:
        raise CensusDataQueryError(
            "SQL validation failed: " + "; ".join(validation.errors)
        )

    connection_parameters: dict[str, Any] = {
        "account": config.account,
        "user": config.user,
        "warehouse": config.warehouse,
        "database": CENSUS_DATABASE,
        "schema": config.schema,
        "login_timeout": CONNECTION_TIMEOUT_SECONDS,
        "network_timeout": QUERY_TIMEOUT_SECONDS,
        # Explicitly close the server session with this short-lived request.
        # 短请求结束时明确关闭服务端会话，避免连接器推断 keep-alive 行为。
        "client_session_keep_alive": False,
        "session_parameters": {
            "STATEMENT_TIMEOUT_IN_SECONDS": QUERY_TIMEOUT_SECONDS,
            "QUERY_TAG": "census-ai-agent",
        },
    }
    if config.role:
        connection_parameters["role"] = config.role
    # Connector 5.x introduced this option; set it only when available so the
    # same code remains compatible with the pinned 4.8.1 deployment driver.
    # Connector 5.x 新增该参数；仅在对应版本设置，以兼容部署固定的 4.8.1。
    connector_major = int(snowflake.connector.__version__.split(".", 1)[0])
    if connector_major >= 5:
        connection_parameters[
            "enable_server_session_keep_alive_auto_detection"
        ] = False

    # OAuth uses a short-lived access token; password mode remains available locally.
    # OAuth 使用短期访问 token；本地环境仍兼容密码认证模式。
    if config.auth_method == "oauth":
        connection_parameters.update(
            authenticator="oauth",
            token=config.token,
        )
    elif config.auth_method == "programmatic_access_token":
        connection_parameters.update(
            authenticator="PROGRAMMATIC_ACCESS_TOKEN",
            token=config.token,
        )
    else:
        connection_parameters["password"] = config.password

    connection = None
    cursor = None
    try:
        # Log only the validated, normalized SQL. Never log credentials or rows.
        # 仅记录校验并规范化后的 SQL，不记录认证信息或查询结果。
        LOGGER.info(
            "Executing Snowflake SQL:\n%s",
            validation.normalized_sql,
        )
        connection = snowflake.connector.connect(
            **connection_parameters,
        )
        cursor = connection.cursor()
        cursor.execute(
            validation.normalized_sql,
            timeout=QUERY_TIMEOUT_SECONDS,
        )
        fetched_rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        is_truncated = len(fetched_rows) > MAX_RESULT_ROWS
        rows = fetched_rows[:MAX_RESULT_ROWS]
        columns = [
            _column_name(description)
            for description in (cursor.description or [])
        ]
        return json.dumps(
            {
                "dataset": DATASET_NAME,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": is_truncated,
                "max_rows": MAX_RESULT_ROWS,
            },
            ensure_ascii=False,
            default=str,
        )
    except SnowflakeError as error:
        raise CensusDataQueryError(
            "Snowflake could not complete the Census data query."
        ) from error
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except SnowflakeError:
                LOGGER.warning("Unable to close the Snowflake cursor.", exc_info=True)
        if connection is not None:
            try:
                connection.close()
            except SnowflakeError:
                LOGGER.warning(
                    "Unable to close the Snowflake connection.",
                    exc_info=True,
                )
