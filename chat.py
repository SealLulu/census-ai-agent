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
from services import history_service


MAX_INTENT_HISTORY_MESSAGES = 10
CHAT_STATUS_VALID = 0
CHAT_STATUS_INVALID = -1
ProgressCallback = Callable[[int, str], None]
CheckpointCallback = Callable[[dict[str, str]], None]
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
    checkpoint_callback: CheckpointCallback | None = None,
    resume_state: dict[str, Any] | None = None,
) -> ChatFlowResult:
    """Run intent, optional data query, and answer generation. / 执行意图、查询与生成。"""
    recent_history = history_messages[-MAX_INTENT_HISTORY_MESSAGES:]
    resume_state = resume_state or {}
    resume_stage = str(resume_state.get("pipeline_stage", ""))
    stage_order = {
        history_service.PIPELINE_STAGE_INTENT_COMPLETE: 1,
        history_service.PIPELINE_STAGE_SQL_COMPLETE: 2,
        # Snowflake rows are intentionally never restored from history. A
        # DATA_COMPLETE record resumes from its persisted SQL and re-queries.
        # Snowflake 返回不落盘；DATA_COMPLETE 仅复用 SQL 并重新查询。
        history_service.PIPELINE_STAGE_DATA_COMPLETE: 2,
    }
    resume_rank = stage_order.get(resume_stage, 0)

    def save_checkpoint(**values: str) -> None:
        """Expose the latest completed stage to the page. / 向页面提供最新完成阶段。"""
        if checkpoint_callback:
            checkpoint_callback(values)

    # Acknowledge the received text before making the first Agent call.
    # 在第一次调用 Agent 前先确认已收到当前文本。
    if progress_callback:
        progress_callback(5, "Request received")

    # Step 1: classify/rewrite, or restore the completed Intent checkpoint.
    # 第一步：识别并改写问题，或恢复已经完成的 Intent 检查点。
    if resume_rank >= 1:
        intent_result = intent_agent.IntentResult(
            status=str(resume_state["intent_status"]),
            resolution=str(resume_state.get("resolution", "")),
            standalone_question=str(resume_state["rewritten_question"]),
            response_text="",
        )
        LOGGER.info("Resuming chat after completed Intent stage.")
    else:
        intent_result = intent_agent.detect_conversation_intent(
            client=client,
            history_messages=recent_history,
            pending_list=pending_list,
            user_input=user_input,
        )
    save_checkpoint(
        pipeline_stage=history_service.PIPELINE_STAGE_INTENT_COMPLETE,
        intent_status=intent_result.status,
        resolution=intent_result.resolution,
        rewritten_question=intent_result.standalone_question,
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

    if resume_rank < 3:
        # Step 2: local Schema Retrieval is deterministic and inexpensive.
        # 第二步：本地 Schema Retrieval 是确定性的轻量操作。
        year_matches = re.findall(
            r"\b(?:19|20)\d{2}\b", intent_result.standalone_question
        )
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

        # Step 3: generate SQL or reuse a completed Text-to-SQL checkpoint.
        # 第三步：生成 SQL，或复用已完成的 Text-to-SQL 检查点。
        if resume_rank >= 2:
            generated_sql_text = str(resume_state["sql"])
            LOGGER.info("Resuming chat after completed SQL stage.")
            save_checkpoint(
                pipeline_stage=history_service.PIPELINE_STAGE_SQL_COMPLETE,
                intent_status=intent_result.status,
                resolution=intent_result.resolution,
                rewritten_question=intent_result.standalone_question,
                sql=generated_sql_text,
            )
        else:
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
            generated_sql_text = generated_sql.sql
            save_checkpoint(
                pipeline_stage=history_service.PIPELINE_STAGE_SQL_COMPLETE,
                intent_status=intent_result.status,
                resolution=intent_result.resolution,
                rewritten_question=intent_result.standalone_question,
                sql=generated_sql_text,
            )

        # Step 4: validate and execute. Snowflake rows are never restored from history.
        # 第四步：校验并执行；Snowflake 结果不会从历史记录恢复。
        try:
            data_input = census_data_service.query_census_data(
                generated_sql_text,
                snowflake_config,
                schema_context,
            )
        except census_data_service.CensusDataQueryError:
            LOGGER.exception("Census data query failed.")
            return ChatFlowResult(
                status=CHAT_STATUS_VALID,
                intent_status=intent_result.status,
                sql=generated_sql_text,
                sql_response="",
                resolution=intent_result.resolution,
                rewritten_question=intent_result.standalone_question,
                direct_response=DATA_QUERY_ERROR_TEXT,
            )
        save_checkpoint(
            pipeline_stage=history_service.PIPELINE_STAGE_DATA_COMPLETE,
            intent_status=intent_result.status,
            resolution=intent_result.resolution,
            rewritten_question=intent_result.standalone_question,
            sql=generated_sql_text,
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
        sql=generated_sql_text,
        sql_response=data_input or "",
        resolution=intent_result.resolution,
        rewritten_question=intent_result.standalone_question,
        answer_stream=stream,
    )
