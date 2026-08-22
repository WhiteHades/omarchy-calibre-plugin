# Contributing

Bug reports, focused fixes, and small product improvements are welcome.

## Before you start

Search the [open issues](https://github.com/WhiteHades/omarchy-calibre-plugin/issues) first. Open an issue before a large interface or architecture change so the scope is clear.

Local development requires Omarchy Quattro, Calibre 7 or newer, Python 3, Node.js, `jq`, and Quickshell. Keep test libraries disposable. The bridge can change and remove books.

## Check your work

Run the full test set from the repository root:

```bash
omarchy plugin validate .
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/model-test.js
tests/qml-lint-test.sh
tests/qml-compile-test.sh
tests/qml-workflow-test.sh
tests/qml-bridge-test.sh
```

The QML behavior checks require a local Omarchy Wayland session but do not open a visible panel. For interface changes, test the affected flow in an isolated fixture before testing the installed plugin.

Add a regression test for every bug fix. Keep commits focused and use short conventional subjects such as `fix: guard stale export targets`.

## Licensing

Contributions to code and documentation are accepted under MIT. Do not replace or modify `assets/calibre.svg` without preserving its separate GPL-3.0-only attribution in `assets/NOTICE.md`.
