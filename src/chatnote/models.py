from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BlockType = Literal["text", "code", "table", "attachment", "tool"]
Role = Literal["user", "assistant", "system", "unknown"]


@dataclass(slots=True)
class TranscriptWarning:
    code: str
    message: str
    message_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.message_index is None:
            data.pop("message_index")
        return data


@dataclass(slots=True)
class MessageBlock:
    type: BlockType
    text: str
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.language is None:
            data.pop("language")
        if not self.metadata:
            data.pop("metadata")
        return data


@dataclass(slots=True)
class TranscriptMessage:
    index: int
    role: Role
    text: str
    blocks: list[MessageBlock]
    timestamp: str | None
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "text": self.text,
            "blocks": [block.to_dict() for block in self.blocks],
            "timestamp": self.timestamp,
            "provenance": self.provenance,
        }


@dataclass(slots=True)
class Transcript:
    source: dict[str, Any]
    conversation: dict[str, Any]
    messages: list[TranscriptMessage]
    warnings: list[TranscriptWarning]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "conversation": self.conversation,
            "messages": [message.to_dict() for message in self.messages],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
