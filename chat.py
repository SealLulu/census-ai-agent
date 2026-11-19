"""
Summary / 文件概要:
Implement the main chat workflow using recent history and pending clarification
state, then optionally query Census data and generate an answer.
使用最近历史与待澄清状态实现聊天主流程，然后按需查询 Census 数据并生成答案。

Functions / 包含函数:
- run_chat_flow: Execute the main chat workflow. / 执行聊天主流程。
"""

from dataclasses import dataclass
import re
from typing import Any, Callable

from openai import OpenAI

from agents import answer_agent, intent_agent
from cold_start import SnowflakeConfig
from data_source import census_data_service, schema_retriever, sql_agent


MAX_INTENT_HISTORY_MESSAGES = 10
CHAT_STATUS_VALID = 0
CHAT_STATUS_INVALID = -1
ProgressCallback = Callable[[int, str], None]


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
    client: OpenAI,
    user_input: str,
    history_messages: list[dict[str, str]],
    pending_list: list[dict[str, str]],
    snowflake_config: SnowflakeConfig,
    progress_callback: ProgressCallback | None = None,
) -> ChatFlowResult:
    """Run intent, optional data query, and answer generation. / 执行意图、查询与生成。"""
    recent_history = history_messages[-MAX_INTENT_HISTORY_MESSAGES:]

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

    # Progress is UI-only and begins only after Intent allows the data pipeline.
    # 进度只用于页面展示；仅当 Intent 允许继续时才启动数据流程进度。
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
    except (FileNotFoundError, ValueError) as error:
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=f"Unable to retrieve the required Census schema: {error}",
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
        missing = ", ".join(generated_sql.required_schema)
        detail = f" Required schema: {missing}." if missing else ""
        return ChatFlowResult(
            status=CHAT_STATUS_VALID,
            intent_status=intent_result.status,
            sql="",
            sql_response="",
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            direct_response=generated_sql.reason + detail,
        )

    # Step 4: validation occurs inside the connector before Snowflake execution.
    # 第四步：Connector 在执行 Snowflake SQL 前完成校验。
    data_input = census_data_service.query_census_data(
        generated_sql.sql,
        snowflake_config,
        schema_context,
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
