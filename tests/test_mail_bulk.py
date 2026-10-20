from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FakeBackend, make_project
from sgbltx_helper.contracts import DeviceProfile, InspectionResult, ReasonCode, RunMode, RunStatus, TaskRequest
from sgbltx_helper.maa_adapter import MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout
from sgbltx_helper.runner import StopController, run
from sgbltx_helper.pages import load_page_catalog

from sgbltx_helper.mail_bulk import BulkObservation, choose_bulk_step


def test_checked_in_bulk_resource_entry_node_exists_in_its_pipeline():
    resource = Path(__file__).resolve().parents[1] / "resource" / "mail_bulk"
    catalog = load_page_catalog(resource / "pages.json")
    pipeline = json.loads((resource / "pipeline" / "mail_bulk_claim.json").read_text(encoding="utf-8"))
    assert catalog.entry_node in pipeline


@pytest.mark.parametrize(
    ("observation", "submitted", "expected"),
    [
        (BulkObservation("main_city"), False, "open_more"),
        (BulkObservation("more_menu"), False, "open_mail"),
        (BulkObservation("mail_inbox", claimable=True), False, "claim_all"),
        (BulkObservation("mail_inbox", claimable=False, no_pending=True), False, "close_mail"),
        (BulkObservation("reward_popup"), False, "dismiss_initial_reward"),
        (BulkObservation("reward_popup"), True, "dismiss_reward"),
        (BulkObservation("unknown"), False, "stop_unknown"),
    ],
)
def test_bulk_step_is_authorized_by_current_observation(observation, submitted, expected):
    assert choose_bulk_step(observation, submitted) == expected


def test_bulk_does_not_submit_a_second_claim_after_result_returns_to_claimable_inbox():
    observation = BulkObservation("mail_inbox", claimable=True, no_pending=False)
    assert choose_bulk_step(observation, claim_request_submitted=True) == "stop_uncertain"


def test_claimable_absence_alone_is_not_a_proof_of_empty_inbox():
    observation = BulkObservation("mail_inbox", claimable=False, no_pending=False)
    assert choose_bulk_step(observation, claim_request_submitted=False) == "stop_uncertain"


def _bulk_pages() -> dict:
    pages = [
        {"page_id": "account_conflict", "role": "blocker", "reason_code": "account_conflict",
         "verified": True, "identity": [{"template": "conflict.png", "verified": True}]},
        {"page_id": "main_city", "role": "entry", "verified": True,
         "identity": [{"template": "city.png", "verified": True}]},
        {"page_id": "more_menu", "role": "entry", "verified": True,
         "identity": [{"template": "more_page.png", "verified": True}]},
        {"page_id": "mail_inbox", "role": "entry", "verified": True,
         "identity": [{"template": "mail_title.png", "verified": True},
                      {"template": "system_tab.png", "verified": True}],
         "claimable": [{"template": "pending.png", "verified": True}],
         "already_claimed": [{"template": "read.png", "verified": True}]},
        {"page_id": "reward_popup", "role": "target", "verified": True,
         "identity": [{"template": "reward_popup.png", "verified": True}]},
        {"page_id": "mail_detail", "role": "target", "verified": True,
         "identity": [{"template": "mail_detail.png", "verified": True}]},
    ]
    return {"schema_version": 1, "resolution": [1080, 1920], "entry_node": "NoInput",
            "target_page_id": "mail_inbox", "pages": pages}


def _bulk_config() -> dict:
    controls = {}
    for name, template in {
        "more": "more_button.png", "mail": "mail_button.png", "bulk_read": "bulk_read.png",
        "mail_close": "mail_close.png", "detail_close": "detail_close.png",
        "reward_close": "reward_close.png",
    }.items():
        controls[name] = {"template": template, "roi": [0, 0, 0, 0], "threshold": 0.9,
                          "verified": True, "description": name}
    return {"controls": controls, "reward_items": [
                {"template": "reward_item.png", "verified": True},
                {"template": "reward_item_second.png", "verified": True}],
            "empty_state": {"template": "empty_state.png", "verified": True},
            "transition_wait_seconds": 0.1, "max_reward_popups": 6}


def _request(root: Path, mode: RunMode, **overrides) -> TaskRequest:
    values = dict(
        task_id="mail_bulk_claim",
        profile=DeviceProfile("test", "main", "", "emulator-5554", "com.sgbltx.goodgame", (1080, 1920)),
        mode=mode, resource_root="resource", artifacts_root="artifacts",
    )
    values.update(overrides)
    return TaskRequest(**values)


def _make_bulk_project(tmp_path: Path):
    project = make_project(tmp_path)
    bulk_root = project.resource_root / "mail_bulk"
    (bulk_root / "pipeline").mkdir(parents=True)
    project.write_pages(_bulk_pages())
    # runner uses resource/mail_bulk/pages.json; retain the ordinary resource fixture too.
    (bulk_root / "pages.json").write_text(json.dumps(_bulk_pages()), encoding="utf-8")
    (bulk_root / "mail_bulk.json").write_text(json.dumps(_bulk_config()), encoding="utf-8")
    pipeline = {
        "NoInput": {
            "recognition": {"type": "TemplateMatch", "param": {
                "template": ["mail_title.png"], "roi": [0, 0, 0, 0], "threshold": [0.9]}},
            "action": {"type": "DoNothing", "param": {}}, "next": [], "repeat": 1, "max_hit": 1,
        }
    }
    (bulk_root / "pipeline" / "mail_bulk_claim.json").write_text(json.dumps(pipeline), encoding="utf-8")
    return project


def _run_bulk(project, backend, mode=RunMode.EXECUTE, stop=None, **overrides):
    request = _request(project.root, mode, **overrides)
    return run(request, backend, project.root / "artifacts" / "locks", project.root, stop or StopController())


def test_bulk_claims_once_closes_reward_and_mail_then_confirms_city(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"city.png": True, "more_button.png": True},
        {"more_page.png": True, "mail_button.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True, "more_button.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.COMPLETED
    assert result.action_count == 5
    assert result.detail["navigation_action_count"] == 2
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["claim_verified"] is True
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls].count("click") == 5
    assert Path(result.detail["before"]).is_file()
    assert Path(result.detail["after"]).is_file()
    assert result.detail["after"] in result.evidence_paths


def test_bulk_claims_once_and_closes_each_recognized_reward_before_mail_and_city(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item_second.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.COMPLETED
    assert result.reason_code is ReasonCode.CLAIM_CONFIRMED
    assert result.action_count == 4
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 3  # 两张奖励弹窗 + 邮件窗口 X
    assert result.detail["reward_popup_count"] == 2
    assert result.detail["claim_verified"] is True
    assert result.detail["post_batch_state_verified"] is True
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls].count("click") == 4


def test_bulk_does_not_blindly_redismiss_unchanged_reward_popup(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.action_count == 2
    assert result.detail["claim_verified"] is True


def test_bulk_waits_through_old_reward_frame_then_closes_next_reward_and_returns_city(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item_second.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.COMPLETED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["reward_popup_count"] == 2
    assert result.detail["dismiss_action_count"] == 3
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert result.action_count == 4
    assert backend.screencap_count == 6


def test_bulk_waits_through_old_reward_frame_then_accepts_mail_list(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.COMPLETED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["reward_popup_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["post_batch_state_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert backend.screencap_count == 5


def test_bulk_persistent_old_reward_waits_to_window_end_without_second_close(tmp_path):
    project = _make_bulk_project(tmp_path)
    config_path = project.resource_root / "mail_bulk" / "mail_bulk.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    # Keep the window long enough for the initial screenshot/classification to
    # finish, then prove that a later read-only poll occurs before exhaustion.
    config["transition_wait_seconds"] = 0.2
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.POSTCONDITION_NOT_MET
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.action_count == 2
    assert result.detail["claim_verified"] is True
    assert backend.screencap_count > 3


def test_bulk_stop_during_reward_transition_wait_preserves_inputs(tmp_path):
    project = _make_bulk_project(tmp_path)
    stop = StopDuringWait()
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
    ])
    result = _run_bulk(project, backend, stop=stop)
    assert result.status is RunStatus.CANCELLED
    assert result.reason_code is ReasonCode.USER_CANCELLED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.action_count == 2


def test_bulk_shared_timeout_during_reward_transition_wait_preserves_inputs(tmp_path):
    project = _make_bulk_project(tmp_path)
    config_path = project.resource_root / "mail_bulk" / "mail_bulk.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["transition_wait_seconds"] = 5
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(
        script=[
            {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
            {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
            {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        ],
        stage_delays={"screencap": [0, 0, 2]},
    )
    result = _run_bulk(project, backend, timeout_seconds=1)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.TIMEOUT
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.action_count == 2


class StopDuringWait(StopController):
    def wait(self, seconds: float) -> bool:
        self.request_stop()
        return True


def test_bulk_reward_popup_limit_stops_before_an_extra_close(tmp_path):
    project = _make_bulk_project(tmp_path)
    config_path = project.resource_root / "mail_bulk" / "mail_bulk.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_reward_popups"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item_second.png": True, "reward_close.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.action_count == 2
    assert result.detail["claim_verified"] is True


def test_bulk_dry_run_from_city_has_zero_inputs(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[{"city.png": True, "more_button.png": True}])
    result = _run_bulk(project, backend, RunMode.DRY_RUN)
    assert isinstance(result, InspectionResult)
    assert result.page_id == "main_city"
    assert result.claim_attempted is False
    assert backend.input_calls == []


def test_bulk_unknown_page_stops_without_input(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[{}])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.action_count == 0
    assert backend.input_calls == []


def test_bulk_rejects_extra_pipeline_resources_before_any_input(tmp_path):
    project = _make_bulk_project(tmp_path)
    extra = project.resource_root / "mail_bulk" / "pipeline" / "unbudgeted.json"
    extra.write_text("{}", encoding="utf-8")
    backend = FakeBackend(script=[{"city.png": True, "more_button.png": True}])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.FAILED
    assert result.reason_code.value == "profile_invalid"
    assert result.action_count == 0
    assert backend.input_calls == []


def test_bulk_respects_one_cumulative_action_budget_across_navigation_and_claim(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"city.png": True, "more_button.png": True},
        {"more_page.png": True, "mail_button.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
    ])
    result = _run_bulk(project, backend, max_actions=2)
    assert result.reason_code.value == "action_budget_exhausted"
    assert result.action_count == 2
    assert result.detail["navigation_action_count"] == 2
    assert result.detail["claim_action_count"] == 0
    assert result.detail["claim_request_submitted"] is False


def test_verified_reward_survives_cleanup_with_inbox_state_unconfirmed(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True, "mail_close.png": True},
        {"city.png": True, "more_button.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_verified"] is True
    assert result.detail["cleanup_verified"] is True
    assert result.detail["post_batch_state_verified"] is False
    assert result.detail["claim_action_count"] == 1
    assert result.action_count == 3


def test_bulk_uncertain_result_never_retries_bulk_button(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_request_submitted"] is True
    assert result.detail["claim_action_count"] == 1
    assert result.action_count == 1


def test_bulk_nothing_to_claim_closes_email_without_claim_input(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"city.png": True, "more_button.png": True},
        {"more_page.png": True, "mail_button.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True, "more_button.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NOTHING_TO_CLAIM
    assert result.detail["claim_action_count"] == 0
    assert result.detail["navigation_action_count"] == 2
    assert result.detail["dismiss_action_count"] == 1
    assert result.detail["cleanup_verified"] is True
    assert result.action_count == 3


def test_bulk_no_claimable_marker_without_empty_state_is_uncertain(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "bulk_read.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code.value == "outcome_uncertain"
    assert result.action_count == 0


def test_read_mail_icon_alone_does_not_prove_no_reward_state(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "mail_close.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.action_count == 0
    assert result.detail["claim_action_count"] == 0


def test_bulk_account_conflict_never_navigates_or_claims(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[{"conflict.png": True, "city.png": True, "more_button.png": True}])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code.value == "account_conflict"
    assert result.action_count == 0
    assert backend.input_calls == []


def test_preexisting_reward_popup_can_be_dismissed_but_is_not_claim_success(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"city.png": True, "more_button.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_request_submitted"] is False
    assert result.detail["claim_verified"] is False
    assert result.detail["claim_action_count"] == 0
    assert result.detail["cleanup_verified"] is True


def test_timeout_after_navigation_preserves_the_input_count(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(
        script=[{"city.png": True, "more_button.png": True}, {"more_page.png": True}],
        stage_delays={"screencap": [0, 2]},
    )
    result = _run_bulk(project, backend, timeout_seconds=1)
    assert result.reason_code.value == "timeout"
    assert result.action_count == 1
    assert result.detail["navigation_action_count"] == 1
    assert result.detail["claim_action_count"] == 0


def test_bulk_waits_for_delayed_reward_popup_without_resubmitting_request(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True, "more_button.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.COMPLETED
    assert result.detail["claim_action_count"] == 1
    assert result.action_count == 3


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (MaaTimeout("控件识别超时"), RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT),
        (MaaStopped("识别期间停止"), RunStatus.CANCELLED, ReasonCode.USER_CANCELLED),
        (MaaError("识别 SDK 错误"), RunStatus.NEEDS_ATTENTION, ReasonCode.OUTCOME_UNCERTAIN),
    ],
)
def test_control_recognition_stop_or_timeout_after_navigation_keeps_action_counts(
    tmp_path, error, status, reason,
):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"city.png": True, "more_button.png": True},
        {"more_page.png": True, "mail_button.png": True},
    ])
    backend.recognition_errors = {"mail_button.png": error}
    result = _run_bulk(project, backend)
    assert result.status is status
    assert result.reason_code is reason
    assert result.action_count == 1
    assert result.detail["navigation_action_count"] == 1
    assert result.detail["claim_action_count"] == 0
    assert result.detail["claim_request_submitted"] is False


def test_unconfirmed_controller_click_preserves_bulk_detail_and_blocks_device(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[{"city.png": True, "more_button.png": True}])
    backend.click_error = MaaStopUnconfirmed("Controller click job 未确认完成")
    result = _run_bulk(project, backend)
    marker = Path(result.detail["device_block"])
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.STOP_UNCONFIRMED
    assert result.action_count == 1
    assert result.detail["navigation_action_count"] == 1
    assert result.detail["claim_action_count"] == 0
    assert result.detail["claim_request_submitted"] is False
    assert result.detail["action_count_is_upper_bound"] is True
    assert marker.is_file()


def test_close_control_recognition_timeout_preserves_claimed_state_and_counts(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_item.png": True, "reward_close.png": True},
    ])
    backend.recognition_errors = {"reward_close.png": MaaTimeout("奖励关闭控件识别超时")}
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.TIMEOUT
    assert result.action_count == 1
    assert result.detail["claim_action_count"] == 1
    assert result.detail["claim_verified"] is True
    assert result.detail["cleanup_verified"] is False
    assert result.detail["input_error"] == "timeout"


def test_reward_panel_marker_without_reward_content_cannot_confirm_claim(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "reward_items.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True, "more_button.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_verified"] is False
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["reward_evidence_complete"] is False
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"


def test_known_reward_with_unregistered_items_cleans_up_without_claiming_success(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["claim_verified"] is False
    assert result.detail["reward_evidence_complete"] is False
    assert result.detail["unverified_reward_popup_count"] == 1
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"


def test_later_unregistered_reward_preserves_uncertainty_after_returning_to_city(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        {"reward_popup.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_verified"] is True
    assert result.detail["reward_evidence_complete"] is False
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 3
    assert result.detail["cleanup_verified"] is True


def test_existing_reward_recovery_closes_mail_without_claiming_new_pending_mail(tmp_path):
    project = _make_bulk_project(tmp_path)
    backend = FakeBackend(script=[
        {"reward_popup.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True,
         "bulk_read.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["cleanup_only"] is True
    assert result.detail["claim_request_submitted"] is False
    assert result.detail["claim_action_count"] == 0
    assert result.detail["navigation_action_count"] == 0
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"


def test_unverified_reward_page_does_not_authorize_safe_cleanup(tmp_path):
    project = _make_bulk_project(tmp_path)
    path = project.resource_root / "mail_bulk" / "pages.json"
    pages = json.loads(path.read_text(encoding="utf-8"))
    next(page for page in pages["pages"] if page["page_id"] == "reward_popup")["verified"] = False
    path.write_text(json.dumps(pages), encoding="utf-8")
    backend = FakeBackend(script=[{"reward_popup.png": True, "reward_close.png": True}])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
    assert result.action_count == 0
    assert backend.input_calls == []


def test_reward_close_waits_through_partial_foreground_animation_without_background_click(tmp_path):
    project = _make_bulk_project(tmp_path)
    path = project.resource_root / "mail_bulk" / "pages.json"
    pages = json.loads(path.read_text(encoding="utf-8"))
    next(page for page in pages["pages"] if page["page_id"] == "reward_popup")["identity"].append(
        {"template": "reward_hint.png", "verified": True}
    )
    path.write_text(json.dumps(pages), encoding="utf-8")
    config_path = project.resource_root / "mail_bulk" / "mail_bulk.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["transition_wait_seconds"] = 0.6
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        {"reward_popup.png": True, "reward_hint.png": True, "reward_item.png": True, "reward_close.png": True},
        {"reward_popup.png": True, "mail_title.png": True, "system_tab.png": True,
         "bulk_read.png": True, "mail_close.png": True},
        {"reward_popup.png": True, "reward_hint.png": True, "reward_item_second.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.detail["cleanup_verified"] is True
    assert result.detail["claim_action_count"] == 0
    assert result.detail["dismiss_action_count"] == 3
    assert result.detail["final_page_id"] == "main_city"
    assert result.status is RunStatus.NEEDS_ATTENTION  # Recovery does not certify the old claim.


@pytest.mark.parametrize("route", ["recover_direct", "recover_mail", "claim_mail"])
def test_city_background_with_account_conflict_does_not_confirm_cleanup(tmp_path, route):
    project = _make_bulk_project(tmp_path)
    script = []
    if route == "claim_mail":
        script.append({"mail_title.png": True, "system_tab.png": True,
                       "pending.png": True, "bulk_read.png": True})
    script.append({"reward_popup.png": True, "reward_item.png": True, "reward_close.png": True})
    if route != "recover_direct":
        script.append({"mail_title.png": True, "system_tab.png": True,
                       "empty_state.png": True, "mail_close.png": True})
    script.append({"city.png": True, "conflict.png": True})
    backend = FakeBackend(script=script)
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.ACCOUNT_CONFLICT
    assert result.detail["cleanup_verified"] is False
    assert result.detail["claim_action_count"] == (1 if route == "claim_mail" else 0)
    assert result.detail["dismiss_action_count"] == (1 if route == "recover_direct" else 2)


def _with_reward_hint_identity(project, transition_wait_seconds: float = 0.6) -> None:
    path = project.resource_root / "mail_bulk" / "pages.json"
    pages = json.loads(path.read_text(encoding="utf-8"))
    next(page for page in pages["pages"] if page["page_id"] == "reward_popup")["identity"].append(
        {"template": "reward_hint.png", "verified": True}
    )
    path.write_text(json.dumps(pages), encoding="utf-8")
    config_path = project.resource_root / "mail_bulk" / "mail_bulk.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["transition_wait_seconds"] = transition_wait_seconds
    config_path.write_text(json.dumps(config), encoding="utf-8")


# 2026-10-20 实机：一键阅读后第一帧奖励动画未完成，只露出关闭提示，标题尚未出现，
# 背后邮件列表完整，分类为 mail_inbox/reward_popup 歧义。
_CLAIM_ANIMATION_FRAME = {"reward_hint.png": True, "mail_title.png": True, "system_tab.png": True,
                          "bulk_read.png": True, "mail_close.png": True}


def test_bulk_waits_through_first_reward_animation_after_claim(tmp_path):
    project = _make_bulk_project(tmp_path)
    _with_reward_hint_identity(project)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        _CLAIM_ANIMATION_FRAME,
        {"reward_popup.png": True, "reward_hint.png": True, "reward_item.png": True, "reward_close.png": True},
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.COMPLETED, result.detail["message"]
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert [call[0] for call in backend.input_calls].count("click") == 3


def test_bulk_persistent_claim_animation_stops_without_further_input(tmp_path):
    project = _make_bulk_project(tmp_path)
    _with_reward_hint_identity(project, transition_wait_seconds=0.3)
    backend = FakeBackend(script=[
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        _CLAIM_ANIMATION_FRAME,
    ])
    result = _run_bulk(project, backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.UNKNOWN_PAGE
    assert result.detail["claim_action_count"] == 1
    assert result.action_count == 1
    assert backend.screencap_count >= 3  # 只读等待期间多次截图，未再输入


def test_checked_in_reward_popup_limit_fits_phase_one_action_cap():
    from sgbltx_helper.contracts import MAX_MAX_ACTIONS

    config = json.loads(
        (Path(__file__).resolve().parents[1] / "resource" / "mail_bulk" / "mail_bulk.json").read_text(encoding="utf-8")
    )
    # 实机一次一键阅读出现 16 张奖励弹窗；导航 2 + 领取 1 + 邮件 X 1 共用同一动作上限。
    assert config["max_reward_popups"] >= 16
    assert config["max_reward_popups"] + 4 <= MAX_MAX_ACTIONS


def test_bulk_closes_sixteen_rewards_in_one_run_within_action_cap(tmp_path):
    project = _make_bulk_project(tmp_path)
    config_path = project.resource_root / "mail_bulk" / "mail_bulk.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_reward_popups"] = 26
    config_path.write_text(json.dumps(config), encoding="utf-8")
    rewards = [
        {"reward_popup.png": True, "mail_title.png": True,
         ("reward_item.png" if i % 2 == 0 else "reward_item_second.png"): True, "reward_close.png": True}
        for i in range(16)
    ]
    backend = FakeBackend(script=[
        {"city.png": True, "more_button.png": True},
        {"more_page.png": True, "mail_button.png": True},
        {"mail_title.png": True, "system_tab.png": True, "pending.png": True, "bulk_read.png": True},
        *rewards,
        {"mail_title.png": True, "system_tab.png": True, "read.png": True, "empty_state.png": True, "mail_close.png": True},
        {"city.png": True},
    ])
    result = _run_bulk(project, backend, max_actions=30)
    assert result.status is RunStatus.COMPLETED, result.detail["message"]
    assert result.detail["reward_popup_count"] == 16
    assert result.action_count == 20
