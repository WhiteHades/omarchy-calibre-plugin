# ADR 0001: Adoption-first native surface

Status: accepted

## Context

Calibre exposes a broad set of library, conversion, server, device, maintenance, and ebook-development tools. Showing every control in an Omarchy panel would make frequent tasks slower and raise the setup cost for new users.

The plugin is intended for broad adoption. It must feel useful on first launch and remain capable for experienced users.

## Decision

The default surface will optimize for the primary workflow: browse, search, add, open, edit metadata, convert, send or export, and safely remove books.

The plugin will use progressive disclosure for format-specific conversion settings, uncommon metadata, diagnostics, and secondary actions. It will not reproduce Calibre's structural ebook editor, ebook renderer, server administration, plugin administration, library recovery, or low-level device filesystem tools in the primary product.

All visible operations will run through Calibre's command-line tools or an isolated Calibre-runtime helper. The plugin will not open the Calibre desktop interface.

## Consequences

- New users get a small, legible default interface.
- Frequent actions remain reachable by mouse and keyboard.
- Advanced conversion and metadata controls remain available without dominating the panel.
- Some specialist Calibre features remain outside the plugin.
- Capability detection is required because installed Calibre versions and format plugins differ.
