from __future__ import annotations

from styleforge.services.memory_extractor import extract_memories

from tests.extension_llm import ScriptedExtensionLlm


def _extract(responses: list[dict], request: str = "黑色衬衫 + 通勤正式"):
    llm = ScriptedExtensionLlm(responses)
    extracts = extract_memories(llm, request)
    return extracts, llm


def test_extract_memories_with_llm_returns_extracts() -> None:
    extracts, llm = _extract(
        [
            {
                "memories": [
                    {"category": "color", "content": "黑色", "meta": {"polarity": "positive"}},
                    {"category": "category", "content": "衬衫", "meta": {}},
                    {"category": "occasion", "content": "通勤", "meta": {}},
                ]
            }
        ]
    )
    categories = {(item["category"], item["content"]) for item in extracts}
    assert categories == {
        ("color", "黑色"),
        ("category", "衬衫"),
        ("occasion", "通勤"),
    }
    color = next(item for item in extracts if item["category"] == "color")
    assert color["meta"]["polarity"] == "positive"
    assert len(llm.calls) == 1


def test_extract_memories_returns_empty_without_llm() -> None:
    assert extract_memories(None, "黑色衬衫 + 通勤正式") == []
    # A blank request short-circuits before any LLM call.
    assert extract_memories(ScriptedExtensionLlm([{"memories": []}]), "  ") == []


def test_extract_memories_swallows_llm_failure() -> None:
    assert extract_memories(ScriptedExtensionLlm([]), "黑色衬衫 + 通勤正式") == []


def test_extract_memories_drops_invalid_entries() -> None:
    extracts, _ = _extract(
        [
            {
                "memories": [
                    {"category": "not-a-category", "content": "黑色"},
                    {"category": "color", "content": ""},
                    {"category": "color", "content": "黑色"},
                ]
            }
        ]
    )
    assert extracts == [{"category": "color", "content": "黑色", "meta": {}}]


def test_extract_memories_dedupes_and_normalizes() -> None:
    extracts, _ = _extract(
        [
            {
                "memories": [
                    {"category": "color", "content": "  黑色  ", "meta": {}},
                    {"category": "color", "content": "黑色", "meta": {}},
                ]
            }
        ]
    )
    assert extracts == [{"category": "color", "content": "黑色", "meta": {}}]
