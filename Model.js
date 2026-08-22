function initialState() {
  return {
    mode: "loading",
    loading: true,
    setupState: "",
    notice: "",
    calibre: {},
    readiness: {},
    libraries: [],
    currentLibrary: "",
    books: [],
    selectedBook: null,
    total: 0,
    nextCursor: null,
    capabilities: { actions: [] },
    jobs: {}
  }
}

function copyState(state) {
  var next = {}
  var source = state || initialState()
  var keys = Object.keys(source)
  for (var i = 0; i < keys.length; i++) next[keys[i]] = source[keys[i]]
  return next
}

function findBook(books, bookId) {
  var wanted = String(bookId)
  for (var i = 0; i < books.length; i++) {
    if (String(books[i].id) === wanted) return books[i]
  }
  return null
}

function selectedFor(books, selectedBook) {
  if (selectedBook) {
    var current = findBook(books, selectedBook.id)
    if (current) return current
  }
  return books.length > 0 ? books[0] : null
}

function applyBootstrap(state, result) {
  var next = copyState(state)
  var payload = result || {}
  var readiness = payload.readiness || {}
  var page = payload.page || {}
  var books = Array.isArray(page.items) ? page.items.slice() : []
  var ready = readiness.state === "ready" || readiness.state === "ready-degraded"

  next.mode = ready ? "library" : "setup"
  next.loading = false
  next.setupState = readiness.state || "calibre-unusable"
  next.notice = readiness.state === "ready-degraded"
    ? "Conversion is unavailable until the Calibre installation is repaired."
    : ""
  next.calibre = payload.calibre || {}
  next.readiness = readiness
  next.libraries = Array.isArray(payload.libraries) ? payload.libraries.slice() : []
  next.currentLibrary = payload.currentLibrary || ""
  next.books = books
  next.selectedBook = selectedFor(books, next.selectedBook)
  next.total = Number(page.total) || 0
  next.nextCursor = page.nextCursor === undefined ? null : page.nextCursor
  next.capabilities = payload.capabilities || { actions: [] }
  return next
}

function actionLabel(actionId) {
  var labels = {
    "install.calibre.omarchy": "Install with Omarchy",
    "open.calibre.download": "Open Calibre download",
    "choose.library": "Choose library",
    "retry": "Retry"
  }
  return labels[actionId] || actionId
}

function setupContent(state) {
  var setupState = state && state.setupState ? state.setupState : "calibre-unusable"
  var content = {
    title: "Calibre could not start",
    body: "Repair or reinstall Calibre, then retry.",
    actions: []
  }

  if (setupState === "calibre-missing") {
    content.title = "Calibre is required"
    content.body = "Install Calibre to manage your library from this panel."
  } else if (setupState === "calibre-unsupported") {
    content.title = "Calibre needs an update"
    content.body = "This plugin supports Calibre 7 or newer. Update Calibre, then retry."
  } else if (setupState === "library-missing") {
    content.title = "Choose a Calibre library"
    content.body = "Select an existing Calibre library folder to continue."
  }

  var actions = state && state.readiness && Array.isArray(state.readiness.actions)
    ? state.readiness.actions
    : []
  for (var i = 0; i < actions.length; i++) {
    content.actions.push({ id: actions[i], label: actionLabel(actions[i]) })
  }
  return content
}

function selectBook(state, bookId) {
  var next = copyState(state)
  next.selectedBook = findBook(next.books || [], bookId)
  return next
}

function selectLibrary(state, libraryToken) {
  var libraries = state && Array.isArray(state.libraries) ? state.libraries : []
  var found = false
  for (var i = 0; i < libraries.length; i++) {
    if (String(libraries[i].token) === String(libraryToken)) {
      found = true
      break
    }
  }
  if (!found) return state

  var next = copyState(state)
  next.currentLibrary = String(libraryToken)
  next.books = []
  next.selectedBook = null
  next.total = 0
  next.nextCursor = null
  next.loading = true
  return next
}

function applyQuery(state, page, append) {
  var next = copyState(state)
  var incoming = page && Array.isArray(page.items) ? page.items : []
  var books = append && Array.isArray(next.books) ? next.books.slice() : []
  var positions = {}
  var i

  for (i = 0; i < books.length; i++) positions[String(books[i].id)] = i
  for (i = 0; i < incoming.length; i++) {
    var key = String(incoming[i].id)
    if (positions[key] === undefined) {
      positions[key] = books.length
      books.push(incoming[i])
    } else {
      books[positions[key]] = incoming[i]
    }
  }

  next.loading = false
  next.books = books
  next.selectedBook = selectedFor(books, next.selectedBook)
  next.total = page && isFinite(Number(page.total)) ? Number(page.total) : books.length
  next.nextCursor = page && page.nextCursor !== undefined ? page.nextCursor : null
  return next
}

function applyBook(state, book) {
  if (!book || book.id === undefined || book.id === null) return state
  var next = copyState(state)
  var books = Array.isArray(next.books) ? next.books.slice() : []
  var found = false
  for (var i = 0; i < books.length; i++) {
    if (String(books[i].id) !== String(book.id)) continue
    books[i] = book
    found = true
    break
  }
  if (!found) books.push(book)
  next.books = books
  next.selectedBook = selectedFor(books, next.selectedBook)
  return next
}

function preferredFormat(book, preferences) {
  var formats = book && Array.isArray(book.formats) ? book.formats : []
  if (formats.length === 0) return ""
  var preferred = Array.isArray(preferences) ? preferences : []
  var available = {}
  var order = []
  var i

  for (i = 0; i < formats.length; i++) {
    var name = String(formats[i].name || formats[i]).toUpperCase()
    if (!name) continue
    available[name] = true
    order.push(name)
  }
  for (i = 0; i < preferred.length; i++) {
    var candidate = String(preferred[i]).toUpperCase()
    if (available[candidate]) return candidate
  }
  return order.length > 0 ? order[0] : ""
}

function parseFormatPreference(value) {
  var source = Array.isArray(value) ? value : String(value || "").split(",")
  var formats = []
  for (var i = 0; i < source.length; i++) {
    var format = String(source[i] || "").trim().toUpperCase()
    if (/^[A-Z0-9]{1,32}$/.test(format) && formats.indexOf(format) === -1) formats.push(format)
  }
  return formats.length > 0 ? formats : ["EPUB", "AZW3", "PDF", "MOBI"]
}

function formatPublished(value) {
  var text = String(value || "")
  var match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text)
  if (!match) return "—"
  var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  var month = Number(match[2])
  var day = Number(match[3])
  if (month < 1 || month > 12 || day < 1 || day > 31) return "—"
  return day + " " + months[month - 1] + " " + match[1]
}

function formatBytes(value) {
  var bytes = Number(value)
  if (!isFinite(bytes) || bytes <= 0) return "0 B"
  var units = ["B", "KiB", "MiB", "GiB", "TiB"]
  var index = 0
  while (bytes >= 1024 && index < units.length - 1) {
    bytes /= 1024
    index++
  }
  var rounded = index === 0 ? Math.round(bytes) : Math.round(bytes * 10) / 10
  return rounded + " " + units[index]
}

function combineSearch(query, filter) {
  var typed = String(query || "").trim()
  var scoped = String(filter || "").trim()
  if (typed && scoped) return "(" + typed + ") and (" + scoped + ")"
  return typed || scoped
}

function beginRequest(state, requestId, label) {
  var next = copyState(state)
  var jobs = {}
  var current = next.jobs || {}
  var keys = Object.keys(current)
  for (var i = 0; i < keys.length; i++) jobs[keys[i]] = current[keys[i]]
  jobs[requestId] = {
    id: requestId,
    label: label || "Working",
    state: "running",
    sequence: 0,
    fraction: 0,
    message: "",
    order: keys.length + 1
  }
  next.jobs = jobs
  return next
}

function jobList(state) {
  var jobs = state && state.jobs ? state.jobs : {}
  var values = []
  var keys = Object.keys(jobs)
  for (var i = 0; i < keys.length; i++) values.push(jobs[keys[i]])
  values.sort(function(a, b) {
    var aRunning = a.state === "running" ? 1 : 0
    var bRunning = b.state === "running" ? 1 : 0
    if (aRunning !== bRunning) return bRunning - aRunning
    return Number(b.order || 0) - Number(a.order || 0)
  })
  return values
}

function activeJobCount(state) {
  var values = jobList(state)
  var count = 0
  for (var i = 0; i < values.length; i++) if (values[i].state === "running") count++
  return count
}

function forgetJob(state, requestId) {
  if (!state || !state.jobs || !state.jobs[requestId]) return state
  var next = copyState(state)
  var jobs = {}
  var keys = Object.keys(state.jobs)
  for (var i = 0; i < keys.length; i++) {
    if (keys[i] !== requestId) jobs[keys[i]] = state.jobs[keys[i]]
  }
  next.jobs = jobs
  return next
}

function applyBridgeEvent(state, event) {
  if (!event || !event.id) return state
  var current = state.jobs && state.jobs[event.id]
  if (!current) return state
  var sequence = Number(event.sequence) || 0
  if (sequence <= Number(current.sequence || 0)) return state

  var next = copyState(state)
  var jobs = {}
  var keys = Object.keys(next.jobs || {})
  var i
  for (i = 0; i < keys.length; i++) jobs[keys[i]] = next.jobs[keys[i]]

  var job = {}
  keys = Object.keys(current)
  for (i = 0; i < keys.length; i++) job[keys[i]] = current[keys[i]]
  job.sequence = sequence

  if (event.type === "progress") {
    job.state = "running"
    job.fraction = event.progress && isFinite(Number(event.progress.fraction))
      ? Math.max(0, Math.min(1, Number(event.progress.fraction)))
      : job.fraction
    job.message = event.progress && event.progress.message ? event.progress.message : ""
  } else if (event.type === "succeeded") {
    job.state = "succeeded"
    job.fraction = 1
    job.result = event.result
  } else if (event.type === "failed") {
    job.state = "failed"
    job.error = event.error || { message: "The operation failed." }
  } else if (event.type === "cancelled") {
    job.state = "cancelled"
  }

  jobs[event.id] = job
  next.jobs = jobs
  return next
}

function commandMatches(command, query) {
  var needle = String(query || "").replace(/^\s+|\s+$/g, "").toLowerCase()
  if (!needle) return true
  var haystack = String((command && command.label) || "") + " "
    + String((command && command.keywords) || "")
  var terms = needle.split(/\s+/)
  haystack = haystack.toLowerCase()
  for (var i = 0; i < terms.length; i++) {
    if (haystack.indexOf(terms[i]) === -1) return false
  }
  return true
}

if (typeof module !== "undefined") {
  module.exports = {
    initialState: initialState,
    applyBootstrap: applyBootstrap,
    setupContent: setupContent,
    selectLibrary: selectLibrary,
    selectBook: selectBook,
    applyQuery: applyQuery,
    applyBook: applyBook,
    preferredFormat: preferredFormat,
    parseFormatPreference: parseFormatPreference,
    formatPublished: formatPublished,
    formatBytes: formatBytes,
    combineSearch: combineSearch,
    beginRequest: beginRequest,
    applyBridgeEvent: applyBridgeEvent,
    jobList: jobList,
    activeJobCount: activeJobCount,
    forgetJob: forgetJob,
    commandMatches: commandMatches
  }
}
