# Product scope

## Product promise

Manage a Calibre library from Omarchy without opening the Calibre desktop interface.

## Primary surface

The two-pane panel keeps the book list on the left and the selected book on the right. It includes:

- local library discovery, switching, and remembered libraries;
- fast Calibre search, common filters, sorting, and saved searches;
- cover, title, authors, series, rating, tags, formats, and reading details;
- file and folder import with Calibre's default duplicate handling;
- common metadata editing, cover changes, and metadata download;
- format opening with a configurable preference order;
- quick conversion with Calibre defaults and an advanced option drawer;
- format add, replace, remove, and export;
- connected-device detection and send-to-device when Calibre supports it;
- safe book removal through Calibre's recoverable deletion path;
- cancellable jobs, progress, errors, diagnostics, recents, and keyboard help;
- a command palette for actions that should not occupy permanent space.

## Secondary controls

These remain available through contextual menus, expandable sections, or the command palette:

- uncommon and custom metadata fields;
- format-specific conversion options and saved profiles;
- full-text search status and indexing;
- reveal in file manager, copy metadata or citation, and open with another app;
- library checks and metadata backup when they can run safely.

## Outside the initial product

- a new ebook renderer or structural ebook editor;
- Calibre Content Server administration;
- Calibre plugin installation and configuration;
- library restore or migration workflows;
- custom-column schema design;
- scheduled news recipes and credential management;
- low-level device filesystem management;
- legacy format-specific utilities.

These exclusions keep the default experience focused. They do not prevent later additions when user demand justifies them.

## Adoption requirements

- The first useful screen must appear without manual configuration when a local library is discoverable.
- Missing Calibre must produce a clear native setup action.
- Defaults must match Calibre where a Calibre default exists.
- Destructive actions must require explicit confirmation.
- The interface must inherit Omarchy colors, typography, spacing, borders, motion, focus, and keyboard behavior.
- Common actions must not require knowledge of Calibre command syntax.
- Advanced controls must not slow down the primary workflow.
