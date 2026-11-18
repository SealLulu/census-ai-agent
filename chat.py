"""
Summary / 文件概要:
Implement the main chat pipeline: inspect the current user intent, obtain optional
data-source context through a caller-provided loader, and generate the answer.
实现聊天主流程：检查当前用户意图，通过调用方提供的加载器取得可选数据源上下文，
然后生成回答。

Functions / 包含函数:
- detect_conversation_intent: Classify the current input. / 判断当前输入的对话意图。
- generate_answer: Generate a streamed answer. / 生成流式回答。
- run_chat_flow: Run intent detection and answer generation. / 执行意图检查与答案生成主流程。
"""

from collections.abc import Callable
from typing import Any

from openai import OpenAI


MODEL_NAME = "gpt-4o-mini"
INTENT_CENSUS_DATA = "census_data"
INTENT_GENERAL = "general_conversation"


def detect_conversation_intent(client: OpenAI, user_input: str) -> str:
    """Classify the current input. / 判断当前输入的对话意图。"""
    # Ask for a strict label so downstream routing does not parse free-form text.
    # 要求模型只返回固定标签，避免下游路由解析自然语言描述。
    completion = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify the user's latest message into exactly one label: "
                    "census_data for US Census, population, demographic, housing, "
                    "or related data questions; general_conversation for every "
                    "other message. Return only the label."
                ),
            },
            {"role": "user", "content": user_input},
        ],
    )

    intent = (completion.choices[0].message.content or "").strip().lower()
    if intent == INTENT_CENSUS_DATA:
        return INTENT_CENSUS_DATA
    # Unknown or malformed labels safely fall back to general conversation.
    # 未知或格式错误的标签统一安全回退为普通对话。
    return INTENT_GENERAL


def generate_answer(
    client: OpenAI,
    history_messages: list[dict[str, str]],
    user_input: str,
    data_input: str | None,
) -> Any:
    """Generate from user and data input. / 根据用户输入与数据输入生成回答。"""
    system_content = (
        "You are Census AI Assistant. Answer clearly and concisely."
    )
    if data_input:
        # Retrieved context becomes the factual basis for data-related answers.
        # 查询到的数据上下文作为数据类回答的事实依据。
        system_content += (
            " Use the following data-source context as the factual basis for the "
            f"answer:\n{data_input}"
        )
    else:
        # Explicitly limit the model when no verified Census context is available.
        # 没有可信 Census 数据上下文时，明确限制模型不得编造精确数据。
        system_content += (
            " No external Census data source returned context. Be transparent "
            "about that limitation and do not invent precise statistics."
        )

    # Streaming lets the page display the answer incrementally.
    # 流式输出使页面可以逐步显示生成内容。
    return client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_content},
            *history_messages,
            {"role": "user", "content": user_input},
        ],
        stream=True,
    )


def run_chat_flow(
    client: OpenAI,
    user_input: str,
    history_messages: list[dict[str, str]],
    data_input_loader: Callable[[str], str | None],
) -> tuple[bool, Any]:
    """Run intent check and answer generation. / 执行意图检查与答案生成。"""
    # Step 1: inspect the current input intent.
    # 第一步：检查当前输入的对话意图。
    intent = detect_conversation_intent(client, user_input)

    # Non-Census input is marked invalid and does not enter answer generation.
    # 非 Census 输入被视为无效意图，不进入答案生成阶段。
    if intent != INTENT_CENSUS_DATA:
        return False, None

    # The page layer owns the data-source integration and provides its loader.
    # 页面层负责数据源集成，并向聊天流程提供加载函数。
    data_input = data_input_loader(user_input)

    # Step 2: generate an answer from history, user input, and data input.
    # 第二步：结合历史消息、用户输入和数据输入生成回答。
    stream = generate_answer(
        client=client,
        history_messages=history_messages,
        user_input=user_input,
        data_input=data_input,
    )
    return True, stream
