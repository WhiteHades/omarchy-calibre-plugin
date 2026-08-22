# Architecture

## Modules

### Native surface

`BarWidget.qml` owns the bar contract and loads the nested panel. `Panel.qml` owns the two-pane interaction. `Model.js` normalizes view state and stays free of process and filesystem work.

The native surface renders common fields and actions explicitly. It renders uncommon metadata and conversion options from capability descriptors.

### Calibre bridge

`CalibreBridge.qml` adapts Quickshell process input and output to the bridge interface. `backend/calibre_bridge.py` owns request validation, scheduling, Calibre command selection, normalization, safe writes, jobs, and errors.

The interface is asynchronous:

```json
{
  "protocol": 1,
  "id": "request-7",
  "operation": "books.query",
  "library": "library-token",
  "input": {}
}
```

```json
{
  "protocol": 1,
  "id": "request-7",
  "sequence": 2,
  "type": "succeeded",
  "result": {},
  "error": null
}
```

Each request emits `accepted`, optional `progress`, then one of `succeeded`, `failed`, or `cancelled`. Library changes use a separate event with an opaque revision.

### Calibre adapters

The public command adapter uses `calibredb`, `ebook-convert`, `fetch-ebook-metadata`, and `ebook-device`. It is the default adapter and the only mutation adapter.

The runtime helper runs in an isolated `calibre-debug` process. It is read-only and limited to dynamic conversion descriptors that do not have a stable machine-readable command. The bridge disables this capability when the installed version is incompatible.

Tests use stub command and temporary filesystem adapters. Contract tests also run the public command adapter against a disposable Calibre library.

## Safe writes

The bridge validates library tokens and selected paths before starting work. It sends user values as separate process arguments. It never writes directly to `metadata.db` or Calibre-managed book folders.

Operations that can replace or remove data use a prepare and commit flow. The preparation records exact targets, observed revisions, overwrite policy, and an expiry. Commit fails when the plan is stale. Export publication uses same-directory temporary files and atomic no-clobber or exchange operations. Unsafe destination links are rejected.

## Performance

The bridge stays alive while the widget is loaded. Bootstrap combines discovery, capabilities, and the first bounded detail page. A query scans matching IDs, then materializes only the requested page. Search is debounced, and superseded reads are cancellable.

Calibre permits one local `calibredb` process at a time. The bridge therefore serializes those commands across libraries. Conversion, device work, and other independent operations can still overlap.

No numeric latency target is set until a disposable-library benchmark exists.
