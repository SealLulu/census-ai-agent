"""
Summary / 文件概要:
Create the Google Gemini client, expose the shared model, and convert stored
chat messages into Gemini content objects.
创建 Google Gemini 客户端、统一模型名称，并将聊天记录转换为 Gemini 内容对象。

Functions / 包含函数:
- create_gemini_client: Create the bounded Gemini client. / 创建带超时的 Gemini 客户端。
- to_gemini_contents: Convert role/content messages. / 转换角色与内容消息。
"""

import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from google import genai
from google.genai import errors, types


GEMINI_MODEL_NAME = "gemini-3.5-flash-lite"
GEMINI_FALLBACK_MODEL_NAME = "gemini-3.6-flash"
GEMINI_MODEL_CANDIDATES = (GEMINI_MODEL_NAME, GEMINI_FALLBACK_MODEL_NAME)
GEMINI_REQUEST_TIMEOUT_MS = 15_000
MAX_QUOTA_RETRY_SECONDS = 15.0
TRANSIENT_RETRY_DELAY_SECONDS = 2.0
FALLBACK_STATUS_CODES = {404, 408, 429, 500, 502, 503, 504}
TRANSIENT_RETRY_STATUS_CODES = {408, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


class LLMServiceError(RuntimeError):
    """Base error for a handled Gemini service failure. / Gemini 服务异常基类。"""


class LLMRateLimitError(LLMServiceError):
    """Gemini quota or rate limit was reached. / Gemini 额度或频率限制已达到。"""


class LLMUnavailableError(LLMServiceError):
    """Gemini is temporarily unavailable or overloaded. / Gemini 暂时不可用或繁忙。"""


def create_gemini_client(api_key: str) -> genai.Client:
    """Create the shared Gemini client. / 创建项目共用的 Gemini 客户端。"""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("GEMINI_API_KEY must be a non-empty string.")
    return genai.Client(
        api_key=api_key.strip(),
        http_options=types.HttpOptions(
            timeout=GEMINI_REQUEST_TIMEOUT_MS,
            # Bound transient-service retries; quota errors fail immediately.
            # 限制瞬时服务故障的重试次数；额度错误直接快速失败。
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _can_fallback(error: Exception) -> bool:
    """Check whether another model may recover. / 判断备用模型是否可能恢复调用。"""
    if isinstance(error, errors.APIError):
        try:
            return int(error.code) in FALLBACK_STATUS_CODES
        except (TypeError, ValueError):
            return False
    return type(error).__name__ in {
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
    }


def _quota_retry_delay(error: Exception) -> float | None:
    """Read Gemini RetryInfo for a bounded 429 retry. / 读取 429 的有限重试时间。"""
    if not isinstance(error, errors.APIError):
        return None
    try:
        if int(error.code) != 429:
            return None
    except (TypeError, ValueError):
        return None

    details = error.details if isinstance(error.details, dict) else {}
    payload = details.get("error", details)
    retry_details = payload.get("details", []) if isinstance(payload, dict) else []
    for detail in retry_details:
        if not isinstance(detail, dict):
            continue
        if not str(detail.get("@type", "")).endswith("RetryInfo"):
            continue
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", str(detail.get("retryDelay", "")))
        if match:
            delay = float(match.group(1))
            if 0 < delay <= MAX_QUOTA_RETRY_SECONDS:
                return delay
    return None


def _is_transient_service_error(error: Exception) -> bool:
    """Identify a temporary API/server failure. / 识别临时 API 或服务端故障。"""
    if isinstance(error, errors.APIError):
        try:
            return int(error.code) in TRANSIENT_RETRY_STATUS_CODES
        except (TypeError, ValueError):
            return False
    return type(error).__name__ in {
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
    }


def _raise_classified_service_error(error: Exception) -> None:
    """Convert expected Gemini failures into UI-safe types. / 转换为页面可安全处理的异常。"""
    if isinstance(error, errors.APIError):
        try:
            status_code = int(error.code)
        except (TypeError, ValueError):
            status_code = 0
        if status_code == 429:
            raise LLMRateLimitError(
                "The Gemini usage limit was reached."
            ) from error
        if status_code in TRANSIENT_RETRY_STATUS_CODES:
            raise LLMUnavailableError(
                "The Gemini service is temporarily unavailable."
            ) from error
    if type(error).__name__ in {
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
    }:
        raise LLMUnavailableError(
            "The Gemini service request timed out."
        ) from error
    raise error


def generate_content_with_fallback(
    client: genai.Client,
    *,
    contents: Any,
    config: types.GenerateContentConfig,
) -> Any:
    """Generate a complete response with fallback. / 使用主模型和备用模型生成完整响应。"""
    for index, model_name in enumerate(GEMINI_MODEL_CANDIDATES):
        quota_retried = False
        transient_retried = False
        while True:
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as error:
                retry_delay = _quota_retry_delay(error)
                if retry_delay is not None and not quota_retried:
                    quota_retried = True
                    LOGGER.warning(
                        "Gemini model %s reached a temporary quota limit; "
                        "retrying once in %.1f seconds.",
                        model_name,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
                is_last = index == len(GEMINI_MODEL_CANDIDATES) - 1
                if (
                    is_last
                    and not transient_retried
                    and _is_transient_service_error(error)
                ):
                    transient_retried = True
                    LOGGER.warning(
                        "Gemini fallback model %s failed temporarily; "
                        "retrying once in %.1f seconds.",
                        model_name,
                        TRANSIENT_RETRY_DELAY_SECONDS,
                    )
                    time.sleep(TRANSIENT_RETRY_DELAY_SECONDS)
                    continue
                if is_last or not _can_fallback(error):
                    _raise_classified_service_error(error)
                LOGGER.warning(
                    "Gemini model %s failed temporarily; using fallback model.",
                    model_name,
                )
                break
    raise RuntimeError("No Gemini model candidate was available.")


def stream_content_with_fallback(
    client: genai.Client,
    *,
    contents: Any,
    config: types.GenerateContentConfig,
) -> Iterator[str]:
    """Fallback only before the first streamed chunk. / 仅在首个流式分片前切换模型。"""
    for index, model_name in enumerate(GEMINI_MODEL_CANDIDATES):
        emitted = False
        quota_retried = False
        transient_retried = False
        while True:
            try:
                for chunk in client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=config,
                ):
                    if chunk.text:
                        emitted = True
                        yield chunk.text
                return
            except Exception as error:
                retry_delay = _quota_retry_delay(error)
                if (
                    not emitted
                    and retry_delay is not None
                    and not quota_retried
                ):
                    quota_retried = True
                    LOGGER.warning(
                        "Gemini stream %s reached a temporary quota limit; "
                        "retrying once in %.1f seconds.",
                        model_name,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
                is_last = index == len(GEMINI_MODEL_CANDIDATES) - 1
                if (
                    not emitted
                    and is_last
                    and not transient_retried
                    and _is_transient_service_error(error)
                ):
                    transient_retried = True
                    LOGGER.warning(
                        "Gemini fallback stream %s failed temporarily; "
                        "retrying once in %.1f seconds.",
                        model_name,
                        TRANSIENT_RETRY_DELAY_SECONDS,
                    )
                    time.sleep(TRANSIENT_RETRY_DELAY_SECONDS)
                    continue
                if emitted or is_last or not _can_fallback(error):
                    _raise_classified_service_error(error)
                LOGGER.warning(
                    "Gemini stream %s failed before output; using fallback model.",
                    model_name,
                )
                break
    raise RuntimeError("No Gemini streaming model candidate was available.")


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
