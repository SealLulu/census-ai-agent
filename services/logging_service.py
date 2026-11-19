"""
Summary / 文件概要:
Configure application logging without using the collision-prone top-level
module name ``logger``.
配置应用日志，并避免使用容易与第三方模块冲突的顶层名称 ``logger``。

Functions / 包含函数:
- setup_logger: Configure application and dependency log levels. / 配置日志级别。
"""

import logging
import sys


def setup_logger() -> None:
    """Configure process-wide application logging. / 配置进程级应用日志。"""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Keep third-party SDK diagnostics useful without flooding deployment logs.
    # 保留第三方 SDK 的告警，同时避免部署日志被普通调试信息淹没。
    logging.getLogger("snowflake.connector").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
