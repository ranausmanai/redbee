# AutoShip

AutoShip turns a plain English spec into a deployed app with one command.

## Installation
```bash
git clone https://github.com/ranausmanai/autoship.git
cd autoship
```
No pip install needed. Just Python 3.10+.

## Prerequisites
You need either Claude Code CLI (`claude` command) or Codex CLI (`codex` command) installed.

## Usage
```bash
# Build from a spec
python3 autoship.py spec.md

# Build with custom output directory
python3 autoship.py spec.md -o myapp

# Build and deploy
python3 autoship.py spec.md --deploy autoship --slug my-app

# Update an existing app
python3 autoship.py changes.md -o myapp
```

## How it works
1. Planning — LLM reads the spec, picks the stack, infers capabilities
2. Building — AI agent writes all code, installs deps, tests locally
3. Verifying — checks all files exist, retries up to 3x if broken
4. Deploying — Dockerizes, configures Nginx + SSL, gives you a live URL

## Features
- Smart capability inference (auth, database, storage, payments)
- Self-healing builds with auto-retry
- Iterative updates to the same URL
- Zero dependencies (stdlib only)
