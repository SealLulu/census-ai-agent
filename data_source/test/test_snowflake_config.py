"""
Summary / 文件概要:
Test normalization of Snowflake account identifiers used by remote deployment.
测试远程部署使用的 Snowflake account 标识标准化逻辑。
"""

import unittest

from cold_start import _normalize_snowflake_account


class SnowflakeAccountNormalizationTest(unittest.TestCase):
    """Protect supported remote account formats. / 保护受支持的远程账号格式。"""

    def test_short_account_reuses_local_organization(self) -> None:
        self.assertEqual(
            _normalize_snowflake_account(
                "VFC42336",
                "BEKNXFB-VFC42336",
            ),
            "BEKNXFB-VFC42336",
        )

    def test_full_account_url_is_reduced_to_identifier(self) -> None:
        self.assertEqual(
            _normalize_snowflake_account(
                "https://BEKNXFB-VFC42336.snowflakecomputing.com/",
            ),
            "beknxfb-vfc42336",
        )


if __name__ == "__main__":
    unittest.main()
