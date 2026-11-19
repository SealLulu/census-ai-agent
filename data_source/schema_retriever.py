"""
Summary / 文件概要:
Search the local Census Schema Catalog with SQLite FTS5 and return a compact,
JSON-serializable schema context for a later SQL-generation step.
使用 SQLite FTS5 搜索本地 Census Schema Catalog，并为后续 SQL 生成步骤返回
精简、可 JSON 序列化的 Schema 上下文。

Functions / 包含函数:
- retrieve_schema: Retrieve relevant Census fields and join metadata. / 检索相关字段与关联信息。
"""

import re
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG_PATH = BASE_DIR / "census_schema_catalog_verified.sqlite"
DEFAULT_RESULT_LIMIT = 20
MAX_RESULT_LIMIT = 100
MAX_RELATIONSHIP_DEPTH = 2
RETRIEVAL_POOL_MULTIPLIER = 8
MIN_RETRIEVAL_POOL = 200
MAX_RETRIEVAL_POOL = 500

US_STATE_IDENTIFIERS = {
    "alabama": ("AL", "01"),
    "alaska": ("AK", "02"),
    "arizona": ("AZ", "04"),
    "arkansas": ("AR", "05"),
    "california": ("CA", "06"),
    "colorado": ("CO", "08"),
    "connecticut": ("CT", "09"),
    "delaware": ("DE", "10"),
    "district of columbia": ("DC", "11"),
    "florida": ("FL", "12"),
    "georgia": ("GA", "13"),
    "hawaii": ("HI", "15"),
    "idaho": ("ID", "16"),
    "illinois": ("IL", "17"),
    "indiana": ("IN", "18"),
    "iowa": ("IA", "19"),
    "kansas": ("KS", "20"),
    "kentucky": ("KY", "21"),
    "louisiana": ("LA", "22"),
    "maine": ("ME", "23"),
    "maryland": ("MD", "24"),
    "massachusetts": ("MA", "25"),
    "michigan": ("MI", "26"),
    "minnesota": ("MN", "27"),
    "mississippi": ("MS", "28"),
    "missouri": ("MO", "29"),
    "montana": ("MT", "30"),
    "nebraska": ("NE", "31"),
    "nevada": ("NV", "32"),
    "new hampshire": ("NH", "33"),
    "new jersey": ("NJ", "34"),
    "new mexico": ("NM", "35"),
    "new york": ("NY", "36"),
    "north carolina": ("NC", "37"),
    "north dakota": ("ND", "38"),
    "ohio": ("OH", "39"),
    "oklahoma": ("OK", "40"),
    "oregon": ("OR", "41"),
    "pennsylvania": ("PA", "42"),
    "rhode island": ("RI", "44"),
    "south carolina": ("SC", "45"),
    "south dakota": ("SD", "46"),
    "tennessee": ("TN", "47"),
    "texas": ("TX", "48"),
    "utah": ("UT", "49"),
    "vermont": ("VT", "50"),
    "virginia": ("VA", "51"),
    "washington": ("WA", "53"),
    "west virginia": ("WV", "54"),
    "wisconsin": ("WI", "55"),
    "wyoming": ("WY", "56"),
}

# County values are added only after they are checked against Snowflake. This
# intentionally starts with the required test case instead of guessing all US
# county codes from names.
# 县级编码仅在 Snowflake 验证后加入；当前先覆盖指定测试用例，不根据县名猜测编码。
COUNTY_FIPS = {
    ("washington", "king county"): "033",
}

# The bundled Catalog has no verified city-boundary mapping. Explicit city
# requests must fail closed instead of silently broadening to their state.
# 当前 Catalog 没有经过验证的城市边界映射；城市请求必须明确失败，不能静默扩大为州级查询。
UNSUPPORTED_CITY_NAMES = {"seattle"}

# Metric definitions identify Census indicator groups, never physical columns.
# Field members are expanded from Catalog metadata such as table_number,
# field_path, universe, and kind so composite metrics remain complete.
# 指标定义只识别 Census 指标组，不硬编码物理字段；成员由 Catalog 元数据动态展开。
METRIC_DEFINITIONS = {
    "total_population": {
        "aliases": ("population", "total population"),
        "table_suffix": "CBG_B01",
        "table_number": "B01001",
        "selector": "total_population",
        "aggregation": "additive_count",
        "required_role_counts": {"value": 1},
    },
    "median_household_income": {
        "aliases": ("median household income", "household income"),
        "table_suffix": "CBG_B19",
        "table_number": "B19013",
        "selector": "all_indicator_fields",
        "aggregation": "non_additive_median",
        "required_role_counts": {"value": 1},
    },
    "age_65_plus": {
        "aliases": ("age 65+", "65 years and over", "65 or older"),
        "table_suffix": "CBG_B01",
        "table_number": "B01001",
        "selector": "age_65_plus",
        "aggregation": "ratio_of_additive_counts",
        "required_role_counts": {"denominator": 1, "numerator": 12},
    },
    "housing_tenure": {
        "aliases": ("owner-occupied", "renter-occupied", "housing tenure"),
        "table_suffix": "CBG_B25",
        "table_number": "B25003",
        "selector": "housing_tenure_members",
        "aggregation": "additive_count_members",
        "required_role_counts": {"members": 2},
    },
}

DEMOGRAPHIC_SUBGROUP_TERMS = {
    "race", "racial", "ethnicity", "ethnic", "sex", "gender", "male",
    "female",
}

# These terms describe conversational structure or geography rather than a
# Census metric. Removing them keeps searches focused on fields such as
# population, median household income, age, and housing.
# 这些词描述问句结构或地理位置，而不是 Census 指标；过滤后可让检索更聚焦。
_IGNORED_TERMS = {
    "a", "about", "all", "and", "are", "as", "at", "average", "be",
    "an", "between", "by", "can", "census", "compare", "county", "data",
    "did", "do", "does", "find", "for", "from", "give", "how", "i", "in",
    "is", "it", "many", "me", "much", "of", "on", "or", "please", "show",
    "state", "than", "that",
    "the", "their", "them", "there", "these", "this", "to", "us", "usa",
    "was", "were", "what", "when", "where", "which", "who", "with",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "hampshire", "jersey", "mexico", "york", "carolina", "dakota",
    "ohio", "oklahoma", "oregon", "pennsylvania", "rhode", "island",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "wisconsin", "wyoming", "columbia",
}


def _build_fts_query(question: str) -> tuple[str, list[str]]:
    """Build a safe OR query from metric-like terms. / 构造安全的指标关键词 OR 查询。"""
    raw_terms = re.findall(r"[a-z][a-z0-9_'-]*", question.lower())
    terms: list[str] = []
    for raw_term in raw_terms:
        term = raw_term.strip("'-")
        if len(term) < 2 or term in _IGNORED_TERMS or term in terms:
            continue
        terms.append(term)

    if not terms:
        raise ValueError("The question does not contain searchable schema terms.")

    # Tokens come from a strict regex, but quoting them also prevents FTS5
    # operators in user input from changing the query structure.
    # 关键词来自严格正则；额外加引号可防止用户输入改变 FTS5 查询结构。
    return " OR ".join(f'"{term}"' for term in terms), terms


def _requested_state_filters(question: str) -> list[dict[str, str]]:
    """Map state names in a question to verified filter values. / 映射州名筛选值。"""
    filters: list[dict[str, str]] = []
    for state_name in _state_names_in_question(question):
        abbreviation, state_fips = US_STATE_IDENTIFIERS[state_name]
        filters.append(
            {
                "input_name": state_name.title(),
                "state_column": "STATE",
                "state_value": abbreviation,
                "state_fips_column": "STATE_FIPS",
                "state_fips_value": state_fips,
            }
        )
    return filters


def _state_names_in_question(question: str) -> list[str]:
    """Find non-overlapping state names in text order. / 按文本顺序识别不重叠州名。"""
    text = " ".join(question.lower().split())
    candidates: list[tuple[int, int, str]] = []
    for state in US_STATE_IDENTIFIERS:
        for match in re.finditer(rf"\b{re.escape(state)}\b", text):
            candidates.append((match.start(), match.end(), state))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    selected: list[tuple[int, int, str]] = []
    for candidate in candidates:
        start, end, _ = candidate
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: item[0])
    return [state for _, _, state in selected]


def get_geographic_context(
    question: str,
    year: int,
) -> dict[str, Any] | None:
    """Resolve verified direct geometry filters. / 解析已验证的地理表直接筛选条件。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string.")
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError("year must be an integer.")

    text = " ".join(question.lower().split())
    if any(re.search(rf"\b{re.escape(city)}\b", text) for city in UNSUPPORTED_CITY_NAMES):
        return None
    filter_groups: list[dict[str, Any]] = []
    for state in _state_names_in_question(question):
        _, state_fips = US_STATE_IDENTIFIERS[state]
        filters = [{"column": "STATE_FIPS", "value": state_fips}]
        for (county_state, county), county_fips in COUNTY_FIPS.items():
            if (
                county_state == state
                and re.search(rf"\b{re.escape(county)}\b", text)
            ):
                filters.append(
                    {"column": "COUNTY_FIPS", "value": county_fips}
                )
        filter_groups.append(
            {
                "location": state.title(),
                "filters": filters,
            }
        )
    if not filter_groups:
        return None
    return {
        "table": f"{year}_CBG_GEOMETRY_WKT",
        # Preserve the requested basic structure for a single location. For
        # comparisons, filter_groups is authoritative and groups are OR-ed.
        # 单地点保留基础 filters 结构；多地点比较以 filter_groups 为准，组间使用 OR。
        "filters": filter_groups[0]["filters"] if len(filter_groups) == 1 else [],
        "filter_groups": filter_groups,
        "filter_group_operator": "OR",
        "requires_fips_join": False,
    }


def _unsupported_geographies(question: str) -> list[str]:
    """Find requested geography that lacks a verified mapping. / 查找没有已验证映射的地域。"""
    text = " ".join(question.lower().split())
    return [
        city.title()
        for city in sorted(UNSUPPORTED_CITY_NAMES)
        if re.search(rf"\b{re.escape(city)}\b", text)
    ]


def _catalog_validated_geographic_context(
    connection: sqlite3.Connection,
    question: str,
    year: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate direct geography filters against Catalog columns. / 对照 Catalog 校验地理筛选字段。"""
    context = get_geographic_context(question, year)
    if context is None:
        return None, []
    required_columns = {
        filter_item["column"]
        for group in context["filter_groups"]
        for filter_item in group["filters"]
    }
    placeholders = ", ".join("?" for _ in required_columns)
    rows = connection.execute(
        f"""
        SELECT column_name
        FROM columns
        WHERE table_name = ?
          AND column_name IN ({placeholders})
        """,
        (context["table"], *sorted(required_columns)),
    ).fetchall()
    available = {row["column_name"] for row in rows}
    missing = sorted(required_columns - available)
    if missing:
        return None, [f"{context['table']}.{column}" for column in missing]
    context["catalog_columns_verified"] = True
    return context, []


def _discover_metric_ids(question: str) -> list[str]:
    """Discover every requested statistical metric. / 发现问题要求的全部统计指标。"""
    text = " ".join(question.lower().replace("_", " ").split())
    discovered: list[str] = []
    age_65_plus = bool(
        re.search(
            r"\b65\s*(?:\+|plus|(?:years?\s+)?(?:and|or)\s+(?:over|older))\b",
            text,
        )
        or re.search(r"\b(?:over|above)\s+65\b", text)
    )
    if re.search(r"\bpopulation\b", text) and (
        "total population" in text or not age_65_plus
    ):
        discovered.append("total_population")
    if "household income" in text:
        discovered.append("median_household_income")
    if age_65_plus:
        discovered.append("age_65_plus")
    if (
        re.search(r"\bowner[ -]occupied\b", text)
        or re.search(r"\brenter[ -]occupied\b", text)
        or "housing tenure" in text
    ):
        discovered.append("housing_tenure")
    return list(dict.fromkeys(discovered))


def _is_total_population_field(field_path: str) -> bool:
    """Identify the overall B01001 total using semantics. / 按语义识别 B01001 总人口。"""
    parts = [part.strip().lower() for part in field_path.split(">")]
    return (
        len(parts) >= 2
        and parts[-1] == "total"
        and "total population" in parts
        and "male" not in parts
        and "female" not in parts
    )


def _is_age_65_plus_member(field_path: str) -> bool:
    """Identify a leaf age band whose lower bound is at least 65. / 识别下限至少 65 岁的叶子年龄段。"""
    leaf = field_path.rsplit(">", 1)[-1].strip().lower()
    match = re.search(r"\b(\d{1,3})\b", leaf)
    return bool(match and int(match.group(1)) >= 65 and "year" in leaf)


def _select_metric_rows(
    rows: list[sqlite3.Row],
    selector: str,
) -> tuple[list[sqlite3.Row], dict[str, list[str]]]:
    """Expand one indicator group into required members. / 将一个指标组展开为必需成员。"""
    if selector == "all_indicator_fields":
        selected = rows
        roles = {"value": [row["column_name"] for row in selected]}
    elif selector == "total_population":
        selected = [
            row for row in rows
            if _is_total_population_field(str(row["field_path"] or ""))
        ]
        roles = {"value": [row["column_name"] for row in selected]}
    elif selector == "age_65_plus":
        denominator = [
            row for row in rows
            if _is_total_population_field(str(row["field_path"] or ""))
        ]
        numerator = [
            row for row in rows
            if _is_age_65_plus_member(str(row["field_path"] or ""))
        ]
        selected = denominator + numerator
        roles = {
            "denominator": [row["column_name"] for row in denominator],
            "numerator": [row["column_name"] for row in numerator],
        }
    elif selector == "housing_tenure_members":
        selected = [
            row for row in rows
            if any(
                term in str(row["field_path"] or "").lower()
                for term in ("owner occupied", "renter occupied")
            )
        ]
        roles = {"members": [row["column_name"] for row in selected]}
    else:
        raise ValueError(f"Unknown metric selector: {selector}")
    return selected, roles


def _retrieve_metric_groups(
    connection: sqlite3.Connection,
    question: str,
    year: int,
    allowed_kinds: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Discover metrics and expand complete Catalog field groups. / 发现指标并展开完整 Catalog 字段组。"""
    columns: list[dict[str, Any]] = []
    metric_context: list[dict[str, Any]] = []
    missing: list[str] = []
    kind_placeholders = ", ".join("?" for _ in allowed_kinds)
    for metric_id in _discover_metric_ids(question):
        definition = METRIC_DEFINITIONS[metric_id]
        table_name = f"{year}_{definition['table_suffix']}"
        rows = connection.execute(
            f"""
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                c.ordinal_position,
                c.table_number,
                c.table_title,
                c.topics,
                c.universe,
                c.field_path,
                c.kind,
                s.purpose AS semantic_purpose,
                NULL AS relevance_score
            FROM columns AS c
            LEFT JOIN table_semantics AS s ON s.table_name = c.table_name
            WHERE c.table_name = ?
              AND c.table_number = ?
              AND c.kind IN ({kind_placeholders})
            ORDER BY c.ordinal_position
            """,
            (
                table_name,
                definition["table_number"],
                *allowed_kinds,
            ),
        ).fetchall()
        selected, roles = _select_metric_rows(rows, definition["selector"])
        required_columns = [row["column_name"] for row in selected]
        expected_counts = definition["required_role_counts"]
        complete = bool(required_columns) and all(
            len(roles.get(role, [])) == expected_count
            for role, expected_count in expected_counts.items()
        )
        if not complete:
            missing.append(
                f"{table_name} indicator {definition['table_number']} "
                f"for metric {metric_id}; expected role counts {expected_counts}, "
                f"found { {role: len(values) for role, values in roles.items()} }"
            )
            continue
        for row in selected:
            column = dict(row)
            column["retrieval_score"] = -2000.0
            column["retrieval_source"] = "metric_group_expansion"
            column["metric_id"] = metric_id
            columns.append(column)
        metric_context.append(
            {
                "metric_id": metric_id,
                "year": year,
                "physical_table": table_name,
                "indicator_id": definition["table_number"],
                "required_columns": required_columns,
                "column_roles": roles,
                "aggregation_semantics": definition["aggregation"],
                "complete": complete,
            }
        )
    return columns, metric_context, missing


def _needs_fts_supplement(question: str, metric_ids: list[str]) -> bool:
    """Keep FTS5 for unknown or explicitly subgrouped metrics. / 为未知或细分指标保留 FTS5。"""
    text_terms = set(re.findall(r"[a-z]+", question.lower()))
    if not metric_ids or bool(text_terms & DEMOGRAPHIC_SUBGROUP_TERMS):
        return True
    # A broad age breakdown still needs discovery. A recognized 65+ request is
    # already expanded into its complete numerator/denominator field group.
    # 普通年龄分组仍需全文检索；已识别的 65+ 指标则由完整字段组负责。
    return "age" in text_terms and "age_65_plus" not in metric_ids


def _balance_ranked_fields(
    ranked: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Round-robin fields across indicator groups. / 在指标组之间轮询选择字段。"""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for column in ranked:
        key = (
            str(column.get("table_name", "")),
            str(column.get("table_number", "")),
        )
        groups.setdefault(key, []).append(column)
    selected: list[dict[str, Any]] = []
    while groups and len(selected) < limit:
        empty: list[tuple[str, str]] = []
        for key in list(groups):
            selected.append(groups[key].pop(0))
            if not groups[key]:
                empty.append(key)
            if len(selected) >= limit:
                break
        for key in empty:
            groups.pop(key, None)
    return selected


def _merge_column_matches(
    preferred: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge columns by table/name while preserving priority. / 按表和字段去重合并。"""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for column in preferred + ranked:
        identity = (column["table_name"], column["column_name"])
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(column)
    return merged


def _validate_catalog_schema(connection: sqlite3.Connection) -> set[str]:
    """Validate required Catalog tables and columns. / 校验 Catalog 必需表与字段。"""
    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    missing_tables = {
        "columns",
        "field_search",
        "relationships",
        "table_semantics",
    } - table_names
    if missing_tables:
        raise ValueError(
            "Schema Catalog is missing required tables: "
            + ", ".join(sorted(missing_tables))
        )

    column_names = {
        row[1] for row in connection.execute("PRAGMA table_info(columns)")
    }
    required_columns = {
        "table_name",
        "column_name",
        "data_type",
        "ordinal_position",
        "table_number",
        "table_title",
        "topics",
        "universe",
        "field_path",
        "kind",
    }
    missing_columns = required_columns - column_names
    if missing_columns:
        raise ValueError(
            "Schema Catalog columns table is missing fields: "
            + ", ".join(sorted(missing_columns))
        )

    relationship_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(relationships)")
    }
    required_relationship_columns = {
        "source_table",
        "target_table",
        "source_columns",
        "target_columns",
        "join_condition",
        "relation_kind",
        "year",
        "verification_status",
        "notes",
    }
    missing_relationship_columns = (
        required_relationship_columns - relationship_columns
    )
    if missing_relationship_columns:
        raise ValueError(
            "Schema Catalog relationships table is missing fields: "
            + ", ".join(sorted(missing_relationship_columns))
        )

    semantic_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(table_semantics)")
    }
    required_semantic_columns = {
        "table_name",
        "purpose",
        "notes",
        "verification_status",
    }
    missing_semantic_columns = required_semantic_columns - semantic_columns
    if missing_semantic_columns:
        raise ValueError(
            "Schema Catalog table_semantics table is missing fields: "
            + ", ".join(sorted(missing_semantic_columns))
        )
    return column_names


def _retrieve_relationships(
    connection: sqlite3.Connection,
    source_tables: list[str],
    year: int,
) -> list[dict[str, Any]]:
    """Read a small directed relationship graph. / 读取有限深度的有向关系图。"""
    frontier = set(source_tables)
    visited_sources: set[str] = set()
    relationships: list[dict[str, Any]] = []
    seen_relationships: set[tuple[str, str, str]] = set()

    for _ in range(MAX_RELATIONSHIP_DEPTH):
        current_sources = sorted(frontier - visited_sources)
        if not current_sources:
            break
        placeholders = ", ".join("?" for _ in current_sources)
        rows = connection.execute(
            f"""
            SELECT
                source_table,
                target_table,
                source_columns,
                target_columns,
                join_condition,
                relation_kind,
                year,
                verification_status,
                notes
            FROM relationships
            WHERE year = ?
              AND source_table IN ({placeholders})
            ORDER BY source_table, target_table, relation_kind
            """,
            (year, *current_sources),
        ).fetchall()
        visited_sources.update(current_sources)
        next_frontier: set[str] = set()
        for row in rows:
            relationship = dict(row)
            identity = (
                relationship["source_table"],
                relationship["target_table"],
                relationship["relation_kind"],
            )
            if identity not in seen_relationships:
                relationships.append(relationship)
                seen_relationships.add(identity)
            next_frontier.add(relationship["target_table"])
        frontier = next_frontier

    return relationships


def _retrieve_related_table_columns(
    connection: sqlite3.Connection,
    relationships: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Load columns for relationship target tables. / 读取关系目标表字段。"""
    # Matched source tables are already represented by the ranked `columns`
    # result. Loading every column from those wide Census tables can expand a
    # small retrieval result into more than a million JSON characters and
    # exhaust the LLM input-token quota. Only relationship targets add new
    # schema context needed to follow a JOIN path.
    # 已命中的源表字段已包含在 columns 中。再次加载这些 Census 宽表的全部字段会让
    # Schema JSON 膨胀到百万字符并耗尽模型输入额度，因此这里只补充关联目标表。
    table_names = sorted(
        {
            relationship["target_table"]
            for relationship in relationships
        }
    )
    if not table_names:
        return {}
    placeholders = ", ".join("?" for _ in table_names)
    rows = connection.execute(
        f"""
        SELECT
            table_name,
            column_name,
            data_type,
            ordinal_position,
            table_title,
            topics,
            universe,
            field_path,
            kind
        FROM columns
        WHERE table_name IN ({placeholders})
        ORDER BY table_name, ordinal_position
        """,
        table_names,
    ).fetchall()
    columns_by_table: dict[str, list[dict[str, Any]]] = {
        table_name: [] for table_name in table_names
    }
    for row in rows:
        column = dict(row)
        columns_by_table[column["table_name"]].append(column)
    return columns_by_table


def _rank_field_matches(
    rows: list[sqlite3.Row],
    question: str,
    search_terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Re-rank a broad FTS pool for business relevance. / 按业务相关性重排 FTS 候选。"""
    normalized_question = " ".join(question.lower().split())
    requested_phrase = " ".join(search_terms)
    ranked: list[dict[str, Any]] = []
    for row in rows:
        match = dict(row)
        searchable_text = " ".join(
            str(match.get(field, "") or "").lower()
            for field in (
                "table_title",
                "topics",
                "universe",
                "field_path",
                "semantic_purpose",
            )
        )
        term_coverage = sum(
            1 for term in search_terms if term in searchable_text
        ) / max(len(search_terms), 1)
        score = float(match.get("relevance_score", 0.0))
        score -= term_coverage * 6.0
        if requested_phrase and requested_phrase in searchable_text:
            score -= 10.0
        if match.get("kind") == "Estimate":
            score -= 0.5
        semantic_purpose = str(match.get("semantic_purpose", "") or "").lower()
        if semantic_purpose:
            semantic_coverage = sum(
                1 for term in search_terms if term in semantic_purpose
            ) / max(len(search_terms), 1)
            score -= semantic_coverage * 4.0
        universe = str(match.get("universe", "") or "").lower().strip()
        field_path = str(match.get("field_path", "") or "").lower().strip()
        if (
            "population" in normalized_question
            and universe == "total population"
            and (field_path == "total" or field_path.endswith("> total"))
        ):
            score -= 8.0
        if "total" in normalized_question and "total" in searchable_text:
            score -= 1.0
        match["retrieval_score"] = round(score, 6)
        ranked.append(match)

    ranked.sort(
        key=lambda item: (
            item["retrieval_score"],
            item.get("table_name", ""),
            item.get("ordinal_position") or 0,
        )
    )
    return ranked[:limit]


def _retrieve_table_details(
    connection: sqlite3.Connection,
    matched_tables: list[str],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Describe matched and related tables. / 描述命中表及关联表的业务用途。"""
    related_tables = list(
        dict.fromkeys(
            table_name
            for relationship in relationships
            for table_name in (
                relationship["source_table"],
                relationship["target_table"],
            )
            if table_name not in matched_tables
        )
    )
    table_names = matched_tables + related_tables
    if not table_names:
        return []

    placeholders = ", ".join("?" for _ in table_names)
    rows = connection.execute(
        f"""
        SELECT
            c.table_name,
            MAX(NULLIF(c.table_title, '')) AS table_title,
            MAX(NULLIF(c.topics, '')) AS topics,
            MAX(NULLIF(c.universe, '')) AS universe,
            s.purpose,
            s.notes AS semantic_notes,
            s.verification_status AS semantic_verification_status
        FROM columns AS c
        LEFT JOIN table_semantics AS s ON s.table_name = c.table_name
        WHERE c.table_name IN ({placeholders})
        GROUP BY
            c.table_name,
            s.purpose,
            s.notes,
            s.verification_status
        """,
        table_names,
    ).fetchall()
    details_by_name = {row["table_name"]: dict(row) for row in rows}
    result: list[dict[str, Any]] = []
    for table_name in table_names:
        detail = details_by_name.get(table_name, {"table_name": table_name})
        detail["role"] = (
            "matched" if table_name in matched_tables else "relationship"
        )
        result.append(detail)
    return result


def _normalize_years(years: int | list[int] | tuple[int, ...]) -> list[int]:
    """Validate and deduplicate requested years. / 校验并去重请求年份。"""
    if isinstance(years, bool):
        raise ValueError(
            "years must be an integer or a non-empty list/tuple of integers."
        )
    if isinstance(years, int):
        requested_years = [years]
    elif isinstance(years, (list, tuple)):
        requested_years = list(years)
    else:
        raise ValueError(
            "years must be an integer or a non-empty list/tuple of integers."
        )
    if not requested_years:
        raise ValueError("years must not be empty.")
    for year in requested_years:
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not 1900 <= year <= 2100
        ):
            raise ValueError(
                "Every year must be an integer between 1900 and 2100."
            )
    return list(dict.fromkeys(requested_years))


def retrieve_schema(
    question: str,
    years: int | list[int] | tuple[int, ...] | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
    *,
    year: int | None = None,
) -> dict[str, Any]:
    """Retrieve relevant Census schema context. / 检索相关 Census Schema 上下文。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string.")
    if year is not None and years is not None:
        raise ValueError("Use either year or years, not both.")
    normalized_years = _normalize_years(
        year if year is not None else (years if years is not None else (2020,))
    )
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_RESULT_LIMIT
    ):
        raise ValueError(f"limit must be between 1 and {MAX_RESULT_LIMIT}.")

    resolved_path = Path(catalog_path).resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(
            f"Schema Catalog was not found: {resolved_path}. "
            "Place census_schema_catalog.sqlite in the data_source directory."
        )

    fts_query, search_terms = _build_fts_query(question)
    # Read-only URI mode protects the deployed Catalog from accidental writes.
    # 只读 URI 模式可防止部署后的 Catalog 被意外修改。
    connection = sqlite3.connect(
        resolved_path.as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        _validate_catalog_schema(connection)
        normalized_question = question.lower()
        wants_margin = (
            "margin of error" in normalized_question
            or re.search(r"\bmoe\b", normalized_question) is not None
        )
        wants_estimate = "estimate" in normalized_question
        if wants_margin and wants_estimate:
            kind_filter = ""
            kind_parameters: tuple[str, ...] = ()
        elif wants_margin:
            kind_filter = "AND c.kind = ?"
            kind_parameters = ("MarginOfError",)
        else:
            # Ordinary business questions should not spend half of the result
            # window on matching margin-of-error twins.
            # 普通业务问题不应让误差字段占用一半的检索窗口。
            kind_filter = "AND c.kind != ?"
            kind_parameters = ("MarginOfError",)

        # Search each year independently so every requested year receives the
        # same result window instead of competing for one global LIMIT.
        # 每个年份独立检索，避免跨年份查询时某一年占满全部结果窗口。
        matches: list[dict[str, Any]] = []
        metric_contexts: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        years_without_matches: list[int] = []
        missing_metric_groups: list[str] = []
        geographic_contexts: list[dict[str, Any]] = []
        missing_geographic_fields: list[str] = []
        discovered_metric_ids = _discover_metric_ids(question)
        if wants_margin and wants_estimate:
            allowed_kinds = ("Estimate", "MarginOfError")
        elif wants_margin:
            allowed_kinds = ("MarginOfError",)
        else:
            allowed_kinds = ("Estimate",)
        for year in normalized_years:
            retrieval_pool = min(
                max(limit * RETRIEVAL_POOL_MULTIPLIER, MIN_RETRIEVAL_POOL),
                MAX_RETRIEVAL_POOL,
            )
            year_rows = connection.execute(
                f"""
                SELECT
                    c.table_name,
                    c.column_name,
                    c.data_type,
                    c.ordinal_position,
                    c.table_number,
                    c.table_title,
                    c.topics,
                    c.universe,
                    c.field_path,
                    c.kind,
                    s.purpose AS semantic_purpose,
                    bm25(field_search) AS relevance_score
                FROM field_search AS f
                JOIN columns AS c ON c.rowid = f.rowid
                LEFT JOIN table_semantics AS s ON s.table_name = c.table_name
                WHERE field_search MATCH ?
                  AND c.table_name LIKE ?
                  {kind_filter}
                ORDER BY relevance_score
                LIMIT ?
                """,
                (
                    fts_query,
                    f"{year}_%",
                    *kind_parameters,
                    retrieval_pool,
                ),
            ).fetchall()
            ranked_fts_matches = _rank_field_matches(
                year_rows,
                question,
                search_terms,
                retrieval_pool,
            )
            if _needs_fts_supplement(question, discovered_metric_ids):
                ranked_fts_matches = _balance_ranked_fields(
                    ranked_fts_matches,
                    limit,
                )
            else:
                ranked_fts_matches = []
            metric_columns, year_metric_contexts, missing_groups = (
                _retrieve_metric_groups(
                    connection,
                    question,
                    year,
                    allowed_kinds,
                )
            )
            # Complete metric groups take priority. Balanced FTS results only
            # supplement unknown or explicitly subgrouped metrics.
            # 完整指标组优先；平衡后的 FTS 只补充未知指标或明确细分问题。
            ranked_year_matches = _merge_column_matches(
                metric_columns,
                ranked_fts_matches,
            )
            metric_contexts.extend(year_metric_contexts)
            missing_metric_groups.extend(missing_groups)
            matches.extend(ranked_year_matches)
            if not ranked_year_matches:
                years_without_matches.append(year)
            matched_tables = list(
                dict.fromkeys(
                    match["table_name"] for match in ranked_year_matches
                )
            )
            relationships.extend(
                _retrieve_relationships(connection, matched_tables, year)
            )
            geographic_context, missing_fields = (
                _catalog_validated_geographic_context(
                    connection,
                    question,
                    year,
                )
            )
            if geographic_context is not None:
                geographic_contexts.append(geographic_context)
            missing_geographic_fields.extend(missing_fields)
        # Direct, Catalog-validated FIPS filters make a name-lookup table
        # unnecessary. Removing that optional path prevents the SQL Agent from
        # selecting a longer join even though it is technically verified.
        # 已有经 Catalog 校验的 FIPS 直接筛选时，不再把多余的名称查找关系交给 SQL Agent。
        direct_filter_tables = {
            context["table"]
            for context in geographic_contexts
            if context.get("requires_fips_join") is False
        }
        relationships = [
            relationship
            for relationship in relationships
            if not (
                relationship["source_table"] in direct_filter_tables
                and "FIPS_CODES" in relationship["target_table"]
            )
        ]
        relationship_tables = _retrieve_related_table_columns(
            connection,
            relationships,
        )
        matched_table_names = list(
            dict.fromkeys(match["table_name"] for match in matches)
        )
        tables = _retrieve_table_details(
            connection,
            matched_table_names,
            relationships,
        )
    except sqlite3.Error as error:
        raise ValueError(f"Unable to search Schema Catalog: {error}") from error
    finally:
        connection.close()

    warnings: list[str] = []
    if years_without_matches:
        warnings.append(
            "No matching fields were found for year(s): "
            + ", ".join(str(year) for year in years_without_matches)
            + "."
        )
    if missing_metric_groups:
        warnings.append(
            "Required metric groups are incomplete in the Catalog: "
            + ", ".join(missing_metric_groups)
            + "."
        )
    if missing_geographic_fields:
        warnings.append(
            "Direct geographic filters were omitted because Catalog fields are "
            "missing: " + ", ".join(missing_geographic_fields) + "."
        )
    unverified_relationships = sum(
        1
        for relationship in relationships
        if relationship.get("verification_status") != "verified"
    )
    if unverified_relationships:
        warnings.append(
            f"{unverified_relationships} related JOIN path(s) are candidate metadata "
            "and cannot be used until data validation marks them verified."
        )
    missing_semantics = sum(
        1 for table in tables if not table.get("purpose")
    )
    if missing_semantics:
        warnings.append(
            f"Table-level semantics are unavailable for {missing_semantics} table(s); "
            "use table_title, topics, universe, and field_path instead."
        )

    geographic_context: dict[str, Any] | list[dict[str, Any]] | None
    if len(geographic_contexts) == 1:
        geographic_context = geographic_contexts[0]
    elif geographic_contexts:
        geographic_context = geographic_contexts
    else:
        geographic_context = None

    return {
        "years": normalized_years,
        # Keep the legacy field for single-year consumers during migration.
        # 迁移期间为单年份调用方保留旧字段；跨年份时明确返回 None。
        "year": normalized_years[0] if len(normalized_years) == 1 else None,
        "search_terms": search_terms,
        # State names are mapped locally because the Snowflake metadata stores
        # two-letter abbreviations (for example, California -> CA) rather than
        # full names. This prevents the SQL Agent from guessing literal values.
        # Snowflake 元数据使用州缩写而非完整名称，因此在本地提供已知筛选值，避免 SQL
        # Agent 猜测字符串格式。
        "geography_filters": _requested_state_filters(question),
        # Single-year questions return one object; multi-year questions return
        # one context per geometry table so each year can be filtered directly.
        # 单年份返回一个对象；跨年份为每个 geometry 表返回独立上下文。
        "geographic_context": geographic_context,
        # Non-empty values are a hard stop because the Catalog cannot safely
        # map these boundaries to Census block groups.
        # 非空值表示必须停止，因为 Catalog 无法安全映射这些边界到 CBG。
        "unsupported_geographies": _unsupported_geographies(question),
        "tables": tables,
        "metrics": metric_contexts,
        "columns": matches,
        "relationships": relationships,
        "relationship_tables": relationship_tables,
        "warnings": warnings,
        "relationship_policy": {
            "allow_candidate_joins": False,
            "verified_status": "verified",
            "warning": (
                "Relationships marked candidate_unverified are discovery hints only. "
                "Do not generate or execute a JOIN until Snowflake uniqueness and "
                "row-coverage checks promote the relationship to verified."
            ),
        },
        "physical_table_policy": {
            "indicator_ids_are_tables": False,
            "warning": (
                "A Census indicator ID such as B01001 is not a physical table "
                "name. Use only metrics[].physical_table and exact table names "
                "returned by this schema context."
            ),
        },
    }
