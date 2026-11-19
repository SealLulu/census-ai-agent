# Census AI | US Population Insights

Explore US Census data through natural language with multi-agent intelligence.

## Project structure

```text
streamlit_app.py       Streamlit UI, login, session state, and timed refresh
cold_start.py          Configuration loading, validation, and user-file checks
requirements.txt       Python dependencies for local and remote deployment
page.py                Two page operations: load_history and chat
chat.py                Main chat workflow
TODO.md                Planned concurrency and deployment extensions
agents/
  intent_agent.py      Intent classification, clarification, and question rewrite
  answer_agent.py      Final streamed answer generation
data_source/
  census_data_service.py  Snowflake Census data access
  schema_retriever.py  Local SQLite FTS5 Census Schema retrieval
  sql_agent.py         Schema-grounded Snowflake Text-to-SQL generation
  sql_validator.py     AST-based Snowflake SQL safety validation
  census_schema_catalog.sqlite  Metadata-only Schema Catalog
  test/                SQL generation, validation, and Snowflake smoke tests
services/
  history_service.py   JSONL schema, storage, conversion, and chat_id merging
  llm_service.py       Google Gemini client and shared model configuration
  session_service.py   Single-instance in-memory online-user registry
startup/
  local_config.toml    Local fallback application settings
  gemini_key.toml      Local Google Gemini API key
  snowflake.toml       Local Snowflake credentials
  start_win.ps1        Windows startup script

.streamlit/
  config.toml          Non-secret headless/telemetry runtime settings
chat_history/          Local per-user JSONL chat histories
```

Each Python module starts with a bilingual summary and a list of the functions it contains.

## Application flow

1. `streamlit_app.py` runs the cold-start gate, handles login/UI state, and refreshes history every minute.
2. `cold_start.py` loads and validates all configuration, creates the chat-history directory, and ensures every allowed user has a JSONL file.
3. After login, `streamlit_app.py` calls `page.load_history`; for new input it calls `page.chat`.
4. `agents/intent_agent.py` classifies and rewrites the question. Each clarification request is appended to the current user's in-memory `pending_list`; the list keeps at most five items and `NEW` truncates the previous chain.
5. `data_source/schema_retriever.py` retrieves the relevant metadata, `data_source/sql_agent.py` generates SQL, and `data_source/sql_validator.py` verifies the SQL before execution.
6. `data_source/census_data_service.py` executes validated SQL in Snowflake, then `agents/answer_agent.py` generates the final answer.
7. `services/history_service.py` owns the JSONL format and merges timed refreshes by `chat_id` so active chats are not duplicated.
8. `services/session_service.py` allows only one in-memory browser session per username. Explicit Logout releases the username.

After receiving text, `page.chat` renders transient progress at 5% before calling the Intent Agent. Intent and Text-to-SQL wait for complete, non-streamed structured responses. Requests that continue through the pipeline advance to 25% after Intent and 60% after the Snowflake data-source stage. Only the Answer Agent streams its response; after the stream completes the page reaches 100%, persists only the complete response, and reruns so refreshed history displays the complete message. Progress itself is never written to JSONL.

All three Agents use `gemini-3.5-flash-lite` first and fall back to
`gemini-3.6-flash` for temporary model, quota, timeout, or server failures.
Intent and Text-to-SQL use complete JSON Schema outputs. Answer streams normally
and changes models only when no response chunk has been displayed yet.

`data_source.census_data_service.query_census_data` executes validated read-only `SELECT` statements against the Snowflake-hosted US Open Census Data & Neighborhood Insights - Free Dataset. It serializes the returned columns and rows as `data_input` for answer generation.

The Intent Agent is also the operational scope guardrail: unrelated, unsupported, prompt-injection, private/individual-level, and non-US requests fail closed as `INVALID`. The Answer Agent treats current Snowflake rows and relevant prior assistant answers as its only factual evidence. It checks whether that evidence covers the complete request, answers only supported portions when coverage is partial, and returns a fixed no-data response when no requested component is supported. Empty or truncated results, CBG samples, estimates, margins of error, medians, percentages, and cross-year comparisons are handled conservatively rather than extrapolated.

## Schema Catalog

The application reads the generated metadata-only Catalog at `data_source/census_schema_catalog_verified.sqlite`. The original input remains at `data_source/census_schema_catalog.sqlite`. Both SQLite files contain `columns`, the FTS5 `field_search` index, `relationships`, and `table_semantics`. `data_source.schema_retriever.retrieve_schema(question, years=[2019, 2020])` opens the verified Catalog once in read-only mode and searches all relevant `{year}_%` tables independently for every requested year, including regular CBG tables, Redistricting tables, and geographic metadata. A single-year request uses `[2019]` or `[2020]`; the application defaults to `[2020]` when the question contains no year.

The Retriever first obtains a wider FTS5 candidate pool, filters ordinary questions toward Estimate fields instead of MarginOfError fields, and then re-ranks candidates using phrase coverage, business descriptions, and total-population semantics. Explicit population, median-household-income, and housing-tenure phrases also supplement FTS5 with real Catalog rows; owner-occupied/renter-occupied requests always receive both required tenure fields. Its response contains enriched `tables`, ranked `columns`, related JOIN paths, related-table columns, and explicit `warnings`.

For recognized state and county requests, `geographic_context` contains exact `STATE_FIPS` and `COUNTY_FIPS` filters that have also been checked against the geometry-table columns in the Catalog. These filters are applied directly to the matching `{year}_CBG_GEOMETRY_WKT` table with `requires_fips_join = false`; an extra FIPS lookup JOIN is not generated. The California, Texas, Washington, New York, and King County test mappings were checked against Snowflake; King County uses `STATE_FIPS=53` and `COUNTY_FIPS=033`. Multi-state comparisons use OR-ed filter groups. The legacy `geography_filters` field remains for compatibility. A request such as Seattle that lacks a verified city-to-CBG boundary mapping is returned in `unsupported_geographies` and fails closed instead of silently becoming a Washington-wide query. Table-level purpose information is included where `table_semantics` provides it; remaining tables continue to rely on `table_title`, `topics`, `universe`, and `field_path`. For compatibility, a single year can also be passed as `year=2020`; do not pass `year` and `years` together.

Catalog relationships default to `verification_status = "candidate_unverified"`. They are returned as discovery context, but the SQL Agent is instructed not to generate those JOINs and the SQL Validator rejects them. Run `python scripts/verify_relationships.py` to copy the source Catalog and generate `data_source/census_schema_catalog_verified.sqlite`. The script uses read-only Snowflake `SELECT` queries to check key non-nullness, source/target uniqueness, and complete source coverage for every 2019/2020 `cbg_to_geography` candidate; it never changes Snowflake or the source Catalog. In the current Catalog, 65 of 66 CBG-to-geometry relationships passed. `2019_CBG_PATTERNS` remains unverified because its source key contains null and unmatched rows. Other relationship kinds are not implicitly verified and remain blocked until separately checked. The SQL Agent prefers the shortest verified path and does not aggregate CBG-level medians, rates, or percentages into statistically invalid state/county/city values.

The retriever is used by the chat workflow before Text-to-SQL generation. Its real Catalog results have been verified for population, household income, housing, and margin-of-error queries.

## Text-to-SQL Agent

`data_source.sql_agent.generate_sql` accepts a question plus the compact context returned by `retrieve_schema`. It returns a Pydantic-validated `SQLResult` with either `success` and one Snowflake SELECT statement, or `insufficient_schema` with a reason and the missing definitions. It only generates SQL; it does not validate or execute the statement.

Run the manual generation smoke test after configuring the Gemini API key. It does not connect to Snowflake:

```powershell
python -m data_source.test.test_sql_agent
```

Before Snowflake execution, `data_source.sql_validator.validate_sql` parses the statement with the Snowflake dialect and rejects multiple statements, non-SELECT operations, wildcard fields, unqualified or unrecognized tables, columns outside the retrieved context, unverified joins, and missing or excessive limits. `data_source.census_data_service.query_census_data` validates again, logs the normalized SQL immediately before the Snowflake call, uses the configured read-only role, applies connection and 30-second statement timeouts, and returns at most 1,000 rows. Credentials and returned rows are never written to this SQL log. Validation, connection, timeout, and execution failures are logged with their technical details, while the chat page returns the user-friendly English message `The Census data query failed. Please try again later.` without exposing SQL or credentials.

Gemini failures are also degraded gracefully after bounded fallback and retry attempts. A temporary `503` or timeout displays `The AI model is temporarily busy. Please wait a moment and try again.` A `429` quota or rate-limit response displays `The AI model usage limit has been reached. Please wait and try again later.` Technical details remain in server logs, and the displayed message is stored as the completed chat response.

Each authenticated browser session permits only one active chat workflow. After a question is submitted, Streamlit immediately reruns with the chat input disabled and displays `Processing the current question...`. The input is re-enabled only after the current Agent/Snowflake workflow completes or returns a handled error. A server-side session-state guard also rejects stale duplicate submissions, so one user cannot replace an in-flight question with a new one.

After configuring a valid API key and all Snowflake credentials, run the manual end-to-end test:

```powershell
python -m data_source.test.test_data_source_flow
```

Pass a custom question to inspect the generated SQL and returned data:

```powershell
python -m data_source.test.test_data_source_flow --question "What is the 2020 median household income for 10 Census block groups?"
```

For a cross-year query, include both years in the question. You can also pass
the Catalog years explicitly:

```powershell
python -m data_source.test.test_data_source_flow --years 2019 2020 --question "Compare 2019 and 2020 median household income for 10 Census block groups."
```

To test Snowflake independently without calling GPT, configure only Snowflake and run the fixed, validated 10-row query:

```powershell
python -m data_source.test.test_snowflake_query
```

## Configuration

Before starting locally, confirm that:

- `startup/gemini_key.toml` contains a valid `GEMINI_API_KEY`.
- For a new workspace, copy `startup/gemini_key.example.toml` to
  `startup/gemini_key.toml`, then replace the placeholder with your Gemini API key.
- `startup/local_config.toml` contains an `ALLOWED_USERS` list and a positive integer `CHAT_HISTORY_LOAD_LIMIT`.
- `startup/snowflake.toml` contains valid local Snowflake connection settings.

The tracked `.streamlit/config.toml` contains runtime settings only—never
credentials. It enables headless mode and disables usage-stat collection so a
fresh workspace can start without Streamlit's first-run email prompt or a write
to the user's global `.streamlit` directory.

Example local configuration:

```toml
ALLOWED_USERS = ["user"]
CHAT_HISTORY_LOAD_LIMIT = 50
```

Example PAT configuration in `startup/snowflake.toml` (ignored by Git because it contains credentials):

```toml
[SNOWFLAKE]
auth_method = "PROGRAMMATIC_ACCESS_TOKEN"
account = "YOUR_ORG-YOUR_ACCOUNT"
user = "YOUR_SNOWFLAKE_LOGIN_NAME"
token = "YOUR_OAUTH_ACCESS_TOKEN"
warehouse = "COMPUTE_WH"
role = "YOUR_READ_ONLY_ROLE" # Optional if the token/default role already supplies it.
schema = "PUBLIC"
```

The application always uses the fixed Snowflake database
`US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET`; it is not read from
local or server configuration.

For OAuth authentication, set `auth_method = "oauth"`. For password authentication,
set `auth_method = "password"` and replace `token` with `password`. Never commit
any credential. The connector account identifier
uses the `organization-account` form; the Snowflake login name is still required
separately.

On a server, Streamlit Secrets take priority for `GEMINI_API_KEY`,
`ALLOWED_USERS`, and `CHAT_HISTORY_LOAD_LIMIT`. Snowflake accepts the top-level
remote values `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_WAREHOUSE`, and
`SNOWFLAKE_TOKEN`; each can also be supplied as an environment variable.
Remaining Snowflake settings, and local fallbacks for those three values, come
from `startup/snowflake.toml`.

Example Streamlit Secrets section:

```toml
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
SNOWFLAKE_ACCOUNT = "YOUR_ORG-YOUR_ACCOUNT"
SNOWFLAKE_WAREHOUSE = "COMPUTE_WH"
SNOWFLAKE_TOKEN = "YOUR_PROGRAMMATIC_ACCESS_TOKEN"
```

`SNOWFLAKE_ACCOUNT` should normally use the complete `organization-account`
identifier. If the remote secret contains only the account name and it exactly
matches the suffix of the local full identifier, cold start safely restores the
local organization prefix. Full `https://...snowflakecomputing.com` account URLs
are also normalized. The project pins Snowflake Connector 4.8.1 for consistent
Python 3.14 deployment behavior instead of automatically adopting a new major
connector release.

## Chat history

Chat history is stored locally in `chat_history` using JSON Lines (`.jsonl`), with one complete Q&A record per line. Login loads only the latest `CHAT_HISTORY_LOAD_LIMIT` records. The directory is retained in Git through `.gitkeep`, while generated history files are ignored.

Each line uses this format:

```json
{"chat_id":"","text":"","rewritten_question":"","time_stamp":"","valid_status":0,"response":"","response_status":0,"sql":"","sql_resp":"","intent_status":"","resolution":"","pending_question":"","pending_clarification":""}
```

- `chat_id`: a UUID that uniquely identifies one question/answer record and prevents duplicates during timed refresh.
- `text`: the original user input displayed in the chat UI.
- `rewritten_question`: the standalone question produced by the Intent Agent and used for Agent history, Schema Retrieval, Text-to-SQL, and answer generation. Legacy rows fall back to `text`.
- `valid_status`: `0` means valid; `-1` means invalid. Invalid records keep `response` empty and the page displays `I cannot answer that question.`
- `response_status`: `0` means completed; a non-zero value is reserved for an in-memory request that is still processing. If model processing fails, the English service-error response is stored as a completed record so history refreshes do not duplicate a user-only pending question. This is separate from semantic clarification state.
- `intent_status`: stores `INVALID`, `NEW`, or `FOLLOW_UP`, allowing history reloads to truncate the pending chain at a `NEW` boundary.
- `resolution`: stores `NONE`, `NEEDS_CLARIFICATION`, or `RESOLVED_PENDING`. `FOLLOW_UP` controls whether answer generation receives recent history; `NEEDS_CLARIFICATION` ends the current pipeline immediately and returns the Intent Agent's clarification question.
- `sql` and `sql_resp` are reserved for the Census data-source workflow.
- `pending_question` and `pending_clarification` store this user's clarification snapshot after the turn. Login and the one-minute history refresh replay `resolution` events to rebuild at most the latest five unresolved items, starting after the latest `NEW` boundary. The list is kept in the browser's Streamlit session and is not shared between users.

## Run on Windows

The startup script creates or reuses `.venv`, installs dependencies, and starts Streamlit:

```powershell
powershell -ExecutionPolicy Bypass -File .\startup\start_win.ps1
```

## Run manually

```powershell
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Run the manual commands from the repository root so Streamlit discovers
`.streamlit/config.toml`. The Windows startup script changes to that directory
automatically and also passes the same safe settings explicitly.
