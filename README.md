# Census AI | US Population Insights

Explore US Census data through natural language with multi-agent intelligence.

## Project structure

```text
streamlit_app.py       Streamlit UI, login, session state, and timed refresh
cold_start.py          Configuration loading, validation, and user-file checks
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
  session_service.py   Single-instance in-memory online-user registry
startup/
  local_config.toml    Local fallback application settings
  openai_key.toml      Local OpenAI API key
  snowflake.toml       Local Snowflake credentials
  requirements.txt     Python dependencies
  start_win.ps1        Windows startup script
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

`data_source.census_data_service.query_census_data` executes validated read-only `SELECT` statements against the Snowflake-hosted US Open Census Data & Neighborhood Insights - Free Dataset. It serializes the returned columns and rows as `data_input` for answer generation.

## Schema Catalog

Place the generated metadata-only Catalog at `data_source/census_schema_catalog.sqlite`. `data_source.schema_retriever.retrieve_schema(question, year=2020)` opens it in read-only mode, searches `field_search` with SQLite FTS5, restricts results to the requested `{year}_CBG_%` tables, and returns at most 20 ranked fields by default. Normal questions exclude `MarginOfError` twins; questions explicitly asking for margin of error retrieve them. Results include type, table number, topic, universe, field path, and kind. The returned context also describes the `CENSUS_BLOCK_GROUP` geographic join and state/county FIPS prefix filters.

The retriever is used by the chat workflow before Text-to-SQL generation. Its real Catalog results have been verified for population, household income, housing, and margin-of-error queries.

## Text-to-SQL Agent

`data_source.sql_agent.generate_sql` accepts a question plus the compact context returned by `retrieve_schema`. It returns a Pydantic-validated `SQLResult` with either `success` and one Snowflake SELECT statement, or `insufficient_schema` with a reason and the missing definitions. It only generates SQL; it does not validate or execute the statement.

Run the manual generation smoke test after configuring the OpenAI API key. It does not connect to Snowflake:

```powershell
python -m data_source.test.test_sql_agent
```

Before Snowflake execution, `data_source.sql_validator.validate_sql` parses the statement with the Snowflake dialect and rejects multiple statements, non-SELECT operations, wildcard fields, unqualified or unrecognized tables, columns outside the retrieved context, unverified joins, and missing or excessive limits. `data_source.census_data_service.query_census_data` validates again, uses the configured read-only role, applies connection and 30-second statement timeouts, and returns at most 1,000 rows.

After configuring a valid API key and all Snowflake credentials, run the manual end-to-end test:

```powershell
python -m data_source.test.test_sql_pipeline
```

To test Snowflake independently without calling GPT, configure only Snowflake and run the fixed, validated 10-row query:

```powershell
python -m data_source.test.test_snowflake_query
```

## Configuration

Before starting locally, confirm that:

- `startup/openai_key.toml` contains a valid `OPENAI_API_KEY`.
- `startup/local_config.toml` contains an `ALLOWED_USERS` list and a positive integer `CHAT_HISTORY_LOAD_LIMIT`.
- `startup/snowflake.toml` contains valid local Snowflake connection settings.

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

On a server, Streamlit Secrets take priority for `OPENAI_API_KEY`, `ALLOWED_USERS`, `CHAT_HISTORY_LOAD_LIMIT`, and the `[SNOWFLAKE]` table. Local files are used as fallbacks.

Example Streamlit Secrets section:

```toml
[SNOWFLAKE]
auth_method = "PROGRAMMATIC_ACCESS_TOKEN"
account = "YOUR_ORG-YOUR_ACCOUNT"
user = "YOUR_SNOWFLAKE_LOGIN_NAME"
token = "YOUR_OAUTH_ACCESS_TOKEN"
warehouse = "COMPUTE_WH"
role = "YOUR_READ_ONLY_ROLE"
schema = "PUBLIC"
```

## Chat history

Chat history is stored locally in `chat_history` using JSON Lines (`.jsonl`), with one complete Q&A record per line. Login loads only the latest `CHAT_HISTORY_LOAD_LIMIT` records. The directory is retained in Git through `.gitkeep`, while generated history files are ignored.

Each line uses this format:

```json
{"chat_id":"","text":"","rewritten_question":"","time_stamp":"","valid_status":0,"response":"","response_status":0,"sql":"","sql_resp":"","intent_status":"","pending_question":"","pending_clarification":""}
```

- `chat_id`: a UUID that uniquely identifies one question/answer record and prevents duplicates during timed refresh.
- `text`: the original user input displayed in the chat UI.
- `rewritten_question`: the standalone question produced by the Intent Agent and used for Agent history, Schema Retrieval, Text-to-SQL, and answer generation. Legacy rows fall back to `text`.
- `valid_status`: `0` means valid; `-1` means invalid. Invalid records keep `response` empty and the page displays `我无法回复你的问题`.
- `response_status`: `0` means normal/completed; a non-zero value means pending. Pending records currently display only the user question.
- `intent_status`: stores `INVALID`, `NEW`, or `FOLLOW_UP`, allowing history reloads to truncate the pending chain at a `NEW` boundary.
- `sql` and `sql_resp` are reserved for the Census data-source workflow.
- `pending_question` and `pending_clarification` store this user's pending-state snapshot after the turn. Login and the one-minute history refresh rebuild at most the latest five unresolved items, starting after the latest `NEW` boundary. The list is kept in the browser's Streamlit session and is not shared between users.

## Run on Windows

The startup script creates or reuses `.venv`, installs dependencies, and starts Streamlit:

```powershell
powershell -ExecutionPolicy Bypass -File .\startup\start_win.ps1
```

## Run manually

```powershell
pip install -r startup/requirements.txt
streamlit run streamlit_app.py
```
