"""
Summary / 文件概要:
Load and validate all startup configuration, prepare the chat-history directory,
and ensure every allowed user has a local JSONL history file.
加载并校验全部启动配置，准备聊天记录目录，并确保每个允许用户都有本地
JSONL 聊天记录文件。

Functions / 包含函数:
- get_chat_history_path: Build a safe per-user JSONL path. / 生成安全的用户文件路径。
- get_legacy_chat_history_path: Locate the old JSON file. / 定位旧版 JSON 文件。
- load_local_config: Load local fallback settings. / 读取本地备用配置。
- load_allowed_users: Load the user allowlist. / 读取允许用户列表。
- load_chat_history_limit: Load the history tail limit. / 读取历史记录加载数量。
- load_gemini_api_key: Load the Gemini API key. / 读取 Gemini API Key。
- load_snowflake_config: Load Snowflake settings. / 读取 Snowflake 配置。
- load_config: Validate and assemble AppConfig. / 校验并组装 AppConfig。
- ensure_chat_storage: Create or migrate user history files. / 创建或迁移用户记录文件。
- initialize_application: Run the complete cold-start sequence. / 执行完整冷启动流程。
"""

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
STARTUP_DIR = BASE_DIR / "startup"
CHAT_HISTORY_DIR = BASE_DIR / "chat_history"


@dataclass(frozen=True)
class SnowflakeConfig:
    """Validated Snowflake settings. / 已完成校验的 Snowflake 配置。"""

    account: str
    user: str
    warehouse: str
    schema: str
    auth_method: str = "password"
    password: str | None = None
    token: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class AppConfig:
    """Validated application settings. / 已完成校验的应用配置。"""

    allowed_users: list[str]
    gemini_api_key: str
    chat_history_load_limit: int
    snowflake: SnowflakeConfig


def get_chat_history_path(username: str) -> Path:
    """Return a safe per-user JSONL path. / 返回安全的用户 JSONL 路径。"""
    # URL encoding prevents path separators in usernames from changing folders.
    # URL 编码可防止用户名中的路径分隔符改变目标目录。
    safe_username = quote(username, safe="")
    return CHAT_HISTORY_DIR / f"user_{safe_username}.jsonl"


def get_legacy_chat_history_path(username: str) -> Path:
    """Return the previous JSON path. / 返回旧版 JSON 聊天记录路径。"""
    safe_username = quote(username, safe="")
    return CHAT_HISTORY_DIR / f"user_{safe_username}.json"


def _is_valid_chat_message(message: object) -> bool:
    """Check one stored message. / 检查一条已保存消息的结构。"""
    return (
        isinstance(message, dict)
        and isinstance(message.get("role"), str)
        and isinstance(message.get("content"), str)
    )


def _legacy_messages_to_records(
    messages: list[dict[str, str]],
) -> list[dict[str, object]]:
    """Convert legacy role/content messages. / 转换旧版 role/content 消息。"""
    records: list[dict[str, object]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message["role"] != "user":
            index += 1
            continue

        response = ""
        response_status = 1
        if (
            index + 1 < len(messages)
            and messages[index + 1]["role"] == "assistant"
        ):
            response = messages[index + 1]["content"]
            response_status = 0
            index += 1

        # Legacy files have no timestamp, SQL, or validity metadata.
        # 旧文件不含时间、SQL 或有效性信息，迁移时使用兼容默认值。
        records.append(
            {
                "chat_id": str(uuid4()),
                "text": message["content"],
                "rewritten_question": message["content"],
                "time_stamp": "",
                "valid_status": 0,
                "response": response,
                "response_status": response_status,
                "sql": "",
                "sql_resp": "",
                "intent_status": "",
                "pending_question": "",
                "pending_clarification": "",
            }
        )
        index += 1
    return records


def _write_jsonl_history(
    history_path: Path, records: list[dict[str, object]]
) -> None:
    """Write Q&A records as JSON Lines. / 以 JSON Lines 格式写入问答记录。"""
    content = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    history_path.write_text(content, encoding="utf-8")


def load_local_config() -> dict:
    """Load local fallback settings. / 读取本地备用配置。"""
    local_config_path = STARTUP_DIR / "local_config.toml"
    try:
        with local_config_path.open("rb") as local_config_file:
            return tomllib.load(local_config_file)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Invalid TOML file: {local_config_path}") from error


def load_allowed_users() -> list[str]:
    """Load allowed users, preferring the server. / 优先读取服务器用户列表。"""
    # Production Streamlit Secrets override the local development configuration.
    # 生产环境的 Streamlit Secrets 优先于本地开发配置。
    try:
        allowed_users = st.secrets["ALLOWED_USERS"]
    except Exception:
        allowed_users = None

    if allowed_users is None:
        allowed_users = load_local_config().get("ALLOWED_USERS")

    if not isinstance(allowed_users, list) or not all(
        isinstance(username, str) for username in allowed_users
    ):
        raise ValueError("ALLOWED_USERS must be configured as a list of strings.")

    return [username.strip() for username in allowed_users if username.strip()]


def load_chat_history_limit() -> int:
    """Load the history tail limit. / 读取聊天记录末尾加载数量。"""
    # Use the same server-first fallback policy as the allowed-user list.
    # 与允许用户列表保持一致，采用服务器优先、本地备用的策略。
    try:
        load_limit = st.secrets["CHAT_HISTORY_LOAD_LIMIT"]
    except Exception:
        load_limit = None

    if load_limit is None:
        load_limit = load_local_config().get("CHAT_HISTORY_LOAD_LIMIT")

    if isinstance(load_limit, bool) or not isinstance(load_limit, int):
        raise ValueError("CHAT_HISTORY_LOAD_LIMIT must be an integer.")
    if load_limit <= 0:
        raise ValueError("CHAT_HISTORY_LOAD_LIMIT must be greater than zero.")

    return load_limit


def load_gemini_api_key() -> str | None:
    """Load the Gemini API key in priority order. / 按优先级读取 Gemini API Key。"""
    # Priority: server secret -> local key file -> environment variable.
    # 优先级：服务器 Secret -> 本地密钥文件 -> 环境变量。
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        api_key = None

    if not api_key:
        local_key_path = STARTUP_DIR / "gemini_key.toml"
        try:
            with local_key_path.open("rb") as local_key_file:
                api_key = tomllib.load(local_key_file).get("GEMINI_API_KEY")
        except FileNotFoundError:
            api_key = None
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"Invalid TOML file: {local_key_path}") from error

    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError("GEMINI_API_KEY must be a string.")

    return api_key.strip() if api_key else None


def load_snowflake_config() -> SnowflakeConfig:
    """Load Snowflake settings, preferring server secrets. / 优先读取服务器配置。"""
    local_path = STARTUP_DIR / "snowflake.toml"
    try:
        with local_path.open("rb") as local_file:
            local_section = tomllib.load(local_file).get("SNOWFLAKE", {})
    except FileNotFoundError:
        local_section = {}
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Invalid TOML file: {local_path}") from error
    if not isinstance(local_section, dict):
        raise ValueError("Local SNOWFLAKE configuration must be a TOML table.")

    try:
        server_section = st.secrets["SNOWFLAKE"]
    except Exception:
        server_section = {}

    def read_value(field: str) -> object:
        """Read one server-first Snowflake setting. / 读取一个服务器优先的 Snowflake 配置。"""
        try:
            return server_section[field]
        except Exception:
            return local_section.get(field)

    required_fields = ("account", "user", "warehouse", "schema")
    values: dict[str, str | None] = {}
    for field in required_fields:
        # Each server value overrides its local counterpart independently.
        # 每个服务器字段分别覆盖对应的本地配置，未配置项仍可使用本地值。
        value = read_value(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"SNOWFLAKE.{field} must be configured as a non-empty string."
            )
        values[field] = value.strip()

    auth_method_value = read_value("auth_method") or "password"
    if not isinstance(auth_method_value, str):
        raise ValueError("SNOWFLAKE.auth_method must be a string.")
    auth_method = auth_method_value.strip().lower()
    if auth_method not in {"password", "oauth", "programmatic_access_token"}:
        raise ValueError(
            "SNOWFLAKE.auth_method must be 'password', 'oauth', or "
            "'PROGRAMMATIC_ACCESS_TOKEN'."
        )
    values["auth_method"] = auth_method

    # Only the credential required by the selected authentication mode is mandatory.
    # 仅强制要求当前认证模式使用的凭据，避免同时保存密码和 OAuth token。
    credential_field = (
        "token"
        if auth_method in {"oauth", "programmatic_access_token"}
        else "password"
    )
    credential = read_value(credential_field)
    if not isinstance(credential, str) or not credential.strip():
        raise ValueError(
            f"SNOWFLAKE.{credential_field} must be configured as a non-empty string "
            f"when auth_method is '{auth_method}'."
        )
    values[credential_field] = credential.strip()
    values[
        "password"
        if auth_method in {"oauth", "programmatic_access_token"}
        else "token"
    ] = None

    role = read_value("role")
    if role is not None and not isinstance(role, str):
        raise ValueError("SNOWFLAKE.role must be a string when configured.")
    values["role"] = role.strip() if isinstance(role, str) and role.strip() else None

    return SnowflakeConfig(**values)


def load_config() -> AppConfig:
    """Load and validate all startup settings. / 加载并校验全部启动配置。"""
    # Build one immutable config object only after every required value is valid.
    # 只有全部必需配置均有效后，才创建统一且不可变的配置对象。
    allowed_users = load_allowed_users()
    if not allowed_users:
        raise ValueError("ALLOWED_USERS must contain at least one username.")

    chat_history_load_limit = load_chat_history_limit()
    gemini_api_key = load_gemini_api_key()
    if not gemini_api_key:
        raise ValueError(
            "GEMINI_API_KEY was not found in Streamlit Secrets, "
            "startup/gemini_key.toml, or the local environment."
        )
    snowflake_config = load_snowflake_config()

    return AppConfig(
        allowed_users=allowed_users,
        gemini_api_key=gemini_api_key,
        chat_history_load_limit=chat_history_load_limit,
        snowflake=snowflake_config,
    )


def ensure_chat_storage(allowed_users: list[str]) -> None:
    """Create or migrate user history files. / 创建或迁移用户聊天记录文件。"""
    CHAT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    for username in allowed_users:
        history_path = get_chat_history_path(username)
        if history_path.exists():
            # Existing JSONL files are preserved; startup never truncates history.
            # 已存在的 JSONL 文件会被保留，冷启动绝不会截断历史记录。
            if not history_path.is_file():
                raise OSError(f"Chat history path is not a file: {history_path}")
            continue

        legacy_path = get_legacy_chat_history_path(username)
        if legacy_path.is_file():
            # Migration runs only when JSONL is absent, preventing duplicate imports.
            # 仅在 JSONL 不存在时执行迁移，避免重复导入旧记录。
            try:
                legacy_messages = json.loads(
                    legacy_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid legacy chat history for user '{username}'."
                ) from error

            if not isinstance(legacy_messages, list) or not all(
                _is_valid_chat_message(message) for message in legacy_messages
            ):
                raise ValueError(
                    f"Invalid legacy chat history format for user '{username}'."
                )

            records = _legacy_messages_to_records(legacy_messages)
            _write_jsonl_history(history_path, records)
            legacy_path.unlink()
            continue

        # Missing users receive an empty file that later supports append-only writes.
        # 缺失用户会获得空文件，后续消息可直接以追加模式写入。
        history_path.touch()


def initialize_application() -> AppConfig:
    """Run the complete cold-start sequence. / 执行完整的应用冷启动流程。"""
    # Configuration and required files must be ready before any page is rendered.
    # 所有配置与必需文件准备完成后，页面流程才可以继续运行。
    config = load_config()
    ensure_chat_storage(config.allowed_users)
    return config
