"""
Supporting services for the main application flow. / 主流程支撑服务。

Submodules are imported explicitly by their consumers. Avoid eager imports here
because Streamlit hot reload can remove a watched submodule while this package is
still initializing, which can raise an intermittent module-cache KeyError.
各调用方显式导入所需子模块。这里不做主动导入，避免 Streamlit 热更新在包初始化期间
清理受监控子模块，从而偶发模块缓存 KeyError。
"""
