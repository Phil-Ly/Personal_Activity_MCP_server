"""Shared subprocess bridge for macOS JavaScript for Automation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TypeVar


class JXABackendError(RuntimeError):
    """Base error carrying whether an external write could have happened."""

    def __init__(
        self,
        message: str,
        *,
        external_state_changed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.external_state_changed = external_state_changed


ErrorType = TypeVar("ErrorType", bound=JXABackendError)


def required_json_string(payload: dict[str, object], field_name: str) -> str:
    value = payload[field_name]
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def optional_json_string(
    payload: dict[str, object],
    field_name: str,
) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or null")
    return value or None


def required_json_bool(payload: dict[str, object], field_name: str) -> bool:
    value = payload[field_name]
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a boolean")
    return value


def optional_json_int(
    payload: dict[str, object],
    field_name: str,
) -> int | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer or null")
    return value


def run_jxa(
    osascript_path: Path,
    script: str,
    args: list[str],
    *,
    application_name: str,
    error_type: type[ErrorType],
) -> str:
    """Execute one JXA script and translate subprocess failures."""
    program = (
        'ObjC.import("Foundation");\n'
        "(function() {\n"
        f"{script.rstrip()}\n"
        f"const __mcpOutput = String(run("
        f"{json.dumps(args, ensure_ascii=True, separators=(',', ':'))}));\n"
        'const __mcpData = $(__mcpOutput + "\\n").dataUsingEncoding('
        "$.NSUTF8StringEncoding);\n"
        "$.NSFileHandle.fileHandleWithStandardOutput.writeData(__mcpData);\n"
        "})();\n"
    )
    try:
        result = subprocess.run(
            [str(osascript_path), "-l", "JavaScript"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            input=program,
        )
    except subprocess.TimeoutExpired as error:
        raise error_type(f"Unable to run osascript: {error}") from error
    except (OSError, ValueError) as error:
        raise error_type(
            f"Unable to run osascript: {error}",
            external_state_changed=False,
        ) from error
    except subprocess.SubprocessError as error:
        raise error_type(f"Unable to run osascript: {error}") from error
    if result.returncode != 0:
        message = (
            result.stderr.strip() or result.stdout.strip() or f"unknown {application_name} error"
        )
        raise error_type(f"{application_name} automation failed: {message}")
    return result.stdout.strip()
