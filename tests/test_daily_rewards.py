from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sgbltx_helper.classification import classify
from sgbltx_helper.contracts import DeviceProfile, InspectionResult, ReasonCode, RunMode, RunStatus, TaskRequest
from sgbltx_helper.maa_adapter import MaaBackend, MaaError, MaaStopUnconfirmed, MaaStopped, MaaTimeout
from sgbltx_helper.pages import load_page_catalog
from sgbltx_helper.runner import StopController, run
from conftest import FakeBackend, make_project


PROJECT = Path(__file__).resolve().parents[1]
RESOURCE = PROJECT / "resource" / "daily_rewards"
RUNS = PROJECT / "artifacts" / "runs"


def _bgr(path: Path) -> np.ndarray:
    rgb = np.asarray(Image.open(path).convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


@pytest.fixture(scope="module")
def daily_backend() -> MaaBackend:
    backend = MaaBackend(adb_path="", address="")
    backend.load_resource(RESOURCE, task_id="daily_rewards_claim")
    yield backend
    backend.close()


def test_task_entry_control_is_scoped_to_verified_home_and_saved_states(daily_backend: MaaBackend) -> None:
    config = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))
    control = config["controls"]["tasks_entry"]
    catalog = load_page_catalog(RESOURCE / "pages.json")

    positives = [
        RUNS / "dryrun-20261007T050442Z-197612" / "before.png",
        RUNS / "dryrun-20261006T093831Z-188456" / "before.png",
    ]
    negatives = [
        RUNS / "run-20261007T025546Z-201920" / "after.png",
        RUNS / "run-20261006T081132Z-173092" / "after.png",
        RUNS / "dryrun-20261005T133916Z-155900" / "before.png",
        RUNS / "dryrun-20261006T094103Z-188892" / "before.png",
    ]
    for path in positives:
        if not path.is_file():
            pytest.skip(f"真实主城正样本缺失：{path}")
        image = _bgr(path)
        outcome = daily_backend.recognize(image, control["template"], tuple(control["roi"]), control["threshold"])
        assert outcome.hit, f"主城任务入口未命中 {path}, score={outcome.score}"
        assert classify(daily_backend, image, catalog).page.page_id == "main_city"
    for path in negatives:
        if not path.is_file():
            pytest.skip(f"真实负样本缺失：{path}")
        image = _bgr(path)
        # The task icon can remain visible behind a menu or reward overlay. Its
        # match alone must never authorize input; the main-city identity is the gate.
        daily_backend.recognize(image, control["template"], tuple(control["roi"]), control["threshold"])
        classified = classify(daily_backend, image, catalog)
        assert classified.page is None or classified.page.page_id != "main_city"


def test_task_entry_control_ignores_map_background_behind_icon(daily_backend: MaaBackend) -> None:
    """任务图标外角露出的地图随视角变化（石路/草地），入口模板只能取图标盘内文字。"""
    control = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))["controls"]["tasks_entry"]
    x, y, w, h = control["roi"]

    def canvas_with(patch: Image.Image, at: tuple[int, int]) -> np.ndarray:
        canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
        canvas.paste(patch.convert("RGB"), at)
        return _bgr_from_image(canvas)

    # 2026-10-20 实机主城：图标外角为草地（ROI 裁剪样本，不含账号信息）
    grass = Image.open(PROJECT / "tests" / "fixtures" / "daily_task_entry_grass_roi.png")
    outcome = daily_backend.recognize(canvas_with(grass, (x, y)), control["template"], (x, y, w, h), control["threshold"])
    assert outcome.hit, f"草地背景下任务入口未命中, score={outcome.score}"

    # 2026-10 早期主城：图标外角为石路（原入口模板本身，贴回实机位置 118,1664）
    stone = Image.open(PROJECT / "tests" / "fixtures" / "daily_task_entry_stone_label.png")
    outcome = daily_backend.recognize(canvas_with(stone, (118, 1664)), control["template"], (x, y, w, h), control["threshold"])
    assert outcome.hit, f"石路背景下任务入口未命中, score={outcome.score}"

    # 空白 ROI 不得命中
    blank = daily_backend.recognize(canvas_with(Image.new("RGB", (w, h)), (x, y)), control["template"], (x, y, w, h), control["threshold"])
    assert not blank.hit


def _bgr_from_image(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


def test_daily_task_hub_and_tab_control_use_real_page_sample(daily_backend: MaaBackend) -> None:
    config = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))
    catalog = load_page_catalog(RESOURCE / "pages.json")
    sample = RUNS / "run-20261007T052223Z-192616" / "after.png"
    if not sample.is_file():
        pytest.skip(f"真实政务页正样本缺失：{sample}")
    image = _bgr(sample)
    classified = classify(daily_backend, image, catalog)
    assert classified.page is not None and classified.page.page_id == "daily_hub"
    tab = config["controls"]["daily_tab"]
    outcome = daily_backend.recognize(image, tab["template"], tuple(tab["roi"]), tab["threshold"])
    assert outcome.hit and outcome.center is not None, f"日常任务入口未命中，score={outcome.score}"

    for path in [
        RUNS / "dryrun-20261007T050442Z-197612" / "before.png",
        RUNS / "dryrun-20261006T094103Z-188892" / "before.png",
        RUNS / "run-20261005T150342Z-154560" / "after.png",
    ]:
        if not path.is_file():
            pytest.skip(f"已保存负样本缺失：{path}")
        other = _bgr(path)
        cls = classify(daily_backend, other, catalog)
        assert cls.page is None or cls.page.page_id != "daily_hub"


def test_daily_task_list_identity_uses_real_page_and_rejects_hub(daily_backend: MaaBackend) -> None:
    config = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))
    catalog = load_page_catalog(RESOURCE / "pages.json")
    positives = [RUNS / "dryrun-20261007T053223Z-180312" / "before.png"]
    negatives = [
        RUNS / "run-20261007T052223Z-192616" / "after.png",
        RUNS / "dryrun-20261007T050442Z-197612" / "before.png",
        RUNS / "dryrun-20261006T094103Z-188892" / "before.png",
        RUNS / "run-20261005T150342Z-154560" / "after.png",
        RUNS / "run-20261006T081132Z-173092" / "after.png",
    ]
    for path in positives:
        if not path.is_file():
            pytest.skip(f"真实日常任务页正样本缺失：{path}")
        cls = classify(daily_backend, _bgr(path), catalog)
        assert cls.page is not None and cls.page.page_id == "daily_tasks"
        control = config["controls"]["daily_back"]
        outcome = daily_backend.recognize(_bgr(path), control["template"], tuple(control["roi"]), control["threshold"])
        assert outcome.hit and outcome.center is not None, f"日常任务返回控件未命中，score={outcome.score}"
        marker = config["read_only_markers"]["daily_tasks_zero_count"]
        count = daily_backend.recognize(_bgr(path), marker["template"], tuple(marker["roi"]), marker["threshold"])
        assert count.hit, f"0/23 只读计数模板未命中，score={count.score}"
    for path in negatives:
        if not path.is_file():
            pytest.skip(f"真实政务页负样本缺失：{path}")
        cls = classify(daily_backend, _bgr(path), catalog)
        assert cls.page is None or cls.page.page_id != "daily_tasks"


def test_daily_batch_controls_and_reward_foreground_use_real_maa_samples(daily_backend: MaaBackend) -> None:
    config = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))
    catalog = load_page_catalog(RESOURCE / "pages.json")
    fresh = RUNS / "dryrun-20261007T080439Z-196708" / "before.png"
    announcement = RUNS / "dryrun-20261007T082025Z-177336" / "before.png"
    old_gray = RUNS / "dryrun-20261007T053223Z-180312" / "before.png"
    reward = RUNS / "run-20261007T093435Z-203824" / "after_3.png"
    for path in (fresh, announcement, old_gray, reward):
        if not path.is_file():
            pytest.skip(f"日常领取/奖励真实样本缺失：{path}")

    fresh_image = _bgr(fresh)
    fresh_cls = classify(daily_backend, fresh_image, catalog)
    assert fresh_cls.page is not None and fresh_cls.page.page_id == "daily_tasks"
    batch = config["controls"]["daily_claim_all"]
    enabled = daily_backend.recognize(fresh_image, batch["template"], tuple(batch["roi"]), batch["threshold"])
    assert enabled.hit and enabled.center is not None
    assert not fresh_cls.blockers
    for item in config["daily_projects"]:
        title = daily_backend.recognize(
            fresh_image, item["template"], tuple(item["search_roi"]), item["threshold"],
        )
        assert title.hit and title.box is not None, item["project_id"]
        _, y, _, _ = title.box
        row_y = max(0, y - 12)
        row = daily_backend.recognize(
            fresh_image, item["row_claim_template"], (800, row_y, 270, min(150, fresh_image.shape[0] - row_y)),
            item["row_claim_threshold"],
        )
        assert row.hit and row.center is not None, item["project_id"]

    gray_image = _bgr(old_gray)
    disabled = daily_backend.recognize(gray_image, batch["template"], tuple(batch["roi"]), batch["threshold"])
    assert not disabled.hit
    gray_control = config["controls"]["daily_claim_all_disabled"]
    gray_hit = daily_backend.recognize(
        gray_image, gray_control["template"], tuple(gray_control["roi"]), gray_control["threshold"],
    )
    assert gray_hit.hit and gray_hit.center is not None and gray_hit.score >= 0.97
    enabled_gray = daily_backend.recognize(
        fresh_image, gray_control["template"], tuple(gray_control["roi"]), gray_control["threshold"],
    )
    assert not enabled_gray.hit
    assert not classify(daily_backend, gray_image, catalog).blockers
    reward_cls = classify(daily_backend, _bgr(reward), catalog)
    assert reward_cls.page is not None and reward_cls.page.page_id == "daily_reward_popup"
    # The disabled-button crop can match the darkened background beneath this
    # foreground. The state machine must gate it by the foreground page first.
    assert reward_cls.page.page_id != "daily_tasks"
    reward_gray_hit = daily_backend.recognize(
        _bgr(reward), gray_control["template"], tuple(gray_control["roi"]), gray_control["threshold"],
    )
    assert reward_gray_hit.hit  # Deliberately demonstrates why page identity is a required gate.
    hint = config["controls"]["daily_reward_close"]
    close = daily_backend.recognize(_bgr(reward), hint["template"], tuple(hint["roi"]), hint["threshold"])
    assert close.hit and close.center is not None
    announcement_cls = classify(daily_backend, _bgr(announcement), catalog)
    assert any(item.page_id == "unrelated_announcement" for item in announcement_cls.blockers)
    # The background daily page can remain visible in the classification object;
    # callers must treat the blocker as dominant and authorize no action.
    assert announcement_cls.page is None or announcement_cls.page.page_id == "daily_tasks"


def test_known_announcement_blank_dismiss_target_is_recognized_only_in_daily_context(daily_backend: MaaBackend) -> None:
    config = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))
    control = config["controls"]["dismiss_known_announcement"]
    positive_paths = [
        RUNS / "dryrun-20261007T083623Z-199088" / "before.png",
        RUNS / "dryrun-20261007T082025Z-177336" / "before.png",
    ]
    negative_paths = [
        RUNS / "dryrun-20261007T080439Z-196708" / "before.png",
        RUNS / "dryrun-20261006T094103Z-188892" / "before.png",
        RUNS / "run-20261007T025546Z-201920" / "after.png",
    ]
    for path in positive_paths:
        if not path.is_file():
            pytest.skip(f"公告空白区实机正样本缺失：{path}")
        image = _bgr(path)
        classified = classify(daily_backend, image, load_page_catalog(RESOURCE / "pages.json"))
        target = daily_backend.recognize(
            image, control["template"], tuple(control["roi"]), control["threshold"],
        )
        assert any(item.page_id == "unrelated_announcement" for item in classified.blockers)
        assert classified.page is not None and classified.page.page_id == "daily_tasks"
        assert target.hit and target.center is not None, f"空白目标未命中 {path}, score={target.score}"
    for path in negative_paths:
        if not path.is_file():
            pytest.skip(f"公告空白区负样本缺失：{path}")
        image = _bgr(path)
        classified = classify(daily_backend, image, load_page_catalog(RESOURCE / "pages.json"))
        assert not any(item.page_id == "unrelated_announcement" for item in classified.blockers)
        # The blank ornament may exist on the background page; the executor
        # must require the exact blocker identity before consulting this target.


def test_live_daily_reward_overlay_takes_precedence_over_background_daily_page(daily_backend: MaaBackend) -> None:
    sample = RUNS / "run-20261007T093435Z-203824" / "after_3.png"
    if not sample.is_file():
        pytest.skip(f"实机日常奖励弹窗样本缺失：{sample}")
    image = _bgr(sample)
    catalog = load_page_catalog(RESOURCE / "pages.json")
    classified = classify(daily_backend, image, catalog)
    assert classified.page is not None and classified.page.page_id == "daily_reward_popup"
    assert classified.ambiguous_pages == []
    close = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))["controls"]["daily_reward_close"]
    outcome = daily_backend.recognize(image, close["template"], tuple(close["roi"]), close["threshold"])
    assert outcome.hit and outcome.center is not None


def test_live_defense_order_announcement_variant_blocks_daily_page_and_has_blank_target(daily_backend: MaaBackend) -> None:
    sample = RUNS / "dryrun-20261007T125433Z-196872" / "before.png"
    if not sample.is_file():
        pytest.skip(f"新鲜守城令公告截图缺失：{sample}")
    image = _bgr(sample)
    catalog = load_page_catalog(RESOURCE / "pages.json")
    classified = classify(daily_backend, image, catalog)
    assert classified.page is not None and classified.page.page_id == "daily_tasks"
    assert not classified.ambiguous_pages
    assert any(item.page_id == "unrelated_announcement_defense" for item in classified.blockers)
    config = json.loads((RESOURCE / "daily_rewards.json").read_text(encoding="utf-8"))
    blank = config["controls"]["dismiss_known_announcement"]
    target = daily_backend.recognize(image, blank["template"], tuple(blank["roi"]), blank["threshold"])
    assert target.hit and target.center is not None


def test_main_city_and_hub_navigation_waits_for_fresh_expected_pages(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_daily_pages"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        {"main_city_resource_coin.png": True, "main_city_wall_marker.png": True, "daily_task_entry_label.png": True},
        {"main_city_resource_coin.png": True, "main_city_wall_marker.png": True},  # stale transition frame
        {"daily_hub_title.png": True, "daily_tab_label.png": True},
        {"daily_hub_title.png": True, "daily_tab_label.png": True},  # stale selected-tab frame
        {"daily_tasks_header.png": True, "daily_tasks_completed_label.png": True, "daily_tasks_back_arrow.png": True},
        {"main_city_resource_coin.png": True, "main_city_wall_marker.png": True},
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
    assert result.action_count == 3
    assert result.detail["navigation_action_count"] == 2
    assert result.detail["dismiss_action_count"] == 1
    assert result.detail["claim_action_count"] == 0
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls] == ["click", "click", "click"]
    assert backend.screencap_count >= 6


def _daily_project(tmp_path: Path):
    project = make_project(tmp_path)
    shutil.copytree(RESOURCE, project.resource_root / "daily_rewards")
    # Positive post-state signals are simulated only in the fake project;
    # production resources have no verified per-row claimed-state sample yet.
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for item in config["daily_projects"]:
        item["claimed_state"] = {
            "template": f"fake_claimed_{item['project_id']}.png",
            "threshold": 0.97, "verified": True,
        }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return project


def _daily_request(mode: RunMode) -> TaskRequest:
    return TaskRequest(
        task_id="daily_rewards_claim",
        profile=DeviceProfile("local-test-mail", "test_mail", "", "emulator-5554", "com.sgbltx.goodgame", (1080, 1920)),
        mode=mode,
    )


def test_daily_dry_run_returns_inspection_without_any_input(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    backend = FakeBackend(script=[{
        "main_city_resource_coin.png": True,
        "main_city_wall_marker.png": True,
    }])
    result = run(_daily_request(RunMode.DRY_RUN), backend, project.root / "artifacts" / "locks", project.root)
    assert isinstance(result, InspectionResult)
    assert result.page_id == "main_city"
    assert backend.input_calls == []


def _daily_claim_state(*, available: bool, reward: bool = False, home: bool = False,
                       claimed: bool = True) -> dict[str, bool]:
    state: dict[str, bool] = {}
    if home:
        state.update({"main_city_resource_coin.png": True, "main_city_wall_marker.png": True})
        return state
    if reward:
        state.update({"daily_reward_title.png": True, "daily_reward_dismiss_hint_live.png": True,
                      "daily_reward_gold.png": True})
        return state
    state.update({
        "daily_tasks_header.png": True,
        "daily_tasks_completed_label.png": True,
        "daily_tasks_back_arrow.png": True,
        "daily_claim_all_enabled.png": available,
        "daily_claim_all_disabled.png": not available and claimed,
        "daily_project_treasure.png": True,
        "daily_project_spend.png": True,
        "daily_project_bookshop.png": True,
        "daily_project_scholar.png": True,
    })
    if available:
        state.update({
            "daily_row_claim_enabled.png": True,
            "daily_row_claim_treasure.png": True,
            "daily_row_claim_spend.png": True,
            "daily_row_claim_bookshop.png": True,
            "daily_row_claim_scholar.png": True,
        })
    elif claimed:
        # Explicit positive test observations; absence of green is not this signal.
        for project_id in ("treasure_shop", "spend_gold", "bookshop_visit", "scholar_visit"):
            state[f"fake_claimed_{project_id}.png"] = True
    return state


def _short_daily_wait(project, seconds=0.03) -> None:
    path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update({"transition_wait_seconds": seconds, "max_daily_pages": 1})
    path.write_text(json.dumps(config), encoding="utf-8")


def _loop_test_project(tmp_path):
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(max_batch_rounds=5, transition_wait_seconds=0.03, max_daily_pages=1)
    config["controls"]["daily_claim_all_disabled"] = {
        "template": "daily_claim_all_disabled.png", "roi": [0, 0, 0, 0],
        "threshold": 0.97, "verified": True,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return project


def _loop_state(*, green):
    state = _daily_claim_state(available=green, claimed=False)
    for key in list(state):
        if key.startswith("daily_project_") or key.startswith("daily_row_claim_"):
            del state[key]
    state["daily_claim_all_disabled.png"] = not green
    return state


def test_daily_two_confirmed_batches_then_gray_return_home(tmp_path):
    project = _loop_test_project(tmp_path)
    backend = FakeBackend(script=[
        _loop_state(green=True),
        _daily_claim_state(available=False, reward=True),
        _loop_state(green=True),
        _daily_claim_state(available=False, reward=True),
        _loop_state(green=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.COMPLETED
    assert result.detail["claim_action_count"] == 2
    assert result.detail["dismiss_action_count"] == 3
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"


def test_daily_explicit_gray_returns_without_claiming(tmp_path):
    project = _loop_test_project(tmp_path)
    backend = FakeBackend(script=[
        _loop_state(green=False), _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NOTHING_TO_CLAIM
    assert result.detail["claim_action_count"] == 0
    assert result.detail["dismiss_action_count"] == 1
    assert result.detail["cleanup_verified"] is True


@pytest.mark.parametrize("signals", [(True, True), (False, False)])
def test_daily_claim_button_conflict_or_unknown_never_claims(tmp_path, signals):
    project = _loop_test_project(tmp_path)
    green, gray = signals
    state = _loop_state(green=False)
    state["daily_claim_all_enabled.png"] = green
    state["daily_claim_all_disabled.png"] = gray
    backend = FakeBackend(script=[state, _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root)
    assert result.detail["claim_action_count"] == 0
    assert result.status is RunStatus.NEEDS_ATTENTION


def test_daily_batch_round_cap_returns_home_without_claiming_done(tmp_path):
    project = _loop_test_project(tmp_path)
    path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["max_batch_rounds"] = 1
    path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        _loop_state(green=True),
        _daily_claim_state(available=False, reward=True),
        _loop_state(green=True),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.PARTIAL_PROGRESS
    assert result.detail["claim_action_count"] == 1
    assert result.detail["cleanup_verified"] is True
    assert result.detail["batch_rounds"][0]["post_batch_state"] == "green"


def test_daily_unknown_items_do_not_prevent_confirmed_closed_loop(tmp_path):
    project = _loop_test_project(tmp_path)
    reward = _daily_claim_state(available=False, reward=True)
    reward.pop("daily_reward_gold.png", None)
    backend = FakeBackend(script=[
        _loop_state(green=True), reward, _loop_state(green=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.COMPLETED
    assert result.reason_code is ReasonCode.CLAIM_CONFIRMED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["cleanup_verified"] is True
    assert result.detail["claim_verified"] is True
    assert result.detail["confirmed_batch_round_count"] == 1
    assert result.detail["uncertain_batch_round_count"] == 0
    assert result.detail["batch_rounds"][0]["reward_content_verified"] is False


def test_two_daily_rounds_confirm_flow_even_if_first_items_are_unknown(tmp_path):
    project = _loop_test_project(tmp_path)
    unknown = _daily_claim_state(available=False, reward=True)
    unknown.pop("daily_reward_gold.png")
    backend = FakeBackend(script=[_loop_state(green=True), unknown, _loop_state(green=True),
                                  _daily_claim_state(available=False, reward=True), _loop_state(green=False),
                                  _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.COMPLETED
    assert result.detail["claim_action_count"] == 2
    assert result.detail["confirmed_batch_round_count"] == 2
    assert result.detail["uncertain_batch_round_count"] == 0
    assert result.detail["cleanup_verified"] is True
    assert [r["post_batch_state"] for r in result.detail["batch_rounds"]] == ["green", "gray"]


def test_daily_submitted_round_without_reward_popup_is_never_retried(tmp_path):
    project = _loop_test_project(tmp_path)
    backend = FakeBackend(script=[_loop_state(green=True), _loop_state(green=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert len(backend.input_calls) == 2  # One claim attempt and independent daily-page cleanup.
    assert len([event for event in backend.input_calls if event[0] == "click"]) == 2


def test_daily_stop_after_batch_input_never_issues_cleanup_or_another_claim(tmp_path):
    project = _loop_test_project(tmp_path)
    stop = StopController()

    class StopAfterFirstInput(FakeBackend):
        def click(self, x, y, deadline=None, should_stop=None):
            result = super().click(x, y, deadline, should_stop)
            stop.request_stop()
            return result

    backend = StopAfterFirstInput(script=[_loop_state(green=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend,
                 project.root / "artifacts" / "locks", project.root, stop)
    assert result.status is RunStatus.CANCELLED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 0
    assert backend.input_calls == [("click", 130, 130)]


@pytest.mark.parametrize("cue", ["daily_unrelated_announcement_title.png", "daily_unrelated_announcement_forward.png"])
def test_partial_announcement_never_authorizes_background_or_blank_click(tmp_path, cue):
    project = _daily_project(tmp_path)
    _short_daily_wait(project)
    current = _daily_claim_state(available=True)
    current[cue] = True
    current["daily_announcement_dismiss_blank.png"] = True
    backend = FakeBackend(script=[current, _daily_claim_state(available=False),
                                  _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.UNKNOWN_PAGE
    assert result.action_count == 0
    assert backend.input_calls == []


def test_missing_claim_buttons_without_positive_state_never_confirm_rewards(tmp_path):
    project = _daily_project(tmp_path)
    _short_daily_wait(project)
    backend = FakeBackend(script=[_daily_claim_state(available=True),
                                  _daily_claim_state(available=False, claimed=False),
                                  _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_verified"] is False
    assert result.detail["post_batch_state_verified"] is False
    assert result.detail["cleanup_verified"] is True
    assert result.detail["claim_action_count"] == 1


def test_missing_buttons_without_positive_state_do_not_prove_nothing_to_claim(tmp_path):
    project = _daily_project(tmp_path)
    _short_daily_wait(project)
    backend = FakeBackend(script=[_daily_claim_state(available=False, claimed=False),
                                  _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is not ReasonCode.NOTHING_TO_CLAIM
    assert result.detail["claim_action_count"] == 0


@pytest.mark.parametrize("error,status,reason", [
    (MaaStopped("test stop"), RunStatus.CANCELLED, ReasonCode.USER_CANCELLED),
    (MaaTimeout("test timeout"), RunStatus.NEEDS_ATTENTION, ReasonCode.TIMEOUT),
])
def test_cleanup_preserves_stop_and_timeout_result(tmp_path, error, status, reason):
    project = _daily_project(tmp_path)
    backend = FakeBackend(script=[_daily_claim_state(available=False, reward=True)], click_error=error)
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is status
    assert result.reason_code is reason
    assert result.detail["claim_action_count"] == 0
    assert result.detail["dismiss_action_count"] == 1
    events = [json.loads(line) for line in Path(result.evidence_paths[-2]).read_text(encoding="utf-8").splitlines()]
    assert len([event for event in events if event.get("event") == "run_finished"]) == 1


class _DailyFrameBackend(FakeBackend):
    def screencap(self, deadline=None, should_stop=None):  # type: ignore[no-untyped-def]
        image = super().screencap(deadline, should_stop)
        frame_id = self._current.get("_reward_frame_id")
        if frame_id is not None:
            image[650:1200, 100:980] = int(frame_id)
        return image


class _OnePixelRewardBackend(FakeBackend):
    def screencap(self, deadline=None, should_stop=None):
        image = super().screencap(deadline, should_stop)
        image[700, 200, 0] = self._current.get("_animation_pixel", 0)
        return image


@pytest.mark.parametrize("cleanup_only", [False, True])
def test_reward_animation_does_not_authorize_repeated_dismissal(tmp_path, cleanup_only):
    project = _loop_test_project(tmp_path)
    _short_daily_wait(project, seconds=0.5)
    frames = [] if cleanup_only else [_loop_state(green=True)]
    for pixel in (1, 2, 3):
        popup = _daily_claim_state(available=False, reward=True)
        popup["_animation_pixel"] = pixel
        frames.append(popup)
    frames.extend([_loop_state(green=False), _daily_claim_state(available=False, home=True)])
    backend = _OnePixelRewardBackend(script=frames)
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.detail["reward_popup_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["claim_action_count"] == (0 if cleanup_only else 1)
    assert result.detail["cleanup_verified"] is True


@pytest.mark.parametrize("cleanup_only", [False, True])
def test_reward_a_b_a_closes_each_presentation_once(tmp_path, cleanup_only):
    project = _loop_test_project(tmp_path)
    popup_a = _daily_claim_state(available=False, reward=True)
    popup_a["_reward_frame_id"] = 1
    popup_b = _daily_claim_state(available=False, reward=True)
    popup_b.pop("daily_reward_gold.png")
    popup_b.update({"_reward_frame_id": 2, "daily_reward_silver.png": True})
    frames = [] if cleanup_only else [_loop_state(green=True)]
    frames.extend([popup_a, popup_b, popup_a, _loop_state(green=False),
                   _daily_claim_state(available=False, home=True)])
    backend = _DailyFrameBackend(script=frames)
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.detail["reward_popup_count"] == 3
    assert result.detail["dismiss_action_count"] == 4
    assert result.detail["cleanup_verified"] is True
    assert result.status is (RunStatus.NEEDS_ATTENTION if cleanup_only else RunStatus.COMPLETED)


def test_partial_reward_cue_flicker_does_not_authorize_second_close(tmp_path):
    project = _loop_test_project(tmp_path)
    _short_daily_wait(project, seconds=0.5)
    popup = _daily_claim_state(available=False, reward=True)
    popup["_reward_frame_id"] = 7
    backend = _DailyFrameBackend(script=[_loop_state(green=True), popup, popup,
                                        {"daily_reward_title.png": True}, popup,
                                        _loop_state(green=False),
                                        _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.detail["reward_popup_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.status is RunStatus.COMPLETED


@pytest.mark.parametrize("error", [MaaTimeout("claim timeout"), MaaStopped("claim stopped")])
def test_terminal_claim_ledger_matches_persisted_snapshot(tmp_path, error):
    project = _loop_test_project(tmp_path)
    backend = FakeBackend(script=[_loop_state(green=True)], click_error=error)
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    stored = json.loads(Path(result.evidence_paths[-1]).read_text(encoding="utf-8"))
    assert stored == result.to_dict()
    assert stored["detail"]["batch_rounds"][0]["submission"] == "uncertain"
    assert stored["detail"]["uncertain_batch_round_count"] == 1
    assert len(backend.input_calls) == 1


@pytest.mark.parametrize("cleanup_only", [False, True])
def test_new_item_match_without_old_item_disappearing_is_not_new_popup(tmp_path, cleanup_only):
    project = _loop_test_project(tmp_path)
    _short_daily_wait(project, seconds=0.5)
    popup = _daily_claim_state(available=False, reward=True)
    more_matches = {**popup, "daily_reward_silver.png": True}
    frames = [] if cleanup_only else [_loop_state(green=True)]
    frames.extend([popup, more_matches, popup, _loop_state(green=False),
                   _daily_claim_state(available=False, home=True)])
    backend = FakeBackend(script=frames)
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.detail["reward_popup_count"] == 1
    assert result.detail["dismiss_action_count"] == 2
    assert result.detail["cleanup_verified"] is True


@pytest.mark.parametrize("error", [MaaTimeout("pre-click timeout"), MaaStopped("pre-click stop"),
                                  MaaError("recognition failed")])
def test_second_claim_target_error_preserves_first_round_and_inputs(tmp_path, error):
    project = _loop_test_project(tmp_path)

    class FailingPreClickBackend(FakeBackend):
        round_two_matches = 0

        def recognize(self, image, template, roi=(0, 0, 0, 0), threshold=0.85,
                      deadline=None, should_stop=None):
            if self._current.get("_round_two") and template == "daily_claim_all_enabled.png":
                self.round_two_matches += 1
                if self.round_two_matches == 2:
                    raise error  # First match observes the state; second authorizes input.
            return super().recognize(image, template, roi, threshold, deadline, should_stop)

    backend = FailingPreClickBackend(script=[_loop_state(green=True),
        _daily_claim_state(available=False, reward=True), {**_loop_state(green=True), "_round_two": True}])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.action_count == len(backend.input_calls) == 2
    assert result.detail["claim_action_count"] == 1
    assert result.detail["confirmed_batch_round_count"] == 1
    assert len(result.detail["batch_rounds"]) == 1
    assert result.status is (RunStatus.CANCELLED if isinstance(error, MaaStopped) else RunStatus.NEEDS_ATTENTION)
    assert json.loads(Path(result.evidence_paths[-1]).read_text(encoding="utf-8")) == result.to_dict()


def test_unconfirmed_sdk_stop_has_terminal_round_counts_and_persisted_snapshot(tmp_path):
    project = _loop_test_project(tmp_path)
    backend = FakeBackend(script=[_loop_state(green=True)], click_error=MaaStopUnconfirmed("stop pending"))
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.reason_code is ReasonCode.STOP_UNCONFIRMED
    assert result.detail["confirmed_batch_round_count"] == 0
    assert result.detail["uncertain_batch_round_count"] == 1
    assert result.detail["pending_batch_round_count"] == 0
    assert result.detail["batch_rounds"][0]["submission"] == "uncertain"
    result_path = project.root / "artifacts" / "runs" / result.run_id / "result.json"
    assert json.loads(result_path.read_text(encoding="utf-8")) == result.to_dict()
    assert len(backend.input_calls) == 1


def test_daily_claim_dry_run_lists_one_batch_candidate_without_input(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    backend = FakeBackend(script=[_daily_claim_state(available=True)])
    result = run(_daily_request(RunMode.DRY_RUN), backend, project.root / "artifacts" / "locks", project.root)
    assert isinstance(result, InspectionResult)
    assert result.page_id == "daily_tasks"
    assert any(candidate.action_id == "claim_daily_all" and candidate.verified for candidate in result.candidate_actions)
    assert backend.input_calls == []


def test_daily_claim_candidate_is_blocked_by_unrelated_foreground(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    state = _daily_claim_state(available=True)
    state.update({
        "daily_unrelated_announcement_title.png": True,
        "daily_unrelated_announcement_forward.png": True,
    })
    backend = FakeBackend(script=[state])
    result = run(_daily_request(RunMode.DRY_RUN), backend, project.root / "artifacts" / "locks", project.root)
    assert isinstance(result, InspectionResult)
    candidate = next(item for item in result.candidate_actions if item.action_id == "claim_daily_all")
    assert not candidate.verified
    assert backend.input_calls == []


def test_known_announcement_dismisses_once_at_verified_blank_then_continues(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_daily_pages"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    blocked = _daily_claim_state(available=True)
    blocked.update({
        "daily_unrelated_announcement_title.png": True,
        "daily_unrelated_announcement_forward.png": True,
        "daily_announcement_dismiss_blank.png": True,
    })
    backend = FakeBackend(script=[
        blocked,
        _daily_claim_state(available=True),
        _daily_claim_state(available=False, reward=True),
        _daily_claim_state(available=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.COMPLETED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 3  # announcement, reward popup, daily window
    assert result.detail["announcement_dismiss_attempted"] is True
    assert result.detail["announcement_dismissed"] is True
    assert result.detail["navigation_action_count"] == 0
    assert result.detail["cleanup_verified"] is True
    assert len(backend.input_calls) == 4  # announcement, one batch claim, reward, back


def test_known_announcement_without_verified_blank_target_still_blocks(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    blocked = _daily_claim_state(available=True)
    blocked.update({
        "daily_unrelated_announcement_title.png": True,
        "daily_unrelated_announcement_forward.png": True,
    })
    backend = FakeBackend(script=[blocked])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["claim_action_count"] == 0
    assert backend.input_calls == []


def test_known_announcement_persistent_after_one_blank_attempt_is_not_reclicked(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_daily_pages"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    blocked = _daily_claim_state(available=True)
    blocked.update({
        "daily_unrelated_announcement_title.png": True,
        "daily_unrelated_announcement_forward.png": True,
        "daily_announcement_dismiss_blank.png": True,
    })
    backend = FakeBackend(script=[blocked, blocked])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["announcement_dismiss_attempted"] is True
    assert result.detail["announcement_dismissed"] is False
    assert result.detail["claim_action_count"] == 0
    assert len(backend.input_calls) == 1


def test_daily_one_click_closes_unknown_reward_and_returns_home(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_daily_pages"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = _DailyFrameBackend(script=[
        _daily_claim_state(available=True),
        _daily_claim_state(available=False, reward=True),
        _daily_claim_state(available=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.COMPLETED
    assert result.reason_code is ReasonCode.CLAIM_CONFIRMED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["reward_popup_count"] == 1
    assert result.detail["post_batch_state_verified"] is True
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls] == ["click", "click", "click"]


def test_starting_on_daily_reward_popup_runs_cleanup_only_without_reclaiming(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"max_daily_pages": 1, "transition_wait_seconds": 0.05})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    popup = _daily_claim_state(available=False, reward=True)
    popup["_reward_frame_id"] = 3
    backend = _DailyFrameBackend(script=[
        popup,
        _daily_claim_state(available=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["cleanup_only"] is True
    assert result.detail["claim_request_submitted"] is False
    assert result.detail["claim_action_count"] == 0
    assert result.detail["reward_popup_count"] == 1
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls] == ["click", "click"]


def test_daily_one_click_waits_through_stale_reward_then_closes_next_popup(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"max_daily_pages": 1, "transition_wait_seconds": 0.5})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    popup_a = _daily_claim_state(available=False, reward=True)
    popup_a["_reward_frame_id"] = 1
    stale_a = _daily_claim_state(available=False, reward=True)
    stale_a["_reward_frame_id"] = 1
    popup_b = _daily_claim_state(available=False, reward=True)
    popup_b["_reward_frame_id"] = 2
    popup_b.pop("daily_reward_gold.png")
    popup_b["daily_reward_silver.png"] = True
    backend = _DailyFrameBackend(script=[
        _daily_claim_state(available=True),
        popup_a,
        stale_a,  # stale popup A after close
        popup_b,  # next popup B with different content frame
        _daily_claim_state(available=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 3  # two rewards plus daily-task window
    assert result.detail["cleanup_verified"] is True
    assert [call[0] for call in backend.input_calls].count("click") == 4


def test_daily_identical_next_reward_requires_observed_transition_frame(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"max_daily_pages": 1, "transition_wait_seconds": 0.5})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    popup = _daily_claim_state(available=False, reward=True)
    popup["_reward_frame_id"] = 7
    stale = _daily_claim_state(available=False, reward=True)
    stale["_reward_frame_id"] = 7
    next_popup = _daily_claim_state(available=False, reward=True)
    next_popup["_reward_frame_id"] = 7
    # Positively identify the uncovered daily page, with no reward cues.
    # Its button has not settled yet, so this frame cannot authorize a claim.
    transition = _daily_claim_state(available=False, claimed=False)
    transition.pop("daily_claim_all_disabled.png", None)
    backend = _DailyFrameBackend(script=[
        _daily_claim_state(available=True),
        popup,
        stale,
        transition,
        next_popup,
        _daily_claim_state(available=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.detail["claim_action_count"] == 1
    assert result.detail["reward_popup_count"] == 2
    assert result.detail["dismiss_action_count"] == 3
    assert result.detail["cleanup_verified"] is True


def test_daily_persistent_old_reward_frame_is_not_clicked_twice(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"max_daily_pages": 1, "transition_wait_seconds": 0.05})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    popup = _daily_claim_state(available=False, reward=True)
    popup["_reward_frame_id"] = 9
    backend = _DailyFrameBackend(script=[_daily_claim_state(available=True), popup])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.detail["cleanup_verified"] is False
    assert [call[0] for call in backend.input_calls].count("click") == 2


def test_daily_batch_unavailable_does_not_claim_or_claim_nothing_complete(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(max_daily_pages=1, transition_wait_seconds=0.03)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[_daily_claim_state(available=False),
                                  _daily_claim_state(available=False, home=True)])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NOTHING_TO_CLAIM
    assert result.detail["claim_action_count"] == 0
    assert result.reason_code is ReasonCode.NOTHING_TO_CLAIM


def test_daily_repeat_run_confirms_zero_claims_and_returns_home(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_daily_pages"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        _daily_claim_state(available=False),
        _daily_claim_state(available=False, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NOTHING_TO_CLAIM
    assert result.detail["claim_action_count"] == 0
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls] == ["click"]


def test_daily_uncertain_batch_is_not_retried_but_safe_cleanup_continues(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"max_daily_pages": 1, "transition_wait_seconds": 0.05})
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        _daily_claim_state(available=True),
        _daily_claim_state(available=True),  # batch result did not prove the four source rows changed
        _daily_claim_state(available=True, home=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_request_submitted"] is True
    assert result.detail["claim_action_count"] == 1
    assert result.detail["claim_verified"] is False
    assert result.detail["cleanup_verified"] is True
    assert [call[0] for call in backend.input_calls].count("click") == 1


def test_terminal_capture_error_never_authorizes_cleanup_on_stale_frame(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    _short_daily_wait(project)

    class FailingTransitionBackend(FakeBackend):
        def screencap(self, deadline=None, should_stop=None):
            if self.screencap_count >= 2:
                raise MaaTimeout("transition capture timed out")
            return super().screencap(deadline, should_stop)

    backend = FailingTransitionBackend(script=[
        _daily_claim_state(available=True), _daily_claim_state(available=True),
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.reason_code is ReasonCode.TIMEOUT
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 0
    assert len(backend.input_calls) == 1
    assert result.detail["batch_rounds"][0]["submission"] == "uncertain"
    assert json.loads(Path(result.evidence_paths[-1]).read_text(encoding="utf-8")) == result.to_dict()


def test_daily_entry_click_is_followed_by_fresh_capture_and_unknown_stops(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    backend = FakeBackend(script=[
        {"main_city_resource_coin.png": True, "main_city_wall_marker.png": True,
         "daily_task_entry_label.png": True},
        {"unregistered_daily_page.png": True},
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.UNKNOWN_PAGE
    assert result.action_count == 1
    assert result.detail["navigation_action_count"] == 1
    assert result.detail["claim_action_count"] == 0
    assert backend.input_calls == [("click", 130, 130)]
    assert backend.screencap_count >= 2


def test_daily_hub_tab_click_requires_hub_identity_and_fresh_post_capture(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    backend = FakeBackend(script=[
        {"daily_hub_title.png": True, "daily_tab_label.png": True},
        {"unregistered_daily_page.png": True},
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.UNKNOWN_PAGE
    assert result.action_count == 1
    assert result.detail["navigation_action_count"] == 1
    assert result.detail["claim_action_count"] == 0
    assert backend.input_calls == [("click", 130, 130)]
    assert backend.screencap_count >= 2


def test_daily_unknown_button_state_does_not_scroll_and_safely_returns_home(tmp_path: Path) -> None:
    project = _daily_project(tmp_path)
    config_path = project.resource_root / "daily_rewards" / "daily_rewards.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(max_daily_pages=2, transition_wait_seconds=0.03)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[
        {"daily_tasks_header.png": True, "daily_tasks_completed_label.png": True, "daily_tasks_back_arrow.png": True},
        {"daily_tasks_header.png": True, "daily_tasks_completed_label.png": True, "daily_tasks_back_arrow.png": True},
        {"main_city_resource_coin.png": True, "main_city_wall_marker.png": True, "daily_task_entry_label.png": True},
    ])
    result = run(_daily_request(RunMode.EXECUTE), backend, project.root / "artifacts" / "locks", project.root)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
    assert result.detail["claim_action_count"] == 0
    assert result.detail["scroll_action_count"] == 0
    assert result.detail["dismiss_action_count"] == 1
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert [call[0] for call in backend.input_calls] == ["click"]
    assert backend.screencap_count >= 2
