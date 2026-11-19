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
DEFAULT_CATALOG_PATH = BASE_DIR / "census_schema_catalog.sqlite"
DEFAULT_RESULT_LIMIT = 20
MAX_RESULT_LIMIT = 100
MAX_RELATIONSHIP_DEPTH = 2
RETRIEVAL_POOL_MULTIPLIER = 8
MIN_RETRIEVAL_POOL = 200
MAX_RETRIEVAL_POOL = 500

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
        relationships: list[dict[str, Any]] = []
        years_without_matches: list[int] = []
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
            ranked_year_matches = _rank_field_matches(
                year_rows,
                question,
                search_terms,
                limit,
            )
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

    return {
        "years": normalized_years,
        # Keep the legacy field for single-year consumers during migration.
        # 迁移期间为单年份调用方保留旧字段；跨年份时明确返回 None。
        "year": normalized_years[0] if len(normalized_years) == 1 else None,
        "search_terms": search_terms,
        "tables": tables,
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
    }
