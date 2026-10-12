"""A3/A4/A5/A8/A9/A10：运行器状态判定。

重点覆盖**关键失误**：
  * 动作返回成功但后置条件不成立 → 不能 completed
  * 只有显式「已领」标志才允许 nothing_to_claim
  * 陌生页面 / 阻断页 → 不发出任何输入
  * 未验证素材 → 拒绝执行
  * 动作预算静态越界 → 执行前就拒绝
  * 停止 / 超时 / 并发 → 有界结束
"""

from __future__ import annotations

from pathlib import Path

import pytest
import threading
import time

import conftest
from conftest import FakeBackend, make_project
from sgbltx_helper.contracts import ContractError, DeviceProfile, ReasonCode, RunMode, RunStatus, TaskRequest
from sgbltx_helper.maa_adapter import MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout, TaskRunSummary
from sgbltx_helper.runner import StopController, load_pipeline, run, statically_count_actions

IDENTITY = "page_identity.png"
CLAIM = "btn_claim.png"
CLAIMED = "mark_claimed.png"
CONFLICT = "page_conflict.png"


def make_request(root: Path, mode: RunMode = RunMode.EXECUTE, **kw) -> TaskRequest:
    defaults = dict(
        task_id="claim_primary",
        profile=DeviceProfile(
            profile_id="test",
            account_alias="main",
            adb_path="C:/leidian/LDPlayer14/adb.exe",
            serial="emulator-5554",
            package_name="com.sgbltx.goodgame",
            expected_resolution=(1080, 1920),
        ),
        mode=mode,
        artifacts_root="artifacts",
        resource_root="resource",
    )
    defaults.update(kw)
    return TaskRequest(**defaults)  # type: ignore[arg-type]


def run_it(project, backend: FakeBackend, mode: RunMode = RunMode.EXECUTE, **kw):  # type: ignore[no-untyped-def]
    request = make_request(project.root, mode, **kw)
    lock_dir = project.root / "artifacts" / "locks"
    return run(request, backend, lock_dir, project.root, StopController())


# --------------------------------------------------------------- 后置验证
class TestPostcondition:
    def test_action_succeeded_but_no_postcondition_is_not_completed(self, project) -> None:  # type: ignore[no-untyped-def]
        """SDK 执行成功 ≠ 领取成功。可领标志仍在 → 不得 completed。"""
        backend = FakeBackend(
            script=[
                {IDENTITY: True, CLAIM: True},          # 前置：可领
                {IDENTITY: True, CLAIM: True},          # 后置：仍可领
            ],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.POSTCONDITION_NOT_MET

    def test_claimable_gone_without_claimed_marker_is_uncertain(self, project) -> None:  # type: ignore[no-untyped-def]
        """可领目标消失但无「已领」标志 → 仍缺少可信证据。"""
        backend = FakeBackend(
            script=[
                {IDENTITY: True, CLAIM: True},
                {IDENTITY: True},                       # 可领消失，但没有已领标志
            ],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN

    def test_claimable_gone_with_marker_is_completed(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[
                {IDENTITY: True, CLAIM: True},
                {IDENTITY: True, CLAIMED: True},
            ],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.COMPLETED
        assert result.reason_code is ReasonCode.CLAIM_CONFIRMED
        assert result.action_count == 1

    def test_missing_sdk_action_records_use_static_upper_bound(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, CLAIMED: True}],
            task_summary=TaskRunSummary(
                entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=[], audit_available=False
            ),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.COMPLETED
        assert result.action_count == 1
        assert result.detail["action_count_is_upper_bound"] is True

    def test_already_claimed_marker_on_another_page_is_not_success(self, project) -> None:  # type: ignore[no-untyped-def]
        pages = conftest.default_pages(page_id="mail_inbox")
        pages["pages"].append({
            "page_id": "unrelated_daily_rewards",
            "role": "target",
            "description": "另一项日常奖励",
            "verified": True,
            "identity": [{"template": "daily_page.png", "threshold": 0.8, "verified": True}],
            "claimable": [],
            "already_claimed": [{"template": "daily_claimed.png", "threshold": 0.8, "verified": True}],
            "allowed_actions": [],
        })
        project.write_pages(pages)
        backend = FakeBackend(
            script=[
                {IDENTITY: True, CLAIM: True},
                {"daily_page.png": True, "daily_claimed.png": True},
            ],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN

    def test_unverified_postcondition_asset_is_not_success(self, project) -> None:  # type: ignore[no-untyped-def]
        project.write_pages(conftest.default_pages(already_claimed_verified=False))
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, CLAIMED: True}],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED

    def test_remaining_claimable_target_prevents_success(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, CLAIM: True, CLAIMED: True}],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.POSTCONDITION_NOT_MET

    def test_after_screenshot_failure_is_uncertain(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        original = backend.screencap
        calls = {"n": 0}

        def flaky(deadline=None, should_stop=None):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] >= 2:
                raise MaaError("模拟后置截图失败")
            return original()

        backend.screencap = flaky  # type: ignore[method-assign]
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN

    @pytest.mark.parametrize("error,status,reason", [
        (MaaTimeout("recognition timeout"), RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT),
        (MaaStopped("recognition stopped"), RunStatus.CANCELLED, ReasonCode.USER_CANCELLED),
    ])
    def test_after_recognition_stop_or_timeout_writes_result_and_evidence(self, project, error, status, reason) -> None:  # type: ignore[no-untyped-def]
        class FailingPostRecognitionBackend(FakeBackend):
            def recognize(self, image, template, roi=(0, 0, 0, 0), threshold=0.85,
                          deadline=None, should_stop=None):  # type: ignore[no-untyped-def]
                if self.screencap_count >= 2:
                    raise error
                return super().recognize(image, template, roi, threshold, deadline, should_stop)

        backend = FailingPostRecognitionBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, CLAIMED: True}],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
        )
        result = run_it(project, backend)
        run_dir = project.root / "artifacts" / "runs" / result.run_id
        assert result.status is status
        assert result.reason_code is reason
        assert (run_dir / "after.png").is_file()
        assert (run_dir / "result.json").is_file()
        assert str(run_dir / "after.png") in result.evidence_paths


# --------------------------------------------------------------- 空状态
class TestNothingToClaim:
    def test_explicit_claimed_marker_gives_nothing_to_claim(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIMED: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NOTHING_TO_CLAIM
        assert result.reason_code is ReasonCode.NOTHING_TO_CLAIM
        assert backend.input_calls == [], "nothing_to_claim 不应发出任何输入"

    def test_no_button_and_no_marker_is_not_nothing_to_claim(self, project) -> None:  # type: ignore[no-untyped-def]
        """无按钮 ≠ 没有奖励：两个标志都没有时必须转人工。"""
        backend = FakeBackend(script=[{IDENTITY: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
        assert backend.input_calls == []


# --------------------------------------------------------------- 不误点
class TestNoMisclick:
    def test_unknown_page_sends_no_input(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.UNKNOWN_PAGE
        assert backend.input_calls == []
        assert backend.screencap_count == 3, "应做 1 次 + 最多 2 次只读恢复重试"

    def test_blocker_page_stops_without_input(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{CONFLICT: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.ACCOUNT_CONFLICT
        assert backend.input_calls == []

    def test_unverified_page_refuses_action(self, project) -> None:  # type: ignore[no-untyped-def]
        project.write_pages(conftest.default_pages(page_verified=False))
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
        assert backend.input_calls == []

    def test_unverified_claim_template_refuses_action(self, project) -> None:  # type: ignore[no-untyped-def]
        project.write_pages(conftest.default_pages(claimable_verified=False))
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
        assert backend.input_calls == []

    def test_wrong_page_refuses_action(self, project) -> None:  # type: ignore[no-untyped-def]
        pages = conftest.default_pages(page_id="mail_inbox")
        pages["target_page_id"] = "daily_tasks"
        project.write_pages(pages)
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert backend.input_calls == []


# --------------------------------------------------------------- 预算
class TestBudget:
    def test_maa_native_pipeline_schema_has_a_provable_single_click_bound(self) -> None:
        template_node = {
            "recognition": {
                "type": "TemplateMatch",
                "param": {"template": ["claim_button.png"], "roi": [1, 2, 300, 80], "threshold": [0.9]},
            },
            "action": {"type": "Click", "param": {"target": True}},
            "next": [],
            "repeat": 1,
            "max_hit": 1,
        }
        pipeline = {
            "ClaimPrimary": {
                "recognition": {
                    "type": "TemplateMatch",
                    "param": {"template": ["detail_title.png"], "roi": [1, 2, 300, 80], "threshold": [0.9]},
                },
                "action": {"type": "DoNothing", "param": {}},
                "next": [{"name": "ClaimReward"}],
                "repeat": 1,
                "max_hit": 1,
            },
            "ClaimReward": template_node,
        }
        budget = statically_count_actions(pipeline, "ClaimPrimary")
        assert budget.visited_nodes == ["ClaimPrimary", "ClaimReward"]
        assert budget.declared_actions == ["Click"]
        assert budget.action_count == 1

    def test_custom_flat_pipeline_shape_is_rejected_before_sdk(self) -> None:
        with pytest.raises(ContractError):
            statically_count_actions(
                {"ClaimPrimary": {"recognition": "TemplateMatch", "template": "x.png", "action": "Click"}},
                "ClaimPrimary",
            )

    def test_mail_claim_pipeline_is_bound_to_the_selected_email_and_one_click(self) -> None:
        resource = Path(__file__).resolve().parents[1]
        pipeline = load_pipeline(resource / "resource" / "pipeline" / "claim_primary.json")
        budget = statically_count_actions(pipeline, "ClaimPrimary")
        assert budget.visited_nodes == ["ClaimPrimary", "ClaimReward"]
        assert budget.declared_actions == ["Click"]
        assert budget.action_count == 1
        from sgbltx_helper.pages import load_page_catalog

        catalog = load_page_catalog(resource / "resource" / "pages.json")
        target = catalog.by_id(catalog.target_page_id)
        assert target is not None
        assert pipeline["ClaimPrimary"]["recognition"]["param"]["template"] == [target.identity[0].template]
        assert pipeline["ClaimReward"]["recognition"]["param"]["template"] == ["mail_claim_button.png"]
        assert pipeline["ClaimReward"]["action"] == {"type": "Click", "param": {"target": True}}

    def test_installed_maa_resource_loads_native_single_claim_pipeline(self) -> None:
        from maa.resource import Resource

        resource_root = Path(__file__).resolve().parents[1] / "resource"
        resource = Resource()
        try:
            job = resource.post_bundle(resource_root)
            job.wait()
            assert job.succeeded
            assert {"ClaimPrimary", "ClaimReward"}.issubset(set(resource.node_list))
        finally:
            del resource

    def test_declared_actions_over_budget_refuses_before_any_input(self, project) -> None:  # type: ignore[no-untyped-def]
        project.write_pipeline(conftest.default_pipeline(actions=25))
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend, max_actions=20)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.ACTION_BUDGET_EXHAUSTED
        assert backend.input_calls == [], "静态越界必须在发出任何输入之前拒绝"

    def test_static_count_matches_declared_actions(self) -> None:
        pipeline = conftest.default_pipeline(actions=3)
        budget = statically_count_actions(pipeline, "ClaimPrimary")
        assert budget.action_count == 3
        assert budget.visited_nodes[0] == "ClaimPrimary"

    @pytest.mark.parametrize("pipeline", [
        {"ClaimPrimary": {"recognition": {"type": "TemplateMatch", "param": {"template": ["x"], "roi": [0, 0, 1, 1], "threshold": [0.9]}}, "action": {"type": "Click", "param": {"target": True}}, "repeat": 1, "max_hit": 1, "next": [{"name": "ClaimPrimary"}]}},
        {"ClaimPrimary": {"recognition": {"type": "TemplateMatch", "param": {"template": ["x"], "roi": [0, 0, 1, 1], "threshold": [0.9]}}, "action": {"type": "Click", "param": {"target": True}}, "repeat": 31, "max_hit": 1, "next": []}},
        {"ClaimPrimary": {"recognition": {"type": "TemplateMatch", "param": {"template": ["x"], "roi": [0, 0, 1, 1], "threshold": [0.9]}}, "action": {"type": "Click", "param": {"target": True}}, "repeat": 1, "max_hit": 1, "on_error": [{"name": "fallback"}], "next": []}},
        {"ClaimPrimary": {"recognition": {"type": "TemplateMatch", "param": {"template": ["x"], "roi": [0, 0, 1, 1], "threshold": [0.9]}}, "action": {"type": "Custom", "param": {}}, "repeat": 1, "max_hit": 1, "next": []}},
    ])
    def test_unsupported_or_unbounded_pipeline_is_rejected_before_sdk(self, project, pipeline) -> None:  # type: ignore[no-untyped-def]
        project.write_pipeline(pipeline)
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.FAILED
        assert backend.input_calls == []

    def test_repeat_field_is_rejected_when_sdk_semantics_are_not_proven(self) -> None:
        pipeline = conftest.default_pipeline(actions=1)
        pipeline["ClaimPrimary"]["repeat"] = 3
        with pytest.raises(ContractError, match="repeat"):
            statically_count_actions(pipeline, "ClaimPrimary")

    def test_default_pipeline_inheritance_is_rejected_before_task_submission(self, project) -> None:  # type: ignore[no-untyped-def]
        (project.resource_root / "default_pipeline.json").write_text(
            '{"Default":{"repeat":31}}', encoding="utf-8"
        )
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.FAILED
        assert result.reason_code is ReasonCode.PROFILE_INVALID
        assert backend.input_calls == []

    def test_extra_pipeline_file_is_rejected_before_task_submission(self, project) -> None:  # type: ignore[no-untyped-def]
        (project.resource_root / "pipeline" / "unrelated.json").write_text(
            '{"Extra":{"action":"Click"}}', encoding="utf-8"
        )
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        result = run_it(project, backend)
        assert result.status is RunStatus.FAILED
        assert backend.input_calls == []

    def test_task_timeout_is_bounded(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}], stage_delays={"task": 1.5})
        started = time.monotonic()
        result = run_it(project, backend, timeout_seconds=1)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.TIMEOUT
        assert time.monotonic() - started < 1.3

    def test_one_deadline_covers_connect_screenshot_and_resource_waits(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}],
            stage_delays={"connect": 0.4, "screencap": 0.4, "load": 0.4},
        )
        started = time.monotonic()
        result = run_it(project, backend, timeout_seconds=1)
        elapsed = time.monotonic() - started
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.TIMEOUT
        assert elapsed < 1.3
        assert len(set(backend.deadlines_seen)) == 1

    def test_template_recognition_uses_shared_deadline(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}], stage_delays={"recognize": 1.5})
        started = time.monotonic()
        result = run_it(project, backend, timeout_seconds=1)
        assert result.reason_code is ReasonCode.TIMEOUT
        assert time.monotonic() - started < 1.3
        assert len(set(backend.deadlines_seen)) == 1

    def test_postcondition_screenshot_uses_shared_deadline(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, CLAIMED: True}],
            task_summary=TaskRunSummary(entry="ClaimPrimary", succeeded=True, task_id=1, input_actions=["Click"]),
            stage_delays={"screencap": [0.0, 1.5]},
        )
        started = time.monotonic()
        result = run_it(project, backend, timeout_seconds=1)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.TIMEOUT
        assert time.monotonic() - started < 1.3
        assert len(set(backend.deadlines_seen)) == 1

    def test_request_over_hard_limit_is_rejected(self, project) -> None:  # type: ignore[no-untyped-def]
        from sgbltx_helper.contracts import BudgetError

        with pytest.raises(BudgetError):
            make_request(project.root, max_actions=31).validate()
        with pytest.raises(BudgetError):
            make_request(project.root, timeout_seconds=301).validate()
        with pytest.raises(BudgetError):
            make_request(project.root, max_recoveries=3).validate()


# --------------------------------------------------------------- 停止
class TestStop:
    def test_stop_before_gate_sends_no_input(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        request = make_request(project.root)
        stop = StopController()
        stop.request_stop()
        result = run(request, backend, project.root / "artifacts" / "locks", project.root, stop)
        assert result.status is RunStatus.CANCELLED
        assert result.reason_code is ReasonCode.USER_CANCELLED
        assert backend.input_calls == []

    def test_stop_during_task_is_cancelled_not_completed(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}, {IDENTITY: True, CLAIMED: True}],
            task_summary=TaskRunSummary(
                entry="ClaimPrimary", succeeded=False, task_id=1, input_actions=["Click"],
                stopped=True, stop_confirmed=True,
            ),
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.CANCELLED
        assert result.reason_code is ReasonCode.USER_CANCELLED
        assert "无法回滚" in result.detail.get("message", "")

    def test_unconfirmed_stop_persists_device_block_until_manual_recovery(self, project) -> None:  # type: ignore[no-untyped-def]
        from sgbltx_helper.locking import RunLock

        lock_dir = project.root / "artifacts" / "locks"
        failed = FakeBackend(
            script=[{IDENTITY: True, CLAIM: True}],
            task_error=MaaStopUnconfirmed("post_stop failed; task job still active"),
        )
        result = run_it(project, failed)
        marker = Path(result.detail["device_block"])
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.STOP_UNCONFIRMED
        assert marker.is_file()

        retry = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        blocked = run_it(project, retry)
        assert blocked.status is RunStatus.NEEDS_ATTENTION
        assert blocked.reason_code is ReasonCode.STOP_UNCONFIRMED
        assert retry.connected is False
        assert retry.input_calls == []

        assert RunLock.clear_stop_unconfirmed(lock_dir, "emulator-5554") is True

    def test_stop_interrupts_a_real_connect_wait(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}], stage_delays={"connect": 5.0})
        stop = StopController()
        threading.Thread(target=lambda: (time.sleep(0.05), stop.request_stop()), daemon=True).start()
        started = time.monotonic()
        result = run(make_request(project.root, timeout_seconds=10), backend,
                     project.root / "artifacts" / "locks", project.root, stop)
        assert result.status is RunStatus.CANCELLED
        assert time.monotonic() - started < 1.0


# --------------------------------------------------------------- 设备选择
class TestDeviceSelection:
    def test_no_device_is_rejected_before_connecting(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(devices=[])
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.NO_DEVICE
        assert backend.connected is False, "无设备时不应尝试连接"
        assert backend.screencap_count == 0

    def test_unlisted_serial_is_rejected(self, project) -> None:  # type: ignore[no-untyped-def]
        from sgbltx_helper.device import AdbDeviceInfo

        backend = FakeBackend(
            devices=[AdbDeviceInfo(name="other", adb_path="", address="emulator-5556")]
        )
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.DEVICE_NOT_FOUND
        assert backend.connected is False

    def test_ambiguous_selection_is_rejected(self, project) -> None:  # type: ignore[no-untyped-def]
        from sgbltx_helper.device import AdbDeviceInfo

        backend = FakeBackend(
            devices=[
                AdbDeviceInfo(name="a", adb_path="", address="emulator-5554"),
                AdbDeviceInfo(name="b", adb_path="", address="EMULATOR-5554"),
            ]
        )
        result = run_it(project, backend)
        assert result.reason_code is ReasonCode.DEVICE_NOT_SPECIFIED
        assert backend.connected is False


# --------------------------------------------------------------- 技术失败
class TestTechnicalFailure:
    def test_connect_failure_is_failed(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(connect_error=MaaError("模拟连接失败"))
        result = run_it(project, backend)
        assert result.status is RunStatus.FAILED
        assert result.reason_code is ReasonCode.CONNECT_FAILED
        assert backend.input_calls == []

    def test_resolution_mismatch_refuses(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{}], resolution=(720, 1280))
        result = run_it(project, backend)
        assert result.status is RunStatus.NEEDS_ATTENTION
        assert result.reason_code is ReasonCode.RESOLUTION_MISMATCH
        assert backend.input_calls == []

    def test_concurrent_run_is_rejected(self, project) -> None:  # type: ignore[no-untyped-def]
        from sgbltx_helper.locking import RunLock

        lock_dir = project.root / "artifacts" / "locks"
        holder = RunLock(lock_dir, "emulator-5554", "main")
        holder.acquire("holder-run")
        try:
            backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
            result = run_it(project, backend)
            assert result.status is RunStatus.FAILED
            assert result.reason_code is ReasonCode.CONCURRENT_RUN
            assert backend.input_calls == []
            assert backend.screencap_count == 0, "拿不到锁时不应连接设备"
        finally:
            holder.release()


# --------------------------------------------------------------- 干运行
class TestDryRun:
    def test_dry_run_returns_inspection_not_task_result(self, project) -> None:  # type: ignore[no-untyped-def]
        from sgbltx_helper.contracts import InspectionResult

        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        payload = run_it(project, backend, RunMode.DRY_RUN)
        assert isinstance(payload, InspectionResult)
        assert payload.claim_attempted is False
        assert payload.page_id == "mail_inbox"

    def test_dry_run_makes_zero_input_calls(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        run_it(project, backend, RunMode.DRY_RUN)
        assert backend.input_calls == []
        assert backend.stop_requested is False

    def test_dry_run_lists_candidates_and_marks_blockers(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        payload = run_it(project, backend, RunMode.DRY_RUN)
        assert payload.candidate_actions, "干运行应列出候选动作"
        assert payload.blockers == []

    def test_dry_run_on_unknown_page_reports_manual_navigation(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{}])
        payload = run_it(project, backend, RunMode.DRY_RUN)
        assert payload.page_id is None
        assert any("人工切换" in b for b in payload.blockers)
        assert backend.input_calls == []

    def test_dry_run_writes_inspection_json(self, project) -> None:  # type: ignore[no-untyped-def]
        backend = FakeBackend(script=[{IDENTITY: True, CLAIM: True}])
        payload = run_it(project, backend, RunMode.DRY_RUN)
        path = project.root / "artifacts" / "runs" / payload.run_id / "inspection.json"
        assert path.is_file()
        assert not (project.root / "artifacts" / "runs" / payload.run_id / "result.json").exists()
