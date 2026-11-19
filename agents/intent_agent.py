"""
Summary / 文件概要:
Classify Census conversation intent, resolve pending clarification context, and
rewrite the current input into a standalone question. This agent never creates SQL.
识别 Census 对话意图、处理待澄清上下文，并把当前输入重写为独立问题；本 Agent 不生成 SQL。

Functions / 包含函数:
- detect_conversation_intent: Resolve intent and clarification state. / 识别意图与澄清状态。
"""

import json
from dataclasses import dataclass

from openai import OpenAI


MODEL_NAME = "gpt-4o-mini"
STATUS_INVALID = "INVALID"
STATUS_NEW = "NEW"
STATUS_FOLLOW_UP = "FOLLOW_UP"
VALID_STATUSES = {STATUS_INVALID, STATUS_NEW, STATUS_FOLLOW_UP}
RESOLUTION_NONE = "NONE"
RESOLUTION_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
RESOLUTION_RESOLVED_PENDING = "RESOLVED_PENDING"
VALID_RESOLUTIONS = {
    RESOLUTION_NONE,
    RESOLUTION_NEEDS_CLARIFICATION,
    RESOLUTION_RESOLVED_PENDING,
}


@dataclass(frozen=True)
class IntentResult:
    """Resolved conversation intent. / 已解析的对话意图。"""

    status: str
    resolution: str
    standalone_question: str
    response_text: str

    @property
    def is_valid(self) -> bool:
        """Return whether processing may continue. / 返回是否可以继续处理。"""
        return self.status != STATUS_INVALID


def detect_conversation_intent(
    client: OpenAI,
    history_messages: list[dict[str, str]],
    pending_list: list[dict[str, str]],
    user_input: str,
) -> IntentResult:
    """Resolve intent using history and pending state. / 使用历史与待澄清状态识别意图。"""
    pending_context = json.dumps(pending_list, ensure_ascii=False)
    completion = client.chat.completions.create(
        model=MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Return one JSON object with exactly four fields: "
                    "{\"status\": \"INVALID|NEW|FOLLOW_UP\", "
                    "\"resolution\": "
                    "\"NONE|NEEDS_CLARIFICATION|RESOLVED_PENDING\", "
                    "\"standalone_question\": \"\", "
                    "\"response_text\": \"\"}. "
                    "INVALID is off-topic. NEW is a self-contained Census "
                    "question. FOLLOW_UP depends on conversation history. "
                    "Pending context is a chronological list of unresolved "
                    "clarification turns. Use the entire pending chain before "
                    "ordinary history. If required information is missing, use "
                    "NEEDS_CLARIFICATION, put the best currently resolved question "
                    "in standalone_question, and put one concise clarification "
                    "question in response_text. If the user resolves pending "
                    "context, use RESOLVED_PENDING. If the user starts a new "
                    "question, use NEW and NONE. For every non-clarification "
                    "result, response_text must be empty. For FOLLOW_UP, always "
                    "rewrite standalone_question as a complete self-contained "
                    "question by inheriting relevant metrics, years, geography, "
                    "and filters from history. Explicit constraints in the current "
                    "input override historical constraints. For example, after "
                    "'What is the population of California?' the input 'Texas?' "
                    "must become 'What is the population of Texas?'. Never return "
                    "an unresolved fragment as standalone_question; request "
                    "clarification instead. Do not generate SQL and do not answer "
                    "the question."
                ),
            },
            {"role": "system", "content": f"Pending context: {pending_context}"},
            *history_messages,
            {"role": "user", "content": user_input},
        ],
    )

    content = completion.choices[0].message.content or ""
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return IntentResult(STATUS_INVALID, RESOLUTION_NONE, "", "")

    status = str(result.get("status", "")).strip().upper()
    resolution = str(result.get("resolution", "")).strip().upper()
    standalone_question = result.get("standalone_question", "")
    response_text = result.get("response_text", "")
    if (
        status not in VALID_STATUSES
        or resolution not in VALID_RESOLUTIONS
        or not isinstance(standalone_question, str)
        or not isinstance(response_text, str)
    ):
        return IntentResult(STATUS_INVALID, RESOLUTION_NONE, "", "")
    if status == STATUS_INVALID:
        return IntentResult(STATUS_INVALID, RESOLUTION_NONE, "", "")

    resolved_question = standalone_question.strip() or user_input.strip()
    if resolution == RESOLUTION_NEEDS_CLARIFICATION:
        clarification = response_text.strip()
        if not clarification:
            clarification = "Could you provide the missing Census query details?"
        return IntentResult(
            status,
            RESOLUTION_NEEDS_CLARIFICATION,
            resolved_question,
            clarification,
        )
    return IntentResult(status, resolution, resolved_question, "")
