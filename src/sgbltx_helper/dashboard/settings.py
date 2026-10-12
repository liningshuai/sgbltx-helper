"""Atomic persistence for dashboard-only local preferences."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .registry import TASKS


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def defaults() -> dict[str, Any]:
        return {
            "modules": {task["task_id"]: bool(task["enabled_by_default"]) for task in TASKS},
            "selected_profile_id": "local-test-mail",
        }

    def load(self) -> dict[str, Any]:
        with self._lock:
            defaults = self.defaults()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                modules = raw.get("modules") if isinstance(raw, dict) else None
                if isinstance(modules, dict):
                    for task_id in defaults["modules"]:
                        if type(modules.get(task_id)) is bool:
                            defaults["modules"][task_id] = modules[task_id]
                selected_profile_id = raw.get("selected_profile_id") if isinstance(raw, dict) else None
                if isinstance(selected_profile_id, str) and selected_profile_id in {"local-main", "local-test-mail"}:
                    defaults["selected_profile_id"] = selected_profile_id
            except FileNotFoundError:
                pass
            except (OSError, UnicodeError, json.JSONDecodeError):
                # A damaged preference file must not make the read-only dashboard unavailable.
                pass
            return defaults

    def set_enabled(self, task_id: str, enabled: bool) -> dict[str, Any]:
        if not isinstance(task_id, str):
            raise ValueError("unknown task")
        if task_id not in {task["task_id"] for task in TASKS}:
            raise ValueError(f"unknown task: {task_id}")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self._lock:
            data = self.load()
            data["modules"][task_id] = enabled
            self._write_atomic(data)
            return data

    def set_selected_profile(self, profile_id: str) -> dict[str, Any]:
        if not isinstance(profile_id, str):
            raise ValueError("unknown profile")
        if profile_id not in {"local-main", "local-test-mail"}:
            raise ValueError(f"unknown profile: {profile_id}")
        with self._lock:
            data = self.load()
            data["selected_profile_id"] = profile_id
            self._write_atomic(data)
            return data

    def _write_atomic(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
