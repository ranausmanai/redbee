<p align="center">
  <h1 align="center">🐝 RedBee</h1>
  <p align="center"><strong>Your AI employee that never sleeps.</strong></p>
  <p align="center">
    <a href="#-quick-start">Quick Start</a> •
    <a href="#-what-it-does">Features</a> •
    <a href="#-how-it-works">Architecture</a> •
    <a href="#-crons">Crons</a> •
    <a href="#-bonus-tools">Bonus Tools</a>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white" />
    <img src="https://img.shields.io/badge/LLM-Claude-orange?logo=anthropic&logoColor=white" />
    <img src="https://img.shields.io/badge/platform-Discord-5865F2?logo=discord&logoColor=white" />
    <img src="https://img.shields.io/github/stars/ranausmanai/redbee?style=social" />
  </p>
</p>

---

RedBee is an **autonomous AI agent** that lives on Discord. Tell it what to do once — it figures out the rest. No hardcoded workflows. The LLM decides what to click, what to type, what to post.

> 💡 **The key idea:** The LLM plans once, deterministic code runs forever. Zero tokens per cron tick unless creative output is needed.

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/ranausmanai/redbee
cd redbee

# 2. Install
pip install discord.py
brew install agent-browser && agent-browser install  # for job applications
# Also need: claude CLI, gh CLI, twitter CLI

# 3. Configure
cp autopilot/plugins/tools/config.example.json autopilot/plugins/tools/config.json
# ✏️ Edit config.json with your profile, resume path, API handles

# 4. Set Discord token
export DISCORD_TOKEN="your-discord-bot-token"

# 5. Launch 🐝
python3 autopilot/plugins/ground_control.py
```

---

## 🎯 What It Does

### 🔍 Job Hunting
Searches **Greenhouse, Remotive, Jobicy, and Hacker News** for matching jobs. Opens real browsers via [agent-browser](https://github.com/nickmilo/agent-browser), reads accessibility trees, fills forms, uploads resumes, and submits applications. **No hardcoded selectors** — the AI reads the page like a human.

### 🐦 Twitter Autopilot
Posts **trend-aware tweets** in your voice. Finds relevant conversations and replies with genuine value. Responds to mentions. Posts weekly threads. All on schedule — all on autopilot.

### 📣 Repo Promotion
Searches Twitter for conversations about problems your repos solve. Replies with context, not spam. **Only promotes repos you whitelist.**

### ⏰ Cron Engine
LLM generates a plan once → deterministic code runs forever. Daily job searches, tweet scheduling, analytics — **zero LLM tokens per tick** unless creative output is needed.

### 🧠 Memory
SQLite-backed memory store. Tracks applications, tweet performance, preferences. The bot **remembers what worked** and adapts.

---

## 🏗️ How It Works

```
📩 Discord message
  → 🤖 Claude (with skill docs as context)
    → ⚡ ACTION: {"type": "job_apply", "url": "..."}
      → 🎮 ground_control.py executes it
        → 🌐 job_applier.py (agent-browser + Claude)
          → ✅ real browser, real form, real submission
```

**One file orchestrator.** Skills are markdown docs that teach the LLM what tools exist. The LLM decides what to do. Ground control executes.

---

## 🧰 Tools

| Tool | Description |
|---|---|
| 🎮 `ground_control.py` | Discord bot brain — LLM actions, crons, memory |
| 🔍 `job_hunter.py` | Searches job boards, scores matches against your profile |
| 📝 `job_applier.py` | Opens browser, navigates forms, fills fields, uploads resume |
| 🐦 `twitter_engine.py` | Tweets, threads, engagement, reply-backs — full autopilot |
| 📣 `twitter_repo_promoter.py` | Finds relevant tweets, replies with repo links |
| 🔧 `config_loader.py` | Loads your profile from `config.json` — zero hardcoded data |

### 🔌 External Dependencies

RedBee doesn't bundle these — it just knows how to use them:

| Tool | Purpose |
|---|---|
| [Claude CLI](https://claude.ai/code) | 🧠 The LLM brain (required) |
| [agent-browser](https://github.com/nickmilo/agent-browser) | 🌐 Browser automation via accessibility trees |
| [twitter CLI](https://github.com/trevorhobenshield/twitter-api-client) | 🐦 Post, search, engage on Twitter/X |
| [gh CLI](https://cli.github.com) | 🐙 GitHub stats, issues, releases |

---

## ⏰ Crons

Set-and-forget automation. Tell RedBee once, it runs forever:

| Cron | Schedule | What It Does |
|---|---|---|
| 🌅 Morning tweet | Daily 9am ET | Trend-aware tweet in your voice |
| 💬 Engagement | Every 3h | Find + reply to relevant conversations |
| 🔄 Reply-back | Every 4h | Respond to people who engage with you |
| 🧵 Weekly thread | Tuesday 10am ET | Auto-generated thread on a trending topic |
| 🔍 Job search | Daily | Search remote AI/ML jobs worldwide |
| 📊 Weekly analytics | Monday 10am ET | GitHub + Twitter performance report |

---

## 🎁 Bonus Tools

Standalone AI tools also included in this repo:

| Tool | What It Does |
|---|---|
| 🧬 **autoevolve** | Evolutionary code optimizer — LLM mutates code against fitness criteria |
| 🚢 **autoship** | Spec-to-deployed-app in one command |
| 🔱 **spawn** | Multi-agent parallel builder — breaks tasks into roles, spawns workers |
| 🤖 **autobot** | Autonomous bot builder |
| 🔌 **autoapi** | API builder from specs |

---

## 🧠 Philosophy

- **📄 One file per tool.** No frameworks. No abstractions. Read any tool top to bottom.
- **🧠 LLM is the brain, not the runtime.** Deterministic code executes. LLM only called for creative decisions.
- **📝 Skills are markdown, not code.** Teaching the LLM costs tokens once, not forever.
- **👁️ Zero hardcoded selectors.** The AI reads the page like a human would.
- **🔒 Config, not code.** All personal data lives in `config.json` (gitignored). Clone and go.

---

## ⭐ Star History

If RedBee saves you time, star the repo — it helps others find it.

[![Star History Chart](https://api.star-history.com/svg?repos=ranausmanai/redbee&type=Date)](https://star-history.com/#ranausmanai/redbee&Date)

---

<p align="center">
  Built with 🐝 by <a href="https://twitter.com/usmanreads">Usman Rana</a>
  <br/>
  <sub>If this is useful, <a href="https://github.com/ranausmanai/redbee">⭐ star it</a> — it means a lot.</sub>
</p>
