#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$root/.tmp"
fixture=$(mktemp -d "$root/.tmp/qml-compile.XXXXXX")

cleanup() {
  rm -f "$fixture"/*.qml "$fixture"/*.js
  rm -f "$fixture/Commons" "$fixture/Ui" "$fixture/assets" "$fixture/backend"
  rmdir "$fixture"
}
trap cleanup EXIT

for source in "$root"/*.qml "$root"/*.js; do
  ln -s "$source" "$fixture/$(basename "$source")"
done
ln -s "$root/tests/qml-compile/shell.qml" "$fixture/shell.qml"
ln -s "$root/assets" "$fixture/assets"
ln -s "$root/backend" "$fixture/backend"
ln -s "/usr/share/omarchy/shell/Commons" "$fixture/Commons"
ln -s "/usr/share/omarchy/shell/Ui" "$fixture/Ui"

timeout 10s quickshell --no-color -p "$fixture"
