#!/usr/bin/env python3
"""ground control — your command center for autopilot, from discord.

Run parallel builds, tweet, post threads, search twitter, check github
stats, monitor engagement, and more. Talk naturally or use commands.

Skills are loaded from markdown files in skills/ — drop a new .md file
to teach ground control new CLI tools. No code changes needed.

Setup:
  1. Create a bot at https://discord.com/developers/applications
  2. Copy the bot token
  3. Invite bot to your server (Send Messages, Read Message History)
  4. Run: python3 ground_control.py --token YOUR_TOKEN --owner YOUR_USER_ID

Talk naturally:
  "build me a todo app with dark mode"
  "how's AutoPilot doing on github?"
  "search tweets about AI agents and like the best ones"
  "write a thread about my new project and post it"
  "who starred my repo today?"
  "check my twitter engagement this week"
  "how many followers do I have?"
  "stop the weather app build"
"""

import argparse
import ast
import asyncio
import json
import operator
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import discord

# ─── Config ──────────────────────────────────────────────────────────────────

AUTOPILOT = Path(__file__).resolve().parent.parent / "autopilot.py"
AUTOSHIP = Path(__file__).resolve().parent.parent.parent / "autoship" / "autoship.py"
BUILDS_DIR = Path.home() / ".autopilot" / "builds"
LOGS_DIR = Path.home() / ".autopilot" / "logs"
STRATEGY_DIR = Path.home() / ".autopilot" / "strategy"
SPECS_DIR = Path.home() / ".autopilot" / "specs"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
TOOLS_DIR = Path(__file__).resolve().parent / "tools"
CRONS_DIR = Path.home() / ".autopilot" / "crons"
MEMORY_DB = Path.home() / ".autopilot" / "memory.db"

for d in [BUILDS_DIR, LOGS_DIR, STRATEGY_DIR, SPECS_DIR, TOOLS_DIR, CRONS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def _skill_summary(path):
    """Extract first non-empty, non-heading line as a one-line description."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:100]
    return path.stem


def load_skill_index():
    """Build a compact index: skill name -> one-line description. ~20 tokens total."""
    if not SKILLS_DIR.exists():
        return ""
    lines = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        lines.append(f"- {f.stem}: {_skill_summary(f)}")
    return "\n".join(lines)


def load_skill(name):
    """Load a single skill file by name. Exact match only, no fuzzy guessing."""
    if not SKILLS_DIR.exists():
        return f"No skill named '{name}'."
    name_lower = name.lower().replace(" ", "-").replace("_", "-")
    # exact match (case-insensitive, normalize separators)
    for f in sorted(SKILLS_DIR.glob("*.md")):
        stem = f.stem.lower().replace("_", "-")
        if stem == name_lower:
            return f.read_text().strip()
    # no match — show available names so LLM can retry
    available = [f.stem for f in sorted(SKILLS_DIR.glob("*.md"))]
    return f"No skill named '{name}'. Available: {', '.join(available)}"


# ─── Memory Store ────────────────────────────────────────────────────────────

class MemoryStore:
    """SQLite-backed key/value store with categories, TTL, and full-text search."""

    def __init__(self, db_path=MEMORY_DB):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            ttl_days INTEGER DEFAULT 0
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_mem_cat ON memory(category)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_mem_key ON memory(category, key)")
        self.db.commit()

    def write(self, category, key, value, ttl_days=0):
        """Upsert a memory entry."""
        val = json.dumps(value) if not isinstance(value, str) else value
        existing = self.db.execute(
            "SELECT id FROM memory WHERE category=? AND key=?", (category, key)
        ).fetchone()
        if existing:
            self.db.execute(
                "UPDATE memory SET value=?, ts=datetime('now'), ttl_days=? WHERE id=?",
                (val, ttl_days, existing[0]),
            )
        else:
            self.db.execute(
                "INSERT INTO memory (category, key, value, ttl_days) VALUES (?,?,?,?)",
                (category, key, val, ttl_days),
            )
        self.db.commit()

    def read(self, category=None, key=None, since=None, limit=50):
        """Read memories by category/key/time."""
        q = "SELECT ts, category, key, value FROM memory WHERE 1=1"
        params = []
        if category:
            q += " AND category=?"
            params.append(category)
        if key:
            q += " AND key=?"
            params.append(key)
        if since:
            q += " AND ts >= datetime('now', ?)"
            params.append(since)
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self.db.execute(q, params).fetchall()
        return [{"ts": r[0], "category": r[1], "key": r[2], "value": r[3]} for r in rows]

    def search(self, query, limit=20):
        """Full-text search across keys and values."""
        pattern = f"%{query}%"
        rows = self.db.execute(
            "SELECT ts, category, key, value FROM memory "
            "WHERE key LIKE ? OR value LIKE ? ORDER BY ts DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
        return [{"ts": r[0], "category": r[1], "key": r[2], "value": r[3]} for r in rows]

    def stats(self):
        """Return per-category counts."""
        rows = self.db.execute(
            "SELECT category, COUNT(*) FROM memory GROUP BY category ORDER BY category"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def cleanup(self):
        """Delete expired entries."""
        self.db.execute(
            "DELETE FROM memory WHERE ttl_days > 0 "
            "AND datetime(ts, '+' || ttl_days || ' days') < datetime('now')"
        )
        self.db.commit()


# ─── Condition Evaluator ────────────────────────────────────────────────────

_SAFE_OPS = {
    "==": operator.eq, "!=": operator.ne,
    ">": operator.gt, "<": operator.lt,
    ">=": operator.ge, "<=": operator.le,
}


def eval_condition(expr, variables):
    """Safely evaluate a condition string like '{stars} > 100' or '{status} contains "up"'.

    Supports: ==, !=, >, <, >=, <=, contains "str", in [list], changed.
    No eval() -- only parsed comparisons.
    """
    # interpolate variables
    resolved = expr
    for k, v in variables.items():
        resolved = resolved.replace(f"{{{k}}}", str(v))

    # "changed" — special: compare current vs saved
    if resolved.strip() == "changed":
        return variables.get("_current") != variables.get("_previous")

    # "X contains Y"
    m = re.match(r'^(.+?)\s+contains\s+"(.*?)"$', resolved.strip())
    if m:
        return m.group(2) in str(m.group(1)).strip()

    # "X in [a, b, c]"
    m = re.match(r'^(.+?)\s+in\s+\[(.+)]$', resolved.strip())
    if m:
        val = m.group(1).strip().strip('"').strip("'")
        items = [x.strip().strip('"').strip("'") for x in m.group(2).split(",")]
        return val in items

    # standard comparisons: X op Y
    for op_str in sorted(_SAFE_OPS, key=len, reverse=True):
        parts = resolved.split(op_str, 1)
        if len(parts) == 2:
            left, right = parts[0].strip(), parts[1].strip()
            # try numeric
            try:
                left_v, right_v = float(left), float(right)
            except ValueError:
                left_v, right_v = left.strip('"').strip("'"), right.strip('"').strip("'")
            return _SAFE_OPS[op_str](left_v, right_v)

    return False


# ─── Cron Scheduler ─────────────────────────────────────────────────────────

DAYS_OF_WEEK = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}

# Timezone offsets from UTC (hours). Add more as needed.
TZ_OFFSETS = {"et": -5, "est": -5, "edt": -4, "ct": -6, "cst": -6, "cdt": -5,
              "pt": -8, "pst": -8, "pdt": -7, "utc": 0, "gmt": 0, "pkt": 5}


def _parse_time_and_tz(s):
    """Extract (hour, minute, tz_offset_hours) from a schedule string.
    Returns (None, None, 0) if no time found."""
    # match patterns like "9am", "10:30am", "14:00", "9am ET"
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*([a-z]{2,4})?\s*$', s)
    if not m:
        return None, None, 0
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    tz = m.group(4)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    tz_offset = TZ_OFFSETS.get(tz, 0) if tz else 0
    return hour, minute, tz_offset


def is_schedule_due(schedule, last_run_ts):
    """Check if a cron schedule is due given the last run timestamp.
    Returns True if it should run now."""
    s = schedule.strip().lower()
    now = time.time()

    # "every Xm/h/d" — simple interval
    m = re.match(r'every\s+(\d+)\s*m(?:in(?:ute)?s?)?$', s)
    if m:
        return now - last_run_ts >= int(m.group(1)) * 60
    m = re.match(r'every\s+(\d+)\s*h(?:ours?)?$', s)
    if m:
        return now - last_run_ts >= int(m.group(1)) * 3600
    m = re.match(r'every\s+(\d+)\s*d(?:ays?)?$', s)
    if m:
        return now - last_run_ts >= int(m.group(1)) * 86400
    if s == "hourly":
        return now - last_run_ts >= 3600

    # Time-aware schedules: "daily 9am ET", "weekly tuesday 10am ET"
    hour, minute, tz_offset = _parse_time_and_tz(s)
    if hour is None:
        # fallback: treat as daily
        return now - last_run_ts >= 86400

    # Convert current UTC time to target timezone
    from datetime import datetime, timezone, timedelta
    utc_now = datetime.now(timezone.utc)
    target_tz = timezone(timedelta(hours=tz_offset))
    local_now = utc_now.astimezone(target_tz)

    # Check if it's the right day for weekly schedules
    is_weekly = "weekly" in s
    if is_weekly:
        target_day = None
        for day_name, day_num in DAYS_OF_WEEK.items():
            if day_name in s:
                target_day = day_num
                break
        if target_day is not None and local_now.weekday() != target_day:
            return False

    # Check if we're past the target time today
    target_time_today = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < target_time_today:
        return False  # not time yet

    # Check we haven't already run today (or this week for weekly)
    if last_run_ts > 0:
        last_run_local = datetime.fromtimestamp(last_run_ts, tz=target_tz)
        if last_run_local.date() == local_now.date():
            return False  # already ran today

    return True


class CronScheduler:
    """Runs cron plans on schedule. Each plan is a JSON file in CRONS_DIR.

    Plan format:
    {
        "name": "star-milestone-tweet",
        "schedule": "every 6h",
        "steps": [
            {"type": "run", "command": "gh api repos/owner/repo --jq '.stargazers_count'", "save_as": "stars"},
            {"type": "condition", "expr": "{stars} > {last_stars}", "on_false": "done"},
            {"type": "memory_write", "category": "github", "key": "stars", "value": "{stars}"},
            {"type": "llm", "prompt": "Write a tweet celebrating hitting {stars} GitHub stars.", "save_as": "tweet"},
            {"type": "run", "command": "twitter post \"{tweet}\"", "label": "Posting tweet"},
            {"type": "done"}
        ],
        "state": {"last_stars": 0}
    }
    """

    def __init__(self, memory, notify_callback=None, engine="claude"):
        self.memory = memory
        self.notify = notify_callback  # async fn(text)
        self.engine = engine
        self._task = None
        self._running = False
        self._plans = {}  # name -> plan dict
        self._last_run = {}  # name -> timestamp
        self._load_plans()

    def _load_plans(self):
        """Load all plan JSON files from CRONS_DIR."""
        self._plans = {}
        for f in CRONS_DIR.glob("*.json"):
            try:
                plan = json.loads(f.read_text())
                self._plans[plan["name"]] = plan
            except Exception:
                pass

    def save_plan(self, plan):
        """Save a cron plan to disk."""
        path = CRONS_DIR / f"{plan['name']}.json"
        path.write_text(json.dumps(plan, indent=2))
        self._plans[plan["name"]] = plan

    def delete_plan(self, name):
        """Delete a cron plan."""
        path = CRONS_DIR / f"{name}.json"
        if path.exists():
            path.unlink()
        self._plans.pop(name, None)
        self._last_run.pop(name, None)

    def list_plans(self):
        """Return list of plans with schedule and last run info."""
        result = []
        for name, plan in self._plans.items():
            result.append({
                "name": name,
                "schedule": plan.get("schedule", "?"),
                "steps": len(plan.get("steps", [])),
                "last_run": self._last_run.get(name, "never"),
            })
        return result

    def start(self, loop):
        """Start the scheduler as an asyncio task."""
        if self._task:
            return
        self._running = True
        self._task = loop.create_task(self._loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self):
        """Main scheduler loop -- checks every 60s which plans are due."""
        while self._running:
            try:
                await asyncio.sleep(60)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.memory.cleanup)
                now = time.time()
                for name, plan in list(self._plans.items()):
                    last = self._last_run.get(name, 0)
                    if is_schedule_due(plan.get("schedule", "every 1h"), last):
                        self._last_run[name] = now
                        try:
                            await self._execute_plan(plan)
                        except Exception as e:
                            if self.notify:
                                await self.notify(f"Cron `{name}` error: {e}")
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    @staticmethod
    def _run_cmd(cmd, timeout=180, env=None):
        """Run a subprocess in a way that can be offloaded to a thread."""
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, env=env,
        )

    @staticmethod
    def _run_llm(prompt, env):
        """Run an LLM subprocess in a way that can be offloaded to a thread."""
        return subprocess.run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            capture_output=True, text=True, env=env, timeout=300,
        )

    async def _execute_plan(self, plan):
        """Execute a plan's steps sequentially. All subprocess calls are
        run in an executor to avoid blocking the Discord event loop."""
        loop = asyncio.get_event_loop()
        state = dict(plan.get("state", {}))
        variables = dict(state)

        for step in plan.get("steps", []):
            step_type = step.get("type")

            if step_type == "run":
                cmd = step.get("command", "")
                for k, v in variables.items():
                    # sanitize variable values for safe shell injection:
                    # strip quotes, backslashes, and other shell-breaking chars
                    safe_v = str(v).replace('"', '').replace("'", "").replace("\\", "").replace("`", "")
                    cmd = cmd.replace(f"{{{k}}}", safe_v)
                try:
                    result = await loop.run_in_executor(
                        None, lambda c=cmd: self._run_cmd(c)
                    )
                    output = (result.stdout + result.stderr).strip()
                    if result.returncode != 0 and self.notify:
                        await self.notify(
                            f"Cron `{plan.get('name')}` step failed:\n"
                            f"`{cmd[:200]}`\n```\n{output[:300]}\n```"
                        )
                        if step.get("on_fail", "") == "stop":
                            break
                except Exception as e:
                    output = f"error: {e}"
                    if self.notify:
                        await self.notify(f"Cron `{plan.get('name')}` step error: {e}")
                save_as = step.get("save_as")
                if save_as:
                    variables[save_as] = output

            elif step_type == "condition":
                expr = step.get("expr", "")
                save_key = step.get("track")
                if save_key and "changed" in expr:
                    variables["_current"] = variables.get(save_key, "")
                    variables["_previous"] = state.get(save_key, "")

                if not eval_condition(expr, variables):
                    on_false = step.get("on_false", "done")
                    if on_false == "done":
                        break

            elif step_type == "llm":
                prompt = step.get("prompt", "")
                for k, v in variables.items():
                    prompt = prompt.replace(f"{{{k}}}", str(v))
                env = os.environ.copy()
                env.pop("CLAUDECODE", None)
                try:
                    result = await loop.run_in_executor(
                        None, lambda p=prompt, e=env: self._run_llm(p, e)
                    )
                    output = result.stdout.strip()
                    # sanitize LLM output: strip markdown bold, quotes, extra whitespace
                    output = re.sub(r'\*+', '', output)
                    output = output.strip('"\'').strip()
                except Exception as e:
                    output = f"error: {e}"
                save_as = step.get("save_as")
                if save_as:
                    variables[save_as] = output

            elif step_type == "memory_write":
                cat = step.get("category", "cron")
                key = step.get("key", plan.get("name", "unknown"))
                val = step.get("value", "")
                for k, v in variables.items():
                    val = val.replace(f"{{{k}}}", str(v)) if isinstance(val, str) else val
                self.memory.write(cat, key, val)

            elif step_type == "memory_read":
                cat = step.get("category")
                key = step.get("key")
                since = step.get("since")
                rows = self.memory.read(category=cat, key=key, since=since)
                save_as = step.get("save_as", "memory_data")
                variables[save_as] = json.dumps(rows)

            elif step_type == "notify":
                text = step.get("text", "")
                for k, v in variables.items():
                    text = text.replace(f"{{{k}}}", str(v))
                if self.notify and text:
                    await self.notify(text)

            elif step_type == "done":
                break

        # persist state updates
        for step in plan.get("steps", []):
            save_as = step.get("save_as")
            if save_as and save_as in variables:
                state[save_as] = variables[save_as]
            # update tracked variables for "changed" conditions
            track = step.get("track")
            if track and track in variables:
                state[track] = variables[track]
        plan["state"] = state
        self.save_plan(plan)


# ─── Build Manager ───────────────────────────────────────────────────────────

class BuildManager:
    """Tracks multiple parallel builds."""

    def __init__(self):
        self.builds = {}  # name -> {process, thread, log_lines, start_time, spec, channel}

    def is_running(self, name):
        b = self.builds.get(name)
        return b and b["process"] and b["process"].poll() is None

    def active_builds(self):
        return {k: v for k, v in self.builds.items() if self.is_running(k)}

    def all_builds(self):
        return dict(self.builds)

    def get_status(self, name):
        b = self.builds.get(name)
        if not b:
            return None
        elapsed = time.time() - b["start_time"]
        running = self.is_running(name)
        return {
            "name": name,
            "running": running,
            "elapsed": elapsed,
            "log_lines": b["log_lines"],
            "spec": b["spec"],
            "returncode": b["process"].returncode if b["process"] and not running else None,
        }

    def get_summary(self):
        """Get a text summary of all builds for LLM context."""
        if not self.builds:
            return "No builds have been started."

        parts = []
        for name, b in self.builds.items():
            elapsed = time.time() - b["start_time"]
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            running = self.is_running(name)
            status = "RUNNING" if running else f"DONE (exit {b['process'].returncode})"
            recent = b["log_lines"][-10:] if b["log_lines"] else ["(no output)"]

            # check strategy for features/repo
            strategy = get_strategy(name)
            repo = strategy.get("repo_url", "") if strategy else ""
            features = []
            if strategy:
                for bh in strategy.get("build_history", []):
                    features.extend(bh.get("features", []))

            parts.append(f"BUILD: {name}\n"
                         f"  Status: {status}\n"
                         f"  Elapsed: {mins}m {secs}s\n"
                         f"  Repo: {repo or 'not created yet'}\n"
                         f"  Features: {', '.join(features[-8:]) or 'none yet'}\n"
                         f"  Recent output:\n    " + "\n    ".join(recent))

        return "\n\n".join(parts)

    async def start_build(self, name, spec_text, channel, loop,
                          engine="codex", iterations=5, reasoning=None):
        """Start a build in a background thread."""
        spec_path = SPECS_DIR / f"{name}.md"
        spec_path.write_text(spec_text)

        cmd = [sys.executable, str(AUTOPILOT), str(spec_path),
               "--build", "-e", engine, "--iterations", str(iterations)]
        if reasoning:
            cmd += ["--reasoning", reasoning]

        log_lines = []
        start_time = time.time()

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, env=env,
        )

        self.builds[name] = {
            "process": proc,
            "thread": None,
            "log_lines": log_lines,
            "start_time": start_time,
            "spec": spec_text,
            "channel": channel,
        }

        def run():
            last_milestone_time = 0
            milestone_interval = 10  # max 1 message per 10 seconds
            milestone_buffer = []

            for line in proc.stdout:
                line = line.rstrip()
                if not line.strip():
                    continue
                log_lines.append(line)

                # only match actual milestone lines (start of line, not substring)
                is_milestone = any(line.lstrip().startswith(kw) for kw in [
                    "ITERATION", "Analyzing", "Building", "Testing",
                    "Pushed", "COMPLETE", "Repo:", "Name:",
                    "ERROR:", "FAILED", "Step ",
                ])
                if is_milestone:
                    milestone_buffer.append(line[:200])

                now = time.time()
                if milestone_buffer and (now - last_milestone_time) >= milestone_interval:
                    # batch milestones into one message
                    batch = "\n".join(milestone_buffer[-5:])  # last 5 lines max
                    milestone_buffer.clear()
                    last_milestone_time = now
                    asyncio.run_coroutine_threadsafe(
                        channel.send(f"`[{name}]` ```\n{batch}\n```"),
                        loop
                    )
            proc.wait()

            # flush remaining milestones
            if milestone_buffer:
                batch = "\n".join(milestone_buffer[-5:])
                asyncio.run_coroutine_threadsafe(
                    channel.send(f"`[{name}]` ```\n{batch}\n```"),
                    loop
                )

            elapsed = time.time() - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            rc = proc.returncode

            strategy = get_strategy(name)
            fields = [("Time", f"{mins}m {secs}s", True)]
            if strategy:
                repo = strategy.get("repo_url", "")
                features = []
                for bh in strategy.get("build_history", []):
                    features.extend(bh.get("features", []))
                if repo:
                    fields.append(("Repo", repo, False))
                if features:
                    feat_list = "\n".join(f"+ {f}" for f in features[-10:])
                    fields.append(("Features", f"```\n{feat_list}\n```", False))

            color = 0x2ea043 if rc == 0 else 0xda3633
            # on failure, show last few meaningful log lines as description
            desc = ""
            if rc != 0 and log_lines:
                # find the last error-like lines
                error_lines = [l for l in log_lines[-20:] if any(
                    kw in l.lower() for kw in ["error", "failed", "refused", "timeout", "not found"]
                )]
                if error_lines:
                    desc = "```\n" + "\n".join(error_lines[-3:])[:500] + "\n```"
                else:
                    desc = "```\n" + "\n".join(log_lines[-3:])[:500] + "\n```"

            asyncio.run_coroutine_threadsafe(
                channel.send(embed=make_embed(
                    f"{'Build Complete' if rc == 0 else 'Build Failed'}: {name}",
                    desc, color=color, fields=fields,
                )),
                loop
            )

        thread = threading.Thread(target=run, daemon=True)
        self.builds[name]["thread"] = thread
        thread.start()

    def stop_build(self, name):
        b = self.builds.get(name)
        if b and b["process"] and b["process"].poll() is None:
            os.kill(b["process"].pid, signal.SIGTERM)
            return True
        return False

    def stop_all(self):
        stopped = []
        for name in list(self.builds.keys()):
            if self.stop_build(name):
                stopped.append(name)
        return stopped


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fmt_last_run(ts):
    """Format a Unix timestamp into a human-readable string."""
    if not ts:
        return "never"
    try:
        dt = datetime.fromtimestamp(float(ts))
        return dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return "unknown"


def make_embed(title, description="", color=0x58a6ff, fields=None):
    embed = discord.Embed(title=title, description=description[:4096], color=color,
                          timestamp=datetime.now())
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value[:1024], inline=inline)
    embed.set_footer(text="ground control")
    return embed


def get_strategy(project_name):
    path = STRATEGY_DIR / f"{project_name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def tail_lines(path, n=20):
    try:
        lines = Path(path).read_text().strip().splitlines()
        return lines[-n:]
    except Exception:
        return []


def format_log_entry(line):
    try:
        entry = json.loads(line)
        action = entry.get("action", "?")
        ok = "+" if entry.get("ok") else "-"
        detail = entry.get("detail", "")[:80]
        ts = entry.get("timestamp", "")[:19]
        return f"`{ts}` {ok} **{action}** {detail}"
    except Exception:
        return line[:100]


# ─── Conversation Buffer ────────────────────────────────────────────────────

class ConversationBuffer:
    """Rolling window of recent messages per channel/user. In-memory, ephemeral.

    Keeps last N exchanges. Auto-expires after a gap of silence.
    Truncates bot responses to just the conversational text (strips ACTION lines,
    command output, embeds). This keeps token cost at ~150-250 for 5 exchanges.
    """

    def __init__(self, max_exchanges=5, expire_minutes=15):
        self.max_exchanges = max_exchanges
        self.expire_seconds = expire_minutes * 60
        self._buffers = {}  # (channel_id, user_id) -> [{"role": "user"/"bot", "text": str, "ts": float}]
        self._call_count = 0

    def _key(self, channel_id, user_id):
        return (channel_id, user_id)

    def _expire(self, key):
        """Drop the buffer if last message is too old."""
        buf = self._buffers.get(key, [])
        if buf and (time.time() - buf[-1]["ts"]) > self.expire_seconds:
            self._buffers.pop(key, None)

    def sweep_expired(self):
        """Remove all expired buffers. Call periodically to prevent memory leak."""
        now = time.time()
        expired = [k for k, v in self._buffers.items()
                   if v and (now - v[-1]["ts"]) > self.expire_seconds]
        for k in expired:
            del self._buffers[k]

    def add_user(self, channel_id, user_id, text):
        key = self._key(channel_id, user_id)
        self._expire(key)
        self._call_count += 1
        if self._call_count % 50 == 0:
            self.sweep_expired()
        if key not in self._buffers:
            self._buffers[key] = []
        self._buffers[key].append({"role": "user", "text": text[:300], "ts": time.time()})
        # trim to max exchanges (each exchange = user + bot = 2 entries)
        while len(self._buffers[key]) > self.max_exchanges * 2:
            self._buffers[key].pop(0)

    def add_bot(self, channel_id, user_id, text):
        key = self._key(channel_id, user_id)
        if key not in self._buffers:
            self._buffers[key] = []
        # strip ACTION lines and truncate -- only keep conversational text
        clean = "\n".join(
            line for line in text.split("\n")
            if not line.strip().startswith("ACTION:")
        ).strip()
        self._buffers[key].append({"role": "bot", "text": clean[:200], "ts": time.time()})
        while len(self._buffers[key]) > self.max_exchanges * 2:
            self._buffers[key].pop(0)

    def get_context(self, channel_id, user_id):
        """Return formatted conversation history for prompt injection."""
        key = self._key(channel_id, user_id)
        self._expire(key)
        buf = self._buffers.get(key, [])
        if not buf:
            return ""
        lines = []
        for entry in buf:
            role = "User" if entry["role"] == "user" else "You"
            lines.append(f"{role}: {entry['text']}")
        return "\nRECENT CONVERSATION:\n" + "\n".join(lines) + "\n"


# ─── LLM for conversational mode ────────────────────────────────────────────

def llm_respond(user_message, build_summary, engine="codex", memory=None, conversation="",
                model="gpt-5.4", reasoning="medium"):
    """Send user message + build context to LLM, get response + optional action."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    skill_index = load_skill_index()

    # load relevant memory context (preferences + recent)
    memory_context = ""
    if memory:
        prefs = memory.read(category="prefs", limit=10)
        if prefs:
            pref_lines = [f"- {r['key']}: {r['value']}" for r in prefs]
            memory_context = "\nSAVED PREFERENCES:\n" + "\n".join(pref_lines) + "\n"
        stats = memory.stats()
        if stats:
            memory_context += f"\nMEMORY CATEGORIES: {', '.join(f'{k} ({v})' for k, v in stats.items())}\n"

    prompt = f"""You are Ground Control, a persistent Discord bot for AutoPilot (an autonomous build + growth engine).
You run as a long-lived process on the user's machine. You have access to CLI tools and can run any command.
You CAN restart yourself (use the restart action).
Engine: {engine} | Model: {model}{f' | Reasoning: {reasoning}' if engine == 'codex' else ''}
Tools dir: {TOOLS_DIR} (run scripts as: python3 {TOOLS_DIR}/<script.py>)
Working dir: {Path(__file__).resolve().parent}
You can run any shell command to check your own environment, versions, or capabilities.

CURRENT STATE:
{build_summary}

AVAILABLE PROJECTS:
{list_projects_text()}
{memory_context}{conversation}
SKILLS (use load_skill action to load full docs when needed):
{skill_index}
If no skill exists for what the user wants, load the "meta" skill and create one on the fly.

USER MESSAGE: {user_message}

Respond conversationally in 1-3 sentences. Be concise and helpful. Sound like a teammate, not a bot.

If the user wants you to DO something, include ACTION blocks at the end of your response.
You can include MULTIPLE actions — they run in sequence.

Built-in actions:
ACTION: {{"type": "build", "spec": "the spec text", "name": "project-name"}}
ACTION: {{"type": "autoship", "spec": "the spec text", "slug": "app-name", "engine": "claude"}}
ACTION: {{"type": "stop", "name": "project-name"}}
ACTION: {{"type": "stop_all"}}
ACTION: {{"type": "restart"}}
ACTION: {{"type": "engine", "engine": "codex", "model": "gpt-5.4", "reasoning": "medium"}}
ACTION: {{"type": "thread", "tweets": ["tweet 1", "tweet 2", "tweet 3"]}}

Load a skill for detailed docs (use before complex tasks with that skill):
ACTION: {{"type": "load_skill", "name": "twitter"}}

Cron (schedule automated tasks — LLM plans once, runs forever without LLM):
ACTION: {{"type": "cron_create", "plan": {{"name": "plan-name", "schedule": "every 6h", "steps": [...], "state": {{}}}}}}
ACTION: {{"type": "cron_list"}}
ACTION: {{"type": "cron_delete", "name": "plan-name"}}

Memory (persistent storage — query on demand, never auto-loaded):
ACTION: {{"type": "memory_write", "category": "prefs", "key": "style", "value": "technical tweets"}}
ACTION: {{"type": "memory_read", "category": "tweets", "since": "-7 days"}}
ACTION: {{"type": "memory_search", "query": "preferences"}}

Self-improvement (create reusable tools and skills):
ACTION: {{"type": "create_tool", "name": "tool_name.py", "code": "#!/usr/bin/env python3\\n..."}}
ACTION: {{"type": "create_skill", "name": "skill_name.md", "content": "# Skill Name\\n..."}}

Codebase access (read, browse, and edit your own source code):
ACTION: {{"type": "read_file", "path": "plugins/tools/twitter_engine.py"}}
ACTION: {{"type": "read_file", "path": "plugins/tools/twitter_engine.py", "start_line": 100, "end_line": 150}}
ACTION: {{"type": "list_files", "path": "plugins/tools"}}
ACTION: {{"type": "list_files", "path": "."}}
ACTION: {{"type": "edit_code", "instruction": "Add a quality check to the engage function in twitter_engine.py that rejects generic replies"}}

edit_code is POWERFUL: it spawns Claude Code as a coding agent against the codebase. Use it for:
- Fixing bugs in existing tools (twitter, job hunter, etc.)
- Adding features the user asks for
- Refactoring or improving code quality
- Any change that touches multiple files or requires understanding existing code
After edit_code changes, use restart to pick up the new code.
Prefer edit_code over create_tool when modifying existing files. create_tool is for brand new scripts only.

Job hunting (built-in, no skill loading needed — use these directly):
ACTION: {{"type": "job_search", "keywords": "", "limit": 10, "min_score": 10}}
ACTION: {{"type": "job_apply", "url": "https://job-boards.greenhouse.io/company/jobs/123", "dry_run": false}}
To search and apply: first job_search, then in the follow-up use job_apply with URLs from the results. Do NOT re-search.

Generic shell action (use this for twitter CLI, gh CLI, or any command):
ACTION: {{"type": "run", "command": "twitter post \\"hello world\\"", "label": "Posting tweet"}}
ACTION: {{"type": "run", "command": "gh api repos/owner/repo --jq '.stargazers_count'", "label": "Checking stars"}}
ACTION: {{"type": "run", "command": "python3 plugins/tools/my_tool.py args", "label": "Running custom tool"}}

You can chain multiple run actions:
ACTION: {{"type": "run", "command": "twitter search \\"AI agents\\" --json -n 5", "label": "Searching tweets"}}
ACTION: {{"type": "run", "command": "twitter like TWEET_ID", "label": "Liking tweet"}}

Rules:
- Always add --json flag when you need to parse output from twitter or gh commands
- For threads: use the built-in thread action, not multiple run actions
- Keep tweets under 280 characters
- BIAS TOWARD ACTION. If the user asks you to do something, suggests an approach, or gives a correction, DO IT with ACTIONs immediately. Do not just agree or explain what you would do. Short response + ACTIONs. Talk is cheap, ACTIONs are real.
- For pure status/info questions with no action needed, answer from your knowledge and context.
- Do NOT use em dashes. Do not be overly enthusiastic. Be direct.
- When the user asks "how is X doing" about a project, check both GitHub stats AND recent tweets about it
- If you can't do something with existing skills, CREATE a new tool and skill for it. Write a script to plugins/tools/, document it in plugins/skills/, then use it. You are self-improving.
- Tools you create persist. Next time someone asks for the same thing, the skill will already be loaded.
- MEMORY: When the user shares preferences, personality, voice, tone, or any "remember this" info, ALWAYS save it with memory_write (category="prefs"). Do not just say "locked in" without actually saving.
- MEMORY: When writing tweets, threads, or content, check SAVED PREFERENCES above and follow them.
- MEMORY: When the user asks "what do you remember" or "what do you know about me", use memory_search to look it up.
- CRITICAL: Never claim you did something or will do something without including the ACTION to actually do it. If you say "switching to codex", include the engine action. If you say "saved", include the memory_write action. If you say "I'll search", include the run action. Words without ACTIONs do nothing. Every "I'll" must have a matching ACTION below it.
- When the user corrects your approach or suggests a different method, apply the correction IMMEDIATELY with new ACTIONs. Do not just say "good idea" and wait."""

    timeout = 300  # 5 minutes -- generous but not infinite

    try:
        if engine == "claude":
            claude_cmd = ["claude", "-p", prompt, "--no-session-persistence"]
            if model and model != "gpt-5.4":  # skip codex-specific default
                claude_cmd.extend(["--model", model])
            result = subprocess.run(
                claude_cmd,
                capture_output=True, text=True, timeout=timeout, env=env,
            )
        else:
            result = subprocess.run(
                ["codex", "exec",
                 "-c", f'model="{model}"',
                 "-c", f'model_reasoning_effort="{reasoning}"',
                 prompt],
                capture_output=True, text=True, timeout=timeout, env=env,
            )
        if result.returncode != 0:
            return "Hit a snag. Try again or rephrase.", None
        return parse_llm_response(result.stdout.strip())
    except subprocess.TimeoutExpired:
        return "Still processing after 5 min. Try breaking it into smaller steps.", None
    except Exception as e:
        return f"Error: {e}", None


def parse_llm_response(text):
    """Split LLM response into message and list of actions."""
    actions = []
    message_lines = []

    for line in text.split("\n"):
        match = re.match(r'ACTION:\s*(\{.*\})', line.strip())
        if match:
            try:
                actions.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass
        else:
            message_lines.append(line)

    message = "\n".join(message_lines).strip()
    return message, actions


def list_projects_text():
    """Get a text list of projects for LLM context."""
    if not BUILDS_DIR.exists():
        return "None"
    projects = [d.name for d in BUILDS_DIR.iterdir() if d.is_dir()]
    if not projects:
        return "None"
    lines = []
    for p in sorted(projects):
        strategy = get_strategy(p)
        repo = strategy.get("repo_url", "") if strategy else ""
        lines.append(f"- {p}" + (f" ({repo})" if repo else ""))
    return "\n".join(lines)


# ─── Social actions ──────────────────────────────────────────────────────────

def do_tweet(text):
    """Post a tweet using twitter-cli."""
    try:
        result = subprocess.run(
            ["twitter", "post", "--json", text],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout.strip()
        try:
            data = json.loads(output)
            if data.get("ok"):
                tweet_id = data.get("data", {}).get("id", "")
                url = data.get("data", {}).get("url", "")
                return True, url or f"Posted (id: {tweet_id})"
            else:
                err = data.get("error", {}).get("message", "Unknown error")
                return False, err
        except json.JSONDecodeError:
            if result.returncode == 0:
                return True, output[:200]
            return False, output[:200]
    except FileNotFoundError:
        return False, "twitter-cli not installed"
    except Exception as e:
        return False, str(e)


def do_thread(tweets):
    """Post a Twitter thread. tweets is a list of strings."""
    if not tweets:
        return False, "No tweets to post"

    results = []
    last_id = None

    for i, tweet_text in enumerate(tweets):
        try:
            if i == 0:
                # first tweet
                cmd = ["twitter", "post", "--json", tweet_text]
            else:
                # reply to previous tweet
                cmd = ["twitter", "reply", "--json", last_id, tweet_text]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            output = result.stdout.strip()

            try:
                data = json.loads(output)
                if data.get("ok"):
                    last_id = data.get("data", {}).get("id", "")
                    url = data.get("data", {}).get("url", "")
                    results.append(f"Tweet {i+1}: {url}")
                else:
                    err = data.get("error", {}).get("message", "Unknown error")
                    results.append(f"Tweet {i+1}: FAILED - {err}")
                    return False, "\n".join(results)
            except json.JSONDecodeError:
                if result.returncode == 0:
                    results.append(f"Tweet {i+1}: posted")
                else:
                    results.append(f"Tweet {i+1}: FAILED - {output[:100]}")
                    return False, "\n".join(results)

        except Exception as e:
            results.append(f"Tweet {i+1}: FAILED - {e}")
            return False, "\n".join(results)

    return True, "\n".join(results)


# ─── Bot ─────────────────────────────────────────────────────────────────────

def create_bot(token, channel_id, owner_id, engine="codex", model="gpt-5.4", reasoning="medium"):
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    builds = BuildManager()
    memory_store = MemoryStore()
    conv_buffer = ConversationBuffer()
    bot_state = {
        "engine": engine,
        "model": model,
        "reasoning": reasoning,
    }  # mutable so commands/actions can switch live
    cron_scheduler = CronScheduler(memory_store, engine=engine)

    @client.event
    async def on_ready():
        print(f"  ground control online as {client.user}")

        # start cron scheduler with notification callback
        notify_ch = client.get_channel(channel_id) if channel_id else None

        async def cron_notify(text):
            ch = notify_ch
            if not ch and owner_id:
                user = await client.fetch_user(owner_id)
                ch = await user.create_dm()
            if ch:
                await ch.send(embed=make_embed("Cron", text, color=0xa78bfa))

        cron_scheduler.notify = cron_notify
        cron_scheduler.start(asyncio.get_event_loop())
        plans = cron_scheduler.list_plans()
        print(f"  crons: {len(plans)} active")
        print(f"  memory: {MEMORY_DB}")

        if channel_id:
            print(f"  watching channel: {channel_id}")
            ch = client.get_channel(channel_id)
            if ch:
                await ch.send(embed=make_embed(
                    "Ground Control Online",
                    "Talk to me naturally or use `!help` for commands.\n"
                    "I can run parallel builds, tweet, schedule crons, and more.",
                    color=0x58a6ff,
                ))
        print(f"  DMs: enabled")
        if owner_id:
            print(f"  owner: {owner_id}")

    @client.event
    async def on_message(message):
        if message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_allowed_channel = channel_id and message.channel.id == channel_id
        is_owner = owner_id and message.author.id == owner_id

        # allow: DMs from owner (or anyone if no owner set), or the designated channel
        if is_dm:
            if owner_id and not is_owner:
                return  # DM from someone else, ignore
        elif not is_allowed_channel:
            return

        content = message.content.strip()
        if not content:
            return

        loop = asyncio.get_event_loop()

        # ── Commands (start with !) ──
        # only parse first line as command; remainder goes to conversational mode
        if content.startswith("!"):
            first_line = content.split("\n", 1)[0].strip()
            parts = first_line.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd == "!help":
                await message.channel.send(embed=make_embed(
                    "Ground Control",
                    "Talk naturally or use commands. I can run twitter, github, and autopilot commands.",
                    color=0x58a6ff,
                    fields=[
                        ("!build <spec>", "Start a build (parallel OK)", False),
                        ("!status", "Show all active builds", False),
                        ("!stop <name>", "Stop a build (!stop all for all)", False),
                        ("!tweet <text>", "Post a tweet", False),
                        ("!thread <topic>", "Compose and post a Twitter thread", False),
                        ("!crons", "List active cron jobs", False),
                        ("!memory [query]", "Search or show memory stats", False),
                        ("!logs [project]", "Show recent logs", False),
                        ("!projects", "List all projects", False),
                        ("!jobs [keywords]", "Search AI/ML jobs across boards", False),
                        ("!apply <url>", "Auto-apply to a job (--dry-run to preview)", False),
                        ("!engine [claude|codex]", "Show or switch LLM engine", False),
                        ("!model [name]", "Show or switch model (e.g. gpt-5.4, o3)", False),
                        ("!reasoning [low|med|high]", "Show or switch reasoning level", False),
                        ("!restart", "Restart the bot (picks up new skills/code)", False),
                        ("Natural language", "\"how's AutoPilot doing on github?\"\n"
                         "\"search tweets about AI agents\"\n"
                         "\"like that tweet\"\n"
                         "\"check my follower count\"\n"
                         "\"who starred my repo today?\"", False),
                    ]
                ))

            elif cmd == "!build":
                spec_text = args

                if message.attachments:
                    for att in message.attachments:
                        if att.filename.endswith(".md"):
                            spec_text = (await att.read()).decode("utf-8")
                            break

                if not spec_text:
                    await message.channel.send(
                        "Send a spec with the command or attach a `.md` file.\n"
                        "Example: `!build A todo app with dark mode`")
                    return

                # generate a short name
                name = re.sub(r'[^a-z0-9]+', '-', spec_text[:40].lower()).strip('-')
                if not name:
                    name = f"build-{int(time.time())}"

                if builds.is_running(name):
                    await message.channel.send(f"`{name}` is already running.")
                    return

                await message.channel.send(embed=make_embed(
                    f"Build Started: {name}",
                    f"```\n{spec_text[:500]}\n```",
                    color=0x2ea043,
                    fields=[
                        ("Engine", bot_state["engine"], True),
                        ("Parallel builds", str(len(builds.active_builds()) + 1), True),
                    ]
                ))
                await builds.start_build(name, spec_text, message.channel, loop, engine=bot_state["engine"])

            elif cmd == "!status":
                active = builds.active_builds()
                if not active:
                    await message.channel.send(embed=make_embed(
                        "No Active Builds",
                        "Nothing running. Use `!build` to start one.",
                        color=0x8b949e,
                    ))
                    return

                for name in active:
                    status = builds.get_status(name)
                    mins = int(status["elapsed"] // 60)
                    secs = int(status["elapsed"] % 60)
                    recent = status["log_lines"][-10:] or ["(no output yet)"]
                    log_text = "\n".join(recent)
                    if len(log_text) > 900:
                        log_text = log_text[-900:]

                    await message.channel.send(embed=make_embed(
                        f"Building: {name}",
                        f"```\n{log_text}\n```",
                        color=0xff9f1c,
                        fields=[("Elapsed", f"{mins}m {secs}s", True)],
                    ))

            elif cmd == "!stop":
                target = args.strip()
                if target == "all":
                    stopped = builds.stop_all()
                    if stopped:
                        await message.channel.send(embed=make_embed(
                            "All Builds Stopped",
                            "Killed: " + ", ".join(stopped),
                            color=0xda3633,
                        ))
                    else:
                        await message.channel.send("Nothing running.")
                elif target:
                    if builds.stop_build(target):
                        await message.channel.send(embed=make_embed(
                            "Build Stopped", f"Killed: **{target}**", color=0xda3633,
                        ))
                    else:
                        await message.channel.send(f"No running build named `{target}`.")
                else:
                    active = list(builds.active_builds().keys())
                    if len(active) == 1:
                        builds.stop_build(active[0])
                        await message.channel.send(embed=make_embed(
                            "Build Stopped", f"Killed: **{active[0]}**", color=0xda3633,
                        ))
                    elif active:
                        await message.channel.send(
                            f"Multiple builds running: {', '.join(active)}\n"
                            f"Use `!stop <name>` or `!stop all`.")
                    else:
                        await message.channel.send("Nothing running.")

            elif cmd == "!tweet":
                if not args:
                    await message.channel.send("Usage: `!tweet your tweet text here`")
                    return
                ok, result = await loop.run_in_executor(None, do_tweet, args)
                color = 0x2ea043 if ok else 0xda3633
                await message.channel.send(embed=make_embed(
                    "Tweeted" if ok else "Tweet Failed",
                    result, color=color,
                ))

            elif cmd == "!thread":
                if not args:
                    await message.channel.send(
                        "Tell me what to thread about and I'll compose it.\n"
                        "Example: `!thread write a thread about my AutoPilot project`")
                    return
                # use LLM to compose the thread
                async with message.channel.typing():
                    compose_result = await loop.run_in_executor(
                        None, llm_respond,
                        f"Write a Twitter thread about: {args}\n\n"
                        f"Return it as ACTION: {{\"type\": \"thread\", \"tweets\": [\"tweet1\", \"tweet2\", ...]}}.\n"
                        f"Each tweet must be under 280 characters. Make it engaging. 4-7 tweets is ideal. "
                        f"First tweet should hook. Last tweet should have a call to action.",
                        builds.get_summary(), bot_state["engine"], memory_store, "",
                        bot_state["model"], bot_state["reasoning"]
                    )
                response_text, thread_actions = compose_result
                if response_text:
                    await message.channel.send(response_text[:2000])
                thread_action = next((a for a in thread_actions if a.get("type") == "thread"), None)
                if thread_action:
                    tweets = thread_action.get("tweets", [])
                    if tweets:
                        # show preview
                        preview = "\n\n".join(f"**{i+1}.** {t}" for i, t in enumerate(tweets))
                        await message.channel.send(embed=make_embed(
                            f"Posting thread ({len(tweets)} tweets)...",
                            preview[:4000], color=0x1da1f2,
                        ))
                        ok, result = await loop.run_in_executor(None, do_thread, tweets)
                        color = 0x2ea043 if ok else 0xda3633
                        await message.channel.send(embed=make_embed(
                            "Thread Posted" if ok else "Thread Failed",
                            result[:2000], color=color,
                        ))

            elif cmd == "!crons":
                target = args.strip().lower()
                if target == "clear" or target == "delete all":
                    plans = cron_scheduler.list_plans()
                    for p in plans:
                        cron_scheduler.delete_plan(p["name"])
                    await message.channel.send(embed=make_embed(
                        "All Crons Deleted",
                        f"Removed {len(plans)} cron(s).", color=0xda3633,
                    ))
                elif target.startswith("delete "):
                    name = target[7:].strip()
                    if name in [p["name"] for p in cron_scheduler.list_plans()]:
                        cron_scheduler.delete_plan(name)
                        await message.channel.send(embed=make_embed(
                            "Cron Deleted", f"Removed: **{name}**", color=0xda3633,
                        ))
                    else:
                        await message.channel.send(f"No cron named `{name}`.")
                else:
                    plans = cron_scheduler.list_plans()
                    if plans:
                        lines = [f"**{p['name']}** — `{p['schedule']}` ({p['steps']} steps, last: {_fmt_last_run(p['last_run'])})"
                                 for p in plans]
                        await message.channel.send(embed=make_embed(
                            f"Active Crons ({len(plans)})", "\n".join(lines), color=0x58a6ff,
                        ))
                    else:
                        await message.channel.send("No crons set up. Ask me to schedule something.")

            elif cmd == "!memory":
                if args.strip():
                    rows = memory_store.search(args.strip())
                    if rows:
                        lines = [f"`{r['ts'][:16]}` **{r['category']}/{r['key']}**: {r['value'][:100]}"
                                 for r in rows[:10]]
                        await message.channel.send(embed=make_embed(
                            f"Memory: \"{args.strip()}\"", "\n".join(lines), color=0x58a6ff,
                        ))
                    else:
                        await message.channel.send(f"No memories matching \"{args.strip()}\"")
                else:
                    stats = memory_store.stats()
                    if stats:
                        lines = [f"**{cat}**: {count} entries" for cat, count in stats.items()]
                        await message.channel.send(embed=make_embed(
                            "Memory Stats", "\n".join(lines), color=0x58a6ff,
                        ))
                    else:
                        await message.channel.send("Memory is empty.")

            elif cmd == "!logs":
                project = args.strip()
                if not project:
                    logs = list(LOGS_DIR.glob("*.jsonl"))
                    if logs:
                        names = "\n".join(f"- `{l.stem}`" for l in sorted(logs)[-10:])
                        await message.channel.send(embed=make_embed(
                            "Available Logs", names, color=0x58a6ff,
                        ))
                    else:
                        await message.channel.send("No logs found.")
                    return

                log_path = LOGS_DIR / f"{project}.jsonl"
                if not log_path.exists():
                    await message.channel.send(f"No log for `{project}`")
                    return
                lines = tail_lines(log_path, 10)
                formatted = "\n".join(format_log_entry(l) for l in lines)
                await message.channel.send(embed=make_embed(
                    f"Logs: {project}", formatted[:2000], color=0x58a6ff,
                ))

            elif cmd == "!projects":
                if not BUILDS_DIR.exists():
                    await message.channel.send("No projects built yet.")
                    return
                projects = [d for d in BUILDS_DIR.iterdir() if d.is_dir()]
                if not projects:
                    await message.channel.send("No projects built yet.")
                    return
                lines = []
                for p in sorted(projects):
                    files = list(f for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)
                    total_size = sum(f.stat().st_size for f in files)
                    size_str = f"{total_size/1024:.0f}KB" if total_size > 1024 else f"{total_size}B"
                    strategy = get_strategy(p.name)
                    repo = strategy.get("repo_url", "") if strategy else ""
                    repo_str = f" [{repo}]" if repo else ""
                    lines.append(f"**{p.name}** — {len(files)} files, {size_str}{repo_str}")
                await message.channel.send(embed=make_embed(
                    "Projects", "\n".join(lines[:15]), color=0x58a6ff,
                ))

            elif cmd == "!engine":
                target = args.strip().lower()
                if target in ("claude", "codex"):
                    bot_state["engine"] = target
                    # set sensible default model when switching engines
                    if target == "codex" and bot_state["model"] in ("sonnet", "haiku", "opus"):
                        bot_state["model"] = "gpt-5.4"
                    elif target == "claude" and bot_state["model"] in ("gpt-5.4", "o3", "o4-mini"):
                        bot_state["model"] = "sonnet"
                    detail = f"Switched to **{target}** (model: {bot_state['model']})."
                    await message.channel.send(embed=make_embed(
                        f"Engine: {target}", detail, color=0x2ea043,
                    ))
                elif target:
                    await message.channel.send(f"Unknown engine `{target}`. Use `claude` or `codex`.")
                else:
                    if bot_state["engine"] == "codex":
                        desc = (f"**Engine:** codex\n"
                                f"**Model:** {bot_state['model']}\n"
                                f"**Reasoning:** {bot_state['reasoning']}\n\n"
                                f"Switch: `!engine claude`, `!model o3`, `!reasoning high`")
                    else:
                        desc = (f"**Engine:** claude\n\n"
                                f"Switch: `!engine codex` (supports model/reasoning)")
                    await message.channel.send(embed=make_embed(
                        "Current Config", desc, color=0x58a6ff,
                    ))

            elif cmd == "!model":
                if args.strip():
                    bot_state["model"] = args.strip()
                    await message.channel.send(embed=make_embed(
                        f"Model: {args.strip()}",
                        f"Switched to **{args.strip()}** ({bot_state['engine']}).",
                        color=0x2ea043,
                    ))
                else:
                    engine = bot_state["engine"]
                    hint = "claude: sonnet, haiku, opus | codex: gpt-5.4, o3, o4-mini"
                    await message.channel.send(f"Current model: **{bot_state['model']}** ({engine})\nOptions: `{hint}`")

            elif cmd == "!reasoning":
                if bot_state["engine"] != "codex":
                    await message.channel.send("Reasoning only applies to codex. Switch first: `!engine codex`")
                elif args.strip().lower() in ("low", "medium", "high"):
                    bot_state["reasoning"] = args.strip().lower()
                    await message.channel.send(embed=make_embed(
                        f"Reasoning: {args.strip().lower()}",
                        f"Set to **{args.strip().lower()}**.",
                        color=0x2ea043,
                    ))
                elif args.strip():
                    await message.channel.send(f"Unknown level `{args.strip()}`. Use `low`, `medium`, or `high`.")
                else:
                    await message.channel.send(f"Current reasoning: **{bot_state['reasoning']}**. Change with `!reasoning high`")

            elif cmd == "!jobs":
                # quick job search
                limit = 5
                min_score = 10
                extra_args = ""
                if args.strip():
                    # parse optional flags like "!jobs 10" or "!jobs --limit 20"
                    extra_args = args.strip()
                job_hunter = TOOLS_DIR / "job_hunter.py"
                if not job_hunter.exists():
                    await message.channel.send("Job hunter tool not found.")
                    return
                search_cmd = f"python3 {job_hunter} --search --limit {limit} --min-score {min_score}"
                if extra_args:
                    search_cmd += f" --keywords \"{extra_args}\""
                await message.channel.send(embed=make_embed(
                    "Job Hunt", f"Searching job boards...", color=0x58a6ff))
                try:
                    result = await loop.run_in_executor(
                        None, lambda: subprocess.run(
                            search_cmd, shell=True, capture_output=True,
                            text=True, timeout=180
                        )
                    )
                    output = (result.stdout or "No results.").strip()
                    if len(output) > 1900:
                        output = output[:1900] + "\n..."
                    await message.channel.send(f"```\n{output}\n```")
                except subprocess.TimeoutExpired:
                    await message.channel.send("Job search timed out (3min limit).")

            elif cmd == "!apply":
                # auto-apply to a job URL
                if not args.strip():
                    await message.channel.send(
                        "Usage: `!apply <job-url>` — auto-fill and submit\n"
                        "`!apply --dry-run <job-url>` — fill but don't submit")
                    return
                job_applier = TOOLS_DIR / "job_applier.py"
                if not job_applier.exists():
                    await message.channel.send("Job applier tool not found.")
                    return
                dry_run = "--dry-run" in args
                url = args.replace("--dry-run", "").strip()
                if not url.startswith("http"):
                    await message.channel.send("Provide a valid job URL starting with http.")
                    return
                mode = "DRY RUN" if dry_run else "LIVE"
                await message.channel.send(embed=make_embed(
                    "Auto Apply", f"Opening {url}\nMode: **{mode}**", color=0x3fb950))
                apply_cmd = f"python3 {job_applier} --url \"{url}\""
                if dry_run:
                    apply_cmd += " --dry-run"
                try:
                    result = await loop.run_in_executor(
                        None, lambda: subprocess.run(
                            apply_cmd, shell=True, capture_output=True,
                            text=True, timeout=300,
                            input="\n" if dry_run else None  # auto-press Enter for dry-run
                        )
                    )
                    output = (result.stdout or "No output.").strip()
                    if len(output) > 1900:
                        output = output[:1900] + "\n..."
                    await message.channel.send(f"```\n{output}\n```")
                except subprocess.TimeoutExpired:
                    await message.channel.send("Application timed out (5min limit).")

            elif cmd == "!restart":
                await message.channel.send(embed=make_embed(
                    "Restarting...",
                    "Picking up new skills and code. Back in a sec.",
                    color=0xff9f1c,
                ))
                await client.close()
                os.execv(sys.executable, [sys.executable] + sys.argv)

            # if there are extra lines after the command, process them as conversation
            extra_lines = content.split("\n", 1)
            if len(extra_lines) > 1 and extra_lines[1].strip():
                content = extra_lines[1].strip()
                # fall through to conversational mode
            else:
                return  # handled command, done

        # ── Conversational mode (no ! prefix) ──
        # multi-step loop: run actions, feed results back to LLM if needed
        user_message = content
        max_steps = 4
        chan_id = message.channel.id
        user_id = message.author.id
        conv_buffer.add_user(chan_id, user_id, content)

        for step in range(max_steps):
            async with message.channel.typing():
                build_summary = builds.get_summary()
                conversation = conv_buffer.get_context(chan_id, user_id)
                response, actions = await loop.run_in_executor(
                    None, llm_respond, user_message, build_summary,
                    bot_state["engine"], memory_store, conversation,
                    bot_state["model"], bot_state["reasoning"]
                )

            if response:
                await message.channel.send(response[:2000])
                # only record the first response (the direct reply, not follow-ups)
                if step == 0:
                    conv_buffer.add_bot(chan_id, user_id, response)

            if not actions:
                break

            # collect run outputs for follow-up
            run_outputs = []

            for action in actions:
                action_type = action.get("type")

                if action_type == "build":
                    spec = action.get("spec", "")
                    name = action.get("name", re.sub(r'[^a-z0-9]+', '-', spec[:40].lower()).strip('-'))
                    if spec:
                        await message.channel.send(embed=make_embed(
                            f"Starting build: {name}",
                            f"```\n{spec[:300]}\n```",
                            color=0x2ea043,
                        ))
                        await builds.start_build(name, spec, message.channel, loop, engine=bot_state["engine"])

                elif action_type == "autoship":
                    spec = action.get("spec", "")
                    slug = action.get("slug", "")
                    eng = action.get("engine", bot_state["engine"])
                    if spec:
                        if not slug:
                            slug = re.sub(r'[^a-z0-9]+', '-', spec[:40].lower()).strip('-')
                        spec_path = SPECS_DIR / f"{slug}.md"
                        spec_path.write_text(spec)
                        await message.channel.send(embed=make_embed(
                            f"AutoShip: {slug}",
                            f"Building and deploying to `{slug}.autoship.fun`\n```\n{spec[:400]}\n```",
                            color=0x2ea043,
                        ))
                        cmd = [sys.executable, str(AUTOSHIP), str(spec_path),
                               "-e", eng, "--deploy", "autoship", "--slug", slug]

                        def run_autoship(cmd=cmd, slug=slug):
                            env = os.environ.copy()
                            env.pop("CLAUDECODE", None)
                            proc = subprocess.Popen(
                                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, env=env,
                            )
                            output_lines = []
                            for line in proc.stdout:
                                line = line.rstrip()
                                if line.strip():
                                    output_lines.append(line)
                            proc.wait()
                            return proc.returncode, output_lines

                        rc, output = await loop.run_in_executor(None, run_autoship)
                        url = f"https://{slug}.autoship.fun"
                        for line in output:
                            if "LIVE URL:" in line:
                                url = line.split("LIVE URL:")[-1].strip()
                        if rc == 0:
                            await message.channel.send(embed=make_embed(
                                f"Shipped: {slug}", f"Live at {url}",
                                color=0x2ea043, fields=[("URL", url, False)],
                            ))
                        else:
                            last_lines = "\n".join(output[-15:])
                            await message.channel.send(embed=make_embed(
                                f"AutoShip Failed: {slug}",
                                f"```\n{last_lines[-1500:]}\n```", color=0xda3633,
                            ))

                elif action_type == "stop":
                    name = action.get("name", "")
                    if name and builds.stop_build(name):
                        await message.channel.send(embed=make_embed(
                            "Build Stopped", f"Killed: **{name}**", color=0xda3633,
                        ))

                elif action_type == "stop_all":
                    stopped = builds.stop_all()
                    if stopped:
                        await message.channel.send(embed=make_embed(
                            "All Builds Stopped",
                            "Killed: " + ", ".join(stopped), color=0xda3633,
                        ))

                elif action_type == "tweet":
                    text = action.get("text", "")
                    if text:
                        ok, result = await loop.run_in_executor(None, do_tweet, text)
                        color = 0x2ea043 if ok else 0xda3633
                        await message.channel.send(embed=make_embed(
                            "Tweeted" if ok else "Tweet Failed",
                            f"{text}\n\n{result}", color=color,
                        ))

                elif action_type == "thread":
                    tweets = action.get("tweets", [])
                    if tweets:
                        preview = "\n\n".join(f"**{i+1}.** {t}" for i, t in enumerate(tweets))
                        await message.channel.send(embed=make_embed(
                            f"Posting thread ({len(tweets)} tweets)...",
                            preview[:4000], color=0x1da1f2,
                        ))
                        ok, result = await loop.run_in_executor(None, do_thread, tweets)
                        color = 0x2ea043 if ok else 0xda3633
                        await message.channel.send(embed=make_embed(
                            "Thread Posted" if ok else "Thread Failed",
                            result[:2000], color=color,
                        ))

                elif action_type == "run":
                    cmd = action.get("command", "")
                    label = action.get("label", "Running command")
                    if cmd:
                        await message.channel.send(embed=make_embed(
                            label, f"```\n{cmd}\n```", color=0xa78bfa,
                        ))
                        try:
                            result = await loop.run_in_executor(
                                None, lambda c=cmd: subprocess.run(
                                    c, shell=True, capture_output=True,
                                    text=True, timeout=180,
                                )
                            )
                            output = (result.stdout + result.stderr).strip()
                            rc = result.returncode
                            status = "OK" if rc == 0 else f"FAILED (exit {rc})"
                            if output:
                                if len(output) > 1800:
                                    output = output[:1800] + "\n... (truncated)"
                                await message.channel.send(f"```\n{output}\n```")
                                run_outputs.append(f"Command: {cmd}\nStatus: {status}\nOutput:\n{output}")
                            else:
                                await message.channel.send("`(no output)`")
                                run_outputs.append(f"Command: {cmd}\nStatus: {status}\nOutput: (no output)")
                        except Exception as e:
                            await message.channel.send(f"`Error: {e}`")
                            run_outputs.append(f"Command: {cmd}\nStatus: FAILED\nOutput: Error: {e}")

                elif action_type == "job_search":
                    keywords = action.get("keywords", "")
                    limit = action.get("limit", 10)
                    min_score = action.get("min_score", 10)
                    job_hunter = TOOLS_DIR / "job_hunter.py"
                    if job_hunter.exists():
                        await message.channel.send(embed=make_embed(
                            "Searching Jobs", f"Scanning job boards...", color=0x58a6ff))
                        search_cmd = f"python3 {job_hunter} --search --limit {limit} --min-score {min_score} --remote-only"
                        if keywords:
                            search_cmd += f' --keywords "{keywords}"'
                        try:
                            result = await loop.run_in_executor(
                                None, lambda c=search_cmd: subprocess.run(
                                    c, shell=True, capture_output=True,
                                    text=True, timeout=180,
                                )
                            )
                            output = (result.stdout or "No results.").strip()
                            if len(output) > 1800:
                                output = output[:1800] + "\n..."
                            await message.channel.send(f"```\n{output}\n```")
                            run_outputs.append(f"Job search results:\n{output}")
                        except Exception as e:
                            await message.channel.send(f"Job search error: {e}")
                            run_outputs.append(f"Job search failed: {e}")

                elif action_type == "job_apply":
                    url = action.get("url", "")
                    dry_run = action.get("dry_run", False)
                    if url:
                        job_applier = TOOLS_DIR / "job_applier.py"
                        if job_applier.exists():
                            mode = "DRY RUN" if dry_run else "LIVE"
                            await message.channel.send(embed=make_embed(
                                "Auto Applying",
                                f"URL: {url}\nMode: **{mode}**",
                                color=0x3fb950))
                            apply_cmd = f'python3 {job_applier} --url "{url}"'
                            if dry_run:
                                apply_cmd += " --dry-run"
                            try:
                                result = await loop.run_in_executor(
                                    None, lambda c=apply_cmd, dr=dry_run: subprocess.run(
                                        c, shell=True, capture_output=True,
                                        text=True, timeout=300,
                                        input="\n" if dr else None,
                                    )
                                )
                                output = (result.stdout or "No output.").strip()
                                if len(output) > 1800:
                                    output = output[:1800] + "\n..."
                                await message.channel.send(f"```\n{output}\n```")
                                run_outputs.append(f"Job application result:\n{output}")
                            except Exception as e:
                                await message.channel.send(f"Application error: {e}")
                                run_outputs.append(f"Job application failed: {e}")
                        else:
                            await message.channel.send("Job applier tool not found.")

                elif action_type == "reddit":
                    sub = action.get("subreddit", "")
                    title = action.get("title", "")
                    body = action.get("body", "")
                    await message.channel.send(embed=make_embed(
                        f"Reddit Post Draft ({sub})",
                        f"**{title}**\n\n{body[:1500]}",
                        color=0xff4500,
                        fields=[("Note", "Reddit posting requires browser auth. "
                                 "Copy this and post manually.", False)],
                    ))

                elif action_type == "create_tool":
                    name = action.get("name", "")
                    code = action.get("code", "")
                    if name and code:
                        tool_path = TOOLS_DIR / name
                        # validate Python syntax before saving
                        if name.endswith(".py"):
                            try:
                                ast.parse(code)
                            except SyntaxError as e:
                                await message.channel.send(embed=make_embed(
                                    f"Tool Syntax Error: {name}",
                                    f"```\n{e}\n```\nFix the code and try again.",
                                    color=0xda3633,
                                ))
                                if step < 1:  # only retry once, don't burn 4 LLM calls
                                    run_outputs.append(
                                        f"create_tool {name}: FAILED - SyntaxError: {e}\n"
                                        f"Fix the syntax error and include a corrected create_tool action."
                                    )
                                continue
                        tool_path.write_text(code)
                        tool_path.chmod(0o755)
                        await message.channel.send(embed=make_embed(
                            f"Tool Created: {name}",
                            f"```\n{code[:1500]}\n```",
                            color=0x2ea043,
                        ))

                elif action_type == "create_skill":
                    name = action.get("name", "")
                    skill_content = action.get("content", "")
                    if name and skill_content:
                        skill_path = SKILLS_DIR / name
                        skill_path.write_text(skill_content)
                        await message.channel.send(embed=make_embed(
                            f"Skill Learned: {name}",
                            f"New skill loaded. I'll remember this for next time.",
                            color=0x2ea043,
                        ))

                elif action_type == "read_file":
                    rel_path = action.get("path", "")
                    start_line = action.get("start_line")
                    end_line = action.get("end_line")
                    if rel_path:
                        base = Path(__file__).resolve().parent
                        full_path = (base / rel_path).resolve()
                        # security: only allow reading within the redbee tree
                        redbee_root = base.parent.parent
                        if not str(full_path).startswith(str(redbee_root)):
                            await message.channel.send("`Access denied: path outside codebase`")
                        elif not full_path.exists():
                            run_outputs.append(f"read_file {rel_path}: FILE NOT FOUND")
                        elif full_path.is_dir():
                            run_outputs.append(f"read_file {rel_path}: is a directory, use list_files instead")
                        else:
                            try:
                                lines = full_path.read_text(errors="replace").splitlines()
                                if start_line and end_line:
                                    lines = lines[max(0, start_line-1):end_line]
                                    label = f"{rel_path} (lines {start_line}-{end_line})"
                                else:
                                    label = f"{rel_path} ({len(lines)} lines)"
                                content_str = "\n".join(f"{i+1:4d}  {l}" for i, l in enumerate(lines))
                                if len(content_str) > 3000:
                                    content_str = content_str[:3000] + "\n... (truncated, use start_line/end_line to read specific sections)"
                                await message.channel.send(f"```py\n# {label}\n{content_str}\n```")
                                run_outputs.append(f"read_file {label}:\n{content_str}")
                            except Exception as e:
                                run_outputs.append(f"read_file {rel_path}: ERROR: {e}")

                elif action_type == "list_files":
                    rel_path = action.get("path", ".")
                    base = Path(__file__).resolve().parent
                    full_path = (base / rel_path).resolve()
                    redbee_root = base.parent.parent
                    if not str(full_path).startswith(str(redbee_root)):
                        await message.channel.send("`Access denied: path outside codebase`")
                    elif full_path.is_dir():
                        entries = []
                        for item in sorted(full_path.iterdir()):
                            if item.name.startswith(".") or item.name == "__pycache__":
                                continue
                            prefix = "d " if item.is_dir() else "f "
                            size = f" ({item.stat().st_size:,}b)" if item.is_file() else ""
                            entries.append(f"{prefix}{item.name}{size}")
                        listing = "\n".join(entries) or "(empty)"
                        await message.channel.send(f"```\n{rel_path}/\n{listing}\n```")
                        run_outputs.append(f"list_files {rel_path}:\n{listing}")
                    else:
                        run_outputs.append(f"list_files {rel_path}: not a directory")

                elif action_type == "edit_code":
                    instruction = action.get("instruction", "")
                    if instruction:
                        redbee_root = Path(__file__).resolve().parent.parent.parent
                        await message.channel.send(embed=make_embed(
                            "Editing codebase...",
                            f"```\n{instruction[:500]}\n```\nSpawning Claude Code agent...",
                            color=0xa78bfa,
                        ))
                        env = os.environ.copy()
                        env.pop("CLAUDECODE", None)
                        edit_prompt = (
                            f"You are editing the RedBee codebase. Working directory: {redbee_root}\n\n"
                            f"INSTRUCTION: {instruction}\n\n"
                            f"Rules:\n"
                            f"- Read the relevant files first to understand the existing code\n"
                            f"- Make minimal, focused changes — do not refactor unrelated code\n"
                            f"- Test that Python files parse correctly (python3 -c 'import ast; ast.parse(open(f).read())')\n"
                            f"- Do NOT modify config.json (it contains personal data)\n"
                            f"- When done, print a short summary of what you changed\n"
                        )
                        try:
                            result = await loop.run_in_executor(
                                None, lambda: subprocess.run(
                                    ["claude", "-p", edit_prompt,
                                     "--allowedTools", "Bash,Write,Read,Edit,Glob,Grep",
                                     "--dangerously-skip-permissions",
                                     "--no-session-persistence"],
                                    cwd=str(redbee_root), env=env,
                                    capture_output=True, text=True, timeout=300,
                                )
                            )
                            output = result.stdout.strip()
                            if len(output) > 1800:
                                output = output[:1800] + "\n... (truncated)"
                            status = "Done" if result.returncode == 0 else f"Failed (exit {result.returncode})"
                            await message.channel.send(embed=make_embed(
                                f"Code Edit: {status}",
                                f"```\n{output[-1500:]}\n```",
                                color=0x2ea043 if result.returncode == 0 else 0xda3633,
                            ))
                            run_outputs.append(f"edit_code: {status}\n{output}")
                            if result.returncode == 0:
                                run_outputs.append(
                                    "Code was modified. If you want changes to take effect, "
                                    "use the restart action. Tell the user what was changed first."
                                )
                        except subprocess.TimeoutExpired:
                            await message.channel.send(embed=make_embed(
                                "Code Edit: Timed Out",
                                "Claude Code agent took too long (5 min limit). Try a simpler instruction.",
                                color=0xda3633,
                            ))
                            run_outputs.append("edit_code: TIMED OUT after 5 minutes")
                        except Exception as e:
                            await message.channel.send(f"`edit_code error: {e}`")
                            run_outputs.append(f"edit_code: ERROR: {e}")

                elif action_type == "cron_create":
                    plan = action.get("plan", {})
                    if plan and plan.get("name") and plan.get("steps"):
                        cron_scheduler.save_plan(plan)
                        await message.channel.send(embed=make_embed(
                            f"Cron Created: {plan['name']}",
                            f"Schedule: `{plan.get('schedule', 'every 1h')}`\n"
                            f"Steps: {len(plan['steps'])}",
                            color=0x2ea043,
                        ))

                elif action_type == "cron_list":
                    plans = cron_scheduler.list_plans()
                    if plans:
                        lines = [f"**{p['name']}** — `{p['schedule']}` ({p['steps']} steps, last: {_fmt_last_run(p['last_run'])})"
                                 for p in plans]
                        await message.channel.send(embed=make_embed(
                            f"Active Crons ({len(plans)})",
                            "\n".join(lines), color=0x58a6ff,
                        ))
                    else:
                        await message.channel.send("No crons set up yet.")

                elif action_type == "cron_delete":
                    cron_name = action.get("name", "")
                    if cron_name:
                        cron_scheduler.delete_plan(cron_name)
                        await message.channel.send(embed=make_embed(
                            "Cron Deleted", f"Removed: **{cron_name}**", color=0xda3633,
                        ))

                elif action_type == "memory_write":
                    cat = action.get("category", "general")
                    key = action.get("key", "")
                    val = action.get("value", "")
                    ttl = action.get("ttl_days", 0)
                    if key:
                        memory_store.write(cat, key, val, ttl)
                        await message.channel.send(embed=make_embed(
                            "Remembered",
                            f"`{cat}/{key}`: {str(val)[:200]}",
                            color=0x2ea043,
                        ))

                elif action_type == "memory_read":
                    cat = action.get("category")
                    key = action.get("key")
                    since = action.get("since")
                    rows = memory_store.read(category=cat, key=key, since=since)
                    if rows:
                        text = json.dumps(rows, indent=2)
                        if len(text) > 1800:
                            text = text[:1800] + "\n..."
                        run_outputs.append(f"Memory read ({cat or 'all'}):\n{text}")
                    else:
                        run_outputs.append(f"Memory read ({cat or 'all'}): no results")

                elif action_type == "memory_search":
                    query = action.get("query", "")
                    if query:
                        rows = memory_store.search(query)
                        if rows:
                            text = json.dumps(rows, indent=2)
                            if len(text) > 1800:
                                text = text[:1800] + "\n..."
                            run_outputs.append(f"Memory search '{query}':\n{text}")
                        else:
                            run_outputs.append(f"Memory search '{query}': no results")

                elif action_type == "load_skill":
                    skill_name = action.get("name", "")
                    if skill_name:
                        skill_content = load_skill(skill_name)
                        run_outputs.append(f"Skill '{skill_name}':\n{skill_content}")

                elif action_type == "engine":
                    new_engine = action.get("engine", "").lower()
                    new_model = action.get("model", "")
                    new_reasoning = action.get("reasoning", "").lower()
                    changes = []
                    if new_engine in ("claude", "codex"):
                        bot_state["engine"] = new_engine
                        changes.append(f"engine={new_engine}")
                    if new_model:
                        bot_state["model"] = new_model
                        changes.append(f"model={new_model}")
                    if new_reasoning in ("low", "medium", "high"):
                        bot_state["reasoning"] = new_reasoning
                        changes.append(f"reasoning={new_reasoning}")
                    if changes:
                        await message.channel.send(embed=make_embed(
                            "Config Updated",
                            f"Set: {', '.join(changes)}",
                            color=0x2ea043,
                        ))

                elif action_type == "restart":
                    await message.channel.send(embed=make_embed(
                        "Restarting...",
                        "Picking up new skills and code. Back in a sec.",
                        color=0xff9f1c,
                    ))
                    await client.close()
                    os.execv(sys.executable, [sys.executable] + sys.argv)

            # if run commands produced output, feed results back to LLM for follow-up
            if run_outputs:
                results_text = "\n\n".join(run_outputs)
                has_failures = any("FAILED" in o for o in run_outputs)
                if has_failures:
                    followup = (
                        f"PREVIOUS REQUEST: {content}\n\n"
                        f"COMMAND RESULTS:\n{results_text}\n\n"
                        f"Some commands FAILED. Fix the errors and retry with corrected commands. "
                        f"Common fixes: wrong file path (check with ls/find first), wrong repo name, "
                        f"missing tool. Do NOT repeat the same broken command. Diagnose and fix."
                    )
                else:
                    followup = (
                        f"PREVIOUS REQUEST: {content}\n\n"
                        f"COMMAND RESULTS:\n{results_text}\n\n"
                        f"Based on these results, complete the user's original request. "
                        f"If you need to take further action, include the appropriate ACTION. "
                        f"If the task is done, just summarize what happened.\n"
                        f"IMPORTANT: Do NOT repeat actions you already ran. Do NOT search again if you already have results. "
                        f"Move to the NEXT step. If the user asked to apply, use job_apply with URLs from the results above."
                    )
                user_message = followup
                continue  # go back to LLM with results
            else:
                break  # no run outputs, we're done

    client.run(token)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="ground control — talk to autopilot from discord",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
setup:
  1. Go to https://discord.com/developers/applications
  2. Create a new application > Bot > copy token
  3. OAuth2 > URL Generator > select 'bot' scope
     > select 'Send Messages', 'Read Message History' permissions
  4. Open the generated URL to invite bot to your server
  5. Right-click your channel > Copy Channel ID (enable Developer Mode in settings)

run:
  # DM mode (just talk to the bot directly)
  python3 ground_control.py --token YOUR_TOKEN --owner YOUR_DISCORD_USER_ID

  # channel mode (bot listens in a specific channel)
  python3 ground_control.py --token YOUR_TOKEN --channel CHANNEL_ID

  # both (DMs + channel)
  python3 ground_control.py --token YOUR_TOKEN --owner USER_ID --channel CHANNEL_ID

  # env vars work too
  AUTOPILOT_DISCORD_TOKEN=xxx AUTOPILOT_DISCORD_OWNER=123 python3 ground_control.py

how to get your user ID:
  Discord Settings > Advanced > enable Developer Mode
  Click your own profile > Copy User ID
"""
    )
    p.add_argument("--token", type=str,
                   default=os.environ.get("AUTOPILOT_DISCORD_TOKEN"),
                   help="Discord bot token (or set AUTOPILOT_DISCORD_TOKEN)")
    p.add_argument("--channel", type=int,
                   default=int(os.environ.get("AUTOPILOT_DISCORD_CHANNEL", "0")) or None,
                   help="Discord channel ID — optional (or set AUTOPILOT_DISCORD_CHANNEL)")
    p.add_argument("--owner", type=int,
                   default=int(os.environ.get("AUTOPILOT_DISCORD_OWNER", "0")) or None,
                   help="Your Discord user ID — locks DMs to only you (or set AUTOPILOT_DISCORD_OWNER)")
    p.add_argument("--engine", type=str, default="codex", choices=["claude", "codex"],
                   help="LLM engine for conversational mode (default: codex)")
    p.add_argument("--model", type=str, default="gpt-5.4",
                   help="Model for codex engine (default: gpt-5.4)")
    p.add_argument("--reasoning", type=str, default="medium",
                   choices=["low", "medium", "high"],
                   help="Reasoning effort for codex engine (default: medium)")

    args = p.parse_args()

    if not args.token:
        print("Error: provide --token or set AUTOPILOT_DISCORD_TOKEN")
        sys.exit(1)
    if not args.channel and not args.owner:
        print("Error: provide --channel and/or --owner")
        print("  --owner YOUR_ID   → bot responds to your DMs")
        print("  --channel CHAN_ID  → bot responds in a channel")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"  ground control")
    print(f"{'='*50}")
    if args.owner:
        print(f"  owner:    {args.owner} (DMs enabled)")
    if args.channel:
        print(f"  channel:  {args.channel}")
    print(f"  engine:   {args.engine}")
    print(f"  model:    {args.model}")
    print(f"  reasoning: {args.reasoning}")
    print(f"  autopilot: {AUTOPILOT}")
    print(f"{'='*50}\n")

    create_bot(args.token, args.channel, args.owner, args.engine,
               args.model, args.reasoning)
