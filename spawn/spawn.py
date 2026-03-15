#!/usr/bin/env python3
"""spawn — one prompt, a team of AI agents, building in parallel.

Usage:
  python3 spawn.py "Build a URL shortener with analytics"
  python3 spawn.py spec.md --tmux
  python3 spawn.py spec.md -e codex
"""

import subprocess, sys, os, json, time, argparse, tempfile
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

def llm(prompt, engine="claude", workdir=None):
    """Quick LLM call for planning (no tools)."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    if engine == "claude":
        r = subprocess.run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            capture_output=True, text=True, timeout=120, env=env, cwd=workdir
        )
        if r.returncode != 0:
            raise RuntimeError(f"claude planning failed: {(r.stderr or r.stdout).strip()[:400]}")
        return r.stdout.strip()
    else:
        r = subprocess.run(
            ["codex", "exec", "--full-auto", prompt],
            capture_output=True, text=True, timeout=120, env=env, cwd=workdir
        )
        if r.returncode != 0:
            raise RuntimeError(f"codex planning failed: {(r.stderr or r.stdout).strip()[:400]}")
        return r.stdout.strip()

def extract_json(text):
    try: return json.loads(text)
    except: pass
    s, e = text.find('['), text.rfind(']') + 1
    if s != -1 and e > s:
        try: return json.loads(text[s:e])
        except: pass
    raise ValueError(f"No JSON found in: {text[:200]}")

def agent_prompt(role, task, all_roles):
    team = "\n".join(f"  - {r['role']}: {r['task']} -> {', '.join(r['files'])}" for r in all_roles)
    return f"""You are the **{role['role']}** on a team building this project:

PROJECT: {task}

FULL TEAM:
{team}

YOUR JOB: {role['task']}
YOUR FILES: {', '.join(role['files'])}

RULES:
- ONLY create the files listed above
- Make your code compatible with the other agents' files
- Use standard interfaces (imports, REST endpoints, shared types)
- Write COMPLETE, working code — no placeholders
- When done, print EXACTLY: "AGENT DONE: {role['role']}"

Build your part now."""

def run_agent(role, task, all_roles, workdir, engine):
    prompt = agent_prompt(role, task, all_roles)
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    if engine == "claude":
        r = subprocess.run([
            "claude", "-p", prompt,
            "--allowedTools", "Bash,Write,Read,Edit",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
        ], capture_output=True, text=True, timeout=600, env=env, cwd=workdir)
    else:
        r = subprocess.run([
            "codex", "exec", "--full-auto", prompt,
        ], capture_output=True, text=True, timeout=600, env=env, cwd=workdir)

    return role["role"], r.returncode, r.stdout, r.stderr

def spawn_tmux(roles, task, workdir, engine):
    session = f"spawn{int(time.time()) % 10000}"
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    scripts = []
    for role in roles:
        prompt = agent_prompt(role, task, roles)
        sf = tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False, dir=workdir)
        if engine == "claude":
            sf.write(f'''#!/bin/bash
echo "=== {role['role'].upper()} ==="
claude -p {json.dumps(prompt)} \
  --allowedTools "Bash,Write,Read,Edit" \
  --dangerously-skip-permissions \
  --no-session-persistence
echo ""
echo "=== {role['role'].upper()} DONE ==="
''')
        else:
            sf.write(f'''#!/bin/bash
echo "=== {role['role'].upper()} ==="
codex exec --full-auto {json.dumps(prompt)}
echo ""
echo "=== {role['role'].upper()} DONE ==="
''')
        sf.close()
        os.chmod(sf.name, 0o755)
        scripts.append(sf.name)

    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", workdir], env=env)
    subprocess.run(["tmux", "send-keys", "-t", session, f"bash {scripts[0]}", "Enter"], env=env)

    for s in scripts[1:]:
        subprocess.run(["tmux", "split-window", "-t", session, "-c", workdir], env=env)
        subprocess.run(["tmux", "send-keys", "-t", session, f"bash {s}", "Enter"], env=env)
        subprocess.run(["tmux", "select-layout", "-t", session, "tiled"], env=env)

    print(f"  Agents spawned in tmux session: {session}")
    print(f"  Watch live:  tmux attach -t {session}")

def spawn_parallel(roles, task, workdir, engine):
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=len(roles)) as pool:
        futures = {
            pool.submit(run_agent, r, task, roles, workdir, engine): r
            for r in roles
        }
        for future in as_completed(futures):
            role_name, returncode, output, error = future.result()
            dt = time.time() - t0
            if returncode == 0:
                print(f"  ✓ {role_name} done [{dt:.0f}s]")
            else:
                detail = (error or output or "").strip().splitlines()
                msg = detail[-1] if detail else "agent exited without output"
                print(f"  ✗ {role_name} failed [{dt:.0f}s] :: {msg}")

def main():
    p = argparse.ArgumentParser(description="spawn — team of AI agents building in parallel")
    p.add_argument("task", help="what to build (quoted string or path to .md file)")
    p.add_argument("-o", "--output", default=None, help="output directory")
    p.add_argument("-e", "--engine", default="claude", choices=["claude", "codex"])
    p.add_argument("--tmux", action="store_true", help="show agents in live tmux panes")
    args = p.parse_args()

    task = Path(args.task).read_text() if Path(args.task).exists() else args.task
    workdir = str(Path(args.output or f"spawn_{int(time.time()) % 10000}").resolve())
    os.makedirs(workdir, exist_ok=True)

    # init git (codex requires it)
    if not os.path.exists(os.path.join(workdir, ".git")):
        subprocess.run(["git", "init"], cwd=workdir, capture_output=True)

    print(f"""
============================================================
  spawn — one prompt, a team of agents
============================================================
  task:    {args.task}
  engine:  {args.engine}
  output:  {workdir}
============================================================

  Planning team...""")

    plan_prompt = f"""Break this project into 3-5 parallel roles for a team of AI agents.
Each role works independently on specific files that together form the complete project.

PROJECT: {task}

Return ONLY a JSON array:
[{{"role": "role name", "task": "what they build", "files": ["file1.py", "file2.html"]}}]

Rules:
- Roles must be independent (can work in parallel without blocking each other)
- Files must not overlap between roles
- Together, all files must form a complete working project
- Include one role that owns the main entry point

Return ONLY valid JSON, no markdown."""

    raw = llm(plan_prompt, args.engine, workdir)
    roles = extract_json(raw)

    print()
    for r in roles:
        print(f"  [{r['role']}] {r['task']}")
        print(f"    -> {', '.join(r['files'])}")
    print()

    if args.tmux:
        spawn_tmux(roles, task, workdir, args.engine)
    else:
        spawn_parallel(roles, task, workdir, args.engine)
        print(f"""
============================================================
  DONE -> {workdir}/
============================================================
""")

if __name__ == "__main__":
    main()
