#!/usr/bin/env python3
"""LLM-driven job application agent using agent-browser.

Uses agent-browser's accessibility tree snapshots + Claude to navigate any job page,
fill forms, upload resume, and submit. No hardcoded selectors.

Usage:
  python3 tools/job_applier.py --url "https://job-boards.greenhouse.io/gitlab/jobs/123"
  python3 tools/job_applier.py --url "https://careers.airbnb.com/positions/123" --dry-run
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from config_loader import get_profile, get_resume_path, get_applicant_summary

RESUME_PATH = get_resume_path()
MEMORY_DB = Path.home() / ".autopilot" / "memory.db"
APPLIED_FILE = Path.home() / ".autopilot" / "applied_jobs.json"
SCREENSHOTS_DIR = Path.home() / ".autopilot" / "job_screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

PROFILE = get_profile()
APPLICANT_SUMMARY = get_applicant_summary()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def run_ab(cmd, timeout=30):
    """Run an agent-browser command and return stdout."""
    full_cmd = f"agent-browser {cmd}"
    sys.stderr.write(f"  > {full_cmd[:120]}\n")
    try:
        r = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            err = r.stderr.strip() or r.stdout.strip()
            sys.stderr.write(f"  ab error: {err[:200]}\n")
            return f"ERROR: {err[:200]}"
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"  ab timeout ({timeout}s)\n")
        return ""
    except Exception as e:
        sys.stderr.write(f"  ab error: {e}\n")
        return ""


def ask_llm(prompt, model="sonnet", timeout=120):
    """Call Claude CLI and return the response text."""
    try:
        cmd = ["claude", "-p", prompt, "--no-session-persistence", "--model", model]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:
        sys.stderr.write(f"LLM call failed: {e}\n")
    return None


def extract_json_from_response(text):
    """Extract JSON array or object from LLM response."""
    if not text:
        return None
    m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r'(\[[\s\S]*\]|\{[\s\S]*\})', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


def snapshot():
    """Take an accessibility snapshot of the current page, interactive elements only."""
    return run_ab("snapshot -i", timeout=15)


def has_form_fields(snap_text):
    """Check if snapshot contains enough form fields for an application."""
    form_keywords = ["textbox", "combobox", "listbox", "file", "checkbox", "radio"]
    count = sum(1 for kw in form_keywords for line in snap_text.split("\n")
                if kw in line.lower())
    return count >= 3


def detect_iframe_via_js():
    """Use JS eval to find embedded job form iframes (Greenhouse, Lever, etc.)."""
    result = run_ab(
        'eval \'JSON.stringify(Array.from(document.querySelectorAll("iframe")).map(f => f.src).filter(s => s && (s.includes("greenhouse") || s.includes("lever") || s.includes("grnh"))))\'',
        timeout=10
    )
    if not result:
        return None
    try:
        urls = json.loads(result.strip('"').replace('\\"', '"'))
        if urls and len(urls) > 0:
            return urls[0]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


# ─── Tracking ────────────────────────────────────────────────────────────────

def load_applied():
    if APPLIED_FILE.exists():
        try:
            return json.loads(APPLIED_FILE.read_text())
        except Exception:
            pass
    return {"applied": [], "skipped": []}


def save_applied(data):
    APPLIED_FILE.write_text(json.dumps(data, indent=2))


def track_application(url, job_title, company, fields_filled):
    """Save to applied_jobs.json and memory.db."""
    ts = int(time.time())
    applied_data = load_applied()
    applied_data["applied"].append({
        "url": url, "title": job_title, "company": company,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "fields_filled": fields_filled,
    })
    save_applied(applied_data)

    if MEMORY_DB.exists():
        try:
            db = sqlite3.connect(str(MEMORY_DB))
            db.execute(
                "INSERT INTO memory (category, key, value) VALUES (?, ?, ?)",
                ("jobs", f"applied-{ts}",
                 json.dumps({"title": job_title, "company": company, "url": url}))
            )
            db.commit()
            db.close()
        except Exception:
            pass


# ─── Agent Loop ──────────────────────────────────────────────────────────────

def apply_with_agent_browser(url, cover_letter=None, dry_run=False, model="sonnet"):
    """Full application flow using agent-browser."""
    result = {"url": url, "status": "started", "fields_filled": 0}
    job_title = ""
    company = ""

    try:
        # ── STEP 1: Open the page ──
        sys.stderr.write(f"Opening {url}\n")
        run_ab(f'open "{url}" --headed', timeout=30)
        run_ab('wait --load networkidle', timeout=30)
        time.sleep(2)

        snap = snapshot()
        if not snap:
            result["status"] = "error: could not load page"
            return result

        # extract company from URL
        gh_match = re.search(r'greenhouse\.io/(?:embed/job_app\?for=)?([^/&?]+)', url)
        if gh_match:
            company = gh_match.group(1).replace("-", " ").title()
        lever_match = re.search(r'lever\.co/([^/]+)', url)
        if not company and lever_match:
            company = lever_match.group(1).replace("-", " ").title()
        if not company:
            host = url.split("//")[-1].split("/")[0]
            for part in host.split("."):
                if part not in ("www", "com", "io", "co", "org", "net", "boards",
                                "jobs", "careers", "job-boards", "greenhouse", "lever", "https"):
                    company = part.replace("-", " ").title()
                    break

        # try to extract job title from snapshot
        for line in snap.split("\n")[:20]:
            if "heading" in line.lower() and len(line) > 20:
                # extract text between quotes
                m = re.search(r'"([^"]+)"', line)
                if m and len(m.group(1)) > 10:
                    job_title = m.group(1)[:150]
                    break

        result["job_title"] = job_title
        result["company"] = company

        # ── STEP 2: Navigate to application form ──
        form_page_snap = snap
        for nav_attempt in range(4):
            if has_form_fields(form_page_snap):
                sys.stderr.write(f"  form found on attempt {nav_attempt}\n")
                break

            sys.stderr.write(f"  navigation attempt {nav_attempt}...\n")

            # check for embedded iframes via JS (snapshots can't see cross-origin iframe content)
            iframe_url = detect_iframe_via_js()
            if iframe_url:
                sys.stderr.write(f"  found iframe via JS, navigating: {iframe_url[:80]}\n")
                run_ab(f'open "{iframe_url}"', timeout=30)
                run_ab('wait --load networkidle', timeout=20)
                time.sleep(2)
                form_page_snap = snapshot()
                if has_form_fields(form_page_snap):
                    break

            # ask LLM how to navigate to the form
            nav_prompt = f"""You are a browser automation agent on a job posting page. You need to get to the APPLICATION FORM.

Current page accessibility snapshot (interactive elements):
{form_page_snap[:3000]}

What should I click to reach the application form? Look for:
- "Application" tab
- "Apply" or "Apply Now" button
- Any button/link that leads to a form

Return ONLY a JSON object:
{{"action": "click", "ref": "e9"}}  — click an element by ref
{{"action": "done"}}  — form is already visible (textboxes for name, email, etc.)
{{"action": "none", "reason": "why"}}  — can't find the form"""

            response = ask_llm(nav_prompt, model=model, timeout=60)
            action = extract_json_from_response(response)

            if not action:
                sys.stderr.write(f"  LLM nav failed\n")
                continue

            act = action.get("action")

            if act == "done":
                break
            elif act == "none":
                sys.stderr.write(f"  LLM says: {action.get('reason', 'unknown')}\n")
                break
            elif act == "click":
                ref = action.get("ref", "")
                if ref:
                    run_ab(f"click @{ref}", timeout=20)
                    time.sleep(3)

                    # after clicking, check for newly loaded iframes
                    iframe_url = detect_iframe_via_js()
                    if iframe_url:
                        sys.stderr.write(f"  iframe appeared after click, navigating: {iframe_url[:80]}\n")
                        run_ab(f'open "{iframe_url}"', timeout=30)
                        run_ab('wait --load networkidle', timeout=20)
                        time.sleep(2)

                    form_page_snap = snapshot()

        if not has_form_fields(form_page_snap):
            result["status"] = "error: could not find application form"
            ts = int(time.time())
            ss_path = SCREENSHOTS_DIR / f"nav-failed-{ts}.png"
            run_ab(f'screenshot "{ss_path}"', timeout=20)
            result["screenshot"] = str(ss_path)
            return result

        # ── STEP 3: Generate cover letter ──
        if not cover_letter:
            cl_prompt = f"""Write a short cover letter (3-4 paragraphs, under 250 words) for this job.

APPLICANT: {APPLICANT_SUMMARY}

JOB: {job_title} at {company}

RULES: Sound human, not corporate. Lead with relevant work. Be specific. No fluff.
No "I am excited to apply" or "I am writing to express". No em dashes.
Write ONLY the letter text."""
            cover_letter = ask_llm(cl_prompt, model=model, timeout=120)

        # ── STEP 4: Fill the form (LLM-driven) ──
        sys.stderr.write("  filling form...\n")

        fill_prompt = f"""You are filling out a job application form. Here is the accessibility snapshot of the form:

{form_page_snap[:4000]}

Fill this form for the following person:

PROFILE:
{json.dumps(PROFILE, indent=2)}

BACKGROUND: {APPLICANT_SUMMARY}

JOB: {job_title} at {company}

COVER LETTER:
{(cover_letter or "Not available")[:500]}

Return a JSON array of agent-browser commands to execute IN ORDER:
[
  {{"cmd": "fill", "ref": "e6", "value": "{PROFILE.get('first_name', '')}"}},
  {{"cmd": "fill", "ref": "e7", "value": "{PROFILE.get('last_name', '')}"}},
  {{"cmd": "select", "ref": "e10", "value": "Yes"}},
  {{"cmd": "upload", "ref": "e9"}},
  {{"cmd": "click", "ref": "e12"}},
  {{"cmd": "skip", "ref": "e15", "reason": "demographic question"}}
]

RULES:
- For name fields: first_name="{PROFILE.get('first_name', '')}", last_name="{PROFILE.get('last_name', '')}"
- For "company" or "employer" fields: use "{PROFILE.get('current_company', '')}" (NOT the applicant's name!)
- For open-ended questions (explain, describe, briefly): write 1-3 sentence answer based on background. Be specific, mention numbers.
- For cover letter / "why interested" textareas: use the COVER LETTER above
- For file upload (resume/CV): use "upload" cmd — I will handle the file path
- For select/dropdown/combobox: use "select" cmd with the best matching option text
- For checkboxes that need checking: use "click" cmd
- For salary: skip
- For demographic questions (gender, race, veteran, disability): skip or select "Decline to self-identify" if required
- For yes/no authorization/visa questions: select "Yes"
- Do NOT fill fields that already have values
- Include ALL fields that need to be filled
- Use the exact ref values from the snapshot (e.g., e6, e7, etc.)
- Do NOT include the submit button — I will handle submission separately"""

        response = ask_llm(fill_prompt, model=model, timeout=120)
        actions = extract_json_from_response(response)

        if not actions or not isinstance(actions, list):
            sys.stderr.write(f"  LLM fill failed: {response[:200] if response else 'no response'}\n")
            result["status"] = "error: LLM could not generate fill commands"
            return result

        fields_filled = 0
        resume_uploaded = False

        for act in actions:
            try:
                cmd = act.get("cmd", "")
                ref = act.get("ref", "")

                if cmd == "skip" or not ref:
                    continue

                if cmd == "fill":
                    value = act.get("value", "")
                    if value:
                        # escape quotes in value
                        escaped = value.replace('"', '\\"')
                        run_ab(f'fill @{ref} "{escaped}"', timeout=20)
                        fields_filled += 1
                        sys.stderr.write(f"  filled @{ref}: {value[:40]}\n")

                elif cmd == "select":
                    value = act.get("value", "")
                    if value:
                        escaped = value.replace('"', '\\"')
                        out = run_ab(f'select @{ref} "{escaped}"', timeout=20)
                        if out.startswith("ERROR"):
                            # combobox (type-ahead): click to focus, type, wait for suggestions
                            run_ab(f'click @{ref}', timeout=20)
                            time.sleep(0.5)
                            run_ab(f'fill @{ref} "{escaped}"', timeout=20)
                            time.sleep(2)  # wait for autocomplete dropdown
                            run_ab('press ArrowDown', timeout=5)
                            time.sleep(0.3)
                            run_ab('press Enter', timeout=5)
                        fields_filled += 1
                        sys.stderr.write(f"  selected @{ref}: {value[:40]}\n")

                elif cmd == "upload":
                    if not resume_uploaded:
                        # Try ref first, then fallback to CSS selector for file inputs
                        out = run_ab(f'upload @{ref} "{RESUME_PATH}"', timeout=15)
                        if out.startswith("ERROR"):
                            # Greenhouse uses styled buttons — target hidden file input
                            run_ab(f'upload "input[type=file]" "{RESUME_PATH}"', timeout=15)
                        resume_uploaded = True
                        fields_filled += 1
                        sys.stderr.write(f"  uploaded resume\n")

                elif cmd == "click":
                    run_ab(f'click @{ref}', timeout=20)
                    fields_filled += 1
                    sys.stderr.write(f"  clicked @{ref}\n")

            except Exception as e:
                sys.stderr.write(f"  action failed ({cmd} @{ref}): {e}\n")
                continue

        result["fields_filled"] = fields_filled
        result["resume_uploaded"] = resume_uploaded
        result["cover_letter_added"] = cover_letter is not None

        # ── STEP 5: Screenshot ──
        ts = int(time.time())
        slug = re.sub(r'[^a-z0-9]+', '-', (company or "unknown").lower())[:30]
        ss_path = SCREENSHOTS_DIR / f"{slug}-{ts}.png"
        run_ab(f'screenshot "{ss_path}"', timeout=20)
        result["screenshot"] = str(ss_path)

        # ── STEP 6: Submit or dry-run ──
        if dry_run:
            result["status"] = "dry_run"
            print(f"DRY RUN: Filled {fields_filled} fields, resume: {resume_uploaded}")
            print(f"Screenshot: {ss_path}")

        elif fields_filled == 0:
            result["status"] = "no_fields_filled"
            print(f"ABORTED: Could not fill any fields.")

        else:
            # find and click submit button
            final_snap = snapshot()
            submit_prompt = f"""Which element submits this job application form?

{final_snap[:2000]}

Return ONLY a JSON object:
{{"ref": "e5"}}

Look for: "Submit application", "Submit", "Send application"
NOT "Apply Now" (that's for navigating, not submitting)."""

            submit_response = ask_llm(submit_prompt, model=model, timeout=30)
            submit_action = extract_json_from_response(submit_response)

            if submit_action and submit_action.get("ref"):
                submit_ref = submit_action["ref"]
                sys.stderr.write(f"  submitting via @{submit_ref}\n")
                run_ab(f"click @{submit_ref}", timeout=20)
                time.sleep(5)

                # post-submit screenshot
                submitted_ss = SCREENSHOTS_DIR / f"{slug}-{ts}-submitted.png"
                run_ab(f'screenshot "{submitted_ss}"', timeout=20)

                # verify submission by checking for confirmation message
                post_snap = snapshot()
                confirmation_keywords = ["thank", "submitted", "received", "confirmation",
                                         "successfully", "application has been"]
                page_text = post_snap.lower() if post_snap else ""
                confirmed = any(kw in page_text for kw in confirmation_keywords)

                if confirmed:
                    result["status"] = "submitted_confirmed"
                    sys.stderr.write("  submission CONFIRMED (saw confirmation message)\n")
                else:
                    # check if page changed (form is gone = likely submitted)
                    still_has_form = has_form_fields(post_snap) if post_snap else False
                    if not still_has_form:
                        result["status"] = "submitted_likely"
                        sys.stderr.write("  submission LIKELY (form disappeared after click)\n")
                    else:
                        result["status"] = "submitted_uncertain"
                        sys.stderr.write("  submission UNCERTAIN (form still visible — may have validation errors)\n")

                result["post_submit_screenshot"] = str(submitted_ss)
                track_application(url, job_title, company, fields_filled)
            else:
                result["status"] = "no_submit_button"
                print("Could not find submit button.")

        # ── STEP 7: Close browser ──
        run_ab("close", timeout=5)

    except Exception as e:
        result["status"] = f"error: {str(e)[:200]}"
        try:
            run_ab(f'screenshot "{SCREENSHOTS_DIR / f"error-{int(time.time())}.png"}"', timeout=20)
            run_ab("close", timeout=5)
        except Exception:
            pass

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Job application URL")
    ap.add_argument("--dry-run", action="store_true", help="Fill form but don't submit")
    ap.add_argument("--cover-letter", type=str, help="Custom cover letter text")
    ap.add_argument("--model", type=str, default="sonnet", help="Claude model for decisions")
    args = ap.parse_args()

    print(f"Opening {args.url}...")
    print(f"Resume: {RESUME_PATH}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Agent model: {args.model}")
    print()

    result = apply_with_agent_browser(
        url=args.url,
        cover_letter=args.cover_letter,
        dry_run=args.dry_run,
        model=args.model,
    )

    print()
    print(f"Status: {result['status']}")
    print(f"Job: {result.get('job_title', 'unknown')} at {result.get('company', 'unknown')}")
    print(f"Fields filled: {result['fields_filled']}")
    print(f"Resume uploaded: {result.get('resume_uploaded', False)}")
    print(f"Cover letter: {result.get('cover_letter_added', False)}")
    if result.get("screenshot"):
        print(f"Screenshot: {result['screenshot']}")


if __name__ == "__main__":
    main()
