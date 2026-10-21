"""测试共享夹具：一个可编排的假后端 + 临时项目目录。

假后端让「页面状态 → 判定结果」的映射完全确定，从而可以针对**关键失误**
（错误页面点击、干运行发出输入、动作后无验证却成功、预算越界、并发互斥）
写断言，而不是复述代码。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sgbltx_helper.maa_adapter import RecognitionOutcome, TaskRunSummary, wait_with_deadline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_RESOURCE = PROJECT_ROOT / "resource"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass
class FakeBackend:
    """按脚本逐次返回页面状态的假后端。"""

    script: list[dict[str, bool]] = field(default_factory=list)
    resolution: tuple[int, int] = (1080, 1920)
    connect_error: Exception | None = None
    screencap_error: Exception | None = None
    task_summary: TaskRunSummary | None = None
    task_error: Exception | None = None
    click_error: Exception | None = None
    recognition_errors: dict[str, Exception] = field(default_factory=dict)
    stage_delays: dict[str, float | list[float]] = field(default_factory=dict)
    deadlines_seen: list[float | None] = field(default_factory=list)
    node_names: list[str] = field(default_factory=lambda: ["ClaimPrimary"])

    input_calls: list[tuple[Any, ...]] = field(default_factory=list)
    screencap_count: int = 0
    stop_requested: bool = False
    connected: bool = False
    closed: bool = False
    devices: list[Any] | None = None
    _current: dict[str, bool] = field(default_factory=dict)

    def _wait_stage(self, name: str, deadline, should_stop) -> None:  # type: ignore[no-untyped-def]
        self.deadlines_seen.append(deadline)
        configured = self.stage_delays.get(name, 0.0)
        delay = configured.pop(0) if isinstance(configured, list) and configured else configured
        if isinstance(delay, list):
            delay = 0.0
        if delay <= 0:
            return
        class Pending:
            end = time.monotonic() + delay

            @property
            def done(self) -> bool:
                return time.monotonic() >= self.end

        wait_with_deadline(Pending(), deadline or (time.monotonic() + delay + 1), should_stop)

    # -- Backend 接口 ----------------------------------------------------
    def discover_devices(self, deadline=None, should_stop=None) -> list[Any]:  # type: ignore[no-untyped-def]
        self._wait_stage("discover", deadline, should_stop)
        if self.devices is not None:
            return list(self.devices)
        from sgbltx_helper.device import AdbDeviceInfo

        return [AdbDeviceInfo(name="fake", adb_path="", address="emulator-5554")]

    def connect(self, deadline=None, should_stop=None) -> None:  # type: ignore[no-untyped-def]
        self._wait_stage("connect", deadline, should_stop)
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    def screencap(self, deadline=None, should_stop=None) -> np.ndarray:  # type: ignore[no-untyped-def]
        self._wait_stage("screencap", deadline, should_stop)
        if self.screencap_error:
            raise self.screencap_error
        idx = min(self.screencap_count, len(self.script) - 1) if self.script else 0
        self._current = dict(self.script[idx]) if self.script else {}
        self.screencap_count += 1
        # 中灰而非全黑：真实主城未被遮罩时固定界面平均亮度约 88，页面身份的 min_mean 据此判断无遮罩。
        return np.full((self.resolution[1], self.resolution[0], 3), 128, dtype=np.uint8)

    def raw_resolution(self) -> tuple[int, int]:
        return self.resolution

    def load_resource(self, resource_root: Path, deadline=None, should_stop=None, task_id=None) -> list[str]:  # type: ignore[no-untyped-def]
        self._wait_stage("load", deadline, should_stop)
        return list(self.node_names)

    def recognize(
        self,
        image: np.ndarray,
        template: str,
        roi: tuple[int, int, int, int] = (0, 0, 0, 0),
        threshold: float = 0.85,
        deadline=None,
        should_stop=None,
    ) -> RecognitionOutcome:
        self._wait_stage("recognize", deadline, should_stop)
        if template in self.recognition_errors:
            raise self.recognition_errors.pop(template)
        hit = bool(self._current.get(template, False))
        return RecognitionOutcome(
            hit=hit,
            score=1.0 if hit else 0.0,
            box=(100, 100, 60, 60) if hit else None,
        )

    def click(self, x: int, y: int, deadline=None, should_stop=None) -> bool:  # type: ignore[no-untyped-def]
        self.input_calls.append(("click", x, y))
        if self.click_error is not None:
            raise self.click_error
        return True

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int, deadline=None, should_stop=None) -> bool:  # type: ignore[no-untyped-def]
        self.input_calls.append(("swipe", x1, y1, x2, y2, duration_ms))
        if self.click_error is not None:
            raise self.click_error
        return True

    def run_task(self, entry: str, deadline: float, should_stop) -> TaskRunSummary:  # type: ignore[no-untyped-def]
        self._wait_stage("task", deadline, should_stop)
        self.input_calls.append(("run_task", entry))
        if self.task_error:
            raise self.task_error
        if self.task_summary is not None:
            return self.task_summary
        return TaskRunSummary(entry=entry, succeeded=True, task_id=1, input_actions=["Click"])

    def request_stop(self) -> None:
        self.stop_requested = True

    def close(self) -> None:
        self.closed = True


@dataclass
class ProjectFixture:
    root: Path
    resource_root: Path
    pages: dict[str, Any]
    pipeline: dict[str, Any]

    def write_pages(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        (self.resource_root / "pages.json").write_text(
            json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def write_pipeline(self, pipeline: dict[str, Any]) -> None:
        self.pipeline = pipeline
        (self.resource_root / "pipeline" / "claim_primary.json").write_text(
            json.dumps(pipeline, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def make_project(tmp_path: Path, pages: dict[str, Any] | None = None, pipeline: dict[str, Any] | None = None) -> ProjectFixture:
    resource_root = tmp_path / "resource"
    (resource_root / "pipeline").mkdir(parents=True, exist_ok=True)
    (resource_root / "image").mkdir(parents=True, exist_ok=True)

    fixture = ProjectFixture(
        root=tmp_path,
        resource_root=resource_root,
        pages=pages or default_pages(),
        pipeline=pipeline or default_pipeline(),
    )
    fixture.write_pages(fixture.pages)
    fixture.write_pipeline(fixture.pipeline)
    return fixture


def default_pages(
    *,
    page_verified: bool = True,
    claimable_verified: bool = True,
    already_claimed_verified: bool = True,
    page_id: str = "mail_inbox",
    with_claimable: bool = True,
    with_already_claimed: bool = True,
    with_blocker: bool = True,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = [
        {
            "page_id": page_id,
            "role": "target",
            "description": "测试用目标页",
            "verified": page_verified,
            "identity": [{"template": "page_identity.png", "threshold": 0.8, "verified": page_verified}],
            "claimable": (
                [{"template": "btn_claim.png", "threshold": 0.8, "verified": claimable_verified}]
                if with_claimable
                else []
            ),
            "already_claimed": (
                [{"template": "mark_claimed.png", "threshold": 0.8, "verified": already_claimed_verified}]
                if with_already_claimed
                else []
            ),
            "allowed_actions": [
                {"action_id": "claim_all", "description": "一键领取", "verified": claimable_verified}
            ],
        }
    ]
    if with_blocker:
        pages.append(
            {
                "page_id": "account_conflict",
                "role": "blocker",
                "reason_code": "account_conflict",
                "description": "重复登录弹窗",
                "verified": True,
                "identity": [{"template": "page_conflict.png", "threshold": 0.8, "verified": True}],
                "claimable": [],
                "already_claimed": [],
                "allowed_actions": [],
            }
        )
    return {
        "schema_version": 1,
        "resolution": [1080, 1920],
        "entry_node": "ClaimPrimary",
        "target_page_id": page_id,
        "pages": pages,
    }


def default_pipeline(*, actions: int = 1) -> dict[str, Any]:
    """生成一条 next 链，含 `actions` 个 Click 节点。"""
    nodes: dict[str, Any] = {}
    names = [f"ClaimPrimaryStep{i}" for i in range(actions)]
    entry = "ClaimPrimary"
    chain = [entry] + names
    for i, name in enumerate(chain):
        node: dict[str, Any] = {
            "recognition": {
                "type": "TemplateMatch",
                "param": {"template": ["page_identity.png"], "roi": [0, 0, 0, 0], "threshold": [0.85]},
            },
            "action": {"type": "Click" if i > 0 else "DoNothing",
                       "param": {"target": True} if i > 0 else {}},
            "repeat": 1,
            "max_hit": 1,
            "next": [],
        }
        if i + 1 < len(chain):
            node["next"] = [{"name": chain[i + 1]}]
        nodes[name] = node
    return nodes


@pytest.fixture
def project(tmp_path: Path) -> ProjectFixture:
    return make_project(tmp_path)


REAL_RUNS = PROJECT_ROOT / "artifacts" / "runs"


def snapshot_real_runs() -> set[str]:
    if not REAL_RUNS.is_dir():
        return set()
    return {entry.name for entry in REAL_RUNS.iterdir()}


@pytest.fixture(scope="session", autouse=True)
def guard_real_run_artifacts():  # type: ignore[no-untyped-def]
    """测试不得在本机真实 artifacts/runs 下新建或删除运行目录。

    runner 每次运行都会按保留期清理 runs；测试若以项目根运行，会删掉实机证据。
    """
    before = snapshot_real_runs()
    yield
    after = snapshot_real_runs()
    removed = sorted(before - after)
    added = sorted(after - before)
    if removed or added:
        pytest.fail(f"测试改动了真实 artifacts/runs：删除 {removed}，新增 {added}")
