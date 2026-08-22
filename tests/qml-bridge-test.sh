#!/bin/bash

set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TEST_DIR=""
QS_PID=""

cleanup() {
  if [[ -n $QS_PID ]] && kill -0 "$QS_PID" 2>/dev/null; then
    kill "$QS_PID" 2>/dev/null || true
    wait "$QS_PID" 2>/dev/null || true
  fi
  if [[ -n $TEST_DIR && -d $TEST_DIR ]]; then
    rm -rf "$TEST_DIR"
  fi
}
trap cleanup EXIT

command -v quickshell >/dev/null
command -v jq >/dev/null
command -v python3 >/dev/null

mkdir -p "$ROOT/.tmp"
TEST_DIR=$(mktemp -d -p "$ROOT/.tmp" qml-bridge.XXXXXX)
mkdir -p "$TEST_DIR/config" "$TEST_DIR/home" "$TEST_DIR/bin"
ln -s "$ROOT/tests/qml-bridge/shell.qml" "$TEST_DIR/config/shell.qml"
ln -s "$ROOT/CalibreBridge.qml" "$TEST_DIR/config/CalibreBridge.qml"
ln -s "$ROOT/backend" "$TEST_DIR/config/backend"
ln -s "$(command -v python3)" "$TEST_DIR/bin/python3"

result="$TEST_DIR/result.json"
log="$TEST_DIR/quickshell.log"

OMARCHY_CALIBRE_TEST_RESULT="$result" \
HOME="$TEST_DIR/home" \
XDG_CONFIG_HOME="$TEST_DIR/home/.config" \
XDG_CACHE_HOME="$TEST_DIR/home/.cache" \
XDG_STATE_HOME="$TEST_DIR/home/.local/state" \
PATH="$TEST_DIR/bin" \
  /usr/bin/quickshell -p "$TEST_DIR/config" --no-color >"$log" 2>&1 &
QS_PID=$!

for _ in {1..80}; do
  [[ -s $result ]] && break
  if ! kill -0 "$QS_PID" 2>/dev/null; then break; fi
  sleep 0.1
done

if [[ ! -s $result ]]; then
  sed -n '1,220p' "$log" >&2
  exit 1
fi

jq -e '
  .ok == true and
  .pendingFailure == true and
  .result.calibre.status == "missing" and
  .result.readiness.state == "calibre-missing" and
  .result.readiness.actions == [
    "install.calibre.omarchy",
    "open.calibre.download",
    "retry"
  ]
' "$result" >/dev/null

echo "ok - qml bridge missing-Calibre recovery"
