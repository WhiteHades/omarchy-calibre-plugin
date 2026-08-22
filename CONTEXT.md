# Domain glossary

## Omarchy Calibre plugin

A user-installed Omarchy Quattro plugin that brings Calibre library workflows into the Omarchy shell. It is not an extension loaded by Calibre.

## Calibre library

A collection of book records and book files managed by Calibre. One Calibre installation can manage more than one library.

## Book record

Calibre's metadata and available file formats for one logical book.

## Native surface

An interface hosted by the Omarchy shell that follows its theme, layout, input, and lifecycle conventions.

## Library source

A local Calibre library selected for browsing and management. Remote Content Servers are outside the first release.

## Remembered library

A library source that the user added to the plugin's library switcher. Remembering a source does not copy or modify its books.

## Safe deletion

Removal of a book record through Calibre's recoverable deletion path after explicit user confirmation.

## Calibre operation

A library or ebook task started and controlled from the native surface. Calibre's command-line tools or source-backed runtime perform the underlying work without opening the Calibre desktop interface.

## Primary workflow

A task that most Calibre users perform regularly: browse, search, add, open, edit metadata, convert, send or export, and remove books.

## Progressive disclosure

An interface rule that keeps the primary workflow visible and places less common controls in contextual menus, expandable sections, or the command palette.

## Calibre bridge

The local process boundary between QML and Calibre. It invokes supported Calibre commands and returns structured results without opening the Calibre desktop interface.

## Bridge operation

A versioned request submitted to the Calibre bridge. Every operation produces ordered events and exactly one terminal result.

## Capability

A runtime statement that a Calibre operation, format, metadata field, or device action is available in the installed environment. The native surface uses capabilities to hide unavailable controls.

## Common metadata

The book fields shown first in the native surface: title, authors, tags, series, series index, rating, publisher, publication date, languages, identifiers, comments, and cover. Other fields remain available through progressive disclosure.

## Book format

One readable file attached to a book record, such as EPUB, AZW3, PDF, or MOBI. A book record can contain several formats.

## Quick conversion

A conversion that uses Calibre's defaults or a saved profile and asks only for the output format. Format-specific controls remain available in an advanced section.
