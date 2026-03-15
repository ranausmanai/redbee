#!/usr/bin/env python3
"""Build a reusable capabilities snapshot for all repos in a GitHub profile.
Usage:
  python3 tools/github_repo_capabilities.py USERNAME > /tmp/repo_capabilities.json
"""
import json, subprocess, sys
STOPWORDS = {"the","and","for","with","that","this","from","into","your","you","are","not","but","have","has","had","was","were","will","can","its","it","our","their","about","use","using","used","than","then","them","they","his","her","she","him","who","what","when","where","why","how","all","any","more","most","some","such","just","like","also","very","really","lots"}

def tokenize_text(text: str):
    import re
    words = re.findall(r"[a-z0-9][a-z0-9+#.-]{1,}", (text or '').lower())
    out=[]
    for w in words:
        if w in STOPWORDS or w.isdigit() or len(w) < 3:
            continue
        out.append(w)
    return out

def unique_keep_order(items):
    seen=set(); out=[]
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item); out.append(item)
    return out


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

if len(sys.argv) != 2:
    print('usage: github_repo_capabilities.py <github_username>', file=sys.stderr)
    sys.exit(1)

user = sys.argv[1]
r = sh(f"gh repo list {user} --limit 100 --json name,nameWithOwner,description,url,stargazerCount,updatedAt,repositoryTopics")
if r.returncode != 0:
    print(r.stderr.strip(), file=sys.stderr)
    sys.exit(r.returncode)
repos = json.loads(r.stdout or '[]')
out = {"profile": user, "repo_count": len(repos), "generated_from": "gh repo list", "repos": []}
for repo in repos:
    owner_repo = repo.get('nameWithOwner') or f"{user}/{repo['name']}"
    readme = ''
    rr = sh(f"gh api repos/{owner_repo}/readme -H 'Accept: application/vnd.github.raw+json' 2>/dev/null")
    if rr.returncode == 0 and rr.stdout:
        readme = rr.stdout
    bullets = []
    for line in readme.splitlines():
        s = line.strip()
        if s.startswith(('- ', '* ')) and len(s) > 6:
            bullets.append(s[2:140])
        if len(bullets) >= 8:
            break
    out['repos'].append({
        'name': repo['name'],
        'full_name': owner_repo,
        'description': repo.get('description') or '',
        'url': repo.get('url') or f'https://github.com/{owner_repo}',
        'stars': repo.get('stargazerCount', 0),
        'updated_at': repo.get('updatedAt'),
        'topics': repo.get('repositoryTopics') or [],
        'capabilities': bullets,
    })
print(json.dumps(out, indent=2))
