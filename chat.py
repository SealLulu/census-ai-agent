"""
Summary / 文件概要:
Implement the main chat workflow using recent history and pending clarification
state, then optionally query Census data and generate an answer.
使用最近历史与待澄清状态实现聊天主流程，然后按需查询 Census 数据并生成答案。

Functions / 包含函数:
- run_chat_flow: Execute the main chat workflow. / 执行聊天主流程。
"""

from dataclasses import dataclass
import logging
import re
from typing import Any, Callable

from google.genai import Client

from agents import answer_agent, intent_agent
from cold_start import SnowflakeConfig
from data_source import census_data_service, schema_retriever, sql_agent


MAX_INTENT_HISTORY_MESSAGES = 10
CHAT_STATUS_VALID = 0
CHAT_STATUS_INVALID = -1
ProgressCallback = Callable[[int, str], None]
LOGGER = logging.getLogger(__name__)
DATA_QUERY_ERROR_TEXT = (
    "The Census data service encountered an issue. "
    "Please try rephrasing your question or ask a different Census question."
)
DATA_SERVICE_UNAVAILABLE_TEXT = DATA_QUERY_ERROR_TEXT


@dataclass(frozen=True)
class ChatFlowResult:
    """Result passed from workflow to page. / 从聊天主流程返回给页面的结果。"""

    status: int
    intent_status: str
    sql: str
    sql_response: str
    resolution: str = ""
    rewritten_question: str = ""
    answer_stream: Any | None = None
    direct_response: str = ""
    pending_question: str | None = None
    pending_clarification: str | None = None


def run_chat_flow(
    client: Client,
    user_input: str,
    history_messages: list[dict[str, str]],
    pending_list: list[dict[str, str]],
    snowflake_config: SnowflakeConfig,
    progress_callback: ProgressCallback | None = None,
) -> ChatFlowResult:
    """Run intent, optional data query, and answer generation. / 执行意图、查询与生成。"""
    recent_history = history_messages[-MAX_INTENT_HISTORY_MESSAGES:]

    # Acknowledge the received text before making the first Agent call.
    # 在第一次调用 Agent 前先确认已收到当前文本。
    if progress_callback:
        progress_callback(5, "Request received")

    # Step 1: classify and rewrite the question; Intent never generates SQL.
    # 第一步：识别意图并补全问题；Intent 不再生成 SQL。
    intent_result = intent_agent.detect_conversation_intent(
        client=client,
        history_messages=recent_history,
        pending_list=pending_list,
        user_input=user_input,
    )
    if not intent_result.is_valid:
        return ChatFlowResult(
            status=CHAT_STATUS_INVALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question="",
        )

    # Save or update pending state when the Intent Agent asks for clarification.
    # Intent Agent 主动追问时保存或更新待澄清状态。
    if (
        intent_result.resolution
        == intent_agent.RESOLUTION_NEEDS_CLARIFICATION
    ):
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=intent_result.response_text,
            pending_question=intent_result.standalone_question,
            pending_clarification=intent_result.response_text,
        )

    # Continue progress only when Intent allows the data pipeline.
    # 仅当 Intent 允许继续时，才推进到后续数据流程进度。
    if progress_callback:
        progress_callback(25, "Intent complete")

    # Step 2: retrieve only the Schema required by the standalone question.
    # 第二步：只检索独立问题所需的 Schema。
    year_matches = re.findall(
        r"\b(?:19|20)\d{2}\b", intent_result.standalone_question
    )
    # Preserve all explicitly requested years for comparisons. When no year is
    # present, use the latest supported Catalog year as the current default.
    # 跨年比较保留所有显式年份；未指定年份时默认使用当前最新的 2020 Catalog。
    schema_years = list(
        dict.fromkeys(int(year) for year in year_matches)
    ) or [2020]
    try:
        schema_context = schema_retriever.retrieve_schema(
            intent_result.standalone_question,
            years=schema_years,
        )
    except (FileNotFoundError, ValueError):
        LOGGER.exception("Unable to retrieve the required Census schema.")
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=(
                "The required Census schema is currently unavailable. "
                "Please try again later."
            ),
        )

    unsupported_geographies = schema_context.get("unsupported_geographies", [])
    if unsupported_geographies:
        LOGGER.info(
            "Unsupported Census geography requested: %s",
            unsupported_geographies,
        )
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=(
                "City-level Census queries are not currently supported because "
                "a verified city-to-Census-block-group mapping is unavailable. "
                "Please ask about a supported state or county."
            ),
        )

    # Step 3: generate schema-grounded SQL. It is still not executed here.
    # 第三步：基于检索结果生成 SQL；此时仍未执行。
    generated_sql = sql_agent.generate_sql(
        client=client,
        question=intent_result.standalone_question,
        schema_context=schema_context,
        schema_name=snowflake_config.schema,
    )
    if generated_sql.status == "insufficient_schema":
        LOGGER.warning(
            "SQL generation reported insufficient schema: reason=%s required=%s",
            generated_sql.reason,
            generated_sql.required_schema,
        )
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=DATA_SERVICE_UNAVAILABLE_TEXT,
        )

    # Step 4: validation occurs inside the connector before Snowflake execution.
    # 第四步：Connector 在执行 Snowflake SQL 前完成校验。
    try:
        data_input = census_data_service.query_census_data(
            generated_sql.sql,
            snowflake_config,
            schema_context,
        )
    except census_data_service.CensusDataQueryError:
        # Keep connector details in server logs while returning a concise,
        # user-friendly message without exposing SQL or credentials.
        # 服务端日志保留完整异常；页面仅返回友好提示，不暴露 SQL 或认证信息。
        LOGGER.exception("Census data query failed.")
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql=generated_sql.sql,
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=DATA_QUERY_ERROR_TEXT,
        )
    if progress_callback:
        progress_callback(60, "Census data ready")

    # Step 5: FOLLOW_UP receives recent history; NEW receives no history.
    # 第五步：FOLLOW_UP 带入最近历史；NEW 不带入历史。
    answer_history = (
        recent_history
        if intent_result.status == intent_agent.STATUS_FOLLOW_UP
        else []
    )
    stream = answer_agent.generate_answer(
        client=client,
        history_messages=answer_history,
        user_input=intent_result.standalone_question,
        data_input=data_input,
    )
    return ChatFlowResult(
        status=CHAT_STATUS_VALID,
        intent_status=intent_result.status,
        sql=generated_sql.sql,
        sql_response=data_input or "",
        resolution=intent_result.resolution,
        rewritten_question=intent_result.standalone_question,
        answer_stream=stream,
    )
