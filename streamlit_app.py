"""
Summary / 文件概要:
Provide the minimal Streamlit entry point. Configure the browser page, run the
cold-start checks, and hand the validated configuration to the page layer.
提供最小化的 Streamlit 程序入口：配置浏览器页面、执行冷启动检查，并将已校验配置
交给页面层。

Functions / 包含函数:
- main: Start and route the application. / 启动并路由整个应用。
"""

import streamlit as st

from cold_start import initialize_application
from page import PAGE_TITLE, render_app


def main() -> None:
    """Start the validated Streamlit app. / 启动完成配置校验的 Streamlit 应用。"""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="📊",
        layout="wide",
    )

    # Cold start is the gate: no login or chat page runs until it succeeds.
    # 冷启动是应用门槛：成功前不会进入登录或聊天页面。
    try:
        config = initialize_application()
    except (OSError, ValueError) as error:
        st.error(f"Startup error: {error}")
        return

    render_app(config)


if __name__ == "__main__":
    # Standard Python entry point. / 标准 Python 程序入口。
    main()
