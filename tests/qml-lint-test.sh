#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
omarchy_shell=${OMARCHY_SHELL_PATH:-/usr/share/omarchy/shell}
qmllint=${QMLLINT:-/usr/lib/qt6/bin/qmllint}
mkdir -p "$root/.tmp"
fixture=$(mktemp -d "$root/.tmp/qml-lint.XXXXXX")

cleanup() {
  rm -f "$fixture/qs/Commons" "$fixture/qs/Ui"
  rmdir "$fixture/qs" "$fixture"
}
trap cleanup EXIT

test -x "$qmllint"
test -d "$omarchy_shell/Commons"
test -d "$omarchy_shell/Ui"

mkdir "$fixture/qs"
ln -s "$omarchy_shell/Commons" "$fixture/qs/Commons"
ln -s "$omarchy_shell/Ui" "$fixture/qs/Ui"

mapfile -t qml_files < <(find "$root" -maxdepth 1 -name '*.qml' -print | sort)
"$qmllint" --silent -I "$fixture" "${qml_files[@]}"

echo "ok - qml lint"
