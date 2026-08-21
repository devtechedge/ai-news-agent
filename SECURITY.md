# Security Assessment — AI News Agent

**Date:** 2026-08-21  
**Scope:** Secrets, outbound APIs, Telegram delivery, in-repo memory, supply chain  
**Context:** There is **no public web UI**. The product is a scheduled GitHub Actions job that reads public RSS, calls Gemini, and posts a private Telegram brief.

---

## Executive summary

| Area | Risk | Notes |
|------|------|--------|
| Authentication | **N/A (by design)** | No user login, no cookies, no session |
| Authorization | **N/A** | Single-operator Telegram chat ID |
| XSS / HTML injection | **N/A on public surface** | No HTML renderer ships with the agent. Telegram receives plain text |
| Injection (SQL) | **N/A** | No database. Memory is a JSON file of MD5 hex strings |
| Secrets in repo | **Low** | Keys live in GitHub Actions secrets. `.env` is gitignored |
| Supply chain | **Low** | Three runtime deps, pinned in `requirements.txt` |
| Public memory file | **Accepted** | `memory.json` stores article hashes + last-run ISO timestamp only |
| False-green cron | **Fixed in v1.0.0** | Job now fails (and skips the memory commit) when Gemini or Telegram produce nothing |

**Overall (this repo + Actions):** Low residual risk for a personal digest. The Gemini key and Telegram bot token are the only credentials that matter.

---

## 1. Secrets

Required Actions secrets (never committed):

| Secret | Used for |
|--------|----------|
| `GEMINI_API_KEY` | Google Gemini `generate_content` |
| `TELEGRAM_BOT_TOKEN` | `api.telegram.org/bot<token>/sendMessage` |
| `TELEGRAM_CHAT_ID` | Destination chat |

**Findings**
- `.gitignore` excludes `.env`, `.venv/`, and similar.
- `agent.py` reads secrets only from `os.getenv`.
- Telegram payloads are JSON-bodied (`requests.post(..., json=payload)`), not query-string tokens.

**Do not** paste a bot token into a README screenshot or a workflow `echo`.

---

## 2. Outbound network

The agent only makes outbound HTTPS/HTTP calls:

1. Public RSS feeds (HN / arXiv / Reddit / Google / OpenAI / Hugging Face)
2. Gemini (`google-genai` SDK)
3. Telegram Bot API

There is no inbound HTTP server, webhook, or open port.

RSS HTML is truncated and sent to Gemini as text. It is **not** executed. Telegram receives the model’s plain-text brief, chunked to stay under the Bot API limit.

---

## 3. Memory file (`memory.json`)

Public JSON of the form:

```json
{
  "processed_ids": ["<md5 hex>"],
  "last_run_date": "<ISO-8601>"
}
```

IDs are `md5(title|link|source)` — not URLs, not titles, not prompts, not API keys.

**v1.0.0 behaviour:** `processed_ids` is updated only after a non-empty Gemini summary **and** a successful Telegram send. Empty last-run bumps with a still-empty ID list (the previous false-green pattern) are no longer committed.

---

## 4. GitHub Actions

| Workflow | What it is |
|----------|------------|
| `daily_news.yml` | **Product cron** — 19:30 UTC + `workflow_dispatch`. Needs `contents: write` to commit `memory.json` |
| `ci.yml` | **Engineering CI** — `compileall` + pytest. No secrets. Does not call Gemini or Telegram |

`daily_news.yml` used to `exit 0` on failure. That step now fails the job. CI does not run the agent (it would spend the Gemini quota and require Telegram secrets).

The `GITHUB_TOKEN` used to push `memory.json` is the default Actions token with `contents: write` only.

---

## 5. Dependency / supply chain

Runtime (`requirements.txt`):

- `feedparser==6.0.10`
- `google-genai==1.30.0`
- `requests==2.32.3`

All three are imported. Nothing was deleted as unused template leftover (this is not a Next.js dump).

Dev: `pytest==8.3.5` via `requirements-dev.txt`.

Re-audit:

```bash
pip install -r requirements.txt
pip install pip-audit
pip-audit -r requirements.txt
```

Dependabot (weekly pip + GitHub Actions, patch/minor only, majors ignored) is enabled.

---

## 6. Residual risk (accepted)

- Gemini and Telegram trust is the operator’s Google / Telegram accounts.
- A leaked `TELEGRAM_BOT_TOKEN` can post to the configured chat until the token is revoked in BotFather.
- Public RSS is untrusted text. The model may echo prompt injection from a feed item into the Telegram brief. Impact is limited to that private chat.
- `memory.json` is world-readable on a public repo. Hashes only.

**Not accepted**
- Committing `.env` or printing secrets in Actions logs.
- Marking a failed Gemini/Telegram run as success.

---

## 7. How to re-test

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q agent.py helpers.py tests
python -m pytest
```
