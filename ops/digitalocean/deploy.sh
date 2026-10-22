#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE=/opt/maison-autodeploy
REPO=https://github.com/ssss2513-cyber/ai-audio-studio.git
BRANCH=maison-lumiel
ORIGIN=https://143-198-87-243.sslip.io
DATA=/srv/maison-lumiel/data
ENVFILE=/srv/maison-lumiel/spaces.env
APP=maison-lumiel
mkdir -p "$BASE/releases" "$BASE/backups"
exec 9>"$BASE/deploy.lock"
flock -n 9 || exit 0
[[ -f "$ENVFILE" && -f "$DATA/classroom.sqlite3" && -f "$DATA/secret.key" ]] || { echo 'Required persistent data or settings missing; deployment stopped.'; exit 1; }
if [[ ! -d "$BASE/source/.git" ]]; then
  git clone --quiet --single-branch --branch "$BRANCH" "$REPO" "$BASE/source"
fi
git -C "$BASE/source" fetch --quiet origin "$BRANCH"
REV=$(git -C "$BASE/source" rev-parse FETCH_HEAD)
[[ "$REV" =~ ^[0-9a-f]{40}$ ]]
if [[ -f "$BASE/deployed" && $(cat "$BASE/deployed") == "$REV" ]]; then exit 0; fi
# Retry a failed commit only after another push, or removal of this marker.
if [[ -f "$BASE/failed" && $(cat "$BASE/failed") == "$REV" ]]; then exit 0; fi
echo "Building revision $REV"
RELEASE="$BASE/releases/$REV"
mkdir -p "$RELEASE"
git -C "$BASE/source" archive "$REV" | tar -x -C "$RELEASE"
IMAGE="maison-lumiel:git-$REV"
if ! docker build --label "maison.revision=$REV" -t "$IMAGE" "$RELEASE/junyeong-classroom-luxe"; then
  printf '%s\n' "$REV" > "$BASE/failed"
  echo 'Build failed; current site remains running.'
  exit 1
fi
# Capture the current service health without showing environment variables.
health() {
  docker exec "$APP" python -c 'import json,urllib.request; h=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/health",timeout=5)); m=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/meta",timeout=5)); assert h.get("ok") and m.get("ready")' >/dev/null 2>&1
}
health || { echo 'Current site is unhealthy; automatic replacement stopped.'; exit 1; }
# Require the exact durable data mount before doing anything to the live container.
MOUNT=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$APP")
[[ "$MOUNT" == "$DATA" ]] || { echo 'Unexpected data mount; deployment stopped.'; exit 1; }
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$BASE/backups/$STAMP"
mkdir -p "$BACKUP"
ROLLBACK="maison-lumiel-rollback-$STAMP"
SWITCHED=0
recover() {
  status=$?
  trap - EXIT
  if [[ "$SWITCHED" == 1 ]]; then
    docker rm -f "$APP" >/dev/null 2>&1 || true
    docker rename "$ROLLBACK" "$APP"
    docker start "$APP" >/dev/null
    printf '%s\n' "$REV" > "$BASE/failed"
    echo 'Deployment failed; previous container restored. Database snapshots retained.'
  fi
  exit "$status"
}
trap recover EXIT
# Stop writes briefly and back up SQLite through its backup API, including lecture data.
docker stop --time 45 "$APP" >/dev/null
# Restart the existing container if snapshotting fails, before any rename.
if ! python3 - "$DATA" "$BACKUP" <<'PY'
import pathlib,sqlite3,shutil,sys
source,target=map(pathlib.Path,sys.argv[1:])
for path in source.rglob('*'):
    if path.is_file() and path.suffix in ('.sqlite3','.sqlite','.db'):
        dest=target/path.relative_to(source)
        dest.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(str(path)) as src,sqlite3.connect(str(dest)) as dst:
            src.backup(dst)
shutil.copy2(source/'secret.key',target/'secret.key')
PY
then
  docker start "$APP" >/dev/null
  exit 1
fi
if ! docker rename "$APP" "$ROLLBACK"; then docker start "$APP" >/dev/null; exit 1; fi
SWITCHED=1
docker run -d --name "$APP" --restart unless-stopped --network maison-net \
  --env-file "$ENVFILE" -v "$DATA:/data" \
  -e CLASSROOM_DATA_DIR=/data -e PUBLIC_ORIGIN="$ORIGIN" "$IMAGE" >/dev/null
OK=0
for attempt in $(seq 1 30); do
  if health; then OK=1; break; fi
  sleep 2
done
[[ "$OK" == 1 ]] || { echo 'New version failed health checks.'; exit 1; }
# Refresh the proxy upstream connection after the container address changes.
docker restart maison-caddy >/dev/null
curl --fail --silent --show-error --retry 5 --retry-delay 2 --retry-connrefused --max-time 10 "$ORIGIN/api/health" >/dev/null
printf '%s\n' "$REV" > "$BASE/deployed"
rm -f "$BASE/failed"
SWITCHED=0
trap - EXIT
docker tag "$IMAGE" maison-lumiel:local
echo "Deployment verified: $REV; backup: $BACKUP; rollback container: $ROLLBACK"
