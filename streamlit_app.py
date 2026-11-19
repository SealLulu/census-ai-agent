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

import streamlit as st
from openai import OpenAI

from cold_start import AppConfig, initialize_application
from page import chat, load_history
from services.history_service import (
    get_pending_list,
    merge_refreshed_records,
    records_to_display_messages,
)
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

    if not acquire_user_session(
        username, st.session_state.browser_session_id
    ):
        st.error(
            "This user is already logged in from another browser session. "
            "Please log out there first."
        )
        return False

    try:
        records = load_history(username, config.chat_history_load_limit)
    except (OSError, ValueError) as error:
        # Loading failed after ownership was acquired, so release it immediately.
        # 获取登录权后加载失败，需要立即释放该用户名。
        release_user_session(
            username, st.session_state.browser_session_id
        )
        st.error(f"Unable to load chat history: {error}")
        return False

    st.session_state.username = username
    st.session_state.chat_records = records
    st.session_state.active_chat_ids = set()
    st.session_state.pending_list = get_pending_list(records)
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
    except (OSError, ValueError) as error:
        # A refresh failure does not discard the already loaded session history.
        # 刷新失败时保留当前会话中已经加载的历史记录。
        st.warning(f"Unable to refresh chat history: {error}")

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
        st.rerun()

    render_page_header()
    render_history_fragment(
        st.session_state.username,
        config.chat_history_load_limit,
    )

    user_input = st.chat_input("Ask a question")
    if not user_input:
        return

    with st.chat_message("user"):
        st.markdown(user_input)

    result = chat(
        client=OpenAI(api_key=config.openai_api_key),
        username=st.session_state.username,
        user_input=user_input,
        history_records=st.session_state.chat_records,
        snowflake_config=config.snowflake,
    )
    if result is not None:
        # Full rerun moves the just-streamed chat into the refreshed history
        # fragment, preventing duplicate bubbles on the next timed refresh.
        # 完整重跑会把刚输出的聊天移入历史片段，避免下次定时刷新时重复显示。
        st.rerun()


def main() -> None:
    """Start the validated Streamlit app. / 启动完成配置校验的 Streamlit 应用。"""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="📊",
        layout="wide",
    )
    try:
        config = initialize_application()
    except (OSError, ValueError) as error:
        st.error(f"Startup error: {error}")
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

    if not st.session_state.username:
        if not render_login(config):
            return
    render_chat_page(config)


if __name__ == "__main__":
    main()
