"""
Summary / 文件概要:
Create the Google Gemini client, expose the shared model, and convert stored
chat messages into Gemini content objects.
创建 Google Gemini 客户端、统一模型名称，并将聊天记录转换为 Gemini 内容对象。

Functions / 包含函数:
- create_gemini_client: Create the bounded Gemini client. / 创建带超时的 Gemini 客户端。
- to_gemini_contents: Convert role/content messages. / 转换角色与内容消息。
"""

from google import genai
from google.genai import types


GEMINI_MODEL_NAME = "gemini-3.8-flash"


def create_gemini_client(api_key: str) -> genai.Client:
    """Create the shared Gemini client. / 创建项目共用的 Gemini 客户端。"""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("GEMINI_API_KEY must be a non-empty string.")
    return genai.Client(
        api_key=api_key.strip(),
        http_options=types.HttpOptions(timeout=30_000),
    )


def to_gemini_contents(
    messages: list[dict[str, str]],
) -> list[types.Content]:
    """Convert chat history to Gemini roles. / 将聊天历史转换为 Gemini 角色。"""
    contents: list[types.Content] = []
    for message in messages:
        role = message.get("role")
        text = message.get("content", "")
        if role not in {"user", "assistant"} or not isinstance(text, str):
            continue
        if not text.strip():
            continue
        contents.append(
            types.Content(
                role="model" if role == "assistant" else "user",
                parts=[types.Part.from_text(text=text)],
            )
        )
    return contents
