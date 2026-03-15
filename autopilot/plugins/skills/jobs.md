# Jobs — auto job hunting

Search for AI/ML jobs, generate cover letters, and auto-apply with browser automation.

## Search for jobs
```bash
python3 plugins/tools/job_hunter.py --search --limit 10
```

## Generate a cover letter
```bash
python3 plugins/tools/job_hunter.py --cover-letter "job description here" --model sonnet
```

## Auto-apply (agent-browser + LLM)
```bash
# dry run — fills form, shows browser, doesn't submit
python3 plugins/tools/job_applier.py --url "https://job-boards.greenhouse.io/company/jobs/123" --dry-run

# live — fills form, uploads resume, submits
python3 plugins/tools/job_applier.py --url "https://jobs.lever.co/company/abc-123"

# with custom cover letter
python3 plugins/tools/job_applier.py --url "URL" --cover-letter "custom text"
```

## Supported form types
- Greenhouse (most tech companies)
- Lever (many startups)
- Generic web forms (best effort)

## What it auto-fills
Name, email, phone, LinkedIn, GitHub, location, current title/company, years of experience, education, resume upload, cover letter.

## What it does
1. Opens the job page via agent-browser (real Chromium)
2. Takes accessibility snapshot, sends to Claude to navigate (clicks tabs, detects iframes)
3. Navigates into embedded Greenhouse/Lever iframes automatically
4. Snapshots the form, Claude generates fill/select/upload commands
5. Fills all fields, uploads resume PDF, generates cover letter with sonnet
6. Takes a screenshot for your records
7. Submits (or stops in dry-run mode)
8. Tracks the application in applied_jobs.json and memory.db

## Daily cron
A daily cron searches all job boards and notifies you on Discord with top matches.
