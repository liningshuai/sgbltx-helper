"""Maa 适配层：把锁定版本的官方 Python 绑定隔离在这一层。

本文件里的 SDK 名称全部来自**实际安装版本**（MaaFw 5.14.2 / 原生库 v5.14.2）
的源码与运行结果，不凭记忆拼造。核实记录见 ``docs/implementation-notes.md``。

已实测确认（见 tools/probe_recognition.py）：
  * ``Resource().post_bundle(dir)`` 加载资源；``pipeline/`` 目录必须至少含一个
    合法 JSON，否则整体加载失败。
  * ``Tasker`` 只绑 Resource（不绑 Controller）即可 ``post_recognition``，
    因此只读识别天然不需要设备，也不需要发出任何输入。
  * ``post_recognition(...)`` → ``TaskJob``；``.wait().get()`` → ``TaskDetail``；
    ``TaskDetail.nodes[i].recognition`` → ``RecognitionDetail``，含 ``hit`` /
    ``box``(Rect) / ``best_result``(BoxAndScoreResult)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .device import AdbDeviceInfo

# 真实输入动作集合：截图与识别不计入操作动作数（设计说明 8）。
NON_INPUT_ACTIONS = frozenset({"DoNothing", "StopTask", "Screencap"})


class MaaError(RuntimeError):
    """适配层统一异常。"""


class MaaTimeout(MaaError):
    """等待 SDK 作业超过截止时间。"""


class MaaStopped(MaaError):
    """运行期间收到停止请求。"""


class MaaStopUnconfirmed(MaaError):
    """停止请求或停止完成确认失败；设备必须保持禁用。"""


STOP_CONFIRMATION_SECONDS = 10.0


@dataclass
class RecognitionOutcome:
    """一次模板匹配的结果。"""

    hit: bool
    score: float | None = None
    box: tuple[int, int, int, int] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def center(self) -> tuple[int, int] | None:
        if self.box is None:
            return None
        x, y, w, h = self.box
        return (x + w // 2, y + h // 2)


@dataclass
class TaskRunSummary:
    """一次 MaaFramework 任务的执行摘要。"""

    entry: str
    succeeded: bool
    task_id: int
    node_names: list[str] = field(default_factory=list)
    input_actions: list[str] = field(default_factory=list)
    raw_nodes: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False
    audit_available: bool = True
    stop_confirmed: bool | None = None

    @property
    def action_count(self) -> int:
        return len(self.input_actions)


class Backend(Protocol):
    """运行器依赖的最小后端接口。真实实现与测试替身都满足它。"""

    def discover_devices(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> list["AdbDeviceInfo"]: ...

    def connect(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> None: ...

    def screencap(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> np.ndarray: ...

    def raw_resolution(self) -> tuple[int, int]: ...

    def load_resource(self, resource_root: Path, deadline: float | None = None, should_stop: Callable[[], bool] | None = None, task_id: str | None = None) -> list[str]: ...

    def recognize(
        self,
        image: np.ndarray,
        template: str,
        roi: tuple[int, int, int, int] = (0, 0, 0, 0),
        threshold: float = 0.85,
        deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> RecognitionOutcome: ...

    def click(
        self, x: int, y: int, deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> bool: ...

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int,
        deadline: float | None = None, should_stop: Callable[[], bool] | None = None,
    ) -> bool: ...

    def run_task(self, entry: str, deadline: float, should_stop: Callable[[], bool]) -> TaskRunSummary: ...

    def request_stop(self) -> Any: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------- 工具
def wait_with_deadline(
    job: Any,
    deadline: float,
    should_stop: Callable[[], bool] | None = None,
    poll_interval: float = 0.05,
) -> None:
    """轮询等待 SDK 作业，带可验证的截止时间与停止检查。

    MaaFw 的 ``Job.wait()`` 没有超时参数，会无限阻塞；这里改为轮询 ``status``，
    使「所有等待都有截止时间」成为可验证的性质。
    """
    while True:
        if should_stop is not None and should_stop():
            raise MaaStopped("等待期间收到停止请求")
        if time.monotonic() >= deadline:
            raise MaaTimeout(f"等待 SDK 作业超时（{poll_interval}s 轮询）")
        if job.done:
            return
        time.sleep(poll_interval)


def validate_pipeline_bundle(resource_root: Path, task_id: str) -> Path:
    """确保 Python 预算器与 Maa 实际载入的 Pipeline 文件集一致。

    Maa 会合并 default_pipeline JSON/JSONC 和资源包里的额外 Pipeline JSON/JSONC；目前无法
    给这些继承/合并语义提供可靠输入上界，因此执行阶段只允许一个任务 JSON。
    """
    resource_root = Path(resource_root).resolve()
    if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
        raise MaaError(f"非法 task_id，拒绝执行 Pipeline：{task_id!r}")
    pipeline_dir = resource_root / "pipeline"
    expected = (pipeline_dir / f"{task_id}.json").resolve()
    if not expected.is_file():
        raise MaaError(f"Pipeline 资源缺少任务文件：{expected}")
    defaults = [
        path for path in resource_root.rglob("*")
        if path.is_file() and path.name.lower() in {"default_pipeline.json", "default_pipeline.jsonc"}
    ]
    if defaults:
        raise MaaError(f"资源包含 default_pipeline JSON/JSONC，存在未计入预算的继承配置：{defaults[0]}")
    pipeline_files = {
        path.resolve()
        for path in pipeline_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonc"}
    }
    if pipeline_files != {expected}:
        extras = sorted(str(path) for path in pipeline_files - {expected})
        raise MaaError(f"资源包含额外或嵌套 Pipeline 文件，无法证明输入上界：{extras}")
    return expected


def _first_recognition(task_detail: Any) -> Any:
    """从 TaskDetail 中取出第一个非空 RecognitionDetail。"""
    if task_detail is None:
        return None
    for node in getattr(task_detail, "nodes", []) or []:
        reco = getattr(node, "recognition", None)
        if reco is not None:
            return reco
    return None


def _reco_to_outcome(reco: Any) -> RecognitionOutcome:
    if reco is None:
        return RecognitionOutcome(hit=False)
    box = getattr(reco, "box", None)
    score: float | None = None
    best = getattr(reco, "best_result", None)
    if best is not None:
        score = float(getattr(best, "score", 0.0))
    elif getattr(reco, "all_results", None):
        try:
            score = float(reco.all_results[0].score)
        except Exception:  # noqa: BLE001
            score = None
    box_tuple = None
    if box is not None:
        box_tuple = (int(box.x), int(box.y), int(box.w), int(box.h))
    return RecognitionOutcome(
        hit=bool(getattr(reco, "hit", False)),
        score=score,
        box=box_tuple,
        raw=getattr(reco, "raw_detail", {}) or {},
    )


# ---------------------------------------------------------------- 真实后端
class MaaBackend:
    """基于 MaaFramework 官方 Python 绑定的真实后端。"""

    def __init__(
        self,
        adb_path: str,
        address: str,
        screencap_methods: int | None = None,
        input_methods: int | None = None,
        log_dir: Path | None = None,
        stdout_level: int | None = None,
        screenshot_raw_size: bool = True,
    ) -> None:
        self.adb_path = adb_path
        self.address = address
        self._screencap_methods = screencap_methods
        self._input_methods = input_methods
        self._log_dir = log_dir
        self._stdout_level = stdout_level
        # 本机实测：MaaFramework 默认把截图缩放到短边 720，1080x1920 会变成 720x1280。
        # 本项目锁定单一分辨率并逐次核对，因此改用原始尺寸，让模板与坐标 1:1 对应
        # 设备像素，避免缩放引入的坐标换算误差。
        self._screenshot_raw_size = screenshot_raw_size

        self.controller: Any = None
        self.resource: Any = None
        self.tasker: Any = None
        self._loaded = False
        self._resource_root: Path | None = None
        self._task_id: str | None = None

    # -- 生命周期 --------------------------------------------------------
    @staticmethod
    def _check_run(deadline: float | None, should_stop: Callable[[], bool] | None) -> None:
        if should_stop is not None and should_stop():
            raise MaaStopped("运行期间收到停止请求")
        if deadline is not None and time.monotonic() >= deadline:
            raise MaaTimeout("整轮运行超时")

    def discover_devices(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> list["AdbDeviceInfo"]:
        """用官方 Toolkit 搜索设备。只读，不连接、不选择。"""
        self._check_run(deadline, should_stop)
        from .device import discover_devices
        devices = discover_devices(self.adb_path or None)
        self._check_run(deadline, should_stop)
        return devices

    def connect(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> None:
        self._check_run(deadline, should_stop)
        from maa.controller import AdbController
        from maa.define import MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
        from maa.tasker import Tasker

        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            Tasker.set_log_dir(str(self._log_dir))
        if self._stdout_level is not None:
            from maa.define import LoggingLevelEnum

            Tasker.set_stdout_level(LoggingLevelEnum(self._stdout_level))

        screencap = (
            self._screencap_methods
            if self._screencap_methods is not None
            else int(MaaAdbScreencapMethodEnum.Default)
        )
        inputs = self._input_methods if self._input_methods is not None else int(MaaAdbInputMethodEnum.Default)

        self.controller = AdbController(
            adb_path=self.adb_path,
            address=self.address,
            screencap_methods=screencap,
            input_methods=inputs,
        )
        if self._screenshot_raw_size:
            if not self.controller.set_screenshot_use_raw_size(True):
                raise MaaError("无法把截图设置为原始尺寸（set_screenshot_use_raw_size）")
        job = self.controller.post_connection()
        wait_with_deadline(job, deadline or time.monotonic() + 60.0, should_stop)
        if not self.controller.connected:
            raise MaaError(f"连接设备失败：{self.address}")

    def screencap(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> np.ndarray:
        self._check_run(deadline, should_stop)
        job = self.controller.post_screencap()
        wait_with_deadline(job, deadline or time.monotonic() + 30.0, should_stop)
        image = job.get()
        if image is None or not isinstance(image, np.ndarray) or image.size == 0:
            raise MaaError("截图失败：返回空图像")
        return image

    def raw_resolution(self) -> tuple[int, int]:
        res = self.controller.resolution
        return (int(res[0]), int(res[1]))

    def load_resource(
        self, resource_root: Path, deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None, task_id: str | None = None,
    ) -> list[str]:
        self._check_run(deadline, should_stop)
        from maa.resource import Resource
        from maa.tasker import Tasker

        self.resource = Resource()
        self._resource_root = Path(resource_root)
        self._task_id = task_id
        job = self.resource.post_bundle(str(resource_root))
        wait_with_deadline(job, deadline or time.monotonic() + 120.0, should_stop)
        if not job.succeeded or not self.resource.loaded:
            raise MaaError(f"加载资源失败：{resource_root}")

        self.tasker = Tasker()
        if self.controller is not None:
            if not self.tasker.bind(self.resource, self.controller):
                raise MaaError("Tasker 绑定 Resource/Controller 失败")
        else:
            # 只读识别路径：只绑 Resource（Tasker.bind 会强制要 controller）
            from maa.library import Library

            if not bool(Library.framework().MaaTaskerBindResource(self.tasker._handle, self.resource._handle)):
                raise MaaError("Tasker 绑定 Resource 失败")
        if not self.tasker.inited:
            raise MaaError("Tasker 初始化失败")

        self._loaded = True
        try:
            return list(self.resource.node_list)
        except Exception:  # noqa: BLE001
            return []

    # -- 识别（只读，不发出任何输入） --------------------------------------
    def recognize(
        self,
        image: np.ndarray,
        template: str,
        roi: tuple[int, int, int, int] = (0, 0, 0, 0),
        threshold: float = 0.85,
        deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> RecognitionOutcome:
        """在给定截图上做模板匹配。纯识别，零输入。"""
        from maa.pipeline import JRecognitionType, JTemplateMatch

        if not self._loaded:
            raise MaaError("资源尚未加载，无法识别")

        param = JTemplateMatch(
            template=[template],
            roi=tuple(roi),  # type: ignore[arg-type]
            threshold=[float(threshold)],
            order_by="Horizontal",
            index=0,
            method=5,  # cv::TM_CCOEFF_NORMED
            green_mask=False,
        )
        self._check_run(deadline, should_stop)
        job = self.tasker.post_recognition(JRecognitionType.TemplateMatch, param, image)
        wait_with_deadline(job, deadline or time.monotonic() + 60.0, should_stop)
        detail = job.get()
        return _reco_to_outcome(_first_recognition(detail))

    # -- 输入 ------------------------------------------------------------
    def click(self, x: int, y: int, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> bool:
        self._check_run(deadline, should_stop)
        job = self.controller.post_click(int(x), int(y))
        try:
            wait_with_deadline(job, deadline or time.monotonic() + 30.0, should_stop)
        except (MaaStopped, MaaTimeout) as interrupted:
            self._confirm_controller_job(job, interrupted)
            raise
        return bool(job.succeeded)

    @staticmethod
    def _confirm_controller_job(job: Any, interrupted: MaaStopped | MaaTimeout, action: str = "click") -> None:
        """等待独立 Controller job 结束；Tasker.stop 不能取消此类作业。"""
        confirm_deadline = time.monotonic() + STOP_CONFIRMATION_SECONDS
        try:
            wait_with_deadline(job, confirm_deadline)
            if not job.done:
                raise RuntimeError(f"Controller {action} job still pending after confirmation wait")
        except Exception as exc:  # noqa: BLE001
            raise MaaStopUnconfirmed(
                f"{type(interrupted).__name__} 后 Controller {action} job 未确认完成："
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int,
        deadline: float | None = None, should_stop: Callable[[], bool] | None = None,
    ) -> bool:
        self._check_run(deadline, should_stop)
        if not 100 <= int(duration_ms) <= 2000:
            raise MaaError("swipe duration must be between 100 and 2000 ms")
        job = self.controller.post_swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms))
        try:
            wait_with_deadline(job, deadline or time.monotonic() + 30.0, should_stop)
        except (MaaStopped, MaaTimeout) as interrupted:
            self._confirm_controller_job(job, interrupted, "swipe")
            raise
        return bool(job.succeeded)

    def start_app(self, package_name: str, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> bool:
        self._check_run(deadline, should_stop)
        job = self.controller.post_start_app(package_name)
        wait_with_deadline(job, deadline or time.monotonic() + 60.0, should_stop)
        return bool(job.succeeded)

    # -- 任务 ------------------------------------------------------------
    def run_task(self, entry: str, deadline: float, should_stop: Callable[[], bool]) -> TaskRunSummary:
        self._check_run(deadline, should_stop)
        if self._resource_root is None or self._task_id is None:
            raise MaaError("执行前必须登记并验证任务 Pipeline 资源")
        validate_pipeline_bundle(self._resource_root, self._task_id)
        job = self.tasker.post_task(entry)
        stopped = False
        try:
            wait_with_deadline(job, deadline, should_stop=should_stop)
        except MaaStopped:
            stopped = True
            self._request_and_confirm_stop(job)
        except MaaTimeout:
            self._request_and_confirm_stop(job)
            raise

        detail = None
        try:
            detail = job.get()
        except Exception:  # noqa: BLE001
            detail = None

        node_names: list[str] = []
        input_actions: list[str] = []
        raw_nodes: list[dict[str, Any]] = []
        if detail is not None:
            for node in getattr(detail, "nodes", []) or []:
                node_names.append(str(getattr(node, "name", "")))
                action = getattr(node, "action", None)
                if action is None:
                    continue
                action_name = str(getattr(action, "action", ""))
                raw_nodes.append(
                    {
                        "node": str(getattr(node, "name", "")),
                        "action": action_name,
                        "success": bool(getattr(action, "success", False)),
                        "box": list(getattr(action, "box", []) or []),
                    }
                )
                if action_name and action_name not in NON_INPUT_ACTIONS:
                    input_actions.append(action_name)

        return TaskRunSummary(
            entry=entry,
            succeeded=bool(job.succeeded) and not stopped,
            task_id=int(job.job_id),
            node_names=node_names,
            input_actions=input_actions,
            raw_nodes=raw_nodes,
            stopped=stopped,
            audit_available=detail is not None,
            stop_confirmed=True if stopped else None,
        )

    def request_stop(self) -> Any:
        if self.tasker is None:
            raise MaaError("Tasker 不存在，无法请求停止")
        return self.tasker.post_stop()

    def _request_and_confirm_stop(self, task_job: Any) -> None:
        """请求异步停止，并等待 stop job 与原任务 job 都完成。"""
        confirm_deadline = time.monotonic() + STOP_CONFIRMATION_SECONDS
        stage = "post_stop request"
        try:
            stop_job = self.request_stop()
            stage = "stop job"
            wait_with_deadline(stop_job, confirm_deadline)
            if not stop_job.succeeded:
                raise RuntimeError(f"stop job failed: status={getattr(stop_job, 'status', None)}")
            stage = "task job"
            wait_with_deadline(task_job, confirm_deadline)
            if not task_job.done:
                raise RuntimeError("task job remains active after stop job completed")
            stage = "Tasker running state"
            if bool(getattr(self.tasker, "running", False)):
                raise RuntimeError("Tasker still reports a running task after task job completion")
        except Exception as exc:  # noqa: BLE001
            raise MaaStopUnconfirmed(f"停止未确认（{stage}）：{type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        self.tasker = None
        self.resource = None
        self.controller = None
        self._loaded = False


# ------------------------------------------------------- 只读包装（干运行）
class ReadOnlyBackend:
    """把任何后端包装成**禁止输入**的只读后端。

    干运行必须证明「没有发出点击、滑动、按键、启动或重启应用动作」。
    这里不靠约定，而是让输入方法直接抛异常，任何漏网都会立刻暴露。
    """

    def __init__(self, inner: Backend) -> None:
        self._inner = inner
        self.blocked_calls: list[str] = []

    def _block(self, name: str) -> None:
        self.blocked_calls.append(name)
        raise AssertionError(f"干运行禁止调用输入方法：{name}")

    # 透传只读能力
    def discover_devices(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> list["AdbDeviceInfo"]:
        return self._inner.discover_devices(deadline, should_stop)

    def connect(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> None:
        self._inner.connect(deadline, should_stop)

    def screencap(self, deadline: float | None = None, should_stop: Callable[[], bool] | None = None) -> np.ndarray:
        return self._inner.screencap(deadline, should_stop)

    def raw_resolution(self) -> tuple[int, int]:
        return self._inner.raw_resolution()

    def load_resource(self, resource_root: Path, deadline: float | None = None, should_stop: Callable[[], bool] | None = None, task_id: str | None = None) -> list[str]:
        return self._inner.load_resource(resource_root, deadline, should_stop, task_id)

    def recognize(
        self,
        image: np.ndarray,
        template: str,
        roi: tuple[int, int, int, int] = (0, 0, 0, 0),
        threshold: float = 0.85,
        deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> RecognitionOutcome:
        return self._inner.recognize(image, template, roi, threshold, deadline, should_stop)

    def request_stop(self) -> Any:
        return self._inner.request_stop()

    def close(self) -> None:
        self._inner.close()

    # 拦截输入
    def click(
        self, x: int, y: int, deadline: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> bool:  # pragma: no cover - 正常路径不应到达
        self._block("click")
        return False

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int,
        deadline: float | None = None, should_stop: Callable[[], bool] | None = None,
    ) -> bool:  # pragma: no cover - 正常路径不应到达
        self._block("swipe")
        return False

    def run_task(self, entry: str, deadline: float, should_stop: Callable[[], bool]) -> TaskRunSummary:
        self._block("run_task")
        raise AssertionError  # pragma: no cover

    def start_app(self, package_name: str) -> bool:
        self._block("start_app")
        return False
