# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repo contains three independent AI-powered CLI tools, all following a "Karpathy-minimal" philosophy: short code, markdown config, autonomous operation.

### autoevolve
Evolutionary code optimization using LLM-powered mutation and selection. Iteratively improves a seed file against fitness criteria with optional benchmarks.
- **Engine**: `autoevolve.py` (~300 lines) — main loop
- **Config**: `evolve.md` — fitness criteria written by the user
- **Seed**: any code file to evolve (e.g., `seed.py`)
- **Benchmark**: `bench.py` — optional deterministic scorer

### autoship
Spec-to-running-app builder. Reads a product spec and autonomously builds the complete application.
- **Engine**: `autoship/autoship.py` (~60 lines) — orchestrator
- **Config**: `autoship/program.md` — agent instructions (the real magic)
- **Input**: any spec markdown file (e.g., `autoship/example.md`)

### spawn
Multi-agent parallel builder. Breaks a task into roles, spawns multiple AI agents working simultaneously.
- **Engine**: `spawn/spawn.py` (~150 lines) — orchestrator with tmux and parallel modes
- **Input**: a task string or spec markdown file (e.g., `spawn/example.md`)

## Running

All tools use `claude` or `codex` CLI as the LLM backend (no API keys needed):

```bash
# autoevolve
python3 autoevolve.py seed.py evolve.md -g 5 -n 3 -b "python3 bench.py {file}" -e claude

# autoship
cd autoship && python3 autoship.py example.md -e codex

# spawn (parallel mode)
cd spawn && python3 spawn.py example.md -e claude

# spawn (tmux live view)
cd spawn && python3 spawn.py example.md --tmux
```

## Architecture Notes

- All three tools call LLMs via subprocess (`claude -p` or `codex exec --full-auto`), not via API SDK
- The `CLAUDECODE` env var must be unset to allow nesting claude calls (`env.pop("CLAUDECODE", None)`)
- Codex requires a git repo in the working directory; scripts auto-run `git init` when needed
- `--dangerously-skip-permissions` (claude) and `--full-auto` (codex) enable autonomous operation
- Python 3.10+ required; only stdlib dependencies (no pip install needed)
