# RedBee

Your AI employee. Runs 24/7 on Discord. Applies to jobs, posts tweets, promotes repos, tracks analytics — while you sleep.

RedBee is an autonomous AI agent powered by Claude. You tell it what to do once, it figures out the rest. No hardcoded workflows. The LLM decides what to click, what to type, what to post.

## What it does

**Job Hunting** — Searches Greenhouse, Remotive, Jobicy, and HN for matching jobs. Opens real browsers via [agent-browser](https://github.com/nickmilo/agent-browser), reads accessibility trees, fills forms, uploads resumes, submits applications. No hardcoded selectors.

**Twitter Autopilot** — Posts trend-aware tweets in your voice. Finds relevant conversations and replies with genuine value. Responds to mentions. Posts weekly threads. All on schedule.

**Repo Promotion** — Searches Twitter for conversations about topics your repos solve. Replies with context, not spam. Only promotes repos you whitelist.

**Cron Engine** — LLM generates a plan once, deterministic code runs forever. Daily job searches, tweet scheduling, analytics — zero LLM tokens per tick unless creative output is needed.

**Memory** — SQLite-backed memory store. Tracks applications, tweet performance, preferences. The bot remembers what worked.

## Architecture

```
Discord message
  → Claude (with skill docs as context)
    → ACTION: {"type": "job_apply", "url": "..."}
      → ground_control.py executes it
        → job_applier.py (agent-browser + Claude)
          → real browser, real form, real submission
```

One file orchestrator. Skills are markdown docs that teach the LLM what tools exist. The LLM decides what to do. Ground control executes.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/ranausmanai/redbee
cd redbee

# 2. Install dependencies
pip install discord.py
brew install agent-browser && agent-browser install  # for job applications
# Also need: claude CLI, gh CLI, twitter CLI

# 3. Configure
cp autopilot/plugins/tools/config.example.json autopilot/plugins/tools/config.json
# Edit config.json with your profile, resume path, API handles

# 4. Set Discord token
export DISCORD_TOKEN="your-discord-bot-token"

# 5. Run
python3 autopilot/plugins/ground_control.py
```

## Tools included

| Tool | What it does |
|---|---|
| `ground_control.py` | Discord bot brain — LLM actions, crons, memory |
| `job_hunter.py` | Searches job boards, scores matches against your profile |
| `job_applier.py` | Opens browser, navigates forms, fills fields, uploads resume |
| `twitter_engine.py` | Tweets, threads, engagement, reply-backs |
| `twitter_repo_promoter.py` | Finds relevant tweets, replies with repo links |

## External tools (bring your own)

RedBee doesn't bundle these — it just knows how to use them:

- **[claude](https://claude.ai/code)** — the LLM brain (required)
- **[agent-browser](https://github.com/nickmilo/agent-browser)** — browser automation via accessibility trees
- **[twitter CLI](https://github.com/trevorhobenshield/twitter-api-client)** — post, search, engage on Twitter/X
- **[gh CLI](https://cli.github.com)** — GitHub stats, issues, releases
- **Reddit** — read-only via public JSON API, no auth needed

## Crons

Set-and-forget automation:

| Cron | Schedule | What |
|---|---|---|
| Morning tweet | Daily 9am ET | Trend-aware tweet in your voice |
| Engagement | Every 3h | Find + reply to relevant conversations |
| Reply-back | Every 4h | Respond to people who engage with you |
| Weekly thread | Tuesday 10am ET | Auto-generated thread on a topic |
| Job search | Daily | Search remote AI/ML jobs |
| Weekly analytics | Monday 10am ET | GitHub + Twitter performance report |

## Also in this repo

Standalone AI tools (not part of RedBee, but useful):

- **autoevolve** — evolutionary code optimizer. LLM mutates code against fitness criteria.
- **autoship** — spec-to-deployed-app in one command
- **spawn** — multi-agent parallel builder
- **AutoPrompt** — natural selection for prompts

## Philosophy

- One file per tool. No frameworks. No abstractions.
- The LLM is the brain, not the runtime. Deterministic code executes, LLM only called for creative decisions.
- Skills are markdown docs, not code. Teaching the LLM costs tokens once, not forever.
- Zero hardcoded selectors. The AI reads the page like a human would.

---

Built by [Usman Rana](https://twitter.com/usmanreads). If this saves you time, star the repo.
