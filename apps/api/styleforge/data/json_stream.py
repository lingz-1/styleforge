"""Streaming reader for a JSON object whose values are independent records."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO


class JsonObjectStreamError(ValueError):
    """Raised when a source is not a valid top-level JSON object."""


class _StreamBuffer:
    def __init__(self, handle: TextIO, chunk_size: int) -> None:
        self.handle = handle
        self.chunk_size = chunk_size
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def _compact(self) -> None:
        if self.position > self.chunk_size:
            self.buffer = self.buffer[self.position :]
            self.position = 0

    def _read_more(self) -> bool:
        if self.eof:
            return False
        self._compact()
        chunk = self.handle.read(self.chunk_size)
        if not chunk:
            self.eof = True
            return False
        self.buffer += chunk
        return True

    def skip_whitespace(self) -> None:
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer) or not self._read_more():
                return

    def peek(self) -> str | None:
        self.skip_whitespace()
        if self.position >= len(self.buffer) and not self._read_more():
            return None
        self.skip_whitespace()
        if self.position >= len(self.buffer):
            return None
        return self.buffer[self.position]

    def consume(self, expected: str) -> None:
        actual = self.peek()
        if actual != expected:
            raise JsonObjectStreamError(
                f"Expected {expected!r} at stream position, found {actual!r}."
            )
        self.position += 1

    def decode_next(self) -> Any:
        self.skip_whitespace()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
            except json.JSONDecodeError as error:
                if self._read_more():
                    continue
                raise JsonObjectStreamError(
                    f"Invalid or truncated JSON near character {error.pos}: {error.msg}."
                ) from error
            self.position = end
            return value


def iter_json_object(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[tuple[str, Any]]:
    """Yield key/value pairs without loading a large top-level object into memory."""
    if chunk_size < 4096:
        raise ValueError("chunk_size must be at least 4096 bytes")

    with path.open("r", encoding="utf-8") as handle:
        stream = _StreamBuffer(handle, chunk_size)
        stream.consume("{")
        first = True

        while True:
            token = stream.peek()
            if token == "}":
                stream.consume("}")
                break
            if token is None:
                raise JsonObjectStreamError("Unexpected end of file before closing object.")
            if not first:
                stream.consume(",")

            key = stream.decode_next()
            if not isinstance(key, str):
                raise JsonObjectStreamError("Top-level object keys must be strings.")
            stream.consume(":")
            yield key, stream.decode_next()
            first = False

        if stream.peek() is not None:
            raise JsonObjectStreamError("Unexpected content after the top-level object.")

