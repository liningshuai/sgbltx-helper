from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sgbltx_helper.maa_adapter import MaaBackend
from sgbltx_helper.pages import load_page_catalog


PROJECT = Path(__file__).resolve().parents[1]
RESOURCE = PROJECT / "resource" / "fief_industry"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 2026-10-21 实机主城 dryrun-20261021T070159Z-39596/before.png 中两处非资源标记的裁剪左上角
INVASION_MARKER_ORIGIN = (260, 660)
INVASION_MARKER_CENTER = (315, 720)
SCROLL_MARKER_ORIGIN = (260, 1220)
SCROLL_MARKER_CENTER = (318, 1280)


def _bgr_canvas(patch_path: Path, origin: tuple[int, int]) -> np.ndarray:
    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    canvas.paste(Image.open(patch_path).convert("RGB"), origin)
    rgb = np.asarray(canvas)
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _offset_roi(center: tuple[int, int], offset: list[int]) -> tuple[int, int, int, int]:
    dx, dy, w, h = offset
    return (center[0] + dx, center[1] + dy, w, h)


@pytest.fixture(scope="module")
def fief_backend() -> MaaBackend:
    backend = MaaBackend(adb_path="", address="")
    backend.load_resource(RESOURCE, task_id="fief_industry_collect")
    yield backend
    backend.close()


def _config() -> dict:
    return json.loads((RESOURCE / "fief_industry.json").read_text(encoding="utf-8"))


def test_resource_pack_loads_and_bubbles_await_live_sample(fief_backend: MaaBackend) -> None:
    config = _config()
    assert [raw["template"] for raw in config["resource_bubbles"]] == ["fief_bubble_grain.png", "fief_bubble_coin.png"]
    assert all(raw["verified"] for raw in config["resource_bubbles"])
    assert config["max_reward_popups"] == 4
    assert config["controls"]["reward_close"]["verified"] is True
    catalog = load_page_catalog(RESOURCE / "pages.json")
    assert catalog.by_id("reward_popup").verified is True
    assert catalog.by_id("main_city").verified is True
    assert {page.page_id for page in catalog.blockers} >= {
        "account_conflict", "unrelated_announcement", "unrelated_announcement_defense",
    }


@pytest.mark.parametrize("fixture", ["fief_invasion_marker_roi.png", "fief_invasion_marker_upright_roi.png"])
def test_invasion_label_hits_real_invasion_marker_only(fief_backend: MaaBackend, fixture: str) -> None:
    label = _config()["invasion_label"]
    invasion = _bgr_canvas(FIXTURES / fixture, INVASION_MARKER_ORIGIN)
    region = _offset_roi(INVASION_MARKER_CENTER, label["offset_roi"])
    scores = [fief_backend.recognize(invasion, t, region, label["threshold"]).score for t in label["templates"]]
    assert max(scores) >= label["threshold"], f"入侵字样未命中, scores={scores}"

    scroll = _bgr_canvas(FIXTURES / "fief_scroll_marker_roi.png", SCROLL_MARKER_ORIGIN)
    region = _offset_roi(SCROLL_MARKER_CENTER, label["offset_roi"])
    assert not any(fief_backend.recognize(scroll, t, region, label["threshold"]).hit for t in label["templates"])


# ---------------------------------------------------------------- 状态机（FakeBackend）
from conftest import FakeBackend, make_project  # noqa: E402
from sgbltx_helper.contracts import DeviceProfile, InspectionResult, ReasonCode, RunMode, RunStatus, TaskRequest  # noqa: E402
from sgbltx_helper.runner import StopController, run  # noqa: E402

CITY = {"city.png": True}
CITY_BUBBLE = {"city.png": True, "bubble_grain.png": True}
POPUP = {"city.png": True, "reward_title.png": True, "reward_hint.png": True, "reward_close.png": True}
# 奖励动画未完成：关闭提示已出现、标题未出现，背后主城完整
PARTIAL = {"city.png": True, "reward_hint.png": True}
# 前景退出过渡：既无奖励线索也无完整页面身份
EXITING = {}


def _make_fief_project(tmp_path: Path, *, bubbles_verified: bool = True, popup_verified: bool = True,
                       max_popups: int = 4, wait: float = 0.3):
    project = make_project(tmp_path)
    root = project.resource_root / "fief_industry"
    (root / "pipeline").mkdir(parents=True)
    pages = {
        "schema_version": 1, "resolution": [1080, 1920], "entry_node": "FiefNoInput", "target_page_id": "main_city",
        "pages": [
            {"page_id": "account_conflict", "role": "blocker", "reason_code": "account_conflict", "verified": True,
             "identity": [{"template": "conflict.png", "verified": True}]},
            {"page_id": "unrelated_announcement", "role": "blocker", "reason_code": "unknown_page", "verified": True,
             "identity": [{"template": "announce.png", "verified": True}]},
            {"page_id": "network_error", "role": "blocker", "reason_code": "network_error", "verified": True,
             "message": "网络异常：请重启游戏后重新登录", "identity": [{"template": "network.png", "verified": True}]},
            {"page_id": "main_city", "role": "entry", "verified": True,
             "identity": [{"template": "city.png", "verified": True}]},
            {"page_id": "reward_popup", "role": "target", "verified": popup_verified,
             "identity": [{"template": "reward_title.png", "verified": popup_verified},
                          {"template": "reward_hint.png", "verified": popup_verified}]},
        ],
    }
    config = {
        "transition_wait_seconds": wait, "max_reward_popups": max_popups, "bubble_search_roi": [0, 0, 0, 0],
        "resource_bubbles": [{"template": "bubble_grain.png", "threshold": 0.97, "verified": bubbles_verified}],
        "invasion_label": {"template": "invasion_label.png", "offset_roi": [-90, 20, 180, 110],
                           "threshold": 0.9, "verified": True},
        "controls": {"reward_close": {"template": "reward_close.png", "roi": [0, 0, 0, 0], "threshold": 0.97,
                                      "verified": popup_verified, "description": "reward_close"}},
    }
    pipeline = {"FiefNoInput": {
        "recognition": {"type": "TemplateMatch", "param": {"template": ["city.png"], "roi": [0, 0, 0, 0], "threshold": [0.9]}},
        "action": {"type": "DoNothing", "param": {}}, "next": [], "repeat": 1, "max_hit": 1}}
    (root / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    (root / "fief_industry.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "pipeline" / "fief_industry_collect.json").write_text(json.dumps(pipeline), encoding="utf-8")
    return project


def _run_fief(project, backend, mode=RunMode.EXECUTE, stop=None, **overrides):
    values = dict(
        task_id="fief_industry_collect",
        profile=DeviceProfile("test", "main", "", "emulator-5554", "com.sgbltx.goodgame", (1080, 1920)),
        mode=mode, resource_root="resource", artifacts_root="artifacts",
    )
    values.update(overrides)
    return run(TaskRequest(**values), backend, project.root / "artifacts" / "locks", project.root,
               stop or StopController())


def _clicks(backend: FakeBackend) -> int:
    return [call[0] for call in backend.input_calls].count("click")


def test_collects_once_dismisses_reward_and_confirms_city(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, POPUP, CITY])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.status is RunStatus.COMPLETED, result.detail["message"]
    assert result.reason_code is ReasonCode.CLAIM_CONFIRMED
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 1
    assert result.detail["claim_verified"] is True
    assert result.detail["cleanup_verified"] is True
    assert result.detail["final_page_id"] == "main_city"
    assert result.detail["bubble_template"] == "bubble_grain.png"
    assert _clicks(backend) == 2


def test_waits_through_claim_animation(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, PARTIAL, PARTIAL, POPUP, CITY])
    result = _run_fief(_make_fief_project(tmp_path, wait=1.0), backend)
    assert result.status is RunStatus.COMPLETED, result.detail["message"]
    assert _clicks(backend) == 2


def test_closes_multiple_reward_popups(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, POPUP, EXITING, POPUP, CITY])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.status is RunStatus.COMPLETED, result.detail["message"]
    assert result.detail["reward_popup_count"] == 2
    assert result.detail["dismiss_action_count"] == 2


def test_reward_popup_limit_stops_before_an_extra_close(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE] + [POPUP, EXITING] * 5)
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.reason_code is ReasonCode.BUDGET_EXHAUSTED
    assert result.detail["dismiss_action_count"] == 4
    assert result.detail["claim_action_count"] == 1


def test_unchanged_popup_after_close_is_not_closed_again(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, POPUP, POPUP])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.detail["dismiss_action_count"] == 1
    assert _clicks(backend) == 2


def test_no_bubble_is_nothing_to_claim_visible_area_only(tmp_path):
    backend = FakeBackend(script=[CITY])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.status is RunStatus.NOTHING_TO_CLAIM
    assert result.action_count == 0
    assert result.detail["visible_area_only"] is True


def test_unverified_bubbles_never_report_nothing_to_claim(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE])
    result = _run_fief(_make_fief_project(tmp_path, bubbles_verified=False), backend)
    assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
    assert backend.input_calls == []


def test_invasion_marker_is_never_clicked(tmp_path):
    backend = FakeBackend(script=[{**CITY_BUBBLE, "invasion_label.png": True}])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert backend.input_calls == []
    assert result.detail["invasion_rejected_count"] == 1
    assert result.status is RunStatus.NOTHING_TO_CLAIM


def test_no_reward_popup_after_claim_is_uncertain(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, CITY])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_action_count"] == 1
    assert _clicks(backend) == 1


def test_bubble_still_present_after_cleanup_is_not_success(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, POPUP, CITY_BUBBLE])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.reason_code is ReasonCode.POSTCONDITION_NOT_MET
    assert result.detail["claim_action_count"] == 1
    assert _clicks(backend) == 2


def test_unverified_reward_popup_stops_after_claim(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, POPUP])
    result = _run_fief(_make_fief_project(tmp_path, popup_verified=False), backend)
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["claim_verified"] is False
    assert result.detail["claim_action_count"] == 1
    assert result.detail["dismiss_action_count"] == 0
    assert _clicks(backend) == 1


def test_preexisting_reward_popup_is_cleanup_only(tmp_path):
    backend = FakeBackend(script=[POPUP, CITY])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is ReasonCode.OUTCOME_UNCERTAIN
    assert result.detail["cleanup_only"] is True
    assert result.detail["cleanup_verified"] is True
    assert result.detail["claim_action_count"] == 0


@pytest.mark.parametrize(("frame", "reason"), [
    ({**CITY_BUBBLE, "conflict.png": True}, ReasonCode.ACCOUNT_CONFLICT),
    ({**CITY_BUBBLE, "announce.png": True}, ReasonCode.UNKNOWN_PAGE),
    ({}, ReasonCode.UNKNOWN_PAGE),
])
def test_blockers_and_unknown_page_stop_without_input(tmp_path, frame, reason):
    backend = FakeBackend(script=[frame])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.status is RunStatus.NEEDS_ATTENTION
    assert result.reason_code is reason
    assert backend.input_calls == []


def test_dry_run_lists_bubble_with_zero_inputs(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE])
    result = _run_fief(_make_fief_project(tmp_path), backend, RunMode.DRY_RUN)
    assert isinstance(result, InspectionResult)
    assert result.page_id == "main_city"
    assert [c.action_id for c in result.candidate_actions] == ["collect_bubble"]
    assert result.candidate_actions[0].verified is True
    assert backend.input_calls == []


def test_dry_run_with_unverified_bubbles_lists_no_candidate(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE])
    result = _run_fief(_make_fief_project(tmp_path, bubbles_verified=False), backend, RunMode.DRY_RUN)
    assert isinstance(result, InspectionResult)
    assert result.candidate_actions == []
    assert any("待" in note for note in result.notes)
    assert backend.input_calls == []


class _StopDuringWait(StopController):
    def wait(self, seconds: float) -> bool:
        self.request_stop()
        return True


def test_stop_during_claim_wait_preserves_claim_count(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, PARTIAL])
    result = _run_fief(_make_fief_project(tmp_path), backend, stop=_StopDuringWait())
    assert result.status is RunStatus.CANCELLED
    assert result.detail["claim_action_count"] == 1
    assert _clicks(backend) == 1


def test_timeout_during_claim_wait_preserves_claim_count(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, PARTIAL, PARTIAL], stage_delays={"screencap": [0, 0, 2]})
    result = _run_fief(_make_fief_project(tmp_path, wait=0.9), backend, timeout_seconds=1)
    assert result.reason_code is ReasonCode.TIMEOUT
    assert result.detail["claim_action_count"] == 1
    assert _clicks(backend) == 1


def test_claim_click_uses_fresh_frame_and_stops_on_new_announcement(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, {**CITY_BUBBLE, "announce.png": True}])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert backend.input_calls == []
    assert result.reason_code is ReasonCode.UNKNOWN_PAGE
    assert result.detail["claim_request_submitted"] is False


def test_notice_on_fresh_frame_before_click_reports_its_manual_fix(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, {**CITY_BUBBLE, "network.png": True}])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert backend.input_calls == []
    assert result.reason_code.value == "network_error"
    assert "网络异常：请重启游戏后重新登录" in result.detail["message"]


def test_notice_after_claim_reports_its_reason_and_manual_fix(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, {"network.png": True}])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.detail["claim_action_count"] == 1
    assert _clicks(backend) == 1
    assert result.reason_code.value == "network_error"
    assert "网络异常：请重启游戏后重新登录" in result.detail["message"]


def test_partial_flicker_is_not_exit_evidence(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, POPUP, PARTIAL, POPUP])
    result = _run_fief(_make_fief_project(tmp_path), backend)
    assert result.reason_code is ReasonCode.POSTCONDITION_NOT_MET
    assert result.detail["dismiss_action_count"] == 1
    assert _clicks(backend) == 2


@pytest.mark.parametrize("bad", ["8s", None, 0, -1, True, 31])
def test_invalid_transition_wait_is_rejected_before_input(tmp_path, bad):
    project = _make_fief_project(tmp_path)
    path = project.resource_root / "fief_industry" / "fief_industry.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["transition_wait_seconds"] = bad
    path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[CITY_BUBBLE])
    result = _run_fief(project, backend)
    assert result.status is RunStatus.FAILED
    assert result.reason_code.value == "profile_invalid"
    assert backend.input_calls == []


def test_unverified_invasion_label_is_assets_unverified(tmp_path):
    project = _make_fief_project(tmp_path)
    path = project.resource_root / "fief_industry" / "fief_industry.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["invasion_label"]["verified"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    backend = FakeBackend(script=[CITY_BUBBLE])
    result = _run_fief(project, backend)
    assert result.reason_code is ReasonCode.ASSETS_UNVERIFIED
    assert backend.input_calls == []


def test_bubble_search_stays_above_announcement_forward_button() -> None:
    x, y, w, h = _config()["bubble_search_roi"]
    assert y + h <= 1490  # 攻城令/守城令“前往”按钮 ROI 顶边


_VERIFIED_BUBBLES = [raw["template"] for raw in json.loads(
    (RESOURCE / "fief_industry.json").read_text(encoding="utf-8"))["resource_bubbles"] if raw.get("verified")]


@pytest.mark.skipif(not _VERIFIED_BUBBLES, reason="资源气泡素材尚未实机采样；验证后此门禁自动生效")
@pytest.mark.parametrize(("fixture", "origin"), [
    ("fief_invasion_marker_roi.png", INVASION_MARKER_ORIGIN),          # 入侵标记摆动中的倾斜姿态
    ("fief_invasion_marker_upright_roi.png", INVASION_MARKER_ORIGIN),  # 入侵标记正立姿态（铜钱图标与资源气泡几乎相同）
    ("fief_scroll_marker_roi.png", SCROLL_MARKER_ORIGIN),
    ("fief_bubble_grain_covered_roi.png", (560, 400)),                 # 被“跨服赛报名”图标压住的粮食气泡
])
def test_verified_bubbles_never_select_non_resource_markers(fief_backend, fixture, origin) -> None:
    from sgbltx_helper.fief_industry import find_bubble

    scan = find_bubble(fief_backend, _bgr_canvas(FIXTURES / fixture, origin), _config())
    assert scan.candidate is None


@pytest.mark.parametrize(("fixture", "origin", "expected"), [
    ("fief_bubble_grain_roi.png", (250, 1200), (324, 1279)),
    ("fief_bubble_coin_roi.png", (790, 540), (862, 611)),
])
def test_verified_bubbles_find_real_resource_bubbles(fief_backend, fixture, origin, expected) -> None:
    from sgbltx_helper.fief_industry import find_bubble

    scan = find_bubble(fief_backend, _bgr_canvas(FIXTURES / fixture, origin), _config())
    assert scan.candidate == expected
    assert scan.invasion_rejected == 0


@pytest.mark.parametrize("angle", [-8, -6, -4, 4, 6, 8])
def test_swaying_invasion_marker_is_rejected_at_unsampled_angles(fief_backend, angle) -> None:
    """入侵标记有摆动动画；未采样到的倾斜角度也必须被排除，不能退化为可点气泡。"""
    from sgbltx_helper.fief_industry import find_bubble

    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    canvas.paste(Image.open(FIXTURES / "fief_invasion_marker_upright_roi.png").convert("RGB"), INVASION_MARKER_ORIGIN)
    banner = canvas.crop((270, 752, 372, 785))
    canvas.paste(banner.rotate(angle, resample=Image.BICUBIC, fillcolor=(0, 0, 0)), (270, 752))
    image = np.ascontiguousarray(np.asarray(canvas)[:, :, ::-1])
    scan = find_bubble(fief_backend, image, _config())
    assert scan.candidate is None
    assert scan.invasion_rejected >= 1


def _popup_canvas(fixture: str) -> np.ndarray:
    return _bgr_canvas(FIXTURES / fixture, (0, 640))


def test_real_fief_reward_popup_is_identified_and_closable(fief_backend) -> None:
    from sgbltx_helper.classification import classify

    image = _popup_canvas("fief_reward_popup_roi.png")
    cls = classify(fief_backend, image, load_page_catalog(RESOURCE / "pages.json"))
    assert cls.page is not None and cls.page.page_id == "reward_popup"
    close = _config()["controls"]["reward_close"]
    outcome = fief_backend.recognize(image, close["template"], tuple(close["roi"]), close["threshold"])
    assert outcome.hit and outcome.center is not None
    assert 435 <= outcome.center[0] <= 645 and 1116 <= outcome.center[1] <= 1139


def test_animating_fief_reward_popup_is_partial_foreground(fief_backend) -> None:
    from sgbltx_helper.classification import classify
    from sgbltx_helper.fief_industry import _partial_reward

    cls = classify(fief_backend, _popup_canvas("fief_reward_popup_animating_roi.png"), load_page_catalog(RESOURCE / "pages.json"))
    assert cls.page is None
    assert _partial_reward(cls)


def test_main_city_identity_survives_camera_pan_after_collect(fief_backend) -> None:
    """点气泡后镜头移到该产业并停留；主城身份只能依赖固定界面（顶部资源栏 + 左下“任务”入口）。"""
    from sgbltx_helper.classification import classify

    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    canvas.paste(Image.open(FIXTURES / "fief_panned_city_hud_roi.png").convert("RGB"), (300, 100))
    canvas.paste(Image.open(FIXTURES / "fief_panned_city_task_roi.png").convert("RGB"), (80, 1540))
    image = np.ascontiguousarray(np.asarray(canvas)[:, :, ::-1])
    cls = classify(fief_backend, image, load_page_catalog(RESOURCE / "pages.json"))
    assert cls.page is not None and cls.page.page_id == "main_city"
    assert not cls.blockers and not cls.ambiguous_pages


def test_unknown_flash_frame_after_claim_is_waited_out(tmp_path):
    backend = FakeBackend(script=[CITY_BUBBLE, CITY_BUBBLE, {}, {}, POPUP, CITY])
    result = _run_fief(_make_fief_project(tmp_path, wait=1.0), backend)
    assert result.status is RunStatus.COMPLETED, result.detail["message"]
    assert _clicks(backend) == 2


@pytest.mark.parametrize("tag", ["weidi", "moremenu"])
def test_dimmed_hud_under_overlay_is_not_main_city(fief_backend, tag) -> None:
    """归一化模板匹配不受整体变暗影响；未登记公告（如魏帝）或“更多”菜单遮罩下的界面不能当作主城。"""
    from sgbltx_helper.classification import classify

    canvas = Image.new("RGB", (1080, 1920), (0, 0, 0))
    canvas.paste(Image.open(FIXTURES / f"fief_dimmed_{tag}_hud_roi.png").convert("RGB"), (300, 100))
    canvas.paste(Image.open(FIXTURES / f"fief_dimmed_{tag}_task_roi.png").convert("RGB"), (80, 1540))
    image = np.ascontiguousarray(np.asarray(canvas)[:, :, ::-1])
    cls = classify(fief_backend, image, load_page_catalog(RESOURCE / "pages.json"))
    assert cls.page is None or cls.page.page_id != "main_city"
