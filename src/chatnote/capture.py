from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .parser import parse_transcript
from .url_validation import ClaudeShareURL, validate_claude_share_url


class FetchError(RuntimeError):
    """Raised when the public shared snapshot cannot be fetched."""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    conversation_id: str
    title: str | None
    message_count: int
    warning_count: int
    raw_path: Path
    transcript_path: Path


@dataclass(frozen=True, slots=True)
class FetchedSource:
    text: str
    extension: str


def fetch_html(url: str, *, timeout_seconds: int = 30) -> str:
    return _read_url(
        url,
        headers={
            "User-Agent": "ChatNote/0.1 (+https://github.com/local/chatnote)",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout_seconds=timeout_seconds,
    )


def fetch_public_share_source(
    share: ClaudeShareURL, *, timeout_seconds: int = 30
) -> FetchedSource:
    api_url = _snapshot_api_url(share)
    try:
        payload = _read_url(
            api_url,
            headers={
                "User-Agent": "ChatNote/0.1 (+https://github.com/local/chatnote)",
                "Accept": "application/json",
                "Referer": share.original_url,
            },
            timeout_seconds=timeout_seconds,
        )
        if _looks_like_cloudflare_challenge(payload):
            raise FetchError("Claude snapshot API returned a Cloudflare challenge.")
        if payload.lstrip().startswith(("{", "[")):
            return FetchedSource(text=payload, extension="json")
    except FetchError as exc:
        api_error = exc
    else:
        api_error = FetchError("Claude snapshot API returned a non-JSON response.")

    html = fetch_html(share.original_url, timeout_seconds=timeout_seconds)
    if _looks_like_empty_claude_app_shell(html):
        raise FetchError(
            "Claude returned only the web app shell, and the public snapshot API "
            f"could not be fetched ({api_error}). The share may need a browser "
            "session to pass Claude's challenge before transcript data is available."
        )
    return FetchedSource(text=html, extension="html")


def _read_url(url: str, *, headers: dict[str, str], timeout_seconds: int) -> str:
    request = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        detail = f"HTTP {exc.code}"
        if exc.headers.get("cf-mitigated") == "challenge":
            detail += " (Cloudflare challenge)"
        raise FetchError(f"Claude share fetch failed with {detail}.") from exc
    except URLError as exc:
        raise FetchError(f"Claude share fetch failed: {exc.reason}") from exc


def capture_share_link(
    url: str,
    *,
    output_dir: Path,
    fetcher=None,
) -> CaptureResult:
    share = validate_claude_share_url(url)
    fetched_at = _utc_now()
    file_stamp = fetched_at.replace("-", "").replace(":", "").replace(".", "")
    fetched_source = _coerce_fetched_source(
        fetcher(share.original_url) if fetcher else fetch_public_share_source(share)
    )
    raw_sha256 = hashlib.sha256(fetched_source.text.encode("utf-8")).hexdigest()

    raw_dir = output_dir / "raw"
    transcript_dir = output_dir / "transcripts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{share.conversation_id}-{file_stamp}.{fetched_source.extension}"
    transcript_path = transcript_dir / f"{share.conversation_id}-{file_stamp}.json"

    raw_path.write_text(fetched_source.text, encoding="utf-8")
    transcript = parse_transcript(
        fetched_source.text,
        source_url=share.original_url,
        conversation_id=share.conversation_id,
        fetched_at=fetched_at,
        raw_sha256=raw_sha256,
    )
    transcript_path.write_text(
        json.dumps(transcript.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return CaptureResult(
        conversation_id=share.conversation_id,
        title=transcript.conversation.get("title"),
        message_count=len(transcript.messages),
        warning_count=len(transcript.warnings),
        raw_path=raw_path,
        transcript_path=transcript_path,
    )


def _coerce_fetched_source(value: str | FetchedSource) -> FetchedSource:
    if isinstance(value, FetchedSource):
        return value
    extension = "json" if value.lstrip().startswith(("{", "[")) else "html"
    return FetchedSource(text=value, extension=extension)


def _snapshot_api_url(share: ClaudeShareURL) -> str:
    snapshot_id = quote(share.conversation_id, safe="")
    return (
        "https://claude.ai/api/chat_snapshots/"
        f"{snapshot_id}?rendering_mode=messages&render_all_tools=true"
    )


def _looks_like_cloudflare_challenge(text: str) -> bool:
    lowered = text.lower()
    return "just a moment" in lowered and "challenges.cloudflare.com" in lowered


def _looks_like_empty_claude_app_shell(text: str) -> bool:
    lowered = text.lower()
    return '<div id="root"></div>' in lowered and "claude-ai" in lowered


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_supported_url(url: str) -> ClaudeShareURL:
    return validate_claude_share_url(url)
