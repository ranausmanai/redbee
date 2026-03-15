# Cron — scheduled automation

Create cron plans that run on a schedule. The LLM generates the plan once, then deterministic code executes it forever with zero LLM calls (unless a step explicitly needs creative output).

## Plan format

A cron plan is a JSON object with steps that execute sequentially:

```json
{
  "name": "unique-plan-name",
  "schedule": "every 6h",
  "steps": [
    {"type": "run", "command": "some shell command", "save_as": "var_name"},
    {"type": "condition", "expr": "{var_name} > 100", "on_false": "done"},
    {"type": "memory_write", "category": "cat", "key": "k", "value": "{var_name}"},
    {"type": "llm", "prompt": "Write a tweet about {var_name}", "save_as": "tweet"},
    {"type": "run", "command": "twitter post \"{tweet}\""},
    {"type": "notify", "text": "Done: {var_name}"},
    {"type": "done"}
  ],
  "state": {}
}
```

## Schedule formats

- `every 5m` — every 5 minutes
- `every 6h` — every 6 hours
- `every 1d` — daily
- `hourly` — every hour
- `daily` — once per day

## Step types

| Type | Description | Fields |
|---|---|---|
| `run` | Execute a shell command | `command`, `save_as` (optional) |
| `condition` | Check an expression, stop if false | `expr`, `on_false` ("done"), `track` (for "changed") |
| `llm` | Call LLM for creative output (costs tokens!) | `prompt`, `save_as` |
| `memory_write` | Store data to memory | `category`, `key`, `value` |
| `memory_read` | Read from memory | `category`, `key`, `since`, `save_as` |
| `notify` | Send a Discord notification | `text` |
| `done` | Stop execution | — |

## Variable interpolation

Use `{var_name}` in any string field. Variables come from:
- `save_as` on previous steps (command stdout or LLM output)
- Plan `state` (persisted between runs)

## Condition expressions

- `{stars} > 100` — numeric comparison
- `{status} == "up"` — string equality
- `{output} contains "error"` — substring check
- `{value} in [a, b, c]` — membership
- `changed` — value differs from last run (use with `track` field)

## Examples

### Uptime monitor (zero LLM calls)
```
ACTION: {"type": "cron_create", "plan": {"name": "uptime-check", "schedule": "every 5m", "steps": [{"type": "run", "command": "curl -s -o /dev/null -w '%{http_code}' https://mysite.com", "save_as": "status"}, {"type": "condition", "expr": "{status} != 200", "on_false": "done"}, {"type": "notify", "text": "Site down! Status: {status}"}, {"type": "done"}], "state": {}}}
```

### GitHub star milestones (LLM only on milestone)
```
ACTION: {"type": "cron_create", "plan": {"name": "star-milestones", "schedule": "every 6h", "steps": [{"type": "run", "command": "gh api repos/user/repo --jq '.stargazers_count'", "save_as": "stars"}, {"type": "condition", "expr": "{stars} > {last_stars}", "on_false": "done"}, {"type": "memory_write", "category": "github", "key": "stars", "value": "{stars}"}, {"type": "llm", "prompt": "Write a short celebratory tweet about hitting {stars} GitHub stars for my project. Keep it authentic, not cringe.", "save_as": "tweet"}, {"type": "run", "command": "twitter post \"{tweet}\""}, {"type": "done"}], "state": {"last_stars": "0"}}}
```

### Daily analytics collection (zero LLM calls)
```
ACTION: {"type": "cron_create", "plan": {"name": "daily-github-stats", "schedule": "daily", "steps": [{"type": "run", "command": "gh api repos/user/repo --jq '{stars: .stargazers_count, forks: .forks_count, issues: .open_issues_count}'", "save_as": "stats"}, {"type": "memory_write", "category": "github", "key": "daily-stats", "value": "{stats}"}, {"type": "done"}], "state": {}}}
```

## Managing crons

- `ACTION: {"type": "cron_list"}` — see all active crons
- `ACTION: {"type": "cron_delete", "name": "plan-name"}` — remove a cron
- `!crons` — quick list command

## Memory integration

Crons write data to memory. The user can then ask "how's my project doing?" and you read from memory:
```
ACTION: {"type": "memory_read", "category": "github", "since": "-7 days"}
```

This gives you a week of collected data to summarize in one LLM call.
