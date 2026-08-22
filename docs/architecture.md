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

The public command adapter uses `calibredb`, `ebook-convert`, `ebook-meta`, `ebook-polish`, and `ebook-device`. It is the default adapter and the only mutation adapter.

The runtime adapter runs in an isolated `calibre-debug` process. It is read-only and limited to capability discovery, dynamic conversion descriptors, and indexed reads that lack a stable machine-readable command. The bridge disables each runtime-backed capability independently when the installed version is incompatible.

Tests use stub command and temporary filesystem adapters. Contract tests also run the public command adapter against a disposable Calibre library.

## Safe writes

The bridge validates library tokens and selected paths before starting work. It sends user values as separate process arguments. It never writes directly to `metadata.db` or book folders.

Operations that can replace or remove data use a prepare and commit flow. The preparation records exact targets, observed revisions, overwrite policy, and an expiry. Commit fails when the plan is stale.

## Performance

The bridge stays alive while the widget is loaded. Bootstrap combines discovery, capabilities, and the first bounded page. Queries are paged, search is debounced, and superseded reads are cancellable. Covers load only for visible records.

No numeric latency target is set until a disposable-library benchmark exists.
