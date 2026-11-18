# Census AI | US Population Insights

Explore US Census data through natural language with multi-agent intelligence.

## Project structure

```text
streamlit_app.py       Minimal Streamlit entry point
cold_start.py          Configuration loading, validation, and user-file checks
page.py                Login/chat pages, JSONL history, and data-source integration
chat.py                Intent detection and answer-generation workflow
startup/
  local_config.toml    Local fallback application settings
  openai_key.toml      Local OpenAI API key
  requirements.txt     Python dependencies
  start_win.ps1        Windows startup script
chat_history/          Local per-user JSONL chat histories
```

Each Python module starts with a bilingual summary and a list of the functions it contains.

## Application flow

1. `streamlit_app.py` runs the cold-start gate.
2. `cold_start.py` loads and validates all configuration, creates the chat-history directory, and ensures every allowed user has a JSONL file.
3. `page.py` handles login, loads the latest complete Q&A records, converts them into chat messages, and calls the chat workflow with the page-level data-source loader.
4. `chat.py` detects the current conversation intent and then generates the answer using the available history and data-source context.

`page.load_data_input` is currently the extension point for a future Census data-source implementation. Answer generation explicitly receives both `user_input` and `data_input`. Until a data source is connected, Census answers are instructed not to invent precise statistics.

## Configuration

Before starting locally, confirm that:

- `startup/openai_key.toml` contains a valid `OPENAI_API_KEY`.
- `startup/local_config.toml` contains an `ALLOWED_USERS` list and a positive integer `CHAT_HISTORY_LOAD_LIMIT`.

Example local configuration:

```toml
ALLOWED_USERS = ["user"]
CHAT_HISTORY_LOAD_LIMIT = 50
```

On a server, Streamlit Secrets take priority for `OPENAI_API_KEY`, `ALLOWED_USERS`, and `CHAT_HISTORY_LOAD_LIMIT`. Local files are used as fallbacks.

## Chat history

Chat history is stored locally in `chat_history` using JSON Lines (`.jsonl`), with one complete Q&A record per line. Login loads only the latest `CHAT_HISTORY_LOAD_LIMIT` records. The directory is retained in Git through `.gitkeep`, while generated history files are ignored.

Each line uses this format:

```json
{"text":"","time_stamp":"","valid_status":0,"response":"","response_status":0,"sql":"","sql_resp":""}
```

- `valid_status`: `0` means valid; `-1` means invalid. Invalid records keep `response` empty and the page displays `我无法回复你的问题`.
- `response_status`: `0` means normal/completed; a non-zero value means pending. Pending records currently display only the user question.
- `sql` and `sql_resp` are reserved for the Census data-source workflow.

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
