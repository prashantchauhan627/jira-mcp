"""
Atlassian Document Format (ADF) helpers.

Jira Cloud API v3 returns descriptions and comments as ADF — a JSON tree —
not as plain strings. The model doesn't need the tree; it needs the text.
So we flatten on read and build the simplest valid ADF on write.
"""

from __future__ import annotations

from typing import Any


def adf_to_text(node: Any) -> str:
    """Recursively extract plain text from an ADF document or node."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")

    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    if node_type == "mention":
        return node.get("attrs", {}).get("text", "@user")
    if node_type == "inlineCard":
        return node.get("attrs", {}).get("url", "")

    inner = adf_to_text(node.get("content", []))

    # Block-level nodes get a trailing newline so paragraphs stay separated.
    block_types = {
        "paragraph", "heading", "bulletList", "orderedList", "listItem",
        "codeBlock", "blockquote", "panel", "rule", "table", "tableRow",
    }
    if node_type in block_types:
        return inner.rstrip("\n") + "\n"
    return inner


def text_to_adf(text: str) -> dict[str, Any]:
    """Build the minimal ADF document for a plain-text body, one paragraph per line."""
    paragraphs = []
    for line in text.split("\n"):
        content = [{"type": "text", "text": line}] if line else []
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}
