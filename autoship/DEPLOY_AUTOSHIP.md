# autoship deploy

## DNS

Point these records to the VPS:

- `A @ -> 187.77.31.25`
- `A * -> 187.77.31.25`

Optional:

- `A www -> 187.77.31.25`

`autoship.fun` already resolves to this VPS. The wildcard `*` record is still required for app subdomains like `my-app.autoship.fun`.

## Hosted mode

```bash
export AUTOSHIP_API_TOKEN=replace-with-private-token
python3 autoship.py example.md -e codex --deploy autoship --slug my-app
```

By default, the CLI will use `AUTOSHIP_API_URL` if set, otherwise:

- `https://api.autoship.fun/deploy`

The hosted API is private. Infra credentials stay on the VPS, not in the OSS repo.

## Operator fallback

For direct SSH deploys from the maintainer machine:

```bash
python3 autoship.py example.md \
  -e codex \
  --deploy autoship \
  --server root@187.77.31.25 \
  --domain autoship.fun \
  --ssh-key /Users/usman/.ssh/autoship_vps \
  --slug my-app
```

Deploy key:

- `/Users/usman/.ssh/autoship_vps`

## Deploy contract

When `--deploy autoship` is used, autoship now:

1. infers app capabilities from the spec
2. writes `autoship.plan.json`
3. asks the model to generate:
   - `Dockerfile`
   - `autoship.json`
   - an app that listens on `0.0.0.0:$PORT`
   - persistence only under `/data`
4. injects standard runtime env vars on deploy:
   - `SECRET_KEY`
   - `SESSION_SECRET`
   - `DATABASE_PATH`
   - `SQLITE_PATH`
   - `UPLOADS_DIR`
   - `AUTOSHIP_ADMIN_EMAIL`
   - `AUTOSHIP_ADMIN_PASSWORD`
   - and app URL/domain metadata

## Result

Deploys land on:

- `https://<slug>.autoship.fun`

The deploy service:

- uploads the generated app bundle to the host
- unpacks it under `/opt/autoship/apps/<slug>/releases/...`
- builds and runs it in Docker
- proxies it through Nginx
- requests a Let's Encrypt cert when DNS is ready
