#!/usr/bin/env python3
"""
Long Horizon, Deep Memory AI News Agent

This script:
1. Fetches AI news from multiple RSS feeds
2. Loads memory (processed IDs) from memory.json
3. Filters out already-processed news items
4. Asks Gemini to pick only the day's important AI developments
5. Sends one short Telegram message (never a multi-message dump)
6. Updates memory.json with considered IDs only after a successful send
7. Commits and pushes the updated memory back to the repo (done by workflow)
"""

import os
import json
import time
import feedparser
import requests
from datetime import datetime
from typing import List, Dict, Any

from helpers import (
    AI_KEYWORDS,  # re-exported for callers / tests that import agent
    GeminiRateLimiter,
    cap_articles,
    filter_ai_news,
    fit_telegram_message,
    select_new_articles,
    unique_entries,
)

# ============================================================================
# CONFIGURATION
# ============================================================================

# RSS Feeds to monitor (free sources)
RSS_FEEDS = [
    "https://hnrss.org/newest?q=AI",  # Hacker News AI search (replaces broken newest.rss)
    "http://arxiv.org/rss/cs.AI",  # ArXiv CS.AI
    "https://www.reddit.com/r/MachineLearning/.rss?sort=new",  # Reddit ML (may need user-agent)
    "https://blog.google/technology/ai/rss/",  # Google AI Blog (updated URL)
    "https://openai.com/news/rss.xml",  # OpenAI News RSS feed
    "https://huggingface.co/blog/feed.xml",  # Hugging Face Blog
]

# Keywords live in helpers.AI_KEYWORDS so unit tests can share the allow-list.


# Rate limiting configuration for Gemini Free Tier
# Free tier: ~15 RPM, ~1500 RPD
GEMINI_REQUESTS_PER_MINUTE = 10  # Conservative limit
GEMINI_MAX_ARTICLES_PER_RUN = 50  # Cap the candidate pool Gemini reads
# One Gemini call writes the whole brief; keep a limiter for 429 retries.

# Model configuration
# Using the latest stable Gemini Flash model with high thinking for stronger summaries
MODEL_NAME = "gemini-3.6-flash"
THINKING_LEVEL = "high"

# Memory file path (stored in repo for persistence)
MEMORY_FILE = "memory.json"

# Telegram configuration (from environment)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Gemini API key (from environment)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TEST_TELEGRAM_ONLY = os.getenv("TEST_TELEGRAM_ONLY", "false").lower() == "true"


# ============================================================================
# MEMORY MANAGEMENT
# ============================================================================

def load_memory() -> Dict[str, Any]:
    """Load the memory state from the JSON file."""
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return {"processed_ids": [], "last_run_date": None}


def save_memory(memory: Dict[str, Any]) -> None:
    """Save the memory state to the JSON file."""
    memory["last_run_date"] = datetime.utcnow().isoformat()
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)
    print(f"Memory saved: {len(memory['processed_ids'])} total processed IDs")


# ============================================================================
# NEWS FETCHING
# ============================================================================

def fetch_rss_feed(url: str) -> List[Dict[str, str]]:
    """Fetch and parse an RSS feed."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        entries = []
        
        for entry in feed.entries[:20]:  # Limit to 20 most recent per feed
            title = getattr(entry, 'title', 'No Title')
            link = getattr(entry, 'link', '')
            summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
            published = getattr(entry, 'published', datetime.utcnow().isoformat())
            
            entries.append({
                "title": title,
                "link": link,
                "summary": summary[:500] if summary else "",  # Truncate long summaries
                "published": published,
                "source": feed.feed.get('title', url)
            })
        
        return entries
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []


def get_all_news() -> List[Dict[str, str]]:
    """Fetch news from all RSS feeds."""
    all_entries = []

    for feed_url in RSS_FEEDS:
        print(f"Fetching: {feed_url}")
        entries = fetch_rss_feed(feed_url)

        source = feed_url.split("//")[-1].split("/")[0]
        all_entries.extend(filter_ai_news(entries, source, AI_KEYWORDS))
        time.sleep(1)

    deduped = unique_entries(all_entries)
    print(f"Total unique entries fetched: {len(deduped)}")
    return deduped


# ============================================================================
# GEMINI API WITH RATE LIMITING
# ============================================================================

def initialize_gemini():
    """Initialize the Gemini client with the API key."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is not set")

    print(f"Gemini initialized with model: {MODEL_NAME} (thinking: {THINKING_LEVEL})")


def get_gemini_model():
    """Get the Gemini client. Imported lazily so helper tests need no API SDK."""
    from google import genai

    return genai.Client(api_key=GEMINI_API_KEY)


def summarize_daily_brief(articles: List[Dict[str, str]], rate_limiter: GeminiRateLimiter) -> str:
    """One Gemini call: keep only important developments, one short brief."""
    if not articles:
        return ""

    from google.genai import types

    client = get_gemini_model()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    articles_text = "\n\n".join([
        f"Title: {article['title']}\n"
        f"Source: {article['source']}\n"
        f"Link: {article['link']}\n"
        f"Summary: {article['summary'][:300]}"
        for article in articles
    ])

    prompt = f"""You write a daily AI news brief a busy person can finish in under a minute.

From the articles below, keep ONLY genuinely important AI developments for {today}:
- new models or major version drops
- lab / product launches that change what people can actually do
- landmark research (not every arXiv paper)
- policy or regulation that actually binds the industry
- large funding rounds or acquisitions

Skip recaps, listicles, rumor blogs, minor changelog notes, and duplicate coverage of the same story. Merge duplicates into one bullet.

OUTPUT (plain text, no HTML, no markdown headings):
Line 1: a short title for the day
Then 4-8 bullets. Each bullet is one development in 1-2 sentences, then the best source URL on the next line.
If nothing is actually important, write two sentences saying so. Do not pad with filler.

Hard limit: the entire brief must be under 2800 characters.

ARTICLES:
{articles_text}
"""

    def generate_summary():
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            # Gemini 3.6+ rejects temperature/top_p/top_k; thinking_level is the control.
            config=types.GenerateContentConfig(
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
            ),
        )
        return (response.text or "").strip()

    return rate_limiter.call_with_retry(generate_summary)


# ============================================================================
# TELEGRAM NOTIFICATION
# ============================================================================

def send_telegram_message(message: str) -> bool:
    """Send a message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram credentials not configured. Skipping notification.")
        print(f"Bot Token present: {bool(TELEGRAM_BOT_TOKEN)}")
        print(f"Chat ID present: {bool(TELEGRAM_CHAT_ID)}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload_text = fit_telegram_message(message)
    if not payload_text:
        print("ERROR: Telegram payload empty after trim. Skipping send.")
        return False
    if len((message or "").strip()) > len(payload_text):
        print(f"Trimmed Telegram payload from {len(message)} to {len(payload_text)} chars (one message).")

    try:
        response = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": payload_text},
            timeout=10,
        )
        response.raise_for_status()
        print("Telegram message sent successfully!")
        return True
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")
        return False


# ============================================================================
# MAIN WORKFLOW
# ============================================================================

def main():
    print("=" * 60)
    print("🤖 LONG HORIZON AI NEWS AGENT")
    print("=" * 60)

    if TEST_TELEGRAM_ONLY:
        print("\n[TEST] Telegram-only test mode enabled.")
        test_message = f"""🤖 AI News Agent test message
📅 {datetime.utcnow().strftime('%A, %B %d, %Y')}
⏰ {datetime.utcnow().strftime('%H:%M UTC')}

Telegram delivery is working.
Gemini model configured: {MODEL_NAME} with thinking level {THINKING_LEVEL}
"""
        send_telegram_message(test_message)
        print("\n✅ Test message attempt complete.")
        return
    
    # Step 1: Load memory
    print("\n[1/6] Loading memory state...")
    memory = load_memory()
    processed_ids = set(memory.get("processed_ids", []))
    print(f"Memory contains {len(processed_ids)} previously processed items")
    
    # Step 2: Fetch news
    print("\n[2/6] Fetching latest AI news...")
    all_articles = get_all_news()
    
    # Step 3: Filter out already-processed articles
    print("\n[3/6] Filtering duplicates...")
    new_articles = select_new_articles(all_articles, processed_ids)
    print(f"Found {len(new_articles)} new articles (out of {len(all_articles)} total)")

    if len(new_articles) > GEMINI_MAX_ARTICLES_PER_RUN:
        print(f"Limiting to {GEMINI_MAX_ARTICLES_PER_RUN} articles to respect rate limits")
        new_articles = cap_articles(new_articles, GEMINI_MAX_ARTICLES_PER_RUN)

    if not new_articles:
        print("\nNo new articles to process. Memory left unchanged.")
        return

    # Step 4: One Gemini pass over today's candidates
    print("\n[4/6] Writing a single daily brief with Gemini...")
    initialize_gemini()

    rate_limiter = GeminiRateLimiter(rpm_limit=GEMINI_REQUESTS_PER_MINUTE)
    try:
        brief = summarize_daily_brief(new_articles, rate_limiter)
    except Exception as e:
        print(f"Gemini brief failed: {e}")
        brief = ""

    if not brief:
        print("\nNo brief produced; memory left unchanged.")
        raise SystemExit(1)

    considered_ids = [article["id"] for article in new_articles]

    # Step 5: One Telegram message
    print("\n[5/6] Formatting one Telegram message...")
    telegram_message = f"""AI brief - {datetime.utcnow().strftime('%A, %B %d, %Y')}

{brief}
"""

    print("\n[6/6] Sending to Telegram...")
    sent = send_telegram_message(telegram_message)
    if not sent:
        print("Telegram delivery failed; memory left unchanged so the next run retries.")
        raise SystemExit(1)

    # Save memory only after a successful brief. Mark every candidate we showed
    # Gemini so skipped noise is not re-offered tomorrow.
    print("\nUpdating memory state...")
    processed_ids.update(considered_ids)
    memory["processed_ids"] = list(processed_ids)
    save_memory(memory)

    print("\n" + "=" * 60)
    print("AGENT RUN COMPLETE")
    print("=" * 60)
    print(f"\nConsidered: {len(considered_ids)} new articles")
    print(f"Brief length: {len(brief)} chars")
    print(f"Total in memory: {len(processed_ids)} articles")
    print("\nMemory file updated. Commit and push via GitHub Actions.")


if __name__ == "__main__":
    main()
