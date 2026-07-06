from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from .html_dom import Node, find_all, has_ancestor, iter_nodes, parse_html
from .models import MessageBlock, Transcript, TranscriptMessage, TranscriptWarning


class ParseError(RuntimeError):
    """Raised when a shared snapshot cannot produce any transcript messages."""


ROLE_ALIASES = {
    "human": "user",
    "user": "user",
    "you": "user",
    "assistant": "assistant",
    "claude": "assistant",
    "system": "system",
}

BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}

SKIP_TAGS = {"button", "canvas", "nav", "noscript", "script", "style", "svg"}


def parse_transcript(
    html: str,
    *,
    source_url: str,
    conversation_id: str,
    fetched_at: str,
    raw_sha256: str,
) -> Transcript:
    root = parse_html(html)
    warnings: list[TranscriptWarning] = []

    structured = _parse_json_text_messages(html)
    if structured is not None:
        title, messages, structured_warnings = structured
        warnings.extend(structured_warnings)
        method = "json_structured_data"
    else:
        structured = _parse_structured_messages(root)
        if structured is not None:
            title, messages, structured_warnings = structured
            warnings.extend(structured_warnings)
            method = "embedded_structured_data"
        else:
            warnings.append(
                TranscriptWarning(
                    code="structured_data_not_found",
                    message=(
                        "No embedded structured message data was found; parsed the "
                        "visible shared snapshot HTML instead."
                    ),
                )
            )
            title, messages, html_warnings = _parse_rendered_messages(root)
            warnings.extend(html_warnings)
            method = "rendered_html"

    if not messages:
        reason = _empty_snapshot_reason(html, root)
        raise ParseError(reason or "No transcript messages could be parsed from the Claude share snapshot.")

    for message in messages:
        if message.timestamp is None:
            warnings.append(
                TranscriptWarning(
                    code="missing_timestamp",
                    message=(
                        "Shared snapshots may omit message timestamps; timestamp "
                        "is set to null for this message."
                    ),
                    message_index=message.index,
                )
            )

    return Transcript(
        source={
            "url": source_url,
            "capture_method": "claude_shared_snapshot",
            "parser_method": method,
            "fetched_at": fetched_at,
            "raw_sha256": raw_sha256,
        },
        conversation={
            "id": conversation_id,
            "title": title,
        },
        messages=messages,
        warnings=warnings,
    )


def _parse_json_text_messages(
    text: str,
) -> tuple[str | None, list[TranscriptMessage], list[TranscriptWarning]] | None:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return _structured_messages_from_payload(payload, source="json_structured_data")


def _parse_structured_messages(
    root: Node,
) -> tuple[str | None, list[TranscriptMessage], list[TranscriptWarning]] | None:
    for payload in _iter_json_payloads(root):
        parsed = _structured_messages_from_payload(payload, source="embedded_structured_data")
        if parsed is not None:
            return parsed

    return None


def _structured_messages_from_payload(
    payload: Any, *, source: str
) -> tuple[str | None, list[TranscriptMessage], list[TranscriptWarning]] | None:
    match = _find_message_collection(payload)
    if match is None:
        return None

    parent, raw_messages = match
    title = _first_string(
        parent,
        ["title", "name", "snapshot_name", "conversation_title", "conversationTitle"],
    )
    parsed_messages: list[TranscriptMessage] = []
    warnings: list[TranscriptWarning] = []

    for index, raw_message in enumerate(raw_messages):
        message, message_warnings = _message_from_structured(index, raw_message, source=source)
        if message is not None:
            parsed_messages.append(message)
            warnings.extend(message_warnings)

    if parsed_messages:
        return title, parsed_messages, warnings
    return None


def _iter_json_payloads(root: Node) -> Iterable[Any]:
    for script in find_all(root, lambda node: node.tag == "script"):
        text = script.text_content(separator="")
        if not text:
            continue
        script_type = script.attrs.get("type", "").lower()
        script_id = script.attrs.get("id", "").lower()
        stripped = text.strip()
        if (
            "json" not in script_type
            and script_id != "__next_data__"
            and not stripped.startswith(("{", "["))
        ):
            continue
        try:
            yield json.loads(stripped)
        except json.JSONDecodeError:
            continue


def _find_message_collection(payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    best: tuple[dict[str, Any], list[dict[str, Any]]] | None = None

    def visit(value: Any, parent: dict[str, Any] | None = None) -> None:
        nonlocal best
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    isinstance(child, list)
                    and key.lower() in {"messages", "conversation", "turns", "chat_messages"}
                    and _looks_like_message_list(child)
                ):
                    candidate = (value, child)
                    if best is None or len(candidate[1]) > len(best[1]):
                        best = candidate
                visit(child, value)
        elif isinstance(value, list):
            if _looks_like_message_list(value):
                candidate_parent = parent or {}
                candidate = (candidate_parent, value)
                if best is None or len(candidate[1]) > len(best[1]):
                    best = candidate
            for child in value:
                visit(child, parent)

    visit(payload)
    return best


def _looks_like_message_list(value: list[Any]) -> bool:
    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return False
    message_like = sum(1 for item in dict_items if _looks_like_message(item))
    return message_like >= max(1, len(dict_items) // 2)


def _looks_like_message(value: dict[str, Any]) -> bool:
    keys = {key.lower() for key in value}
    has_role = bool(keys & {"role", "sender", "author", "speaker"})
    if not has_role and "type" in value:
        has_role = _normalize_role(str(value["type"])) != "unknown"
    has_content = bool(keys & {"content", "text", "message", "body", "parts"})
    return has_role and has_content


def _message_from_structured(
    index: int, raw_message: dict[str, Any], *, source: str
) -> tuple[TranscriptMessage | None, list[TranscriptWarning]]:
    role = _normalize_role(
        _first_string(raw_message, ["role", "sender", "author", "speaker", "type"])
    )
    timestamp = _first_string(
        raw_message,
        ["created_at", "createdAt", "timestamp", "created_time", "createdTime", "updated_at"],
    )
    blocks, warnings = _blocks_from_structured(raw_message, index)
    if not blocks:
        return None, warnings

    text = _message_text(blocks)
    return (
        TranscriptMessage(
            index=index,
            role=role,
            text=text,
            blocks=blocks,
            timestamp=timestamp,
            provenance={"source": source, "raw_index": index},
        ),
        warnings,
    )


def _blocks_from_structured(
    raw_message: dict[str, Any], message_index: int
) -> tuple[list[MessageBlock], list[TranscriptWarning]]:
    warnings: list[TranscriptWarning] = []
    content = (
        raw_message.get("content")
        or raw_message.get("text")
        or raw_message.get("message")
        or raw_message.get("body")
        or raw_message.get("parts")
    )

    blocks: list[MessageBlock] = []

    def append_value(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            cleaned = _clean_text(value)
            if cleaned:
                blocks.append(MessageBlock(type="text", text=cleaned))
            return
        if isinstance(value, list):
            for item in value:
                append_value(item)
            return
        if not isinstance(value, dict):
            cleaned = _clean_text(str(value))
            if cleaned:
                blocks.append(MessageBlock(type="text", text=cleaned))
            return

        part_type = _first_string(value, ["type", "kind", "content_type"]) or ""
        text = _first_string(value, ["text", "content", "value", "body", "input"]) or ""
        language = _first_string(value, ["language", "lang"])
        lower_type = part_type.lower()

        if "tool" in lower_type:
            warnings.append(
                TranscriptWarning(
                    code="unsupported_tool_call",
                    message="Tool-call data is recorded as unsupported for Sprint 1 snapshots.",
                    message_index=message_index,
                )
            )
            blocks.append(
                MessageBlock(
                    type="tool",
                    text="[Unsupported tool-call data omitted by shared snapshot parser]",
                    metadata={"structured_type": part_type},
                )
            )
            return

        if any(token in lower_type for token in ("image", "file", "attachment")):
            name = _first_string(value, ["name", "filename", "file_name", "title"]) or "attachment"
            warnings.append(
                TranscriptWarning(
                    code="attachment_placeholder",
                    message="Attachment contents are out of scope for Sprint 1 snapshots.",
                    message_index=message_index,
                )
            )
            blocks.append(MessageBlock(type="attachment", text=f"[Attachment not captured: {name}]"))
            return

        if "code" in lower_type or language:
            blocks.append(MessageBlock(type="code", text=text, language=language))
            return

        if "table" in lower_type:
            blocks.append(MessageBlock(type="table", text=text))
            return

        if text:
            blocks.append(MessageBlock(type="text", text=_clean_text(text)))
            return

        for nested_key in ("children", "items", "parts"):
            if nested_key in value:
                append_value(value[nested_key])

    append_value(content)

    for attachment_key in ("attachments", "files"):
        raw_attachments = raw_message.get(attachment_key)
        if not isinstance(raw_attachments, list):
            continue
        for attachment in raw_attachments:
            if not isinstance(attachment, dict):
                continue
            name = (
                _first_string(attachment, ["file_name", "filename", "name", "title"])
                or attachment_key[:-1]
            )
            warnings.append(
                TranscriptWarning(
                    code="attachment_placeholder",
                    message="Attachment contents are out of scope for Sprint 1 snapshots.",
                    message_index=message_index,
                )
            )
            blocks.append(MessageBlock(type="attachment", text=f"[Attachment not captured: {name}]"))
    return blocks, warnings


def _parse_rendered_messages(
    root: Node,
) -> tuple[str | None, list[TranscriptMessage], list[TranscriptWarning]]:
    title = _page_title(root)
    candidates = _message_candidates(root)
    messages: list[TranscriptMessage] = []
    warnings: list[TranscriptWarning] = []

    for raw_index, node in enumerate(candidates):
        role = _infer_role(node)
        blocks, block_warnings = _blocks_from_html(node, len(messages))
        if not blocks:
            continue
        messages.append(
            TranscriptMessage(
                index=len(messages),
                role=role,
                text=_message_text(blocks),
                blocks=blocks,
                timestamp=_timestamp_from_node(node),
                provenance={
                    "source": "rendered_html",
                    "tag": node.tag,
                    "raw_index": raw_index,
                },
            )
        )
        warnings.extend(block_warnings)

    return title, messages, warnings


def _message_candidates(root: Node) -> list[Node]:
    candidates = [
        node
        for node in iter_nodes(root)
        if node.tag not in {"document", "html", "body"}
        and _infer_role_from_attrs(node) != "unknown"
        and node.text_content().strip()
    ]
    leaf_candidates = _drop_container_candidates(candidates)
    if leaf_candidates:
        return leaf_candidates

    article_candidates = [
        node
        for node in iter_nodes(root)
        if node.tag in {"article", "section"}
        and _infer_role_from_text(node.text_content(separator="\n")) != "unknown"
    ]
    return _drop_container_candidates(article_candidates)


def _infer_role(node: Node) -> str:
    role = _infer_role_from_attrs(node)
    if role != "unknown":
        return role
    return _infer_role_from_text(node.text_content(separator="\n"))


def _infer_role_from_attrs(node: Node) -> str:
    attr_values = " ".join(
        value
        for key, value in node.attrs.items()
        if key
        in {
            "aria-label",
            "class",
            "data-author",
            "data-message-author-role",
            "data-role",
            "data-testid",
            "id",
            "role",
        }
    )
    return _normalize_role(attr_values)


def _drop_container_candidates(candidates: list[Node]) -> list[Node]:
    candidate_ids = {id(node) for node in candidates}

    def contains_candidate_descendant(node: Node) -> bool:
        for child in iter_nodes(node):
            if child is not node and id(child) in candidate_ids:
                return True
        return False

    leaves = [node for node in candidates if not contains_candidate_descendant(node)]
    return [node for node in leaves if not has_ancestor(node, {id(candidate) for candidate in leaves})]


def _infer_role_from_text(text: str) -> str:
    first_line = ""
    for line in text.splitlines():
        cleaned = _clean_text(line).lower()
        if cleaned:
            first_line = cleaned
            break
    if first_line in ROLE_ALIASES:
        return ROLE_ALIASES[first_line]
    if first_line.startswith("you"):
        return "user"
    if first_line.startswith("claude"):
        return "assistant"
    return "unknown"


def _blocks_from_html(
    node: Node, message_index: int
) -> tuple[list[MessageBlock], list[TranscriptWarning]]:
    blocks: list[MessageBlock] = []
    warnings: list[TranscriptWarning] = []
    text_buffer: list[str] = []

    def flush_text() -> None:
        text = _clean_text("\n".join(text_buffer))
        text_buffer.clear()
        text = _drop_role_label(text)
        if text:
            blocks.append(MessageBlock(type="text", text=text))

    def walk(current: Node | str) -> None:
        if isinstance(current, str):
            if current.strip():
                text_buffer.append(current)
            return
        if current is not node and current.tag in SKIP_TAGS:
            return
        if current.tag == "br":
            text_buffer.append("\n")
            return
        if current.tag == "pre":
            flush_text()
            blocks.append(
                MessageBlock(
                    type="code",
                    text=current.text_content(separator="\n").strip(),
                    language=_code_language(current),
                )
            )
            return
        if current.tag == "table":
            flush_text()
            blocks.append(MessageBlock(type="table", text=_table_to_markdown(current)))
            return
        if current.tag == "img":
            flush_text()
            label = current.attrs.get("alt") or current.attrs.get("src") or "image"
            blocks.append(MessageBlock(type="attachment", text=f"[Attachment not captured: {label}]"))
            warnings.append(
                TranscriptWarning(
                    code="attachment_placeholder",
                    message="Attachment contents are out of scope for Sprint 1 snapshots.",
                    message_index=message_index,
                )
            )
            return

        for child in current.children:
            walk(child)
        if current.tag in BLOCK_TAGS:
            text_buffer.append("\n")

    walk(node)
    flush_text()
    return blocks, warnings


def _code_language(node: Node) -> str | None:
    for child in iter_nodes(node):
        class_value = child.attrs.get("class", "")
        match = re.search(r"(?:language|lang)-([A-Za-z0-9_+-]+)", class_value)
        if match:
            return match.group(1)
    return None


def _table_to_markdown(table: Node) -> str:
    rows: list[list[str]] = []
    header_row = False
    for row in find_all(table, lambda node: node.tag == "tr"):
        cells = find_all(row, lambda node: node.tag in {"th", "td"})
        if not cells:
            continue
        rows.append([_clean_text(cell.text_content()) for cell in cells])
        if any(cell.tag == "th" for cell in cells):
            header_row = True

    if not rows:
        return _clean_text(table.text_content())

    rendered = ["| " + " | ".join(row) + " |" for row in rows]
    if header_row and rendered:
        rendered.insert(1, "| " + " | ".join("---" for _ in rows[0]) + " |")
    return "\n".join(rendered)


def _timestamp_from_node(node: Node) -> str | None:
    for key in ("datetime", "data-created-at", "data-created", "data-timestamp", "title"):
        value = node.attrs.get(key)
        if value and re.search(r"\d{4}-\d{2}-\d{2}", value):
            return value
    for child in find_all(node, lambda candidate: candidate.tag == "time"):
        value = child.attrs.get("datetime") or child.text_content()
        if value:
            return _clean_text(value)
    return None


def _page_title(root: Node) -> str | None:
    for title in find_all(root, lambda node: node.tag == "title"):
        text = _clean_text(title.text_content())
        if text:
            return text
    for heading in find_all(root, lambda node: node.tag == "h1"):
        text = _clean_text(heading.text_content())
        if text and _normalize_role(text) == "unknown":
            return text
    return None


def _message_text(blocks: list[MessageBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.type == "code":
            language = block.language or ""
            parts.append(f"```{language}\n{block.text}\n```")
        else:
            parts.append(block.text)
    return "\n\n".join(part for part in parts if part).strip()


def _normalize_role(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = value.lower()
    for token, role in ROLE_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return role
    return "unknown"


def _first_string(value: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        if key in value and value[key] is not None:
            raw = value[key]
            if isinstance(raw, dict):
                nested = _first_string(raw, ["name", "text", "value", "title", "role"])
                if nested:
                    return nested
            elif isinstance(raw, str):
                return raw
            else:
                return str(raw)
    return None


def _clean_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    compacted: list[str] = []
    for line in lines:
        if line or (compacted and compacted[-1]):
            compacted.append(line)
    while compacted and not compacted[0]:
        compacted.pop(0)
    while compacted and not compacted[-1]:
        compacted.pop()
    return "\n".join(compacted)


def _drop_role_label(text: str) -> str:
    lines = text.splitlines()
    if lines and _normalize_role(lines[0]) != "unknown":
        return "\n".join(lines[1:]).strip()
    return text


def _empty_snapshot_reason(html: str, root: Node) -> str | None:
    lowered = html.lower()
    if "cf-mitigated" in lowered and "challenge" in lowered:
        return (
            "Claude returned a Cloudflare challenge instead of the shared transcript; "
            "the snapshot must be fetched by a browser session or retried later."
        )

    root_nodes = find_all(root, lambda node: node.attrs.get("id") == "root")
    has_empty_root = any(not node.text_content().strip() for node in root_nodes)
    if has_empty_root and "claude-ai" in lowered and "chat_snapshots" not in lowered:
        return (
            "Claude returned only the web app shell, not transcript data. "
            "The shared snapshot content is loaded from Claude's public snapshot API."
        )

    return None
