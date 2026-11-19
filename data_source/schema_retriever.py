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

# These terms describe conversational structure or geography rather than a
# Census metric. Removing them keeps searches focused on fields such as
# population, median household income, age, and housing.
# 这些词描述问句结构或地理位置，而不是 Census 指标；过滤后可让检索更聚焦。
_IGNORED_TERMS = {
    "a", "about", "all", "and", "are", "as", "at", "average", "be",
    "by", "can", "census", "compare", "county", "data", "did", "do",
    "does", "for", "from", "give", "how", "i", "in", "is", "it",
    "many", "me", "of", "on", "or", "please", "show", "state", "than", "that",
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
    missing_tables = {"columns", "field_search"} - table_names
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
    return column_names


def retrieve_schema(
    question: str,
    year: int = 2020,
    limit: int = DEFAULT_RESULT_LIMIT,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> dict[str, Any]:
    """Retrieve relevant Census schema context. / 检索相关 Census Schema 上下文。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string.")
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2100:
        raise ValueError("year must be an integer between 1900 and 2100.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULT_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RESULT_LIMIT}.")

    resolved_path = Path(catalog_path).resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(
            f"Schema Catalog was not found: {resolved_path}. "
            "Place census_schema_catalog.sqlite in the sql directory."
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
                bm25(field_search) AS relevance_score
            FROM field_search AS f
            JOIN columns AS c ON c.rowid = f.rowid
            WHERE field_search MATCH ?
              AND c.table_name LIKE ?
              {kind_filter}
            ORDER BY relevance_score
            LIMIT ?
            """,
            (
                fts_query,
                f"{year}_CBG_%",
                *kind_parameters,
                limit,
            ),
        ).fetchall()

        geography_table = f"{year}_METADATA_CBG_GEOGRAPHIC_DATA"
        fips_table = f"{year}_METADATA_CBG_FIPS_CODES"
        geography_columns = connection.execute(
            """
            SELECT column_name, data_type, ordinal_position
            FROM columns
            WHERE table_name = ?
            ORDER BY ordinal_position
            """,
            (geography_table,),
        ).fetchall()
        fips_columns = connection.execute(
            """
            SELECT column_name, data_type, ordinal_position
            FROM columns
            WHERE table_name = ?
            ORDER BY ordinal_position
            """,
            (fips_table,),
        ).fetchall()
    except sqlite3.Error as error:
        raise ValueError(f"Unable to search Schema Catalog: {error}") from error
    finally:
        connection.close()

    matches = [dict(row) for row in rows]
    tables = list(dict.fromkeys(match["table_name"] for match in matches))
    return {
        "year": year,
        "search_terms": search_terms,
        "tables": tables,
        "columns": matches,
        "geography_join": {
            "table": geography_table,
            "key": "CENSUS_BLOCK_GROUP",
            "columns": [dict(row) for row in geography_columns],
        },
        "fips_lookup": {
            "table": fips_table,
            "columns": [dict(row) for row in fips_columns],
            "cbg_state_filter": (
                "The first two characters of CENSUS_BLOCK_GROUP match STATE_FIPS."
            ),
            "cbg_county_filter": (
                "The first five characters of CENSUS_BLOCK_GROUP match COUNTY_FIPS."
            ),
        },
    }
