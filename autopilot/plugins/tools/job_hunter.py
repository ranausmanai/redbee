#!/usr/bin/env python3
"""Auto job hunter. Searches multiple free job APIs, matches to profile, drafts cover letters.

Usage:
  python3 tools/job_hunter.py --search                    # find matching jobs
  python3 tools/job_hunter.py --search --apply             # find + auto-apply where possible
  python3 tools/job_hunter.py --search --keywords "LLM engineer remote"
  python3 tools/job_hunter.py --cover-letter JOB_URL       # draft a cover letter for a specific job
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from config_loader import get_profile, get_resume_path

# ─── Config ──────────────────────────────────────────────────────────────────

RESUME_PATH = get_resume_path()
MEMORY_DB = Path.home() / ".autopilot" / "memory.db"
APPLIED_FILE = Path.home() / ".autopilot" / "applied_jobs.json"
APPLIED_FILE.parent.mkdir(parents=True, exist_ok=True)

_p = get_profile()
PROFILE = {
    "name": _p["full_name"],
    "email": _p["email"],
    "title": _p["current_title"],
    "years": int(_p["years_experience"]),
    "location": _p["location"],
    "linkedin": _p["linkedin"].replace("https://", ""),
    "github": _p["github"].replace("https://", ""),
    "skills": [
        "applied ai", "llm", "agentic workflows", "predictive modeling", "nlp",
        "mlops", "gcp", "vertex ai", "bigquery", "cloud run", "azure ml",
        "python", "sql", "spark", "tensorflow", "langfuse",
        "team building", "roadmapping", "ai governance",
    ],
    "titles_wanted": [
        "ai engineer", "ml engineer", "machine learning engineer",
        "applied ai", "llm engineer", "ai lead", "ai manager",
        "engineering manager ai", "engineering manager ml",
        "data science lead", "mlops engineer", "ai architect",
        "head of ai", "director of ai", "staff ml engineer",
        "senior ai engineer", "senior ml engineer",
        "applied scientist", "research engineer",
    ],
    "keywords": [
        "ai", "ml", "machine learning", "deep learning", "llm",
        "nlp", "natural language", "computer vision", "mlops",
        "data science", "predictive", "agentic", "autonomous",
        "tensorflow", "pytorch", "python", "gcp", "vertex",
    ],
}

SEARCH_QUERIES = [
    "AI Engineer remote",
    "Machine Learning Engineer remote",
    "Applied AI Engineer remote",
    "LLM Engineer remote",
    "Engineering Manager AI remote",
    "MLOps Engineer remote",
    "AI Lead remote",
]


# ─── Job Sources ─────────────────────────────────────────────────────────────

def fetch_remotive(query="artificial-intelligence"):
    """Remotive.com - free API, no auth, remote jobs only."""
    url = f"https://remotive.com/api/remote-jobs?category={query}&limit=50"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JobHunter/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        jobs = []
        for j in data.get("jobs", []):
            jobs.append({
                "source": "remotive",
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "url": j.get("url", ""),
                "location": j.get("candidate_required_location", "Anywhere"),
                "salary": j.get("salary", ""),
                "description": clean_html(j.get("description", ""))[:500],
                "date": j.get("publication_date", ""),
                "tags": j.get("tags", []),
            })
        return jobs
    except Exception as e:
        sys.stderr.write(f"remotive error: {e}\n")
        return []


def fetch_jobicy():
    """Jobicy.com - free API, remote jobs."""
    url = "https://jobicy.com/api/v2/remote-jobs?count=50&tag=ai,machine-learning,data-science"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JobHunter/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        jobs = []
        for j in data.get("jobs", []):
            jobs.append({
                "source": "jobicy",
                "title": j.get("jobTitle", ""),
                "company": j.get("companyName", ""),
                "url": j.get("url", ""),
                "location": j.get("jobGeo", "Remote"),
                "salary": f"{j.get('annualSalaryMin', '')}-{j.get('annualSalaryMax', '')}",
                "description": clean_html(j.get("jobDescription", ""))[:500],
                "date": j.get("pubDate", ""),
                "tags": [],
            })
        return jobs
    except Exception as e:
        sys.stderr.write(f"jobicy error: {e}\n")
        return []


def fetch_hn_whoishiring():
    """HN Who is Hiring - monthly thread, free API."""
    # find the latest "Who is hiring?" thread
    search_url = "https://hn.algolia.com/api/v1/search_by_date?query=%22who%20is%20hiring%22&tags=story&hitsPerPage=1"
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "JobHunter/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        hits = data.get("hits", [])
        if not hits:
            return []
        story_id = hits[0].get("objectID")

        # get comments (job postings)
        comments_url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=200"
        req = urllib.request.Request(comments_url, headers={"User-Agent": "JobHunter/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        jobs = []
        for c in data.get("hits", []):
            text = clean_html(c.get("comment_text", ""))
            if not text or len(text) < 50:
                continue
            # extract company name (usually first line or pipe-separated)
            first_line = text.split("\n")[0]
            company = first_line.split("|")[0].strip()[:60]
            # check if it mentions remote
            text_lower = text.lower()
            is_remote = any(w in text_lower for w in ["remote", "anywhere", "worldwide", "global"])
            if not is_remote:
                continue
            jobs.append({
                "source": "hn",
                "title": first_line[:100],
                "company": company,
                "url": f"https://news.ycombinator.com/item?id={c.get('objectID', '')}",
                "location": "Remote",
                "salary": "",
                "description": text[:500],
                "date": c.get("created_at", ""),
                "tags": [],
            })
        return jobs
    except Exception as e:
        sys.stderr.write(f"hn error: {e}\n")
        return []


def fetch_greenhouse_boards():
    """Search known tech companies' Greenhouse boards for AI/ML roles."""
    companies = [
        # big tech with remote AI roles
        "airbnb", "stripe", "figma", "notion", "databricks",
        "datadog", "hashicorp", "cloudflare", "elastic",
        "duolingo", "instacart", "doordash", "reddit",
        # AI-native companies
        "anthropic", "openai", "cohere", "huggingface", "scale",
        "weights-and-biases", "anyscale", "modal", "replicate",
        # remote-first companies
        "gitlab", "mozilla", "automattic", "canonical", "zapier",
        "duckduckgo", "webflow", "fly", "posthog", "cal-com",
        # dev tools / infra
        "vercel", "supabase", "grafana-labs", "sentry",
        "confluent", "cockroachlabs", "timescale",
        # fintech / healthtech with AI
        "brex", "ramp", "plaid", "navan",
    ]
    jobs = []
    for company in companies:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JobHunter/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            for j in data.get("jobs", []):
                title = j.get("title", "").lower()
                # only AI/ML related titles
                if any(kw in title for kw in ["ai", "ml", "machine learning", "data scien",
                                                "llm", "nlp", "deep learning", "applied scien"]):
                    loc = ""
                    if j.get("location", {}).get("name"):
                        loc = j["location"]["name"]
                    # use direct greenhouse URL (goes straight to form, no wrapper page)
                    gh_id = j.get("id", "")
                    direct_url = f"https://job-boards.greenhouse.io/{company}/jobs/{gh_id}" if gh_id else j.get("absolute_url", "")
                    jobs.append({
                        "source": f"greenhouse:{company}",
                        "title": j.get("title", ""),
                        "company": company.replace("-", " ").title(),
                        "url": direct_url,
                        "location": loc,
                        "salary": "",
                        "description": clean_html(j.get("content", ""))[:500],
                        "date": j.get("updated_at", ""),
                        "tags": [],
                        "gh_job_id": gh_id,
                        "gh_board": company,
                    })
        except Exception:
            continue
    return jobs


# ─── Helpers ─────────────────────────────────────────────────────────────────

def clean_html(text):
    """Strip HTML tags."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_applied():
    if APPLIED_FILE.exists():
        try:
            return json.loads(APPLIED_FILE.read_text())
        except Exception:
            pass
    return {"applied": [], "skipped": []}


def save_applied(data):
    APPLIED_FILE.write_text(json.dumps(data, indent=2))


def load_prefs():
    """Read voice/style prefs from memory DB."""
    if not MEMORY_DB.exists():
        return ""
    try:
        db = sqlite3.connect(str(MEMORY_DB))
        rows = db.execute("SELECT key, value FROM memory WHERE category='prefs'").fetchall()
        db.close()
        return "\n".join(f"- {r[0]}: {r[1]}" for r in rows) if rows else ""
    except Exception:
        return ""


def score_job(job):
    """Score how well a job matches the profile. Higher = better match."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    company = (job.get("company") or "").lower()
    text = f"{title} {desc}"

    score = 0

    # title match (most important)
    for wanted in PROFILE["titles_wanted"]:
        if wanted in title:
            score += 10
            break

    # keyword matches in description
    for kw in PROFILE["keywords"]:
        if kw in text:
            score += 1

    # skill matches
    for skill in PROFILE["skills"]:
        if skill in text:
            score += 2

    # seniority signals (bonus for senior/lead/manager/staff/director)
    for level in ["senior", "staff", "lead", "manager", "director", "head of", "principal"]:
        if level in title:
            score += 3
            break

    # remote check
    loc = (job.get("location") or "").lower()
    if any(w in loc for w in ["anywhere", "worldwide", "global"]):
        score += 8  # truly remote from anywhere
    elif "remote" in loc and not any(w in loc for w in ["us only", "usa only", "united states only"]):
        score += 5
    elif "remote" in loc:
        score += 3  # US-only remote (less useful for non-US applicants)
    elif any(w in text for w in ["remote", "work from anywhere"]):
        score += 3

    # penalize clearly junior roles
    if "junior" in title or "intern" in title or "entry level" in title:
        score -= 20

    return score


def generate_cover_letter(job, model="sonnet"):
    """Generate a personalized cover letter using LLM."""
    prefs = load_prefs()
    prompt = f"""Write a short cover letter (3-4 paragraphs) for this job application.

APPLICANT:
- Name: {PROFILE['name']}
- Current: Engineering Manager & Applied AI Engineer
- 13+ years building production AI systems
- Key achievements: Built Groupon's AI department from scratch, delivered AI Deal Generator (2 days to 30 seconds), built predictive analytics platforms at Toptal and Jazz
- Skills: LLMs, agentic workflows, predictive modeling, NLP, MLOps, Python, GCP, team leadership
- Open source: AutoPilot (autonomous build engine), AutoPrompt (evolutionary prompt optimization), VespeR (agentic coding workflows), Conductor-LLM (cost-aware model routing)

JOB:
- Title: {job.get('title', '')}
- Company: {job.get('company', '')}
- Description: {job.get('description', '')[:800]}

RULES:
- Sound like a real person, not a corporate drone
- No fluff, no "I am writing to express my interest", no "I am excited to apply"
- Lead with what you've actually built that's relevant to THIS specific role
- Be direct and specific. Mention numbers and outcomes.
- Keep it under 250 words
- No em dashes
{f"Voice preferences:{chr(10)}{prefs}" if prefs else ""}

Write ONLY the cover letter text. No subject line, no "Dear Hiring Manager", no sign-off."""

    try:
        cmd = ["claude", "-p", prompt, "--no-session-persistence", "--model", model]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:
        sys.stderr.write(f"cover letter generation failed: {e}\n")
    return None


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", action="store_true", help="Search for matching jobs")
    ap.add_argument("--keywords", type=str, default="", help="Extra search keywords")
    ap.add_argument("--apply", action="store_true", help="Auto-apply where possible")
    ap.add_argument("--cover-letter", type=str, help="Generate cover letter for a job URL")
    ap.add_argument("--model", type=str, default="sonnet", help="Claude model for cover letters")
    ap.add_argument("--min-score", type=int, default=8, help="Minimum match score")
    ap.add_argument("--limit", type=int, default=10, help="Max results to show")
    ap.add_argument("--remote-only", action="store_true", help="Only show remote jobs")
    args = ap.parse_args()

    if args.cover_letter:
        job = {"title": "Role", "company": "Company", "description": args.cover_letter, "url": args.cover_letter}
        letter = generate_cover_letter(job, model=args.model)
        if letter:
            print(letter)
        else:
            print("Failed to generate cover letter")
        return

    if not args.search:
        ap.print_help()
        return

    # collect jobs from all sources
    applied_data = load_applied()
    applied_urls = set(a.get("url", "") for a in applied_data.get("applied", []))

    print("Searching job boards...", file=sys.stderr)
    all_jobs = []

    sources = [
        ("Remotive", lambda: fetch_remotive()),
        ("Remotive (ML)", lambda: fetch_remotive("data")),
        ("Jobicy", lambda: fetch_jobicy()),
        ("HN Who's Hiring", lambda: fetch_hn_whoishiring()),
        ("Greenhouse", lambda: fetch_greenhouse_boards()),
    ]

    for name, fetcher in sources:
        print(f"  {name}...", file=sys.stderr)
        jobs = fetcher()
        all_jobs.extend(jobs)
        print(f"    found {len(jobs)}", file=sys.stderr)

    # dedupe by URL
    seen_urls = set()
    unique_jobs = []
    for job in all_jobs:
        url = job.get("url", "")
        if url and url not in seen_urls and url not in applied_urls:
            seen_urls.add(url)
            unique_jobs.append(job)

    # score and rank
    scored = []
    for job in unique_jobs:
        # remote filter
        if args.remote_only:
            loc = (job.get("location") or "").lower()
            desc = (job.get("description") or "").lower()
            title = (job.get("title") or "").lower()
            combined = f"{loc} {desc} {title}"
            is_remote = any(w in combined for w in
                           ["remote", "anywhere", "worldwide", "global", "work from home"])
            if not is_remote:
                continue
            # exclude geo-restricted remote roles (not accessible from Pakistan)
            # normalize: strip punctuation, collapse whitespace
            loc_norm = re.sub(r'[^a-z0-9 ]', ' ', loc)
            loc_norm = re.sub(r'\s+', ' ', loc_norm).strip()
            restricted_patterns = [
                r'\bus\b', r'\busa\b', r'\bu s\b', r'\bunited states\b',
                r'\bcanada\b', r'\buk\b', r'\bunited kingdom\b',
                r'\bengland\b', r'\bireland\b', r'\bgermany\b',
                r'\bfrance\b', r'\bspain\b', r'\bitaly\b', r'\bportugal\b',
                r'\bswitzerland\b', r'\bbrazil\b', r'\bchina\b',
                r'\bamerica\b', r'\bamer\b', r'\bemea\b', r'\bapac\b',
            ]
            if any(re.search(p, loc_norm) for p in restricted_patterns):
                # allow if location also says "anywhere" or "worldwide"
                if not any(w in loc_norm for w in ["anywhere", "worldwide", "global"]):
                    continue
        score = score_job(job)
        if score >= args.min_score:
            job["match_score"] = score
            scored.append(job)

    scored.sort(key=lambda x: -x["match_score"])
    top = scored[:args.limit]

    if not top:
        print(f"Scanned {len(all_jobs)} jobs, no matches above score {args.min_score}")
        return

    # output results
    lines = [f"Found {len(scored)} matches from {len(all_jobs)} jobs (showing top {len(top)}):"]
    lines.append("")
    for i, job in enumerate(top, 1):
        lines.append(f"{i}. [{job['match_score']}pts] {job['title']}")
        lines.append(f"   {job['company']} | {job['location']} | {job['source']}")
        lines.append(f"   {job['url']}")
        if job.get("salary"):
            lines.append(f"   Salary: {job['salary']}")
        lines.append("")

    print("\n".join(lines))

    # save to memory for tracking
    if MEMORY_DB.exists():
        try:
            db = sqlite3.connect(str(MEMORY_DB))
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
                category TEXT, key TEXT, value TEXT, ttl_days INTEGER DEFAULT 0)""")
            db.execute(
                "INSERT INTO memory (category, key, value) VALUES (?, ?, ?)",
                ("jobs", f"search-{int(time.time())}",
                 json.dumps({"found": len(scored), "scanned": len(all_jobs), "top": [
                     {"title": j["title"], "company": j["company"], "url": j["url"], "score": j["match_score"]}
                     for j in top
                 ]}))
            )
            db.commit()
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
