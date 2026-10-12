"""证据记录：事件流、结果文件、截图与保留策略。

设计说明 9：原始游戏截图只保存在本地 artifact；测试样本裁剪脱敏；
配置、日志与私密截图不进入 Git，不自动上传第三方。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import RunEvent


def utc_now_iso() -> str:
    """统一用 UTC ISO-8601，避免跨时区审查歧义。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"


def save_image_bgr(path: Path, image: np.ndarray) -> Path:
    """把 MaaFramework 返回的 BGR 数组写成 PNG。"""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(image)
    if arr.ndim == 2:
        pil = Image.fromarray(arr)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        pil = Image.fromarray(arr[:, :, [2, 1, 0, 3]])
    elif arr.ndim == 3 and arr.shape[2] == 3:
        pil = Image.fromarray(arr[:, :, ::-1])
    else:
        raise ValueError(f"不支持的图像形状：{arr.shape}")
    pil.save(path)
    return path


def crop_bgr(image: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    """按 (x, y, w, h) 裁剪。用于生成脱敏的测试样本。"""
    x, y, w, h = roi
    return np.asarray(image)[y : y + h, x : x + w]


@dataclass
class RunWorkspace:
    """一轮运行的 artifact 目录。"""

    run_id: str
    root: Path  # 例如 artifacts/runs/<run_id>

    @classmethod
    def create(cls, artifacts_root: Path, run_id: str) -> "RunWorkspace":
        root = Path(artifacts_root) / "runs" / run_id
        root.mkdir(parents=True, exist_ok=True)
        return cls(run_id=run_id, root=root)

    # -- 路径 ------------------------------------------------------------
    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def result_path(self) -> Path:
        return self.root / "result.json"

    @property
    def inspection_path(self) -> Path:
        return self.root / "inspection.json"

    def image_path(self, name: str) -> Path:
        return self.root / f"{name}.png"

    # -- 写入 ------------------------------------------------------------
    def log_event(self, seq: int, node: str, event: str, detail: dict[str, Any] | None = None) -> RunEvent:
        evt = RunEvent(
            run_id=self.run_id,
            seq=seq,
            at=utc_now_iso(),
            node=node,
            event=event,
            detail=detail or {},
        )
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(evt.to_dict(), ensure_ascii=False) + "\n")
        return evt

    def save_image(self, name: str, image: np.ndarray) -> Path:
        return save_image_bgr(self.image_path(name), image)

    def write_json(self, path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def cleanup_expired(artifacts_root: Path, retention_days: int, now: datetime | None = None) -> list[Path]:
    """删除本项目 artifact/runs 下超过保留期的运行目录。

    只在本项目 artifact 目录内操作，且只删目录本身；不触碰其它任何路径。
    """
    if retention_days <= 0:
        return []
    runs_dir = Path(artifacts_root) / "runs"
    if not runs_dir.is_dir():
        return []

    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=retention_days)
    removed: list[Path] = []

    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(entry)
                removed.append(entry)
            except OSError:
                continue
    return removed


def redact_for_sample(image: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    """裁剪出测试样本。配合人工确认裁剪区域不含账号/聊天信息。"""
    return crop_bgr(image, roi)


def elapsed_ms(start_monotonic: float) -> int:
    return int((time.monotonic() - start_monotonic) * 1000)
