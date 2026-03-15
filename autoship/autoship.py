#!/usr/bin/env python3
"""autoship — describe it, ship it.

Usage:
  python3 autoship.py login --code SHIP-ABC123            # claim hosted deploy access
  python3 autoship.py spec.md                              # fresh build
  python3 autoship.py spec.md -o myapp                     # custom output dir
  python3 autoship.py spec.md -e codex --deploy autoship   # build & deploy

  python3 autoship.py changes.md -o myapp                  # update existing app
  python3 autoship.py changes.md -o myapp --deploy autoship # update & redeploy
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

PROGRAM = Path(__file__).with_name("program.md")
DEPLOY_SCRIPT = Path(__file__).with_name("deploy_release.sh")
DEPLOY_ROOT = "/opt/autoship"
DEFAULT_AUTOSHIP_API_URL = "https://api.autoship.fun/deploy"
DEFAULT_AUTOSHIP_LOGIN_URL = "https://api.autoship.fun/claim"
SSH_KNOWN_HOST_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]
ARCHIVE_EXCLUDES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".DS_Store",
}
CODEX_REASONING = 'model_reasoning_effort="medium"'
PLAN_FILE = "autoship.plan.json"
SPEC_FILE = "autoship.spec.md"
AUTH_DIR = Path.home() / ".config" / "autoship"
AUTH_FILE = AUTH_DIR / "auth.json"


def die(message):
    raise SystemExit(message)


class Progress:
    def __init__(self, total, label="update"):
        self.total = total
        self.current = 0
        self.label = label

    def stage(self, name):
        self.current += 1
        print(f"  [{self.current}/{self.total}] {name}...", end=" ", flush=True)

    def done(self, detail="done"):
        print(detail, flush=True)


def is_update(outdir):
    return (outdir / PLAN_FILE).exists() and (outdir / SPEC_FILE).exists()


def list_app_files(outdir):
    skip = ARCHIVE_EXCLUDES | {PLAN_FILE, SPEC_FILE, "autoship.json"}
    files = []
    for p in sorted(outdir.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(outdir)
        if any(part in skip for part in rel.parts):
            continue
        files.append(str(rel))
    return files


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or f"app-{int(time.time())}")[:48]


def run(cmd, *, cwd=None, env=None, capture=False, check=True, input_text=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        input=input_text,
        capture_output=capture,
        check=check,
    )


def ask_yes_no(prompt, *, default=False):
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for left, right in (("[", "]"), ("{", "}")):
        start = text.find(left)
        end = text.rfind(right) + 1
        if start != -1 and end > start:
            snippet = text[start:end]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No JSON found in: {text[:300]}")


def llm(prompt, engine, workdir):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    if engine == "claude":
        result = run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            cwd=str(workdir.resolve()),
            env=env,
            capture=True,
            check=False,
        )
    else:
        result = run(
            ["codex", "exec", "--full-auto", "-c", CODEX_REASONING, prompt],
            cwd=str(workdir.resolve()),
            env=env,
            capture=True,
            check=False,
        )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail[:800] or f"{engine} planning failed")

    return result.stdout.strip()


def ssh_cmd(server, *, password=None, key=None, copy=False):
    binary = "scp" if copy else "ssh"
    cmd = []
    if password:
        if not shutil_which("sshpass"):
            die("sshpass is required when AUTOSHIP_SSH_PASSWORD is used.")
        cmd += ["sshpass", "-p", password]
    cmd += [binary, *SSH_KNOWN_HOST_OPTS]
    if key:
        cmd += ["-i", key]
    target = server
    return cmd, target


def shutil_which(binary):
    from shutil import which
    return which(binary)


def init_git(outdir):
    if not (outdir / ".git").exists():
        run(["git", "init"], cwd=str(outdir.resolve()), capture=True)


def read_saved_auth():
    if not AUTH_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_saved_auth(data):
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(data, indent=2) + "\n")


def claim_invite_code(code, *, login_url):
    body = json.dumps({"code": code.strip()}).encode()
    request = urllib.request.Request(
        login_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            payload = resp.read().decode()
        data = json.loads(payload)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode(errors="replace").strip()
        raise SystemExit(f"Login failed ({exc.code}):\n{details}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Login failed:\n{exc}") from exc
    if not isinstance(data, dict) or "token" not in data:
        raise SystemExit("Login failed: invalid claim response.")
    return data


def api_root_url(api_url):
    parsed = urllib.parse.urlsplit(api_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/deploy"):
        path = path[:-7]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def start_browser_pair(api_url):
    start_url = api_root_url(api_url).rstrip("/") + "/authorize/start"
    request = urllib.request.Request(
        start_url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            payload = resp.read().decode()
        data = json.loads(payload)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode(errors="replace").strip()
        raise SystemExit(f"Browser connect failed ({exc.code}):\n{details}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Browser connect failed:\n{exc}") from exc

    approve_url = data.get("approve_url")
    poll_url = data.get("poll_url")
    if not approve_url or not poll_url:
        raise SystemExit("Browser connect failed: invalid authorize/start response.")

    print("  Connect this CLI on autoship.fun...")
    if data.get("code"):
        print(f"  CODE:       {data['code']}")
    print(f"  BROWSER:    {approve_url}")
    try:
        webbrowser.open(approve_url)
    except Exception:
        pass

    deadline = time.time() + int(data.get("expires_in", 300))
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(poll_url, timeout=30) as resp:
                payload = resp.read().decode()
                status = resp.status
        except urllib.error.HTTPError as exc:
            if exc.code == 202:
                time.sleep(2)
                continue
            details = exc.read().decode(errors="replace").strip()
            raise SystemExit(f"Browser connect failed ({exc.code}):\n{details}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Browser connect failed:\n{exc}") from exc

        data = json.loads(payload)
        if status == 200 and data.get("token"):
            auth = {
                "api_token": data["token"],
                "api_url": data.get("api_url", api_url),
                "domain": data.get("domain", "autoship.fun"),
                "claimed_at": int(time.time()),
            }
            write_saved_auth(auth)
            print(f"  CONNECTED:  {auth['domain']}")
            return auth
        time.sleep(2)

    raise SystemExit("Browser connect timed out. Re-run the command to try again.")


def login_command(argv):
    p = argparse.ArgumentParser(description="autoship login")
    p.add_argument("--code", default=None, help="invite code from autoship.fun")
    p.add_argument("--login-url", default=os.getenv("AUTOSHIP_LOGIN_URL", DEFAULT_AUTOSHIP_LOGIN_URL))
    args = p.parse_args(argv)

    code = (args.code or "").strip()
    if not code:
        code = input("Invite code: ").strip()
    if not code:
        die("Invite code is required.")

    result = claim_invite_code(code, login_url=args.login_url)
    auth = {
        "api_token": result["token"],
        "api_url": result.get("api_url", DEFAULT_AUTOSHIP_API_URL),
        "domain": result.get("domain", "autoship.fun"),
        "claimed_at": int(time.time()),
    }
    write_saved_auth(auth)

    print("autoship login complete")
    print(f"  token saved to {AUTH_FILE}")
    print(f"  deploy api: {auth['api_url']}")
    print(f"  domain:     {auth['domain']}")


def logout_command(argv):
    argparse.ArgumentParser(description="autoship logout").parse_args(argv)
    AUTH_FILE.unlink(missing_ok=True)
    print("autoship login removed")


def heuristic_capabilities(spec):
    text = spec.lower()
    has = lambda *words: any(word in text for word in words)

    auth = "none"
    if has("login", "log in", "sign in", "signup", "sign up", "account", "user authentication", "password"):
        auth = "local"
    if has("oauth", "google login", "github login", "magic link"):
        auth = "external"

    database = "none"
    if "localstorage" in text or "local storage" in text:
        database = "none"
    elif has("database", "sqlite", "postgres", "save", "saved", "persist", "history", "analytics", "dashboard", "admin", "account", "users"):
        database = "sqlite"
    if has("postgres", "postgresql"):
        database = "postgres"

    payments = "none"
    if has("payment", "payments", "billing", "checkout", "stripe", "subscription", "subscriptions"):
        payments = "external"

    email = "none"
    if has("email", "emails", "newsletter", "verification", "password reset", "invite"):
        email = "external"

    storage = "none"
    if has("upload", "uploads", "file", "files", "image", "images", "avatar", "document"):
        storage = "local"

    jobs = "none"
    if has("cron", "schedule", "scheduled", "background job", "worker", "queue"):
        jobs = "local"

    admin = has("admin", "moderation", "backoffice", "staff")
    api = has("api", "webhook", "endpoint", "rest", "graphql")

    return {
        "database": database,
        "auth": auth,
        "payments": payments,
        "email": email,
        "storage": storage,
        "jobs": jobs,
        "admin": admin,
        "api": api,
    }


def normalize_capabilities(raw, spec):
    caps = heuristic_capabilities(spec)
    if isinstance(raw, dict):
        caps.update({k: v for k, v in raw.items() if v not in (None, "")})

    if caps["auth"] != "none" and caps["database"] == "none":
        caps["database"] = "sqlite"
    if caps["admin"] and caps["database"] == "none":
        caps["database"] = "sqlite"
    if caps["payments"] != "none":
        caps["database"] = caps["database"] if caps["database"] != "none" else "sqlite"
    if caps["email"] != "none" and caps["auth"] == "none" and ("newsletter" in spec.lower()):
        caps["database"] = caps["database"] if caps["database"] != "none" else "sqlite"

    return caps


def infer_secrets(caps):
    secrets_needed = []
    if caps["auth"] == "external":
        secrets_needed += ["AUTH_PROVIDER_CLIENT_ID", "AUTH_PROVIDER_CLIENT_SECRET"]
    if caps["payments"] == "external":
        secrets_needed += ["STRIPE_SECRET_KEY"]
    if caps["email"] == "external":
        secrets_needed += ["EMAIL_PROVIDER_API_KEY"]
    if caps["storage"] == "s3":
        secrets_needed += ["S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    return secrets_needed


def write_plan_file(outdir, plan):
    (outdir / PLAN_FILE).write_text(json.dumps(plan, indent=2) + "\n")


def read_plan_file(outdir):
    path = outdir / PLAN_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def plan_build(spec, *, deploy, slug, domain, engine, workdir):
    deploy_files = ["Dockerfile", "autoship.json"] if deploy == "autoship" else []
    deploy_note = ""
    if deploy == "autoship":
        deploy_note = f"""
- The app will be deployed to https://{slug}.{domain}
- Include Dockerfile and autoship.json in the files list
- Prefer static or single-process apps when the spec allows it
"""

    plan_prompt = f"""Plan a minimal app build for this spec.

SPEC:
{spec}

Return ONLY valid JSON:
{{
  "app_type": "static_site | web_app | saas | dashboard | api | tool",
  "stack": "chosen stack",
  "summary": "one sentence",
  "files": ["file1", "file2"],
  "run": "main local run command",
  "capabilities": {{
    "database": "none | sqlite | postgres",
    "auth": "none | local | external",
    "payments": "none | external",
    "email": "none | external",
    "storage": "none | local | s3",
    "jobs": "none | local",
    "admin": false,
    "api": false
  }},
  "secrets_needed": ["ENV_VAR_IF_NEEDED"]
}}

Rules:
- Keep the project very small, usually under 8 files
- Include README.md
{deploy_note}
- Prefer the simplest stack that satisfies the spec
- Set admin=true only when the spec explicitly asks for an admin, staff, backoffice, or moderation interface
- Files should be enough to build a complete working app
- Return JSON only, no markdown"""

    plan = extract_json(llm(plan_prompt, engine, workdir))
    files = plan.get("files") or []
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise RuntimeError("Planner returned invalid files list.")
    required = ["README.md", *deploy_files]
    for item in required:
        if item not in files:
            files.append(item)
    plan["files"] = files
    plan["app_type"] = str(plan.get("app_type", "web_app"))
    plan["capabilities"] = normalize_capabilities(plan.get("capabilities"), spec)
    if plan["app_type"] == "static_site":
        spec_text = spec.lower()
        explicit_admin = any(word in spec_text for word in ("admin", "moderation", "backoffice", "staff"))
        explicit_data = any(
            word in spec_text
            for word in (
                "database",
                "sqlite",
                "postgres",
                "save",
                "saved",
                "persist",
                "history",
                "analytics",
                "dashboard",
                "account",
                "accounts",
                "user",
                "users",
                "login",
                "log in",
                "sign in",
                "signup",
                "sign up",
                "authentication",
            )
        )
        if not explicit_admin:
            plan["capabilities"]["admin"] = False
        if not explicit_data and plan["capabilities"].get("auth") == "none":
            plan["capabilities"]["database"] = "none"
    plan["secrets_needed"] = infer_secrets(plan["capabilities"])
    plan["turnkey"] = not bool(plan["secrets_needed"])
    plan["deploy"] = {"slug": slug, "domain": domain}
    return plan


def capability_summary(plan):
    caps = plan.get("capabilities") or {}
    pairs = [
        ("db", caps.get("database", "none")),
        ("auth", caps.get("auth", "none")),
        ("payments", caps.get("payments", "none")),
        ("email", caps.get("email", "none")),
        ("storage", caps.get("storage", "none")),
        ("jobs", caps.get("jobs", "none")),
        ("admin", "yes" if caps.get("admin") else "no"),
        ("api", "yes" if caps.get("api") else "no"),
    ]
    return ", ".join(f"{name}={value}" for name, value in pairs)


def capability_contract(plan):
    caps = plan.get("capabilities") or {}
    lines = [
        "CAPABILITY CONTRACT",
        f"- Build only the inferred capabilities: {json.dumps(caps, sort_keys=True)}",
        "- Do not add login, payments, email providers, dashboards, queues, or extra services unless the plan requires them.",
    ]

    if caps.get("database") == "sqlite":
        lines += [
            "- Use SQLite only. Store the database under /data/app.db.",
            "- Prefer DATABASE_PATH or SQLITE_PATH from the environment, defaulting to /data/app.db.",
        ]
    elif caps.get("database") == "none":
        lines.append("- Do not add a database.")

    if caps.get("auth") == "local":
        lines += [
            "- Implement local auth only. No OAuth, no external auth provider.",
            "- Use SECRET_KEY and SESSION_SECRET from the environment when the framework supports them.",
            "- If an admin bootstrap is useful, read AUTOSHIP_ADMIN_EMAIL and AUTOSHIP_ADMIN_PASSWORD on first run.",
        ]
    elif caps.get("auth") == "none":
        lines.append("- Do not add login or signup flows.")

    if caps.get("storage") == "local":
        lines += [
            "- Store uploads/files locally under /data/uploads.",
            "- Prefer UPLOADS_DIR from the environment, defaulting to /data/uploads.",
        ]

    if caps.get("jobs") == "local":
        lines.append("- Keep background work in-process. Do not require Redis, Celery, or a separate worker service.")
    else:
        lines.append("- Do not add a background queue or worker service.")

    if plan.get("turnkey"):
        lines.append("- This app must be turnkey on autoship.fun with no third-party credentials.")
    elif plan.get("secrets_needed"):
        secrets = ", ".join(plan["secrets_needed"])
        lines.append(f"- External secrets are unavoidable here: {secrets}. List them clearly in the README.")

    return "\n".join(lines)


def build_prompt(program, spec, *, deploy, slug, domain, plan):
    extra = ""
    if deploy == "autoship":
        extra = f"""

DEPLOY CONTRACT
- This app will be deployed automatically to https://{slug}.{domain}
- You MUST include a Dockerfile that runs the app in production
- The app MUST listen on host 0.0.0.0 and respect PORT (default 8000)
- Include autoship.json with JSON only, for example:
  {{"container_port": 8000, "healthcheck_path": "/", "data_dir": "/data"}}
- If the app needs persistence, store writable files under /data
- Prefer SQLite at /data/app.db when a database is needed
- The runtime will provide env vars such as AUTOSHIP_APP_URL, SECRET_KEY, SESSION_SECRET, DATABASE_PATH, SQLITE_PATH, DATA_DIR, AUTOSHIP_DATA_DIR, UPLOADS_DIR, AUTOSHIP_ADMIN_EMAIL, and AUTOSHIP_ADMIN_PASSWORD when relevant
- Avoid any manual setup, cloud secrets, or hosted services
"""

    file_list = "\n".join(f"- {path}" for path in plan.get("files", []))
    summary = plan.get("summary", "")
    stack = plan.get("stack", "")
    run_cmd = plan.get("run", "")

    return f"""{program}

Build the complete application described below.

SPEC:
{spec}

BUILD PLAN
- Stack: {stack}
- Summary: {summary}
- Local run command: {run_cmd}

FILES TO CREATE
{file_list}

{capability_contract(plan)}
{extra}

Write all files. Install deps. Test it runs. Fix any errors.
Only create the files listed above unless an extra file is absolutely required for the app to run.
When done, print EXACTLY: "AUTOSHIP COMPLETE" and list every file you created."""


def update_prompt(program, original_spec, change_request, *, deploy, slug, domain, plan, existing_files):
    extra = ""
    if deploy == "autoship":
        extra = f"""

DEPLOY CONTRACT
- This app is deployed at https://{slug}.{domain}
- Maintain the existing Dockerfile and autoship.json
- The app MUST listen on host 0.0.0.0 and respect PORT (default 8000)
- If the app needs persistence, store writable files under /data
"""

    file_list = "\n".join(f"- {path}" for path in existing_files)

    return f"""{program}

You are updating an existing application. The app is already built and working.

ORIGINAL SPEC:
{original_spec}

CHANGE REQUEST:
{change_request}

EXISTING FILES
{file_list}

{capability_contract(plan)}
{extra}

Read the existing files first to understand the current state.
Then apply ONLY the changes described in the change request.
Do not rewrite files that don't need changes.
Install any new deps if needed. Test that the app still runs. Fix any errors.
When done, print EXACTLY: "AUTOSHIP COMPLETE" and list every file you changed."""


def run_agent(engine, prompt, outdir):
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
        cmd = ["codex", "exec", "--full-auto", "-c", CODEX_REASONING, prompt]

    result = run(cmd, cwd=str(outdir.resolve()), env=env, check=False, capture=True)
    return result.returncode == 0


def verify_build(outdir, plan):
    """Check if the build produced the expected files."""
    missing = []
    for f in plan.get("files", []):
        if not (outdir / f).exists():
            missing.append(f)
    return missing


def fix_prompt(original_prompt, missing_files, error_hint=None):
    parts = ["The previous build attempt had issues."]
    if missing_files:
        flist = ", ".join(missing_files)
        parts.append(f"These files are missing: {flist}")
    if error_hint:
        parts.append(f"The agent also exited with an error.")
    parts.append("Read the existing files, figure out what went wrong, fix it, and finish the build.")
    parts.append('When done, print EXACTLY: "AUTOSHIP COMPLETE".')
    return "\n".join(parts)


def build_with_retries(engine, prompt, outdir, plan, *, max_attempts=3, progress=None):
    for attempt in range(1, max_attempts + 1):
        ok = run_agent(engine, prompt, outdir)
        missing = verify_build(outdir, plan)

        if ok and not missing:
            return True

        if attempt == max_attempts:
            if missing:
                print(f"\n  warning: missing files after {max_attempts} attempts: {', '.join(missing)}", flush=True)
            return ok and not missing

        if progress:
            label = f"retrying ({attempt}/{max_attempts})"
            print(f"\n  [{progress.current}/{progress.total}] {label}...", end=" ", flush=True)

        prompt = fix_prompt(prompt, missing, error_hint=(not ok))

    return False


def repair_deploy_output(engine, outdir, slug, domain):
    prompt = f"""The app in this directory already exists, but it is missing deployment files required by autoship.

Add or fix ONLY the deployment artifacts so the app can be deployed to https://{slug}.{domain}.

Required files and rules:
- Dockerfile must run the app in production
- The app must listen on 0.0.0.0 and respect PORT (default 8000)
- autoship.json must be valid JSON with:
  {{"container_port": 8000, "healthcheck_path": "/", "data_dir": "/data"}}
- If the app writes to disk, write only under /data
- Do not redesign or rewrite the app unless needed for deployment

When done, print EXACTLY: "AUTOSHIP COMPLETE"."""
    run_agent(engine, prompt, outdir)


def ensure_deploy_contract(engine, outdir, slug, domain):
    dockerfile = outdir / "Dockerfile"
    if not dockerfile.exists():
        repair_deploy_output(engine, outdir, slug, domain)
        if not dockerfile.exists():
            die("Deploy mode requires the generated app to include a Dockerfile.")

    manifest_path = outdir / "autoship.json"
    data = {
        "container_port": 8000,
        "healthcheck_path": "/",
        "data_dir": "/data",
    }
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text())
            if isinstance(loaded, dict):
                data.update(loaded)
        except json.JSONDecodeError:
            die("autoship.json exists but is not valid JSON.")

    data["container_port"] = int(data.get("container_port", 8000))
    if not str(data.get("healthcheck_path", "/")).startswith("/"):
        data["healthcheck_path"] = f"/{data['healthcheck_path']}"
    data["data_dir"] = str(data.get("data_dir", "/data"))
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def should_exclude(path):
    return any(part in ARCHIVE_EXCLUDES for part in path.parts)


def make_archive(outdir):
    temp = tempfile.NamedTemporaryFile(prefix="autoship-", suffix=".tgz", delete=False)
    temp.close()
    archive_path = Path(temp.name)

    with tarfile.open(archive_path, "w:gz") as tar:
        for path in outdir.rglob("*"):
            if should_exclude(path.relative_to(outdir)):
                continue
            tar.add(path, arcname=str(path.relative_to(outdir)))

    return archive_path


def upload_archive(archive_path, server, *, password=None, key=None):
    scp, target = ssh_cmd(server, password=password, key=key, copy=True)
    remote_archive = f"/tmp/{archive_path.name}"
    run([*scp, str(archive_path), f"{target}:{remote_archive}"], check=True)
    return remote_archive


def remote_deploy(server, slug, domain, email, remote_archive, *, password=None, key=None):
    ssh, target = ssh_cmd(server, password=password, key=key, copy=False)
    try:
        result = run(
            [*ssh, target, "bash", "-s", "--", slug, domain, email or "__none__", remote_archive, DEPLOY_ROOT],
            input_text=DEPLOY_SCRIPT.read_text(),
            capture=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise SystemExit(f"Remote deploy failed:\n{details}") from exc

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        die("Remote deploy did not return a deployment result.")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse remote deploy result:\n{result.stdout}") from exc


def deploy_via_api(outdir, slug, *, api_url, api_token, domain, email=None):
    archive_path = make_archive(outdir)
    try:
        headers = {
            "Content-Type": "application/gzip",
            "X-Autoship-Slug": slug,
            "X-Autoship-Domain": domain,
            "X-Autoship-Email": email or "",
        }
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        request = urllib.request.Request(
            api_url,
            data=archive_path.read_bytes(),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=900) as resp:
            payload = resp.read().decode()
        return json.loads(payload)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode(errors="replace").strip()
        raise SystemExit(f"Hosted autoship deploy failed ({exc.code}):\n{details}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Hosted autoship deploy failed:\n{exc}") from exc
    finally:
        archive_path.unlink(missing_ok=True)


def deploy_autoship(
    outdir,
    slug,
    *,
    engine,
    server,
    domain,
    password=None,
    key=None,
    email=None,
    api_url=None,
    api_token=None,
):
    ensure_deploy_contract(engine, outdir, slug, domain)
    if api_url:
        return deploy_via_api(
            outdir,
            slug,
            api_url=api_url or DEFAULT_AUTOSHIP_API_URL,
            api_token=api_token,
            domain=domain,
            email=email,
        )
    if server:
        archive_path = make_archive(outdir)
        try:
            remote_archive = upload_archive(archive_path, server, password=password, key=key)
            return remote_deploy(
                server,
                slug,
                domain,
                email,
                remote_archive,
                password=password,
                key=key,
            )
        finally:
            archive_path.unlink(missing_ok=True)
    die("autoship deploy requires a hosted API URL or an operator SSH server target.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login_command(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "logout":
        logout_command(sys.argv[2:])
        return

    p = argparse.ArgumentParser(description="autoship — describe it, ship it")
    p.add_argument("spec", help="path to spec or change-request file (.md or .txt)")
    p.add_argument("-o", "--output", default=None, help="output directory name")
    p.add_argument("-e", "--engine", default="claude", choices=["claude", "codex"])
    p.add_argument("--deploy", default="none", choices=["none", "autoship"], help="deploy target")
    p.add_argument("--slug", default=None, help="subdomain slug to deploy as")
    p.add_argument("--server", default=os.getenv("AUTOSHIP_SERVER"), help="SSH target, e.g. root@1.2.3.4")
    p.add_argument("--domain", default=os.getenv("AUTOSHIP_DOMAIN", "autoship.fun"), help="base domain, e.g. autoship.fun")
    p.add_argument("--ssh-key", default=os.getenv("AUTOSHIP_SSH_KEY"), help="SSH private key path")
    p.add_argument("--email", default=os.getenv("AUTOSHIP_EMAIL"), help="Let's Encrypt email")
    p.add_argument("--api-url", default=os.getenv("AUTOSHIP_API_URL", DEFAULT_AUTOSHIP_API_URL), help="hosted autoship deploy API URL")
    p.add_argument("--api-token", default=os.getenv("AUTOSHIP_API_TOKEN"), help="hosted autoship deploy token")
    args = p.parse_args()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    if args.deploy == "none" and interactive and ask_yes_no("Deploy to autoship.fun after build?", default=False):
        args.deploy = "autoship"

    saved_auth = read_saved_auth()
    if not args.api_token:
        args.api_token = saved_auth.get("api_token")
    if args.api_url == DEFAULT_AUTOSHIP_API_URL and saved_auth.get("api_url"):
        args.api_url = saved_auth["api_url"]
    if args.domain == "autoship.fun" and saved_auth.get("domain"):
        args.domain = saved_auth["domain"]
    if args.deploy == "autoship" and interactive and not args.server and not args.api_token:
        auth = start_browser_pair(args.api_url)
        args.api_token = auth.get("api_token")
        args.api_url = auth.get("api_url", args.api_url)
        args.domain = auth.get("domain", args.domain)

    spec_path = Path(args.spec)
    spec_text = spec_path.read_text()
    outdir = Path(args.output or f"ship_{spec_path.stem}")
    outdir.mkdir(exist_ok=True)
    init_git(outdir)

    existing_plan = read_plan_file(outdir)
    existing_deploy = (existing_plan or {}).get("deploy") or {}
    slug = slugify(args.slug or existing_deploy.get("slug") or outdir.name.replace("ship_", "", 1))
    domain = args.domain or existing_deploy.get("domain") or "autoship.fun"
    if args.deploy == "autoship":
        if not domain:
            die("autoship deploy requires a domain.")
        if not (args.api_url or args.server):
            die("autoship deploy requires a hosted API URL or --server for operator mode.")

    program = PROGRAM.read_text() if PROGRAM.exists() else ""
    updating = is_update(outdir)
    num_stages = 3 if args.deploy == "autoship" else 2

    if updating:
        mode = "update"
        original_spec = (outdir / SPEC_FILE).read_text()
        plan = existing_plan or json.loads((outdir / PLAN_FILE).read_text())
        plan["deploy"] = {"slug": slug, "domain": domain}
        existing_files = list_app_files(outdir)

        print(f"""
============================================================
  autoship — updating existing app
============================================================
  change:  {args.spec}
  engine:  {args.engine}
  output:  {outdir}/
  deploy:  {args.deploy}
  files:   {len(existing_files)} existing
============================================================
""")
        print(f"  plan:    {plan.get('app_type', 'web_app')} / {plan.get('stack', 'unknown')}")
        print(f"  caps:    {capability_summary(plan)}")
        print()

        progress = Progress(num_stages, label="update")
        progress.stage("Applying changes")
        prompt = update_prompt(
            program,
            original_spec,
            spec_text,
            deploy=args.deploy,
            slug=slug,
            domain=domain,
            plan=plan,
            existing_files=existing_files,
        )
        if not build_with_retries(args.engine, prompt, outdir, plan, progress=progress):
            die("Update failed after retries; refusing to deploy a partial build.")
        progress.done()
        write_plan_file(outdir, plan)

    else:
        mode = "build"
        num_stages += 1  # planning is an extra stage for fresh builds

        print(f"""
============================================================
  autoship — describe it, ship it
============================================================
  spec:    {args.spec}
  engine:  {args.engine}
  output:  {outdir}/
  deploy:  {args.deploy}
============================================================
""")

        progress = Progress(num_stages, label="build")
        progress.stage("Planning")
        plan = plan_build(
            spec_text,
            deploy=args.deploy,
            slug=slug,
            domain=domain,
            engine=args.engine,
            workdir=outdir,
        )
        write_plan_file(outdir, plan)
        progress.done(f"{plan.get('app_type', 'web_app')} / {plan.get('stack', 'unknown')}")

        print(f"           caps: {capability_summary(plan)}")
        print(f"           turnkey: {'yes' if plan.get('turnkey') else 'no'}")
        if plan.get("secrets_needed"):
            print(f"           needs: {', '.join(plan['secrets_needed'])}")
        print()

        progress.stage("Building")
        prompt = build_prompt(
            program,
            spec_text,
            deploy=args.deploy,
            slug=slug,
            domain=domain,
            plan=plan,
        )
        if not build_with_retries(args.engine, prompt, outdir, plan, progress=progress):
            die("Build failed after retries; refusing to deploy a partial build.")
        write_plan_file(outdir, plan)
        (outdir / SPEC_FILE).write_text(spec_text)
        progress.done()

    deploy_result = None
    if args.deploy == "autoship":
        progress.stage("Deploying")
        deploy_result = deploy_autoship(
            outdir,
            slug,
            engine=args.engine,
            server=args.server,
            domain=domain,
            password=os.getenv("AUTOSHIP_SSH_PASSWORD"),
            key=args.ssh_key,
            email=args.email,
            api_url=args.api_url,
            api_token=args.api_token,
        )
        url = deploy_result.get("url", f"https://{slug}.{domain}")
        progress.done(url)

    action = "UPDATED" if updating else "SHIPPED"
    print(f"""
============================================================
  {action} -> {outdir}/
============================================================
""")

    if deploy_result:
        print(f"  LIVE URL:   {deploy_result['url']}")
        print(f"  HTTP URL:   {deploy_result['http_url']}")
        print(f"  CERT STATE: {deploy_result['subdomain_cert']}")
        if deploy_result.get("admin_email"):
            print(f"  ADMIN:      {deploy_result['admin_email']}")
        if deploy_result.get("admin_password"):
            print(f"  PASSWORD:   {deploy_result['admin_password']}")
        print()


if __name__ == "__main__":
    main()
