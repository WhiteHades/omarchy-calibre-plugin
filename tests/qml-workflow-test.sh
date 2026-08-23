#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
omarchy_shell=${OMARCHY_SHELL_PATH:-/usr/share/omarchy/shell}
mkdir -p "$root/.tmp"
fixture=$(mktemp -d "$root/.tmp/qml-workflow.XXXXXX")
result=$(mktemp "$fixture/result.XXXXXX")

cleanup() {
  rm -f "$fixture"/*.qml "$fixture"/*.js "$fixture"/result.*
  rm -f "$fixture/Ui"/*
  rmdir "$fixture/Ui"
  rm -f "$fixture/Commons" "$fixture/assets" "$fixture/backend"
  rmdir "$fixture"
}
trap cleanup EXIT

for source in "$root"/*.qml "$root"/*.js; do
  ln -s "$source" "$fixture/$(basename "$source")"
done
ln -s "$root/tests/qml-workflows/shell.qml" "$fixture/shell.qml"
ln -s "$root/assets" "$fixture/assets"
ln -s "$root/backend" "$fixture/backend"
ln -s "$omarchy_shell/Commons" "$fixture/Commons"
mkdir "$fixture/Ui"
for source in "$omarchy_shell"/Ui/*; do
  if [[ $(basename "$source") != "KeyboardPanel.qml" ]]; then
    ln -s "$source" "$fixture/Ui/$(basename "$source")"
  fi
done
ln -s "$root/tests/qml-workflows/KeyboardPanel.qml" "$fixture/Ui/KeyboardPanel.qml"

OMARCHY_CALIBRE_TEST_RESULT="$result" timeout 10s quickshell --no-color -p "$fixture"

python3 - "$result" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if not payload.get("ok"):
    raise SystemExit("; ".join(payload.get("failures", ["workflow test failed"])))
print("ok - stale workflow results stay isolated")
PY
