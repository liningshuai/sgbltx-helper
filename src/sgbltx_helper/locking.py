"""同设备 + 同账号的进程级互斥。

设计说明 8：同一 device_serial 和 account_alias 在本机只能有一个有效运行；
异常退出后的释放须避免误删活跃运行的锁。

做法：使用操作系统的**建议性文件锁**（Windows ``msvcrt.locking`` /
POSIX ``fcntl.flock``）。进程崩溃时由内核释放，因此不会留下需要人工清理的
陈旧锁；锁文件本身保留（内含上次运行的元数据）不影响后续加锁。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .contracts import ConcurrentRunError

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StopUnconfirmedError(ConcurrentRunError):
    """A persistent device block remains after an unconfirmed SDK stop."""

try:  # Windows
    import msvcrt

    _HAVE_MSVCRT = True
except ImportError:  # pragma: no cover - 非 Windows
    msvcrt = None  # type: ignore[assignment]
    _HAVE_MSVCRT = False

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False


def lock_file_name(device_serial: str, account_alias: str | None = None) -> str:
    """设备独占锁名；别名只用于日志元数据，不参与设备互斥键。"""
    key = device_serial.strip().lower()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    slug = _SAFE.sub("_", key)[:48].strip("_") or "run"
    return f"{slug}.{digest}.lock"


class RunLock:
    """进程级互斥锁。用作上下文管理器。"""

    def __init__(self, lock_dir: Path, device_serial: str, account_alias: str) -> None:
        self.lock_dir = Path(lock_dir)
        self.device_serial = device_serial
        self.account_alias = account_alias
        self.path = self.lock_dir / lock_file_name(device_serial)
        self.stop_marker_path = self.lock_dir / f"{lock_file_name(device_serial)}.stop-unconfirmed.json"
        self._fh: Any = None
        self.acquired = False

    # -- 内部 ------------------------------------------------------------
    def _try_lock(self, fh: Any) -> bool:
        if _HAVE_MSVCRT:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        if _HAVE_FCNTL:  # pragma: no cover - 非 Windows
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        # 没有可用锁原语时拒绝运行，而不是假装互斥成功
        raise ConcurrentRunError("当前平台没有可用的文件锁原语，拒绝在没有互斥保护的情况下运行。")

    def _holder_info(self) -> str:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return f"pid={data.get('pid')} run_id={data.get('run_id')} started_at={data.get('started_at')}"
        except Exception:  # noqa: BLE001
            return "<无法读取持有者信息>"

    def _stop_unconfirmed_error(self) -> StopUnconfirmedError | None:
        if not self.stop_marker_path.is_file():
            return None
        try:
            detail = self.stop_marker_path.read_text(encoding="utf-8")
        except OSError as exc:
            detail = f"marker unreadable: {exc}"
        return StopUnconfirmedError(
            f"设备 {self.device_serial} 因上次停止未确认而被禁用；"
            f"请先人工确认模拟器任务已停止，再清除 {self.stop_marker_path}。记录：{detail}"
        )

    # -- 公开 ------------------------------------------------------------
    def acquire(self, run_id: str) -> None:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        error = self._stop_unconfirmed_error()
        if error is not None:
            raise error
        fh = open(self.path, "a+", encoding="utf-8")
        if not self._try_lock(fh):
            fh.close()
            raise ConcurrentRunError(
                "同一设备与账号已有有效运行，拒绝启动第二个进程："
                f"{self.path.name}（持有者 {self._holder_info()}）"
            )
        # 标记可能在锁前检查之后由上一轮写入；取得锁后必须再查一次。
        self._fh = fh
        self.acquired = True
        error = self._stop_unconfirmed_error()
        if error is not None:
            self.release()
            raise error
        # 拿到锁之后才写元数据，避免误删/误写活跃运行的记录
        fh.seek(0)
        fh.truncate()
        fh.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "run_id": run_id,
                    "device_serial": self.device_serial,
                    "account_alias": self.account_alias,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                ensure_ascii=False,
            )
        )
        fh.flush()

    def mark_stop_unconfirmed(self, run_id: str, reason: str) -> Path:
        """持锁期间持久化设备禁用标记，防止下一轮误控制仍运行的任务。"""
        if not self.acquired:
            raise RuntimeError("必须先取得设备锁才能写入停止未确认标记")
        temporary = self.stop_marker_path.with_suffix(f".tmp-{os.getpid()}")
        payload = json.dumps(
            {
                "device_serial": self.device_serial,
                "account_alias": self.account_alias,
                "run_id": run_id,
                "pid": os.getpid(),
                "reason": reason,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            ensure_ascii=False,
        )
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.stop_marker_path)
        return self.stop_marker_path

    @staticmethod
    def clear_stop_unconfirmed(lock_dir: Path, device_serial: str) -> bool:
        """清除禁用标记；调用方必须先完成设备停止状态的人工恢复核查。"""
        path = Path(lock_dir) / f"{lock_file_name(device_serial)}.stop-unconfirmed.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if _HAVE_MSVCRT:
                try:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            elif _HAVE_FCNTL:  # pragma: no cover - 非 Windows
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            self._fh.close()
            self._fh = None
            self.acquired = False

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
