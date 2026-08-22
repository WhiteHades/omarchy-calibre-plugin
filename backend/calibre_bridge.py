#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

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
DEVICE_ACTIONS = {
    "device.probe",
    "device.info",
    "device.list",
    "device.eject",
    "device.send",
}


class BridgeError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class CalibreBridge:
    def __init__(self, *, device_adapter: DeviceAdapter | None = None) -> None:
        self.libraries: dict[str, Path] = {}
        self.confirmations: dict[str, dict[str, Any]] = {}
        self._conversion_capabilities: dict[str, Any] | None = None
        self.device_adapter = device_adapter if device_adapter is not None else DeviceAdapter()

    def handle(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        request_id = request.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise BridgeError("invalid_request", "Request id must be a non-empty string")
        if request.get("protocol") != PROTOCOL_VERSION:
            raise BridgeError("invalid_request", "Unsupported bridge protocol")

        events = [self.event(request_id, 0, "accepted")]
        try:
            operation = request.get("operation")
            if operation == "bootstrap":
                result = self.bootstrap(request.get("input", {}))
            elif operation == "books.query":
                result = self.books_query(request.get("library"), request.get("input", {}))
            elif operation == "conversion.describe":
                result = self.describe_conversion(request.get("library"), request.get("input", {}))
            elif isinstance(operation, str) and operation in DEVICE_ACTIONS:
                result = self.device_action(operation, request.get("library"), request.get("input", {}))
            elif operation == "action.run":
                result = self.action_run(request.get("library"), request.get("input", {}))
            elif operation == "action.prepare":
                result = self.action_prepare(request.get("library"), request.get("input", {}))
            elif operation == "action.commit":
                result = self.action_commit(request.get("input", {}))
            else:
                raise BridgeError("invalid_request", f"Unknown operation: {operation}")
            events.append(self.event(request_id, 1, "succeeded", result=result))
        except BridgeError as error:
            events.append(self.event(request_id, 1, "failed", error=error.as_dict()))
        except DeviceError as error:
            events.append(self.event(request_id, 1, "failed", error=error.as_dict()))
        return events

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
        page = self.query_books(current, {"limit": page_size}) if current else self.empty_page()
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
            return self.device_adapter.eject()
        if action == "device.send":
            return self.device_send(library_token, input_data)
        raise BridgeError("invalid_request", f"Unknown device action: {action}")

    def device_send(self, library_token: Any, input_data: dict[str, Any]) -> dict[str, Any]:
        library = self.require_library(library_token)
        book_id = self.require_book_id(input_data.get("bookId"))
        format_name = self.require_format_name(input_data.get("format"), "format")
        destination = self.require_device_destination(input_data.get("destination"))
        force = input_data.get("force", False)
        if not isinstance(force, bool):
            raise BridgeError("invalid_request", "force must be a boolean")

        book = self.get_book(str(library_token), book_id)
        self.find_format(book, format_name)
        staging = Path(tempfile.mkdtemp(prefix="omarchy-calibre-device-"))
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
            sent = self.device_adapter.send(source, destination, force=force)
            return {**sent, "bookId": book_id, "format": format_name}
        finally:
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
        confirmation_token = secrets.token_urlsafe(24)
        self.confirmations[confirmation_token] = {
            "expires": time.monotonic() + 60,
            "name": "book.remove",
            "libraryToken": library_token,
            "library": library,
            "bookIds": book_ids,
            "bookRevisions": {book["id"]: book["modified"] for book in books},
        }
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
        plan = self.confirmations.pop(token, None)
        if plan is None:
            raise BridgeError("confirmation_required", "The confirmation has expired or was already used")
        if plan["expires"] < time.monotonic():
            self.cleanup_plan(plan)
            raise BridgeError("confirmation_required", "The confirmation has expired or was already used")
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
                ]
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
                ]
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
                ]
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
                    self.require_unchanged_file(
                        destination / relative,
                        revision,
                        "An exported file changed; review the replacement again",
                    )
                files = self.publish_staged_export(staging, destination, replace=True)
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
                    ]
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

    def require_library(self, library_token: Any) -> Path:
        if not isinstance(library_token, str) or not library_token:
            raise BridgeError("invalid_request", "A library token is required")
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
        self.run(command)
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
            ]
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
        self.run(command)
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
            ]
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
        confirmation_token = secrets.token_urlsafe(24)
        self.confirmations[confirmation_token] = {
            "expires": time.monotonic() + 60,
            "name": "format.replace",
            "libraryToken": library_token,
            "library": library,
            "bookId": book_id,
            "format": format_name,
            "source": source,
            "sourceRevision": self.file_revision(source),
            "targetRevision": self.file_revision(Path(target["path"])),
        }
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
        confirmation_token = secrets.token_urlsafe(24)
        self.confirmations[confirmation_token] = {
            "expires": time.monotonic() + 60,
            "name": "format.remove",
            "libraryToken": library_token,
            "library": library,
            "bookId": book_id,
            "format": format_name,
            "targetRevision": self.file_revision(Path(target["path"])),
        }
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
        return value

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

    @staticmethod
    def file_revision(path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
        except OSError as error:
            raise BridgeError("file_unavailable", "A selected format is unavailable") from error
        return stat.st_size, stat.st_mtime_ns

    @classmethod
    def require_unchanged_file(
        cls,
        path: Path,
        expected: tuple[int, int],
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
                ]
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
        confirmation_token = secrets.token_urlsafe(24)
        self.confirmations[confirmation_token] = {
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
        }
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
            self._conversion_capabilities = discovered
        except (BridgeError, json.JSONDecodeError, TypeError, ValueError):
            self._conversion_capabilities = fallback
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
        staged_files = sorted(path for path in staging.rglob("*") if path.is_file())
        relative_files = [path.relative_to(staging) for path in staged_files]
        collisions = [relative for relative in relative_files if (destination / relative).exists()]
        if not collisions:
            shutil.rmtree(staging, ignore_errors=True)
            raise BridgeError("confirmation_not_needed", "The export does not replace any files")
        if any(not (destination / relative).is_file() for relative in collisions):
            shutil.rmtree(staging, ignore_errors=True)
            raise BridgeError("invalid_request", "Export cannot replace a directory")

        confirmation_token = secrets.token_urlsafe(24)
        self.confirmations[confirmation_token] = {
            "expires": time.monotonic() + 60,
            "name": "book.export.replace",
            "staging": staging,
            "destination": destination,
            "stagedRevisions": {
                relative: self.file_revision(staging / relative) for relative in relative_files
            },
            "targetRevisions": {
                relative: self.file_revision(destination / relative) for relative in collisions
            },
        }
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
        destination = Path(raw_destination).expanduser().resolve()
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

    @staticmethod
    def publish_staged_export(staging: Path, destination: Path, *, replace: bool) -> list[Path]:
        staged_files = sorted(path for path in staging.rglob("*") if path.is_file())
        relative_files = [path.relative_to(staging) for path in staged_files]
        collisions = [relative for relative in relative_files if (destination / relative).exists()]
        if collisions and not replace:
            raise BridgeError("confirmation_required", "Export would replace existing files")
        if any(not (destination / relative).is_file() for relative in collisions):
            raise BridgeError("invalid_request", "Export cannot replace a directory")

        destination.mkdir(parents=False, exist_ok=True)
        published = []
        for source, relative in zip(staged_files, relative_files, strict=True):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.omarchy-{secrets.token_hex(6)}")
            try:
                shutil.copy2(source, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            published.append(target)
        return published

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

        command = [
            "calibredb",
            "list",
            "--with-library",
            str(library),
            "--for-machine",
            "--fields",
            BOOK_FIELDS,
            "--sort-by",
            sort,
        ]
        if direction == "ascending":
            command.append("--ascending")
        if search:
            command.extend(["--search", search])
        completed = self.run(command)
        try:
            rows = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise BridgeError("tool_failed", "Calibre returned invalid book data") from error
        if not isinstance(rows, list):
            raise BridgeError("tool_failed", "Calibre returned an invalid book list")
        page_rows = rows[offset : offset + limit]
        items = [self.normalize_book(row) for row in page_rows]
        next_offset = offset + len(page_rows)
        next_cursor = str(next_offset) if next_offset < len(rows) else None
        return {"items": items, "total": len(rows), "nextCursor": next_cursor}

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

    @staticmethod
    def run(command: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as error:
            raise BridgeError("capability_unavailable", f"Required command is unavailable: {command[0]}") from error
        except subprocess.TimeoutExpired as error:
            raise BridgeError("timeout", f"Calibre command timed out: {command[0]}", retryable=True) from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            message = detail.splitlines()[-1] if detail else f"Calibre command failed: {command[0]}"
            raise BridgeError("tool_failed", message) from error


def emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    bridge = CalibreBridge()
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise BridgeError("invalid_request", "Request must be a JSON object")
            events = bridge.handle(request)
        except json.JSONDecodeError:
            events = [
                CalibreBridge.event(
                    "invalid",
                    0,
                    "failed",
                    error=BridgeError("invalid_request", "Request must be valid JSON").as_dict(),
                )
            ]
        except BridgeError as error:
            request_id = request.get("id", "invalid") if isinstance(request, dict) else "invalid"
            events = [CalibreBridge.event(str(request_id), 0, "failed", error=error.as_dict())]
        for event in events:
            emit(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
