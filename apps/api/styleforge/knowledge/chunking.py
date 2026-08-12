"""Markdown knowledge chunking shared by keyword retrieval and RAG indexing."""

from __future__ import annotations

# Long chunks are sub-split so each embedding stays well under FashionCLIP's
# 77-token context window (roughly 220 CJK characters).
MAX_SUBCHUNK_CHARS = 220
SUBCHUNK_OVERLAP_CHARS = 40


def chunk_markdown(markdown: str) -> list[tuple[str, str]]:
    """Split a Markdown knowledge file into ``(section_title, content)`` pairs.

    A leading ``# `` title is skipped; every ``## `` line starts a new section
    and the accumulated body becomes the previous section's content.
    """
    chunks: list[tuple[str, str]] = []
    section = "概述"
    body: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if body:
                chunks.append((section, " ".join(body)))
            section = line.removeprefix("## ").strip()
            body = []
        elif line:
            body.append(line.lstrip("- "))
    if body:
        chunks.append((section, " ".join(body)))
    return chunks


def split_long_text(text: str) -> list[str]:
    """Split text longer than ``MAX_SUBCHUNK_CHARS`` into overlapping parts."""
    text = text.strip()
    if not text or len(text) <= MAX_SUBCHUNK_CHARS:
        return [text] if text else []
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_SUBCHUNK_CHARS, len(text))
        # Prefer a word/space boundary near the cap instead of mid-token.
        if end < len(text):
            boundary = text.rfind(" ", start + MAX_SUBCHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - SUBCHUNK_OVERLAP_CHARS, start + 1)
    return parts
