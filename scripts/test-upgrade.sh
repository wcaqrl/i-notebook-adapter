#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

: "${LAZYCAT_SOURCE_VERSION:?Selected upstream version is required}"
: "${LAZYCAT_SOURCE_REF:?Selected image reference is required}"
: "${LAZYCAT_SOURCE_REVISION:?Selected platform digest is required}"
[[ "$LAZYCAT_SOURCE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ "$LAZYCAT_SOURCE_REF" == "docker.io/aidenzou/i-notebook:$LAZYCAT_SOURCE_VERSION" ]]
[[ "$LAZYCAT_SOURCE_REVISION" =~ ^sha256:[a-f0-9]{64}$ ]]
PLATFORM="${LAZYCAT_TARGET_PLATFORM:-linux/amd64}"
[[ "$PLATFORM" == linux/amd64 ]]
# The approved 0.0.1 LPK's origin_hash is this official 0.7.1 amd64 manifest.
OLD_IMAGE='docker.io/aidenzou/i-notebook:0.7.1@sha256:a66aba1a819369b6a1bf6f52ba1ab31d3762742570c133444bfcf31d093fe47c'
NEW_IMAGE="$LAZYCAT_SOURCE_REF@$LAZYCAT_SOURCE_REVISION"
TEST_ROOT="$(mktemp -d -t notebook-upgrade.XXXXXXXX)"
CONTAINER="notebook-upgrade-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$"
PROOF=.lazycat-action/upgrade-proof.json
mkdir -p .lazycat-action "$TEST_ROOT/legacy/data/uploads" "$TEST_ROOT/fresh/data/uploads"
rm -f -- "$PROOF"

cleanup() {
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT
on_error() {
  echo 'iNotebook upgrade test failed; no packaging proof was issued.' >&2
  docker logs --tail 70 "$CONTAINER" >&2 2>/dev/null || true
}
trap on_error ERR

docker pull --platform "$PLATFORM" "$OLD_IMAGE" >/dev/null
docker pull --platform "$PLATFORM" "$NEW_IMAGE" >/dev/null
# The test runs as the Runner UID. Copy only the runtime JS directory to a
# writable disposable bind; production setup_script patches the same module
# in the container rootfs before the original startup command executes.
mkdir -p "$TEST_ROOT/runtime"
docker create --name "$CONTAINER" "$NEW_IMAGE" >/dev/null
docker cp "$CONTAINER:/app/server/_ssr/." "$TEST_ROOT/runtime"
docker rm "$CONTAINER" >/dev/null

start_notebook() {
  local image="$1" root="$2" mode="$3" db_mount upload_mount port deadline
  local -a extra_args=() command=()
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || true
  db_mount=/appdata
  upload_mount=/app/uploads
  if [[ "$mode" == legacy ]]; then
    db_mount=/app/prisma
    upload_mount=/app/public/uploads
  else
    extra_args=(--volume "$TEST_ROOT/runtime:/app/server/_ssr" --volume "$PWD/content:/lzcapp/pkg/content:ro" --entrypoint sh)
    command=(-eu -c 'node /lzcapp/pkg/content/prepare-runtime.mjs; exec docker-entrypoint.sh ./scripts/docker-start.sh')
  fi
  docker run --detach --name "$CONTAINER" --platform "$PLATFORM" \
    --user "$(id -u):$(id -g)" --memory 1g --cpus 2 \
    --publish 127.0.0.1::3000 \
    --env "DATABASE_URL=file:$db_mount/db.sqlite" \
    --env SITE_URL=http://localhost \
    --volume "$root/data:$db_mount" \
    --volume "$root/data/uploads:$upload_mount" \
    "${extra_args[@]}" "$image" "${command[@]}" >/dev/null
  port="$(docker port "$CONTAINER" 3000/tcp | awk -F: 'NR == 1 {print $NF}')"
  deadline=$((SECONDS + 150))
  while (( SECONDS < deadline )); do
    if curl --fail --silent "http://127.0.0.1:$port/start" >/dev/null; then
      printf '%s\n' "$port"
      return 0
    fi
    [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" == true ]] || return 1
    sleep 2
  done
  return 1
}

echo 'Testing the real approved database and attachment API on 0.7.1'
python3 -B scripts/notebook-fixture.py init-old "$TEST_ROOT/legacy"
port="$(start_notebook "$OLD_IMAGE" "$TEST_ROOT/legacy" legacy)"
python3 -B scripts/notebook-fixture.py old-upload "$TEST_ROOT/legacy" "http://127.0.0.1:$port"
docker stop --time 20 "$CONTAINER" >/dev/null
# Next.js snapshots public file names at startup: uploaded files become
# directly routable only after recreation in this historical release.
port="$(start_notebook "$OLD_IMAGE" "$TEST_ROOT/legacy" legacy)"
python3 -B scripts/notebook-fixture.py old-download "$TEST_ROOT/legacy" "http://127.0.0.1:$port"
docker stop --time 20 "$CONTAINER" >/dev/null

echo "Testing 0.7.1 -> $LAZYCAT_SOURCE_VERSION at the exact selected digest"
port="$(start_notebook "$NEW_IMAGE" "$TEST_ROOT/legacy" current)"
python3 -B scripts/notebook-fixture.py verify-old "$TEST_ROOT/legacy" "http://127.0.0.1:$port"
docker stop --time 20 "$CONTAINER" >/dev/null
# Upstream must create an actual migration backup before upgrading old schema.
test -n "$(find "$TEST_ROOT/legacy/data" -maxdepth 1 -name 'db.sqlite.backup-*' -print -quit)"
port="$(start_notebook "$NEW_IMAGE" "$TEST_ROOT/legacy" current)"
python3 -B scripts/notebook-fixture.py verify-old "$TEST_ROOT/legacy" "http://127.0.0.1:$port"
docker stop --time 20 "$CONTAINER" >/dev/null

echo 'Testing empty-directory initialization and recreation'
port="$(start_notebook "$NEW_IMAGE" "$TEST_ROOT/fresh" current)"
docker stop --time 20 "$CONTAINER" >/dev/null
python3 -B scripts/notebook-fixture.py configure-api "$TEST_ROOT/fresh"
port="$(start_notebook "$NEW_IMAGE" "$TEST_ROOT/fresh" current)"
python3 -B scripts/notebook-fixture.py verify-fresh "$TEST_ROOT/fresh" "http://127.0.0.1:$port"
docker stop --time 20 "$CONTAINER" >/dev/null
port="$(start_notebook "$NEW_IMAGE" "$TEST_ROOT/fresh" current)"
python3 -B scripts/notebook-fixture.py verify-fresh "$TEST_ROOT/fresh" "http://127.0.0.1:$port"
docker stop --time 20 "$CONTAINER" >/dev/null

python3 - "$PROOF" "$LAZYCAT_SOURCE_VERSION" "${LAZYCAT_APPLICATION_VERSION:-$LAZYCAT_SOURCE_VERSION}" "$LAZYCAT_SOURCE_REVISION" <<'PY'
import hashlib, json, pathlib, sys
path, source, application, digest = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    'baseline_lpk_version': '0.0.1', 'baseline_source_version': '0.7.1',
    'source_version': source, 'application_version': application, 'digest': digest,
    'runtime_patch_sha256': hashlib.sha256(pathlib.Path('content/prepare-runtime.mjs').read_bytes()).hexdigest(),
    'checks': ['legacy-schema', 'notes', 'hierarchy', 'history', 'legacy-task-export', 'settings', 'api-auth', 'attachments', 'fresh-install', 'sqlite', 'migration-backup', 'restart'],
}, indent=2) + '\n')
PY
echo "Verified 0.7.1 -> $LAZYCAT_SOURCE_VERSION and fresh installation; safe to package the tested digest."
