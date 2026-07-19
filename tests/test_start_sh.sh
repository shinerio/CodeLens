#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
TEST_DIR=$(mktemp -d)
export TMPDIR="$TEST_DIR/tmp"
mkdir -p "$TMPDIR" "$TEST_DIR/bin"

cleanup() {
  "$PROJECT_DIR/start.sh" stop >/dev/null 2>&1 || true
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT

cat >"$TEST_DIR/bin/uv" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = sync ]; then exit 0; fi
exec sleep 300
EOF
cat >"$TEST_DIR/bin/pnpm" <<'EOF'
#!/usr/bin/env bash
for argument in "$@"; do
  if [ "$argument" = install ]; then exit 0; fi
done
exec sleep 300
EOF
cat >"$TEST_DIR/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TEST_DIR/bin/uv" "$TEST_DIR/bin/pnpm" "$TEST_DIR/bin/curl"
export PATH="$TEST_DIR/bin:$PATH"

"$PROJECT_DIR/start.sh" >"$TEST_DIR/start.log" 2>&1 &
start_pid=$!
state_dir="$TMPDIR/codelens-review-${UID}"
for _ in $(seq 1 50); do
  [ -f "$state_dir/api.pid" ] && break
  sleep 0.1
done
if [ ! -f "$state_dir/api.pid" ]; then
  cat "$TEST_DIR/start.log" >&2
  exit 1
fi
first_api_pid=$(<"$state_dir/api.pid")

"$PROJECT_DIR/start.sh" restart >"$TEST_DIR/restart.log" 2>&1 &
restart_pid=$!
for _ in $(seq 1 50); do
  second_api_pid=$(<"$state_dir/api.pid" 2>/dev/null || true)
  [ -n "$second_api_pid" ] && [ "$second_api_pid" != "$first_api_pid" ] && break
  sleep 0.1
done
[ "${second_api_pid:-}" != "$first_api_pid" ]

"$PROJECT_DIR/start.sh" stop >"$TEST_DIR/stop.log"
wait "$start_pid" || true
wait "$restart_pid" || true
[ ! -d "$state_dir" ]
! "$PROJECT_DIR/start.sh" unsupported >/dev/null 2>&1
