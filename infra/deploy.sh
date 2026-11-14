#!/usr/bin/env bash
# Deploy the committed code to the Hostinger VPS and (re)start the stack.
#
#   infra/deploy.sh
#
# Reads VPS_HOST, VPS_SSH_USER, VPS_SSH_KEY and (optional) SITE_DOMAIN from .env. Copies the
# committed tree (HEAD), your .env and the Kafka CA certificate over SSH, then builds and starts
# the containers on the VPS. The VPS needs Docker with the compose plugin and the template's
# Traefik (see docker-compose.yml); it needs no GitHub access.
set -euo pipefail

cd "$(dirname "$0")/.."

setting() { # read one KEY=value from .env without executing the file; empty if absent
  { grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2- | tr -d '\r'
}

host="$(setting VPS_HOST)"
user="$(setting VPS_SSH_USER)"
key="$(setting VPS_SSH_KEY)"
ca_file="$(setting KAFKA_CA_FILE)"
domain="$(setting SITE_DOMAIN)"
remote_dir="/opt/factslens"

[ -n "$host" ] || { echo "VPS_HOST is not set in .env" >&2; exit 1; }
[ -f "$ca_file" ] || { echo "Kafka CA file not found: $ca_file" >&2; exit 1; }

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=15)
[ -n "$key" ] && ssh_opts+=(-i "${key/#\~/$HOME}")
target="${user:-root}@${host}"
remote() { ssh "${ssh_opts[@]}" "$target" "$@"; }

if [ -n "$(git status --porcelain)" ]; then
  echo "note: uncommitted changes are NOT deployed (only the last commit is)"
fi
commit="$(git rev-parse --short HEAD)"
echo "deploying commit $commit to $target:$remote_dir"

remote "mkdir -p $remote_dir/secrets $remote_dir/ground_truth && cd $remote_dir && rm -rf src migrations"
git archive HEAD | remote "tar -x -C $remote_dir"
scp -q "${ssh_opts[@]}" .env "$target:$remote_dir/.env"
scp -q "${ssh_opts[@]}" "$ca_file" "$target:$remote_dir/$ca_file"
# Public propaganda lists (eval/ground_truth/fetch.py output): copied, never committed.
ground_truth="eval/ground_truth/data"
if ls "$ground_truth"/*.csv >/dev/null 2>&1; then
  scp -q "${ssh_opts[@]}" "$ground_truth"/*.csv "$target:$remote_dir/ground_truth/"
else
  echo "note: $ground_truth is empty; run eval/ground_truth/fetch.py --resolve-bluesky to enable provenance signals"
fi
remote "chmod 600 $remote_dir/.env && echo $commit > $remote_dir/DEPLOYED_COMMIT"

remote "cd $remote_dir && docker compose up -d --build --remove-orphans && docker compose ps"

[ -n "$domain" ] || domain="$(remote hostname -f)"
echo "waiting for https://$domain/health (the first certificate can take a minute)..."
for _ in $(seq 1 40); do
  if curl -fsS -m 5 "https://$domain/health"; then
    echo
    echo "deployed $commit: https://$domain/"
    exit 0
  fi
  sleep 5
done
echo "the app did not report healthy within ~3 minutes; check: ssh $target 'cd $remote_dir && docker compose logs --tail 50'" >&2
exit 1
