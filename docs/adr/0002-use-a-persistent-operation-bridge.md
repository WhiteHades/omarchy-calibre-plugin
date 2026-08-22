# ADR 0002: Use a persistent operation bridge

Status: accepted

## Context

The native surface must support quick library reads, safe mutations, progress, cancellation, and runtime capability detection. QML must not learn Calibre command syntax, output quirks, private Python types, or filesystem rules.

Three interfaces were compared:

- a minimal asynchronous operation interface;
- a generic capability and resource interface;
- named methods for each common workflow.

The generic interface adapts well but makes common callers assemble abstract queries. Named methods are easy at first but become a shallow module as Calibre behavior spreads into QML.

## Decision

Place one deep Calibre bridge module at the QML and Python seam. Its external interface has three entry points:

```text
submit(operation) -> requestId
cancel(requestId)
event(message)
```

The persistent bridge uses newline-delimited JSON. A bridge operation receives an accepted event, zero or more ordered progress events, and exactly one terminal event.

The bootstrap operation returns detected libraries, the first bounded book page, the Calibre version, and capabilities. Common book records use a stable shape. Uncommon metadata and conversion controls use runtime descriptors.

Public Calibre commands remain the mutation authority. A separate read-only Calibre-runtime adapter can provide faster indexed reads and dynamic option data when capability checks pass. The public command adapter remains the fallback.

## Invariants

- QML submits domain operations, not executable names or shell strings.
- Processes receive argument arrays. User values never become shell code.
- Mutations are serialized per library. Reads can run concurrently.
- Destructive work uses prepare and commit operations with an expiring confirmation token.
- Book deletion always uses Calibre's recoverable path.
- Conversion and export stage output before attachment or replacement.
- Cancellation is idempotent. A committed mutation reports success even when cancellation arrives late.
- Raw command output stays in redacted diagnostics. QML receives stable error codes.
- No adapter launches the Calibre desktop, viewer, editor, or Content Server.

## Consequences

- QML learns one lifecycle for reads, mutations, jobs, and failures.
- Calibre version handling, parsing, safety, and scheduling stay local to the bridge implementation.
- The long-lived process adds protocol and recovery work.
- Runtime descriptors need protocol versioning and conservative fallbacks.
- The common interface remains deliberate while custom fields and format plugins can still appear in advanced controls.
