#!/usr/bin/env python3
"""autoapi — give it a URL, get back an API.

Usage:
  python3 autoapi.py https://example.com
  python3 autoapi.py https://example.com -o my_api
  python3 autoapi.py https://example.com -e codex --reasoning low
"""

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

PROGRAM = Path(__file__).with_name("program.md")
CODEX_REASONING_DEFAULT = "medium"


def die(message):
    raise SystemExit(message)


class Progress:
    def __init__(self, total):
        self.total = total
        self.current = 0

    def stage(self, name):
        self.current += 1
        print(f"  [{self.current}/{self.total}] {name}...", end=" ", flush=True)

    def done(self, detail="done"):
        print(detail, flush=True)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or f"api-{int(time.time())}")[:48]


def run(cmd, *, cwd=None, env=None, capture=False, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=capture, check=check)


def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for left, right in (("{", "}"), ("[", "]")):
        start = text.find(left)
        end = text.rfind(right) + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No JSON found in: {text[:300]}")


def llm(prompt, engine, workdir, reasoning="medium"):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    if engine == "claude":
        result = run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            cwd=str(workdir.resolve()), env=env, capture=True, check=False,
        )
    else:
        result = run(
            ["codex", "exec", "-c", f'model_reasoning_effort="{reasoning}"', prompt],
            cwd=str(workdir.resolve()), env=env, capture=True, check=False,
        )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail[:800] or f"{engine} failed")
    return result.stdout.strip()


def run_agent(engine, prompt, outdir, reasoning="medium"):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    if engine == "claude":
        cmd = [
            "claude", "-p", prompt,
            "--allowedTools", "Bash,Write,Read,Edit,Glob,Grep",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
        ]
    else:
        cmd = ["codex", "exec", "--full-auto", "-c",
               f'model_reasoning_effort="{reasoning}"', prompt]

    result = run(cmd, cwd=str(outdir.resolve()), env=env, check=False, capture=True)
    return result.returncode == 0


def fetch_page(url):
    """Fetch HTML content from a URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            encoding = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(encoding, errors="replace")
    except urllib.error.HTTPError as e:
        die(f"Failed to fetch {url}: HTTP {e.code}")
    except urllib.error.URLError as e:
        die(f"Failed to fetch {url}: {e.reason}")


def strip_noise(html):
    """Remove scripts, styles, comments, and excess whitespace from HTML."""
    # remove script/style blocks
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # remove HTML comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    # remove SVG blocks
    html = re.sub(r'<svg[^>]*>.*?</svg>', '<svg/>', html, flags=re.DOTALL | re.IGNORECASE)
    # collapse whitespace
    html = re.sub(r'\n\s*\n', '\n', html)
    html = re.sub(r'  +', ' ', html)
    return html.strip()


def truncate_html(html, max_chars=12000):
    """Strip noise and truncate HTML to fit in LLM context."""
    html = strip_noise(html)
    if len(html) <= max_chars:
        return html
    return html[:max_chars] + "\n<!-- TRUNCATED -->"


def init_git(outdir):
    if not (outdir / ".git").exists():
        run(["git", "init"], cwd=str(outdir.resolve()), capture=True)


def plan_api(url, html, engine, workdir, reasoning):
    """LLM analyzes the page and plans what endpoints to create."""
    truncated = truncate_html(html)

    prompt = f"""Analyze this webpage and plan a REST API that exposes its data as JSON.

URL: {url}

HTML (may be truncated):
```
{truncated}
```

Return ONLY valid JSON:
{{
  "site_name": "human readable name",
  "description": "what data this site has",
  "data_patterns": ["list of data types found, e.g. 'product listings', 'weather forecasts'"],
  "endpoints": [
    {{
      "path": "/api/items",
      "method": "GET",
      "description": "what this endpoint returns",
      "fields": ["field1", "field2"]
    }}
  ],
  "scrape_strategy": "how to extract the data (CSS selectors, patterns, etc.)",
  "needs_pagination": true,
  "base_url": "{url}"
}}

Rules:
- Design 2-5 useful REST endpoints based on the actual data visible on the page
- Each endpoint should return structured JSON with real fields from the page
- Be specific about CSS selectors or HTML patterns for extraction
- Return JSON only, no markdown"""

    return extract_json(llm(prompt, engine, workdir, reasoning))


def build_prompt(program, url, html, plan):
    truncated = truncate_html(html, max_chars=15000)
    endpoints = json.dumps(plan.get("endpoints", []), indent=2)
    strategy = plan.get("scrape_strategy", "")

    return f"""{program}

Build a complete API server that scrapes data from a website and serves it as clean JSON.

TARGET SITE
- URL: {url}
- Description: {plan.get('description', '')}

SAMPLE HTML (for understanding structure):
```
{truncated}
```

PLANNED ENDPOINTS
{endpoints}

SCRAPE STRATEGY
{strategy}

REQUIREMENTS
- Use Python with FastAPI (pip install fastapi uvicorn httpx beautifulsoup4 lxml)
- The API must actually fetch and parse the live website when called
- Each endpoint returns clean JSON — no HTML, no raw scrape dumps
- Add proper error handling — if the source site is down, return appropriate error
- Add a GET /api/health endpoint that returns {{"status": "ok"}}
- Add a GET / root endpoint that returns API documentation as JSON (list of available endpoints with descriptions)
- Add caching: cache responses for 5 minutes to avoid hammering the source site
- Include a requirements.txt
- Include a README.md with: what site it scrapes, available endpoints, how to run
- The server should run on 0.0.0.0:8000

Write all files. Install deps. Test that the server starts and at least /api/health responds.
Fix any errors. When done, print EXACTLY: "AUTOAPI COMPLETE"."""


def verify_build(outdir):
    """Check if the build produced required files."""
    required = ["requirements.txt", "README.md"]
    missing = [f for f in required if not list(outdir.glob(f"**/{f}"))]
    # check for at least one .py file
    py_files = list(outdir.glob("**/*.py"))
    if not py_files:
        missing.append("*.py (no Python files)")
    return missing


def build_with_retries(engine, prompt, outdir, max_attempts=3, progress=None, reasoning="medium"):
    for attempt in range(1, max_attempts + 1):
        ok = run_agent(engine, prompt, outdir, reasoning)
        missing = verify_build(outdir)

        if ok and not missing:
            return True

        if attempt == max_attempts:
            if missing:
                print(f"\n  warning: missing after {max_attempts} attempts: {', '.join(missing)}", flush=True)
            return ok and not missing

        if progress:
            print(f"\n  [{progress.current}/{progress.total}] retrying ({attempt}/{max_attempts})...", end=" ", flush=True)

        fix_parts = ["The previous build attempt had issues."]
        if missing:
            fix_parts.append(f"Missing: {', '.join(missing)}")
        if not ok:
            fix_parts.append("The agent exited with an error.")
        fix_parts.append("Read existing files, fix issues, finish the build.")
        fix_parts.append('When done, print EXACTLY: "AUTOAPI COMPLETE".')
        prompt = "\n".join(fix_parts)

    return False


def main():
    p = argparse.ArgumentParser(description="autoapi — give it a URL, get back an API")
    p.add_argument("url", help="URL of the website to turn into an API")
    p.add_argument("-o", "--output", default=None, help="output directory")
    p.add_argument("-e", "--engine", default="claude", choices=["claude", "codex"])
    p.add_argument("--reasoning", default="medium", choices=["low", "medium", "high"],
                   help="codex reasoning effort (default: medium)")
    args = p.parse_args()

    url = args.url
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    slug = slugify(url.split("//")[-1].split("/")[0].replace("www.", ""))
    outdir = Path(args.output or f"api_{slug}")
    outdir.mkdir(exist_ok=True)
    init_git(outdir)

    program = PROGRAM.read_text() if PROGRAM.exists() else ""
    progress = Progress(3)

    print(f"""
============================================================
  autoapi — give it a URL, get back an API
============================================================
  url:     {url}
  engine:  {args.engine}
  output:  {outdir}/
============================================================
""")

    # Stage 1: Fetch
    progress.stage("Fetching page")
    html = fetch_page(url)
    progress.done(f"{len(html)} chars")

    # Stage 2: Plan
    progress.stage("Planning API")
    plan = plan_api(url, html, args.engine, outdir, args.reasoning)
    (outdir / "autoapi.plan.json").write_text(json.dumps(plan, indent=2) + "\n")

    endpoints = plan.get("endpoints", [])
    endpoint_summary = ", ".join(e.get("path", "?") for e in endpoints)
    progress.done(f"{len(endpoints)} endpoints")
    print(f"           routes: {endpoint_summary}")
    print()

    # Stage 3: Build
    progress.stage("Building API")
    prompt = build_prompt(program, url, html, plan)
    if not build_with_retries(args.engine, prompt, outdir, progress=progress, reasoning=args.reasoning):
        die("Build failed after retries.")
    progress.done()

    print(f"""
============================================================
  API READY -> {outdir}/
============================================================
  endpoints: {endpoint_summary}
  run:       cd {outdir} && pip install -r requirements.txt && python3 main.py
  then:      curl http://localhost:8000/
============================================================
""")


if __name__ == "__main__":
    main()
