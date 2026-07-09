from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .pipeline import Extractor


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 120

MODEL_ENV_VAR = "CHATNOTE_MODEL"
API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
BASE_URL_ENV_VAR = "CHATNOTE_LLM_BASE_URL"

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class LLMExtractorError(RuntimeError):
    """Raised when the model-backed extractor cannot be configured or called."""


@dataclass(frozen=True, slots=True)
class OpenRouterConfig:
    model: str
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def load_openrouter_config(
    *, model: str | None = None, env: Mapping[str, str] | None = None
) -> OpenRouterConfig:
    """Resolve extractor config from the --model flag and environment.

    The model has no default slug: a guessed model fails confusingly at the
    API, so a missing model is a configuration error instead.
    """
    if env is None:
        env = os.environ
    resolved_model = model or env.get(MODEL_ENV_VAR)
    if not resolved_model:
        raise LLMExtractorError(
            "No extraction model configured. Pass --model or set the "
            f"{MODEL_ENV_VAR} environment variable to an OpenRouter model "
            "slug (e.g. anthropic/claude-sonnet-5)."
        )
    api_key = env.get(API_KEY_ENV_VAR)
    if not api_key:
        raise LLMExtractorError(
            f"{API_KEY_ENV_VAR} is not set. Create a key at "
            "https://openrouter.ai/keys and export it before extracting."
        )
    base_url = env.get(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL
    return OpenRouterConfig(
        model=resolved_model,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
    )


def build_openrouter_extractor(
    config: OpenRouterConfig, *, transport: Transport | None = None
) -> Extractor:
    """Return a pipeline extractor that calls an OpenAI-compatible chat API.

    `transport` maps one request dict to the decoded response payload; tests
    inject it to run offline, and the default POSTs to
    `{base_url}/chat/completions` via urllib.
    """
    send = transport or _urllib_transport

    def extractor(prompt: str) -> str:
        response = send(
            {
                "url": f"{config.base_url}/chat/completions",
                "headers": {
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                "body": {
                    "model": config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": config.max_output_tokens,
                },
                "timeout_seconds": config.timeout_seconds,
            }
        )
        return _extract_message_content(response, model=config.model)

    return extractor


def _urllib_transport(request: dict[str, Any]) -> dict[str, Any]:
    http_request = Request(
        request["url"],
        data=json.dumps(request["body"]).encode("utf-8"),
        headers=request["headers"],
        method="POST",
    )
    try:
        with urlopen(http_request, timeout=request["timeout_seconds"]) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = _api_error_message(body) or f"HTTP {exc.code}"
        raise LLMExtractorError(f"LLM API request failed: {detail}") from exc
    except URLError as exc:
        raise LLMExtractorError(f"LLM API request failed: {exc.reason}") from exc
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMExtractorError(
            "LLM API returned a non-JSON response body."
        ) from exc
    if not isinstance(decoded, dict):
        raise LLMExtractorError("LLM API response must be a JSON object.")
    return decoded


def _api_error_message(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        message = payload["error"].get("message")
        if isinstance(message, str) and message.strip():
            return message
    return None


def _extract_message_content(response: dict[str, Any], *, model: str) -> str:
    error = response.get("error")
    if isinstance(error, dict):
        message = error.get("message") or json.dumps(error, ensure_ascii=False)
        raise LLMExtractorError(f"LLM API returned an error: {message}")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMExtractorError(
            f"LLM API response for model {model!r} contained no choices."
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LLMExtractorError("LLM API response choice must be an object.")
    if choice.get("finish_reason") == "length":
        raise LLMExtractorError(
            f"Model {model!r} output was truncated at the max_tokens limit; "
            "the extraction JSON is incomplete."
        )
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise LLMExtractorError(
            f"LLM API response for model {model!r} contained no message content."
        )
    return _strip_code_fences(content)


def _strip_code_fences(text: str) -> str:
    """Unwrap ```json ... ``` fences some models add despite JSON-only prompts."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()
