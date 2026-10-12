"""对外契约：请求、结果、事件、状态与预算。

这些是本项目**自己的**概念接口（见设计说明第 6 节），不是 MaaFramework 的
官方 API 名称。到真实 SDK 的映射集中在 ``maa_adapter``。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- 预算边界
# 设计说明 8：默认 120 秒 / 恢复 2 次 / 操作 20 次；第一阶段上限 300 秒 /
# 恢复 2 次 / 操作 30 次。截图与识别不计入操作动作。
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RECOVERIES = 2
MAX_MAX_RECOVERIES = 2
DEFAULT_MAX_ACTIONS = 20
MAX_MAX_ACTIONS = 30


class RunMode(StrEnum):
    """运行模式。没有显式选择模式时拒绝执行。"""

    DRY_RUN = "dry-run"
    EXECUTE = "execute"


class RunStatus(StrEnum):
    """TaskResult 的最终状态（设计说明第 6 节）。"""

    COMPLETED = "completed"
    NOTHING_TO_CLAIM = "nothing_to_claim"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"
    CANCELLED = "cancelled"


class ReasonCode(StrEnum):
    """结果原因码。字符串会写进 result.json，供 Codex 审查。"""

    OK = "ok"
    NOTHING_TO_CLAIM = "nothing_to_claim"
    CLAIM_CONFIRMED = "claim_confirmed"

    # 设备与配置
    NO_DEVICE = "no_device"
    DEVICE_NOT_SPECIFIED = "device_not_specified"
    DEVICE_NOT_FOUND = "device_not_found"
    CONNECT_FAILED = "connect_failed"
    PROFILE_INVALID = "profile_invalid"
    RESOLUTION_MISMATCH = "resolution_mismatch"
    RESOURCE_LOAD_FAILED = "resource_load_failed"
    ASSETS_UNVERIFIED = "assets_unverified"
    CONCURRENT_RUN = "concurrent_run"
    MODE_NOT_SELECTED = "mode_not_selected"

    # 页面状态
    UNKNOWN_PAGE = "unknown_page"
    LOGIN_PAGE = "login_page"
    ACCOUNT_CONFLICT = "account_conflict"
    CAPTCHA = "captcha"
    CLAIM_TARGET_ABSENT = "claim_target_absent"

    # 结果判定
    OUTCOME_UNCERTAIN = "outcome_uncertain"
    PARTIAL_PROGRESS = "partial_progress"
    POSTCONDITION_NOT_MET = "postcondition_not_met"

    # 预算与停止
    BUDGET_EXHAUSTED = "budget_exhausted"
    ACTION_BUDGET_EXHAUSTED = "action_budget_exhausted"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    TIMEOUT = "timeout"
    USER_CANCELLED = "user_cancelled"
    STOP_UNCONFIRMED = "stop_unconfirmed"

    # 其它
    INPUT_ACTION_FAILED = "input_action_failed"
    SDK_ERROR = "sdk_error"


class ContractError(ValueError):
    """契约/配置校验失败。"""


class BudgetError(ContractError):
    """预算越界（超出第一阶段上限或为负）。"""


class DeviceError(ContractError):
    """设备选择或分辨率核对失败。

    ``reason_code`` 会直接写进 TaskResult，使 A2/A11 的判定可追溯到具体原因。
    """

    def __init__(self, message: str, reason_code: "ReasonCode | None" = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code or ReasonCode.NO_DEVICE


class ConcurrentRunError(ContractError):
    """同一 device_serial + account_alias 已有有效运行。"""


# ------------------------------------------------------------------ 设备档案
@dataclass(frozen=True)
class DeviceProfile:
    """明确设备档案。

    设计说明 6：不得遇到多个设备时默认选择第一台，因此 ``serial`` 必须由
    用户显式填写；留空一律拒绝操作。
    """

    profile_id: str
    account_alias: str
    adb_path: str
    serial: str
    package_name: str
    expected_resolution: tuple[int, int]
    instance_index: int | None = None
    instance_name: str | None = None

    def describe(self) -> str:
        return (
            f"profile={self.profile_id} alias={self.account_alias} "
            f"serial={self.serial or '<未填写>'} pkg={self.package_name} "
            f"res={self.expected_resolution[0]}x{self.expected_resolution[1]}"
        )

    @property
    def lock_key(self) -> str:
        """互斥键：同一设备 + 同一账号别名。"""
        return f"{self.serial}::{self.account_alias}"


# ------------------------------------------------------------------ 任务请求
@dataclass
class TaskRequest:
    """一次运行的输入。"""

    task_id: str
    profile: DeviceProfile
    mode: RunMode
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_recoveries: int = DEFAULT_MAX_RECOVERIES
    max_actions: int = DEFAULT_MAX_ACTIONS
    resource_root: str = "resource"
    artifacts_root: str = "artifacts"
    retention_days: int = 7

    def validate(self) -> None:
        if self.mode not in (RunMode.DRY_RUN, RunMode.EXECUTE):
            raise ContractError(f"未显式选择运行模式：{self.mode!r}")
        if not self.task_id:
            raise ContractError("task_id 不能为空")
        if not self.profile.package_name:
            raise ContractError("profile.package_name 不能为空")
        w, h = self.profile.expected_resolution
        if w <= 0 or h <= 0:
            raise ContractError(f"profile.expected_resolution 非法：{w}x{h}")

        if self.timeout_seconds <= 0:
            raise BudgetError("timeout_seconds 必须为正数")
        if self.timeout_seconds > MAX_TIMEOUT_SECONDS:
            raise BudgetError(
                f"timeout_seconds={self.timeout_seconds} 超出第一阶段上限 {MAX_TIMEOUT_SECONDS}"
            )
        if self.max_recoveries < 0:
            raise BudgetError("max_recoveries 不能为负")
        if self.max_recoveries > MAX_MAX_RECOVERIES:
            raise BudgetError(
                f"max_recoveries={self.max_recoveries} 超出第一阶段上限 {MAX_MAX_RECOVERIES}"
            )
        if self.max_actions <= 0:
            raise BudgetError("max_actions 必须为正数")
        if self.max_actions > MAX_MAX_ACTIONS:
            raise BudgetError(
                f"max_actions={self.max_actions} 超出第一阶段上限 {MAX_MAX_ACTIONS}"
            )


# ------------------------------------------------------------------ 任务结果
@dataclass
class TaskResult:
    """真实运行的最终结果。字段与设计说明 6 的 TaskResult 一一对应。"""

    run_id: str
    task_id: str
    account_alias: str
    device_serial: str
    status: RunStatus
    reason_code: ReasonCode
    started_at: str
    finished_at: str
    action_count: int
    evidence_paths: list[str] = field(default_factory=list)
    mode: RunMode = RunMode.EXECUTE
    schema_version: str = SCHEMA_VERSION
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = str(self.status)
        data["reason_code"] = str(self.reason_code)
        data["mode"] = str(self.mode)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# -------------------------------------------------------------- 干运行结果
@dataclass
class Observation:
    """一条观察：某个模板/页面在本次截图上是否命中。"""

    name: str
    kind: str  # "page_identity" | "claimable" | "already_claimed" | "blocker"
    hit: bool
    score: float | None = None
    box: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateAction:
    """候选动作。干运行只列出，不执行。"""

    action_id: str
    description: str
    verified: bool
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InspectionResult:
    """干运行的输出。**与 TaskResult 分开**，不得宣称已完成领取。"""

    run_id: str
    task_id: str
    account_alias: str
    device_serial: str
    observed_at: str
    page_id: str | None
    observations: list[Observation] = field(default_factory=list)
    candidate_actions: list[CandidateAction] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    mode: RunMode = RunMode.DRY_RUN
    claim_attempted: bool = False  # 恒为 False，供审查时一眼确认

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = str(self.mode)
        data["observations"] = [o.to_dict() for o in self.observations]
        data["candidate_actions"] = [a.to_dict() for a in self.candidate_actions]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ 运行事件
@dataclass
class RunEvent:
    """一行 events.jsonl。日志不得记录登录凭据（本项目也不采集）。"""

    run_id: str
    seq: int
    at: str
    node: str
    event: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------ 配置文件解析
@dataclass
class LoadedConfig:
    request: TaskRequest
    raw: dict[str, Any]


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ContractError(f"期望整数，得到布尔值：{value!r}")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContractError(f"期望整数，得到 {value!r}") from exc


def load_config(raw: dict[str, Any], mode: RunMode) -> LoadedConfig:
    """把 config/local.json 解析成 TaskRequest。缺失项用安全默认值。"""
    if not isinstance(raw, dict):
        raise ContractError("配置文件根节点必须是 JSON 对象")

    prof = raw.get("profile")
    if not isinstance(prof, dict):
        raise ContractError("配置文件缺少 profile 对象")
    dev = prof.get("device")
    if not isinstance(dev, dict):
        raise ContractError("配置文件缺少 profile.device 对象")

    resolution = dev.get("expected_resolution") or [0, 0]
    if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
        raise ContractError("profile.device.expected_resolution 必须是 [宽, 高]")

    profile = DeviceProfile(
        profile_id=str(prof.get("profile_id") or "local"),
        account_alias=str(prof.get("account_alias") or "unnamed"),
        adb_path=str(dev.get("adb_path") or ""),
        serial=str(dev.get("serial") or "").strip(),
        package_name=str(dev.get("package_name") or ""),
        expected_resolution=(int(resolution[0]), int(resolution[1])),
        instance_index=dev.get("instance_index"),
        instance_name=dev.get("instance_name"),
    )

    budget = raw.get("budget") or {}
    resource = raw.get("resource") or {}
    artifacts = raw.get("artifacts") or {}

    request = TaskRequest(
        task_id=str(raw.get("task_id") or "claim_primary"),
        profile=profile,
        mode=mode,
        timeout_seconds=_as_int(budget.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS),
        max_recoveries=_as_int(budget.get("max_recoveries"), DEFAULT_MAX_RECOVERIES),
        max_actions=_as_int(budget.get("max_actions"), DEFAULT_MAX_ACTIONS),
        resource_root=str(resource.get("root") or "resource"),
        artifacts_root=str(artifacts.get("root") or "artifacts"),
        retention_days=_as_int(artifacts.get("retention_days"), 7),
    )
    request.validate()
    return LoadedConfig(request=request, raw=raw)
