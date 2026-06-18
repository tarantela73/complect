from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

ALLOWED_NODE_TYPES = frozenset({
    "doc",
    "paragraph",
    "heading",
    "text",
    "bulletList",
    "orderedList",
    "listItem",
    "blockquote",
    "codeBlock",
    "hardBreak",
    "horizontalRule",
    "image",
})


def _validate_node_types(nodes: list[dict]) -> None:
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"Node must be a dict, got {type(node).__name__}")
        node_type = node.get("type")
        if node_type not in ALLOWED_NODE_TYPES:
            raise ValueError(f"Unknown node type: {node_type!r}")
        children = node.get("content")
        if children:
            _validate_node_types(children)


class TiptapDocSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["doc"]
    content: list[dict] = []

    @model_validator(mode="after")
    def validate_node_types(self) -> "TiptapDocSchema":
        _validate_node_types(self.content)
        return self
