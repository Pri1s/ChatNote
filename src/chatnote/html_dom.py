from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterable


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(slots=True)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)

    def append(self, child: Node | str) -> None:
        self.children.append(child)

    def text_content(self, separator: str = " ") -> str:
        parts: list[str] = []

        def walk(node: Node | str) -> None:
            if isinstance(node, str):
                if node.strip():
                    parts.append(node.strip())
                return
            if node.tag == "br":
                parts.append("\n")
            for child in node.children:
                walk(child)

        walk(self)
        return separator.join(part for part in parts if part)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].append(node)
        if node.tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].append(data)


def parse_html(html: str) -> Node:
    parser = _TreeBuilder()
    parser.feed(html)
    parser.close()
    return parser.root


def iter_nodes(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.children):
            if isinstance(child, Node):
                stack.append(child)


def find_all(root: Node, predicate: Callable[[Node], bool]) -> list[Node]:
    return [node for node in iter_nodes(root) if predicate(node)]


def has_ancestor(node: Node, candidates: set[int]) -> bool:
    current = node.parent
    while current is not None:
        if id(current) in candidates:
            return True
        current = current.parent
    return False
