# Changelog

All notable changes to this project are recorded here.

## 0.1.1 - 2026-08-24

### Fixed

- Matched the Calibre icon to Omarchy's bar dimensions and active theme colors.
- Removed duplicate libraries from the library selector and made it close reliably from its trigger.
- Corrected panel focus, Escape dismissal, file-picker stacking, and hidden-scrollbar behavior.
- Allowed library reads while a local Calibre Content server is running; writes ask you to stop the server and retry.

## 0.1.0 - 2026-08-22

### Added

- Native Omarchy Quattro bar widget and adaptive two-pane library panel.
- Library discovery, search, sorting, common filters, and pagination.
- Book import, metadata and cover editing, metadata fetch, conversion, format management, export, and removal.
- Connected-reader discovery, transfer, replacement checks, and eject support through Calibre.
- Keyboard navigation, command palette, jobs view, diagnostics, and missing-Calibre recovery.

### Safety

- Structured process arguments and stable errors across the QML bridge.
- Expiring confirmation plans with revision checks for destructive work.
- Atomic, no-clobber export publication with stale-target detection.
- Global serialization for local `calibredb` commands to respect Calibre's database lock.
