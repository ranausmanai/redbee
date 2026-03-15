#!/usr/bin/env bash
set -euo pipefail

slug="$1"
domain="$2"
email="$3"
archive_path="$4"
deploy_root="${5:-/opt/autoship}"

if [ "$email" = "__none__" ]; then
  email=""
fi

app_root="$deploy_root/apps/$slug"
release_root="$app_root/releases/$(date +%Y%m%d%H%M%S)"
data_root="$app_root/data"
current_link="$app_root/current"
subdomain="${slug}.${domain}"
nginx_site="/etc/nginx/sites-available/${subdomain}"
nginx_link="/etc/nginx/sites-enabled/${subdomain}"
env_file="$app_root/runtime.env"
generated_secrets_path="$app_root/.deploy_secrets.json"

mkdir -p "$deploy_root/apps" "$release_root" "$data_root" "$app_root"
tar -xzf "$archive_path" -C "$release_root"

manifest_path="$release_root/autoship.json"
plan_path="$release_root/autoship.plan.json"
dockerfile_path="$release_root/Dockerfile"
if [ ! -f "$dockerfile_path" ]; then
  echo "Missing Dockerfile in release" >&2
  exit 1
fi
if [ ! -f "$manifest_path" ]; then
  cat > "$manifest_path" <<'JSON'
{"container_port":8000,"healthcheck_path":"/","data_dir":"/data"}
JSON
fi

container_port="$(python3 - "$manifest_path" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    data = json.load(fh)
print(int(data.get("container_port", 8000)))
PY
)"

health_path="$(python3 - "$manifest_path" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    data = json.load(fh)
print(data.get("healthcheck_path", "/"))
PY
)"

if [ -z "$health_path" ]; then
  health_path="/"
fi
case "$health_path" in
  /*) ;;
  *) health_path="/$health_path" ;;
esac

host_port="$(python3 - <<'PY'
import socket
for port in range(12000, 20000):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        continue
    else:
        print(port)
        sock.close()
        break
else:
    raise SystemExit("No free host ports available in 12000-20000")
PY
)"

turnkey="$(python3 - "$plan_path" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.exists():
    print("true")
    raise SystemExit(0)
try:
    data = json.loads(path.read_text())
except Exception:
    print("false")
    raise SystemExit(0)
print("true" if data.get("turnkey", True) else "false")
PY
)"

python3 - "$plan_path" "$env_file" "$generated_secrets_path" "$subdomain" "$slug" "$domain" <<'PY'
import json
import pathlib
import secrets
import sys

plan_path = pathlib.Path(sys.argv[1])
env_path = pathlib.Path(sys.argv[2])
secrets_path = pathlib.Path(sys.argv[3])
subdomain = sys.argv[4]
slug = sys.argv[5]
domain = sys.argv[6]

caps = {}
if plan_path.exists():
    try:
        caps = json.loads(plan_path.read_text()).get("capabilities") or {}
    except Exception:
        caps = {}

existing = {}
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        existing[key] = value

def keep(name, factory):
    value = existing.get(name)
    if value:
        return value
    return factory()

values = {
    "AUTOSHIP_APP_URL": f"https://{subdomain}",
    "AUTOSHIP_APP_SLUG": slug,
    "AUTOSHIP_BASE_DOMAIN": domain,
    "AUTOSHIP_DATA_DIR": "/data",
    "DATA_DIR": "/data",
    "SECRET_KEY": keep("SECRET_KEY", lambda: secrets.token_urlsafe(32)),
    "SESSION_SECRET": keep("SESSION_SECRET", lambda: secrets.token_urlsafe(32)),
    "JWT_SECRET": keep("JWT_SECRET", lambda: secrets.token_urlsafe(32)),
}

generated = {"admin_email": None, "admin_password": None}

if caps.get("database") == "sqlite":
    values["DATABASE_PATH"] = existing.get("DATABASE_PATH") or "/data/app.db"
    values["SQLITE_PATH"] = existing.get("SQLITE_PATH") or "/data/app.db"
    values["DATABASE_URL"] = existing.get("DATABASE_URL") or "sqlite:////data/app.db"

if caps.get("storage") == "local":
    values["UPLOADS_DIR"] = existing.get("UPLOADS_DIR") or "/data/uploads"

if caps.get("auth") == "local" or caps.get("admin"):
    admin_email = existing.get("AUTOSHIP_ADMIN_EMAIL") or f"admin@{subdomain}"
    admin_password = existing.get("AUTOSHIP_ADMIN_PASSWORD") or secrets.token_urlsafe(16)
    values["AUTOSHIP_ADMIN_EMAIL"] = admin_email
    values["AUTOSHIP_ADMIN_PASSWORD"] = admin_password
    generated["admin_email"] = admin_email
    generated["admin_password"] = admin_password

env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
secrets_path.write_text(json.dumps(generated))
PY

mkdir -p "$data_root/uploads"

image_tag="autoship/${slug}:$(basename "$release_root")"
container_name="autoship-${slug}"

docker build -t "$image_tag" "$release_root" >/dev/null
docker rm -f "$container_name" >/dev/null 2>&1 || true
docker run -d \
  --name "$container_name" \
  --restart unless-stopped \
  --env-file "$env_file" \
  -e PORT="$container_port" \
  -v "$data_root:/data" \
  -p "127.0.0.1:${host_port}:${container_port}" \
  "$image_tag" >/dev/null

echo "$host_port" > "$app_root/.host_port"
ln -sfn "$release_root" "$current_link"

cat > "$nginx_site" <<NGINX
server {
    listen 80;
    server_name ${subdomain};

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:${host_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
NGINX
ln -sfn "$nginx_site" "$nginx_link"

nginx -t >/dev/null
systemctl reload nginx

python3 - "$host_port" "$health_path" <<'PY'
import sys, time, urllib.request
port = sys.argv[1]
health_path = sys.argv[2]
targets = [health_path]
if health_path != "/":
    targets.append("/")
last_error = None
for _ in range(40):
    for target in targets:
        url = f"http://127.0.0.1:{port}{target}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    raise SystemExit(0)
        except Exception as exc:
            last_error = exc
    time.sleep(1)
raise SystemExit(f"Container did not become healthy: {last_error}")
PY

public_ip="$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
root_resolves="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u | grep -Fx "$public_ip" || true)"
sub_resolves="$(getent ahostsv4 "$subdomain" 2>/dev/null | awk '{print $1}' | sort -u | grep -Fx "$public_ip" || true)"

root_cert="pending-dns"
sub_cert="pending-dns"

if [ -n "$root_resolves" ]; then
  if [ -n "$email" ]; then
    certbot --nginx -d "$domain" --non-interactive --agree-tos -m "$email" --redirect >/dev/null || true
  else
    certbot --nginx -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email --redirect >/dev/null || true
  fi
  root_cert="requested"
fi

if [ -n "$sub_resolves" ]; then
  if [ -n "$email" ]; then
    certbot --nginx -d "$subdomain" --non-interactive --agree-tos -m "$email" --redirect >/dev/null || true
  else
    certbot --nginx -d "$subdomain" --non-interactive --agree-tos --register-unsafely-without-email --redirect >/dev/null || true
  fi
  sub_cert="requested"
fi

python3 - "$subdomain" "$host_port" "$sub_cert" "$root_cert" "$generated_secrets_path" "$turnkey" <<'PY'
import json, pathlib, sys

payload = {
    "subdomain": sys.argv[1],
    "url": f"https://{sys.argv[1]}",
    "http_url": f"http://{sys.argv[1]}",
    "host_port": int(sys.argv[2]),
    "subdomain_cert": sys.argv[3],
    "root_cert": sys.argv[4],
    "turnkey": sys.argv[6].lower() == "true",
}

secrets_path = pathlib.Path(sys.argv[5])
if secrets_path.exists():
    try:
        generated = json.loads(secrets_path.read_text())
    except Exception:
        generated = {}
    if generated.get("admin_email"):
        payload["admin_email"] = generated["admin_email"]
    if generated.get("admin_password"):
        payload["admin_password"] = generated["admin_password"]

print(json.dumps(payload))
PY
