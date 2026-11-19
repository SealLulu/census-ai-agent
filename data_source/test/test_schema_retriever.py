"""
Summary / 文件概要:
Regression-test verified geographic filters and important metric supplementation
against the bundled Census Schema Catalog.
基于项目内 Census Schema Catalog 回归测试已验证地理筛选和重要指标字段补齐。

Tests / 包含测试:
- State and King County FIPS contexts. / 州及 King County FIPS 上下文。
- Multi-location filter grouping. / 多地点筛选分组。
- Population, income, and tenure fallback fields. / 人口、收入和住房指标兜底字段。
- Cross-year field supplementation and de-duplication. / 跨年份字段补齐与去重。
"""

import unittest

from data_source.schema_retriever import retrieve_schema


class SchemaRetrieverTest(unittest.TestCase):
    """Verify compact deterministic retrieval additions. / 验证确定性检索补充。"""

    def test_verified_state_fips_filters(self) -> None:
        cases = {
            "California": "06",
            "Texas": "48",
            "Washington": "53",
            "New York": "36",
        }
        for state, state_fips in cases.items():
            with self.subTest(state=state):
                result = retrieve_schema(
                    f"population in {state}",
                    years=[2020],
                )
                context = result["geographic_context"]
                self.assertEqual(context["table"], "2020_CBG_GEOMETRY_WKT")
                self.assertEqual(
                    context["filters"],
                    [{"column": "STATE_FIPS", "value": state_fips}],
                )
                self.assertFalse(context["requires_fips_join"])
                self.assertTrue(context["catalog_columns_verified"])
                self.assertFalse(
                    any(
                        "FIPS_CODES" in relationship["target_table"]
                        for relationship in result["relationships"]
                    )
                )

    def test_king_county_uses_direct_geometry_filters(self) -> None:
        result = retrieve_schema(
            "owner-occupied versus renter-occupied in King County, Washington",
            years=[2020],
        )
        context = result["geographic_context"]
        self.assertEqual(
            context["filters"],
            [
                {"column": "STATE_FIPS", "value": "53"},
                {"column": "COUNTY_FIPS", "value": "033"},
            ],
        )
        self.assertFalse(context["requires_fips_join"])

    def test_multiple_states_use_or_filter_groups(self) -> None:
        result = retrieve_schema(
            "Compare population in California and Texas",
            years=[2020],
        )
        context = result["geographic_context"]
        self.assertEqual(context["filters"], [])
        self.assertEqual(context["filter_group_operator"], "OR")
        self.assertEqual(
            [group["filters"][0]["value"] for group in context["filter_groups"]],
            ["06", "48"],
        )

    def test_important_metrics_are_supplemented_and_deduplicated(self) -> None:
        cases = {
            "population": {("2020_CBG_B01", "B01001e1")},
            "median household income": {("2020_CBG_B19", "B19013e1")},
            "owner-occupied versus renter-occupied": {
                ("2020_CBG_B25", "B25003e2"),
                ("2020_CBG_B25", "B25003e3"),
            },
        }
        for question, required in cases.items():
            with self.subTest(question=question):
                result = retrieve_schema(question, years=[2020])
                identities = [
                    (column["table_name"], column["column_name"])
                    for column in result["columns"]
                ]
                self.assertTrue(required.issubset(identities))
                for identity in required:
                    self.assertEqual(identities.count(identity), 1)
                    column = next(
                        item
                        for item in result["columns"]
                        if (item["table_name"], item["column_name"]) == identity
                    )
                    self.assertTrue(column["field_path"])

    def test_cross_year_tenure_fields(self) -> None:
        result = retrieve_schema(
            "Compare 2019 and 2020 owner-occupied versus renter-occupied housing",
            years=[2019, 2020],
        )
        identities = {
            (column["table_name"], column["column_name"])
            for column in result["columns"]
        }
        expected = {
            (f"{year}_CBG_B25", column)
            for year in (2019, 2020)
            for column in ("B25003e2", "B25003e3")
        }
        self.assertTrue(expected.issubset(identities))

    def test_required_geographic_scenarios_use_verified_relationships(self) -> None:
        cases = (
            ("California population", [2020], "2020_CBG_B01"),
            ("Washington population in 2019", [2019], "2019_CBG_B01"),
            (
                "California owner-occupied versus renter-occupied",
                [2020],
                "2020_CBG_B25",
            ),
            (
                "King County, Washington median household income",
                [2020],
                "2020_CBG_B19",
            ),
        )
        for question, years, source_table in cases:
            with self.subTest(question=question):
                result = retrieve_schema(question, years=years)
                matching = [
                    relationship
                    for relationship in result["relationships"]
                    if relationship["source_table"] == source_table
                    and relationship["target_table"].endswith(
                        "_CBG_GEOMETRY_WKT"
                    )
                ]
                self.assertTrue(matching)
                self.assertTrue(
                    all(
                        item["verification_status"] == "verified"
                        for item in matching
                    )
                )

    def test_seattle_city_request_fails_closed(self) -> None:
        result = retrieve_schema(
            "What is the median household income in Seattle, Washington?",
            years=[2020],
        )
        self.assertIsNone(result["geographic_context"])
        self.assertEqual(result["unsupported_geographies"], ["Seattle"])


if __name__ == "__main__":
    unittest.main()
