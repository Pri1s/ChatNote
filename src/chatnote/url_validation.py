from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class URLValidationError(ValueError):
    """Raised when a capture URL is outside the supported Sprint 1 surface."""


@dataclass(frozen=True, slots=True)
class ClaudeShareURL:
    original_url: str
    conversation_id: str


def validate_claude_share_url(url: str) -> ClaudeShareURL:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise URLValidationError("Claude share links must use https.")

    if host not in {"claude.ai", "www.claude.ai"}:
        raise URLValidationError(
            "Only Claude shared snapshot URLs are supported, such as "
            "https://claude.ai/share/<id>."
        )

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0] != "share" or not path_parts[1]:
        raise URLValidationError(
            "Only Claude shared snapshot URLs are supported. Private Claude "
            "chat URLs and other Claude.ai pages are out of scope for Sprint 1."
        )

    return ClaudeShareURL(original_url=url.strip(), conversation_id=path_parts[1])
