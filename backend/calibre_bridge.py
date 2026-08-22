#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

try:
    from .bridge_server import DuplicateRequest, OperationContext, OperationScheduler, SchedulerClosed
except ImportError:  # pragma: no cover - used when the bridge runs as a script
    from bridge_server import DuplicateRequest, OperationContext, OperationScheduler, SchedulerClosed

try:
    from .device_adapter import DeviceAdapter, DeviceError
except ImportError:  # pragma: no cover - used when the bridge runs as a script
    from device_adapter import DeviceAdapter, DeviceError


PROTOCOL_VERSION = 1
MINIMUM_CALIBRE_VERSION = (7, 0, 0)
TERMINAL_TYPES = {"succeeded", "failed", "cancelled"}
BOOK_FIELDS = (
    "author_sort,authors,comments,cover,formats,identifiers,isbn,languages,"
    "last_modified,pubdate,publisher,rating,series,series_index,size,tags,"
    "timestamp,title,uuid"
)
SORT_FIELDS = {
    "id",
    "title",
    "authors",
    "author_sort",
    "series",
    "rating",
    "timestamp",
    "last_modified",
    "pubdate",
    "publisher",
    "size",
    "tags",
}
METADATA_FIELDS = {
    "title": "title",
    "authors": "authors",
    "tags": "tags",
    "series": "series",
    "seriesIndex": "series_index",
    "rating": "rating",
    "publisher": "publisher",
    "published": "pubdate",
    "languages": "languages",
    "identifiers": "identifiers",
    "comments": "comments",
}
COVER_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_COVER_BYTES = 50 * 1024 * 1024
MAX_METADATA_RESULT_BYTES = 5 * 1024 * 1024
METADATA_FETCH_TIMEOUT = 120
METADATA_PREVIEW_TTL = 300
DEVICE_BOOK_FOLDERS = ("books", "ebooks", "documents")
MAX_DEVICE_FILENAME_BYTES = 240
DEVICE_ACTIONS = {
    "device.probe",
    "device.info",
    "device.list",
    "device.eject",
    "device.send",
}
LIBRARY_MUTATIONS = {
    "book.metadata.update",
    "book.cover.set",
    "books.import",
    "format.add",
    "book.convert.quick",
}
METADATA_DOWNLOAD_FIELDS = tuple(METADATA_FIELDS)
FileRevision = tuple[int, int, int, int, str]


class BridgeError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        result = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        for key, value in self.details.items():
            if key not in result:
                result[key] = value
        return result


class CalibreBridge:
    def __init__(self, *, device_adapter: DeviceAdapter | None = None) -> None:
        self.libraries: dict[str, Path] = {}
        self.confirmations: dict[str, dict[str, Any]] = {}
        self._conversion_capabilities: dict[str, Any] | None = None
        self._state_lock = threading.RLock()
        self._operation_local = threading.local()
        self.device_adapter = device_adapter if device_adapter is not None else DeviceAdapter(
            runner=self.run_device_command
        )

    def run_device_command(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(command, timeout=timeout, check=False)

    @staticmethod
    def validate_request(request: dict[str, Any]) -> None:
        if not isinstance(request, dict):
            raise BridgeError("invalid_request", "Request must be a JSON object")
        request_id = request.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise BridgeError("invalid_request", "Request id must be a non-empty string")
        if request.get("protocol") != PROTOCOL_VERSION:
            raise BridgeError("invalid_request", "Unsupported bridge protocol")

    @staticmethod
    def canonical_export_destination(value: Any) -> Path | None:
        if not isinstance(value, (str, Path)) or not str(value):
            return None
        try:
            return Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            return None

    @classmethod
    def export_scheduling_key(cls, value: Any) -> tuple[str, str] | None:
        destination = cls.canonical_export_destination(value)
        if destination is None:
            return None
        return ("export", str(destination))

    def scheduling_key(self, request: dict[str, Any]) -> tuple[str, str] | None:
        operation = request.get("operation")
        input_data = request.get("input", {})
        action = input_data.get("name") if isinstance(input_data, dict) else None
        if operation in DEVICE_ACTIONS or action in DEVICE_ACTIONS:
            return ("device", "calibre")
        if operation == "bootstrap":
            return ("bootstrap", "calibre")
        if operation in {"action.commit", "action.discard"} and isinstance(input_data, dict):
            token = input_data.get("confirmationToken")
            with self._state_lock:
                plan = self.confirmations.get(token) if isinstance(token, str) else None
            if operation == "action.commit" and isinstance(plan, dict):
                if plan.get("name") == "device.send.replace":
                    return ("device", "calibre")
                if plan.get("name") == "book.export.replace":
                    export_key = self.export_scheduling_key(plan.get("destination"))
                    if export_key is not None:
                        return export_key
            library_token = plan.get("libraryToken") if isinstance(plan, dict) else None
            if isinstance(library_token, str) and library_token:
                return ("library", library_token)
        if operation in {"action.run", "action.prepare"} and action in {"book.export", "book.export.replace"}:
            export_key = self.export_scheduling_key(input_data.get("destination"))
            if export_key is not None:
                return export_key
        if operation != "action.run" or action not in LIBRARY_MUTATIONS:
            return None
        library_token = request.get("library")
        if isinstance(library_token, str) and library_token:
            return ("library", library_token)
        return None

    def handle(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        self.validate_request(request)
        request_id = request["id"]

        events = [self.event(request_id, 0, "accepted")]
        try:
            result = self.execute(request)
            events.append(self.event(request_id, 1, "succeeded", result=result))
        except BridgeError as error:
            events.append(self.event(request_id, 1, "failed", error=error.as_dict()))
        except DeviceError as error:
            events.append(self.event(request_id, 1, "failed", error=error.as_dict()))
        return events

    def execute(
        self,
        request: dict[str, Any],
        context: OperationContext | None = None,
    ) -> dict[str, Any]:
        self.validate_request(request)
        previous = getattr(self._operation_local, "context", None)
        self._operation_local.context = context
        try:
            operation = request.get("operation")
            if context is not None:
                context.report_progress({"message": self.operation_label(operation)})
            if operation == "bootstrap":
                return self.bootstrap(request.get("input", {}))
            if operation == "books.query":
                return self.books_query(request.get("library"), request.get("input", {}))
            if operation == "conversion.describe":
                return self.describe_conversion(request.get("library"), request.get("input", {}))
            if isinstance(operation, str) and operation in DEVICE_ACTIONS:
                return self.device_action(operation, request.get("library"), request.get("input", {}))
            if operation == "action.run":
                return self.action_run(request.get("library"), request.get("input", {}))
            if operation == "action.prepare":
                return self.action_prepare(request.get("library"), request.get("input", {}))
            if operation == "action.commit":
                return self.action_commit(request.get("input", {}))
            if operation == "action.discard":
                return self.action_discard(request.get("input", {}))
            raise BridgeError("invalid_request", f"Unknown operation: {operation}")
        finally:
            self._operation_local.context = previous

    @staticmethod
    def operation_label(operation: Any) -> str:
        labels = {
            "bootstrap": "Loading Calibre library",
            "books.query": "Searching Calibre library",
            "conversion.describe": "Loading conversion options",
            "action.run": "Running Calibre action",
            "action.prepare": "Preparing Calibre action",
            "action.commit": "Applying confirmed action",
            "action.discard": "Discarding prepared action",
            "book.metadata.fetch": "Fetching book metadata",
            "device.probe": "Checking ebook reader",
            "device.info": "Reading ebook reader",
            "device.list": "Reading ebook reader files",
            "device.send": "Sending book to ebook reader",
            "device.eject": "Ejecting ebook reader",
        }
        return labels.get(operation, "Running Calibre operation")

    @staticmethod
    def event(
        request_id: str,
        sequence: int,
        event_type: str,
        *,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "protocol": PROTOCOL_VERSION,
            "id": request_id,
            "sequence": sequence,
            "type": event_type,
        }
        if event_type == "succeeded":
            event["result"] = result
        if event_type == "failed":
            event["error"] = error
        return event

    def bootstrap(self, input_data: Any) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Bootstrap input must be an object")

        calibre = self.calibre_info()
        remembered = input_data.get("rememberedLibraries", [])
        if not isinstance(remembered, list):
            raise BridgeError("invalid_request", "rememberedLibraries must be an array")

        page_size = input_data.get("pageSize", 50)
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 200:
            raise BridgeError("invalid_request", "pageSize must be between 1 and 200")

        if not calibre["available"]:
            setup_state = {
                "missing": "calibre-missing",
                "unsupported": "calibre-unsupported",
                "unusable": "calibre-unusable",
            }.get(calibre["status"], "calibre-unusable")
            return {
                "calibre": calibre,
                "readiness": {
                    "state": setup_state,
                    "actions": ["install.calibre.omarchy", "open.calibre.download", "retry"],
                },
                "libraries": [],
                "currentLibrary": "",
                "page": self.empty_page(),
                "capabilities": {"actions": []},
            }

        libraries = []
        for candidate in remembered:
            library = self.register_library(candidate)
            if library is not None:
                libraries.append(library)
        if not libraries:
            for candidate in self.discover_libraries():
                library = self.register_library(candidate)
                if library is not None and all(item["token"] != library["token"] for item in libraries):
                    libraries.append(library)

        current = libraries[0]["token"] if libraries else ""
        page = (
            self.query_books(
                current,
                {
                    "limit": page_size,
                    "search": input_data.get("search", ""),
                    "sort": input_data.get("sort", "title"),
                    "direction": input_data.get("direction", "ascending"),
                },
            )
            if current
            else self.empty_page()
        )
        return {
            "calibre": calibre,
            "readiness": self.ready_state(calibre, bool(libraries)),
            "libraries": libraries,
            "currentLibrary": current,
            "page": page,
            "capabilities": self.capabilities(),
        }

    def calibre_info(self) -> dict[str, Any]:
        missing = [command for command in ("calibredb", "ebook-convert") if shutil.which(command) is None]
        executable = shutil.which("calibredb")
        if executable is None:
            return {
                "available": False,
                "installed": False,
                "supported": False,
                "status": "missing",
                "version": "",
                "missingCommands": missing,
            }
        try:
            completed = self.run([executable, "--version"])
        except BridgeError:
            return {
                "available": False,
                "installed": True,
                "supported": False,
                "status": "unusable",
                "version": "",
                "missingCommands": missing,
            }
        match = re.search(r"calibre\s+([^\s)]+)", completed.stdout)
        version = match.group(1) if match else "unknown"
        numeric = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", version)
        parsed = (
            tuple(int(part or 0) for part in numeric.groups())
            if numeric is not None
            else None
        )
        supported = parsed is None or parsed >= MINIMUM_CALIBRE_VERSION
        status = "unsupported" if not supported else ("degraded" if missing else "ready")
        return {
            "available": supported,
            "installed": True,
            "supported": supported,
            "status": status,
            "version": version,
            "missingCommands": missing,
        }

    @staticmethod
    def ready_state(calibre: dict[str, Any], has_library: bool) -> dict[str, Any]:
        if not has_library:
            return {"state": "library-missing", "actions": ["choose.library", "retry"]}
        if calibre["status"] == "degraded":
            return {
                "state": "ready-degraded",
                "actions": ["install.calibre.omarchy", "open.calibre.download", "retry"],
            }
        return {"state": "ready", "actions": []}

    def capabilities(self) -> dict[str, Any]:
        actions = [
            "book.metadata.update",
            "book.cover.set",
            "book.remove",
            "books.import",
            "format.add",
            "format.remove",
            "book.export",
        ]
        if shutil.which("fetch-ebook-metadata"):
            actions.append("book.metadata.fetch")
        if shutil.which("ebook-convert"):
            actions.append("book.convert.quick")
        device = self.device_adapter.capabilities()
        if device.get("available"):
            actions.append("device.probe")
            supports = device.get("supports", {})
            for action, command in (
                ("device.info", "info"),
                ("device.list", "list"),
                ("device.eject", "eject"),
                ("device.send", "send"),
            ):
                if supports.get(command):
                    actions.append(action)
        capabilities: dict[str, Any] = {"actions": actions}
        capabilities["device"] = device
        if "book.convert.quick" in actions:
            capabilities["conversion"] = self.conversion_capabilities()
        return capabilities

    def register_library(self, candidate: Any) -> dict[str, Any] | None:
        if not isinstance(candidate, str) or not candidate:
            return None
        path = Path(candidate).expanduser().resolve()
        if not path.is_dir() or not (path / "metadata.db").is_file():
            return None
        token = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]
        with self._state_lock:
            self.libraries[token] = path
        return {
            "token": token,
            "name": path.name,
            "path": str(path),
        }

    def discover_libraries(self) -> list[str]:
        candidates = [str(Path.home() / "Calibre Library")]
        executable = shutil.which("calibre-debug")
        if executable is None:
            return candidates
        code = (
            "import json; from calibre.utils.config import prefs; "
            "print(json.dumps(prefs['library_path']))"
        )
        try:
            completed = self.run([executable, "-c", code], timeout=30)
            configured = json.loads(completed.stdout.strip().splitlines()[-1])
            if isinstance(configured, str) and configured:
                candidates.insert(0, configured)
        except (BridgeError, IndexError, json.JSONDecodeError):
            pass
        return list(dict.fromkeys(candidates))

    @staticmethod
    def empty_page() -> dict[str, Any]:
        return {"items": [], "total": 0, "nextCursor": None}

    def books_query(self, library_token: Any, input_data: Any) -> dict[str, Any]:
        if not isinstance(library_token, str) or not library_token:
            raise BridgeError("invalid_request", "A library token is required")
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Query input must be an object")
        return self.query_books(library_token, input_data)

    def action_run(self, library_token: Any, input_data: Any) -> dict[str, Any]:
        if isinstance(input_data, dict) and isinstance(input_data.get("name"), str) and input_data["name"] in DEVICE_ACTIONS:
            action = input_data["name"]
            return self.device_action(action, library_token, input_data)
        library = self.require_library(library_token)
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Action input must be an object")
        action = input_data.get("name")
        if action == "book.metadata.update":
            return self.update_metadata(library_token, library, input_data)
        if action == "book.metadata.fetch":
            return self.fetch_metadata(library_token, library, input_data)
        if action == "book.cover.set":
            return self.set_cover(library_token, library, input_data)
        if action == "books.import":
            return self.import_books(library, input_data)
        if action == "format.add":
            return self.add_format(library_token, library, input_data)
        if action == "book.export":
            return self.export_books(str(library_token), library, input_data)
        if action == "book.convert.quick":
            return self.quick_convert(str(library_token), library, input_data)
        raise BridgeError("capability_unavailable", f"Unsupported action: {action}")

    def device_action(
        self,
        action: str,
        library_token: Any,
        input_data: Any,
    ) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Device input must be an object")
        if action == "device.probe":
            return self.device_adapter.probe()
        if action == "device.info":
            return self.device_adapter.info()
        if action == "device.list":
            path = input_data.get("path", "/")
            recursive = input_data.get("recursive", False)
            return self.device_adapter.list(path, recursive=recursive)
        if action == "device.eject":
            self.begin_commit()
            return self.device_adapter.eject()
        if action == "device.send":
            return self.device_send(library_token, input_data)
        raise BridgeError("invalid_request", f"Unknown device action: {action}")

    def fetch_metadata(
        self,
        library_token: Any,
        library: Path,
        input_data: Any,
    ) -> dict[str, Any]:
        """Fetch metadata into a private preview plan without changing Calibre."""

        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Metadata input must be an object")
        book_id = self.require_book_id(input_data.get("bookId"))
        executable = shutil.which("fetch-ebook-metadata")
        if executable is None:
            raise BridgeError(
                "capability_unavailable",
                "The fetch-ebook-metadata command is unavailable",
                retryable=True,
            )
        timeout = input_data.get("timeout", METADATA_FETCH_TIMEOUT)
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or not 1 <= timeout <= 300
        ):
            raise BridgeError("invalid_request", "Metadata timeout must be between 1 and 300 seconds")

        book = self.get_book(str(library_token), book_id)
        staging = self.metadata_staging_directory()
        cover_path = staging / "cover.jpg"
        try:
            command = self.metadata_fetch_command(
                executable,
                book,
                cover_path,
                timeout,
            )
            completed = self.run(command, timeout=timeout, check=False)
            output = completed.stdout or ""
            if completed.returncode != 0:
                stderr_lines = [line.strip() for line in (completed.stderr or "").splitlines()]
                if not output.strip() and "No results found" in stderr_lines:
                    raise BridgeError(
                        "metadata_no_result",
                        "Calibre found no metadata for this book",
                        retryable=True,
                    )
                raise BridgeError("tool_failed", "Calibre metadata lookup failed")
            if not output.strip():
                raise BridgeError(
                    "metadata_no_result",
                    "Calibre returned no metadata result",
                    retryable=True,
                )
            if len(output.encode("utf-8")) > MAX_METADATA_RESULT_BYTES:
                raise BridgeError("metadata_result_too_large", "Calibre returned too much metadata")
            downloaded = self.parse_metadata_opf(output)
            candidate = self.complete_metadata_candidate(book, downloaded)
            cover = self.metadata_cover_path(staging)
            changes = self.metadata_changes(book, candidate, cover is not None)
            plan = {
                "expires": time.monotonic() + METADATA_PREVIEW_TTL,
                "name": "book.metadata.fetch",
                "libraryToken": str(library_token),
                "library": library,
                "bookId": book_id,
                "bookRevision": book.get("modified", ""),
                "candidate": candidate,
                "cover": cover,
                "staging": staging,
            }
            # The preview remains cancellable while the network-backed CLI runs.
            # Once the staged plan is stored, fence the operation so a late
            # cancel cannot strand its temporary directory between requests.
            token = self.store_confirmation(plan)
            return {
                "previewToken": token,
                "bookId": book_id,
                "candidate": candidate,
                "changes": changes,
                "coverAvailable": cover is not None,
                "coverPath": str(cover) if cover is not None else "",
                "expiresInSeconds": METADATA_PREVIEW_TTL,
            }
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def metadata_staging_directory() -> Path:
        return Path(tempfile.mkdtemp(prefix="omarchy-calibre-metadata-"))

    @staticmethod
    def metadata_fetch_command(
        executable: str,
        book: dict[str, Any],
        cover_path: Path,
        timeout: int,
    ) -> list[str]:
        title = str(book.get("title", "") or "").strip()
        authors = book.get("authors", [])
        if isinstance(authors, str):
            authors = [authors]
        if not isinstance(authors, list):
            authors = []
        authors = [str(author).strip() for author in authors if str(author).strip()]
        identifiers = book.get("identifiers", {})
        if not isinstance(identifiers, dict):
            identifiers = {}
        identifiers = {
            str(key).strip().lower(): str(value).strip()
            for key, value in identifiers.items()
            if str(key).strip() and str(value).strip()
        }
        isbn = str(book.get("isbn", "") or "").strip()
        if not isbn:
            for key in ("isbn", "isbn13", "isbn10"):
                if identifiers.get(key):
                    isbn = identifiers[key]
                    break

        if not title and not authors and not isbn and not identifiers:
            raise BridgeError(
                "metadata_identity_missing",
                "The selected book has no title, author, ISBN, or identifier",
            )

        command = [executable, "--opf"]
        if title:
            command.extend(["--title", title])
        if authors:
            command.extend(["--authors", " & ".join(authors)])
        if isbn:
            command.extend(["--isbn", isbn])
        for key, value in identifiers.items():
            if key in {"isbn", "isbn10", "isbn13"}:
                continue
            command.extend(["--identifier", f"{key}:{value}"])
        command.extend(["--cover", str(cover_path), "--timeout", str(timeout)])
        return command

    @staticmethod
    def metadata_cover_path(staging: Path) -> Path | None:
        candidate = staging / "cover.jpg"
        if not candidate.exists():
            return None
        try:
            resolved = candidate.resolve(strict=True)
            root = staging.resolve(strict=True)
            size = resolved.stat().st_size
        except (OSError, RuntimeError) as error:
            raise BridgeError("metadata_cover_invalid", "Calibre returned an unreadable cover") from error
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise BridgeError("metadata_cover_invalid", "Calibre returned an invalid cover")
        if size < 1 or size > MAX_COVER_BYTES:
            raise BridgeError("metadata_cover_invalid", "Calibre returned an invalid cover size")
        return resolved

    @staticmethod
    def opf_local_name(tag: Any) -> str:
        if not isinstance(tag, str):
            return ""
        return tag.rsplit("}", 1)[-1].lower()

    @classmethod
    def opf_text(cls, element: ET.Element) -> str:
        return "".join(element.itertext()).strip()

    @classmethod
    def parse_metadata_opf(cls, output: str) -> dict[str, Any]:
        try:
            root = ET.fromstring(output)
        except ET.ParseError as error:
            raise BridgeError("metadata_malformed_opf", "Calibre returned malformed OPF metadata") from error

        metadata_nodes = [
            element
            for element in root.iter()
            if cls.opf_local_name(element.tag) == "metadata"
        ]
        if not metadata_nodes:
            raise BridgeError("metadata_malformed_opf", "Calibre returned OPF without metadata")
        metadata = metadata_nodes[0]
        result: dict[str, Any] = {}

        creators = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "creator" and cls.opf_text(element)
        ]
        if creators:
            result["authors"] = creators
        titles = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "title" and cls.opf_text(element)
        ]
        if titles:
            result["title"] = titles[0]
        subjects = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "subject" and cls.opf_text(element)
        ]
        if subjects:
            result["tags"] = subjects
        publishers = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "publisher" and cls.opf_text(element)
        ]
        if publishers:
            result["publisher"] = publishers[0]
        dates = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "date" and cls.opf_text(element)
        ]
        if dates:
            result["published"] = dates[0]
        languages = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "language" and cls.opf_text(element)
        ]
        if languages:
            result["languages"] = languages
        descriptions = [
            cls.opf_text(element)
            for element in metadata.iter()
            if cls.opf_local_name(element.tag) == "description" and cls.opf_text(element)
        ]
        if descriptions:
            result["comments"] = descriptions[0]

        identifiers: dict[str, str] = {}
        for element in metadata.iter():
            if cls.opf_local_name(element.tag) != "identifier":
                continue
            value = cls.opf_text(element)
            if not value:
                continue
            attributes = {
                cls.opf_local_name(key): str(attribute).strip()
                for key, attribute in element.attrib.items()
            }
            scheme = attributes.get("scheme", "").lower()
            if not scheme and ":" in value:
                prefix, possible_value = value.split(":", 1)
                if prefix and possible_value:
                    scheme, value = prefix.lower(), possible_value
            if scheme in {"calibre", "uuid"} or value.lower().startswith("urn:uuid:"):
                continue
            identifiers[scheme or "identifier"] = value
        if identifiers:
            result["identifiers"] = identifiers

        for element in metadata.iter():
            if cls.opf_local_name(element.tag) != "meta":
                continue
            attributes = {
                cls.opf_local_name(key): str(attribute).strip()
                for key, attribute in element.attrib.items()
            }
            name = attributes.get("name", attributes.get("property", "")).lower()
            content = attributes.get("content", "").strip()
            if not content:
                continue
            if name in {"calibre:series", "series"}:
                result["series"] = content
            elif name in {"calibre:series_index", "series_index"}:
                try:
                    result["seriesIndex"] = float(content)
                except ValueError:
                    continue
            elif name in {"calibre:rating", "rating"}:
                try:
                    rating = float(content)
                except ValueError:
                    continue
                result["rating"] = rating / 2

        if not result:
            raise BridgeError("metadata_no_result", "Calibre returned no usable metadata result", retryable=True)
        return result

    @staticmethod
    def complete_metadata_candidate(
        book: dict[str, Any],
        downloaded: dict[str, Any],
    ) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            field: book.get(field, [] if field in {"authors", "tags", "languages"} else {})
            for field in METADATA_DOWNLOAD_FIELDS
        }
        defaults["title"] = str(book.get("title", "") or "")
        defaults["authors"] = list(book.get("authors", []) or [])
        defaults["tags"] = list(book.get("tags", []) or [])
        defaults["languages"] = list(book.get("languages", []) or [])
        defaults["identifiers"] = dict(book.get("identifiers", {}) or {})
        defaults["series"] = str(book.get("series", "") or "")
        defaults["seriesIndex"] = float(book.get("seriesIndex", 1.0) or 1.0)
        defaults["rating"] = float(book.get("rating", 0) or 0)
        defaults["publisher"] = str(book.get("publisher", "") or "")
        defaults["published"] = str(book.get("published", "") or "")
        defaults["comments"] = str(book.get("comments", "") or "")
        for field, value in downloaded.items():
            if field not in METADATA_FIELDS:
                continue
            if field == "tags":
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    existing = defaults["tags"]
                    existing_names = {item.casefold() for item in existing}
                    downloaded_tags = []
                    seen = set(existing_names)
                    for item in value:
                        key = item.casefold()
                        if key in seen:
                            continue
                        seen.add(key)
                        downloaded_tags.append(item)
                    defaults[field] = downloaded_tags + existing
            elif field in {"authors", "languages"}:
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    defaults[field] = value
            elif field == "identifiers":
                if isinstance(value, dict) and all(
                    isinstance(key, str) and isinstance(item, str)
                    for key, item in value.items()
                ):
                    defaults[field] = {**defaults["identifiers"], **value}
            elif field in {"rating", "seriesIndex"}:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    defaults[field] = value
            elif isinstance(value, str):
                defaults[field] = value
        return defaults

    @staticmethod
    def metadata_changes(
        book: dict[str, Any],
        candidate: dict[str, Any],
        cover_available: bool,
    ) -> list[dict[str, Any]]:
        changes = []
        for field in METADATA_DOWNLOAD_FIELDS:
            current = book.get(field, [] if field in {"authors", "tags", "languages"} else {})
            proposed = candidate.get(field)
            if current != proposed:
                changes.append({"field": field, "current": current, "proposed": proposed})
        if cover_available:
            changes.append({
                "field": "cover",
                "current": bool(book.get("cover")),
                "proposed": True,
            })
        return changes

    def apply_metadata_preview(
        self,
        plan: dict[str, Any],
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        selected = input_data.get("fields", input_data.get("selectedFields"))
        if not isinstance(selected, list) or not selected:
            raise BridgeError("invalid_request", "Metadata fields must contain at least one field")
        if len(selected) > len(METADATA_DOWNLOAD_FIELDS) + 1 or not all(
            isinstance(field, str) for field in selected
        ):
            raise BridgeError("invalid_request", "Metadata fields are invalid")
        selected = list(dict.fromkeys(selected))
        allowed = set(METADATA_DOWNLOAD_FIELDS) | {"cover"}
        if any(field not in allowed for field in selected):
            raise BridgeError("invalid_request", "Metadata fields contain an unsupported field")

        current = self.get_book(plan["libraryToken"], plan["bookId"])
        if current.get("modified", "") != plan.get("bookRevision", ""):
            raise BridgeError("confirmation_stale", "The selected book changed; review metadata again")
        cover = plan.get("cover")
        if "cover" in selected:
            staging = plan.get("staging")
            current_cover = self.metadata_cover_path(staging) if isinstance(staging, Path) else None
            if not isinstance(cover, Path) or current_cover != cover.resolve():
                raise BridgeError("metadata_cover_unavailable", "The metadata preview has no cover to apply")
            cover = current_cover

        fields = {
            field: plan["candidate"][field]
            for field in selected
            if field in METADATA_FIELDS
        }
        command = [
            "calibredb",
            "set_metadata",
            "--with-library",
            str(plan["library"]),
            str(plan["bookId"]),
        ]
        for field, value in fields.items():
            calibre_field = METADATA_FIELDS[field]
            command.extend(["--field", f"{calibre_field}:{self.metadata_value(field, value)}"])
        if "cover" in selected:
            command.extend(["--field", f"cover:{cover}"])
        self.run(command, commit=True)
        return {
            "book": self.get_book(plan["libraryToken"], plan["bookId"]),
            "appliedFields": selected,
        }

    def device_send(self, library_token: Any, input_data: dict[str, Any]) -> dict[str, Any]:
        library = self.require_library(library_token)
        book_id = self.require_book_id(input_data.get("bookId"))
        format_name = self.require_format_name(input_data.get("format"), "format")
        force = input_data.get("force", False)
        if not isinstance(force, bool):
            raise BridgeError("invalid_request", "force must be a boolean")
        if force:
            raise BridgeError(
                "confirmation_required",
                "Replacing a reader file requires a fresh confirmation",
            )

        book = self.get_book(str(library_token), book_id)
        self.find_format(book, format_name)
        device_identity = self.device_adapter.info()
        raw_destination = input_data.get("destination")
        destination = self.default_device_destination(book, format_name) \
            if raw_destination is None else self.require_device_destination(raw_destination)
        staging = Path(tempfile.mkdtemp(prefix="omarchy-calibre-device-"))
        retained = False
        try:
            self.run(
                [
                    "calibredb",
                    "export",
                    "--with-library",
                    str(library),
                    "--to-dir",
                    str(staging),
                    "--single-dir",
                    "--formats",
                    format_name,
                    "--dont-update-metadata",
                    "--dont-write-opf",
                    "--dont-save-cover",
                    "--dont-save-extra-files",
                    str(book_id),
                ]
            )
            source = self.staged_device_format(staging, format_name)
            self.begin_commit()
            try:
                sent = self.device_adapter.send(source, destination, force=False)
            except DeviceError as error:
                if error.code != "destination_exists":
                    raise
                target_revision = self.device_file_revision(destination)
                if target_revision is None:
                    raise BridgeError(
                        "confirmation_stale",
                        "The reader copy changed; send the book again before replacing it",
                        retryable=True,
                    ) from error
                destination = target_revision[0]
                token = self.store_confirmation({
                    "expires": time.monotonic() + 60,
                    "name": "device.send.replace",
                    "libraryToken": str(library_token),
                    "library": library,
                    "bookId": book_id,
                    "bookRevision": book.get("modified", ""),
                    "format": format_name,
                    "deviceIdentity": device_identity,
                    "destination": destination,
                    "targetRevision": target_revision,
                    "source": source,
                    "sourceRevision": self.file_revision(source),
                    "staging": staging,
                })
                retained = True
                raise BridgeError(
                    "destination_exists",
                    "This book already exists on the ebook reader",
                    retryable=True,
                    details={
                        "confirmationToken": token,
                        "expiresInSeconds": 60,
                        "format": format_name,
                        "destination": destination,
                    },
                ) from error
            return {**sent, "bookId": book_id, "format": format_name}
        finally:
            if not retained:
                shutil.rmtree(staging, ignore_errors=True)

    def action_prepare(self, library_token: Any, input_data: Any) -> dict[str, Any]:
        library = self.require_library(library_token)
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Action input must be an object")
        action = input_data.get("name")
        if action == "format.replace":
            return self.prepare_format_replace(str(library_token), library, input_data)
        if action == "format.remove":
            return self.prepare_format_remove(str(library_token), library, input_data)
        if action == "book.export.replace":
            return self.prepare_export_replace(str(library_token), library, input_data)
        if action == "book.convert.replace":
            return self.prepare_conversion_replace(str(library_token), library, input_data)
        if action != "book.remove":
            raise BridgeError("capability_unavailable", "Unsupported destructive action")
        raw_ids = input_data.get("bookIds")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or len(raw_ids) > 100
            or not all(isinstance(book_id, int) and not isinstance(book_id, bool) and book_id > 0 for book_id in raw_ids)
        ):
            raise BridgeError("invalid_request", "bookIds must contain between 1 and 100 positive integers")
        book_ids = list(dict.fromkeys(raw_ids))
        books = [self.get_book(library_token, book_id) for book_id in book_ids]
        confirmation_token = self.store_confirmation({
            "expires": time.monotonic() + 60,
            "name": "book.remove",
            "libraryToken": library_token,
            "library": library,
            "bookIds": book_ids,
            "bookRevisions": {book["id"]: book["modified"] for book in books},
        })
        titles = ", ".join(book["title"] for book in books[:3])
        if len(books) > 3:
            titles += f", and {len(books) - 3} more"
        return {
            "confirmationToken": confirmation_token,
            "expiresInSeconds": 60,
            "summary": f"Remove {len(books)} book{'s' if len(books) != 1 else ''}: {titles}",
        }

    def action_commit(self, input_data: Any) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Commit input must be an object")
        token = input_data.get("confirmationToken")
        if not isinstance(token, str) or not token:
            raise BridgeError("confirmation_required", "A valid confirmation is required")
        plan = self.pop_confirmation(token)
        if plan is None:
            raise BridgeError("confirmation_required", "The confirmation has expired or was already used")
        if plan["expires"] < time.monotonic():
            self.cleanup_plan(plan)
            raise BridgeError("confirmation_required", "The confirmation has expired or was already used")
        if plan["name"] == "book.metadata.fetch":
            try:
                return self.apply_metadata_preview(plan, input_data)
            finally:
                self.cleanup_plan(plan)
        if plan["name"] == "device.send.replace":
            try:
                current = self.get_book(plan["libraryToken"], plan["bookId"])
                if current.get("modified", "") != plan.get("bookRevision", ""):
                    raise BridgeError(
                        "confirmation_stale",
                        "The selected book changed; send it again before replacing the reader copy",
                    )
                self.find_format(current, plan["format"])
                self.require_unchanged_file(
                    plan["source"],
                    plan["sourceRevision"],
                    "The staged reader file changed; send the book again",
                )
                if self.device_adapter.info() != plan["deviceIdentity"]:
                    raise BridgeError(
                        "confirmation_stale",
                        "The connected ebook reader changed; send the book again",
                    )
                if self.device_file_revision(plan["destination"]) != plan["targetRevision"]:
                    raise BridgeError(
                        "confirmation_stale",
                        "The reader copy changed; send the book again before replacing it",
                    )
                self.begin_commit()
                sent = self.device_adapter.send(
                    plan["source"],
                    plan["destination"],
                    force=True,
                )
                return {
                    **sent,
                    "bookId": plan["bookId"],
                    "format": plan["format"],
                }
            finally:
                self.cleanup_plan(plan)
        if plan["name"] == "book.remove":
            book_ids = plan["bookIds"]
            for book_id in book_ids:
                book = self.get_book(plan["libraryToken"], book_id)
                if book["modified"] != plan["bookRevisions"][book_id]:
                    raise BridgeError("confirmation_stale", "The selected books changed; review the removal again")
            self.run(
                [
                    "calibredb",
                    "remove",
                    "--with-library",
                    str(plan["library"]),
                    ",".join(str(book_id) for book_id in book_ids),
                ],
                commit=True,
            )
            return {"removedIds": book_ids}
        if plan["name"] == "format.replace":
            source = plan["source"]
            self.require_unchanged_file(source, plan["sourceRevision"], "The replacement file changed")
            current = self.get_book(plan["libraryToken"], plan["bookId"])
            target = self.find_format(current, plan["format"])
            self.require_unchanged_file(
                Path(target["path"]),
                plan["targetRevision"],
                "The existing format changed; review the replacement again",
            )
            self.run(
                [
                    "calibredb",
                    "add_format",
                    "--with-library",
                    str(plan["library"]),
                    str(plan["bookId"]),
                    str(source),
                ],
                commit=True,
            )
            book = self.get_book(plan["libraryToken"], plan["bookId"])
            attached = self.find_format(book, plan["format"])
            return {"book": book, "format": attached, "replaced": True}
        if plan["name"] == "format.remove":
            current = self.get_book(plan["libraryToken"], plan["bookId"])
            target = self.find_format(current, plan["format"])
            self.require_unchanged_file(
                Path(target["path"]),
                plan["targetRevision"],
                "The selected format changed; review the removal again",
            )
            self.run(
                [
                    "calibredb",
                    "remove_format",
                    "--with-library",
                    str(plan["library"]),
                    str(plan["bookId"]),
                    plan["format"],
                ],
                commit=True,
            )
            book = self.get_book(plan["libraryToken"], plan["bookId"])
            return {"book": book, "removedFormat": plan["format"]}
        if plan["name"] == "book.export.replace":
            try:
                staging = plan["staging"]
                destination = plan["destination"]
                for relative, revision in plan["stagedRevisions"].items():
                    self.require_unchanged_file(
                        staging / relative,
                        revision,
                        "The staged export changed; start the export again",
                    )
                for relative, revision in plan["targetRevisions"].items():
                    target = destination / relative
                    if revision is None:
                        if target.exists() or target.is_symlink():
                            raise BridgeError(
                                "confirmation_stale",
                                "A new export collision appeared; review the replacement again",
                            )
                    else:
                        self.require_unchanged_file(
                            target,
                            revision,
                            "An exported file changed; review the replacement again",
                        )
                self.begin_commit()
                files = self.publish_staged_export(
                    staging,
                    destination,
                    replace=True,
                    target_revisions=plan["targetRevisions"],
                )
                return self.export_result(destination, files)
            finally:
                self.cleanup_plan(plan)
        if plan["name"] == "book.convert.replace":
            try:
                self.require_unchanged_file(
                    plan["source"],
                    plan["sourceRevision"],
                    "The conversion source changed; start the conversion again",
                )
                self.require_unchanged_file(
                    plan["target"],
                    plan["targetRevision"],
                    "The existing format changed; review the replacement again",
                )
                self.require_unchanged_file(
                    plan["output"],
                    plan["outputRevision"],
                    "The staged conversion changed; start the conversion again",
                )
                self.run(
                    [
                        "calibredb",
                        "add_format",
                        "--with-library",
                        str(plan["library"]),
                        str(plan["bookId"]),
                        str(plan["output"]),
                    ],
                    commit=True,
                )
                book = self.get_book(plan["libraryToken"], plan["bookId"])
                attached = self.find_format(book, plan["outputFormat"])
                return {
                    "book": book,
                    "format": attached,
                    "inputFormat": plan["inputFormat"],
                    "outputFormat": plan["outputFormat"],
                    "appliedOptions": plan["options"],
                    "replaced": True,
                }
            finally:
                self.cleanup_plan(plan)
        raise BridgeError("confirmation_required", "The confirmation action is unavailable")

    def action_discard(self, input_data: Any) -> dict[str, bool]:
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Discard input must be an object")
        token = input_data.get("confirmationToken")
        if not isinstance(token, str) or not token:
            raise BridgeError("invalid_request", "A confirmation token is required")
        plan = self.pop_confirmation(token)
        if plan is None:
            return {"discarded": False}
        self.cleanup_plan(plan)
        return {"discarded": True}

    def pop_confirmation(self, token: str) -> dict[str, Any] | None:
        with self._state_lock:
            return self.confirmations.pop(token, None)

    def store_confirmation(self, plan: dict[str, Any]) -> str:
        try:
            self.begin_commit()
            self.prune_confirmations()
            token = secrets.token_urlsafe(24)
            with self._state_lock:
                self.confirmations[token] = plan
            return token
        except Exception:
            self.cleanup_plan(plan)
            raise

    def prune_confirmations(self) -> int:
        now = time.monotonic()
        expired: list[dict[str, Any]] = []
        with self._state_lock:
            for token, plan in list(self.confirmations.items()):
                if plan.get("expires", 0) < now:
                    expired.append(self.confirmations.pop(token))
        for plan in expired:
            self.cleanup_plan(plan)
        return len(expired)

    def close(self) -> None:
        with self._state_lock:
            plans = list(self.confirmations.values())
            self.confirmations.clear()
        for plan in plans:
            self.cleanup_plan(plan)

    def require_library(self, library_token: Any) -> Path:
        if not isinstance(library_token, str) or not library_token:
            raise BridgeError("invalid_request", "A library token is required")
        with self._state_lock:
            library = self.libraries.get(library_token)
        if library is None:
            raise BridgeError("library_unavailable", "The selected library is unavailable")
        return library

    def update_metadata(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id = input_data.get("bookId")
        if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id < 1:
            raise BridgeError("invalid_request", "bookId must be a positive integer")
        fields = input_data.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise BridgeError("invalid_request", "Metadata fields must be a non-empty object")

        command = ["calibredb", "set_metadata", "--with-library", str(library), str(book_id)]
        for field, value in fields.items():
            calibre_field = METADATA_FIELDS.get(field)
            if calibre_field is None:
                raise BridgeError("invalid_request", f"Unsupported metadata field: {field}")
            command.extend(["--field", f"{calibre_field}:{self.metadata_value(field, value)}"])
        self.run(command, commit=True)
        return {"book": self.get_book(library_token, book_id)}

    def set_cover(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id = self.require_book_id(input_data.get("bookId"))
        value = input_data.get("path")
        if not isinstance(value, str) or not value:
            raise BridgeError("invalid_request", "Cover path must be a non-empty string")
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in COVER_EXTENSIONS:
            raise BridgeError("invalid_request", "Cover must be a readable image file")
        size = path.stat().st_size
        if size < 1 or size > MAX_COVER_BYTES:
            raise BridgeError("invalid_request", "Cover must be between 1 byte and 50 MiB")

        self.run(
            [
                "calibredb",
                "set_metadata",
                "--with-library",
                str(library),
                str(book_id),
                "--field",
                f"cover:{path}",
            ],
            commit=True,
        )
        return {"book": self.get_book(library_token, book_id)}

    def import_books(self, library: Path, input_data: dict[str, Any]) -> dict[str, Any]:
        paths = input_data.get("paths")
        if not isinstance(paths, list) or not paths or len(paths) > 100:
            raise BridgeError("invalid_request", "Import requires between 1 and 100 paths")
        if input_data.get("duplicatePolicy", "calibre-default") != "calibre-default":
            raise BridgeError("invalid_request", "Unsupported duplicate policy")

        resolved_paths = []
        for value in paths:
            if not isinstance(value, str) or not value:
                raise BridgeError("invalid_request", "Import paths must be non-empty strings")
            path = Path(value).expanduser().resolve()
            if not path.exists():
                raise BridgeError("invalid_request", f"Import path does not exist: {path}")
            resolved_paths.append(path)

        before = self.library_book_ids(library)
        command = ["calibredb", "add", "--with-library", str(library)]
        if input_data.get("recursive") is True:
            command.append("--recurse")
        if input_data.get("oneBookPerDirectory") is True:
            command.append("--one-book-per-directory")
        command.extend(str(path) for path in resolved_paths)
        self.run(command, commit=True)
        after = self.library_book_ids(library)
        added = sorted(after - before)
        file_count = sum(1 for path in resolved_paths if path.is_file())
        return {
            "addedIds": added,
            "skipped": max(0, file_count - len(added)),
        }

    def add_format(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id = input_data.get("bookId")
        if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id < 1:
            raise BridgeError("invalid_request", "bookId must be a positive integer")
        raw_path = input_data.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise BridgeError("invalid_request", "Format path must be a non-empty string")
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise BridgeError("invalid_request", "Format path must identify a file")
        format_name = source.suffix.removeprefix(".").upper()
        if not format_name:
            raise BridgeError("invalid_request", "Format file must have an extension")

        book = self.get_book(library_token, book_id)
        if any(item["name"] == format_name for item in book["formats"]):
            raise BridgeError("confirmation_required", f"Replacing {format_name} requires confirmation")
        self.run(
            [
                "calibredb",
                "add_format",
                "--with-library",
                str(library),
                "--dont-replace",
                str(book_id),
                str(source),
            ],
            commit=True,
        )
        book = self.get_book(library_token, book_id)
        attached = next(
            (item for item in book["formats"] if item["name"] == format_name),
            None,
        )
        if attached is None:
            raise BridgeError("tool_failed", "Calibre did not attach the requested format")
        return {"book": book, "format": attached, "replaced": False}

    def prepare_format_replace(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id = self.require_book_id(input_data.get("bookId"))
        source = self.require_file(input_data.get("path"), "Replacement path")
        format_name = source.suffix.removeprefix(".").upper()
        if not format_name:
            raise BridgeError("invalid_request", "Replacement file must have an extension")
        book = self.get_book(library_token, book_id)
        target = self.find_format(book, format_name)
        confirmation_token = self.store_confirmation({
            "expires": time.monotonic() + 60,
            "name": "format.replace",
            "libraryToken": library_token,
            "library": library,
            "bookId": book_id,
            "format": format_name,
            "source": source,
            "sourceRevision": self.file_revision(source),
            "targetRevision": self.file_revision(Path(target["path"])),
        })
        return {
            "confirmationToken": confirmation_token,
            "expiresInSeconds": 60,
            "summary": f"Replace {format_name} for {book['title']}",
        }

    def prepare_format_remove(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id = self.require_book_id(input_data.get("bookId"))
        format_name = input_data.get("format")
        if (
            not isinstance(format_name, str)
            or not re.fullmatch(r"[A-Za-z0-9]{1,32}", format_name)
        ):
            raise BridgeError("invalid_request", "Format must be a file extension")
        format_name = format_name.upper()
        book = self.get_book(library_token, book_id)
        target = self.find_format(book, format_name)
        confirmation_token = self.store_confirmation({
            "expires": time.monotonic() + 60,
            "name": "format.remove",
            "libraryToken": library_token,
            "library": library,
            "bookId": book_id,
            "format": format_name,
            "targetRevision": self.file_revision(Path(target["path"])),
        })
        return {
            "confirmationToken": confirmation_token,
            "expiresInSeconds": 60,
            "summary": f"Remove {format_name} from {book['title']}",
        }

    @staticmethod
    def require_book_id(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise BridgeError("invalid_request", "bookId must be a positive integer")
        return value

    @staticmethod
    def require_file(value: Any, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise BridgeError("invalid_request", f"{label} must be a non-empty string")
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise BridgeError("invalid_request", f"{label} must identify a file")
        return path

    @staticmethod
    def require_device_destination(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise BridgeError("invalid_request", "Device destination must be a valid device path")
        if "\x00" in value or "\\" in value or "\n" in value or "\r" in value:
            raise BridgeError("invalid_request", "Device destination must be a valid device path")
        path = value[4:] if value.startswith("dev:") else value
        if not (path == "/" or path.startswith("/") or path.startswith("carda:/") or path.startswith("cardb:/")):
            raise BridgeError(
                "invalid_request",
                "Device destination must begin with /, carda:/, or cardb:/",
            )
        if path == "/" or path.endswith("/"):
            raise BridgeError("invalid_request", "Device destination must identify a file")
        if any(part in {".", ".."} for part in path.split("/")) or "//" in path:
            raise BridgeError("invalid_request", "Device destination cannot contain dot segments or empty path segments")
        return path

    def default_device_destination(
        self,
        book: dict[str, Any],
        format_name: str,
    ) -> str:
        folder = "/"
        try:
            listing = self.device_adapter.list("/")
        except DeviceError as error:
            if error.code != "unsupported":
                raise
        else:
            entries = listing.get("entries", []) if isinstance(listing, dict) else []
            if isinstance(entries, list):
                for preferred in DEVICE_BOOK_FOLDERS:
                    match = next(
                        (
                            entry
                            for entry in entries
                            if isinstance(entry, dict)
                            and entry.get("isDirectory") is True
                            and str(entry.get("name", "")).casefold() == preferred
                            and self.safe_device_folder(entry.get("path")) is not None
                        ),
                        None,
                    )
                    if match is not None:
                        folder = self.safe_device_folder(match.get("path")) or "/"
                        break

        title = self.safe_device_filename_part(book.get("title"), "Untitled")
        raw_authors = book.get("authors", [])
        if isinstance(raw_authors, str):
            raw_authors = [raw_authors]
        if not isinstance(raw_authors, list):
            raw_authors = []
        author_text = " & ".join(str(author) for author in raw_authors if str(author).strip())
        authors = self.safe_device_filename_part(author_text, "")
        stem = f"{title} - {authors}" if authors else title
        extension = "." + format_name.lower()
        budget = MAX_DEVICE_FILENAME_BYTES - len(extension.encode("utf-8"))
        encoded_stem = stem.encode("utf-8")[:budget]
        stem = encoded_stem.decode("utf-8", errors="ignore").rstrip(" .") or "Untitled"
        filename = stem + extension
        destination = (folder.rstrip("/") + "/" + filename) if folder != "/" else "/" + filename
        return self.require_device_destination(destination)

    @staticmethod
    def device_parent_path(destination: str) -> str:
        if destination.startswith(("carda:/", "cardb:/")):
            prefix, relative = destination.split(":/", 1)
            parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
            return f"{prefix}:/{parent}" if parent else f"{prefix}:/"
        parent = destination.rsplit("/", 1)[0]
        return parent or "/"

    def device_listing_revision(self, destination: str) -> tuple[str, int, str, str] | None:
        path = self.require_device_destination(destination)
        listing = self.device_adapter.list(self.device_parent_path(path))
        entries = listing.get("entries", []) if isinstance(listing, dict) else []
        if not isinstance(entries, list):
            raise BridgeError("tool_failed", "The ebook reader returned an invalid file listing")
        files = [
            item
            for item in entries
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and item.get("isDirectory") is False
        ]
        matches = [item for item in files if item["path"] == path]
        if not matches:
            matches = [item for item in files if item["path"].casefold() == path.casefold()]
        if not matches:
            return None
        if len(matches) != 1:
            raise BridgeError("tool_failed", "The ebook reader returned an ambiguous file listing")
        entry = matches[0]
        canonical_path = self.require_device_destination(entry["path"])
        size = entry.get("size")
        modified = entry.get("modified")
        mode = entry.get("mode")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BridgeError("tool_failed", "The ebook reader returned an invalid file listing")
        if not isinstance(modified, str) or not isinstance(mode, str):
            raise BridgeError("tool_failed", "The ebook reader returned an invalid file listing")
        return canonical_path, size, modified, mode

    def device_file_revision(self, destination: str) -> tuple[str, int, str, str, str] | None:
        before = self.device_listing_revision(destination)
        if before is None:
            return None
        with tempfile.TemporaryDirectory(prefix="omarchy-calibre-reader-snapshot-") as temporary:
            snapshot = Path(temporary) / "reader-copy"
            self.device_adapter.receive(before[0], snapshot)
            try:
                snapshot_size = snapshot.stat().st_size
                with snapshot.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as error:
                raise BridgeError("tool_failed", "The reader copy could not be verified") from error
        after = self.device_listing_revision(before[0])
        if after != before or snapshot_size != before[1]:
            raise BridgeError(
                "confirmation_stale",
                "The reader copy changed; send the book again before replacing it",
                retryable=True,
            )
        return (*before, digest)

    @staticmethod
    def safe_device_folder(value: Any) -> str | None:
        if not isinstance(value, str) or not value.startswith("/"):
            return None
        if "\\" in value or "\x00" in value or "\n" in value or "\r" in value or "//" in value:
            return None
        if any(part in {".", ".."} for part in value.split("/")):
            return None
        return value.rstrip("/") or "/"

    @staticmethod
    def safe_device_filename_part(value: Any, fallback: str) -> str:
        raw = str(value or "")
        cleaned = "".join(
            " " if character in '<>:"/\\|?*' or ord(character) < 32 or ord(character) == 127 else character
            for character in raw
        )
        return " ".join(cleaned.split()).strip(" .") or fallback

    @staticmethod
    def staged_device_format(staging: Path, format_name: str) -> Path:
        root = staging.resolve()
        matches: list[Path] = []
        for candidate in staging.rglob("*"):
            if not candidate.is_file() or candidate.suffix.upper() != f".{format_name}":
                continue
            try:
                resolved = candidate.resolve(strict=True)
                size = resolved.stat().st_size
            except (OSError, RuntimeError) as error:
                raise BridgeError("tool_failed", "Calibre exported an unreadable book format") from error
            if not resolved.is_relative_to(root):
                raise BridgeError("tool_failed", "Calibre exported a format outside the staging directory")
            if size < 1:
                raise BridgeError("tool_failed", "Calibre exported an empty book format")
            matches.append(resolved)
        if not matches:
            raise BridgeError("tool_failed", f"Calibre did not export the requested {format_name} format")
        if len(matches) > 1:
            raise BridgeError("tool_failed", f"Calibre exported multiple {format_name} formats")
        return matches[0]

    @staticmethod
    def find_format(book: dict[str, Any], format_name: str) -> dict[str, Any]:
        found = next((item for item in book["formats"] if item["name"] == format_name), None)
        if found is None:
            raise BridgeError("format_not_found", f"{format_name} is not attached to this book")
        return found

    @classmethod
    def file_revision(cls, path: Path) -> FileRevision:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError as error:
            raise BridgeError("file_unavailable", "A selected format is unavailable") from error
        try:
            return cls.file_revision_descriptor(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def file_revision_descriptor(descriptor: int) -> FileRevision:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BridgeError("file_unavailable", "A selected format is unavailable")
        digest = hashlib.sha256()
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise BridgeError("file_unavailable", "A selected format changed while Calibre read it")
        return (*after_identity, digest.hexdigest())

    @classmethod
    def require_unchanged_file(
        cls,
        path: Path,
        expected: FileRevision,
        message: str,
    ) -> None:
        if cls.file_revision(path) != expected:
            raise BridgeError("confirmation_stale", message)

    def export_books(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_ids, destination = self.export_request(library_token, input_data)
        staging = self.stage_export(library, book_ids)
        try:
            self.begin_commit()
            files = self.publish_staged_export(staging, destination, replace=False)
            return self.export_result(destination, files)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def quick_convert(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id, input_format, output_format, source, _, raw_options = self.conversion_request(
            library_token,
            input_data,
            replacing=False,
        )
        staging, output = self.stage_conversion(source, output_format, raw_options)
        try:
            self.run(
                [
                    "calibredb",
                    "add_format",
                    "--with-library",
                    str(library),
                    "--dont-replace",
                    str(book_id),
                    str(output),
                ],
                commit=True,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        book = self.get_book(library_token, book_id)
        attached = self.find_format(book, output_format)
        return {
            "book": book,
            "format": attached,
            "inputFormat": input_format,
            "outputFormat": output_format,
            "appliedOptions": dict(raw_options),
            "replaced": False,
        }

    def prepare_conversion_replace(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_id, input_format, output_format, source, target, options = self.conversion_request(
            library_token,
            input_data,
            replacing=True,
        )
        assert target is not None
        staging, output = self.stage_conversion(source, output_format, options)
        confirmation_token = self.store_confirmation({
            "expires": time.monotonic() + 60,
            "name": "book.convert.replace",
            "libraryToken": library_token,
            "library": library,
            "bookId": book_id,
            "inputFormat": input_format,
            "outputFormat": output_format,
            "options": dict(options),
            "source": source,
            "sourceRevision": self.file_revision(source),
            "target": Path(target["path"]),
            "targetRevision": self.file_revision(Path(target["path"])),
            "staging": staging,
            "output": output,
            "outputRevision": self.file_revision(output),
        })
        return {
            "confirmationToken": confirmation_token,
            "expiresInSeconds": 60,
            "summary": f"Replace {output_format} with a conversion from {input_format}",
        }

    def conversion_request(
        self,
        library_token: str,
        input_data: dict[str, Any],
        *,
        replacing: bool,
    ) -> tuple[int, str, str, Path, dict[str, Any] | None, dict[str, Any]]:
        book_id = self.require_book_id(input_data.get("bookId"))
        output_format = self.require_format_name(input_data.get("outputFormat"), "outputFormat")
        conversion = self.conversion_capabilities()
        if output_format not in conversion["outputFormats"]:
            raise BridgeError("unsupported_format", f"Calibre cannot convert to {output_format}")

        book = self.get_book(library_token, book_id)
        target = next((item for item in book["formats"] if item["name"] == output_format), None)
        if replacing and target is None:
            raise BridgeError("format_not_found", f"{output_format} is not attached to this book")
        if not replacing and target is not None:
            raise BridgeError("confirmation_required", f"Replacing {output_format} requires confirmation")
        available = {
            item["name"]: item
            for item in book["formats"]
            if item["name"] in conversion["inputFormats"] and item["name"] != output_format
        }
        requested_input = input_data.get("inputFormat")
        if requested_input is not None:
            input_format = self.require_format_name(requested_input, "inputFormat")
            if input_format not in available:
                raise BridgeError("unsupported_format", f"{input_format} is not a usable input format")
        else:
            input_format = next(
                (name for name in conversion["preferredInputOrder"] if name in available),
                next(iter(available), ""),
            )
        if not input_format:
            raise BridgeError("no_input_format", "This book has no format that Calibre can convert")
        options = input_data.get("options", {})
        if not isinstance(options, dict) or len(options) > 64:
            raise BridgeError("invalid_request", "Conversion options must be an object with at most 64 fields")
        return book_id, input_format, output_format, Path(available[input_format]["path"]), target, options

    def stage_conversion(
        self,
        source: Path,
        output_format: str,
        options: dict[str, Any],
    ) -> tuple[Path, Path]:
        staging = Path(tempfile.mkdtemp(prefix="omarchy-calibre-convert-"))
        output = staging / f"converted.{output_format.lower()}"
        try:
            arguments: list[str] = []
            if options:
                descriptors = self.load_conversion_descriptors(source, output)
                arguments = self.conversion_arguments(options, descriptors)
            self.run(["ebook-convert", str(source), str(output), *arguments], timeout=3600)
            if not output.is_file() or output.stat().st_size == 0:
                raise BridgeError("conversion_failed", "Calibre did not produce a converted book")
            return staging, output
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def conversion_capabilities(self) -> dict[str, Any]:
        with self._state_lock:
            if self._conversion_capabilities is not None:
                return self._conversion_capabilities
        fallback = {
            "inputFormats": [
                "EPUB", "AZW3", "MOBI", "LIT", "PRC", "FB2", "HTML", "HTM",
                "XHTM", "SHTML", "XHTML", "ZIP", "DOCX", "ODT", "RTF", "PDF", "TXT",
            ],
            "outputFormats": ["AZW3", "DOCX", "EPUB", "FB2", "HTMLZ", "MOBI", "PDF", "RTF", "TXT"],
            "preferredInputOrder": [
                "EPUB", "AZW3", "MOBI", "LIT", "PRC", "FB2", "HTML", "HTM",
                "XHTM", "SHTML", "XHTML", "ZIP", "DOCX", "ODT", "RTF", "PDF", "TXT",
            ],
            "defaultOutputFormat": "EPUB",
            "source": "fallback",
        }
        executable = shutil.which("calibre-debug")
        if executable is None:
            with self._state_lock:
                self._conversion_capabilities = fallback
            return fallback
        code = (
            "import json; "
            "from calibre.customize.ui import available_input_formats, available_output_formats; "
            "from calibre.utils.config import prefs; "
            "print(json.dumps({'inputFormats': sorted(x.upper() for x in available_input_formats()), "
            "'outputFormats': sorted(x.upper() for x in available_output_formats() if x.lower() != 'oeb'), "
            "'preferredInputOrder': list(prefs['input_format_order']), "
            "'defaultOutputFormat': str(prefs['output_format']).upper(), 'source': 'calibre-runtime'}))"
        )
        try:
            completed = self.run([executable, "-c", code], timeout=30)
            discovered = json.loads(completed.stdout)
            required = {
                "inputFormats",
                "outputFormats",
                "preferredInputOrder",
                "defaultOutputFormat",
                "source",
            }
            if not isinstance(discovered, dict) or not required.issubset(discovered):
                raise ValueError("missing conversion capability fields")
            if not all(isinstance(discovered[key], list) for key in required - {"defaultOutputFormat", "source"}):
                raise ValueError("invalid conversion capability lists")
            result = discovered
        except (BridgeError, json.JSONDecodeError, TypeError, ValueError):
            result = fallback
        with self._state_lock:
            if self._conversion_capabilities is None:
                self._conversion_capabilities = result
            return self._conversion_capabilities

    def describe_conversion(self, library_token: Any, input_data: Any) -> dict[str, Any]:
        self.require_library(library_token)
        if not isinstance(input_data, dict):
            raise BridgeError("invalid_request", "Conversion input must be an object")
        book_id = self.require_book_id(input_data.get("bookId"))
        input_format = self.require_format_name(input_data.get("inputFormat"), "inputFormat")
        output_format = self.require_format_name(input_data.get("outputFormat"), "outputFormat")
        capabilities = self.conversion_capabilities()
        if input_format not in capabilities["inputFormats"]:
            raise BridgeError("unsupported_format", f"Calibre cannot read {input_format}")
        if output_format not in capabilities["outputFormats"]:
            raise BridgeError("unsupported_format", f"Calibre cannot write {output_format}")
        book = self.get_book(str(library_token), book_id)
        source = self.find_format(book, input_format)

        with tempfile.TemporaryDirectory(prefix="omarchy-calibre-options-") as temporary:
            output = Path(temporary) / f"output.{output_format.lower()}"
            result = self.load_conversion_descriptors(Path(source["path"]), output)
        result["inputFormat"] = input_format
        result["outputFormat"] = output_format
        return result

    def load_conversion_descriptors(self, source: Path, output: Path) -> dict[str, Any]:
        executable = shutil.which("calibre-debug")
        if executable is None:
            raise BridgeError("capability_unavailable", "Advanced conversion options are unavailable")
        runtime = Path(__file__).with_name("calibre_runtime.py")
        completed = self.run(
            [
                executable,
                str(runtime),
                "conversion-options",
                str(source),
                str(output),
            ],
            timeout=30,
        )
        try:
            raw = completed.stdout.strip().splitlines()[-1]
            result = json.loads(raw)
        except (IndexError, json.JSONDecodeError) as error:
            raise BridgeError("capability_unavailable", "Calibre returned invalid conversion options") from error
        if not isinstance(result, dict) or not isinstance(result.get("groups"), list):
            raise BridgeError("capability_unavailable", "Calibre returned invalid conversion options")
        return result

    @staticmethod
    def conversion_arguments(options: dict[str, Any], descriptors: dict[str, Any]) -> list[str]:
        catalog: dict[str, dict[str, Any]] = {}
        for group in descriptors["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("options"), list):
                raise BridgeError("capability_unavailable", "Calibre returned invalid conversion options")
            for descriptor in group["options"]:
                if isinstance(descriptor, dict) and isinstance(descriptor.get("name"), str):
                    catalog[descriptor["name"]] = descriptor

        arguments = []
        for name, value in options.items():
            if not isinstance(name, str) or name not in catalog:
                raise BridgeError("invalid_request", f"Unsupported conversion option: {name}")
            descriptor = catalog[name]
            flag = descriptor.get("flag")
            if not isinstance(flag, str) or not re.fullmatch(r"--[a-z0-9-]+", flag):
                raise BridgeError("capability_unavailable", "Calibre returned an invalid conversion flag")
            value_type = descriptor.get("type")
            default = descriptor.get("default")
            if value_type == "boolean":
                if not isinstance(value, bool):
                    raise BridgeError("invalid_request", f"{name} must be true or false")
                if value != default:
                    arguments.append(flag)
                continue
            if value_type == "choice":
                choices = descriptor.get("choices", [])
                if value not in choices:
                    raise BridgeError("invalid_request", f"{name} must be one of Calibre's available choices")
            elif value_type == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise BridgeError("invalid_request", f"{name} must be an integer")
            elif value_type == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise BridgeError("invalid_request", f"{name} must be a number")
            elif value_type == "string":
                if not isinstance(value, str) or len(value) > 100_000 or "\x00" in value:
                    raise BridgeError("invalid_request", f"{name} must be a valid string")
            else:
                raise BridgeError("capability_unavailable", "Calibre returned an unknown conversion option type")
            if value != default:
                arguments.extend([flag, str(value)])
        return arguments

    @staticmethod
    def require_format_name(value: Any, label: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,32}", value):
            raise BridgeError("invalid_request", f"{label} must be a file extension")
        return value.upper()

    def prepare_export_replace(
        self,
        library_token: str,
        library: Path,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        book_ids, destination = self.export_request(library_token, input_data)
        staging = self.stage_export(library, book_ids)
        try:
            staged_files = sorted(path for path in staging.rglob("*") if path.is_file())
            relative_files = [path.relative_to(staging) for path in staged_files]
            collisions = [
                relative
                for relative in relative_files
                if (destination / relative).exists() or (destination / relative).is_symlink()
            ]
            if not collisions:
                raise BridgeError("confirmation_not_needed", "The export does not replace any files")
            if any(
                (destination / relative).is_symlink()
                or not (destination / relative).is_file()
                for relative in collisions
            ):
                raise BridgeError("invalid_request", "Export cannot replace a directory")

            confirmation_token = self.store_confirmation({
                "expires": time.monotonic() + 60,
                "name": "book.export.replace",
                "libraryToken": library_token,
                "staging": staging,
                "destination": destination,
                "stagedRevisions": {
                    relative: self.file_revision(staging / relative) for relative in relative_files
                },
                "targetRevisions": {
                    relative: (
                        self.file_revision(destination / relative)
                        if relative in collisions
                        else None
                    )
                    for relative in relative_files
                },
            })
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return {
            "confirmationToken": confirmation_token,
            "expiresInSeconds": 60,
            "summary": f"Replace {len(collisions)} existing export file{'s' if len(collisions) != 1 else ''}",
        }

    def export_request(
        self,
        library_token: str,
        input_data: dict[str, Any],
    ) -> tuple[list[int], Path]:
        raw_ids = input_data.get("bookIds")
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or len(raw_ids) > 100
            or not all(isinstance(book_id, int) and not isinstance(book_id, bool) and book_id > 0 for book_id in raw_ids)
        ):
            raise BridgeError("invalid_request", "bookIds must contain between 1 and 100 positive integers")
        book_ids = list(dict.fromkeys(raw_ids))
        for book_id in book_ids:
            self.get_book(library_token, book_id)

        raw_destination = input_data.get("destination")
        if not isinstance(raw_destination, str) or not raw_destination:
            raise BridgeError("invalid_request", "Export destination must be a non-empty string")
        destination = self.canonical_export_destination(raw_destination)
        if destination is None:
            raise BridgeError("invalid_request", "Export destination must be a valid path")
        if destination.exists() and not destination.is_dir():
            raise BridgeError("invalid_request", "Export destination must be a directory")
        parent = destination.parent
        if not parent.is_dir():
            raise BridgeError("invalid_request", "Export destination parent does not exist")
        return book_ids, destination

    def stage_export(self, library: Path, book_ids: list[int]) -> Path:
        staging = Path(tempfile.mkdtemp(prefix="omarchy-calibre-export-"))
        try:
            self.run(
                [
                    "calibredb",
                    "export",
                    "--with-library",
                    str(library),
                    "--to-dir",
                    str(staging),
                    ",".join(str(book_id) for book_id in book_ids),
                ]
            )
            if not any(path.is_file() for path in staging.rglob("*")):
                raise BridgeError("tool_failed", "Calibre did not export any files")
            return staging
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @classmethod
    def publish_staged_export(
        cls,
        staging: Path,
        destination: Path,
        *,
        replace: bool,
        target_revisions: dict[Path, FileRevision | None] | None = None,
    ) -> list[Path]:
        staging_root = staging.resolve()
        staged_files = sorted(path for path in staging.rglob("*") if path.is_file())
        relative_files: list[Path] = []
        for source in staged_files:
            if source.is_symlink() or not source.resolve().is_relative_to(staging_root):
                raise BridgeError("tool_failed", "Calibre returned an unsafe staged export")
            relative = source.relative_to(staging)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise BridgeError("tool_failed", "Calibre returned an unsafe staged export path")
            relative_files.append(relative)

        if replace:
            if target_revisions is None or set(target_revisions) != set(relative_files):
                raise BridgeError("confirmation_stale", "The confirmed export file set changed")
        elif target_revisions is not None:
            raise BridgeError("invalid_request", "Unexpected export target revisions")
        elif any(
            (destination / relative).exists() or (destination / relative).is_symlink()
            for relative in relative_files
        ):
            raise BridgeError("confirmation_required", "Export would replace existing files")

        destination.mkdir(parents=False, exist_ok=True)
        try:
            destination_stat = destination.lstat()
        except OSError as error:
            raise BridgeError("invalid_request", "Export destination is unavailable") from error
        if destination.is_symlink() or not stat.S_ISDIR(destination_stat.st_mode):
            raise BridgeError("invalid_request", "Export destination must be a directory")

        published: list[Path] = []
        for source, relative in zip(staged_files, relative_files, strict=True):
            expected = target_revisions[relative] if target_revisions is not None else None
            parent_fd = cls.open_export_parent(destination, relative.parent)
            target_name = relative.name
            try:
                try:
                    if expected is None:
                        cls.publish_new_export_file(
                            source,
                            parent_fd,
                            target_name,
                            stale=replace,
                        )
                    else:
                        cls.replace_confirmed_export_file(source, parent_fd, target_name, expected)
                except BridgeError:
                    raise
                except OSError as error:
                    raise BridgeError(
                        "tool_failed",
                        "Calibre could not publish the exported files",
                    ) from error
            finally:
                os.close(parent_fd)
            published.append(destination / relative)
        return published

    @staticmethod
    def open_export_parent(destination: Path, relative_parent: Path) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            current_fd = os.open(destination, flags)
            for part in relative_parent.parts:
                if part in {"", "."}:
                    continue
                if part == "..":
                    raise OSError("unsafe export parent")
                try:
                    os.mkdir(part, mode=0o755, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except OSError as error:
            if "current_fd" in locals():
                os.close(current_fd)
            raise BridgeError(
                "invalid_request",
                "Export destination contains an unsafe directory",
            ) from error

    @staticmethod
    def copy_export_contents(source: Path, destination_fd: int) -> None:
        with source.open("rb") as source_file, os.fdopen(os.dup(destination_fd), "wb") as destination_file:
            shutil.copyfileobj(source_file, destination_file)
            destination_file.flush()
            os.fsync(destination_file.fileno())

    @classmethod
    def export_file_revision_at(cls, parent_fd: int, name: str) -> FileRevision:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            return cls.file_revision_descriptor(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def renameat2(parent_fd: int, old_name: str, new_name: str, flags: int) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise OSError(errno.ENOSYS, "renameat2 is unavailable")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(
            parent_fd,
            os.fsencode(old_name),
            parent_fd,
            os.fsencode(new_name),
            flags,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), new_name)

    @staticmethod
    def atomic_rename_noreplace(parent_fd: int, temporary_name: str, target_name: str) -> None:
        CalibreBridge.renameat2(parent_fd, temporary_name, target_name, 1)

    @staticmethod
    def atomic_exchange_export_file(parent_fd: int, temporary_name: str, target_name: str) -> None:
        CalibreBridge.renameat2(parent_fd, temporary_name, target_name, 2)

    @classmethod
    def create_export_temporary(
        cls,
        source: Path,
        parent_fd: int,
        target_name: str,
    ) -> tuple[str, FileRevision]:
        temporary_name = f".{target_name}.omarchy-{secrets.token_hex(6)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            temporary_name,
            flags,
            stat.S_IMODE(source.stat().st_mode) or 0o600,
            dir_fd=parent_fd,
        )
        try:
            cls.copy_export_contents(source, descriptor)
        except BaseException:
            os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise
        os.close(descriptor)
        try:
            return temporary_name, cls.export_file_revision_at(parent_fd, temporary_name)
        except BaseException:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def publish_new_export_file(
        cls,
        source: Path,
        parent_fd: int,
        target_name: str,
        *,
        stale: bool,
    ) -> None:
        temporary_name, _ = cls.create_export_temporary(source, parent_fd, target_name)
        try:
            cls.atomic_rename_noreplace(parent_fd, temporary_name, target_name)
        except FileExistsError as error:
            if stale:
                raise BridgeError(
                    "confirmation_stale",
                    "A new export collision appeared; review the replacement again",
                ) from error
            raise BridgeError("confirmation_required", "Export would replace existing files") from error
        except OSError as error:
            if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                if stale:
                    raise BridgeError(
                        "confirmation_stale",
                        "A new export collision appeared; review the replacement again",
                    ) from error
                raise BridgeError("confirmation_required", "Export would replace existing files") from error
            raise BridgeError(
                "tool_failed",
                "The destination does not support safe atomic export",
            ) from error
        finally:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    @classmethod
    def replace_confirmed_export_file(
        cls,
        source: Path,
        parent_fd: int,
        target_name: str,
        expected: FileRevision,
    ) -> None:
        temporary_name, staged_revision = cls.create_export_temporary(source, parent_fd, target_name)
        preserve_temporary = False
        try:
            try:
                current = cls.export_file_revision_at(parent_fd, target_name)
            except (BridgeError, OSError) as error:
                raise BridgeError(
                    "confirmation_stale",
                    "An exported file changed; review the replacement again",
                ) from error
            if current != expected:
                raise BridgeError(
                    "confirmation_stale",
                    "An exported file changed; review the replacement again",
                )

            try:
                cls.atomic_exchange_export_file(parent_fd, temporary_name, target_name)
            except OSError as error:
                if error.errno in {errno.ENOENT, errno.EEXIST, errno.ENOTEMPTY}:
                    raise BridgeError(
                        "confirmation_stale",
                        "An exported file changed; review the replacement again",
                    ) from error
                raise BridgeError(
                    "tool_failed",
                    "The destination does not support safe atomic replacement",
                ) from error

            try:
                displaced_revision = cls.export_file_revision_at(parent_fd, temporary_name)
            except (BridgeError, OSError):
                displaced_revision = None
            if displaced_revision != expected:
                try:
                    cls.atomic_exchange_export_file(parent_fd, temporary_name, target_name)
                except OSError as error:
                    preserve_temporary = True
                    raise BridgeError(
                        "tool_failed",
                        "An export changed during publication; a recovery file was preserved",
                    ) from error
                try:
                    rolled_back_revision = cls.export_file_revision_at(parent_fd, temporary_name)
                except (BridgeError, OSError):
                    preserve_temporary = True
                else:
                    if rolled_back_revision != staged_revision:
                        recovery_name = f".{target_name}.omarchy-recovery-{secrets.token_hex(6)}"
                        try:
                            os.rename(
                                temporary_name,
                                recovery_name,
                                src_dir_fd=parent_fd,
                                dst_dir_fd=parent_fd,
                            )
                        except OSError:
                            preserve_temporary = True
                        else:
                            temporary_name = recovery_name
                            preserve_temporary = True
                raise BridgeError(
                    "confirmation_stale",
                    "An exported file changed; review the replacement again",
                )

            os.unlink(temporary_name, dir_fd=parent_fd)
            temporary_name = ""
        finally:
            if temporary_name and not preserve_temporary:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass

    @staticmethod
    def export_result(destination: Path, files: list[Path]) -> dict[str, Any]:
        return {
            "destination": str(destination),
            "files": [{"path": str(path), "size": path.stat().st_size} for path in files],
        }

    @staticmethod
    def cleanup_plan(plan: dict[str, Any]) -> None:
        staging = plan.get("staging")
        if isinstance(staging, Path):
            shutil.rmtree(staging, ignore_errors=True)

    def library_book_ids(self, library: Path) -> set[int]:
        completed = self.run(
            [
                "calibredb",
                "list",
                "--with-library",
                str(library),
                "--for-machine",
                "--fields",
                "title",
            ]
        )
        try:
            rows = json.loads(completed.stdout)
            return {int(row["id"]) for row in rows if isinstance(row, dict) and "id" in row}
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise BridgeError("tool_failed", "Calibre returned invalid book identifiers") from error

    @staticmethod
    def metadata_value(field: str, value: Any) -> str:
        if field in {"authors", "tags", "languages"}:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise BridgeError("invalid_request", f"{field} must be an array of strings")
            separator = " & " if field == "authors" else ","
            return separator.join(value)
        if field == "identifiers":
            if not isinstance(value, dict) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items()
            ):
                raise BridgeError("invalid_request", "identifiers must be an object of strings")
            return ",".join(f"{key}:{item}" for key, item in value.items())
        if field == "rating":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 5:
                raise BridgeError("invalid_request", "rating must be between 0 and 5")
            return str(value)
        if field == "seriesIndex":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise BridgeError("invalid_request", "seriesIndex must be zero or greater")
            return str(value)
        if not isinstance(value, str):
            raise BridgeError("invalid_request", f"{field} must be a string")
        if len(value) > 100_000:
            raise BridgeError("invalid_request", f"{field} is too large")
        return value

    def get_book(self, library_token: str, book_id: int) -> dict[str, Any]:
        page = self.query_books(
            library_token,
            {"search": f"id:{book_id}", "sort": "id", "limit": 2},
        )
        for book in page["items"]:
            if book["id"] == book_id:
                return book
        raise BridgeError("book_not_found", f"Book {book_id} was not found")

    def query_books(self, library_token: str, input_data: dict[str, Any]) -> dict[str, Any]:
        library = self.require_library(library_token)

        search = input_data.get("search", "")
        if not isinstance(search, str) or len(search) > 4096:
            raise BridgeError("invalid_request", "Search must be a string of at most 4096 characters")
        sort = input_data.get("sort", "id")
        if sort not in SORT_FIELDS:
            raise BridgeError("invalid_request", "Unsupported sort field")
        direction = input_data.get("direction", "ascending")
        if direction not in {"ascending", "descending"}:
            raise BridgeError("invalid_request", "Sort direction must be ascending or descending")
        limit = input_data.get("limit", 50)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise BridgeError("invalid_request", "Query limit must be between 1 and 200")
        cursor = input_data.get("cursor")
        try:
            offset = 0 if cursor in {None, ""} else int(cursor)
        except (TypeError, ValueError) as error:
            raise BridgeError("invalid_request", "Query cursor is invalid") from error
        if offset < 0:
            raise BridgeError("invalid_request", "Query cursor is invalid")

        index_command = [
            "calibredb",
            "list",
            "--with-library",
            str(library),
            "--for-machine",
            "--fields",
            "id",
            "--sort-by",
            sort,
        ]
        if direction == "ascending":
            index_command.append("--ascending")
        if search:
            index_command.extend(["--search", search])
        completed = self.run(index_command)
        try:
            index_rows = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise BridgeError("tool_failed", "Calibre returned invalid book data") from error
        if not isinstance(index_rows, list):
            raise BridgeError("tool_failed", "Calibre returned an invalid book list")

        book_ids: list[int] = []
        for row in index_rows:
            book_id = row.get("id") if isinstance(row, dict) else None
            if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id < 1:
                raise BridgeError("tool_failed", "Calibre returned an invalid book index")
            book_ids.append(book_id)

        page_ids = book_ids[offset : offset + limit]
        if page_ids:
            id_filter = " or ".join(f"id:{book_id}" for book_id in page_ids)
            detail_search = f"({search}) and ({id_filter})" if search else id_filter
            detail_command = [
                "calibredb",
                "list",
                "--with-library",
                str(library),
                "--for-machine",
                "--fields",
                BOOK_FIELDS,
                "--sort-by",
                sort,
                "--limit",
                str(len(page_ids)),
                "--search",
                detail_search,
            ]
            if direction == "ascending":
                detail_command.append("--ascending")
            completed = self.run(detail_command)
            try:
                detail_rows = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise BridgeError("tool_failed", "Calibre returned invalid book data") from error
            if not isinstance(detail_rows, list):
                raise BridgeError("tool_failed", "Calibre returned an invalid book list")
            rows_by_id = {
                row.get("id"): row
                for row in detail_rows
                if isinstance(row, dict) and row.get("id") in page_ids
            }
            if any(book_id not in rows_by_id for book_id in page_ids):
                raise BridgeError("tool_failed", "Calibre returned an incomplete book page")
            items = [self.normalize_book(rows_by_id[book_id]) for book_id in page_ids]
        else:
            items = []

        next_offset = offset + len(page_ids)
        next_cursor = str(next_offset) if next_offset < len(book_ids) else None
        return {"items": items, "total": len(book_ids), "nextCursor": next_cursor}

    @staticmethod
    def normalize_book(row: Any) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise BridgeError("tool_failed", "Calibre returned an invalid book record")
        authors = row.get("authors", [])
        if isinstance(authors, str):
            authors = [part.strip() for part in authors.split(" & ") if part.strip()]
        elif not isinstance(authors, list):
            authors = []
        tags = row.get("tags", [])
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(",") if part.strip()]
        elif not isinstance(tags, list):
            tags = []
        raw_formats = row.get("formats", [])
        formats = []
        if isinstance(raw_formats, list):
            for value in raw_formats:
                if not isinstance(value, str) or not value:
                    continue
                path = Path(value)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                formats.append(
                    {
                        "name": path.suffix.removeprefix(".").upper(),
                        "path": value,
                        "size": size,
                    }
                )
        published = str(row.get("pubdate", "") or "")
        if published.startswith("0101-"):
            published = ""
        return {
            "id": int(row.get("id", 0)),
            "title": str(row.get("title", "") or ""),
            "authors": authors,
            "authorSort": str(row.get("author_sort", "") or ""),
            "series": str(row.get("series", "") or ""),
            "seriesIndex": float(row.get("series_index", 1.0) or 1.0),
            "rating": float(row.get("rating", 0) or 0) / 2,
            "tags": tags,
            "publisher": str(row.get("publisher", "") or ""),
            "published": published,
            "languages": row.get("languages", []) if isinstance(row.get("languages", []), list) else [],
            "identifiers": row.get("identifiers", {}) if isinstance(row.get("identifiers", {}), dict) else {},
            "comments": str(row.get("comments", "") or ""),
            "formats": formats,
            "cover": str(row.get("cover", "") or ""),
            "modified": str(row.get("last_modified", "") or ""),
        }

    def begin_commit(self) -> None:
        context = getattr(self._operation_local, "context", None)
        if context is None:
            return
        if context.begin_commit():
            return
        context.check_cancelled()
        raise BridgeError("operation_finished", "The Calibre operation is no longer active")

    def run(
        self,
        command: list[str],
        *,
        timeout: float = 120,
        context: OperationContext | None = None,
        cwd: Path | None = None,
        commit: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        active_context = context or getattr(self._operation_local, "context", None)
        if active_context is not None:
            active_context.check_cancelled()
        lock_file = self.acquire_calibredb_lock(active_context) if Path(command[0]).name == "calibredb" else None
        try:
            return self.run_command(
                command,
                timeout=timeout,
                active_context=active_context,
                cwd=cwd,
                commit=commit,
                check=check,
            )
        finally:
            if lock_file is not None:
                try:
                    import fcntl

                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                finally:
                    os.close(lock_file)

    @staticmethod
    def acquire_calibredb_lock(context: OperationContext | None) -> int:
        import fcntl

        runtime_value = os.environ.get("XDG_RUNTIME_DIR", "")
        runtime = Path(runtime_value) if runtime_value else Path(tempfile.gettempdir()) / f"omarchy-calibre-{os.getuid()}"
        try:
            if not runtime_value:
                runtime.mkdir(mode=0o700, exist_ok=True)
            runtime_stat = runtime.lstat()
            if not stat.S_ISDIR(runtime_stat.st_mode) or runtime_stat.st_uid != os.getuid():
                raise OSError("unsafe runtime directory")
            flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            lock_file = os.open(runtime / "omarchy-calibre-calibredb.lock", flags, 0o600)
            lock_stat = os.fstat(lock_file)
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.getuid():
                os.close(lock_file)
                raise OSError("unsafe lock file")
        except OSError as error:
            raise BridgeError(
                "tool_failed",
                "Calibre command coordination is unavailable",
                retryable=True,
            ) from error

        try:
            while True:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return lock_file
                except BlockingIOError:
                    if context is not None:
                        context.check_cancelled()
                    time.sleep(0.025)
        except BaseException:
            os.close(lock_file)
            raise

    def run_command(
        self,
        command: list[str],
        *,
        timeout: float,
        active_context: OperationContext | None,
        cwd: Path | None,
        commit: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        if active_context is not None:
            if commit and not active_context.begin_commit():
                active_context.check_cancelled()
                raise BridgeError("operation_finished", "The Calibre operation is no longer active")
            active_context.report_progress(
                {"message": f"Running {Path(command[0]).name}"}
            )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise BridgeError(
                "capability_unavailable",
                f"Required command is unavailable: {Path(command[0]).name}",
            ) from error
        except OSError as error:
            raise BridgeError("tool_failed", "Calibre could not start the requested operation") from error

        if active_context is not None:
            active_context.register_process_terminator(
                lambda: self.cancel_process_group(process)
            )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self.terminate_process_group(process)
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                self.terminate_process_group(process, force=True)
                stdout, stderr = process.communicate()
            raise BridgeError(
                "timeout",
                f"Calibre command timed out: {Path(command[0]).name}",
                retryable=True,
            ) from error

        if active_context is not None:
            active_context.check_cancelled()
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check and process.returncode != 0:
            detail = (stderr or stdout or "").strip()
            if Path(command[0]).name == "calibredb" and "another calibre program" in detail.lower():
                raise BridgeError(
                    "calibre_busy",
                    "Calibre is busy. Wait for the active library task, then retry",
                    retryable=True,
                )
            raise BridgeError("tool_failed", "Calibre could not complete the requested operation")
        return completed

    @staticmethod
    def terminate_process_group(process: subprocess.Popen[str], *, force: bool = False) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.kill() if force else process.terminate()
            except ProcessLookupError:
                return

    @classmethod
    def cancel_process_group(cls, process: subprocess.Popen[str]) -> None:
        cls.terminate_process_group(process)

        def escalate() -> None:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                cls.terminate_process_group(process, force=True)

        threading.Thread(
            target=escalate,
            name=f"calibre-cancel-{process.pid}",
            daemon=True,
        ).start()


def emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class BridgeRuntime:
    """Keep the JSON-lines transport responsive while Calibre work runs."""

    def __init__(
        self,
        bridge: Any,
        emitter: Callable[[dict[str, Any]], None] = emit,
        *,
        max_workers: int = 4,
    ) -> None:
        self.bridge = bridge
        self.emitter = emitter
        self._emit_lock = threading.RLock()
        self.scheduler = OperationScheduler(self._emit_scheduled, max_workers=max_workers)

    def receive(self, request: Any) -> None:
        request_id = request.get("id", "invalid") if isinstance(request, dict) else "invalid"
        try:
            self.bridge.validate_request(request)
        except BridgeError as error:
            self.reject(str(request_id), error)
            return

        if request.get("type") == "cancel":
            self.scheduler.cancel(request["id"])
            return

        try:
            key = self.bridge.scheduling_key(request)
            self.scheduler.submit(
                request["id"],
                lambda context: self.bridge.execute(request, context),
                key=key,
            )
        except DuplicateRequest:
            self.reject(request["id"], BridgeError("duplicate_request", "Request id was already used"))
        except SchedulerClosed:
            self.reject(request["id"], BridgeError("bridge_stopped", "The Calibre bridge is stopping", retryable=True))
        except (TypeError, ValueError) as error:
            self.reject(request["id"], BridgeError("invalid_request", str(error)))

    def reject(self, request_id: str, error: BridgeError) -> None:
        self._emit(
            CalibreBridge.event(
                request_id or "invalid",
                0,
                "failed",
                error=error.as_dict(),
            )
        )

    def close(self) -> None:
        self.scheduler.close()
        closer = getattr(self.bridge, "close", None)
        if callable(closer):
            closer()

    def _emit_scheduled(self, event: dict[str, Any]) -> None:
        payload = {"protocol": PROTOCOL_VERSION, **event}
        self._emit(payload)

    def _emit(self, event: dict[str, Any]) -> None:
        with self._emit_lock:
            self.emitter(event)


def main() -> int:
    runtime = BridgeRuntime(CalibreBridge())
    try:
        for raw_line in sys.stdin:
            if not raw_line.strip():
                continue
            try:
                request = json.loads(raw_line)
            except json.JSONDecodeError:
                runtime.reject("invalid", BridgeError("invalid_request", "Request must be valid JSON"))
                continue
            runtime.receive(request)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
