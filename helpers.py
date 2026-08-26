"""Pure helpers for the AI news agent.

No network, no Gemini, no Telegram. Kept small so unit tests can cover
hashing, RSS keyword filters, a single Telegram payload, and batching without
secrets or the daily workflow.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable, Dict, List, Sequence, TypeVar

T = TypeVar("T")

AI_KEYWORDS = [
    "AI",
    "artificial intelligence",
    "machine learning",
    "LLM",
    "neural",
    "deep learning",
    "transformer",
    "generative",
    "AGI",
    "inference",
    "model",
    "training",
    "GPT",
    "Claude",
    "Gemini",
]


def generate_item_id(title: str, link: str, source: str) -> str:
    """Stable MD5 of title|link|source. Used as the memory.json key."""
    content = f"{title}|{link}|{source}"
    return hashlib.md5(content.encode()).hexdigest()


def is_ai_related(
    title: str,
    summary: str = "",
    keywords: Sequence[str] | None = None,
) -> bool:
    """Case-insensitive keyword match against title + summary."""
    needles = keywords if keywords is not None else AI_KEYWORDS
    text = f"{title} {summary}".lower()
    return any(keyword.lower() in text for keyword in needles)


def filter_ai_news(
    entries: List[Dict[str, str]],
    source: str,
    keywords: Sequence[str] | None = None,
) -> List[Dict[str, str]]:
    """Keyword-filter general tech feeds (HN / YC / hnrss). Other sources pass through."""
    source_l = source.lower()
    if any(token in source_l for token in ("hackernews", "ycombinator", "hnrss")):
        return [
            entry
            for entry in entries
            if is_ai_related(entry.get("title", ""), entry.get("summary", ""), keywords)
        ]
    return list(entries)


def unique_entries(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Drop exact title+link duplicates, preserving first-seen order."""
    seen: set[str] = set()
    unique: List[Dict[str, str]] = []
    for entry in entries:
        key = f"{entry.get('title', '')}|{entry.get('link', '')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def select_new_articles(
    articles: List[Dict[str, str]],
    processed_ids: set[str],
) -> List[Dict[str, str]]:
    """Attach an id and keep articles whose hash is not in memory."""
    new_articles: List[Dict[str, str]] = []
    for article in articles:
        item_id = generate_item_id(
            article.get("title", ""),
            article.get("link", ""),
            article.get("source", ""),
        )
        if item_id in processed_ids:
            continue
        tagged = dict(article)
        tagged["id"] = item_id
        new_articles.append(tagged)
    return new_articles


def cap_articles(articles: List[T], max_n: int) -> List[T]:
    """Hard cap per run so Gemini free-tier stays inside daily quota."""
    if max_n < 0:
        raise ValueError("max_n must be >= 0")
    return articles[:max_n]


def make_batches(items: List[T], size: int) -> List[List[T]]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    return [items[i : i + size] for i in range(0, len(items), size)]


TELEGRAM_SAFE_LIMIT = 3500
TELEGRAM_HARD_LIMIT = 4096


def fit_telegram_message(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> str:
    """One Telegram payload. Trim at a newline rather than splitting into chunks."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    split_at = text.rfind("\n", 0, limit)
    if split_at == -1 or split_at < limit * 0.5:
        split_at = limit
    return text[:split_at].rstrip()


def chunk_message(text: str, limit: int = 3500) -> List[str]:
    """Split a Telegram payload on newlines, preferring ~half-limit breaks."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1 or split_at < limit * 0.5:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks


class GeminiRateLimiter:
    """In-process RPM gate + exponential backoff for 429s."""

    def __init__(self, rpm_limit: int = 10) -> None:
        if rpm_limit < 1:
            raise ValueError("rpm_limit must be >= 1")
        self.rpm_limit = rpm_limit
        self.requests_this_minute = 0
        self.minute_start_time = time.time()
        self.total_requests_today = 0

    def wait_if_needed(self) -> None:
        current_time = time.time()

        if current_time - self.minute_start_time >= 60:
            self.requests_this_minute = 0
            self.minute_start_time = current_time

        if self.requests_this_minute >= self.rpm_limit:
            wait_time = 60 - (current_time - self.minute_start_time)
            if wait_time > 0:
                print(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
                time.sleep(wait_time)
                self.requests_this_minute = 0
                self.minute_start_time = time.time()

        self.requests_this_minute += 1
        self.total_requests_today += 1

    def call_with_retry(self, func: Callable[[], T], max_retries: int = 3) -> T:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                self.wait_if_needed()
                return func()
            except Exception as exc:  # noqa: BLE001 — Gemini client raises many types
                last_error = exc
                error_msg = str(exc).lower()
                if "rate limit" in error_msg or "429" in error_msg:
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 5
                        print(
                            f"Rate limit hit. Retrying in {wait_time}s... "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(wait_time)
                        continue
                    raise RuntimeError(f"Rate limit exceeded after {max_retries} retries") from exc
                raise
        raise RuntimeError("call_with_retry exhausted retries") from last_error
