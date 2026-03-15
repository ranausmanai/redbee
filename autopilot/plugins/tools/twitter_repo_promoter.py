#!/usr/bin/env python3
"""Find relevant tweets and reply with personalized repo recommendations.
Uses keyword matching to find tweets, then 1 cheap LLM call to write natural replies.

Usage:
  python3 tools/twitter_repo_promoter.py --profile USERNAME --count 2
  python3 tools/twitter_repo_promoter.py --profile USERNAME --count 2 --post
  python3 tools/twitter_repo_promoter.py --profile USERNAME --count 2 --model haiku
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HISTORY_FILE = Path.home() / ".autopilot" / "replied_tweets.json"
MEMORY_DB = Path.home() / ".autopilot" / "memory.db"
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

STOPWORDS = {
    "the","and","for","with","that","this","from","into","your","you","are","not","but","have",
    "has","had","was","were","will","can","its","it","our","their","about","use","using","used",
    "than","then","them","they","his","her","she","him","who","what","when","where","why","how",
    "all","any","more","most","some","such","just","like","also","very","really","lots","build",
    "building","built","repo","github","open","source","project","projects","looking","connect",
    "people","drop","working","hey","want","need","make","made","get","got","new","way","try"
}
SEARCH_QUERIES = [
    '"build in public" ai github',
    'ai agent github repo',
    'open source ai tools',
    'evolutionary algorithm code optimization',
    'autonomous coding agent',
    'prompt engineering optimization tool',
]
from config_loader import get_twitter_handle, get_github_username, get_repos_to_promote

OWN_ACCOUNTS = {get_twitter_handle(), get_github_username()}
PROMOTED_REPOS = set(get_repos_to_promote())

REPO_CACHE = Path.home() / ".autopilot" / "repo_cache.json"
CACHE_TTL = 86400  # 24 hours


def sh(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9+#.-]{1,}", (text or "").lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()]


def load_history():
    if HISTORY_FILE.exists():
        try:
            return set(json.loads(HISTORY_FILE.read_text()).get("ids", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def save_history(ids):
    trimmed = sorted(ids)[-500:]
    HISTORY_FILE.write_text(json.dumps({"ids": trimmed}, indent=2))


def load_prefs():
    """Read voice/style prefs from memory DB."""
    if not MEMORY_DB.exists():
        return ""
    try:
        db = sqlite3.connect(str(MEMORY_DB))
        rows = db.execute("SELECT key, value FROM memory WHERE category='prefs'").fetchall()
        db.close()
        if rows:
            return "\n".join(f"- {r[0]}: {r[1]}" for r in rows)
    except Exception:
        pass
    return ""


def fetch_repos(profile):
    """Fetch repos from GitHub API, cached for 24h."""
    if REPO_CACHE.exists():
        try:
            cache = json.loads(REPO_CACHE.read_text())
            if cache.get("profile") == profile and (time.time() - cache.get("ts", 0)) < CACHE_TTL:
                return cache.get("repos", [])
        except (json.JSONDecodeError, KeyError):
            pass

    cmd = f'gh api users/{profile}/repos --paginate --jq \'[.[] | select(.fork == false) | {{name: .name, description: .description, url: .html_url, stars: .stargazers_count, topics: .topics, language: .language}}]\''
    r = sh(cmd)
    if r.returncode != 0:
        sys.stderr.write(f"gh api failed: {r.stderr}\n")
        if REPO_CACHE.exists():
            try:
                return json.loads(REPO_CACHE.read_text()).get("repos", [])
            except Exception:
                pass
        return []
    try:
        repos = json.loads(r.stdout or "[]")
        repos = [r for r in repos if r.get("description") and r.get("name") in PROMOTED_REPOS]
        REPO_CACHE.write_text(json.dumps({"profile": profile, "ts": time.time(), "repos": repos}))
        return repos
    except json.JSONDecodeError:
        return []


def repo_terms(repo):
    parts = [
        repo.get("name") or "",
        repo.get("description") or "",
        " ".join(repo.get("topics") or []),
        repo.get("language") or "",
    ]
    return set(tokenize(" ".join(parts)))


def score_match(tweet_text, repo):
    tweet_tokens = set(tokenize(tweet_text))
    terms = repo_terms(repo)
    shared = sorted(tweet_tokens & terms)
    score = len(shared)
    desc = (repo.get("description") or "").lower()
    text = (tweet_text or "").lower()
    for kw in ("agent", "prompt", "automat", "evolv", "optim", "spawn", "autonomous"):
        if kw in text and kw in desc:
            score += 2
    return score, shared[:6]


def collect_tweets(count, replied_ids):
    seen = {}
    for query in SEARCH_QUERIES:
        cmd = f'twitter search {json.dumps(query)} --json -n {max(count * 3, 8)}'
        try:
            r = sh(cmd)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode != 0:
            continue
        try:
            payload = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            continue
        for tweet in payload.get("data") or []:
            tid = tweet.get("id")
            if not tid or tid in replied_ids or tid in seen:
                continue
            author = (tweet.get("author") or {}).get("screenName", "")
            if author.lower() in OWN_ACCOUNTS:
                continue
            if tweet.get("isRetweet"):
                continue
            text = tweet.get("text") or ""
            if len(text.strip()) < 40:
                continue
            seen[tid] = tweet
    return list(seen.values())


def generate_replies(matches, prefs, model="haiku"):
    """One LLM call to write all replies. ~300-500 tokens with haiku."""
    pairs = []
    for m in matches:
        pairs.append({
            "author": m["author"],
            "tweet": m["tweet_preview"],
            "repo": m["repo"],
            "repo_desc": m["repo_desc"],
            "repo_url": m["repo_url"],
        })

    prompt = f"""Write Twitter replies for each tweet below. You are a dev who builds open source tools.

CRITICAL RULES:
- ENGAGE WITH THE TWEET FIRST. React to what they're saying. Add your own take, opinion, or experience.
- Your reply should make sense even WITHOUT mentioning your repo. The value is in the conversation, not the link.
- Only mention your repo if it's genuinely, directly relevant. If the connection is loose, just have a good conversation and skip the repo link entirely.
- DO NOT start with "we built X" or "check out X" or any variation. That's spam.
- If you mention a repo, do it at the end, casually, like "fwiw been working on something similar" with the URL. Never lead with it.
- Keep each reply under 260 chars
- No em dashes, no hashtags, no emojis, no "love this", no "great post"
- Sound like a real person having a real conversation
- Each reply must be different in structure
{f"User voice preferences:{chr(10)}{prefs}" if prefs else ""}

For each tweet, you get the tweet text AND a potentially relevant repo. Judge for yourself if the repo is actually relevant enough to mention. If not, just write a good engaging reply without it.

Tweets:
{json.dumps(pairs, indent=2)}

Respond with ONLY a JSON array of reply strings, one per tweet. No markdown, no explanation.
Example: ["@user1 reply text here", "@user2 reply text here"]"""

    try:
        cmd = ["claude", "-p", prompt, "--no-session-persistence", "--model", model]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None
        # parse JSON from output (might have extra text around it)
        output = r.stdout.strip()
        # find the JSON array in the output
        start = output.find("[")
        end = output.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(output[start:end])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        sys.stderr.write(f"LLM reply generation failed: {e}\n")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--min-score", type=int, default=3, help="Minimum overlap score to reply")
    ap.add_argument("--model", type=str, default="haiku", help="Claude model for replies (haiku, sonnet, opus)")
    args = ap.parse_args()

    repos = fetch_repos(args.profile)
    if not repos:
        print("No repos found for profile")
        sys.exit(1)

    replied_ids = load_history()
    tweets = collect_tweets(args.count, replied_ids)

    matches = []
    used_repos = set()
    for tweet in tweets:
        best = None
        for repo in repos:
            score, shared = score_match(tweet.get("text", ""), repo)
            if score < args.min_score:
                continue
            repo_name = repo.get("name", "")
            if repo_name in used_repos:
                continue
            if not best or score > best[0]:
                best = (score, shared, repo)
        if not best:
            continue
        score, shared, repo = best
        used_repos.add(repo.get("name", ""))
        matches.append({
            "tweet_id": tweet.get("id"),
            "author": (tweet.get("author") or {}).get("screenName"),
            "tweet_preview": (tweet.get("text") or "")[:150],
            "repo": repo.get("name"),
            "repo_desc": (repo.get("description") or ""),
            "repo_url": repo.get("url") or "",
            "score": score,
        })
        if len(matches) >= args.count:
            break

    if not matches:
        print(f"Scanned {len(tweets)} tweets, no good matches (skipped {len(replied_ids)} dupes)")
        return

    # generate replies with LLM
    prefs = load_prefs()
    replies = generate_replies(matches, prefs, model=args.model)

    if replies and len(replies) == len(matches):
        for i, m in enumerate(matches):
            m["reply"] = replies[i]
    else:
        # fallback: simple template if LLM fails
        sys.stderr.write("LLM failed, using fallback templates\n")
        for m in matches:
            m["reply"] = f"@{m['author']} been building {m['repo']} for this. {m['repo_url']}"

    if args.post:
        posted = 0
        failed = 0
        for item in matches:
            try:
                r = subprocess.run(
                    ["twitter", "reply", item['tweet_id'], item['reply']],
                    capture_output=True, text=True, timeout=60
                )
                if r.returncode == 0:
                    replied_ids.add(item["tweet_id"])
                    posted += 1
                else:
                    failed += 1
            except subprocess.TimeoutExpired:
                failed += 1
        save_history(replied_ids)

        lines = [f"Posted {posted}/{len(matches)} replies ({len(replied_ids)} tracked, {len(tweets)} scanned, model: {args.model})"]
        for item in matches:
            lines.append(f"  @{item['author']} -> {item['repo']} (score:{item['score']})")
            lines.append(f"    {item['reply']}")
        print("\n".join(lines))
    else:
        lines = [f"Dry run: {len(matches)} matches from {len(tweets)} tweets ({len(replied_ids)} dupes skipped, model: {args.model})"]
        for item in matches:
            lines.append(f"  @{item['author']} -> {item['repo']} (score:{item['score']})")
            lines.append(f"    {item['reply']}")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
