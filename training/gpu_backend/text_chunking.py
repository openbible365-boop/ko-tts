"""Text chunking for TTS engines with bounded single-request generation."""

from __future__ import annotations

import re


SENTENCE_ENDINGS = frozenset(".!?。！？;；\n")


def _split_oversized(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for word in text.split():
        if len(word) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                word[start : start + max_chars]
                for start in range(0, len(word), max_chars)
            )
            continue
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_tts_text(text: str, max_chars: int = 80) -> list[str]:
    """Split at sentence endings, then word boundaries, without dropping text."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    normalized = re.sub(r"[ \t\r\f\v]+", " ", text).strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    units: list[str] = []
    current = ""
    for char in normalized:
        current += char
        if char in SENTENCE_ENDINGS:
            unit = current.strip()
            if unit:
                units.append(unit)
            current = ""
    if current.strip():
        units.append(current.strip())

    chunks: list[str] = []
    for unit in units:
        parts = [unit] if len(unit) <= max_chars else _split_oversized(unit, max_chars)
        chunks.extend(parts)
    return chunks
