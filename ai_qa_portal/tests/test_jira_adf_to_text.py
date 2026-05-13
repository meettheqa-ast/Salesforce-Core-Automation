"""ADF -> plain text flattening used by the Jira sync orchestrator.

If this regresses, every Jira description / comment landing in RAG
silently becomes empty (or JSON noise). Pin the basic shapes.
"""

from __future__ import annotations

from ai_qa_portal.backend.services.jira_sync import adf_to_text


def test_plain_string_returned_as_is():
    assert adf_to_text("hello") == "hello"


def test_paragraph_with_text_node():
    node = {
        "type": "paragraph",
        "content": [{"type": "text", "text": "Hello there"}],
    }
    out = adf_to_text(node).strip()
    assert "Hello there" in out


def test_block_nodes_get_their_own_line():
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "First."}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second."}]},
        ],
    }
    out = adf_to_text(doc)
    assert "First." in out
    assert "Second." in out
    assert out.index("First.") < out.index("Second.")


def test_nested_lists_flatten():
    doc = {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Item A"}]},
            ]},
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Item B"}]},
            ]},
        ],
    }
    out = adf_to_text(doc)
    assert "Item A" in out
    assert "Item B" in out


def test_unknown_node_type_does_not_crash():
    out = adf_to_text({"type": "weirdNewBlock", "text": "fallback"})
    # When a leaf node carries a `text` field, we keep it even if the
    # type is unfamiliar so future ADF additions degrade gracefully.
    assert "fallback" in out
