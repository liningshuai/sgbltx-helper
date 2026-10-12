"""单任务运行器：状态控制、预算、停止、结果判定。

设计说明 4 的核心闭环在这里落地：
    读取当前画面 → 判断状态 → 执行操作 → 再次识别 → 确认结果

硬性规则（全部在代码里强制，而不是靠约定）：
  * 未验证的页面状态不执行动作 → ``assets_unverified``，零输入退出。
  * 缺少后置证据不得标 completed → 一律 ``needs_attention/outcome_uncertain``。
  * 无按钮不等于没有奖励 → 只有显式「已领」标志才允许 ``nothing_to_claim``。
  * 干运行走 ``ReadOnlyBackend``，任何输入调用直接抛异常。
  * 所有等待都有截止时间；停止确认后不再提交新任务。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .classification import Classification, classify
from .contracts import (
    CandidateAction,
    ConcurrentRunError,
    ContractError,
    DeviceError,
    InspectionResult,
    ReasonCode,
    RunMode,
    RunStatus,
    TaskRequest,
    TaskResult,
)
from .device import check_resolution, select_device
from .evidence import RunWorkspace, cleanup_expired, make_run_id, utc_now_iso
from .locking import RunLock, StopUnconfirmedError
from .daily_rewards import run_daily_rewards
from .mail_bulk import run_mail_bulk
from .maa_adapter import Backend, MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout, ReadOnlyBackend, validate_pipeline_bundle
from .pages import PageCatalog, load_page_catalog

# 视为真实输入的 Pipeline 动作（与 maa_adapter.NON_INPUT_ACTIONS 互补）
PIPELINE_INPUT_ACTIONS = frozenset(
    {
        "Click",
        "LongPress",
        "Swipe",
        "MultiSwipe",
        "ClickKey",
        "LongPressKey",
        "KeyDown",
        "KeyUp",
        "InputText",
        "StartApp",
        "StopApp",
        "Scroll",
        "TouchDown",
        "TouchMove",
        "TouchUp",
        "Shell",
        "Command",
        "Custom",
    }
)
_SAFE_PIPELINE_ACTIONS = frozenset({"DoNothing", "Click"})
_SAFE_PIPELINE_FIELDS = frozenset({"recognition", "action", "next", "repeat", "max_hit"})


class StopController:
    """停止标记。Ctrl+C 由 CLI 转成 ``request_stop()``。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request_stop(self) -> None:
        self._event.set()

    def should_stop(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)

    def reset(self) -> None:
        self._event.clear()


@dataclass
class PipelineBudget:
    """从 Pipeline 静态统计出的动作预算。"""

    entry: str
    declared_actions: list[str] = field(default_factory=list)
    visited_nodes: list[str] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.declared_actions)


def load_pipeline(path: Path) -> dict[str, Any]:
    if not Path(path).is_file():
        raise ContractError(f"Pipeline 文件不存在：{path}")
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"Pipeline 不是合法 JSON：{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError(f"Pipeline 根节点必须是对象：{path}")
    return raw


def statically_count_actions(pipeline: dict[str, Any], entry: str, max_visit: int = 200) -> PipelineBudget:
    """验证保守支持的有限 Pipeline，并以所有可达输入节点给出上界。

    仅接受 Maa 原生 schema、无循环、无汇合、每节点至多一个 next 的小型线性流程。
    每节点必须显式 repeat=1/max_hit=1；识别、动作参数和错误分支以外的扩展字段
    都在交给 Maa SDK 前拒绝。
    """
    if entry not in pipeline:
        raise ContractError(f"Pipeline 中不存在入口节点 {entry!r}")
    if len(pipeline) > max_visit:
        raise ContractError(f"Pipeline 节点数超过安全上限 {max_visit}")

    incoming: dict[str, int] = {str(name): 0 for name in pipeline}
    edges: dict[str, list[str]] = {}
    for name, node in pipeline.items():
        if not isinstance(name, str) or not name or not isinstance(node, dict):
            raise ContractError("Pipeline 节点必须是非空名称对应的对象")
        unsupported = set(node) - _SAFE_PIPELINE_FIELDS
        if unsupported:
            raise ContractError(f"节点 {name!r} 含不支持字段：{sorted(unsupported)}")
        if node.get("repeat") != 1:
            raise ContractError(f"节点 {name!r} 的 repeat 必须显式为 1")
        if node.get("max_hit") != 1:
            raise ContractError(f"节点 {name!r} 的 max_hit 必须显式为 1")

        recognition = node.get("recognition")
        if not isinstance(recognition, dict) or set(recognition) != {"type", "param"}:
            raise ContractError(f"节点 {name!r} 必须使用 Maa recognition.type/param 结构")
        if recognition.get("type") != "TemplateMatch" or not isinstance(recognition.get("param"), dict):
            raise ContractError(f"节点 {name!r} 只支持 Maa TemplateMatch 识别")
        reco_param = recognition["param"]
        if set(reco_param) != {"template", "roi", "threshold"}:
            raise ContractError(f"节点 {name!r} recognition.param 含不支持字段")
        templates = reco_param.get("template")
        if (not isinstance(templates, list) or len(templates) != 1
                or not isinstance(templates[0], str) or not templates[0].strip()):
            raise ContractError(f"节点 {name!r} 必须指定一个非空 template")
        roi = reco_param.get("roi")
        if not isinstance(roi, (list, tuple)) or len(roi) != 4 or any(type(v) is not int for v in roi):
            raise ContractError(f"节点 {name!r} 的 roi 必须是四个整数")
        threshold = reco_param.get("threshold")
        if (not isinstance(threshold, list) or len(threshold) != 1
                or not isinstance(threshold[0], (int, float)) or not 0 <= threshold[0] <= 1):
            raise ContractError(f"节点 {name!r} 的 threshold 必须是单元素 0 到 1 数组")

        action_data = node.get("action")
        if not isinstance(action_data, dict) or set(action_data) != {"type", "param"}:
            raise ContractError(f"节点 {name!r} 必须使用 Maa action.type/param 结构")
        action = action_data.get("type")
        params = action_data.get("param")
        if not isinstance(action, str) or action not in _SAFE_PIPELINE_ACTIONS or not isinstance(params, dict):
            raise ContractError(f"节点 {name!r} 含不支持动作：{action!r}")
        if action == "DoNothing" and params:
            raise ContractError(f"节点 {name!r} 的 DoNothing param 必须为空")
        if action == "Click" and params != {"target": True}:
            raise ContractError(f"节点 {name!r} 的 Click 只能点击当前识别框")
        next_value = node.get("next", [])
        if (not isinstance(next_value, list) or len(next_value) > 1
                or any(not isinstance(item, dict) or set(item) != {"name"} or not isinstance(item["name"], str)
                       for item in next_value)):
            raise ContractError(f"节点 {name!r} 的 Maa next 只支持至多一个 {{name}} 节点")
        edges[name] = [item["name"] for item in next_value]
        for child in edges[name]:
            if child not in pipeline:
                raise ContractError(f"节点 {name!r} 指向不存在的节点 {child!r}")
            incoming[child] += 1

    if any(count > 1 for count in incoming.values()):
        raise ContractError("Pipeline 不支持汇合分支")

    budget = PipelineBudget(entry=entry)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ContractError(f"Pipeline 存在循环：{name!r}")
        if name in visited:
            raise ContractError(f"Pipeline 节点可能被重复访问：{name!r}")
        visiting.add(name)
        budget.visited_nodes.append(name)
        action = str(pipeline[name]["action"]["type"])
        if action in PIPELINE_INPUT_ACTIONS:
            budget.declared_actions.append(action)
        for child in edges[name]:
            visit(child)
        visiting.remove(name)
        visited.add(name)

    visit(entry)
    if len(visited) != len(pipeline):
        raise ContractError("Pipeline 含不可达节点")
    return budget


def _log(ws: RunWorkspace, seq: int, node: str, event: str, detail: dict[str, Any] | None = None) -> int:
    ws.log_event(seq, node, event, detail or {})
    return seq + 1


def _result(
    ws: RunWorkspace,
    request: TaskRequest,
    run_id: str,
    status: RunStatus,
    reason: ReasonCode,
    started_at: str,
    action_count: int,
    detail: dict[str, Any] | None = None,
    evidence: list[str] | None = None,
) -> TaskResult:
    result = TaskResult(
        run_id=run_id,
        task_id=request.task_id,
        account_alias=request.profile.account_alias,
        device_serial=request.profile.serial,
        status=status,
        reason_code=reason,
        started_at=started_at,
        finished_at=utc_now_iso(),
        action_count=action_count,
        evidence_paths=evidence if evidence is not None else [str(ws.root)],
        mode=request.mode,
        detail=detail or {},
    )
    ws.write_json(ws.result_path, result.to_dict())
    ws.log_event(9999, "finish", "run_finished", {"status": str(status), "reason": str(reason)})
    return result


# ------------------------------------------------------------------ 主流程
def run(
    request: TaskRequest,
    backend: Backend,
    lock_dir: Path,
    project_root: Path,
    stop: StopController | None = None,
    include_candidates: bool = True,
    *,
    dashboard_job_id: str | None = None,
) -> TaskResult | InspectionResult:
    """执行一轮运行。

    dry-run 返回 ``InspectionResult``；execute 返回 ``TaskResult``。
    两者**不共用类型**，避免干运行被误读成领取成功。
    """
    request.validate()
    deadline = time.monotonic() + request.timeout_seconds
    stop = stop or StopController()
    run_id = make_run_id("dryrun" if request.mode is RunMode.DRY_RUN else "run")
    started_at = utc_now_iso()
    artifacts_root = Path(project_root) / request.artifacts_root
    resource_root = Path(project_root) / request.resource_root
    if request.task_id == "mail_bulk_claim":
        resource_root = resource_root / "mail_bulk"
    elif request.task_id == "daily_rewards_claim":
        resource_root = resource_root / "daily_rewards"
    ws = RunWorkspace.create(artifacts_root, run_id)
    seq = 0

    cleanup_expired(artifacts_root, request.retention_days)

    lock = RunLock(lock_dir, request.profile.serial, request.profile.account_alias)
    try:
        lock.acquire(run_id)
    except ConcurrentRunError as exc:
        unconfirmed = isinstance(exc, StopUnconfirmedError)
        reason = ReasonCode.STOP_UNCONFIRMED if unconfirmed else ReasonCode.CONCURRENT_RUN
        _log(
            ws, seq, "lock",
            "device_blocked_stop_unconfirmed" if unconfirmed else "concurrent_run_rejected",
            {"message": str(exc)},
        )
        return _result(
            ws, request, run_id,
            RunStatus.NEEDS_ATTENTION if unconfirmed else RunStatus.FAILED,
            reason, started_at, 0, {"message": str(exc)},
        )

    # 干运行强制只读包装：任何输入调用都会抛异常，而不是静默发生
    effective_backend: Backend = ReadOnlyBackend(backend) if request.mode is RunMode.DRY_RUN else backend

    try:
        started_detail = {
            "mode": str(request.mode),
            "profile": request.profile.describe(),
            # 把「是否套了只读护栏」写进事件流，使干运行的结构性保证可被审计
            "input_guard": type(effective_backend).__name__,
            "input_guard_active": isinstance(effective_backend, ReadOnlyBackend),
        }
        if isinstance(dashboard_job_id, str) and dashboard_job_id:
            started_detail["dashboard_job_id"] = dashboard_job_id
        seq = _log(
            ws, seq, "start", "run_started",
            started_detail,
        )

        # 0) 明确设备选择：不默认第一台，档案未指定就拒绝（A2）
        try:
            devices = effective_backend.discover_devices(deadline, stop.should_stop)
            selected = select_device(request.profile, devices)
        except DeviceError as exc:
            _log(
                ws, seq, "device", "device_selection_failed",
                {"reason": str(exc.reason_code), "message": str(exc)},
            )
            return _result(
                ws, request, run_id, RunStatus.NEEDS_ATTENTION, exc.reason_code, started_at, 0,
                {"message": str(exc)},
            )
        seq = _log(
            ws, seq, "device", "device_selected",
            {"address": selected.address, "name": selected.name, "candidates": len(devices)},
        )

        # 1) 连接（不是输入动作）
        try:
            effective_backend.connect(deadline, stop.should_stop)
        except MaaStopped as exc:
            _log(ws, seq, "connect", "stopped", {"message": str(exc)})
            return _result(ws, request, run_id, RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, started_at, 0)
        except MaaTimeout as exc:
            _log(ws, seq, "connect", "timeout", {"message": str(exc)})
            return _result(ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, started_at, 0)
        except (MaaError, OSError) as exc:
            _log(ws, seq, "connect", "connect_failed", {"message": str(exc)})
            return _result(
                ws, request, run_id, RunStatus.FAILED, ReasonCode.CONNECT_FAILED, started_at, 0,
                {"message": str(exc)},
            )

        # 2) 先截图，再核对分辨率（截图不是输入动作）
        try:
            image = effective_backend.screencap(deadline, stop.should_stop)
        except MaaStopped as exc:
            _log(ws, seq, "screencap", "stopped", {"message": str(exc)})
            return _result(ws, request, run_id, RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, started_at, 0)
        except MaaTimeout as exc:
            _log(ws, seq, "screencap", "timeout", {"message": str(exc)})
            return _result(ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, started_at, 0)
        except MaaError as exc:
            _log(ws, seq, "screencap", "screencap_failed", {"message": str(exc)})
            return _result(
                ws, request, run_id, RunStatus.FAILED, ReasonCode.CONNECT_FAILED, started_at, 0,
                {"message": str(exc)},
            )

        actual_res = effective_backend.raw_resolution()
        try:
            check_resolution(request.profile, actual_res)
        except DeviceError as exc:
            _log(ws, seq, "resolution", "mismatch", {"actual": list(actual_res), "message": str(exc)})
            before_path = ws.save_image("before", image)
            return _result(
                ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.RESOLUTION_MISMATCH, started_at, 0,
                {"actual_resolution": list(actual_res), "message": str(exc), "before": str(before_path)},
            )

        before_path = ws.save_image("before", image)
        seq = _log(
            ws, seq, "screencap", "before_saved",
            {"path": str(before_path), "resolution": list(actual_res)},
        )

        # 3) 加载资源
        try:
            nodes = effective_backend.load_resource(resource_root, deadline, stop.should_stop, request.task_id)
        except MaaStopped as exc:
            _log(ws, seq, "resource", "stopped", {"message": str(exc)})
            return _result(ws, request, run_id, RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, started_at, 0)
        except MaaTimeout as exc:
            _log(ws, seq, "resource", "timeout", {"message": str(exc)})
            return _result(ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, started_at, 0)
        except MaaError as exc:
            _log(ws, seq, "resource", "load_failed", {"message": str(exc)})
            return _result(
                ws, request, run_id, RunStatus.FAILED, ReasonCode.RESOURCE_LOAD_FAILED, started_at, 0,
                {"message": str(exc)},
            )

        try:
            catalog = load_page_catalog(resource_root / "pages.json")
        except ContractError as exc:
            _log(ws, seq, "resource", "pages_invalid", {"message": str(exc)})
            return _result(
                ws, request, run_id, RunStatus.FAILED, ReasonCode.PROFILE_INVALID, started_at, 0,
                {"message": str(exc)},
            )

        if tuple(catalog.resolution) != tuple(request.profile.expected_resolution):
            _log(
                ws, seq, "resource", "catalog_resolution_mismatch",
                {"catalog": list(catalog.resolution), "profile": list(request.profile.expected_resolution)},
            )
            return _result(
                ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.RESOLUTION_MISMATCH, started_at, 0,
                {"message": "页面规格声明的分辨率与档案不一致"},
            )

        if request.task_id in {"mail_bulk_claim", "daily_rewards_claim"}:
            try:
                validate_pipeline_bundle(resource_root, request.task_id)
                task_pipeline = load_pipeline(resource_root / "pipeline" / f"{request.task_id}.json")
                task_budget = statically_count_actions(task_pipeline, catalog.entry_node)
                if task_budget.action_count != 0:
                    raise ContractError(f"{request.task_id} 资源 Pipeline 必须保持零输入；输入由受限控制器逐步提交")
            except (ContractError, MaaError) as exc:
                _log(ws, seq, "resource", "task_pipeline_invalid", {"task_id": request.task_id, "message": str(exc)})
                return _result(
                    ws, request, run_id, RunStatus.FAILED, ReasonCode.PROFILE_INVALID,
                    started_at, 0, {"message": str(exc)},
                )

        # 4) 分类当前页面（含有限只读重试）
        cls, image, seq = _classify_with_recovery(
            effective_backend, catalog, request, ws, stop, seq, image, deadline
        )

        if request.task_id == "mail_bulk_claim":
            try:
                flow_config = json.loads((resource_root / "mail_bulk.json").read_text(encoding="utf-8"))
                if not isinstance(flow_config, dict):
                    raise ContractError("mail_bulk.json 根节点必须是对象")
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                _log(ws, seq, "resource", "mail_bulk_config_invalid", {"message": str(exc)})
                return _result(
                    ws, request, run_id, RunStatus.FAILED, ReasonCode.PROFILE_INVALID,
                    started_at, 0, {"message": str(exc)},
                )
            return run_mail_bulk(
                request=request, backend=effective_backend, catalog=catalog, config=flow_config,
                workspace=ws, run_id=run_id, started_at=started_at,
                initial_image=image, before_path=before_path, seq=seq, deadline=deadline, stop=stop,
            )

        if request.task_id == "daily_rewards_claim":
            try:
                flow_config = json.loads((resource_root / "daily_rewards.json").read_text(encoding="utf-8"))
                if not isinstance(flow_config, dict):
                    raise ContractError("daily_rewards.json 根节点必须是对象")
                bounds = {
                    "max_daily_pages": (flow_config.get("max_daily_pages"), 1, 20),
                    "max_claims": (flow_config.get("max_claims"), 1, request.max_actions),
                    "max_reward_popups": (flow_config.get("max_reward_popups"), 1, 10),
                }
                for name, (value, low, high) in bounds.items():
                    if type(value) is not int or not low <= value <= high:
                        raise ContractError(f"{name} 必须是 {low} 到 {high} 的整数")
                wait_seconds = flow_config.get("transition_wait_seconds")
                if (isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float))
                        or not 0 < wait_seconds <= min(30, request.timeout_seconds)):
                    raise ContractError("transition_wait_seconds 必须是大于 0 且不超过整轮超时/30 秒的数字")
                scroll = flow_config.get("list_scroll")
                if scroll is not None:
                    if not isinstance(scroll, dict):
                        raise ContractError("list_scroll 必须是对象")
                    coords = [scroll.get(key) for key in ("x1", "y1", "x2", "y2", "duration_ms")]
                    if any(type(value) is not int for value in coords):
                        raise ContractError("list_scroll 坐标和 duration_ms 必须为整数")
                    x1, y1, x2, y2, duration_ms = coords
                    viewport = scroll.get("container_roi")
                    if (not isinstance(viewport, list) or len(viewport) != 4
                            or any(type(value) is not int for value in viewport)):
                        raise ContractError("list_scroll.container_roi 必须是整数 [x,y,w,h]")
                    vx, vy, vw, vh = viewport
                    if (not scroll.get("verified") or not 100 <= duration_ms <= 2000
                            or not (0 <= vx < 1080 and 0 <= vy < 1920 and 0 < vw <= 1080 - vx and 0 < vh <= 1920 - vy)
                            or not (vx <= x1 < vx + vw and vx <= x2 < vx + vw
                                    and vy <= y1 < vy + vh and vy <= y2 < vy + vh)
                            or (x1, y1) == (x2, y2)):
                        raise ContractError("list_scroll 未验证或手势参数越界")
            except (OSError, UnicodeError, json.JSONDecodeError, ContractError) as exc:
                _log(ws, seq, "resource", "daily_rewards_config_invalid", {"message": str(exc)})
                return _result(
                    ws, request, run_id, RunStatus.FAILED, ReasonCode.PROFILE_INVALID,
                    started_at, 0, {"message": str(exc)},
                )
            return run_daily_rewards(
                request=request, backend=effective_backend, catalog=catalog, config=flow_config,
                workspace=ws, run_id=run_id, started_at=started_at,
                initial_image=image, before_path=before_path, seq=seq, deadline=deadline, stop=stop,
            )

        if request.mode is RunMode.DRY_RUN:
            inspection = _build_inspection(
                request, run_id, ws, cls, before_path, nodes, seq, stop, include_candidates
            )
            ws.write_json(ws.inspection_path, inspection.to_dict())
            return inspection

        return _execute(
            request=request,
            backend=effective_backend,
            catalog=catalog,
            cls=cls,
            ws=ws,
            seq=seq,
            run_id=run_id,
            started_at=started_at,
            resource_root=resource_root,
            stop=stop,
            before_path=before_path,
            deadline=deadline,
        )
    except MaaStopUnconfirmed as exc:
        marker = lock.mark_stop_unconfirmed(run_id, str(exc))
        _log(ws, seq, "stop", "stop_unconfirmed", {"message": str(exc), "device_block": str(marker)})
        run_context = dict(getattr(exc, "run_context", {}) or {})
        action_count = int(run_context.pop("action_count", 0))
        return _result(
            ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.STOP_UNCONFIRMED,
            started_at, action_count,
            {**run_context, "message": str(exc), "device_block": str(marker)},
        )
    except MaaStopped as exc:
        _log(ws, seq, "run", "stopped", {"message": str(exc)})
        return _result(ws, request, run_id, RunStatus.CANCELLED, ReasonCode.USER_CANCELLED, started_at, 0)
    except MaaTimeout as exc:
        _log(ws, seq, "run", "timeout", {"message": str(exc)})
        return _result(ws, request, run_id, RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, started_at, 0)
    finally:
        try:
            effective_backend.close()
        except Exception:  # noqa: BLE001
            pass
        lock.release()


def _classify_with_recovery(
    backend: Backend,
    catalog: PageCatalog,
    request: TaskRequest,
    ws: RunWorkspace,
    stop: StopController,
    seq_start: int,
    first_image: np.ndarray,
    deadline: float,
) -> tuple[Classification, np.ndarray, int]:
    """分类当前页面，允许有限的只读重试（不产生任何输入）。"""
    image = first_image
    seq = seq_start
    attempts = 0
    while True:
        cls = classify(backend, image, catalog, deadline, stop.should_stop)
        seq = _log(
            ws, seq, "classify", "classified",
            {
                "attempt": attempts,
                "page_id": cls.page.page_id if cls.page else None,
                "blockers": [b.page_id for b in cls.blockers],
                "ambiguous": cls.ambiguous_pages,
                "claimable_hits": [o.name for o in cls.claimable if o.hit],
                "already_claimed_hits": [o.name for o in cls.already_claimed if o.hit],
            },
        )
        if cls.page is not None or cls.blockers or cls.ambiguous_pages:
            return cls, image, seq
        if attempts >= request.max_recoveries or stop.should_stop() or time.monotonic() >= deadline:
            return cls, image, seq
        attempts += 1
        if stop.wait(min(0.5, max(0.0, deadline - time.monotonic()))):
            return cls, image, seq
        image = backend.screencap(deadline, stop.should_stop)
        seq = _log(ws, seq, "recover", "rescreen", {"attempt": attempts})


# ------------------------------------------------------------------ 干运行
def _build_inspection(
    request: TaskRequest,
    run_id: str,
    ws: RunWorkspace,
    cls: Classification,
    before_path: Path,
    nodes: list[str],
    seq: int,
    stop: StopController,
    include_candidates: bool = True,
) -> InspectionResult:
    blockers: list[str] = []
    notes: list[str] = ["干运行只分析当前页，不自动导航、不发出任何输入。"]

    if stop.should_stop():
        blockers.append("用户已请求停止")
    for b in cls.blockers:
        blockers.append(f"命中阻断页 {b.page_id}（{b.reason_code or 'blocker'}），需要人工处理。")
    if cls.ambiguous_pages:
        blockers.append(f"页面身份不唯一：{', '.join(cls.ambiguous_pages)}")
    if cls.page is None and not cls.blockers and not cls.ambiguous_pages:
        blockers.append("当前画面不是任何已登记页面；请人工切换到目标页面后重试。")

    candidates: list[CandidateAction] = []
    if cls.page is not None and include_candidates:
        for action in cls.page.allowed_actions:
            blocked: str | None = None
            if not action.verified:
                blocked = "该动作尚未经实测验证，execute 会拒绝执行"
            elif not cls.has_claimable:
                blocked = "当前页未识别到可领取目标"
            candidates.append(
                CandidateAction(
                    action_id=action.action_id,
                    description=action.description,
                    verified=action.verified,
                    blocked_reason=blocked,
                )
            )

    if not candidates:
        notes.append("没有可列出的候选动作。" if include_candidates else "本次只记录页面状态，未评估候选动作。")

    ws.log_event(seq, "inspect", "inspection_built", {"page_id": cls.page.page_id if cls.page else None})

    return InspectionResult(
        run_id=run_id,
        task_id=request.task_id,
        account_alias=request.profile.account_alias,
        device_serial=request.profile.serial,
        observed_at=utc_now_iso(),
        page_id=cls.page.page_id if cls.page else None,
        observations=list(cls.observations) + list(cls.claimable) + list(cls.already_claimed),
        candidate_actions=candidates,
        blockers=blockers,
        evidence_paths=[str(before_path), str(ws.events_path), str(ws.inspection_path)],
        notes=notes + [f"资源包含节点 {len(nodes)} 个"],
    )


# ------------------------------------------------------------------ execute
def _execute(
    request: TaskRequest,
    backend: Backend,
    catalog: PageCatalog,
    cls: Classification,
    ws: RunWorkspace,
    seq: int,
    run_id: str,
    started_at: str,
    resource_root: Path,
    stop: StopController,
    before_path: Path,
    deadline: float,
) -> TaskResult:
    def finish(
        status: RunStatus,
        reason: ReasonCode,
        detail: dict[str, Any] | None = None,
        actions: int = 0,
        evidence: list[str] | None = None,
    ) -> TaskResult:
        return _result(ws, request, run_id, status, reason, started_at, actions, detail, evidence)

    # --- 前置闸门：全部零输入 -------------------------------------------
    if stop.should_stop():
        _log(ws, seq, "gate", "stopped_before_action", {})
        return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED)

    if cls.blockers:
        page = cls.blockers[0]
        _log(ws, seq, "gate", "blocker_page", {"page_id": page.page_id, "reason": page.reason_code})
        try:
            reason = ReasonCode(page.reason_code) if page.reason_code else ReasonCode.UNKNOWN_PAGE
        except ValueError:
            reason = ReasonCode.UNKNOWN_PAGE
        return finish(
            RunStatus.NEEDS_ATTENTION,
            reason,
            {"blocker_page": page.page_id, "reason_code": page.reason_code, "message": "命中阻断页，转人工"},
        )

    if cls.ambiguous_pages:
        _log(ws, seq, "gate", "ambiguous_page", {"pages": cls.ambiguous_pages})
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.UNKNOWN_PAGE, {"ambiguous": cls.ambiguous_pages})

    if cls.page is None:
        _log(ws, seq, "gate", "unknown_page", {})
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.UNKNOWN_PAGE,
            {"message": "当前画面未识别为已登记页面，未执行任何动作"},
        )

    page = cls.page
    if not catalog.target_page_id or page.page_id != catalog.target_page_id:
        _log(ws, seq, "gate", "wrong_page", {"page_id": page.page_id, "target": catalog.target_page_id})
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.UNKNOWN_PAGE,
            {"page_id": page.page_id, "target_page_id": catalog.target_page_id, "message": "不在目标页面"},
        )

    if not page.verified:
        _log(ws, seq, "gate", "page_unverified", {"page_id": page.page_id})
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.ASSETS_UNVERIFIED,
            {"page_id": page.page_id, "message": "该页面的识别素材尚未经实测验证，拒绝执行动作"},
        )

    unverified = [m.template for m in page.claimable + page.already_claimed if not m.verified]
    if unverified:
        _log(ws, seq, "gate", "claim_assets_unverified", {"templates": unverified})
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.ASSETS_UNVERIFIED,
            {"templates": unverified, "message": "领取相关模板尚未验证，拒绝执行动作"},
        )

    # 无按钮 ≠ 没有奖励：只有显式「已领」标志才允许 nothing_to_claim
    if not cls.has_claimable:
        if cls.has_already_claimed_marker:
            _log(ws, seq, "gate", "already_claimed", {"page_id": page.page_id})
            return finish(RunStatus.NOTHING_TO_CLAIM, ReasonCode.NOTHING_TO_CLAIM, {"page_id": page.page_id})
        _log(ws, seq, "gate", "state_unverifiable", {"page_id": page.page_id})
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.OUTCOME_UNCERTAIN,
            {"page_id": page.page_id, "message": "页面身份成立但既无可领标志也无已领标志，状态不可判定"},
        )

    # Pipeline 静态预算检查（执行前）
    pipeline_path = resource_root / "pipeline" / f"{request.task_id}.json"
    try:
        validate_pipeline_bundle(resource_root, request.task_id)
        pipeline = load_pipeline(pipeline_path)
        budget = statically_count_actions(pipeline, catalog.entry_node)
    except (ContractError, MaaError) as exc:
        _log(ws, seq, "gate", "pipeline_invalid", {"message": str(exc)})
        return finish(RunStatus.FAILED, ReasonCode.PROFILE_INVALID, {"message": str(exc)})

    if budget.action_count > request.max_actions:
        _log(
            ws, seq, "gate", "action_budget_exceeded",
            {"declared": budget.action_count, "max_actions": request.max_actions},
        )
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.ACTION_BUDGET_EXHAUSTED,
            {"declared_actions": budget.declared_actions, "max_actions": request.max_actions},
        )

    _log(
        ws, seq, "gate", "gate_passed",
        {"page_id": page.page_id, "declared_actions": budget.action_count, "nodes": budget.visited_nodes},
    )

    if stop.should_stop():
        return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED)
    if time.monotonic() >= deadline:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                      {"message": "提交 Pipeline 前整轮截止时间已到"})

    # --- 执行 -----------------------------------------------------------
    try:
        summary = backend.run_task(catalog.entry_node, deadline, stop.should_stop)
    except MaaStopUnconfirmed:
        raise
    except MaaStopped:
        _log(ws, seq, "task", "stopped_during_task", {})
        return finish(
            RunStatus.CANCELLED,
            ReasonCode.USER_CANCELLED,
            {"message": "停止确认；已发送的动作可能已完成，无法回滚"},
        )
    except MaaTimeout:
        _log(ws, seq, "task", "task_timeout", {"timeout_seconds": request.timeout_seconds})
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT, {"timeout_seconds": request.timeout_seconds})
    except MaaError as exc:
        _log(ws, seq, "task", "task_error", {"message": str(exc)})
        return finish(RunStatus.FAILED, ReasonCode.SDK_ERROR, {"message": str(exc)})

    if time.monotonic() >= deadline:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                      {"message": "Pipeline 结束时已超过整轮截止时间，结果不确定"}, summary.action_count)

    action_count = summary.action_count
    action_count_is_upper_bound = not summary.audit_available
    if action_count_is_upper_bound:
        # 预算已在 SDK 前证明；若 SDK 结果详情缺失，报告该保守上界，不伪报 0 次输入。
        action_count = budget.action_count
    _log(
        ws, seq, "task", "task_finished",
        {
            "entry": summary.entry,
            "succeeded": summary.succeeded,
            "stopped": summary.stopped,
            "input_actions": summary.input_actions,
            "audit_available": summary.audit_available,
            "action_count_upper_bound": budget.action_count if action_count_is_upper_bound else None,
            "nodes": summary.node_names,
        },
    )

    if summary.stopped and summary.stop_confirmed is not True:
        raise MaaStopUnconfirmed("SDK 返回 stopped，但没有 stop job 与原任务 job 均完成的证据")

    if summary.stopped or stop.should_stop():
        return finish(
            RunStatus.CANCELLED,
            ReasonCode.USER_CANCELLED,
            {
                "input_actions": summary.input_actions,
                "action_count_is_upper_bound": action_count_is_upper_bound,
                "message": "用户停止；已发送的动作可能已完成，无法回滚",
            },
            action_count,
        )

    if action_count > request.max_actions:
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.ACTION_BUDGET_EXHAUSTED,
            {"action_count": action_count, "max_actions": request.max_actions},
            action_count,
        )

    # --- 后置验证 -------------------------------------------------------
    try:
        after_image = backend.screencap(deadline, stop.should_stop)
    except MaaStopped as exc:
        return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                      {"message": f"动作后截图期间收到停止；领取结果不确定：{exc}"}, action_count)
    except MaaTimeout as exc:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                      {"message": f"动作后截图超出整轮截止时间，结果不确定：{exc}"}, action_count)
    except MaaError as exc:
        _log(ws, seq, "verify", "after_screencap_failed", {"message": str(exc)})
        return finish(
            RunStatus.NEEDS_ATTENTION,
            ReasonCode.OUTCOME_UNCERTAIN,
            {"message": f"动作已发出但无法取得后置截图：{exc}"},
            action_count,
        )

    after_path = ws.save_image("after", after_image)
    evidence = [str(before_path), str(after_path), str(ws.events_path), str(ws.result_path)]
    try:
        after_cls = classify(backend, after_image, catalog, deadline, stop.should_stop)
    except MaaStopped:
        return finish(RunStatus.CANCELLED, ReasonCode.USER_CANCELLED,
                      {"message": "后置识别期间收到停止；结果不确定"}, action_count, evidence)
    except MaaTimeout:
        return finish(RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT,
                      {"message": "后置识别超出整轮截止时间，结果不确定"}, action_count, evidence)
    _log(
        ws, seq, "verify", "after_classified",
        {
            "path": str(after_path),
            "page_id": after_cls.page.page_id if after_cls.page else None,
            "claimable_hits": [o.name for o in after_cls.claimable if o.hit],
            "already_claimed_hits": [o.name for o in after_cls.already_claimed if o.hit],
        },
    )

    if after_cls.blockers:
        return finish(
            RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
            {"blocker_page": after_cls.blockers[0].page_id, "message": "动作后出现阻断页，结果不确定"},
            action_count, evidence,
        )

    if after_cls.page is None:
        return finish(
            RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
            {"message": "动作后无法确认页面身份，结果不确定"},
            action_count, evidence,
        )

    if after_cls.has_claimable:
        return finish(
            RunStatus.NEEDS_ATTENTION, ReasonCode.POSTCONDITION_NOT_MET,
            {"message": "动作后仍识别到可领取目标，未取得领取证据"},
            action_count, evidence,
        )

    target_page = catalog.by_id(catalog.target_page_id)
    if after_cls.page.page_id != catalog.target_page_id:
        return finish(
            RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
            {"page_id": after_cls.page.page_id, "target_page_id": catalog.target_page_id,
             "message": "动作后页面不属于本任务目标页面，不能确认领取结果"},
            action_count, evidence,
        )

    hit_post_templates = {
        observation.name.rsplit(":", 1)[-1]
        for observation in after_cls.already_claimed if observation.hit
    }
    verified_post_templates = {
        matcher.template for matcher in (target_page.already_claimed if target_page else []) if matcher.verified
    }
    if hit_post_templates and not hit_post_templates.issubset(verified_post_templates):
        return finish(
            RunStatus.NEEDS_ATTENTION, ReasonCode.ASSETS_UNVERIFIED,
            {"postcondition": sorted(hit_post_templates), "message": "后置已领素材未经验证"},
            action_count, evidence,
        )

    if after_cls.has_already_claimed_marker:
        return finish(
            RunStatus.COMPLETED, ReasonCode.CLAIM_CONFIRMED,
            {
                "message": "动作后可领目标消失且识别到已领取标志",
                "postcondition": [o.name for o in after_cls.already_claimed if o.hit],
                "input_actions": summary.input_actions,
                "action_count_is_upper_bound": action_count_is_upper_bound,
            },
            action_count, evidence,
        )

    return finish(
        RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN,
        {"message": "动作后可领目标消失但缺少已领取标志，无法确认领取结果"},
        action_count, evidence,
    )
