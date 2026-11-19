"""
Summary / 文件概要:
Own the JSONL chat-history schema, validation, conversion, append-only storage,
and chat_id-based refresh merging.
负责 JSONL 聊天历史格式、校验、消息转换、追加存储，以及基于 chat_id 的刷新合并。

JSONL history format / JSONL 历史记录格式（每行一条完整问答记录）:
{
    "chat_id": "",          # Unique chat identifier / 唯一聊天标识
    "text": "",             # User input / 用户输入
    "rewritten_question": "", # Standalone model input / 改写后的独立问题
    "time_stamp": "",       # ISO 8601 UTC time / ISO 8601 UTC 时间
    "valid_status": 0,       # 0: valid / 有效; -1: invalid / 无效
    "response": "",         # Empty when invalid / 无效时为空
    "response_status": 0,    # 0: completed / 完成; non-zero: pending / 待处理
    "sql": "",              # Executed SQL / 执行的 SQL
    "sql_resp": "",         # SQL result / SQL 返回结果
    "intent_status": "",    # INVALID, NEW, or FOLLOW_UP / 本轮意图
    "resolution": "",       # NONE, NEEDS_CLARIFICATION, or RESOLVED_PENDING
    "pending_question": "", # Pending resolved question / 待补充的问题
    "pending_clarification": "" # Agent clarification / Agent 主动追问
}

Functions / 包含函数:
- generate_chat_id: Generate a unique chat ID. / 生成唯一聊天 ID。
- load_history_records: Load the latest JSONL records. / 读取末尾历史记录。
- append_history_record: Append one complete record. / 追加一条完整记录。
- build_history_record: Build a canonical record. / 构造标准记录。
- records_to_model_messages: Convert records for Agents. / 转换为 Agent 历史消息。
- records_to_display_messages: Convert records for the UI. / 转换为页面显示消息。
- get_pending_list: Restore the unresolved pending chain. / 从历史恢复未解决的待澄清链。
- merge_refreshed_records: Merge refreshed history by chat_id. / 按 chat_id 合并刷新结果。
"""

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from cold_start import get_chat_history_path


VALID_STATUS_VALID = 0
VALID_STATUS_INVALID = -1
RESPONSE_STATUS_NORMAL = 0
RESPONSE_STATUS_PENDING = 1
INVALID_RESPONSE_TEXT = "我无法回复你的问题"
MAX_PENDING_ITEMS = 5
INTENT_STATUS_NEW = "NEW"
RESOLUTION_NONE = "NONE"
RESOLUTION_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
RESOLUTION_RESOLVED_PENDING = "RESOLVED_PENDING"
VALID_RESOLUTIONS = {
    "",
    RESOLUTION_NONE,
    RESOLUTION_NEEDS_CLARIFICATION,
    RESOLUTION_RESOLVED_PENDING,
}

STRING_FIELDS = (
    "chat_id",
    "text",
    "rewritten_question",
    "time_stamp",
    "response",
    "sql",
    "sql_resp",
    "intent_status",
    "resolution",
    "pending_question",
    "pending_clarification",
)
STATUS_FIELDS = ("valid_status", "response_status")


def generate_chat_id() -> str:
    """Generate a unique chat ID. / 生成唯一聊天 ID。"""
    return str(uuid4())


def _normalize_legacy_record(
    record: dict[str, Any],
    username: str,
    line_number: int,
    raw_line: str,
) -> dict[str, Any]:
    """Add stable defaults for fields absent from old records. / 为旧记录补充稳定默认值。"""
    normalized = dict(record)
    if "chat_id" not in normalized:
        normalized["chat_id"] = str(
            uuid5(NAMESPACE_URL, f"{username}:{line_number}:{raw_line}")
        )
    normalized.setdefault("pending_question", "")
    normalized.setdefault("pending_clarification", "")
    normalized.setdefault("intent_status", "")
    # Legacy clarification rows can be identified by their saved response.
    # 旧记录没有 resolution；可通过追问回复与 pending 快照识别澄清轮次。
    legacy_resolution = ""
    if (
        normalized.get("pending_clarification")
        and normalized.get("response")
        == normalized.get("pending_clarification")
    ):
        legacy_resolution = RESOLUTION_NEEDS_CLARIFICATION
    normalized.setdefault("resolution", legacy_resolution)
    # Old JSONL rows predate question rewriting; their original text remains usable.
    # 旧 JSONL 记录没有改写字段，读取时使用原始问题作为兼容值。
    normalized.setdefault("rewritten_question", str(normalized.get("text", "")))
    return normalized


def _is_valid_record(record: object) -> bool:
    """Validate one history record. / 校验一条历史记录。"""
    if not isinstance(record, dict):
        return False
    if not all(isinstance(record.get(field), str) for field in STRING_FIELDS):
        return False
    if not record["chat_id"]:
        return False
    if not all(
        isinstance(record.get(field), int)
        and not isinstance(record.get(field), bool)
        for field in STATUS_FIELDS
    ):
        return False
    if record["valid_status"] not in (
        VALID_STATUS_VALID,
        VALID_STATUS_INVALID,
    ):
        return False
    if record["resolution"] not in VALID_RESOLUTIONS:
        return False
    if record["valid_status"] != VALID_STATUS_VALID and record["response"]:
        return False
    return True


def load_history_records(
    username: str, load_limit: int
) -> list[dict[str, Any]]:
    """Load the latest complete records. / 读取最后若干条完整问答记录。"""
    history_path = get_chat_history_path(username)
    with history_path.open("r", encoding="utf-8") as history_file:
        # Keep line numbers so legacy records receive stable derived chat IDs.
        # 保留行号，使旧记录每次刷新都能得到同一个派生 chat_id。
        latest_lines = deque(
            (
                (line_number, line.strip())
                for line_number, line in enumerate(history_file, start=1)
                if line.strip()
            ),
            maxlen=load_limit,
        )

    records: list[dict[str, Any]] = []
    for line_number, raw_line in latest_lines:
        try:
            decoded = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSONL chat history for user '{username}'."
            ) from error
        if not isinstance(decoded, dict):
            raise ValueError(
                f"Invalid chat history record for user '{username}'."
            )

        record = _normalize_legacy_record(
            decoded, username, line_number, raw_line
        )
        if not _is_valid_record(record):
            raise ValueError(
                f"Invalid chat history record for user '{username}'."
            )
        records.append(record)
    return records


def append_history_record(username: str, record: dict[str, Any]) -> None:
    """Append one complete record. / 向用户 JSONL 追加一条完整记录。"""
    if not _is_valid_record(record):
        raise ValueError("Cannot save an invalid chat history record.")

    history_path = get_chat_history_path(username)
    with history_path.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_history_record(
    chat_id: str,
    user_input: str,
    valid_status: int,
    rewritten_question: str = "",
    response: str = "",
    response_status: int = RESPONSE_STATUS_NORMAL,
    sql: str = "",
    sql_resp: str = "",
    intent_status: str = "",
    resolution: str = "",
    pending_question: str = "",
    pending_clarification: str = "",
) -> dict[str, Any]:
    """Build a canonical history record. / 构造一条标准历史记录。"""
    record = {
        "chat_id": chat_id,
        "text": user_input,
        "rewritten_question": rewritten_question or user_input,
        "time_stamp": datetime.now(timezone.utc).isoformat(),
        "valid_status": valid_status,
        "response": response,
        "response_status": response_status,
        "sql": sql,
        "sql_resp": sql_resp,
        "intent_status": intent_status,
        "resolution": resolution,
        "pending_question": pending_question,
        "pending_clarification": pending_clarification,
    }
    if not _is_valid_record(record):
        raise ValueError("Cannot build an invalid chat history record.")
    return record


def _records_to_messages(
    records: list[dict[str, Any]],
    use_rewritten_question: bool,
) -> list[dict[str, str]]:
    """Convert records with the selected user-question source. / 按指定问题字段转换消息。"""
    messages: list[dict[str, str]] = []
    for record in records:
        user_content = (
            record.get("rewritten_question") or record["text"]
            if use_rewritten_question
            else record["text"]
        )
        messages.append({"role": "user", "content": user_content})
        if record["valid_status"] != VALID_STATUS_VALID:
            messages.append(
                {"role": "assistant", "content": INVALID_RESPONSE_TEXT}
            )
        elif (
            record["response_status"] == RESPONSE_STATUS_NORMAL
            and record["response"]
        ):
            messages.append(
                {"role": "assistant", "content": record["response"]}
            )
        # Pending records intentionally display only the user input.
        # pending 记录当前只显示用户输入。
    return messages


def records_to_model_messages(
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Use rewritten questions for Agent history. / 使用改写问题生成 Agent 历史。"""
    return _records_to_messages(records, use_rewritten_question=True)


def records_to_display_messages(
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Use original questions for UI history. / 使用原始问题生成页面历史。"""
    return _records_to_messages(records, use_rewritten_question=False)


def get_pending_list(
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Restore the unresolved pending chain. / 恢复当前尚未解决的待澄清链。"""
    pending_list: list[dict[str, str]] = []

    for record in records:
        # NEW starts a separate question and immediately cuts off any older
        # clarification chain, even when the new question itself is incomplete.
        # NEW 表示新的独立问题；即使它仍需澄清，也要立即截断旧 pending 链。
        if record.get("intent_status") == INTENT_STATUS_NEW:
            pending_list.clear()

        pending_question = record.get("pending_question", "")
        pending_clarification = record.get("pending_clarification", "")
        resolution = record.get("resolution", "")

        # Invalid input and technical processing records do not change the
        # semantic clarification chain; they only carry its latest snapshot.
        # 无效输入和技术处理中记录不改变澄清链，只携带最近的状态快照。
        if (
            record.get("valid_status") != VALID_STATUS_VALID
            or record.get("response_status") == RESPONSE_STATUS_PENDING
        ):
            continue

        if resolution == RESOLUTION_NEEDS_CLARIFICATION:
            if pending_question and pending_clarification:
                pending_list.append(
                    {
                        "chat_id": record["chat_id"],
                        "text": record["text"],
                        "time_stamp": record["time_stamp"],
                        "pending_question": pending_question,
                        "pending_clarification": pending_clarification,
                    }
                )
                del pending_list[:-MAX_PENDING_ITEMS]
            continue

        if resolution == RESOLUTION_RESOLVED_PENDING:
            pending_list.clear()
            continue

        if resolution == RESOLUTION_NONE:
            # A normal completed NEW/FOLLOW_UP turn either had no pending
            # context or replaced it, so no clarification remains active.
            # 正常完成的一轮没有未解决澄清，或已经替换原澄清链。
            pending_list.clear()
            continue

        # Legacy rows without resolution retain the previous inference rules.
        # 没有 resolution 的旧记录继续使用快照规则兼容读取。
        if not pending_question and not pending_clarification:
            pending_list.clear()
            continue

        # A real clarification turn stores the Agent's question both as response
        # and pending_clarification. Invalid or failed records only repeat the
        # snapshot, so they must not create an extra in-memory pending item.
        # 真正的澄清轮次会把 Agent 追问同时写入 response 和 pending_clarification；
        # 无效或失败记录只携带快照，不应在内存列表中重复追加。
        if record.get("response") == pending_clarification:
            pending_list.append(
                {
                    "chat_id": record["chat_id"],
                    "text": record["text"],
                    "time_stamp": record["time_stamp"],
                    "pending_question": pending_question,
                    "pending_clarification": pending_clarification,
                }
            )
            del pending_list[:-MAX_PENDING_ITEMS]

    if not records:
        return pending_list

    # A configured tail limit may start after the original clarification turn.
    # Preserve at least the latest snapshot so the conversation can continue.
    # 当历史读取条数截断了最初的澄清轮次时，至少保留最后一条状态快照。
    latest_record = records[-1]
    latest_question = latest_record.get("pending_question", "")
    latest_clarification = latest_record.get("pending_clarification", "")
    latest_pair = (latest_question, latest_clarification)
    restored_pair = (
        (
            pending_list[-1]["pending_question"],
            pending_list[-1]["pending_clarification"],
        )
        if pending_list
        else ("", "")
    )
    if latest_pair != ("", "") and latest_pair != restored_pair:
        pending_list.append(
            {
                "chat_id": latest_record["chat_id"],
                "text": latest_record["text"],
                "time_stamp": latest_record["time_stamp"],
                "pending_question": latest_question,
                "pending_clarification": latest_clarification,
            }
        )
    return pending_list[-MAX_PENDING_ITEMS:]


def merge_refreshed_records(
    refreshed_records: list[dict[str, Any]],
    current_records: list[dict[str, Any]],
    active_chat_ids: set[str],
) -> list[dict[str, Any]]:
    """Merge disk refresh with active chats by chat_id. / 按 chat_id 合并刷新与进行中聊天。"""
    merged = list(refreshed_records)
    refreshed_ids = {record["chat_id"] for record in refreshed_records}

    # Only active in-memory records may be absent from disk. Completed records
    # always follow the refreshed file so the configured tail limit still applies.
    # 只有进行中的内存记录可以暂未落盘；已完成记录以刷新后的文件为准。
    for record in current_records:
        chat_id = record["chat_id"]
        if chat_id in active_chat_ids and chat_id not in refreshed_ids:
            merged.append(record)
            refreshed_ids.add(chat_id)
    return merged
