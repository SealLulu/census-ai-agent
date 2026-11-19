"""
Summary / 文件概要:
Regression-test the SQL allowlist built from actual Schema Retrieval output.
使用真实 Schema Retrieval 输出回归测试 SQL 表和字段白名单。

Tests / 包含测试:
- Accept the physical B01 table returned by the Catalog. / 接受 Catalog 返回的 B01 物理表。
- Reject an invented table derived from indicator B01001. / 拒绝由指标编号拼出的虚构表。
"""

import unittest

from data_source.census_data_service import CENSUS_DATABASE
from data_source.schema_retriever import retrieve_schema
from data_source.sql_validator import validate_sql


class SQLValidatorPhysicalTableTest(unittest.TestCase):
    """Keep indicator IDs separate from physical tables. / 区分指标编号与物理表。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_context = retrieve_schema(
            "What is the total population?",
            years=[2020],
        )

    def test_physical_catalog_table_is_allowed(self) -> None:
        result = validate_sql(
            (
                f'SELECT "B01001e1" FROM "{CENSUS_DATABASE}".'
                '"PUBLIC"."2020_CBG_B01" LIMIT 10'
            ),
            self.schema_context,
            CENSUS_DATABASE,
        )
        self.assertTrue(result.is_valid, result.errors)

    def test_unquoted_year_table_is_safely_normalized(self) -> None:
        result = validate_sql(
            (
                f"SELECT b.B01001e1 FROM {CENSUS_DATABASE}."
                "PUBLIC.2020_CBG_B01 AS b LIMIT 10"
            ),
            self.schema_context,
            CENSUS_DATABASE,
        )
        self.assertTrue(result.is_valid, result.errors)
        self.assertIn('"2020_CBG_B01"', result.normalized_sql)
        self.assertIn('"B01001e1"', result.normalized_sql)

    def test_indicator_id_cannot_be_used_as_table_suffix(self) -> None:
        result = validate_sql(
            (
                f'SELECT "B01001e1" FROM "{CENSUS_DATABASE}".'
                '"PUBLIC"."2020_CBG_B01001" LIMIT 10'
            ),
            self.schema_context,
            CENSUS_DATABASE,
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(
            any("Table is not present" in error for error in result.errors),
            result.errors,
        )


if __name__ == "__main__":
    unittest.main()
