"""Pure-helper tests. No network, no Gemini, no Telegram secrets."""

from __future__ import annotations

import time

import pytest

from helpers import (
    AI_KEYWORDS,
    GeminiRateLimiter,
    cap_articles,
    chunk_message,
    filter_ai_news,
    fit_telegram_message,
    generate_item_id,
    is_ai_related,
    make_batches,
    select_new_articles,
    unique_entries,
)


def test_generate_item_id_is_stable_and_hex():
    first = generate_item_id("Title", "https://example.com/a", "HN")
    second = generate_item_id("Title", "https://example.com/a", "HN")
    assert first == second
    assert len(first) == 32
    assert first == first.lower()
    int(first, 16)  # valid hex


def test_generate_item_id_changes_with_any_field():
    base = generate_item_id("Title", "https://example.com/a", "HN")
    assert generate_item_id("Other", "https://example.com/a", "HN") != base
    assert generate_item_id("Title", "https://example.com/b", "HN") != base
    assert generate_item_id("Title", "https://example.com/a", "ArXiv") != base


def test_is_ai_related_matches_allow_list():
    assert is_ai_related("New LLM beats the baseline", "")
    assert is_ai_related("Weekly recap", "Claude and Gemini ship tools")
    assert not is_ai_related("City council votes on parking", "local news")


def test_is_ai_related_respects_custom_keywords():
    assert is_ai_related("Rust 1.80 released", "", keywords=["Rust"])
    assert not is_ai_related("Rust 1.80 released", "", keywords=["LLM"])


def test_filter_ai_news_only_filters_hn_family():
    entries = [
        {"title": "New LLM drop", "summary": ""},
        {"title": "City parking vote", "summary": ""},
    ]
    hn = filter_ai_news(entries, "hnrss.org")
    assert [e["title"] for e in hn] == ["New LLM drop"]

    yc = filter_ai_news(entries, "news.ycombinator.com")
    assert [e["title"] for e in yc] == ["New LLM drop"]

    arxiv = filter_ai_news(entries, "arxiv.org")
    assert arxiv == entries


def test_unique_entries_preserves_first_seen():
    entries = [
        {"title": "A", "link": "https://a.example/1"},
        {"title": "A", "link": "https://a.example/1"},
        {"title": "B", "link": "https://b.example/1"},
    ]
    unique = unique_entries(entries)
    assert [e["title"] for e in unique] == ["A", "B"]


def test_select_new_articles_skips_known_ids():
    articles = [
        {"title": "A", "link": "https://a.example/1", "source": "HN"},
        {"title": "B", "link": "https://b.example/1", "source": "HN"},
    ]
    known = {generate_item_id("A", "https://a.example/1", "HN")}
    fresh = select_new_articles(articles, known)
    assert len(fresh) == 1
    assert fresh[0]["title"] == "B"
    assert "id" in fresh[0]
    assert fresh[0]["id"] == generate_item_id("B", "https://b.example/1", "HN")


def test_cap_articles_and_batches():
    items = list(range(12))
    assert cap_articles(items, 5) == [0, 1, 2, 3, 4]
    assert cap_articles(items, 0) == []
    assert make_batches(items, 5) == [list(range(5)), list(range(5, 10)), [10, 11]]
    with pytest.raises(ValueError):
        cap_articles(items, -1)
    with pytest.raises(ValueError):
        make_batches(items, 0)


def test_chunk_message_short_and_empty():
    assert chunk_message("hello") == ["hello"]
    assert chunk_message("") == [""]


def test_chunk_message_prefers_newline_break():
    text = "alpha\n" + ("b" * 20) + "\n" + ("c" * 20)
    chunks = chunk_message(text, limit=30)
    assert all(len(c) <= 30 for c in chunks)
    assert len(chunks) >= 2
    assert "alpha" in chunks[0]


def test_chunk_message_hard_splits_without_newline():
    text = "x" * 80
    chunks = chunk_message(text, limit=30)
    assert chunks == ["x" * 30, "x" * 30, "x" * 20]




def test_fit_telegram_message_passthrough_and_strip():
    assert fit_telegram_message("hello") == "hello"
    assert fit_telegram_message("  hi  ") == "hi"
    assert fit_telegram_message("") == ""
    assert fit_telegram_message("   ") == ""


def test_fit_telegram_message_trims_to_one_payload():
    text = "alpha\n" + ("b" * 20) + "\n" + ("c" * 40)
    fitted = fit_telegram_message(text, limit=30)
    assert isinstance(fitted, str)
    assert len(fitted) <= 30
    assert "alpha" in fitted
    assert fitted == fitted.rstrip()


def test_fit_telegram_message_hard_cut_without_newline():
    fitted = fit_telegram_message("x" * 80, limit=30)
    assert fitted == "x" * 30
    assert len(fitted) <= 30

def test_rate_limiter_counts_under_cap(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    limiter = GeminiRateLimiter(rpm_limit=3)
    limiter.wait_if_needed()
    limiter.wait_if_needed()
    limiter.wait_if_needed()
    assert limiter.requests_this_minute == 3
    assert limiter.total_requests_today == 3
    assert slept == []


def test_rate_limiter_retries_on_429(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    limiter = GeminiRateLimiter(rpm_limit=50)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 rate limit")
        return "ok"

    assert limiter.call_with_retry(flaky, max_retries=3) == "ok"
    assert calls["n"] == 3


def test_rate_limiter_raises_after_exhausted_429(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    limiter = GeminiRateLimiter(rpm_limit=50)

    def always_limited():
        raise RuntimeError("rate limit exceeded")

    with pytest.raises(RuntimeError, match="Rate limit exceeded"):
        limiter.call_with_retry(always_limited, max_retries=2)


def test_ai_keywords_cover_core_models():
    lowered = {k.lower() for k in AI_KEYWORDS}
    for token in ("llm", "gpt", "claude", "gemini", "transformer"):
        assert token in lowered
