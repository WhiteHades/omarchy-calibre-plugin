#!/usr/bin/env python3
"""Small, structured adapter for Calibre's public ``ebook-device`` CLI."""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable


DEFAULT_TIMEOUT = 120.0
MAX_ARGUMENT_LENGTH = 4096
NO_DEVICE_TEXT = "Unable to find a connected ebook reader."
DEVICE_LOCKED_TEXT = "The device is locked."
DESTINATION_EXISTS_TEXT = "File already exists:"
COMMANDS = ("info", "books", "df", "ls", "cp", "mkdir", "touch", "cat", "rm", "eject", "test_file")
DEVICE_COMMANDS = {
    "info": "info",
    "list": "ls",
    "receive": "cp",
    "send": "cp",
    "eject": "eject",
}

Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


class DeviceError(Exception):
    """An adapter failure that can be serialized across the QML bridge."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        action: str | None = None,
        detail: str | None = None,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.action = action
        self.detail = detail
        self.returncode = returncode

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.action is not None:
            result["action"] = self.action
        if self.returncode is not None:
            result["returncode"] = self.returncode
        return result


class DeviceAdapter:
    """Invoke ``ebook-device`` with validated argv lists.

    The runner is injectable so callers can put the adapter behind their normal
    process boundary and tests can exercise the public command contract without
    requiring a connected reader. The default runner never invokes a shell.
    """

    def __init__(
        self,
        *,
        executable: str | os.PathLike[str] = "ebook-device",
        timeout: float = DEFAULT_TIMEOUT,
        runner: Runner | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive finite number")
        if not math.isfinite(float(timeout)) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self.timeout = float(timeout)
        self._executable = self._resolve_executable(executable)
        self._runner = runner or self._run_process
        self._capabilities: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def capabilities(self) -> dict[str, Any]:
        """Return executable/version/command capability information."""
        with self._lock:
            return self._discover_capabilities()

    def _discover_capabilities(self) -> dict[str, Any]:
        if self._capabilities is not None:
            return self._capabilities

        if self._executable is None:
            self._capabilities = {
                "available": False,
                "state": "unavailable",
                "executable": "",
                "version": "",
                "commands": [],
                "supports": self._supports(()),
            }
            return self._capabilities

        version = ""
        discovery_error: dict[str, Any] | None = None
        try:
            version_result = self._invoke(("--version",))
            if version_result.returncode == 0:
                version = self._parse_version(self._text(version_result.stdout, "version"))
        except DeviceError as error:
            discovery_error = error.as_dict()

        commands: tuple[str, ...] = ()
        try:
            usage_result = self._invoke(())
            commands = self._parse_commands(
                self._text(usage_result.stdout, "usage") + "\n" + self._text(usage_result.stderr, "usage")
            )
        except DeviceError as error:
            discovery_error = error.as_dict()

        self._capabilities = {
            "available": True,
            "state": "ready" if commands else "unusable",
            "executable": str(self._executable),
            "version": version,
            "commands": list(commands),
            "supports": self._supports(commands),
        }
        if discovery_error is not None:
            self._capabilities["error"] = discovery_error
        return self._capabilities

    def probe(self) -> dict[str, Any]:
        """Report whether the CLI is usable and whether a device is connected."""
        capabilities = self.capabilities()
        if not capabilities["available"]:
            return {"state": "unavailable", "available": False, "info": None}
        if not capabilities["supports"]["info"]:
            return {"state": "unsupported", "available": True, "info": None}
        try:
            info = self.info()
        except DeviceError as error:
            if error.code == "no_device":
                return {"state": "no-device", "available": True, "info": None}
            return {"state": "error", "available": True, "info": None, "error": error.as_dict()}
        return {"state": "connected", "available": True, "info": info}

    def info(self) -> dict[str, str]:
        """Return the device information reported by ``ebook-device info``."""
        self._require_command("info")
        result = self._checked_invoke(("info",), "info")
        values: dict[str, str] = {}
        labels = {
            "Device name": "deviceName",
            "Device version": "deviceVersion",
            "Software version": "softwareVersion",
            "Mime type": "mimeType",
        }
        for line in self._text(result.stdout, "info").splitlines():
            label, separator, value = line.partition(":")
            if separator and label.strip() in labels:
                values[labels[label.strip()]] = value.strip()
        required = {"deviceName", "deviceVersion", "softwareVersion", "mimeType"}
        if set(values) != required:
            raise DeviceError(
                "invalid_output",
                "ebook-device returned incomplete device information",
                action="info",
            )
        return values

    def list(self, path: str = "/", *, recursive: bool = False) -> dict[str, Any]:
        """List files on the device using the CLI's long listing format."""
        device_path = self._device_path(path, allow_root=True, label="Device path")
        if not isinstance(recursive, bool):
            raise DeviceError("invalid_request", "recursive must be a boolean")
        self._require_command("list")
        args = ["ls", "-l"]
        if recursive:
            args.append("-R")
        args.append(device_path)
        result = self._checked_invoke(tuple(args), "list")
        return {
            "path": device_path,
            "entries": self._parse_listing(self._text(result.stdout, "list"), device_path, recursive),
        }

    def send(
        self,
        source: str | os.PathLike[str],
        destination: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Copy one local file to one validated device file path."""
        local_source = self._local_file(source)
        device_path = self._device_path(destination, allow_root=False, label="Device destination")
        if not isinstance(force, bool):
            raise DeviceError("invalid_request", "force must be a boolean")
        self._require_command("send")
        args = ["cp"]
        if force:
            args.append("--force")
        args.extend([str(local_source), f"dev:{device_path}"])
        self._checked_invoke(tuple(args), "send")
        return {
            "source": str(local_source),
            "destination": device_path,
            "replaced": force,
        }

    def receive(
        self,
        source: str,
        destination: str | os.PathLike[str],
    ) -> dict[str, str]:
        """Copy one device file to a new validated local path."""
        device_path = self._device_path(source, allow_root=False, label="Device source")
        local_destination = self._local_destination(destination)
        self._require_command("receive")
        try:
            self._checked_invoke(
                ("cp", f"dev:{device_path}", str(local_destination)),
                "receive",
            )
        except Exception:
            local_destination.unlink(missing_ok=True)
            raise
        if local_destination.is_symlink() or not local_destination.is_file():
            local_destination.unlink(missing_ok=True)
            raise DeviceError(
                "invalid_output",
                "ebook-device did not create the requested local file",
                action="receive",
            )
        return {"source": device_path, "destination": str(local_destination)}

    def eject(self) -> dict[str, bool]:
        """Eject the connected device through Calibre."""
        self._require_command("eject")
        self._checked_invoke(("eject",), "eject")
        return {"ejected": True}

    def _require_command(self, action: str) -> None:
        capabilities = self.capabilities()
        command = DEVICE_COMMANDS[action]
        if command not in capabilities["commands"]:
            discovery_error = capabilities.get("error")
            if not capabilities["commands"] and isinstance(discovery_error, dict):
                raise DeviceError(
                    str(discovery_error.get("code", "unavailable")),
                    str(discovery_error.get("message", "ebook-device capabilities are unavailable")),
                    retryable=bool(discovery_error.get("retryable", False)),
                    action=action,
                    detail=discovery_error.get("detail"),
                    returncode=discovery_error.get("returncode"),
                )
            raise DeviceError(
                "unsupported",
                f"ebook-device does not support {action}",
                action=action,
            )

    def _checked_invoke(self, args: tuple[str, ...], action: str) -> subprocess.CompletedProcess[str]:
        result = self._invoke(args)
        stdout = self._text(result.stdout, action)
        stderr = self._text(result.stderr, action)
        detail = (stderr or stdout).strip()
        if NO_DEVICE_TEXT.lower() in detail.lower():
            raise DeviceError(
                "no_device",
                "No ebook reader is connected",
                retryable=True,
                action=action,
                detail=detail,
                returncode=result.returncode,
            )
        if DEVICE_LOCKED_TEXT.lower() in detail.lower():
            raise DeviceError(
                "device_locked",
                "The ebook reader is locked",
                retryable=False,
                action=action,
                detail=detail,
                returncode=result.returncode,
            )
        if action == "send" and DESTINATION_EXISTS_TEXT.lower() in detail.lower():
            raise DeviceError(
                "destination_exists",
                "This book already exists on the ebook reader",
                retryable=True,
                action=action,
                detail=detail,
                returncode=result.returncode,
            )
        if result.returncode == 0:
            return result
        raise DeviceError(
            "command_failed",
            f"ebook-device {action} failed",
            retryable=True,
            action=action,
            detail=detail or None,
            returncode=result.returncode,
        )

    def _invoke(self, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if self._executable is None:
            raise DeviceError("unavailable", "ebook-device is unavailable", retryable=True)
        command = [str(self._executable), *args]
        try:
            result = self._runner(command, self.timeout)
        except FileNotFoundError as error:
            raise DeviceError("unavailable", "ebook-device is unavailable", retryable=True) from error
        except subprocess.TimeoutExpired as error:
            raise DeviceError(
                "timeout",
                "ebook-device timed out",
                retryable=True,
                action=args[0] if args else "capabilities",
            ) from error
        except OSError as error:
            raise DeviceError(
                "unavailable",
                "ebook-device could not be started",
                retryable=True,
                detail=str(error),
            ) from error
        if not isinstance(result, subprocess.CompletedProcess):
            raise DeviceError("invalid_runner", "The device command runner returned an invalid result")
        return result

    @staticmethod
    def _run_process(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )

    @staticmethod
    def _resolve_executable(value: str | os.PathLike[str]) -> Path | None:
        if not isinstance(value, (str, os.PathLike)):
            raise ValueError("executable must be a path or command name")
        raw = os.fspath(value)
        if not raw or "\x00" in raw:
            raise ValueError("executable must be a non-empty path or command name")
        if os.sep in raw or (os.altsep and os.altsep in raw):
            path = Path(raw).expanduser()
            if not path.is_file() or not os.access(path, os.X_OK):
                return None
            return path.resolve()
        resolved = shutil.which(raw)
        return Path(resolved).resolve() if resolved else None

    @staticmethod
    def _supports(commands: tuple[str, ...]) -> dict[str, bool]:
        return {
            "info": "info" in commands,
            "list": "ls" in commands,
            "receive": "cp" in commands,
            "send": "cp" in commands,
            "eject": "eject" in commands,
        }

    @staticmethod
    def _parse_version(output: str) -> str:
        match = re.search(r"calibre\s+(?:version:\s*)?([^\s)]+)", output, re.IGNORECASE)
        return match.group(1) if match else ""

    @staticmethod
    def _parse_commands(output: str) -> tuple[str, ...]:
        match = re.search(r"command is one of:\s*(.*?)(?:\n\s*For help|$)", output, re.IGNORECASE | re.DOTALL)
        if match is None:
            return ()
        found = set(re.findall(r"[a-z_]+", match.group(1).lower()))
        return tuple(command for command in COMMANDS if command in found)

    @staticmethod
    def _text(value: str | bytes | None, action: str) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            return value
        raise DeviceError("invalid_runner", f"The device command returned invalid {action} output")

    @classmethod
    def _local_file(cls, value: str | os.PathLike[str]) -> Path:
        if not isinstance(value, (str, os.PathLike)):
            raise DeviceError("invalid_request", "Source must be a file path")
        raw = os.fspath(value)
        if not raw or len(raw) > MAX_ARGUMENT_LENGTH or "\x00" in raw:
            raise DeviceError("invalid_request", "Source must be a valid file path")
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise DeviceError("invalid_request", "Source must identify an existing file") from error
        if not path.is_file():
            raise DeviceError("invalid_request", "Source must identify an existing file")
        return path

    @classmethod
    def _local_destination(cls, value: str | os.PathLike[str]) -> Path:
        if not isinstance(value, (str, os.PathLike)):
            raise DeviceError("invalid_request", "Destination must be a file path")
        raw = os.fspath(value)
        if not raw or len(raw) > MAX_ARGUMENT_LENGTH or "\x00" in raw:
            raise DeviceError("invalid_request", "Destination must be a valid file path")
        if raw.endswith(os.sep) or (os.altsep and raw.endswith(os.altsep)):
            raise DeviceError("invalid_request", "Destination must identify a file")
        unresolved = Path(raw).expanduser()
        if unresolved.name in {"", ".", ".."}:
            raise DeviceError("invalid_request", "Destination must identify a file")
        try:
            parent = unresolved.parent.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise DeviceError("invalid_request", "Destination parent must exist") from error
        if not parent.is_dir():
            raise DeviceError("invalid_request", "Destination parent must be a directory")
        destination = parent / unresolved.name
        if os.path.lexists(destination):
            raise DeviceError("invalid_request", "Destination must not already exist")
        return destination

    @classmethod
    def _device_path(cls, value: str, *, allow_root: bool, label: str) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_ARGUMENT_LENGTH:
            raise DeviceError("invalid_request", f"{label} must be a valid device path")
        if "\x00" in value or "\\" in value or "\n" in value or "\r" in value:
            raise DeviceError("invalid_request", f"{label} must be a valid device path")
        path = value[4:] if value.startswith("dev:") else value
        if not (path == "/" or path.startswith("/") or path.startswith("carda:/") or path.startswith("cardb:/")):
            raise DeviceError("invalid_request", f"{label} must begin with /, carda:/, or cardb:/")
        if path == "/" and not allow_root:
            raise DeviceError("invalid_request", f"{label} must identify a file")
        if path != "/" and path.endswith("/") and not allow_root:
            raise DeviceError("invalid_request", f"{label} must identify a file")
        parts = path.split("/")
        if any(part in {".", ".."} for part in parts):
            raise DeviceError("invalid_request", f"{label} cannot contain dot segments")
        if "//" in path and path != "/":
            raise DeviceError("invalid_request", f"{label} cannot contain empty path segments")
        return path

    @classmethod
    def _parse_listing(cls, output: str, requested_path: str, recursive: bool) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        current_path = requested_path
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if recursive and stripped.endswith(":") and not cls._looks_like_listing(stripped):
                current_path = cls._device_path(stripped[:-1], allow_root=True, label="Listing path")
                continue
            fields = stripped.split(maxsplit=4)
            if len(fields) != 5 or not re.fullmatch(r"[d-][r-][w-][x-][r-][w-][x-][r-][w-][x-]", fields[0]):
                raise DeviceError("invalid_output", "ebook-device returned an invalid file listing", action="list")
            try:
                size = int(fields[1])
            except ValueError as error:
                raise DeviceError("invalid_output", "ebook-device returned an invalid file size", action="list") from error
            name = fields[4]
            is_directory = fields[0].startswith("d")
            if not name or name in {".", ".."} or "/" in name:
                raise DeviceError("invalid_output", "ebook-device returned an invalid file name", action="list")
            entry_path = current_path.rstrip("/") + "/" + name
            if current_path == "/":
                entry_path = "/" + name
            entries.append(
                {
                    "name": name,
                    "path": entry_path,
                    "isDirectory": is_directory,
                    "size": size,
                    "mode": fields[0],
                    "modified": f"{fields[2]} {fields[3]}",
                }
            )
        return entries

    @staticmethod
    def _looks_like_listing(line: str) -> bool:
        return bool(re.match(r"[d-][r-][w-][x-][r-][w-][x-][r-][w-][x-]\s", line))
