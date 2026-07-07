"""Tests for narrative layout helpers in build.py."""
from __future__ import annotations

from bookforge.layout.build import _ORPHAN_TAIL_CHARS, _paras, _split_paras


def _long_para(n: int) -> str:
    return "word " * n


def test_split_paras_merges_small_tail():
    """A short tail should merge into the head instead of orphaning a page."""
    cap = 500
    head_para = _long_para(120)  # ~600 chars
    tail_para = "Short closing paragraph."  # well under _ORPHAN_TAIL_CHARS
    text = f"{head_para}\n\n{tail_para}"
    assert len(tail_para) < _ORPHAN_TAIL_CHARS
    assert len(text) <= int(cap * 1.3)

    head, tail = _split_paras(text, cap)
    assert tail == ""
    assert head == text


def test_split_paras_splits_large_tail():
    """A large tail should still split when it cannot merge."""
    cap = 500
    head_para = _long_para(120)
    tail_para = _long_para(300)  # ~1500 chars — too big to merge
    text = f"{head_para}\n\n{tail_para}"

    head, tail = _split_paras(text, cap)
    assert tail != ""
    assert len(tail) >= _ORPHAN_TAIL_CHARS
    assert head in text
    assert tail in text
    assert head != text


def test_paras_strips_leading_bullets():
    html_out = _paras("• First point\n\n- Second line\n\n* Third item")
    assert "•" not in html_out
    assert html_out.count("<p>") == 3
    assert "First point" in html_out
    assert "Second line" in html_out
    assert "Third item" in html_out


def test_paras_collapses_whitespace():
    html_out = _paras("Too   many    spaces   here.")
    assert "Too many spaces here." in html_out
