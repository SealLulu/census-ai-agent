import logging
import sys

def setup_logger():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # 避免第三方 SDK 输出过多调试信息
    logging.getLogger("snowflake.connector").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)