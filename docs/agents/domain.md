# Domain docs

Read the repository's domain documentation before exploring or changing code.

## Sources

- Read `CONTEXT.md` at the repository root when it exists.
- If `CONTEXT-MAP.md` exists instead, read the contexts relevant to the task.
- Read applicable decisions under `docs/adr/`.

Missing domain files are not an error. The domain-modeling workflow creates them when a term or architectural decision needs a durable record.

## Layout

This is a single-context repository:

```text
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

Use terms as defined in `CONTEXT.md`. If a proposed change contradicts an ADR, state the conflict before proceeding.
