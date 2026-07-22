# 🤖 Long Horizon AI News Agent

A **serverless, completely free** AI news aggregation agent that runs daily via GitHub Actions, summarizes the latest AI developments using Google's Gemini 2.0 Flash Thinking model, and sends you an executive summary via Telegram at 7:30 PM UTC every day.

## ✨ Features

- **Zero Cost**: Runs entirely on free tiers (GitHub Actions + Gemini Free Tier + Telegram)
- **No Laptop Required**: Fully serverless - runs on GitHub's infrastructure
- **Deep Memory**: Maintains persistent state across runs by storing processed article IDs in the repository
- **High-Reasoning AI**: Uses Gemini 2.0 Flash Thinking Experimental for intelligent analysis
- **Rate Limit Safe**: Implements batching, exponential backoff, and conservative limits to stay within API quotas
- **Daily Schedule**: Automatically runs at 7:30 PM UTC (customizable)
- **Telegram Notifications**: Receive beautifully formatted executive summaries directly in your chat

---

## 📋 Prerequisites

You need the following (all free):

1. **GitHub Account** - For hosting the repository and running Actions
2. **Google Account** - For obtaining a Gemini API key
3. **Telegram Account** - For receiving notifications

---

## 🚀 Step-by-Step Setup

### Step 1: Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow the prompts to name your bot (e.g., "AI News Daily")
4. BotFather will give you a **Bot Token** (looks like: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)
5. **Save this token** - you'll need it later

### Step 2: Get Your Telegram Chat ID

1. Start a conversation with your new bot (click "Start" or send any message)
2. Visit this URL in your browser (replace `YOUR_BOT_TOKEN` with your actual token):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
3. Look for `"chat":{"id":123456789,...}` in the response
4. **Save this number** - this is your Chat ID

Alternatively, add `@userinfobot` as a friend in Telegram and send it any message - it will reply with your Chat ID.

### Step 3: Get Your Google Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Sign in with your Google account
3. Click **"Create API Key"**
4. Select **"Create API key in new project"** (or use existing)
5. Copy the generated API key
6. **Save this key** - you'll need it later

> **Note on Rate Limits (Free Tier):**
> - ~15 requests per minute (RPM)
> - ~1,500 requests per day (RPD)
> - 1 million tokens per minute (TPM)
> 
> This agent is designed to stay well within these limits by batching articles and implementing exponential backoff.

### Step 4: Fork/Clone This Repository

1. Create a new **public** or **private** repository on GitHub (your choice)
2. Clone it locally or upload the files directly:
   - `agent.py` - Main Python script
   - `requirements.txt` - Python dependencies
   - `memory.json` - Persistent memory file (initialized empty)
   - `.github/workflows/daily_news.yml` - GitHub Actions workflow

### Step 5: Configure GitHub Secrets

1. Go to your repository on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **"New repository secret"** and add these three secrets:

| Secret Name | Value |
|-------------|-------|
| `GEMINI_API_KEY` | Your Google Gemini API key from Step 3 |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from Step 1 |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID from Step 2 |

4. Click **"Add secret"** after each one

### Step 6: Initialize the Memory File

The `memory.json` file should already exist with this content:

```json
{
  "processed_ids": [],
  "last_run_date": null
}
```

Make sure this file is committed to your repository.

### Step 7: Enable GitHub Actions

1. Go to your repository's **Actions** tab
2. If prompted, click **"I understand my workflows, go ahead and enable them"**
3. The workflow will automatically run on the next scheduled time (7:30 PM UTC)

### Step 8: Test the Workflow (Optional)

To test immediately without waiting for the scheduled time:

1. Go to **Actions** tab
2. Click on **"Daily AI News Agent"** workflow
3. Click **"Run workflow"** dropdown
4. Click **"Run workflow"** button
5. Wait for the workflow to complete (~2-5 minutes depending on news volume)
6. Check your Telegram for the summary!

---

## 📁 File Structure

```
your-repo/
├── .github/
│   └── workflows/
│       └── daily_news.yml    # GitHub Actions cron job configuration
├── agent.py                  # Main Python script (news fetching, summarization, Telegram)
├── requirements.txt          # Python dependencies
├── memory.json              # Persistent memory (tracked in git)
└── README.md                # This file
```

---

## 🔧 How It Works

### Daily Workflow

1. **7:30 PM UTC**: GitHub Actions triggers the workflow
2. **Checkout**: Repository is cloned to the runner
3. **Setup**: Python 3.11 is installed, dependencies are installed
4. **Fetch News**: Agent pulls from 6 RSS feeds:
   - Hacker News (filtered for AI keywords)
   - ArXiv CS.AI
   - Reddit r/MachineLearning
   - Google AI Blog
   - OpenAI Blog
   - Hugging Face Blog
5. **Filter Duplicates**: Compares against `memory.json` to skip already-processed articles
6. **Batch Processing**: Groups articles into batches of 5 to minimize API calls
7. **AI Summarization**: Sends each batch to Gemini 2.0 Flash Thinking with reasoning-enabled prompts
8. **Rate Limiting**: Implements wait times and exponential backoff if limits are approached
9. **Telegram Dispatch**: Sends formatted executive summary to your chat
10. **Memory Update**: Updates `memory.json` with new article IDs
11. **Git Commit**: Commits and pushes updated memory back to repository

### Deep Memory System

The `memory.json` file tracks:
- `processed_ids`: List of MD5 hashes of all previously processed articles
- `last_run_date`: Timestamp of the last successful run

This ensures:
- No duplicate summaries
- Persistent state across days
- Resumability if a run fails mid-way

### Rate Limit Protection

The agent implements multiple safeguards:

```python
# Conservative limits (below actual free tier caps)
GEMINI_REQUESTS_PER_MINUTE = 10  # Actual limit: ~15
GEMINI_MAX_ARTICLES_PER_RUN = 50  # Prevents excessive daily usage
BATCH_SIZE = 5  # Minimizes total API calls

# Features:
- Request counting and timing
- Automatic waiting when limit approached
- Exponential backoff on 429 errors (5s, 10s, 20s)
- Article limiting per run
```

---

## ⚙️ Customization

### Change the Schedule

Edit `.github/workflows/daily_news.yml`:

```yaml
schedule:
  - cron: '30 19 * * *'  # Currently: 7:30 PM UTC
```

Use [crontab.guru](https://crontab.guru) to generate your desired schedule.

Examples:
- `0 8 * * *` = 8:00 AM UTC daily
- `0 18 * * 1-5` = 6:00 PM UTC weekdays only
- `0 12 * * *` = 12:00 PM UTC daily

### Add More RSS Feeds

Edit `agent.py` and add to the `RSS_FEEDS` list:

```python
RSS_FEEDS = [
    "https://news.ycombinator.com/newest.rss",
    "http://arxiv.org/rss/cs.AI",
    # Add your own:
    "https://your-favorite-ai-blog.com/feed.xml",
]
```

### Adjust Rate Limits

If you have specific needs, modify in `agent.py`:

```python
GEMINI_REQUESTS_PER_MINUTE = 10  # Increase/decrease as needed
GEMINI_MAX_ARTICLES_PER_RUN = 50  # Max articles per day
BATCH_SIZE = 5  # Articles per API call
```

---

## 🐛 Troubleshooting

### Workflow Doesn't Run

- Ensure Actions are enabled in your repository
- Check that the cron syntax is correct
- Remember: GitHub Actions may have up to 10-minute delays on scheduled runs

### No Telegram Message Received

1. Verify all three secrets are correctly set
2. Check that your bot token is valid
3. Ensure you've started a conversation with the bot
4. Confirm your Chat ID is correct (not your username)

### Gemini API Errors

- **"API_KEY_INVALID"**: Double-check your GEMINI_API_KEY secret
- **"Rate limit exceeded"**: The agent should handle this automatically, but if persistent, reduce `GEMINI_MAX_ARTICLES_PER_RUN`
- **"Model not found"**: The model name `gemini-2.0-flash-thinking-exp` may change; check [Google's documentation](https://ai.google.dev/)

### Memory File Not Updating

- Ensure `memory.json` exists in the repository root
- Check that the workflow has push permissions (default for private repos may vary)
- For private repos, you may need to use a Personal Access Token instead of `GITHUB_TOKEN`

### Articles Being Skipped

- This is normal if no new AI news was published since the last run
- Check `memory.json` to see how many articles are tracked
- The agent intentionally limits to 50 articles/run to respect rate limits

---

## 💰 Cost Breakdown

| Service | Plan | Cost |
|---------|------|------|
| GitHub Actions | Free tier (2,000 min/month) | $0 |
| Gemini API | Free tier (1,500 req/day) | $0 |
| Telegram Bot API | Free | $0 |
| **Total** | | **$0.00/month** |

> Note: GitHub Actions free tier includes 2,000 minutes/month for public repos (unlimited for public repos as of 2024). This agent uses ~3-5 minutes per day, well within limits.

---

## 🔒 Security Notes

- **Never commit API keys** to the repository
- All secrets are stored in GitHub Secrets (encrypted)
- The repository can be private for additional security
- Gemini API key has project-level restrictions by default

---

## 📊 Monitoring

To monitor your agent:

1. **GitHub Actions Tab**: View run history, logs, and execution times
2. **memory.json**: Check how many articles have been processed over time
3. **Telegram**: Daily summaries serve as your notification log

---

## 🧠 Model Information

This agent uses **Gemini 2.0 Flash Thinking Experimental** (`gemini-2.0-flash-thinking-exp`), which:

- Automatically engages in multi-step reasoning
- Provides deeper analysis than standard models
- Is optimized for complex analytical tasks
- Is currently available as a free experimental model

The prompt is specifically crafted to leverage the model's reasoning capabilities for:
- Theme identification
- Breakthrough detection
- Trend analysis
- Strategic insights

---

## 📝 License

This project is provided as-is for educational and personal use. Feel free to modify and distribute.

---

## 🙋 Support

If you encounter issues:

1. Check the **Troubleshooting** section above
2. Review GitHub Actions logs for detailed error messages
3. Verify all secrets are correctly configured
4. Ensure your Gemini API key has no project restrictions blocking access

---

**Enjoy your daily AI intelligence brief! 🚀**
