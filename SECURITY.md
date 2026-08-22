# Security policy

## Report a vulnerability

Use [GitHub's private vulnerability report](https://github.com/WhiteHades/omarchy-calibre-plugin/security/advisories/new). Do not post private library paths, book metadata, device details, or exploit steps in a public issue.

Include the plugin version, Calibre version, Omarchy version, affected action, and the smallest safe reproduction. You will receive an acknowledgment after the report is reviewed.

## Supported versions

The latest tagged release and the current `main` branch receive security fixes. Older releases are not supported.

## Security boundaries

Omarchy plugins run without a sandbox. This plugin can read local Calibre libraries, start Calibre command-line tools, open selected book files, write to user-selected export locations, and communicate with connected ebook readers. Metadata fetch can access the network through Calibre's configured providers.

The plugin never installs Calibre silently. A missing-Calibre setup action starts Omarchy's installer only after user input. Library mutations use Calibre's public tools, and destructive replacement or removal requires a revision-bound confirmation.
