#!/usr/bin/env bash
# CodeLens Trigger Hook — auto-generated, do not edit manually
set -euo pipefail

CODELENS_API="${CODELENS_API:-http://127.0.0.1:__PORT__}"
REPO_PATH="$(git rev-parse --show-toplevel)"
HOOK_NAME="${1:-$(basename "$0")}"
shift 2>/dev/null || true

# JSON-escape a string: escape backslashes, double quotes, and control characters
json_escape() {
  printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1], end="")'
}

ESCAPED_REPO_PATH="$(json_escape "$REPO_PATH")"

case "$HOOK_NAME" in
  post-commit)
    COMMIT_SHA="$(git rev-parse HEAD)"
    PAYLOAD="{\"event\":\"post-commit\",\"repository_path\":\"$ESCAPED_REPO_PATH\",\"commit_sha\":\"$COMMIT_SHA\"}"
    ;;
  pre-push)
    # pre-push hook receives ref info via stdin: <local_ref> <local_oid> <remote_ref> <remote_oid>
    read -r PUSH_REF _local_oid _remote_ref _remote_oid || PUSH_REF="${1:-}"
    ESCAPED_PUSH_REF="$(json_escape "$PUSH_REF")"
    PAYLOAD="{\"event\":\"pre-push\",\"repository_path\":\"$ESCAPED_REPO_PATH\",\"push_ref\":\"$ESCAPED_PUSH_REF\"}"
    ;;
  *) exit 0 ;;
esac

curl -4 -sf -X POST \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  --noproxy "*" \
  "${CODELENS_API}/api/trigger-events" \
  --max-time 5 \
  -o /dev/null 2>/dev/null || true
