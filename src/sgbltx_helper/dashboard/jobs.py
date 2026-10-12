"""Single-job dashboard coordinator and production runner adapter."""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..contracts import InspectionResult, RunMode, TaskResult, load_config
from ..runner import StopController
from .evidence import _events
from .registry import get_profile, get_task
from .settings import SettingsStore

Executor = Callable[[str, str, str, StopController], dict[str, Any] | TaskResult | InspectionResult]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _json_value(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def default_executor(project_root: Path) -> Executor:
    """Create the explicit production executor; construction itself is device-free."""
    root = Path(project_root).resolve()

    def execute(task_id: str, profile_id: str, mode: str, stop: StopController) -> dict[str, Any]:
        profile = get_profile(root, profile_id)
        if not profile or not profile["available"]:
            raise ValueError("登记的本机档案不可用")
        config_path = Path(profile["config_path"])
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        raw["task_id"] = task_id
        cfg = load_config(raw, RunMode(mode))
        from ..cli import _make_backend
        from ..runner import run

        backend = _make_backend(cfg, root)
        lock_dir = root / cfg.request.artifacts_root / "locks"
        payload = run(
            cfg.request, backend, lock_dir, root, stop=stop,
            dashboard_job_id=getattr(stop, "dashboard_job_id", None),
        )
        serialized = _json_value(payload)
        if isinstance(payload, InspectionResult):
            serialized["result_type"] = "inspection"
            serialized["status"] = "inspection"
            serialized["mode"] = "dry-run"
        else:
            serialized["result_type"] = "task"
            serialized["mode"] = mode
        return serialized

    return execute


def _stage_label(event: dict[str, Any]) -> str:
    node = str(event.get("node") or "")
    kind = str(event.get("event") or "")
    if kind == "input_submitted":
        return {
            "more": "正在打开更多菜单",
            "mail": "正在打开系统邮件",
            "bulk_read": "正在提交一键阅读",
            "reward_close": "正在关闭奖励弹窗",
            "detail_close": "正在关闭邮件详情",
            "mail_close": "正在关闭邮件窗口",
            "open_daily_tasks": "正在打开任务入口",
            "open_daily_rewards": "正在打开日常任务",
            "claim_daily_all": "正在提交日常一键领取",
            "dismiss_daily_reward": "正在关闭日常奖励弹窗",
            "close_daily_tasks": "正在退出日常任务页",
            "scan_daily_tasks": "正在扫描日常任务列表",
            "dismiss_known_announcement": "正在跳过已识别的攻城令公告",
        }.get(node, "正在提交已登记操作")
    if kind == "control_recognition_timeout":
        return "一键阅读按钮识别超时" if node == "bulk_read" else "后续控件识别超时"
    if kind == "control_recognition_stopped":
        return "后续控件识别已停止"
    if kind == "control_recognition_failed":
        return "后续控件识别失败"
    return {
        "run_started": "运行器启动",
        "device_selected": "设备已选择",
        "before_saved": "当前画面已截图",
        "classified": "正在识别页面",
        "mail_bulk_initial_state": "检查邮件初始状态",
        "daily_rewards_initial_state": "检查日常任务初始状态",
        "daily_batch_claim_confirmed": "已确认日常领取后置状态",
        "mail_bulk_transition_observed": "确认页面变化",
        "run_finished": "运行器已返回结果",
        "inspection_built": "只读结果已生成",
    }.get(kind, f"{node} · {kind}".strip(" ·"))


class JobManager:
    def __init__(self, project_root: Path, settings: SettingsStore, executor: Executor, artifacts_root: Path | None = None):
        self.project_root = Path(project_root).resolve()
        registered_artifacts_root = Path(artifacts_root).resolve() if artifacts_root else self.project_root / "artifacts"
        self.runs_root = registered_artifacts_root if registered_artifacts_root.name.lower() == "runs" else registered_artifacts_root / "runs"
        self.settings = settings
        self.executor = executor
        self._lock = threading.RLock()
        self._active: dict[str, Any] | None = None
        self._latest: dict[str, Any] | None = None
        self._requests: dict[str, tuple[tuple[str, str, str], dict[str, Any]]] = {}

    def start(self, request_id: str, task_id: str, profile_id: str, mode: str) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("request_id is required and must be at most 128 characters")
        if not isinstance(task_id, str):
            raise ValueError("unknown task")
        task = get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if not isinstance(profile_id, str):
            raise ValueError("unknown profile")
        if not isinstance(mode, str):
            raise ValueError("unsupported mode")
        if mode not in {RunMode.DRY_RUN.value, RunMode.EXECUTE.value}:
            raise ValueError(f"unsupported mode: {mode}")
        parameters = (task_id, profile_id, mode)
        with self._lock:
            previous = self._requests.get(request_id)
            if previous:
                if previous[0] != parameters:
                    raise ValueError("request_id was already used with different parameters")
                return dict(previous[1])
            if not self.settings.load()["modules"].get(task_id, False):
                raise ValueError(f"module disabled: {task_id}")
            if self._active is not None:
                raise ValueError("another job is active")
            profile = get_profile(self.project_root, profile_id)
            if profile is None:
                raise ValueError(f"unknown profile: {profile_id}")
            if not profile["available"]:
                raise ValueError(f"profile unavailable: {profile['availability_reason']}")
            job_id = str(uuid.uuid4())
            stop_controller = StopController()
            stop_controller.dashboard_job_id = job_id
            action_upper_bound = profile.get("max_actions")
            job = {
                "job_id": job_id,
                "request_id": request_id,
                "task_id": task_id,
                "profile_id": profile_id,
                "mode": mode,
                "status": "queued",
                "stage": "排队中",
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "stop_requested": False,
                "stop_controller": stop_controller,
                "progress": {
                    "action_count": None,
                    "navigation_action_count": None,
                    "claim_action_count": None,
                    "dismiss_action_count": None,
                    "scroll_action_count": None,
                    "batch_round_count": None,
                    "confirmed_batch_round_count": None,
                    "uncertain_batch_round_count": None,
                    "pending_batch_round_count": None,
                    "action_upper_bound": action_upper_bound if type(action_upper_bound) is int else None,
                },
            }
            self._active = job
            self._latest = job
            public = self._public(job)
            self._requests[request_id] = (parameters, public)
            thread = threading.Thread(target=self._run_job, args=(job,), name=f"dashboard-{job['job_id'][:8]}", daemon=True)
            job["thread"] = thread
            thread.start()
            return public

    def _run_job(self, job: dict[str, Any]) -> None:
        with self._lock:
            job["status"] = "stop_requested" if job["stop_requested"] else "running"
            job["stage"] = "停止请求中" if job["stop_requested"] else "运行器启动"
            job["started_at"] = _now()
            job["_started_epoch"] = time.time()
        try:
            result = self.executor(job["task_id"], job["profile_id"], job["mode"], job["stop_controller"])
            result = _json_value(result)
            with self._lock:
                job["result"] = result
                result_detail = result.get("detail") if isinstance(result, dict) else None
                result_detail = result_detail if isinstance(result_detail, dict) else {}
                job["progress"] = {
                    "action_count": result.get("action_count") if type(result.get("action_count")) is int else None,
                    "navigation_action_count": result_detail.get("navigation_action_count") if type(result_detail.get("navigation_action_count")) is int else None,
                    "claim_action_count": result_detail.get("claim_action_count") if type(result_detail.get("claim_action_count")) is int else None,
                    "dismiss_action_count": result_detail.get("dismiss_action_count") if type(result_detail.get("dismiss_action_count")) is int else None,
                    "scroll_action_count": result_detail.get("scroll_action_count") if type(result_detail.get("scroll_action_count")) is int else None,
                    "batch_round_count": result_detail.get("claim_action_count") if type(result_detail.get("claim_action_count")) is int else None,
                    "confirmed_batch_round_count": result_detail.get("confirmed_batch_round_count") if type(result_detail.get("confirmed_batch_round_count")) is int else None,
                    "uncertain_batch_round_count": result_detail.get("uncertain_batch_round_count") if type(result_detail.get("uncertain_batch_round_count")) is int else None,
                    "pending_batch_round_count": result_detail.get("pending_batch_round_count") if type(result_detail.get("pending_batch_round_count")) is int else None,
                    "action_upper_bound": job["progress"].get("action_upper_bound"),
                }
                job["stage"] = "已结束"
                job["status"] = "finished"
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["status"] = "failed"
                job["stage"] = "运行失败"
        finally:
            with self._lock:
                job["finished_at"] = _now()
                if self._active is job:
                    self._active = None
                self._latest = job

    @staticmethod
    def _public(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        return {key: value for key, value in job.items() if key not in {"stop_controller", "thread"}}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            job = self._active or self._latest
            if self._active is not None:
                self._refresh_progress(self._active)
            return {"job": self._public(job), "active": self._active is not None}

    def _refresh_progress(self, job: dict[str, Any]) -> None:
        runs_root = self.runs_root.resolve()
        if not runs_root.is_dir():
            return
        matches = []
        try:
            entries = list(runs_root.iterdir())
            for entry in entries:
                if not entry.is_dir():
                    continue
                directory = entry.resolve()
                if directory.parent != runs_root:
                    continue
                events_path = directory / "events.jsonl"
                if not events_path.is_file():
                    continue
                events = _events(events_path)
                if not events:
                    continue
                first = next((event for event in events if event.get("event") == "run_started"), {})
                detail = first.get("detail")
                if not isinstance(detail, dict) or detail.get("dashboard_job_id") != job["job_id"]:
                    continue
                matches.append((directory.name, events))
        except (OSError, ValueError, RuntimeError):
            return
        if len(matches) != 1:
            return
        run_id, events = matches[0]
        counts: dict[str, int | None] = {
            "navigation_action_count": None,
            "claim_action_count": None,
            "dismiss_action_count": None,
            "scroll_action_count": None,
            "batch_round_count": None,
            "confirmed_batch_round_count": None,
            "uncertain_batch_round_count": None,
        }
        counter_by_node = {
            "more": "navigation_action_count", "mail": "navigation_action_count",
            "bulk_read": "claim_action_count", "reward_close": "dismiss_action_count",
            "detail_close": "dismiss_action_count", "mail_close": "dismiss_action_count",
            "open_daily_tasks": "navigation_action_count", "open_daily_rewards": "navigation_action_count",
            "scan_daily_tasks": "scroll_action_count", "close_daily_tasks": "dismiss_action_count",
            "claim_daily_all": "claim_action_count", "dismiss_daily_reward": "dismiss_action_count",
            "dismiss_known_announcement": "dismiss_action_count",
        }
        submitted = 0
        batch_rounds = 0
        confirmed_batch_rounds = 0
        uncertain_batch_rounds = 0
        for event in events:
            if event.get("event") == "input_submitted":
                submitted += 1
                counter = counter_by_node.get(event.get("node"))
                if counter:
                    counts[counter] = (counts[counter] or 0) + 1
                if event.get("node") == "claim_daily_all":
                    batch_rounds += 1
            if event.get("event") == "daily_batch_round_rechecked":
                detail = event.get("detail")
                if isinstance(detail, dict):
                    if detail.get("round_result") == "confirmed":
                        confirmed_batch_rounds += 1
                    elif detail.get("round_result") == "uncertain":
                        uncertain_batch_rounds += 1
            if event.get("event") == "daily_batch_round_result":
                detail = event.get("detail")
                if isinstance(detail, dict) and detail.get("round_result") == "uncertain":
                    uncertain_batch_rounds += 1
        job["run_id"] = run_id
        job["progress"] = {
            "action_count": submitted,
            **counts,
            "batch_round_count": batch_rounds,
            "confirmed_batch_round_count": confirmed_batch_rounds,
            "uncertain_batch_round_count": uncertain_batch_rounds,
            "pending_batch_round_count": max(0, batch_rounds - confirmed_batch_rounds - uncertain_batch_rounds),
            "action_upper_bound": job["progress"].get("action_upper_bound"),
        }
        if not job.get("stop_requested"):
            job["stage"] = _stage_label(events[-1])

    def request_stop(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._active
            if job is None or job["job_id"] != job_id:
                latest = self._latest
                if latest and latest["job_id"] == job_id:
                    return self._public(latest) or {}
                raise ValueError("job not found")
            job["stop_controller"].request_stop()
            job["stop_requested"] = True
            job["status"] = "stop_requested"
            job["stage"] = "停止请求已发送，等待运行器确认"
            return self._public(job) or {}

    def request_shutdown_stop(self) -> None:
        with self._lock:
            job = self._active
            if job is not None:
                job["stop_controller"].request_stop()
                job["stop_requested"] = True
                job["status"] = "stop_requested"
                job["stage"] = "服务关闭时请求停止，等待运行器确认"
