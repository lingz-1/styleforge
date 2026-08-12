from __future__ import annotations

from styleforge.services.memory_extractor import extract_memories


def test_extract_memories_hits_multiple_categories() -> None:
    extracts = extract_memories("黑色衬衫 + 通勤正式")
    categories = {(item["category"], item["content"]) for item in extracts}
    assert ("color", "黑色") in categories
    assert ("category", "衬衫") in categories
    assert ("occasion", "通勤") in categories
    assert ("formality", "正式") in categories


def test_extract_memories_marks_negated_preferences() -> None:
    extracts = extract_memories("不要黑色")
    colors = [item for item in extracts if item["category"] == "color"]
    assert colors
    assert colors[0]["content"] == "避免黑色"
    assert colors[0]["meta"]["polarity"] == "negative"


def test_extract_memories_caps_noisy_categories() -> None:
    extracts = extract_memories("黑色 白色 灰色 蓝色 红色 绿色")
    colors = [item for item in extracts if item["category"] == "color"]
    assert len(colors) == 3


def test_extract_memories_detects_style() -> None:
    extracts = extract_memories("我喜欢复古风")
    styles = [item for item in extracts if item["category"] == "style"]
    assert styles and styles[0]["content"] == "复古"
    assert styles[0]["meta"]["style_tag"] == "vintage"


def test_extract_memories_detects_habit() -> None:
    extracts = extract_memories("我每天上班都穿衬衫")
    habits = [item for item in extracts if item["category"] == "habit"]
    assert habits and habits[0]["content"] == "常穿衬衫"


def test_extract_memories_returns_empty_for_plain_text() -> None:
    assert extract_memories("帮我随便看看") == []
    assert extract_memories("") == []
