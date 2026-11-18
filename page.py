"""
Summary / 文件概要:
Render login and chat pages, read and append per-user JSONL history records,
invoke the chat pipeline, and provide the page-level data-source integration point.
渲染登录与聊天页面，读取并追加每个用户的 JSONL 历史记录，调用聊天主流程，
并提供页面层的数据源集成入口。

JSONL history format / JSONL 历史记录格式（每行一条完整问答记录）:
{
    "text": "",             # User input / 用户输入
    "time_stamp": "",       # ISO 8601 time / ISO 8601 时间
    "valid_status": 0,       # 0: valid / 有效; -1: invalid / 无效
    "response": "",         # Empty when invalid / 无效时为空
    "response_status": 0,    # 0: normal / 正常; non-zero: pending / 待处理
    "sql": "",              # Executed SQL / 执行的 SQL
    "sql_resp": ""          # SQL result / SQL 返回结果
}

Functions / 包含函数:
- load_chat_history: Load the latest JSONL records. / 读取最后若干条 JSONL 记录。
- history_records_to_messages: Convert records for model context. / 转换为模型消息。
- append_chat_record: Append one complete Q&A record. / 追加一条完整问答记录。
- load_data_input: Obtain data input for the user input. / 根据用户输入获取数据输入。
- initialize_session_state: Initialize Streamlit state. / 初始化 Streamlit 会话状态。
- render_login: Render and validate login. / 渲染并校验登录页面。
- render_chat: Render chat and persist new records. / 渲染聊天并保存新记录。
- render_app: Route between login and chat. / 在登录与聊天页面之间路由。
"""

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI

from chat import run_chat_flow
from cold_start import AppConfig, get_chat_history_path


PAGE_TITLE = "Census AI | US Population Insights"
PAGE_HEADING = "Census AI Assistant"
PAGE_SUBTITLE = (
    "Explore US Census data through natural language with multi-agent intelligence."
)

VALID_STATUS_VALID = 0
VALID_STATUS_INVALID = -1
RESPONSE_STATUS_NORMAL = 0
RESPONSE_STATUS_PENDING = 1
INVALID_RESPONSE_TEXT = "我无法回复你的问题"

HISTORY_STRING_FIELDS = (
    "text",
    "time_stamp",
    "response",
    "sql",
    "sql_resp",
)
HISTORY_STATUS_FIELDS = ("valid_status", "response_status")


def _is_valid_history_record(record: object) -> bool:
    """Validate one complete JSONL record. / 校验一条完整的 JSONL 历史记录。"""
    if not isinstance(record, dict):
        return False
    if not all(isinstance(record.get(field), str) for field in HISTORY_STRING_FIELDS):
        return False
    if not all(
        isinstance(record.get(field), int)
        and not isinstance(record.get(field), bool)
        for field in HISTORY_STATUS_FIELDS
    ):
        return False
    if record["valid_status"] not in (
        VALID_STATUS_VALID,
        VALID_STATUS_INVALID,
    ):
        return False

    # Invalid questions must not store a generated response.
    # 无效问题不得保存模型生成的回答，页面会使用固定提示语。
    if record["valid_status"] != VALID_STATUS_VALID and record["response"]:
        return False
    return True


def load_chat_history(username: str, load_limit: int) -> list[dict[str, Any]]:
    """Load the latest complete records. / 读取指定用户最后若干条完整问答记录。"""
    history_path = get_chat_history_path(username)
    with history_path.open("r", encoding="utf-8") as history_file:
        # Keep only the requested tail, so a large history file is not fully loaded.
        # 只保留指定数量的末尾行，避免把大型历史文件全部载入内存。
        latest_lines = deque(
            (line.strip() for line in history_file if line.strip()),
            maxlen=load_limit,
        )

    records: list[dict[str, Any]] = []
    for line in latest_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSONL chat history for user '{username}'."
            ) from error

        if not _is_valid_history_record(record):
            raise ValueError(
                f"Invalid chat history record for user '{username}'."
            )
        records.append(record)
    return records


def history_records_to_messages(
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Convert stored records into model messages. / 将历史记录转换为模型上下文消息。"""
    messages: list[dict[str, str]] = []
    for record in records:
        messages.append({"role": "user", "content": record["text"]})

        if record["valid_status"] != VALID_STATUS_VALID:
            # Invalid records always use the fixed on-screen/model-context response.
            # 无效记录始终使用固定回复，不读取其 response 字段。
            messages.append(
                {"role": "assistant", "content": INVALID_RESPONSE_TEXT}
            )
        elif (
            record["response_status"] == RESPONSE_STATUS_NORMAL
            and record["response"]
        ):
            messages.append(
                {"role": "assistant", "content": record["response"]}
            )
        # Pending records currently have no assistant message.
        # pending 记录目前不生成助手消息，后续可在这里扩展处理策略。
    return messages


def append_chat_record(username: str, record: dict[str, Any]) -> None:
    """Append one complete Q&A record. / 向用户 JSONL 追加一条完整问答记录。"""
    if not _is_valid_history_record(record):
        raise ValueError("Cannot save an invalid chat history record.")

    history_path = get_chat_history_path(username)
    with history_path.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_history_record(
    user_input: str,
    valid_status: int,
    response: str = "",
    response_status: int = RESPONSE_STATUS_NORMAL,
    sql: str = "",
    sql_resp: str = "",
) -> dict[str, Any]:
    """Build a record using the canonical schema. / 按统一格式构造历史记录。"""
    return {
        "text": user_input,
        "time_stamp": datetime.now(timezone.utc).isoformat(),
        "valid_status": valid_status,
        "response": response,
        "response_status": response_status,
        "sql": sql,
        "sql_resp": sql_resp,
    }


def load_data_input(user_input: str) -> str | None:
    """Load data input for generation. / 为答案生成加载数据输入。"""
    # This is the page-level integration point for a future Census data source.
    # 这是页面层预留的 Census 数据源入口，后续可在这里执行并记录 SQL。
    _ = user_input
    return None


def initialize_session_state() -> None:
    """Initialize persistent session values. / 初始化跨重跑保留的会话数据。"""
    if "username" not in st.session_state:
        st.session_state.username = None
    if "chat_records" not in st.session_state:
        st.session_state.chat_records = []


def render_page_header() -> None:
    """Show the shared page heading. / 显示页面共用的主标题和副标题。"""
    st.title(PAGE_HEADING)
    st.caption(PAGE_SUBTITLE)


def render_login(config: AppConfig) -> None:
    """Show and validate the login form. / 显示并校验登录表单。"""
    render_page_header()
    st.subheader("🔐 Login")
    st.write("Enter your username to continue to the chat.")

    with st.form("login_form"):
        username = st.text_input("Username", placeholder="Enter your username")
        submitted = st.form_submit_button("Continue", use_container_width=True)

    if not submitted:
        return

    username = username.strip()
    if not username:
        st.warning("Please enter a username.")
        return
    if username not in config.allowed_users:
        st.error("This username is not allowed to access the chat.")
        return

    try:
        records = load_chat_history(username, config.chat_history_load_limit)
    except (OSError, ValueError) as error:
        st.error(f"Unable to load chat history: {error}")
        return

    st.session_state.username = username
    st.session_state.chat_records = records
    st.rerun()


def render_chat_history() -> None:
    """Display records as user/assistant bubbles. / 将历史记录显示为用户与助手气泡。"""
    for message in history_records_to_messages(st.session_state.chat_records):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


def handle_assistant_response(
    client: OpenAI, username: str, user_input: str
) -> None:
    """Run chat flow and save one complete record. / 执行聊天流程并保存一条完整记录。"""
    try:
        # Stored records are converted only when building the model context.
        # 仅在构造模型上下文时，才把 JSONL 问答记录转换为 role/content 消息。
        history_messages = history_records_to_messages(
            st.session_state.chat_records
        )
        is_valid, stream = run_chat_flow(
            client=client,
            user_input=user_input,
            history_messages=history_messages,
            data_input_loader=load_data_input,
        )

        if not is_valid:
            record = _build_history_record(
                user_input=user_input,
                valid_status=VALID_STATUS_INVALID,
                response="",
            )
            with st.chat_message("assistant"):
                st.markdown(INVALID_RESPONSE_TEXT)
        else:
            with st.chat_message("assistant"):
                response = st.write_stream(stream)
            record = _build_history_record(
                user_input=user_input,
                valid_status=VALID_STATUS_VALID,
                response=str(response),
            )

        # One append writes the question and its final result atomically as one line.
        # 一次追加同时写入问题与最终结果，保证每行都是完整问答记录。
        append_chat_record(username, record)
        st.session_state.chat_records.append(record)
    except Exception as error:
        st.error(f"An error occurred while processing the chat: {error}")


def render_chat(client: OpenAI) -> None:
    """Show chat for a signed-in user. / 为已登录用户显示聊天界面。"""
    if st.sidebar.button("Log out", use_container_width=True):
        # Logout clears browser state but intentionally keeps local history.
        # 退出登录只清除浏览器会话，本地聊天记录继续保留。
        st.session_state.username = None
        st.session_state.chat_records = []
        st.rerun()

    render_page_header()
    render_chat_history()

    user_input = st.chat_input("Ask a question")
    if not user_input:
        return

    with st.chat_message("user"):
        st.markdown(user_input)
    handle_assistant_response(
        client, st.session_state.username, user_input
    )


def render_app(config: AppConfig) -> None:
    """Route between login and chat. / 根据登录状态进入登录或聊天页面。"""
    initialize_session_state()

    # Revoke a current session if the user was removed from the allowlist.
    # 如果当前用户已从允许列表删除，则撤销现有会话。
    if (
        st.session_state.username
        and st.session_state.username not in config.allowed_users
    ):
        st.session_state.username = None
        st.session_state.chat_records = []

    if not st.session_state.username:
        render_login(config)
        return

    render_chat(OpenAI(api_key=config.openai_api_key))
