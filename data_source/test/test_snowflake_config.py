"""
Summary / 文件概要:
Test normalization of Snowflake account identifiers used by remote deployment.
测试远程部署使用的 Snowflake account 标识标准化逻辑。
"""

import unittest
from unittest.mock import mock_open, patch

import cold_start
from cold_start import _normalize_snowflake_account


class SnowflakeAccountNormalizationTest(unittest.TestCase):
    """Protect supported remote account formats. / 保护受支持的远程账号格式。"""

    def test_selected_account_is_not_replaced_by_local_account(self) -> None:
        self.assertEqual(
            _normalize_snowflake_account("VFC42336"),
            "VFC42336",
        )

    def test_full_account_url_is_reduced_to_identifier(self) -> None:
        self.assertEqual(
            _normalize_snowflake_account(
                "https://BEKNXFB-VFC42336.snowflakecomputing.com/",
            ),
            "beknxfb-vfc42336",
        )


class SnowflakeRemoteOverrideTest(unittest.TestCase):
    """Verify exact top-level Streamlit Secrets override local TOML values."""

    def test_remote_values_override_local_values(self) -> None:
        local_toml = b"""[SNOWFLAKE]
auth_method = "PROGRAMMATIC_ACCESS_TOKEN"
account = "LOCAL-ACCOUNT"
user = "LOCAL_USER"
token = "LOCAL_TOKEN"
warehouse = "LOCAL_WH"
schema = "PUBLIC"
"""
        remote_secrets = {
            "SNOWFLAKE_ACCOUNT": "REMOTE-ACCOUNT",
            "SNOWFLAKE_WAREHOUSE": "REMOTE_WH",
            "SNOWFLAKE_TOKEN": "REMOTE_TOKEN",
        }
        with (
            patch("pathlib.Path.open", mock_open(read_data=local_toml)),
            patch.object(cold_start.st, "secrets", remote_secrets),
        ):
            config = cold_start.load_snowflake_config()

        self.assertEqual(config.account, "REMOTE-ACCOUNT")
        self.assertEqual(config.warehouse, "REMOTE_WH")
        self.assertEqual(config.token, "REMOTE_TOKEN")
        self.assertEqual(config.user, "LOCAL_USER")


if __name__ == "__main__":
    unittest.main()
