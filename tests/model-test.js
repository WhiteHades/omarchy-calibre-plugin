const assert = require("node:assert/strict")
const Model = require("../Model.js")

const dune = {
  id: 1,
  title: "Dune",
  authors: ["Frank Herbert"],
  formats: [
    { name: "PDF", path: "/library/Dune.pdf", size: 20 },
    { name: "EPUB", path: "/library/Dune.epub", size: 10 },
  ],
}
const kindred = {
  id: 2,
  title: "Kindred",
  authors: ["Octavia E. Butler"],
  formats: [{ name: "EPUB", path: "/library/Kindred.epub", size: 12 }],
}

let state = Model.initialState()
assert.equal(state.mode, "loading")
assert.deepEqual(state.books, [])
assert.equal(state.selectedBook, null)

state = Model.applyBootstrap(state, {
  calibre: { available: false, status: "missing", version: "", missingCommands: ["calibredb"] },
  readiness: {
    state: "calibre-missing",
    actions: ["install.calibre.omarchy", "open.calibre.download", "retry"],
  },
  libraries: [],
  currentLibrary: "",
  page: { items: [], total: 0, nextCursor: null },
  capabilities: { actions: [] },
})
assert.equal(state.mode, "setup")
assert.equal(Model.setupContent(state).title, "Calibre is required")
assert.match(Model.setupContent(state).body, /Install Calibre/)
assert.deepEqual(Model.setupContent(state).actions.map(action => action.id), [
  "install.calibre.omarchy",
  "open.calibre.download",
  "retry",
])

const setupCases = [
  {
    readiness: "calibre-unsupported",
    title: "Calibre needs an update",
    body: /Calibre 7 or newer/,
  },
  {
    readiness: "calibre-unusable",
    title: "Calibre could not start",
    body: /Repair or reinstall Calibre/,
  },
  {
    readiness: "library-missing",
    title: "Choose a Calibre library",
    body: /library folder/,
  },
]

for (const setupCase of setupCases) {
  const setupState = Model.applyBootstrap(Model.initialState(), {
    calibre: { available: true, status: "ready", version: "9.4", missingCommands: [] },
    readiness: { state: setupCase.readiness, actions: ["retry"] },
    libraries: [],
    currentLibrary: "",
    page: { items: [], total: 0, nextCursor: null },
    capabilities: { actions: [] },
  })
  const content = Model.setupContent(setupState)
  assert.equal(content.title, setupCase.title)
  assert.match(content.body, setupCase.body)
}

const degradedState = Model.applyBootstrap(Model.initialState(), {
  calibre: { available: true, status: "degraded", version: "9.4", missingCommands: ["ebook-convert"] },
  readiness: { state: "ready-degraded", actions: ["install.calibre.omarchy", "retry"] },
  libraries: [{ token: "library-1", name: "Science Fiction", path: "/library" }],
  currentLibrary: "library-1",
  page: { items: [dune], total: 1, nextCursor: null },
  capabilities: { actions: ["book.metadata.update"] },
})
assert.equal(degradedState.mode, "library")
assert.match(degradedState.notice, /Conversion is unavailable/)

state = Model.applyBootstrap(state, {
  calibre: { available: true, status: "ready", version: "9.4", missingCommands: [] },
  readiness: { state: "ready", actions: [] },
  libraries: [{ token: "library-1", name: "Science Fiction", path: "/library" }],
  currentLibrary: "library-1",
  page: { items: [dune, kindred], total: 2, nextCursor: null },
  capabilities: { actions: ["book.metadata.update", "book.convert.quick"] },
})
assert.equal(state.mode, "library")
assert.equal(state.selectedBook.id, 1)
assert.equal(state.total, 2)

const secondLibraryState = Model.applyBootstrap(Model.initialState(), {
  calibre: { available: true, status: "ready", version: "9.4", missingCommands: [] },
  readiness: { state: "ready", actions: [] },
  libraries: [
    { token: "library-1", name: "Science Fiction", path: "/library" },
    { token: "library-2", name: "Nonfiction", path: "/nonfiction" },
  ],
  currentLibrary: "library-1",
  page: { items: [dune], total: 1, nextCursor: null },
  capabilities: { actions: [] },
})
const switchedState = Model.selectLibrary(secondLibraryState, "library-2")
assert.equal(switchedState.currentLibrary, "library-2")
assert.deepEqual(switchedState.books, [])
assert.equal(switchedState.selectedBook, null)
assert.equal(switchedState.total, 0)
assert.equal(Model.selectLibrary(secondLibraryState, "missing"), secondLibraryState)

state = Model.selectBook(state, 2)
assert.equal(state.selectedBook.title, "Kindred")
state = Model.applyQuery(state, { items: [dune], total: 1, nextCursor: null }, false)
assert.equal(state.selectedBook.title, "Dune")

state = Model.applyQuery(state, { items: [kindred], total: 2, nextCursor: null }, true)
assert.deepEqual(state.books.map(book => book.id), [1, 2])
state = Model.applyBook(state, { ...kindred, title: "Kindred: A Novel" })
assert.equal(state.books[1].title, "Kindred: A Novel")

assert.equal(Model.preferredFormat(dune, ["EPUB", "PDF"]), "EPUB")
assert.equal(Model.preferredFormat(dune, ["AZW3"]), "PDF")
assert.equal(Model.preferredFormat({ formats: [] }, ["EPUB"]), "")
assert.equal(Model.formatPublished("2023-07-08T09:32:19+00:00"), "8 Jul 2023")
assert.equal(Model.formatPublished(""), "—")
assert.equal(Model.formatBytes(2980312), "2.8 MiB")
assert.equal(Model.formatBytes(0), "0 B")

state = Model.beginRequest(state, "convert-1", "Convert Dune")
state = Model.applyBridgeEvent(state, {
  id: "convert-1",
  sequence: 1,
  type: "progress",
  progress: { fraction: 0.5, message: "Converting" },
})
assert.equal(state.jobs["convert-1"].state, "running")
assert.equal(state.jobs["convert-1"].fraction, 0.5)
state = Model.applyBridgeEvent(state, { id: "convert-1", sequence: 2, type: "succeeded", result: {} })
assert.equal(state.jobs["convert-1"].state, "succeeded")

assert.equal(Model.commandMatches({ label: "Edit metadata", keywords: "title author tags" }, "author"), true)
assert.equal(Model.commandMatches({ label: "Edit metadata", keywords: "title author tags" }, "device"), false)

console.log("ok - calibre model contracts")
