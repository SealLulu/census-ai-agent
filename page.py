"""
Summary / 文件概要:
Expose the two page-level operations used by the Streamlit entry point: loading
history and processing one current user input with streamed output.
仅提供 Streamlit 入口需要的两个页面级操作：加载历史，以及处理一次当前用户输入并
输出流式回复。

Functions / 包含函数:
- load_history: Load the latest history records. / 加载最新历史记录。
- chat: Process and persist one user input. / 处理并保存一次用户输入。
"""

import logging
from typing import Any

import streamlit as st
from openai import OpenAI

import chat as chat_workflow
from cold_start import SnowflakeConfig
from services import history_service


LOGGER = logging.getLogger(__name__)
SERVICE_ERROR_TEXT = "当前服务异常，请稍等~"


def load_history(username: str, load_limit: int) -> list[dict[str, Any]]:
    """Load the latest history records. / 加载用户最后若干条历史记录。"""
    return history_service.load_history_records(username, load_limit)


def chat(
    client: OpenAI,
    username: str,
    user_input: str,
    history_records: list[dict[str, Any]],
    snowflake_config: SnowflakeConfig,
) -> dict[str, Any] | None:
    """Process, display, and persist one input. / 处理、显示并保存一次用户输入。"""
    chat_id = history_service.generate_chat_id()
    # Both containers belong to this browser's Streamlit session. They are not
    # shared with another logged-in user, even though the server process is shared.
    # 以下容器属于当前浏览器的 Streamlit 会话，不会与其他登录用户共享。
    active_chat_ids: set[str] = st.session_state.active_chat_ids
    active_chat_ids.add(chat_id)
    pending_list: list[dict[str, str]] = st.session_state.pending_list
    latest_pending = pending_list[-1] if pending_list else {}

    # Keep a pending in-memory record so a timed refresh can identify this chat
    # and preserve it by chat_id until the final record reaches disk.
    # 先保留一条内存 pending 记录，使定时刷新可按 chat_id 保留当前聊天。
    pending_record = history_service.build_history_record(
        chat_id=chat_id,
        user_input=user_input,
        valid_status=history_service.VALID_STATUS_VALID,
        response_status=history_service.RESPONSE_STATUS_PENDING,
        pending_question=latest_pending.get("pending_question", ""),
        pending_clarification=latest_pending.get(
            "pending_clarification", ""
        ),
    )
    history_records.append(pending_record)

    # The progress widget is created lazily only after Intent allows the request
    # to enter the data pipeline. Progress is never persisted to JSONL.
    # 仅当 Intent 允许进入数据流程后才创建进度条；进度状态不写入 JSONL。
    progress_bar: Any | None = None

    def render_progress(percent: int, label: str) -> None:
        """Render transient pipeline progress. / 渲染不落盘的流程进度。"""
        nonlocal progress_bar
        progress_text = f"{percent}% · {label}"
        if progress_bar is None:
            progress_bar = st.progress(percent, text=progress_text)
        else:
            progress_bar.progress(percent, text=progress_text)

    try:
        history_messages = history_service.records_to_model_messages(
            history_records[:-1]
        )
        flow_result = chat_workflow.run_chat_flow(
            client=client,
            user_input=user_input,
            history_messages=history_messages,
            pending_list=pending_list,
            snowflake_config=snowflake_config,
            progress_callback=render_progress,
        )

        # Resolution is the single source of truth for clarification state.
        # resolution 是澄清状态的唯一依据，避免额外布尔状态产生冲突。
        if flow_result.status != history_service.VALID_STATUS_INVALID:
            if (
                flow_result.intent_status
                == history_service.INTENT_STATUS_NEW
            ):
                # NEW always starts a separate chain, including an incomplete
                # new question that immediately requires clarification.
                # NEW 始终开启新链，包括尚不完整、需要立即澄清的新问题。
                pending_list.clear()

            if (
                flow_result.resolution
                == history_service.RESOLUTION_NEEDS_CLARIFICATION
                and flow_result.pending_question
                and flow_result.pending_clarification
            ):
                # Each clarification turn is appended. This preserves repeated
                # pending replies instead of overwriting the previous turn.
                # 每次主动追问都追加到列表，保留用户连续多轮 pending 的过程。
                pending_list.append(
                    {
                        "chat_id": chat_id,
                        "text": user_input,
                        "time_stamp": pending_record["time_stamp"],
                        "pending_question": flow_result.pending_question,
                        "pending_clarification": (
                            flow_result.pending_clarification
                        ),
                    }
                )
                del pending_list[:-history_service.MAX_PENDING_ITEMS]
            else:
                # A completed or replaced question closes the whole chain.
                # 问题完成或被新问题替换后，清空整条待澄清链。
                pending_list.clear()

        # JSONL stores the latest snapshot while memory retains the full chain.
        # JSONL 每行保存最新快照，内存 pending_list 则保留完整待澄清链。
        latest_pending = pending_list[-1] if pending_list else {}
        pending_question = latest_pending.get("pending_question", "")
        pending_clarification = latest_pending.get(
            "pending_clarification", ""
        )

        if flow_result.status == history_service.VALID_STATUS_INVALID:
            final_record = history_service.build_history_record(
                chat_id=chat_id,
                user_input=user_input,
                valid_status=flow_result.status,
                rewritten_question=flow_result.rewritten_question,
                intent_status=flow_result.intent_status,
                resolution=flow_result.resolution,
                pending_question=pending_question,
                pending_clarification=pending_clarification,
            )
            with st.chat_message("assistant"):
                st.markdown(history_service.INVALID_RESPONSE_TEXT)
        elif flow_result.direct_response:
            # Clarification is displayed directly without data or answer generation.
            # 主动追问直接显示，不调用数据服务或答案生成。
            with st.chat_message("assistant"):
                st.markdown(flow_result.direct_response)
            final_record = history_service.build_history_record(
                chat_id=chat_id,
                user_input=user_input,
                valid_status=flow_result.status,
                rewritten_question=flow_result.rewritten_question,
                response=flow_result.direct_response,
                intent_status=flow_result.intent_status,
                resolution=flow_result.resolution,
                pending_question=pending_question,
                pending_clarification=pending_clarification,
            )
        else:
            if flow_result.answer_stream is None:
                raise RuntimeError(
                    "Valid chat flow did not return an answer stream."
                )
            with st.chat_message("assistant"):
                response = st.write_stream(flow_result.answer_stream)
            if progress_bar is not None:
                progress_bar.progress(100, text="100% · Answer complete")
            final_record = history_service.build_history_record(
                chat_id=chat_id,
                user_input=user_input,
                valid_status=flow_result.status,
                rewritten_question=flow_result.rewritten_question,
                response=str(response),
                sql=flow_result.sql,
                sql_resp=flow_result.sql_response,
                intent_status=flow_result.intent_status,
                resolution=flow_result.resolution,
                pending_question=pending_question,
                pending_clarification=pending_clarification,
            )

        history_service.append_history_record(username, final_record)
        history_records[-1] = final_record
        return final_record
    except Exception:
        # A failed request remains pending on disk for later recovery/retry logic.
        # 失败请求以 pending 状态保存，方便后续增加恢复或重试逻辑。
        LOGGER.exception(
            "Chat processing failed for user=%s chat_id=%s",
            username,
            chat_id,
        )
        try:
            history_service.append_history_record(username, pending_record)
        except (OSError, ValueError):
            LOGGER.exception(
                "Unable to save pending chat for user=%s chat_id=%s",
                username,
                chat_id,
            )
        with st.chat_message("assistant"):
            st.markdown(SERVICE_ERROR_TEXT)
        # Returning None prevents the caller from immediately rerunning and
        # clearing the transient service-error message from the current page.
        # 返回 None 可避免调用方立即重跑，导致当前页面上的服务异常提示被清除。
        return None
    finally:
        if progress_bar is not None:
            progress_bar.empty()
        active_chat_ids.discard(chat_id)
