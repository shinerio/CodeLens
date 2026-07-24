#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
TEST_DIR=$(mktemp -d)
export TMPDIR="$TEST_DIR/tmp"
mkdir -p "$TMPDIR" "$TEST_DIR/bin"

cleanup() {
  "$PROJECT_DIR/code-lens" stop >/dev/null 2>&1 || true
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

"$PROJECT_DIR/code-lens" >"$TEST_DIR/start.log" 2>&1
state_dir="$TMPDIR/codelens-review-${UID}"
if [ ! -f "$state_dir/backend.pid" ]; then
  cat "$TEST_DIR/start.log" >&2
  exit 1
fi
first_backend_pid=$(<"$state_dir/backend.pid")
[ -n "$first_backend_pid" ]
kill -0 "$first_backend_pid"

"$PROJECT_DIR/code-lens" restart >"$TEST_DIR/restart.log" 2>&1
second_backend_pid=$(<"$state_dir/backend.pid")
[ "${second_backend_pid:-}" != "$first_backend_pid" ]
kill -0 "$second_backend_pid"

"$PROJECT_DIR/code-lens" stop >"$TEST_DIR/stop.log"
[ ! -d "$state_dir" ]
! "$PROJECT_DIR/code-lens" unsupported >/dev/null 2>&1
