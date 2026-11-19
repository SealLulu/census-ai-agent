"""
Summary / 文件概要:
Generate the final streamed natural-language answer from the standalone user
question, optional conversation history, and verified Snowflake data.
根据独立用户问题、可选对话历史和已验证的 Snowflake 数据生成最终流式回答。

Functions / 包含函数:
- generate_answer: Generate the final answer stream. / 生成最终回答流。
"""

from typing import Any

from openai import OpenAI


MODEL_NAME = "gpt-4o-mini"


def generate_answer(
    client: OpenAI,
    history_messages: list[dict[str, str]],
    user_input: str,
    data_input: str | None,
) -> Any:
    """Generate from user and verified data input. / 根据用户问题和已验证数据生成回答。"""
    system_content = "You are Census AI Assistant. Answer clearly and concisely."
    if data_input:
        # Snowflake output is the factual basis for data-related answers.
        # Snowflake 输出是数据类回答的事实依据。
        system_content += (
            " Use the following data-source context as the factual basis for the "
            f"answer:\n{data_input}"
        )
    else:
        system_content += (
            " No external Census data source returned context. Be transparent "
            "about that limitation and do not invent precise statistics."
        )

    return client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_content},
            *history_messages,
            {"role": "user", "content": user_input},
        ],
        stream=True,
    )
