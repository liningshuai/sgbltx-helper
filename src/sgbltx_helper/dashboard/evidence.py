"""Bounded, read-only access to existing run evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"(?:dryrun|run)-[A-Za-z0-9T_-]{8,96}\Z")
_STEP = re.compile(r"step-\d{4,8}\.png\Z")
_IMAGES = {"before.png", "after.png"}


def _runs_dir(artifacts_root: Path) -> Path:
    root = Path(artifacts_root).resolve()
    return root if root.name.lower() == "runs" else root / "runs"


def _safe_run_dir(artifacts_root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    try:
        root = _runs_dir(artifacts_root).resolve(strict=True)
        run_dir = (root / run_id).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("run not found") from exc
    if run_dir.parent != root or not run_dir.is_dir():
        raise ValueError("run not found")
    return run_dir


def _safe_evidence_file(run_dir: Path, path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"invalid evidence path: {path.name}") from exc
    if resolved.parent != run_dir.resolve() or not resolved.is_file():
        raise ValueError(f"evidence file escapes run directory: {path.name}")
    return resolved


def _read_json(path: Path) -> dict[str, Any] | None:
    resolved = _safe_evidence_file(path.parent, path)
    if resolved is None:
        return None
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid evidence JSON: {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid evidence JSON: {path.name} must contain an object")
    return value


def _image_names(run_dir: Path) -> list[str]:
    names = []
    try:
        entries = list(run_dir.iterdir())
    except OSError:
        return names
    for path in entries:
        if path.name not in _IMAGES and not _STEP.fullmatch(path.name):
            continue
        try:
            if _safe_evidence_file(run_dir, path) is not None:
                names.append(path.name)
        except ValueError:
            continue
    return sorted(names, key=lambda name: (name not in _IMAGES, name))


def _events(path: Path) -> list[dict[str, Any]]:
    resolved = _safe_evidence_file(path.parent, path)
    if resolved is None:
        return []
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read events.jsonl: {exc}") from exc
    lines = content.splitlines(keepends=True)
    result = []
    for index, line in enumerate(lines):
        if not line.endswith(("\n", "\r")):
            # The last event may still be being written by the runner.
            if index == len(lines) - 1:
                continue
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise ValueError(f"malformed events.jsonl line {index + 1}")
        if isinstance(value, dict):
            result.append(value)
    return result


def list_runs(artifacts_root: Path, limit: int = 30) -> list[dict[str, Any]]:
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    root = _runs_dir(artifacts_root).resolve()
    if not root.is_dir():
        return []
    directories = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for path in entries:
        if not _RUN_ID.fullmatch(path.name):
            continue
        try:
            directories.append(_safe_run_dir(root, path.name))
        except (OSError, ValueError, RuntimeError):
            continue
    directories.sort(key=lambda path: _mtime(path), reverse=True)
    runs = []
    for directory in directories[:limit]:
        try:
            result = _read_json(directory / "result.json")
            inspection = _read_json(directory / "inspection.json")
            record = result or inspection or {}
            runs.append({
                "run_id": directory.name,
                "task_id": record.get("task_id"),
                "account_alias": record.get("account_alias"),
                "status": record.get("status") or ("inspection" if inspection else "incomplete"),
                "reason_code": record.get("reason_code"),
                "mode": record.get("mode") or ("dry-run" if inspection else None),
                "started_at": record.get("started_at") or record.get("observed_at"),
                "action_count": record.get("action_count"),
                "images": _image_names(directory),
                "evidence_error": None,
            })
        except (OSError, ValueError, RuntimeError) as exc:
            runs.append({"run_id": directory.name, "status": "evidence_error", "images": [], "evidence_error": str(exc)})
    return runs


def read_run(artifacts_root: Path, run_id: str) -> dict[str, Any]:
    directory = _safe_run_dir(artifacts_root, run_id)
    errors = []
    documents: dict[str, Any] = {}
    for name, filename in (("result", "result.json"), ("inspection", "inspection.json")):
        try:
            documents[name] = _read_json(directory / filename)
        except ValueError as exc:
            errors.append(str(exc))
            documents[name] = None
    try:
        events = _events(directory / "events.jsonl")
    except ValueError as exc:
        events = []
        errors.append(str(exc))
    return {
        "run_id": run_id,
        "result": documents["result"],
        "inspection": documents["inspection"],
        "events": events,
        "images": _image_names(directory),
        "evidence_errors": errors,
    }


def resolve_image(artifacts_root: Path, run_id: str, name: str) -> Path:
    if not isinstance(name, str) or not (name in _IMAGES or _STEP.fullmatch(name)):
        raise ValueError("image name is not allowed")
    run_dir = _safe_run_dir(artifacts_root, run_id)
    path = _safe_evidence_file(run_dir, run_dir / name)
    if path is None:
        raise ValueError("image not found")
    return path


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
