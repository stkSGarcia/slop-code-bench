"""Tests for runtime stream processing helpers."""

from __future__ import annotations

from slop_code.execution.stream_processor import ensure_string


def test_ensure_string_preserves_text_around_invalid_utf8_bytes() -> None:
    decoded = ensure_string(b'{"type":"message_update","data":"ok"}\xff\n')

    assert '{"type":"message_update","data":"ok"}' in decoded
    assert decoded.endswith("\n")
