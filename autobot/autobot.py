#!/usr/bin/env python3
"""autobot — give it a personality + docs, get a working chatbot.

Usage:
  python3 autobot.py bot.md --docs ./knowledge/
  python3 autobot.py bot.md --docs ./knowledge/ -e codex --reasoning low
  python3 autobot.py bot.md --docs ./knowledge/ -o my_chatbot
"""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

PROGRAM = Path(__file__).with_name("program.md")
DOC_EXTENSIONS = {".md", ".txt", ".html", ".csv", ".json", ".yml", ".yaml", ".rst", ".py", ".js"}
MAX_DOC_CHARS = 50000  # total doc content sent to the builder


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
    return (slug or f"bot-{int(time.time())}")[:48]


def run(cmd, *, cwd=None, env=None, capture=False, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=capture, check=check)


def init_git(outdir):
    if not (outdir / ".git").exists():
        run(["git", "init"], cwd=str(outdir.resolve()), capture=True)


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


def load_docs(docs_path):
    """Load all documents from a directory. Returns list of {name, content}."""
    docs_path = Path(docs_path)
    if not docs_path.exists():
        die(f"Docs path does not exist: {docs_path}")

    docs = []
    if docs_path.is_file():
        docs.append({
            "name": docs_path.name,
            "content": docs_path.read_text(errors="replace"),
        })
        return docs

    for f in sorted(docs_path.rglob("*")):
        if f.is_dir():
            continue
        if f.suffix.lower() not in DOC_EXTENSIONS:
            continue
        if any(part.startswith(".") for part in f.relative_to(docs_path).parts):
            continue
        try:
            content = f.read_text(errors="replace").strip()
            if content:
                docs.append({
                    "name": str(f.relative_to(docs_path)),
                    "content": content,
                })
        except Exception:
            continue

    return docs


def format_docs_for_prompt(docs):
    """Format docs into a single string, truncating if needed."""
    parts = []
    total = 0
    for doc in docs:
        header = f"### {doc['name']}\n"
        content = doc["content"]
        if total + len(content) + len(header) > MAX_DOC_CHARS:
            remaining = MAX_DOC_CHARS - total - len(header) - 20
            if remaining > 200:
                parts.append(header + content[:remaining] + "\n[TRUNCATED]")
            break
        parts.append(header + content)
        total += len(header) + len(content)
    return "\n\n---\n\n".join(parts)


def build_prompt(program, bot_spec, docs_text, doc_count):
    return f"""{program}

Build a complete chatbot application based on the spec and knowledge base below.

BOT SPEC:
{bot_spec}

KNOWLEDGE BASE ({doc_count} documents):
{docs_text}

REQUIREMENTS:
- Python backend with Flask (pip install flask)
- Clean web UI with a chat interface (HTML/CSS/JS served by Flask)
- The UI should have: chat bubbles, input field, send button, bot name/avatar area
- Dark or modern theme — must look professional
- The bot answers ONLY from the knowledge base content above
- Use simple text matching / keyword search for retrieval — no external APIs, no embeddings
- Store the knowledge base content in a JSON file that the server loads at startup
- Users should be able to add more docs by dropping files in a /docs folder and restarting
- Include requirements.txt
- Include README.md
- Server runs on 0.0.0.0:8000

Write all files. Install deps. Test the server starts. Fix any errors.
When done, print EXACTLY: "AUTOBOT COMPLETE"."""


def verify_build(outdir):
    missing = []
    if not list(outdir.glob("**/*.py")):
        missing.append("*.py")
    if not list(outdir.glob("**/requirements.txt")):
        missing.append("requirements.txt")
    # check for HTML template or inline HTML
    has_html = (list(outdir.glob("**/*.html")) or
                any("text/html" in f.read_text(errors="replace")
                    for f in outdir.glob("**/*.py") if f.stat().st_size < 100000))
    if not has_html:
        missing.append("HTML UI")
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
        fix_parts.append('When done, print EXACTLY: "AUTOBOT COMPLETE".')
        prompt = "\n".join(fix_parts)

    return False


def main():
    p = argparse.ArgumentParser(description="autobot — give it a personality + docs, get a chatbot")
    p.add_argument("spec", help="path to bot personality/spec file (.md)")
    p.add_argument("--docs", required=True, help="path to knowledge base directory or file")
    p.add_argument("-o", "--output", default=None, help="output directory")
    p.add_argument("-e", "--engine", default="claude", choices=["claude", "codex"])
    p.add_argument("--reasoning", default="medium", choices=["low", "medium", "high"],
                   help="codex reasoning effort (default: medium)")
    args = p.parse_args()

    spec_path = Path(args.spec)
    if not spec_path.exists():
        die(f"Spec file not found: {spec_path}")
    bot_spec = spec_path.read_text()

    slug = slugify(spec_path.stem)
    outdir = Path(args.output or f"bot_{slug}")
    outdir.mkdir(exist_ok=True)
    init_git(outdir)

    progress = Progress(3)

    print(f"""
============================================================
  autobot — give it a personality, get a chatbot
============================================================
  spec:    {args.spec}
  docs:    {args.docs}
  engine:  {args.engine}
  output:  {outdir}/
============================================================
""")

    # Stage 1: Load docs
    progress.stage("Loading knowledge base")
    docs = load_docs(args.docs)
    if not docs:
        die(f"No documents found in {args.docs}")
    total_chars = sum(len(d["content"]) for d in docs)
    progress.done(f"{len(docs)} docs, {total_chars:,} chars")

    # Stage 2: Format
    progress.stage("Planning chatbot")
    docs_text = format_docs_for_prompt(docs)
    doc_names = [d["name"] for d in docs]
    print(f"{len(doc_names)} docs indexed")
    for name in doc_names[:5]:
        print(f"           - {name}")
    if len(doc_names) > 5:
        print(f"           ... and {len(doc_names) - 5} more")
    print()

    # Stage 3: Build
    progress.stage("Building chatbot")
    program = PROGRAM.read_text() if PROGRAM.exists() else ""
    prompt = build_prompt(program, bot_spec, docs_text, len(docs))
    if not build_with_retries(args.engine, prompt, outdir, progress=progress, reasoning=args.reasoning):
        die("Build failed after retries.")
    progress.done()

    print(f"""
============================================================
  BOT READY -> {outdir}/
============================================================
  run:    cd {outdir} && pip install -r requirements.txt && python3 app.py
  then:   open http://localhost:8000
============================================================
""")


if __name__ == "__main__":
    main()
