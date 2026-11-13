#!/usr/bin/env bash
# Deploy the committed code to the Hostinger VPS and (re)start the stack.
#
#   infra/deploy.sh
#
# Reads VPS_HOST, VPS_SSH_USER and VPS_SSH_KEY from .env. Copies the committed tree (HEAD),
# your .env and the Kafka CA certificate over SSH, then builds and starts the containers on
# the VPS. The VPS needs Docker with the compose plugin; it needs no GitHub access.
set -euo pipefail

cd "$(dirname "$0")/.."

setting() { # read one KEY=value from .env without executing the file
  grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '\r'
}

host="$(setting VPS_HOST)"
user="$(setting VPS_SSH_USER)"
key="$(setting VPS_SSH_KEY)"
ca_file="$(setting KAFKA_CA_FILE)"
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

remote "mkdir -p $remote_dir/secrets && cd $remote_dir && rm -rf src migrations"
git archive HEAD | remote "tar -x -C $remote_dir"
scp -q "${ssh_opts[@]}" .env "$target:$remote_dir/.env"
scp -q "${ssh_opts[@]}" "$ca_file" "$target:$remote_dir/$ca_file"
remote "chmod 600 $remote_dir/.env && echo $commit > $remote_dir/DEPLOYED_COMMIT"

remote "cd $remote_dir && docker compose up -d --build --remove-orphans && docker compose ps"

echo "waiting for the app to answer..."
for _ in $(seq 1 30); do
  if curl -fsS -m 5 "http://$host/health"; then
    echo
    echo "deployed $commit: http://$host/"
    exit 0
  fi
  sleep 4
done
echo "the app did not report healthy within 2 minutes; check: ssh $target 'cd $remote_dir && docker compose logs --tail 50'" >&2
exit 1
