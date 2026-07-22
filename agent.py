#!/usr/bin/env python3
"""
Long Horizon, Deep Memory AI News Agent

This script:
1. Fetches AI news from multiple RSS feeds
2. Loads memory (processed IDs) from memory.json
3. Filters out already-processed news items
4. Batches new items to respect Gemini API rate limits
5. Uses Gemini 2.0 Flash Thinking Experimental for high-reasoning summarization
6. Sends an executive summary to Telegram
7. Updates memory.json with new processed IDs
8. Commits and pushes the updated memory back to the repo (done by workflow)

Rate Limit Safeguards (Gemini Free Tier):
- ~15 requests per minute
- ~1,500 requests per day
- We batch news items and use exponential backoff
- We limit total articles processed per run to stay well within daily limits
"""

import os
import json
import time
import hashlib
import feedparser
import requests
from google import genai
from google.genai import types
from datetime import datetime
from typing import List, Dict, Any, Tuple

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

# Keywords to filter Hacker News (since it's general tech)
AI_KEYWORDS = ["AI", "artificial intelligence", "machine learning", "LLM", "neural", "deep learning", 
               "transformer", "generative", "AGI", "inference", "model", "training", "GPT", "Claude", "Gemini"]

# Rate limiting configuration for Gemini Free Tier
# Free tier: ~15 RPM, ~1500 RPD
GEMINI_REQUESTS_PER_MINUTE = 10  # Conservative limit
GEMINI_MAX_ARTICLES_PER_RUN = 50  # Limit articles per run to avoid hitting daily limits
BATCH_SIZE = 5  # Process in small batches

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


def generate_item_id(title: str, link: str, source: str) -> str:
    """Generate a unique ID for a news item based on its content."""
    content = f"{title}|{link}|{source}"
    return hashlib.md5(content.encode()).hexdigest()


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


def filter_ai_news(entries: List[Dict[str, str]], source: str) -> List[Dict[str, str]]:
    """Filter entries for AI-related content (especially for general feeds like HN)."""
    if "hackernews" in source.lower() or "ycombinator" in source.lower():
        filtered = []
        for entry in entries:
            text = f"{entry['title']} {entry['summary']}".lower()
            if any(keyword.lower() in text for keyword in AI_KEYWORDS):
                filtered.append(entry)
        return filtered
    return entries


def get_all_news() -> List[Dict[str, str]]:
    """Fetch news from all RSS feeds."""
    all_entries = []
    
    for feed_url in RSS_FEEDS:
        print(f"Fetching: {feed_url}")
        entries = fetch_rss_feed(feed_url)
        
        # Extract source name from URL
        source = feed_url.split("//")[-1].split("/")[0]
        
        # Filter for AI content
        filtered = filter_ai_news(entries, source)
        all_entries.extend(filtered)
        
        # Small delay between feeds
        time.sleep(1)
    
    # Remove duplicates (same title + link)
    seen = set()
    unique_entries = []
    for entry in all_entries:
        key = f"{entry['title']}|{entry['link']}"
        if key not in seen:
            seen.add(key)
            unique_entries.append(entry)
    
    print(f"Total unique entries fetched: {len(unique_entries)}")
    return unique_entries


# ============================================================================
# GEMINI API WITH RATE LIMITING
# ============================================================================

class GeminiRateLimiter:
    """
    Rate limiter for Gemini API calls.
    
    Free Tier Limits (as of latest documentation):
    - 15 requests per minute (RPM)
    - 1,500 requests per day (RPD)
    - 1 million tokens per minute (TPM)
    
    This class implements:
    - Request counting and timing
    - Exponential backoff on rate limit errors
    - Batch processing to minimize API calls
    """
    
    def __init__(self, rpm_limit: int = GEMINI_REQUESTS_PER_MINUTE):
        self.rpm_limit = rpm_limit
        self.requests_this_minute = 0
        self.minute_start_time = time.time()
        self.total_requests_today = 0
        
    def wait_if_needed(self):
        """Wait if we've hit the per-minute limit."""
        current_time = time.time()
        
        # Reset counter if a minute has passed
        if current_time - self.minute_start_time >= 60:
            self.requests_this_minute = 0
            self.minute_start_time = current_time
        
        # Wait if we're at the limit
        if self.requests_this_minute >= self.rpm_limit:
            wait_time = 60 - (current_time - self.minute_start_time)
            if wait_time > 0:
                print(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
                time.sleep(wait_time)
                self.requests_this_minute = 0
                self.minute_start_time = time.time()
        
        self.requests_this_minute += 1
        self.total_requests_today += 1
    
    def call_with_retry(self, func, max_retries: int = 3):
        """Call a function with exponential backoff on rate limit errors."""
        for attempt in range(max_retries):
            try:
                self.wait_if_needed()
                return func()
            except Exception as e:
                error_msg = str(e).lower()
                if "rate limit" in error_msg or "429" in error_msg:
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 5  # Exponential backoff: 5s, 10s, 20s
                        print(f"Rate limit hit. Retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                    else:
                        raise Exception(f"Rate limit exceeded after {max_retries} retries")
                else:
                    raise


def initialize_gemini():
    """Initialize the Gemini client with the API key."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is not set")

    print(f"Gemini initialized with model: {MODEL_NAME} (thinking: {THINKING_LEVEL})")


def get_gemini_model():
    """
    Get the Gemini model with thinking/reasoning configuration.
    """
    return genai.Client(api_key=GEMINI_API_KEY)


def summarize_news_batch(articles: List[Dict[str, str]], rate_limiter: GeminiRateLimiter) -> str:
    """
    Summarize a batch of news articles using Gemini with high reasoning.
    
    We batch articles together to minimize API calls while staying within
    token limits. Each batch is summarized into a cohesive executive summary.
    """
    if not articles:
        return ""
    
    client = get_gemini_model()
    
    # Format articles for the prompt
    articles_text = "\n\n".join([
        f"Title: {article['title']}\n"
        f"Source: {article['source']}\n"
        f"Link: {article['link']}\n"
        f"Summary: {article['summary'][:300]}"  # Truncate for token efficiency
        for article in articles
    ])
    
    # Craft a prompt that leverages the model's reasoning capabilities
    # The thinking model will automatically engage in deep analysis
    prompt = f"""You are an expert AI research analyst. Analyze the following AI news articles and provide a concise executive summary.

YOUR TASK:
1. Identify the most significant developments across all articles
2. Group related stories into themes
3. Highlight breakthrough moments, major announcements, or paradigm shifts
4. Note any concerning trends or challenges mentioned
5. Provide actionable insights for someone tracking the AI field

NEWS ARTICLES:
{articles_text}

OUTPUT FORMAT:
📊 AI NEWS EXECUTIVE SUMMARY
Date: {datetime.utcnow().strftime('%Y-%m-%d')}

🔑 KEY THEMES (2-3 main topics):
• [Theme 1]
• [Theme 2]

🚀 BREAKTHROUGH DEVELOPMENTS:
• [Most significant announcement/breakthrough]
• [Second most important development]

📈 TRENDING TOPICS:
• [Emerging pattern or trend]

⚠️ NOTABLE CONCERNS/CHALLENGES:
• [Any risks or challenges mentioned]

💡 STRATEGIC INSIGHT:
[One-sentence takeaway for decision-makers]

---
Articles analyzed: {len(articles)}
"""

    def generate_summary():
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                top_p=0.8,
                top_k=40,
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
            ),
        )
        return response.text
    
    # Call with rate limiting and retry logic
    summary = rate_limiter.call_with_retry(generate_summary)
    return summary


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

    def chunk_message(text: str, limit: int = 3500) -> List[str]:
        """Split a long message into Telegram-safe chunks."""
        if len(text) <= limit:
            return [text]

        chunks = []
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

    try:
        for index, chunk in enumerate(chunk_message(message), start=1):
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            if index > 1:
                print(f"Telegram message chunk {index} sent successfully.")
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
    new_articles = []
    for article in all_articles:
        item_id = generate_item_id(article['title'], article['link'], article['source'])
        if item_id not in processed_ids:
            article['id'] = item_id
            new_articles.append(article)
    
    print(f"Found {len(new_articles)} new articles (out of {len(all_articles)} total)")
    
    # Limit articles per run to stay within rate limits
    if len(new_articles) > GEMINI_MAX_ARTICLES_PER_RUN:
        print(f"Limiting to {GEMINI_MAX_ARTICLES_PER_RUN} articles to respect rate limits")
        # Prioritize: take from diverse sources
        new_articles = new_articles[:GEMINI_MAX_ARTICLES_PER_RUN]
    
    if not new_articles:
        print("\n✅ No new articles to process. Exiting.")
        return
    
    # Step 4: Initialize Gemini and process articles
    print("\n[4/6] Initializing Gemini and processing articles...")
    initialize_gemini()
    
    rate_limiter = GeminiRateLimiter()
    
    # Process in batches
    batches = [new_articles[i:i+BATCH_SIZE] for i in range(0, len(new_articles), BATCH_SIZE)]
    summaries = []
    
    for i, batch in enumerate(batches):
        print(f"Processing batch {i+1}/{len(batches)} ({len(batch)} articles)...")
        try:
            summary = summarize_news_batch(batch, rate_limiter)
            if summary:
                summaries.append(summary)
            # Update processed IDs as we go (in case of interruption)
            for article in batch:
                if article['id'] not in processed_ids:
                    processed_ids.add(article['id'])
        except Exception as e:
            print(f"Error processing batch {i+1}: {e}")
            continue
    
    # Step 5: Combine summaries and format final message
    print("\n[5/6] Formatting executive summary...")
    
    if len(summaries) == 1:
        final_summary = summaries[0]
    else:
        # If we had multiple batches, create a meta-summary
        combined = "\n\n---\n\n".join(summaries)
        final_summary = f"""📊 COMPREHENSIVE AI NEWS SUMMARY
Date: {datetime.utcnow().strftime('%Y-%m-%d')}

{combined}

---
📝 Note: Summary compiled from {len(new_articles)} articles across multiple batches.
"""
    
    # Add header with stats
    telegram_message = f"""🤖 Daily AI Intelligence Brief
📅 {datetime.utcnow().strftime('%A, %B %d, %Y')}
⏰ {datetime.utcnow().strftime('%H:%M UTC')}

{final_summary}

---
📊 Articles analyzed: {len(new_articles)}
🧠 Powered by Gemini 2.0 Flash Thinking
🔄 Next update: Tomorrow at 7:30 PM UTC
"""
    
    # Step 6: Send to Telegram
    print("\n[6/6] Sending to Telegram...")
    send_telegram_message(telegram_message)
    
    # Step 7: Save memory
    print("\n💾 Updating memory state...")
    memory["processed_ids"] = list(processed_ids)
    save_memory(memory)
    
    print("\n" + "=" * 60)
    print("✅ AGENT RUN COMPLETE")
    print("=" * 60)
    print(f"\nProcessed: {len(new_articles)} new articles")
    print(f"Total in memory: {len(processed_ids)} articles")
    print(f"API calls made: ~{len(batches)} (within rate limits)")
    print("\nMemory file updated. Commit and push via GitHub Actions.")


if __name__ == "__main__":
    main()
