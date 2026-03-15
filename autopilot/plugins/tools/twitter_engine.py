#!/usr/bin/env python3
"""Twitter autopilot engine. Handles all automated Twitter activity:
- Trend-aware original tweets
- Thread creation
- Smart engagement (find + reply to relevant conversations)
- Reply-back (respond to people who engage with you)
- Performance tracking

Usage:
  python3 twitter_engine.py --action morning-tweet
  python3 twitter_engine.py --action engage --count 3
  python3 twitter_engine.py --action reply-back
  python3 twitter_engine.py --action thread --topic "how I built an AI job applier"
  python3 twitter_engine.py --action trending
"""
import argparse
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from config_loader import get_twitter_handle, get_github_username, get_repos_to_promote

MEMORY_DB = Path.home() / ".autopilot" / "memory.db"
HISTORY_FILE = Path.home() / ".autopilot" / "replied_tweets.json"
POSTED_FILE = Path.home() / ".autopilot" / "posted_tweets.json"
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

HANDLE = get_twitter_handle()
GH_PROFILE = get_github_username()

OWN_ACCOUNTS = {HANDLE, GH_PROFILE}

# repos worth talking about — repo hooks are loaded from config, descriptions added here
_REPO_HOOKS = {
    "tinyforge": "a 0.8B model that teaches itself to code. on your laptop. no cloud.",
    "AgentBridge": "makes any API agent-ready in 30 seconds. openapi spec in, MCP server out.",
    "VespeR": "control plane for Claude Code — sessions, agents, patterns, resumable context.",
    "AutoPrompt": "natural selection for prompts. feed it a seed + fitness criteria, get evolved output.",
    "AutoPilot": "autonomous build + growth engine. give it a spec, walk away, come back to a shipped project.",
    "conductor-llm": "routes LLM calls to cheap vs expensive models automatically. save money.",
    "autoship": "describe it, ship it. spec-to-deployed-app in one command.",
    "groking": "terminal coding agent powered by Grok. real file editing, parallel workers.",
}
REPOS = {r: _REPO_HOOKS.get(r, "") for r in get_repos_to_promote() if r in _REPO_HOOKS}

# Dynamic search queries — rotated each run
SEARCH_QUERIES = [
    # high-intent conversations (each must be a single query string for twitter CLI)
    "building an ai agent",
    "built an agent",
    "autonomous ai github",
    "prompt optimization tips",
    "llm cost saving",
    "coding agent terminal",
    "browser automation ai",
    "ai job hunting automation",
    "open source ai tool",
    "self improving ai model",
    "local llm run locally",
    "ai agent framework",
    "claude code workflow",
    "spec to app builder",
    "best ai tools developer",
    "anyone built ai agent",
    "build in public ai",
    "ai side project",
]

VOICE_RULES = """
VOICE RULES (critical — every tweet MUST follow these):
- Write like you are thinking out loud. Not performing. Not teaching. Just... noticing things.
- Plain English. Short sentences. Break ideas into small lines.
- Structure: observation → step by step → interesting outcome → short insight
- Tone: calm, observational, curious. Slightly philosophical when natural. Dry humor.
- Lowercase is fine. Fragments are fine. Rough is fine.
- NEVER sound polished, smooth, or "clever". That is AI slop.
- No emojis, no hashtags, no em dashes (—), no exclamation marks
- No buzzwords: revolutionary, game-changing, cutting-edge, the secret is, the real X is Y
- No compressed LinkedIn wisdom: "most X fail because Y. the secret isn't A, it's B." — that's a bot.
- No "just shipped", "excited to", "love this", "great post", "this is so true"
- Be specific. What broke. What surprised you. What you tried at 2am.
- Under 280 chars for single tweets
- If mentioning a repo, do it at the END, casually. Never lead with it.

GOOD example:
"I tried something odd last night.
A small model trying to fix its own mistakes.
After a few rounds it starts fixing problems it has never seen before.
Maybe small models do not need bigger brains.
Maybe they just need better feedback."

BAD example (AI slop — NEVER write like this):
"most agents fail not because the LLM is bad but because the prompt doesn't tell it what good looks like. the secret isn't a better model, it's a better eval."
"""


def sh(cmd, timeout=60):
    """Run a shell command. cmd can be a string or list."""
    try:
        if isinstance(cmd, list):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"timeout: {str(cmd)[:80]}\n")
        return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'timeout'})()


def ask_llm(prompt, model="sonnet", timeout=120):
    try:
        cmd = ["claude", "-p", prompt, "--no-session-persistence", "--model", model]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:
        sys.stderr.write(f"LLM error: {e}\n")
    return None


def extract_json(text):
    if not text:
        return None
    m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```', text)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    m = re.search(r'(\[[\s\S]*\]|\{[\s\S]*\})', text)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    return None


def load_json(path):
    if path.exists():
        try: return json.loads(path.read_text())
        except: pass
    return {}


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def load_history():
    d = load_json(HISTORY_FILE)
    return set(d.get("ids", []))


def save_history(ids):
    save_json(HISTORY_FILE, {"ids": sorted(ids)[-1000:]})


def load_prefs():
    if not MEMORY_DB.exists():
        return ""
    try:
        db = sqlite3.connect(str(MEMORY_DB))
        rows = db.execute("SELECT key, value FROM memory WHERE category='prefs'").fetchall()
        db.close()
        return "\n".join(f"- {r[0]}: {r[1]}" for r in rows) if rows else ""
    except: return ""


def track_tweet(action, tweet_text, tweet_id=None, metrics=None):
    """Track posted/replied tweets for analytics."""
    if not MEMORY_DB.exists():
        return
    try:
        db = sqlite3.connect(str(MEMORY_DB))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
            category TEXT, key TEXT, value TEXT, ttl_days INTEGER DEFAULT 0)""")
        db.execute(
            "INSERT INTO memory (category, key, value) VALUES (?, ?, ?)",
            ("twitter", f"{action}-{int(time.time())}",
             json.dumps({"text": tweet_text[:200], "id": tweet_id, "metrics": metrics}))
        )
        db.commit()
        db.close()
    except: pass


# ─── Actions ─────────────────────────────────────────────────────────────────

def action_trending():
    """Fetch what's trending in AI/dev Twitter right now."""
    queries = ['"ai agent"', '"open source" ai', '"llm"', '"claude" OR "gpt"']
    trending = []
    for q in queries:
        r = sh(f'twitter search {q} --min-likes 50 -n 5 --json --exclude retweets')
        if r.returncode != 0:
            continue
        try:
            data = json.loads(r.stdout or "{}")
            for t in data.get("data", []):
                m = t.get("metrics", {})
                trending.append({
                    "text": (t.get("text") or "")[:150],
                    "author": (t.get("author") or {}).get("screenName", ""),
                    "likes": m.get("likes", 0),
                    "views": m.get("views", 0),
                })
        except: continue
    trending.sort(key=lambda x: -x.get("likes", 0))
    return trending[:10]


def action_morning_tweet(model="sonnet", post=False):
    """Write and post a trend-aware morning tweet."""
    # get trending topics for context
    trending = action_trending()
    trending_summary = "\n".join(
        f"- @{t['author']}: {t['text']} ({t['likes']} likes)"
        for t in trending[:5]
    ) if trending else "No trending data available."

    # get recent own tweets to avoid repetition
    r = sh(f'twitter user-posts {HANDLE} -n 5 --json')
    recent = ""
    if r.returncode == 0:
        try:
            data = json.loads(r.stdout or "{}")
            recent = "\n".join(
                f"- {t.get('text', '')[:100]}"
                for t in data.get("data", [])[:5]
            )
        except: pass

    prefs = load_prefs()

    prompt = f"""You manage @{HANDLE} on Twitter. Usman is an Engineering Manager & Applied AI Engineer (13+ years) building autonomous AI tools.

His repos: {json.dumps({k: v for k, v in list(REPOS.items())[:4]}, indent=2)}

TRENDING in AI Twitter right now:
{trending_summary}

YOUR RECENT TWEETS (do NOT repeat similar ideas):
{recent}

Write ONE tweet. Strategy — pick one:
1. React to a trending topic with a specific take from YOUR experience building these tools
2. Share something you learned/broke/discovered while building (specific, not generic)
3. Ask a genuine question that devs want to answer
4. Contrarian take on something everyone agrees on

{VOICE_RULES}
{f"User voice preferences:{chr(10)}{prefs}" if prefs else ""}

Return ONLY the tweet text. Nothing else."""

    tweet = ask_llm(prompt, model=model)
    if not tweet:
        print("Failed to generate tweet")
        return

    # clean up any quotes the LLM might wrap it in
    tweet = tweet.strip('"').strip("'").strip()
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    print(f"Tweet: {tweet}")

    if post:
        r = sh(["twitter", "post", tweet])
        if r.returncode == 0:
            # extract tweet ID
            tweet_id = None
            try:
                for line in r.stdout.split("\n"):
                    if "id:" in line and "url" not in line:
                        tweet_id = line.split("id:")[1].strip().strip("'\"")
            except: pass
            track_tweet("morning", tweet, tweet_id)
            print(f"Posted! {r.stdout.strip()[:200]}")
        else:
            print(f"Post failed: {r.stderr[:200]}")
    else:
        print("(dry run — use --post to publish)")


def action_thread(topic, model="sonnet", post=False):
    """Create and post a thread on a specific topic."""
    prefs = load_prefs()

    prompt = f"""Write a Twitter thread (4-6 tweets) about: {topic}

You are @{HANDLE}, Engineering Manager & Applied AI Engineer, building autonomous AI tools.
Your repos: {json.dumps(REPOS, indent=2)}

Thread structure:
- Tweet 1: Hook. Make people stop scrolling. Question, bold claim, or surprising result.
- Tweet 2-4: The meat. Specific details, what you tried, what worked, what didn't. Numbers.
- Tweet 5: What you'd do differently / what's next
- Last tweet: Casual CTA — link to repo if relevant, or ask a question

{VOICE_RULES}
{f"User voice preferences:{chr(10)}{prefs}" if prefs else ""}

Return a JSON array of tweet strings. Use actual newlines inside the strings for line breaks (NOT the literal characters backslash-n).
["tweet 1 text here", "tweet 2 text here", ...]

Each tweet must be under 280 chars. Return ONLY the JSON array."""

    response = ask_llm(prompt, model=model, timeout=180)
    tweets = extract_json(response)

    if not tweets or not isinstance(tweets, list):
        print(f"Failed to generate thread: {response[:200] if response else 'no response'}")
        return

    print(f"Thread ({len(tweets)} tweets):")
    for i, t in enumerate(tweets):
        print(f"  {i+1}. {t}")
    print()

    if post:
        prev_id = None
        for i, tweet_text in enumerate(tweets):
            if prev_id:
                r = sh(["twitter", "post", tweet_text, "--reply-to", prev_id])
            else:
                r = sh(["twitter", "post", tweet_text])

            if r.returncode == 0:
                # extract tweet ID for threading
                try:
                    for line in r.stdout.split("\n"):
                        if "id:" in line and "url" not in line:
                            prev_id = line.split("id:")[1].strip().strip("'\"")
                except: pass
                print(f"  Posted tweet {i+1}")
            else:
                print(f"  Failed tweet {i+1}: {r.stderr[:100]}")
                break
            time.sleep(2)  # don't spam the API

        track_tweet("thread", tweets[0] if tweets else "", prev_id)
        print(f"Thread posted! ({len(tweets)} tweets)")
    else:
        print("(dry run — use --post to publish)")


def action_engage(count=3, model="sonnet", post=False):
    """Find relevant tweets and reply with value."""
    replied_ids = load_history()

    # pick random subset of queries each run
    queries = random.sample(SEARCH_QUERIES, min(6, len(SEARCH_QUERIES)))

    all_tweets = []
    for query in queries:
        r = sh(f'twitter search "{query}" --json -n 10 --exclude retweets')
        if r.returncode != 0:
            continue
        try:
            data = json.loads(r.stdout or "{}")
            for t in data.get("data", []):
                tid = t.get("id")
                if not tid or tid in replied_ids:
                    continue
                author = (t.get("author") or {}).get("screenName", "")
                if author.lower() in OWN_ACCOUNTS:
                    continue
                text = t.get("text", "")
                if len(text) < 40:
                    continue
                m = t.get("metrics", {})
                # prefer tweets with SOME engagement (more likely to be seen)
                engagement = m.get("likes", 0) + m.get("retweets", 0) * 2
                all_tweets.append({
                    "id": tid,
                    "author": author,
                    "text": text,
                    "engagement": engagement,
                    "query": query,
                })
        except: continue

    if not all_tweets:
        print(f"No tweets found (searched {len(queries)} queries, {len(replied_ids)} already replied)")
        return

    # sort by engagement — reply to tweets people actually see
    all_tweets.sort(key=lambda x: -x["engagement"])

    # pick top candidates, dedupe by author
    seen_authors = set()
    candidates = []
    for t in all_tweets:
        if t["author"].lower() in seen_authors:
            continue
        seen_authors.add(t["author"].lower())
        candidates.append(t)
        if len(candidates) >= count * 2:
            break

    # find best repo match for each candidate
    matches = []
    for tweet in candidates[:count]:
        text_lower = tweet["text"].lower()
        best_repo = None
        best_score = 0
        for name, desc in REPOS.items():
            score = 0
            for word in desc.lower().split():
                if len(word) > 3 and word in text_lower:
                    score += 1
            # bonus for specific keyword matches
            for kw in ["agent", "prompt", "evolv", "automat", "autonomous", "local", "cost",
                        "browser", "terminal", "spec", "ship", "deploy", "code", "model"]:
                if kw in text_lower and kw in desc.lower():
                    score += 2
            if score > best_score:
                best_score = score
                best_repo = name

        matches.append({
            "tweet_id": tweet["id"],
            "author": tweet["author"],
            "text": tweet["text"][:200],
            "repo": best_repo if best_score >= 2 else None,
            "repo_desc": REPOS.get(best_repo, "") if best_score >= 2 else None,
            "repo_url": f"https://github.com/{GH_PROFILE}/{best_repo}" if best_score >= 2 else None,
        })

    if not matches:
        print("No good matches found")
        return

    prefs = load_prefs()

    prompt = f"""Write Twitter replies for each tweet below. You are @{HANDLE}, a dev who builds open source AI tools.

TWEETS TO REPLY TO:
{json.dumps(matches, indent=2)}

{VOICE_RULES}
{f"User voice preferences:{chr(10)}{prefs}" if prefs else ""}

CRITICAL:
- ENGAGE WITH THE TWEET FIRST. React, add your experience, share an opinion.
- If a repo is included AND genuinely relevant, mention it casually at the end ("been building something similar: URL"). If it's a stretch, skip the repo entirely.
- Each reply MUST start with @author
- Vary the structure. Don't start every reply the same way.
- Add genuine value. What would make the tweet author want to follow you?

Return ONLY a JSON array of reply strings. One per tweet."""

    response = ask_llm(prompt, model=model)
    replies = extract_json(response)

    if not replies or len(replies) != len(matches):
        print(f"LLM failed to generate replies")
        return

    results = []
    for i, m in enumerate(matches):
        m["reply"] = replies[i]

        if post:
            r = sh(["twitter", "reply", m["tweet_id"], replies[i]])
            if r.returncode == 0:
                replied_ids.add(m["tweet_id"])
                results.append(f"  @{m['author']} -> {m.get('repo', 'no repo')} (posted)")
                results.append(f"    {replies[i]}")
                track_tweet("engage", replies[i], m["tweet_id"])
            else:
                results.append(f"  @{m['author']} -> FAILED")
            time.sleep(3)  # rate limit
        else:
            results.append(f"  @{m['author']} -> {m.get('repo', 'no repo')}")
            results.append(f"    {replies[i]}")

    if post:
        save_history(replied_ids)

    header = f"{'Posted' if post else 'Dry run'}: {len(matches)} replies ({len(replied_ids)} tracked, {len(all_tweets)} scanned)"
    print(header)
    print("\n".join(results))


def action_reply_back(model="sonnet", post=False):
    """Find and reply to people who engaged with your tweets."""
    # search for mentions and replies to us
    r = sh(f'twitter search --to {HANDLE} --json -n 20 --exclude retweets')
    if r.returncode != 0:
        print("Failed to fetch mentions")
        return

    replied_ids = load_history()
    mentions = []
    try:
        data = json.loads(r.stdout or "{}")
        for t in data.get("data", []):
            tid = t.get("id")
            author = (t.get("author") or {}).get("screenName", "")
            if not tid or tid in replied_ids or author.lower() in OWN_ACCOUNTS:
                continue
            mentions.append({
                "id": tid,
                "author": author,
                "text": (t.get("text") or "")[:200],
            })
    except:
        print("Failed to parse mentions")
        return

    if not mentions:
        print(f"No new mentions to reply to ({len(replied_ids)} already handled)")
        return

    # also check replies on our recent tweets
    r2 = sh(f'twitter user-posts {HANDLE} -n 5 --json')
    our_tweet_ids = []
    if r2.returncode == 0:
        try:
            data = json.loads(r2.stdout or "{}")
            our_tweet_ids = [t.get("id") for t in data.get("data", []) if t.get("id")]
        except: pass

    for tweet_id in our_tweet_ids[:3]:
        r3 = sh(f'twitter tweet {tweet_id} --json')
        if r3.returncode != 0:
            continue
        try:
            data = json.loads(r3.stdout or "{}")
            for reply in data.get("replies", []):
                rid = reply.get("id")
                author = (reply.get("author") or {}).get("screenName", "")
                if not rid or rid in replied_ids or author.lower() in OWN_ACCOUNTS:
                    continue
                mentions.append({
                    "id": rid,
                    "author": author,
                    "text": (reply.get("text") or "")[:200],
                    "context": "reply to our tweet",
                })
        except: continue

    if not mentions:
        print("No new replies or mentions")
        return

    prefs = load_prefs()

    prompt = f"""People are engaging with @{HANDLE} on Twitter. Write replies to continue the conversations.

MENTIONS/REPLIES TO RESPOND TO:
{json.dumps(mentions[:5], indent=2)}

{VOICE_RULES}
{f"User voice preferences:{chr(10)}{prefs}" if prefs else ""}

RULES:
- Be conversational. These people engaged with YOU — reward that.
- Answer questions directly. Don't dodge.
- If they're praising your work, be genuine but not cringey. "thanks, yeah X was tricky to get right" > "thanks so much!"
- If they're asking about your tools, give specific answers.
- Keep it short. 1-2 sentences max.
- Start each reply with @author

Return ONLY a JSON array of reply strings."""

    response = ask_llm(prompt, model=model)
    replies = extract_json(response)

    if not replies:
        print("Failed to generate replies")
        return

    results = []
    for i, m in enumerate(mentions[:len(replies)]):
        if post:
            r = sh(["twitter", "reply", m["id"], replies[i]])
            if r.returncode == 0:
                replied_ids.add(m["id"])
                results.append(f"  replied to @{m['author']}: {replies[i][:80]}")
                track_tweet("reply-back", replies[i], m["id"])
            else:
                results.append(f"  FAILED @{m['author']}")
            time.sleep(3)
        else:
            results.append(f"  @{m['author']}: {m['text'][:60]}")
            results.append(f"    -> {replies[i]}")

    if post:
        save_history(replied_ids)

    print(f"{'Posted' if post else 'Dry run'}: {len(results)} reply-backs")
    print("\n".join(results))


def main():
    ap = argparse.ArgumentParser(description="Twitter autopilot engine")
    ap.add_argument("--action", required=True,
                    choices=["morning-tweet", "engage", "reply-back", "thread", "trending"],
                    help="What to do")
    ap.add_argument("--count", type=int, default=3, help="Number of engagements")
    ap.add_argument("--topic", type=str, help="Thread topic")
    ap.add_argument("--model", type=str, default="sonnet", help="Claude model")
    ap.add_argument("--post", action="store_true", help="Actually post (default is dry run)")
    args = ap.parse_args()

    if args.action == "trending":
        trending = action_trending()
        for t in trending:
            print(f"[{t['likes']} likes, {t['views']} views] @{t['author']}: {t['text']}")

    elif args.action == "morning-tweet":
        action_morning_tweet(model=args.model, post=args.post)

    elif args.action == "engage":
        action_engage(count=args.count, model=args.model, post=args.post)

    elif args.action == "reply-back":
        action_reply_back(model=args.model, post=args.post)

    elif args.action == "thread":
        if not args.topic:
            print("--topic required for thread action")
            sys.exit(1)
        action_thread(args.topic, model=args.model, post=args.post)


if __name__ == "__main__":
    main()
