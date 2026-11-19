"""
Summary / 文件概要:
Configure and render the Streamlit application, own login/session UI state, call
page.load_history after login, call page.chat for new input, and refresh history
every minute with chat_id-based de-duplication.
配置并渲染 Streamlit 应用，管理登录与页面状态；登录后调用 page.load_history，
聊天时调用 page.chat，并每分钟按 chat_id 去重刷新历史。

Functions / 包含函数:
- initialize_session_state: Initialize Streamlit state. / 初始化 Streamlit 状态。
- render_page_header: Render the title and subtitle. / 渲染标题与副标题。
- render_login: Render and validate login. / 渲染并校验登录。
- render_history_fragment: Refresh and render history every minute. / 每分钟刷新历史。
- render_chat_page: Render the authenticated chat page. / 渲染已登录聊天页面。
- main: Start and route the application. / 启动并路由应用。
"""

import logging

import streamlit as st

from cold_start import AppConfig, initialize_application
from logger import setup_logger
from page import chat, load_history
from services.history_service import (
    get_pending_list,
    latest_history_activity,
    merge_refreshed_records,
    records_to_display_messages,
)
from services.llm_service import create_gemini_client
from services.session_service import (
    acquire_user_session,
    generate_session_id,
    release_user_session,
)


PAGE_TITLE = "Census AI | US Population Insights"
PAGE_HEADING = "Census AI Assistant"
PAGE_SUBTITLE = (
    "Explore US Census data through natural language with multi-agent intelligence."
)
LOGGER = logging.getLogger(__name__)


def initialize_session_state() -> None:
    """Initialize persistent UI state. / 初始化跨重跑保留的页面状态。"""
    if "username" not in st.session_state:
        st.session_state.username = None
    if "chat_records" not in st.session_state:
        st.session_state.chat_records = []
    if "active_chat_ids" not in st.session_state:
        st.session_state.active_chat_ids = set()
    if "browser_session_id" not in st.session_state:
        st.session_state.browser_session_id = generate_session_id()
    if "pending_list" not in st.session_state:
        # Streamlit session state belongs to one browser session, so each user
        # receives an isolated in-memory clarification chain.
        # Streamlit 会话状态属于单个浏览器会话，因此每个用户拥有独立的待澄清链。
        st.session_state.pending_list = []
    if "chat_processing" not in st.session_state:
        # This per-browser lock prevents a user from submitting another message
        # while the current Agent workflow is still running.
        # 该浏览器会话锁用于阻止用户在当前 Agent 流程运行时再次提交问题。
        st.session_state.chat_processing = False
    if "queued_user_input" not in st.session_state:
        st.session_state.queued_user_input = None


def render_page_header() -> None:
    """Render the shared heading. / 渲染共用的主标题与副标题。"""
    st.title(PAGE_HEADING)
    st.caption(PAGE_SUBTITLE)


def render_login(config: AppConfig) -> bool:
    """Render and validate login. / 渲染并校验登录页面。"""
    # Keep the complete login view in one placeholder. After successful login,
    # it can be removed immediately and the chat can render in the same run.
    # 将完整登录页面放入同一个占位容器；登录成功后立即清除，并在同一次运行中渲染聊天页。
    login_area = st.empty()
    with login_area.container():
        render_page_header()
        st.subheader("🔐 Login")
        st.write("Enter your username to continue to the chat.")

        with st.form("login_form"):
            username = st.text_input(
                "Username", placeholder="Enter your username"
            )
            submitted = st.form_submit_button(
                "Continue", use_container_width=True
            )

    if not submitted:
        return False

    username = username.strip()
    if not username:
        st.warning("Please enter a username.")
        return False
    if username not in config.allowed_users:
        st.error("This username is not allowed to access the chat.")
        return False

    try:
        records = load_history(username, config.chat_history_load_limit)
    except (OSError, ValueError):
        LOGGER.exception("Unable to load chat history for user=%s", username)
        st.error("Unable to load chat history. Please try again.")
        return False

    if not acquire_user_session(
        username,
        st.session_state.browser_session_id,
        last_history_activity=latest_history_activity(records),
    ):
        st.error(
            "This user is already logged in from another browser session. "
            "Please log out there first or try again after 10 minutes of "
            "inactivity."
        )
        return False

    st.session_state.username = username
    st.session_state.chat_records = records
    st.session_state.active_chat_ids = set()
    st.session_state.pending_list = get_pending_list(records)
    st.session_state.chat_processing = False
    st.session_state.queued_user_input = None
    login_area.empty()
    return True


@st.fragment(run_every="1m")
def render_history_fragment(username: str, load_limit: int) -> None:
    """Refresh and render history. / 定时刷新并渲染聊天历史。"""
    try:
        refreshed_records = load_history(username, load_limit)
        st.session_state.chat_records = merge_refreshed_records(
            refreshed_records=refreshed_records,
            current_records=st.session_state.chat_records,
            active_chat_ids=st.session_state.active_chat_ids,
        )
        st.session_state.pending_list = get_pending_list(
            st.session_state.chat_records
        )
    except (OSError, ValueError):
        # A refresh failure does not discard the already loaded session history.
        # 刷新失败时保留当前会话中已经加载的历史记录。
        LOGGER.exception("Unable to refresh chat history for user=%s", username)
        st.warning(
            "Unable to refresh chat history. Existing messages are still "
            "available."
        )

    # The current input is already rendered as a live chat bubble below this
    # fragment. Exclude its in-memory pending record during timed refreshes so
    # the same user question is not displayed twice.
    # 当前输入已作为实时聊天气泡显示；定时刷新时排除对应的内存 pending 记录，避免重复显示。
    visible_records = [
        record
        for record in st.session_state.chat_records
        if record["chat_id"] not in st.session_state.active_chat_ids
    ]
    for message in records_to_display_messages(visible_records):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


def render_chat_page(config: AppConfig) -> None:
    """Render the authenticated chat page. / 渲染已登录用户的聊天页面。"""
    if st.sidebar.button("Log out", use_container_width=True):
        release_user_session(
            st.session_state.username,
            st.session_state.browser_session_id,
        )
        st.session_state.username = None
        st.session_state.chat_records = []
        st.session_state.active_chat_ids = set()
        st.session_state.pending_list = []
        st.session_state.chat_processing = False
        st.session_state.queued_user_input = None
        st.rerun()

    render_page_header()
    render_history_fragment(
        st.session_state.username,
        config.chat_history_load_limit,
    )

    # The first rerun stores one submitted question and redraws this widget as
    # disabled before any slow Agent or Snowflake call begins.
    # 第一次重跑先保存已提交问题，并在耗时的 Agent 或 Snowflake 调用前将输入框置灰。
    submitted_input = st.chat_input(
        (
            "Processing the current question..."
            if st.session_state.chat_processing
            else "Ask a question"
        ),
        disabled=st.session_state.chat_processing,
        submit_mode="disable",
    )
    if submitted_input:
        # Server-side guard in addition to the disabled widget. It prevents a
        # stale or duplicated browser event from replacing the active request.
        # 除置灰输入框外再做服务端检查，防止过期或重复事件覆盖当前请求。
        if st.session_state.chat_processing:
            st.warning(
                "Please wait for the current question to finish before "
                "submitting another one."
            )
            return
        st.session_state.queued_user_input = submitted_input
        st.session_state.chat_processing = True
        st.rerun()

    if not st.session_state.chat_processing:
        return

    user_input = st.session_state.queued_user_input
    if not isinstance(user_input, str) or not user_input.strip():
        # Recover from incomplete session state instead of leaving the input
        # permanently disabled.
        # 会话状态不完整时自动释放锁，避免输入框永久置灰。
        st.session_state.chat_processing = False
        st.session_state.queued_user_input = None
        st.rerun()

    with st.chat_message("user"):
        st.markdown(user_input)

    try:
        chat(
            # Intent and SQL use complete responses; only Answer streams. A bounded
            # client timeout prevents failed Agent calls from hanging the page.
            # Intent 和 SQL 使用完整响应，仅 Answer 流式返回；超时限制避免页面长期挂起。
            client=create_gemini_client(config.gemini_api_key),
            username=st.session_state.username,
            user_input=user_input,
            history_records=st.session_state.chat_records,
            snowflake_config=config.snowflake,
        )
    finally:
        # Always release the user-level processing lock, including handled model
        # or Snowflake failures, so the input becomes available again.
        # 无论正常完成还是模型、Snowflake 异常，都释放用户级处理锁。
        st.session_state.chat_processing = False
        st.session_state.queued_user_input = None

    # Full rerun moves the completed chat into history and redraws an enabled
    # input widget. / 完整重跑将聊天移入历史并重新启用输入框。
    st.rerun()


def main() -> None:
    """Start the validated Streamlit app. / 启动完成配置校验的 Streamlit 应用。"""
    setup_logger()
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="📊",
        layout="wide",
    )
    try:
        config = initialize_application()
    except (OSError, ValueError):
        LOGGER.exception("Application startup failed.")
        st.error(
            "The application could not start because its configuration is "
            "invalid."
        )
        return

    initialize_session_state()
    if st.session_state.username and not acquire_user_session(
        st.session_state.username,
        st.session_state.browser_session_id,
    ):
        # Another browser session owns this username; revoke this page.
        # 如果用户名已被另一个浏览器会话持有，则撤销当前页面。
        st.session_state.username = None
        st.session_state.chat_records = []
        st.session_state.active_chat_ids = set()
        st.session_state.pending_list = []
        st.session_state.chat_processing = False
        st.session_state.queued_user_input = None
        st.error(
            "This user is already logged in from another browser session."
        )

    if (
        st.session_state.username
        and st.session_state.username not in config.allowed_users
    ):
        release_user_session(
            st.session_state.username,
            st.session_state.browser_session_id,
        )
        st.session_state.username = None
        st.session_state.chat_records = []
        st.session_state.active_chat_ids = set()
        st.session_state.pending_list = []
        st.session_state.chat_processing = False
        st.session_state.queued_user_input = None

    if not st.session_state.username:
        if not render_login(config):
            return
    render_chat_page(config)


if __name__ == "__main__":
    main()
